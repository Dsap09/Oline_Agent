"""
Context-aware intent detection (hybrid) untuk Oline.

Prinsip:
- Pesan TANPA keyword intent -> fast path chat (tanpa panggilan LLM).
- Pesan dengan keyword yang JELAS perintah -> langsung eksekusi (tanpa LLM).
- Pesan dengan keyword tapi bisa jadi komentar/cerita/pertanyaan meta -> LLM gate
  (chat vs action + intent + confidence).
- Confidence rendah -> minta konfirmasi user via tombol (dikoordinasi bot.py).
- Classifier gagal/timeout -> fallback ke hasil keyword (safety net).
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

EXECUTE_CONFIDENCE = 0.75
CLARIFY_CONFIDENCE = 0.45
CACHE_TTL_SECONDS = 3600
TOPIC_TTL_SECONDS = 3600
CLASSIFIER_TIMEOUT = 8.0

# Daftar intent yang dikenali + deskripsi singkat untuk prompt classifier.
INTENT_TAXONOMY = {
    "coding_agent": "mengubah/memperbaiki kode Oline sendiri (self-improvement), tambah fitur, refactor",
    "github": "baca file repo GitHub, buat pull request, push ke GitHub",
    "vercel_logs": "baca/analisis log error Vercel",
    "notion": "simpan catatan/memori ke Notion atau kelola kolom Notion",
    "cuaca": "cek prakiraan cuaca suatu kota",
    "rekomendasi": "minta rekomendasi film/lagu/seri/anime",
    "suara": "minta voice note, nyanyi, gombal, atau puisi",
    "jurnal": "catat jurnal atau rekap jurnal",
    "kuota": "cek kuota/pemakaian AI atau token",
    "health": "cek kesehatan fitur Oline",
    "kelola_fitur": "aktifkan/nonaktifkan fitur Oline",
    "drive": "kelola file Google Drive (upload, cari, kirim file)",
    "design_reference": "cari referensi desain website",
    "deploy": "deploy, daftar, atau hapus deployment Vercel",
    "preview": "buat landing page/aplikasi web atau preview tampilan",
    "lokasi": "cari tempat terdekat (cafe, restoran, apotek, dll)",
    "search": "cari informasi atau berita di internet",
    "saham": "cek harga saham atau IHSG",
    "coding": "jalankan/eksekusi kode atau debug program",
    "gambar": "cari dan kirim gambar/foto",
    "neo4j": "catat atau lihat aktivitas di graph Neo4j",
    "akademik": "bantuan akademik ERINE (jurnal, sitasi, ringkas docx, tanya PDF)",
    "cek_token": "cek status/validitas token API",
    "renew_token": "perbarui atau ganti token API",
}

# Label manusiawi intent untuk pertanyaan konfirmasi.
INTENT_LABELS = {
    "coding_agent": "ubah kode Oline",
    "github": "akses GitHub",
    "vercel_logs": "cek log Vercel",
    "notion": "simpan ke Notion",
    "cuaca": "cek cuaca",
    "rekomendasi": "minta rekomendasi film/lagu",
    "suara": "kirim voice note",
    "jurnal": "catat/rekap jurnal",
    "kuota": "cek kuota AI",
    "health": "cek kesehatan fitur",
    "kelola_fitur": "atur fitur Oline",
    "drive": "kelola file Drive",
    "design_reference": "cari referensi desain",
    "deploy": "deploy ke Vercel",
    "preview": "buat landing page/aplikasi web",
    "lokasi": "cari tempat terdekat",
    "search": "cari info di internet",
    "saham": "cek saham/IHSG",
    "coding": "jalankan kode",
    "gambar": "cari gambar",
    "neo4j": "kelola aktivitas Neo4j",
    "akademik": "bantuan akademik",
    "cek_token": "cek token",
    "renew_token": "perbarui token",
}

TAXONOMY_TEXT = "\n".join(f"- {key}: {desc}" for key, desc in INTENT_TAXONOMY.items())

CLASSIFIER_SYSTEM_PROMPT = (
    "Kamu adalah classifier intent untuk asisten AI pribadi di Telegram (Oline).\n"
    "Tugasmu: dari pesan user (dan konteks percakapan), tentukan apakah user minta AKSI "
    "(butuh data/tool) atau hanya CHAT (ngobrol).\n\n"
    "Aturan penting:\n"
    "- Komentar/observasi ('cuaca hari ini panas ya') = chat.\n"
    "- Cerita/pengalaman ('aku lagi baca buku tentang saham') = chat.\n"
    "- Pertanyaan meta ('cara cek cuaca gimana?', 'kamu tahu gak cara pakai ini') = chat.\n"
    "- Rencana belum terjadi ('nanti aku mau catat belanja') = chat.\n"
    "- Permintaan jelas + target ('cek cuaca Surabaya', 'saham BBCA berapa?') = action.\n"
    "- Kalau ragu antara chat dan action, pilih chat dengan confidence rendah.\n"
    "- Jangan tertipu kata kunci yang cuma muncul di tengah cerita/komentar.\n\n"
    "Intent yang tersedia (untuk mode action):\n"
    f"{TAXONOMY_TEXT}\n\n"
    "Balas HANYA JSON valid tanpa markdown, dengan skema:\n"
    '{"mode":"chat|action","intent":"<nama intent dari daftar, atau none>",'
    '"confidence":0.0-1.0,"alasan":"alasan singkat"}'
)

# Kata kerja perintah yang menandakan permintaan aksi eksplisit.
_DIRECTIVE_VERBS = (
    "cek", "lihat", "tampilkan", "tunjukin", "kirim", "kirimi", "cari", "cariin",
    "buat", "buatkan", "bikin", "bikinin", "deploy", "onlinekan", "publish",
    "jalankan", "eksekusi", "catat", "simpan", "tulis", "rekam",
    "perbarui", "update", "ganti", "renew", "refresh", "rotasi",
    "matikan", "nonaktifkan", "aktifkan", "hidupkan", "toggle",
    "lanjutkan", "hapus", "delete", "daftar", "list",
)

# Penanda permintaan data/query (mis. "berapa harga saham BBCA").
_QUERY_MARKERS = ("berapa", "harga", "prediksi", "jadwal")

# Pola pertanyaan meta tentang CARA memakai sesuatu -> bukan perintah aksi.
_META_PATTERNS = (
    r"(cara|gimana|bagaimana)\s+(cek|pakai|buat|bikin|kirim|simpan|catat|deploy)",
    r"(tau|tahu)\s+gak\s+(cara|gimana)",
    r"apa\s+itu\s+.*(tool|fitur|cek)",
    r" bisa gak (kamu|kita|oline)",
)


def _normalize(text: str) -> str:
    """Normalisasi ringan untuk key cache (lowercase + spasi rapi)."""
    low = (text or "").lower()
    low = re.sub(r"[^\w\s]", " ", low)
    return re.sub(r"\s+", " ", low).strip()


def _cache_key(text: str) -> str:
    digest = hashlib.sha1(_normalize(text).encode("utf-8")).hexdigest()[:24]
    return f"intent_cache:{digest}"


def _is_meta_question(text: str) -> bool:
    low = (text or "").lower()
    return any(re.search(pattern, low) for pattern in _META_PATTERNS)


def _is_clear_directive(text: str, intent: Optional[str]) -> bool:
    """
    True bila pesan adalah perintah aksi yang jelas (boleh langsung dieksekusi
    tanpa LLM). Sengaja konservatif: harus ada kata kerja perintah atau penanda
    query data, dan bukan pertanyaan meta.
    """
    if not intent:
        return False
    low = (text or "").lower().strip()
    if not low:
        return False
    if _is_meta_question(low):
        return False

    words = low.split()
    first_word = words[0].rstrip(",.") if words else ""
    starts_with_directive = first_word in _DIRECTIVE_VERBS
    has_query_marker = any(marker in low for marker in _QUERY_MARKERS)

    if starts_with_directive:
        return True
    if len(words) <= 8 and has_query_marker:
        return True
    return False


def _extract_json_object(raw: str) -> Optional[dict]:
    """Mengekstrak JSON object dari respons model (strip fence bila ada)."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Gagal parse JSON intent classifier: %s", str(e))
    return None


