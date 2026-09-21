"""
Mesin grounding Oline — memastikan jawaban selalu bersumber dari DATA NYATA
(database / tool), bukan karangan model. Berlaku untuk intent yang butuh data
live/database.

Prinsip: sebelum model menjawab, sistem mengambil data nyata dari tool wajib
intent tersebut, menginjeksikannya ke konteks, lalu model merumuskan jawaban
berdasarkan data itu. Jika data tidak bisa diperoleh / argumen kurang, Oline
jujur menyatakan data tak tersedia atau meminta info, BUKAN menebak.
"""

import json
import logging
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# --- Status grounding ---
GROUNDED = "grounded"          # data nyata berhasil diambil & siap diinjeksi
UNAVAILABLE = "unavailable"    # tool gagal / data tidak tersedia
NEED_INFO = "need_info"        # butuh klarifikasi (argumen kurang)
SKIP = "skip"                  # tidak butuh grounding

GROUNDING_INTENTS = (
    "cuaca", "saham", "search", "lokasi", "design_reference",
    "jurnal", "gambar", "vercel_logs", "neo4j",
)


def _is_definitional(message: str) -> bool:
    """Deteksi pertanyaan definisi/penjelasan (tidak butuh data live)."""
    return bool(re.search(r"\b(apa itu|definisi|pengertian|arti)\b", message.lower()))


def _is_factual_query(message: str) -> bool:
    """
    Deteksi apakah pesan fast path (intent None) butuh grounding pencarian.
    Mengembalikan True untuk pertanyaan faktual/real-time; False untuk obrolan santai.
    """
    low = message.lower().strip()
    if not low:
        return False

    # Obrolan/balasan singkat yang tidak butuh pencarian
    small_talk = {
        "halo", "hai", "hi", "hello", "hey", "pagi", "siang", "sore", "malam",
        "apa kabar", "apa kabarmu", "gimana", "gimana kabar", "makasih",
        "terima kasih", "thanks", "ok", "oke", "sip", "santai", "iya", "ya",
        "baik", "bagus", "mantap", "kamu siapa", "siapa kamu",
    }

    # Buang seluruh frase sapaan/balasan; jika tak ada isi tersisa -> bukan faktual.
    remaining = low
    for phrase in small_talk:
        remaining = re.sub(rf"\b{re.escape(phrase)}\b", " ", remaining)
    remaining = re.sub(r"\s+", " ", remaining).strip()
    if not remaining or len(remaining) < 3:
        return False

    # Kata tanya / kata yang menandakan butuh informasi faktual (pada sisa teks)
    if re.search(
        r"\b(siapa|apa|kapan|dimana|di mana|berapa|kenapa|bagaimana|mengapa|apakah|"
        r"caranya|definisi|pengertian|arti)\b",
        remaining,
    ):
        return True

    # Frasa spesifik yang menandakan minta dicarikan informasi
    if re.search(r"\b(tolong cari|tolong jelaskan|tolong carikan|carikan|cariin|tolong info)\b", remaining):
        return True

    # Kata kunci berita/real-time/current
    if re.search(
        r"\b(terbaru|berita|sekarang|hari ini|harga|update|live|news|tahun ini|"
        r"tren|info|profil|sejarah|kapan berdiri)\b",
        remaining,
    ):
        return True

    return False


def _extract_city(message: str) -> Optional[dict]:
    low = message.lower().strip()
    m = re.search(r"(?:cuaca|suhu|panas|hujan)\s*(?:di|untuk|kota)?\s*([a-z][a-z\s\-]{1,40}?)(?:\?|\.|$)", low)
    if m:
        city = m.group(1).strip()
        stop = {"hari ini", "besok", "sekarang", "nanti", "ya", "dong", "kak", "nih", "saya", "aku"}
        if city and city not in stop:
            return {"city": city.title()}
    # Pesan pendek tanpa pola (mis. balasan "surabaya" setelah Oline minta kota):
    # dalam konteks intent cuaca, perlakukan sebagai nama kota.
    words = [w for w in low.split() if w]
    if 1 <= len(words) <= 3:
        candidate = low.rstrip("?!.")
        # Buang kualifikasi waktu di akhir ("sekarang", "hari ini", "besok", "nanti")
        candidate = re.sub(r"\s+(sekarang|hari ini|besok|nanti|nih|ya|dong)$", "", candidate).strip()
        # Hindari kata perintah/umum yang bukan kota
        if not any(k in low for k in ("cuaca", "suhu", "panas", "hujan", "gimana", "berapa", "di ", "tolong", "cek")):
            if candidate and not _is_definitional(candidate):
                return {"city": candidate.title()}
    return None


def _extract_ticker(message: str) -> Optional[dict]:
    low = message.lower()
    if re.search(r"\b(ihsg|index|indeks|market|bursa)\b", low):
        return {"ticker": "IHSG"}
    m = re.search(r"\b([A-Z]{3,5})\b", message)
    if m:
        return {"ticker": m.group(1)}
    return None


def _extract_category(message: str) -> Optional[dict]:
    low = message.lower()
    mapping = {
        "cafe": ["cafe", "kafe", "coffee", "kopi"],
        "restoran": ["restoran", "restaurant", "tempat makan"],
        "mall": ["mall", "mal"],
        "bank": ["bank", "atm"],
        "apotek": ["apotek", "farmasi"],
        "spbu": ["spbu", "pom bensin", "bensin"],
        "toko buku": ["toko buku"],
        "rumah sakit": ["rumah sakit", "klinik"],
    }
    for cat, keys in mapping.items():
        if any(k in low for k in keys):
            return {"category": cat}
    return None


