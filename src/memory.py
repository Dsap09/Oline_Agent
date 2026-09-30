"""
Auto-save memori proaktif Oline.

Alur: deteksi hal penting dari percakapan (Groq, fallback Gemini) -> anti-duplikat
-> rate limit (5 entri/hari, 1 batch/10 menit) -> simpan ke Notion "Memori Oline"
-> audit log KV + notifikasi halus (digabung satu pesan).
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

MEMORY_SETTINGS_PREFIX = "memory_autosave"
MEMORY_COUNT_PREFIX = "memory_count"
MEMORY_LAST_PREFIX = "memory_last"
MEMORY_LOG_PREFIX = "memory_log"

MEMORY_CATEGORIES = ["fakta", "preferensi", "aturan", "ringkasan"]

# Pemetaan kategori internal -> nilai properti "Jenis" di Notion.
CATEGORY_TO_JENIS = {
    "fakta": "Fakta",
    "preferensi": "Preferensi",
    "aturan": "Aturan",
    "ringkasan": "Ringkasan",
}

DEFAULT_MEMORY_SETTINGS = {
    "enabled": True,
    "categories": list(MEMORY_CATEGORIES),
    "notify": True,
}

MIN_CONFIDENCE = 0.7
MAX_PER_DAY = 5
MIN_INTERVAL_SECONDS = 600
MIN_MESSAGE_LENGTH = 12
DEDUP_THRESHOLD = 0.85

# Settings disimpan lama (1 tahun) agar preferensi user tidak hilang.
SETTINGS_TTL_SECONDS = 365 * 24 * 3600
AUDIT_TTL_SECONDS = 30 * 24 * 3600
AUDIT_MAX_ENTRIES = 100

_STOPWORDS = {
    "aku", "saya", "kamu", "user", "pengguna", "yang", "dan", "di", "ke", "dari",
    "itu", "ini", "ada", "adalah", "dengan", "untuk", "nya", "gak", "nggak",
    "tidak", "juga", "ya", "sih", "kok", "dong", "banget", "aja", "saja",
    "the", "a", "an", "is", "are", "to", "of", "in", "i", "my", "me",
}

# Sapaan/acknowledgement singkat yang tidak perlu dianalisis.
_SMALL_TALK = {
    "halo", "hai", "hi", "hello", "hey", "pagi", "siang", "sore", "malam",
    "apa kabar", "apa kabarmu", "gimana", "makasih", "terima kasih", "thanks",
    "ok", "oke", "sip", "siap", "baik", "bagus", "mantap", "wkwk", "haha",
    "ya", "iya", "no", "nah", "lah", "hmm", "test", "tes",
}

DETECT_SYSTEM_PROMPT = (
    "Kamu adalah ekstraktor memori jangka panjang untuk asisten AI pribadi di Telegram.\n"
    "Tugasmu: dari pesan user (dan balasan asisten), tentukan apakah ada informasi JANGKA PANJANG "
    "tentang user yang layak disimpan.\n\n"
    "Kategori yang boleh:\n"
    "- fakta: nama/panggilan, kota, pekerjaan, hobi, minat, konteks hidup (sedang skripsi, cari kerja), "
    "nama proyek, deadline yang disebutkan.\n"
    "- preferensi: suka/tidak suka (makanan, minuman, gaya jawaban), bahasa, jam aktif, model AI atau fitur favorit.\n"
    "- aturan: instruksi permanen ke asisten (mis. 'jangan panggil bestie', 'selalu deploy di project yang sama').\n"
    "- ringkasan: keputusan atau rencana penting dari percakapan (hanya jika jelas penting).\n\n"
    "JANGAN simpan: sapaan, obrolan receh, pertanyaan umum, permintaan sementara, data sensitif "
    "(password, token, nomor kartu), atau hal yang jelas berubah besok (mis. cuaca, harga saham).\n\n"
    "Balas HANYA JSON array valid tanpa markdown, dengan skema:\n"
    '[{"kategori":"fakta|preferensi|aturan|ringkasan","judul":"maks 60 karakter",'
    '"isi":"1 kalimat lengkap sudut pandang orang ketiga tentang user","confidence":0.0-1.0}]\n'
    "Jika tidak ada yang layak disimpan, balas: []"
)


def _wib_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=7)))


# --- Pengaturan per user (KV) ---

async def get_memory_settings(chat_id: int) -> dict:
    """Mengambil pengaturan auto-save user (default aktif semua kategori + notifikasi)."""
    from src.kv import get_cache

    settings = {
        "enabled": DEFAULT_MEMORY_SETTINGS["enabled"],
        "categories": list(DEFAULT_MEMORY_SETTINGS["categories"]),
        "notify": DEFAULT_MEMORY_SETTINGS["notify"],
    }
    try:
        raw = await get_cache(f"{MEMORY_SETTINGS_PREFIX}:{chat_id}")
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                if isinstance(data.get("enabled"), bool):
                    settings["enabled"] = data["enabled"]
                if isinstance(data.get("notify"), bool):
                    settings["notify"] = data["notify"]
                cats = data.get("categories")
                if isinstance(cats, list):
                    valid = [c for c in cats if c in MEMORY_CATEGORIES]
                    if valid:
                        settings["categories"] = valid
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Error parsing memory settings: %s", str(e))
    return settings


async def set_memory_settings(chat_id: int, **updates) -> bool:
    """Menyimpan pengaturan auto-save user (enabled/categories/notify)."""
    from src.kv import set_cache

    settings = await get_memory_settings(chat_id)
    if isinstance(updates.get("enabled"), bool):
        settings["enabled"] = updates["enabled"]
    if isinstance(updates.get("notify"), bool):
        settings["notify"] = updates["notify"]
    if isinstance(updates.get("categories"), list):
        valid = [c for c in updates["categories"] if c in MEMORY_CATEGORIES]
        settings["categories"] = valid
    payload = json.dumps(settings, ensure_ascii=False)
    return await set_cache(f"{MEMORY_SETTINGS_PREFIX}:{chat_id}", payload, ttl_seconds=SETTINGS_TTL_SECONDS)


# --- Rate limit ---

async def _get_daily_count(chat_id: int) -> int:
    from src.kv import _kv_request

    key = f"{MEMORY_COUNT_PREFIX}:{chat_id}:{_wib_now().strftime('%Y-%m-%d')}"
    res = await _kv_request(["GET", key])
    if res and res.get("result") is not None:
        try:
            return int(res["result"])
        except (TypeError, ValueError):
            return 0
    return 0


async def _rate_limit_ok(chat_id: int) -> bool:
    """True bila user masih boleh menyimpan (belum lewat kuota harian & interval)."""
    from src.kv import get_cache

    last_raw = await get_cache(f"{MEMORY_LAST_PREFIX}:{chat_id}")
    if last_raw:
        try:
            if time.time() - float(last_raw) < MIN_INTERVAL_SECONDS:
                return False
        except (TypeError, ValueError):
            pass
    return await _get_daily_count(chat_id) < MAX_PER_DAY


async def _register_save(chat_id: int, jumlah: int = 1) -> None:
    """Mencatat pemakaian: INCR kuota harian + update timestamp interval."""
    from src.kv import _kv_request, set_cache

    key = f"{MEMORY_COUNT_PREFIX}:{chat_id}:{_wib_now().strftime('%Y-%m-%d')}"
    await _kv_request(["INCRBY", key, str(max(1, int(jumlah)))])
    await _kv_request(["EXPIRE", key, "172800"])
    await set_cache(
        f"{MEMORY_LAST_PREFIX}:{chat_id}",
        str(time.time()),
        ttl_seconds=MIN_INTERVAL_SECONDS + 300,
    )


# --- Audit log ---

async def log_memory_action(chat_id: int, kategori: str, judul: str, aksi: str, sumber: str = "") -> bool:
    """
    Audit log aksi memori ke KV (key memory_log:<chat_id>).
    TTL 30 hari, maksimal 100 entri terakhir.
    """
    from src.kv import _kv_pipeline

    key = f"{MEMORY_LOG_PREFIX}:{chat_id}"
    payload = json.dumps({
        "waktu": _wib_now().strftime("%Y-%m-%d %H:%M:%S"),
        "kategori": kategori,
        "judul": str(judul)[:100],
        "aksi": aksi,
        "sumber": str(sumber)[:200],
    }, ensure_ascii=False)
    res = await _kv_pipeline([
        ["RPUSH", key, payload],
        ["LTRIM", key, str(-AUDIT_MAX_ENTRIES), "-1"],
        ["EXPIRE", key, str(AUDIT_TTL_SECONDS)],
    ])
    return res is not None


async def get_memory_log(chat_id: int, limit: int = 20) -> list[dict]:
    """Mengambil audit log memori terbaru (maks `limit` entri)."""
    from src.kv import _kv_request

    key = f"{MEMORY_LOG_PREFIX}:{chat_id}"
    res = await _kv_request(["LRANGE", key, str(-abs(limit)), "-1"])
    entries: list[dict] = []
    if res and isinstance(res.get("result"), list):
        for raw in res["result"]:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, dict):
                    entries.append(data)
            except (json.JSONDecodeError, TypeError):
                continue
    return entries


# --- Detektor hal penting ---

def _is_worth_detecting(text: str) -> bool:
    """Pre-filter murah: lewati pesan pendek/sapaan/command agar tidak boros API."""
    t = (text or "").strip()
    if len(t) < MIN_MESSAGE_LENGTH:
        return False
    if t.startswith("/"):
        return False
    low = t.lower().strip()
    if low in _SMALL_TALK:
        return False
    return True


def _extract_json_array(raw: str) -> list:
    """Mengekstrak JSON array dari respons model (strip markdown fence bila ada)."""
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end != -1 and end > start:
            data = json.loads(text[start:end + 1])
            if isinstance(data, list):
                return data
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Gagal parse JSON detektor memori: %s", str(e))
    return []


async def _detect_with_gemini(prompt: str) -> str:
    """Fallback detektor via Gemini bila Groq gagal/kosong."""
    from src.gemini import _get_client

    client = _get_client()
    response = await asyncio.wait_for(
        asyncio.to_thread(
            client.models.generate_content,
            model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
            contents=prompt,
        ),
        timeout=15.0,
    )
    return response.text if response and response.text else ""


async def detect_memories(user_message: str, bot_response: str, user_name: str = "Teman") -> list[dict]:
    """
    Mendeteksi kandidat memori dari pesan user + balasan asisten.
    Returns list dict {kategori, judul, isi, confidence} dengan confidence >= MIN_CONFIDENCE.
    """
    user_prompt = (
        f"Nama user di Telegram: {user_name}\n"
        f"Pesan user: {(user_message or '')[:1000]}\n"
        f"Balasan asisten: {(bot_response or '')[:800]}"
    )

    raw = ""
    try:
        from src.groq import chat_groq
        raw = await asyncio.wait_for(
            chat_groq(DETECT_SYSTEM_PROMPT, [], user_prompt, chat_id=0),
            timeout=12.0,
        )
    except Exception as e:
        logger.warning("Detektor memori (Groq) gagal: %s", str(e))

    if not raw or not raw.strip():
        try:
            raw = await _detect_with_gemini(f"{DETECT_SYSTEM_PROMPT}\n\n{user_prompt}")
        except Exception as e:
            logger.warning("Detektor memori (Gemini) gagal: %s", str(e))
            return []

    results: list[dict] = []
    for item in _extract_json_array(raw):
        if not isinstance(item, dict):
            continue
        kategori = str(item.get("kategori", "")).strip().lower()
        if kategori not in MEMORY_CATEGORIES:
            continue
        judul = str(item.get("judul", "")).strip()[:100]
        isi = str(item.get("isi", "")).strip()[:1800]
        if not judul or not isi:
            continue
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < MIN_CONFIDENCE:
            continue
        results.append({
            "kategori": kategori,
            "judul": judul,
            "isi": isi,
            "confidence": round(confidence, 2),
        })
    return results


# --- Anti-duplikat ---

def _normalize(text: str) -> str:
    low = (text or "").lower()
    low = re.sub(r"[^\w\s]", " ", low)
    return re.sub(r"\s+", " ", low).strip()


def _tokens(text: str) -> set:
    return set(_normalize(text).split())


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _has_new_info(candidate: dict, entry: dict) -> bool:
    """True bila isi kandidat membawa token baru di luar isi entri lama & stopwords."""
    new_tokens = _tokens(candidate.get("isi", "")) - _tokens(entry.get("isi", "")) - _STOPWORDS
    return len(new_tokens) >= 1


def _best_match(candidate: dict, entries: list[dict]) -> tuple[Optional[dict], float]:
    """Mencari entri lama paling mirip dengan kandidat (judul atau isi)."""
    best: Optional[dict] = None
    best_score = 0.0
    for entry in entries:
        score = max(
            _similarity(candidate.get("judul", ""), entry.get("title", "")),
            _similarity(candidate.get("isi", ""), entry.get("isi", "")),
        )
        if score > best_score:
            best, best_score = entry, score
    return best, best_score


# --- Orkestrator ---

async def maybe_autosave(
    chat_id: int,
    user_message: str,
    bot_response: str,
    user_name: str = "Teman",
) -> list[dict]:
    """
    Deteksi & simpan memori penting secara otomatis (best-effort, non-fatal).
    Dipanggil SETELAH balasan terkirim ke user. Returns daftar aksi
    [{'aksi': 'save'|'update'|'skip', 'judul': ...}].
    """
    if not chat_id or not user_message:
        return []
    try:
        return await asyncio.wait_for(
            _maybe_autosave_inner(chat_id, user_message, bot_response, user_name),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        logger.warning("Auto-save memori timeout (chat=%s)", chat_id)
    except Exception as e:
        logger.warning("Auto-save memori gagal (chat=%s): %s", chat_id, str(e))
    return []


async def _maybe_autosave_inner(
    chat_id: int,
    user_message: str,
    bot_response: str,
    user_name: str,
) -> list[dict]:
    settings = await get_memory_settings(chat_id)
    if not settings.get("enabled"):
        return []
    if not _is_worth_detecting(user_message):
        return []
    if not await _rate_limit_ok(chat_id):
        logger.info("Auto-save memori dilewati (rate limit) chat=%s", chat_id)
        return []

    candidates = await detect_memories(user_message, bot_response, user_name)
    aktif = settings.get("categories", MEMORY_CATEGORIES)
    candidates = [c for c in candidates if c["kategori"] in aktif]
    if not candidates:
        return []

    from src.notion import query_memory_entries, save_memory_entry, update_memory_page

    try:
        existing = await query_memory_entries()
    except Exception as e:
        logger.warning("Gagal query memori lama (lanjut simpan tanpa dedup): %s", str(e))
        existing = []

    hasil: list[dict] = []
    jumlah_simpan = 0
    sumber = (user_message or "").strip()[:200]

    for cand in candidates:
        match, score = _best_match(cand, existing)
        if match and score >= DEDUP_THRESHOLD:
            if _has_new_info(cand, match):
                res = await update_memory_page(
                    match.get("id", ""),
                    content=cand["isi"],
                    memory_type=CATEGORY_TO_JENIS[cand["kategori"]],
                    sumber=sumber,
                    confidence=cand["confidence"],
                )
                if isinstance(res, dict) and res.get("status") == "success":
                    jumlah_simpan += 1
                    hasil.append({"aksi": "update", "kategori": cand["kategori"], "judul": cand["judul"]})
                    await log_memory_action(chat_id, cand["kategori"], cand["judul"], "update", sumber)
                continue
            # duplikat persis -> skip
            hasil.append({"aksi": "skip", "kategori": cand["kategori"], "judul": cand["judul"]})
            continue

        res = await save_memory_entry(
            title=cand["judul"],
            content=cand["isi"],
            memory_type=CATEGORY_TO_JENIS[cand["kategori"]],
            sumber=sumber,
            confidence=cand["confidence"],
            source_message=sumber,
        )
        if isinstance(res, dict) and res.get("status") == "success":
            jumlah_simpan += 1
            hasil.append({"aksi": "save", "kategori": cand["kategori"], "judul": cand["judul"]})
            await log_memory_action(chat_id, cand["kategori"], cand["judul"], "save", sumber)
            # Cegah kandidat berikutnya di batch yang sama menduplikasi entri baru ini.
            if res.get("page_id"):
                existing.append({
                    "id": res.get("page_id"),
                    "title": cand["judul"],
                    "isi": cand["isi"],
                })

    if jumlah_simpan > 0:
        await _register_save(chat_id, jumlah_simpan)
        if settings.get("notify", True):
            await _notify(chat_id, [h for h in hasil if h["aksi"] in ("save", "update")])

    return hasil


async def _notify(chat_id: int, saved: list[dict]) -> None:
    """Kirim notifikasi halus (digabung satu pesan)."""
    if not saved:
        return
    lines = []
    for item in saved:
        if item["aksi"] == "save":
            lines.append(f"📝 Aku catat ya: \"{item['judul']}\"")
        else:
            lines.append(f"📝 Aku perbarui catatan: \"{item['judul']}\"")
    lines.append("")
    lines.append("Ketik /memory untuk lihat atau atur memori.")
    try:
        from src.handlers import send_telegram_message
        await send_telegram_message(chat_id, "\n".join(lines))
    except Exception as e:
        logger.warning("Gagal kirim notifikasi memori: %s", str(e))