def _parse_decision(raw: str) -> Optional[dict]:
    """Validasi & normalisasi output classifier."""
    data = _extract_json_object(raw)
    if not isinstance(data, dict):
        return None
    mode = str(data.get("mode", "")).strip().lower()
    if mode not in ("chat", "action"):
        return None

    intent = str(data.get("intent", "")).strip().lower()
    if intent in ("none", "null", "", "chat", "-"):
        intent = None
    if intent and intent not in INTENT_TAXONOMY:
        intent = None

    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        "mode": mode,
        "intent": intent,
        "confidence": round(confidence, 2),
        "alasan": str(data.get("alasan", ""))[:200],
    }


async def _classify_with_gemini(prompt: str) -> str:
    """Fallback classifier via Gemini bila Groq gagal/kosong."""
    from src.gemini import _get_client

    client = _get_client()
    response = await asyncio.wait_for(
        asyncio.to_thread(
            client.models.generate_content,
            model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
            contents=f"{CLASSIFIER_SYSTEM_PROMPT}\n\n{prompt}",
        ),
        timeout=15.0,
    )
    return response.text if response and response.text else ""


async def _build_prompt(user_message: str, chat_id: int) -> str:
    """Menyusun prompt dengan konteks 3-5 pesan terakhir + topik aktif."""
    lines = [f"Pesan user: {user_message[:600]}"]
    try:
        from src.kv import get_cache, get_history

        topic = await get_cache(f"intent_topic:{chat_id}")
        if topic:
            lines.append(f"Topik aktif sebelumnya: {topic}")

        history = await get_history(chat_id)
        if history:
            lines.append("Konteks percakapan (terbaru):")
            for item in history[-4:]:
                role = "User" if item.get("role") == "user" else "Oline"
                text = (item.get("text") or "").strip()
                if text:
                    lines.append(f"- {role}: {text[:200]}")
    except Exception as e:
        logger.warning("Gagal menyusun konteks intent: %s", str(e))
    return "\n".join(lines)