def _extract_query(message: str) -> Optional[dict]:
    q = message.strip()
    return {"query": q} if q else None


def _extract_image(message: str) -> Optional[dict]:
    q = message.strip()
    return {"query": q, "max_results": 1} if q else None


# Spesifikasi grounding per intent.
# - tool: nama tool yang wajib dipanggil untuk grounding.
# - extract: fungsi (message -> dict argumen | None). None => butuh klarifikasi.
# - need: teks klarifikasi bila argumen kurang.
# - should_ground: fungsi (message -> bool) penjaga; bila False => SKIP.
_GROUNDING_SPECS: dict[str, dict[str, Any]] = {
    "cuaca": {
        "tool": "get_weather_forecast",
        "extract": _extract_city,
        "need": "kota mana yang mau kamu cek cuacanya? (misal: 'cuaca di Jakarta')",
        "should_ground": lambda m: not _is_definitional(m),
    },
    "saham": {
        "tool": "get_stock_price",
        "extract": _extract_ticker,
        "need": "ticker saham yang mau dicek apa? (misal: BBCA, TLKM, atau IHSG)",
        "should_ground": lambda m: not _is_definitional(m),
    },
    "search": {
        "tool": "search_internet",
        "extract": _extract_query,
        "need": "topik apa yang mau aku cari di internet?",
        "should_ground": lambda m: True,
    },
    "lokasi": {
        "tool": "get_nearby_places",
        "extract": _extract_category,
        "need": "kamu cari tempat apa? (misal: cafe, restoran, bank, apotek)",
        "should_ground": lambda m: True,
    },
    "design_reference": {
        "tool": "search_design_reference",
        "extract": _extract_query,
        "need": "referensi desain untuk apa? (misal: landing page toko buku)",
        "should_ground": lambda m: True,
    },
    "jurnal": {
        "tool": "get_journal_recap",
        "extract": lambda m: {},
        "need": None,
        "should_ground": lambda m: True,
    },
    "gambar": {
        "tool": "search_and_send_image",
        "extract": _extract_image,
        "need": "gambar apa yang mau kamu cari?",
        "should_ground": lambda m: True,
    },
    "vercel_logs": {
        "tool": "read_vercel_logs",
        "extract": lambda m: {"mode": "error"},
        "need": None,
        "should_ground": lambda m: True,
    },
    "neo4j": {
        "tool": "cari_aktivitas_neo4j",
        "extract": lambda m: {},
        "need": None,
        "should_ground": lambda m: True,
    },
}


def build_grounding_augment(tool_name: str, result: Any) -> str:
    """Membangun blok data nyata untuk ditambahkan ke system prompt."""
    try:
        payload = json.dumps(result, ensure_ascii=False)
    except Exception:
        payload = str(result)
    return (
        "\n\n## DATA NYATA (WAJIB gunakan data ini untuk menjawab, JANGAN mengarang "
        "atau menambahkan angka/fakta/URL di luar data ini)\n"
        f"Sumber tool: {tool_name}\n{payload}"
    )


async def prepare_grounding(
    chat_id: int,
    intent: Optional[str],
    user_message: str,
) -> tuple[str, Optional[str], Any, Optional[str]]:
    """
    Menyiapkan grounding untuk suatu intent.

    Returns:
        (status, tool_name, result, message)
        - status GROUNDED: tool_name & result siap diinjeksi.
        - status NEED_INFO: message = klarifikasi untuk user (argumen kurang).
        - status UNAVAILABLE: message = alasan data tak tersedia.
        - status SKIP: tidak butuh grounding.
    """
    if not intent:
        # Fast path (obrolan biasa): ground via pencarian untuk pertanyaan faktual/real-time.
        if _is_factual_query(user_message):
            q = user_message.strip()
            if not q:
                return SKIP, None, None, None
            from src.tools import execute_tool
            try:
                result = await execute_tool("search_internet", {"query": q}, chat_id=chat_id)
            except Exception as e:
                logger.warning("Grounding fast path (search) gagal: %s", str(e))
                return UNAVAILABLE, None, None, f"Pencarian gagal: {str(e)[:150]}"
            if isinstance(result, dict) and result.get("error"):
                return UNAVAILABLE, None, None, str(result.get("error"))[:250]
            return GROUNDED, "search_internet", result, None
        return SKIP, None, None, None

    spec = _GROUNDING_SPECS.get(intent)
    if not spec:
        return SKIP, None, None, None

    if not spec["should_ground"](user_message):
        return SKIP, None, None, None

    args = spec["extract"](user_message)
    if args is None:
        return NEED_INFO, None, None, spec.get("need") or "Info tambahan dibutuhkan."

    from src.tools import execute_tool
    try:
        result = await execute_tool(spec["tool"], dict(args), chat_id=chat_id)
    except Exception as e:
        logger.warning("Grounding tool %s gagal: %s", spec["tool"], str(e))
        return UNAVAILABLE, None, None, f"Tool {spec['tool']} gagal: {str(e)[:150]}"

    # Tool mengembalikan error -> data tak tersedia (jangan mengarang).
    if isinstance(result, dict) and result.get("error"):
        return UNAVAILABLE, None, None, str(result.get("error"))[:250]

    return GROUNDED, spec["tool"], result, None