async def classify_message(user_message: str, chat_id: int) -> Optional[dict]:
    """
    Klasifikasi pesan via LLM (Groq -> fallback Gemini) dengan cache KV 1 jam.
    Returns dict {mode, intent, confidence, alasan} atau None bila gagal.
    """
    if not user_message:
        return None

    from src.kv import get_cache, set_cache

    cache_key = _cache_key(user_message)
    try:
        cached = await get_cache(cache_key)
        if cached:
            data = json.loads(cached)
            if isinstance(data, dict) and data.get("mode"):
                return data
    except Exception:
        pass

    prompt = await _build_prompt(user_message, chat_id)

    raw = ""
    try:
        from src.groq import chat_groq
        raw = await asyncio.wait_for(
            chat_groq(CLASSIFIER_SYSTEM_PROMPT, [], prompt, chat_id=0),
            timeout=CLASSIFIER_TIMEOUT,
        )
    except Exception as e:
        logger.warning("Intent classifier (Groq) gagal: %s", str(e))

    if not raw or not raw.strip():
        try:
            raw = await _classify_with_gemini(prompt)
        except Exception as e:
            logger.warning("Intent classifier (Gemini) gagal: %s", str(e))
            return None

    decision = _parse_decision(raw)
    if decision is None:
        return None

    try:
        await set_cache(cache_key, json.dumps(decision, ensure_ascii=False), ttl_seconds=CACHE_TTL_SECONDS)
    except Exception:
        pass
    return decision


def _intent_label(intent: Optional[str]) -> str:
    return INTENT_LABELS.get(intent or "", intent or "melakukan sesuatu")


async def _remember_topic(chat_id: int, intent: Optional[str], text: str) -> None:
    """Menyimpan topik aktif agar pesan lanjutan bisa dikontekstualkan."""
    if not intent:
        return
    try:
        from src.kv import set_cache
        topic = f"{_intent_label(intent)} (contoh: {(text or '').strip()[:80]})"
        await set_cache(f"intent_topic:{chat_id}", topic, ttl_seconds=TOPIC_TTL_SECONDS)
    except Exception as e:
        logger.warning("Gagal simpan topik intent: %s", str(e))


async def _audit(chat_id: int, text: str, mode: str, intent: Optional[str],
                 confidence: float, source: str) -> None:
    """Audit keputusan intent ke KV (best-effort)."""
    try:
        from src.kv import log_intent_decision
        await log_intent_decision(chat_id, text, mode, intent, confidence, source)
    except Exception as e:
        logger.warning("Gagal audit intent: %s", str(e))


async def resolve_intent(
    text: str,
    chat_id: int,
    keyword_intent: Optional[str],
) -> dict:
    """
    Orkestrasi hybrid. Returns dict:
      {mode: "action"|"chat"|"clarify", intent, confidence, source, question}
    """
    low = (text or "").strip()
    if keyword_intent is None:
        return {"mode": "chat", "intent": None, "confidence": 0.0, "source": "fast", "question": ""}

    if _is_clear_directive(low, keyword_intent):
        await _remember_topic(chat_id, keyword_intent, low)
        await _audit(chat_id, low, "action", keyword_intent, 1.0, "directive")
        return {"mode": "action", "intent": keyword_intent, "confidence": 1.0, "source": "directive", "question": ""}

    decision = await classify_message(low, chat_id)
    if not decision:
        # Fallback aman: pakai keyword lama agar fungsi tidak hilang saat LLM down.
        await _audit(chat_id, low, "action", keyword_intent, 0.0, "fallback")
        return {"mode": "action", "intent": keyword_intent, "confidence": 0.0, "source": "fallback", "question": ""}

    mode = decision.get("mode")
    confidence = float(decision.get("confidence") or 0.0)
    llm_intent = decision.get("intent")
    intent_final = llm_intent or keyword_intent

    if mode == "chat":
        await _audit(chat_id, low, "chat", None, confidence, "classifier")
        return {"mode": "chat", "intent": None, "confidence": confidence, "source": "classifier", "question": ""}

    if confidence >= EXECUTE_CONFIDENCE:
        await _remember_topic(chat_id, intent_final, low)
        await _audit(chat_id, low, "action", intent_final, confidence, "classifier")
        return {"mode": "action", "intent": intent_final, "confidence": confidence, "source": "classifier", "question": ""}

    if confidence >= CLARIFY_CONFIDENCE:
        question = f"Sepertinya kamu mau {_intent_label(intent_final)}. Betul?"
        await _audit(chat_id, low, "clarify", intent_final, confidence, "classifier")
        return {"mode": "clarify", "intent": intent_final, "confidence": confidence, "source": "classifier", "question": question}

    await _audit(chat_id, low, "chat", None, confidence, "classifier")
    return {"mode": "chat", "intent": None, "confidence": confidence, "source": "classifier", "question": ""}
