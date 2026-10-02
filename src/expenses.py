"""
Fitur pencatatan pengeluaran Oline (pengganti fitur jurnal lama).

Modul ini menangani logika yang tidak bergantung pada Telegram:
- Parsing nominal fleksibel (25rb, 25k, 25.000, Rp 25.000, 25000, 1,5jt).
- Deteksi pengeluaran dari chat bebas (kopi 25rb, beli bensin 50rb).
- Kategori otomatis (map keyword lokal, fallback AI bila tidak dikenal).
- State draft/konfirmasi, pengaturan user, anti-duplikat, audit log (KV).
- Builder teks rekap & hasil pencarian.

Penyimpanan transaksi final ada di Notion "Keuangan Oline"
(lihat src/notion.py: save_expense/query_expenses/archive_expense).
"""

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))

CATEGORIES = [
    "Makan & Minum",
    "Transport",
    "Belanja",
    "Rumah",
    "Kesehatan",
    "Hiburan",
    "Pendidikan",
    "Lain-lain",
]

CATEGORY_EMOJI = {
    "Makan & Minum": "🍔",
    "Transport": "🚗",
    "Belanja": "🛒",
    "Rumah": "🏠",
    "Kesehatan": "💊",
    "Hiburan": "🎮",
    "Pendidikan": "📚",
    "Lain-lain": "📦",
}

# Map keyword lokal -> kategori. Dicek dengan word boundary (kata utuh + sufiks ringan).
CATEGORY_KEYWORDS = {
    "Makan & Minum": [
        "makan", "minum", "kopi", "teh", "nasi", "ayam", "bakso", "mie", "mi",
        "sate", "martabak", "jajan", "snack", "camilan", "resto", "restoran",
        "warung", "kafe", "cafe", "coffee", "sarapan", "lunch", "dinner",
        "geprek", "seblak", "boba", "es", "jus", "roti", "kue", "gofood",
        "grabfood", "shopeefood", "makanan", "minuman", "catering", "catering",
        "indomie", "warteg", "padang", "pizza", "burger", "sushi", "dimsum",
    ],
    "Transport": [
        "grab", "gojek", "ojek", "ojol", "taksi", "taxi", "bensin", "bbm",
        "pertalite", "pertamax", "solar", "parkir", "tol", "kereta", "krl",
        "mrt", "lrt", "bus", "angkot", "tiket", "pesawat", "travel", "ongkir",
        "ongkos", "transport", "servis motor", "cuci mobil", "cuci motor",
    ],
    "Belanja": [
        "indomaret", "alfamart", "alfamidi", "superindo", "hypermart",
        "carrefour", "transmart", "belanja", "belanjaan", "groceries",
        "sembako", "supermarket", "minimarket", "shopee", "tokopedia", "lazada",
        "tiktok shop", "online shop", "olshop", "baju", "celana", "sepatu",
        "tas", "skincare", "makeup", "kosmetik", "sabun", "sampo", "odol",
        "tisu", "detergen", "perlengkapan",
    ],
    "Rumah": [
        "listrik", "pln", "air", "pdam", "internet", "wifi", "indihome",
        "telkom", "pulsa", "kuota internet", "gas", "elpiji", "sewa", "kontrakan",
        "kos", "iuran", "sampah", "keamanan", "perbaikan rumah", "servis ac",
        "token listrik", "tagihan", "bpjs", "asuransi",
    ],
    "Kesehatan": [
        "obat", "apotek", "dokter", "klinik", "rumah sakit", "rs", "puskesmas",
        "vitamin", "suplemen", "periksa", "lab", "dentist", "dokter gigi",
        "masker", "tes kesehatan", "medical", "fisioterapi", "bidan",
    ],
    "Hiburan": [
        "nonton", "bioskop", "film", "game", "gaming", "top up game", "topup",
        "netflix", "spotify", "viu", "disney", "youtube premium", "langganan",
        "konser", "karaoke", "liburan", "wisata", "rekreasi", "healing",
        "steam", "playstation", "nintendo",
    ],
    "Pendidikan": [
        "buku", "kursus", "les", "bimbel", "sekolah", "kuliah", "spp", "uang pangkal",
        "semester", "skripsi", "jurnal", "pelatihan", "workshop", "seminar",
        "udemy", "coursera", "alza", "stationery", "atk", "alat tulis",
    ],
}

# Kata yang menandakan nominal uang eksplisit (suffix).
_CURRENCY_SUFFIX = r"(rb|ribu|ribuan|k|jt|juta|jutaan|m)"
_AMOUNT_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?:(?:rp)\.?\s*)?"
    r"(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*"
    + _CURRENCY_SUFFIX + r"?(?![A-Za-z])",
    re.IGNORECASE,
)

# Kata kerja konteks pengeluaran.
_EXPENSE_VERBS = [
    "beli", "membeli", "bayar", "membayar", "habis", "abis", "jajan",
    "isi", "top up", "topup", "scan", "tadi", "buat", "belanja",
]

# Konteks yang bukan uang (cegah false positive: "umur 25", "jam 10").
_BLACKLIST_CONTEXT = [
    "umur", "usia", "tahun", "tanggal", "tgl", "jam", "pukul", "menit",
    "detik", "nomor", "no", "nik", "ukuran", "size", "berat", "tinggi",
    "km", "kg", "cm", "mm", "ml", "liter", "persen", "%", "halaman", "hal",
    "bab", "ipk", "golongan", "kelas", "tingkat", "lantai", "blok", "rt", "rw",
    "orang", "buah", "pcs", "unit", "biji", "ekor", "lembar",
]

_QUESTION_STARTS = (
    "berapa", "apa", "apakah", "kenapa", "mengapa", "gimana", "bagaimana",
    "kapan", "dimana", "di mana", "mana", "siapa",
)

# Filler yang dibuang dari deskripsi.
_FILLER_WORDS = [
    "catat", "pengeluaran", "pengeluaranku", "beli", "membeli", "bayar",
    "membayar", "habis", "abis", "buat", "untuk", "tadi", "barusan",
    "jajan", "dah", "udah", "sudah", "lagi", "dong", "ya", "nih", "ku",
]

RECEIPT_VISION_PROMPT = (
    "Baca struk/nota belanja ini dengan teliti. Ekstrak data berikut dan balas "
    "HANYA dengan JSON valid tanpa markdown, dengan skema: "
    '{"total": <angka total pembayaran tanpa titik, mis. 87500>, '
    '"toko": "<nama toko, kosongkan bila tidak ada>", '
    '"tanggal": "<YYYY-MM-DD, kosongkan bila tidak ada>", '
    '"items": ["<daftar item singkat>"]}. '
    "Jika bukan struk belanja, balas {\"total\": 0}."
)

# Kata kunci caption foto yang menandakan struk belanja.
RECEIPT_CAPTION_KEYWORDS = ["struk", "nota", "bon", "receipt", "nota belanja"]

DEFAULT_SETTINGS = {
    "autosave": True,
    "confirm": False,
}

SETTINGS_PREFIX = "expense_settings"
DRAFT_PREFIX = "expense_draft"
EDIT_PREFIX = "expense_edit"
SCAN_PREFIX = "expense_scan"
TOTAL_PREFIX = "expense_total"
DEDUP_PREFIX = "expense_dedup"
LOG_PREFIX = "expense_log"
INDEX_PREFIX = "expense_index"
BACKUP_PREFIX = "expense_backup"

SETTINGS_TTL = 365 * 24 * 3600
DRAFT_TTL = 1800
EDIT_TTL = 900
SCAN_TTL = 600
TOTAL_TTL = 90 * 24 * 3600
DEDUP_TTL = 24 * 3600
LOG_TTL = 30 * 24 * 3600
INDEX_TTL = 30 * 24 * 3600
BACKUP_TTL = 7 * 24 * 3600
LOG_MAX = 100
BACKUP_MAX = 50

# Confidence minimum agar entri chat boleh diproses; >= AUTO_SAVE_CONFIDENCE
# boleh langsung disimpan (tanpa konfirmasi) bila setting autosave aktif.
MIN_DETECT_CONFIDENCE = 0.6
AUTO_SAVE_CONFIDENCE = 0.85

_MONTHS_ID = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "Mei", 6: "Jun",
    7: "Jul", 8: "Agu", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Des",
}


# --- Utilitas waktu & format ---

def wib_now() -> datetime:
    return datetime.now(WIB)


def today_iso() -> str:
    return wib_now().strftime("%Y-%m-%d")


def format_rupiah(nominal: Any) -> str:
    """Format nominal ke Rupiah: 25000 -> 'Rp25.000'."""
    try:
        value = int(round(float(nominal)))
    except (TypeError, ValueError):
        return "Rp0"
    sign = "-" if value < 0 else ""
    return f"{sign}Rp{abs(value):,}".replace(",", ".")


def format_short_date(iso_date: str) -> str:
    """YYYY-MM-DD -> '30 Sep 2026'. Mengembalikan input apa adanya bila gagal."""
    try:
        dt = datetime.strptime((iso_date or "")[:10], "%Y-%m-%d")
        return f"{dt.day} {_MONTHS_ID.get(dt.month, dt.strftime('%b'))} {dt.year}"
    except (ValueError, TypeError):
        return iso_date or "-"


def parse_periode(text: str) -> tuple[str, str, str]:
    """
    Menerjemahkan kata kunci periode menjadi (start_iso, end_iso, label).
    Default: bulan ini.
    """
    low = (text or "").lower()
    now = wib_now()
    end_iso = now.strftime("%Y-%m-%d")

    if "hari" in low:
        return end_iso, end_iso, "Hari Ini"
    if "minggu" in low or "7 hari" in low or "pekan" in low:
        start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        return start, end_iso, "Minggu Ini"
    if "tahun" in low:
        return f"{now.year}-01-01", end_iso, "Tahun Ini"
    start = now.replace(day=1).strftime("%Y-%m-%d")
    return start, end_iso, "Bulan Ini"


# --- Parsing nominal ---

def parse_amount(token: str) -> Optional[int]:
    """
    Parse satu token nominal menjadi int Rupiah.
    Mendukung: 25rb, 25 rb, 25ribu, 25k, 1,5jt, 1.5 juta, 25.000, 25,000,
    Rp 25.000, Rp25.000, 25000.
    Mengembalikan None bila tidak valid.
    """
    if token is None:
        return None
    raw = str(token).strip().lower()
    if not raw:
        return None

    match = _AMOUNT_RE.search(raw)
    if not match:
        return None

    number_str = match.group(1)
    suffix = (match.group(2) or "").lower()
    if not number_str:
        return None

    try:
        if suffix in ("rb", "ribu", "ribuan", "k"):
            multiplier = 1_000
        elif suffix in ("jt", "juta", "jutaan", "m"):
            multiplier = 1_000_000
        else:
            multiplier = 1

        # Normalisasi pemisah ribuan/desimal.
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", number_str):
            value = float(number_str.replace(".", ""))
        elif re.fullmatch(r"\d{1,3}(,\d{3})+", number_str):
            value = float(number_str.replace(",", ""))
        elif multiplier > 1 and re.fullmatch(r"\d+[.,]\d+", number_str):
            # "1,5jt" / "1.5k" -> desimal
            value = float(number_str.replace(",", "."))
        else:
            value = float(number_str.replace(",", "").replace(".", ""))

        result = int(round(value * multiplier))
        return result if result > 0 else None
    except (ValueError, TypeError):
        return None


def _has_currency_marker(part: str, match: re.Match) -> bool:
    """True bila kandidat nominal punya penanda mata uang eksplisit (rb/k/Rp/format ribuan)."""
    prefix = part[max(0, match.start() - 4):match.start()].lower()
    token_low = match.group(0).lower().strip()
    if match.group(2):
        return True
    if token_low.startswith("rp") or token_low.startswith("rp."):
        return True
    if re.search(r"\brp\.?\s*$", prefix):
        return True
    return bool(re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", match.group(1)))


def _find_amount(part: str) -> Optional[tuple[int, bool, re.Match]]:
    """
    Cari satu kandidat nominal di sebuah bagian teks.
    Returns (nominal, punya_penanda_mata_uang, match) atau None.
    Bila ada >1 kandidat, pilih yang punya penanda; jika tidak ada, None (ambigu).
    """
    matches = list(_AMOUNT_RE.finditer(part))
    if not matches:
        return None

    with_marker = [m for m in matches if _has_currency_marker(part, m)]

    chosen = None
    has_marker = False
    if len(with_marker) == 1:
        chosen = with_marker[0]
        has_marker = True
    elif len(matches) == 1:
        chosen = matches[0]
    else:
        return None

    nominal = parse_amount(chosen.group(0))
    if nominal is None:
        return None
    return nominal, has_marker, chosen


def _clean_description(part: str, amount_match: re.Match) -> str:
    """Buang nominal & filler dari bagian teks, sisakan deskripsi."""
    text = (part[:amount_match.start()] + " " + part[amount_match.end():]).strip()
    text = re.sub(r"\brp\.?\s*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s&\-]", " ", text)
    words = [w for w in text.split() if w]
    while words and words[0] in _FILLER_WORDS:
        words.pop(0)
    while words and words[-1] in _FILLER_WORDS:
        words.pop()
    return " ".join(words).strip()


def detect_expense_entry(text: str) -> Optional[dict]:
    """
    Deteksi pengeluaran dari chat bebas.

    Returns dict {"items": [{nominal, deskripsi, kategori, confidence}],
    "confidence": float, "sumber": "chat"} atau None bila bukan pengeluaran.

    Confidence tinggi (>= 0.85) bila ada penanda mata uang eksplisit (rb/k/Rp).
    """
    if not text or not text.strip():
        return None

    low = text.lower().strip()
    if low.startswith(_QUESTION_STARTS):
        return None

    parts = [p.strip() for p in re.split(r"\s*[,;]\s*|\s+dan\s+", low) if p.strip()]
    if not parts:
        return None

    items: list[dict] = []
    multi = len(parts) > 1

    for part in parts:
        parsed = _find_amount(part)
        if not parsed:
            continue
        nominal, has_marker, match = parsed

        has_verb = any(re.search(rf"\b{re.escape(v)}\b", part) for v in _EXPENSE_VERBS)
        local_cat = categorize_local(part)
        blacklisted = any(re.search(rf"\b{re.escape(b)}\b", part) for b in _BLACKLIST_CONTEXT)

        if blacklisted and not has_marker:
            continue

        confidence = 0.0
        if has_marker:
            confidence = 0.95
        elif has_verb:
            confidence = 0.8
            if nominal < 1000:
                nominal *= 1000
                confidence = 0.7
        elif local_cat and nominal >= 1000:
            confidence = 0.75
        elif nominal >= 1000:
            confidence = 0.8
        elif local_cat:
            nominal *= 1000
            confidence = 0.7
        else:
            continue

        deskripsi = _clean_description(part, match)
        if not deskripsi:
            if local_cat:
                deskripsi = local_cat
            elif has_verb:
                deskripsi = "Pengeluaran"
            else:
                continue

        items.append({
            "nominal": nominal,
            "deskripsi": deskripsi[:80],
            "kategori": local_cat,
            "confidence": round(confidence, 2),
        })

    if not items:
        return None

    overall = min(item["confidence"] for item in items)
    if multi:
        # Multi-item rawan salah pisah; wajib lewat konfirmasi.
        overall = min(overall, 0.7)
    if overall < MIN_DETECT_CONFIDENCE:
        return None

    return {"items": items, "confidence": round(overall, 2), "sumber": "chat"}


# --- Kategori otomatis ---

def categorize_local(deskripsi: str) -> Optional[str]:
    """Kategori via map keyword lokal. None bila tidak ada yang cocok."""
    if not deskripsi:
        return None
    low = deskripsi.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}\b", low):
                return category
    return None


_CATEGORIZE_SYSTEM = (
    "Kamu adalah pengkategorisasi pengeluaran pribadi. "
    "Balas HANYA nama satu kategori dari daftar berikut, tanpa penjelasan: "
    + ", ".join(CATEGORIES)
)


async def categorize_expense(deskripsi: str, use_ai: bool = True) -> str:
    """
    Kategori otomatis: map lokal dulu (instan), fallback AI bila tidak dikenal.
    Selalu mengembalikan salah satu dari CATEGORIES.
    """
    local = categorize_local(deskripsi)
    if local:
        return local
    if not use_ai or not (deskripsi or "").strip():
        return "Lain-lain"

    prompt = f"Pengeluaran: \"{deskripsi.strip()[:200]}\""

    try:
        from src.groq import chat_groq

        raw = await asyncio.wait_for(
            chat_groq(_CATEGORIZE_SYSTEM, [], prompt, chat_id=0),
            timeout=6.0,
        )
        cat = _match_category(raw)
        if cat:
            return cat
    except Exception as e:
        logger.warning("Kategori AI (Groq) gagal: %s", str(e))

    try:
        from src.gemini import _get_client

        client = _get_client()
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model="gemini-3.5-flash-lite",
                contents=f"{_CATEGORIZE_SYSTEM}\n\n{prompt}",
            ),
            timeout=8.0,
        )
        cat = _match_category(getattr(response, "text", "") or "")
        if cat:
            return cat
    except Exception as e:
        logger.warning("Kategori AI (Gemini) gagal: %s", str(e))

    return "Lain-lain"


def _match_category(raw: str) -> Optional[str]:
    if not raw:
        return None
    low = raw.lower()
    for cat in CATEGORIES:
        if cat.lower() in low:
            return cat
    return None


# --- Parsing hasil vision struk ---

def _extract_json_object(raw: str) -> Optional[dict]:
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
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _normalize_receipt_date(raw: Any) -> str:
    """Terima berbagai format tanggal struk, kembalikan YYYY-MM-DD (fallback hari ini)."""
    if not raw:
        return today_iso()
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text[:11], fmt).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return match.group(0)
    return today_iso()


def parse_receipt_result(raw: str) -> Optional[dict]:
    """
    Parse output Moondream menjadi draft pengeluaran.
    Returns dict {nominal, deskripsi, toko, tanggal, catatan, sumber} atau None.
    """
    data = _extract_json_object(raw)

    nominal = None
    toko = ""
    tanggal_raw = ""
    items: list[str] = []

    if data:
        nominal = parse_amount(str(data.get("total") or data.get("nominal") or data.get("total_pembayaran") or ""))
        toko = str(data.get("toko") or data.get("store") or "").strip()[:80]
        tanggal_raw = data.get("tanggal") or data.get("date") or ""
        raw_items = data.get("items") or data.get("item") or []
        if isinstance(raw_items, list):
            items = [str(i).strip()[:60] for i in raw_items if str(i).strip()][:10]
        elif isinstance(raw_items, str) and raw_items.strip():
            items = [raw_items.strip()[:60]]

    if not nominal:
        # Fallback regex: cari angka di baris "total".
        match = re.search(r"(?:total|jumlah|grand\s*total)[^\d]{0,20}([\d.,]+)", (raw or "").lower())
        if match:
            nominal = parse_amount(match.group(1))
    if not nominal:
        match = re.search(r"rp\.?\s*([\d.,]+)", (raw or "").lower())
        if match:
            nominal = parse_amount(match.group(1))
    if not nominal:
        return None

    if not toko:
        match = re.search(r"(?:toko|store|toserba|minimarket|market)[:\s]+([^\n,]{2,50})", (raw or ""), re.IGNORECASE)
        if match:
            toko = match.group(1).strip()[:80]

    deskripsi = toko or (items[0] if items else "Belanja struk")
    return {
        "nominal": nominal,
        "deskripsi": deskripsi[:80],
        "toko": toko,
        "tanggal": _normalize_receipt_date(tanggal_raw),
        "catatan": "; ".join(items)[:500],
        "sumber": "struk",
    }


# --- Pengaturan user (KV) ---

async def get_expense_settings(chat_id: int) -> dict:
    """Pengaturan pengeluaran user (default: autosave ON, konfirmasi OFF)."""
    from src.kv import get_cache

    settings = dict(DEFAULT_SETTINGS)
    try:
        raw = await get_cache(f"{SETTINGS_PREFIX}:{chat_id}")
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                if isinstance(data.get("autosave"), bool):
                    settings["autosave"] = data["autosave"]
                if isinstance(data.get("confirm"), bool):
                    settings["confirm"] = data["confirm"]
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Error parsing expense settings: %s", str(e))
    return settings


async def set_expense_settings(chat_id: int, **updates) -> bool:
    """Simpan pengaturan pengeluaran user (autosave/confirm)."""
    from src.kv import set_cache

    settings = await get_expense_settings(chat_id)
    if isinstance(updates.get("autosave"), bool):
        settings["autosave"] = updates["autosave"]
    if isinstance(updates.get("confirm"), bool):
        settings["confirm"] = updates["confirm"]
    return await set_cache(
        f"{SETTINGS_PREFIX}:{chat_id}",
        json.dumps(settings, ensure_ascii=False),
        ttl_seconds=SETTINGS_TTL,
    )


# --- Draft & state konfirmasi (KV) ---

async def save_draft(chat_id: int, draft: dict) -> bool:
    from src.kv import set_cache

    draft["created_at"] = wib_now().isoformat()
    return await set_cache(
        f"{DRAFT_PREFIX}:{chat_id}",
        json.dumps(draft, ensure_ascii=False),
        ttl_seconds=DRAFT_TTL,
    )


async def get_draft(chat_id: int) -> Optional[dict]:
    from src.kv import get_cache

    try:
        raw = await get_cache(f"{DRAFT_PREFIX}:{chat_id}")
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("items"):
                return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


async def clear_draft(chat_id: int) -> bool:
    from src.kv import del_cache

    return await del_cache(f"{DRAFT_PREFIX}:{chat_id}")


async def set_edit_state(chat_id: int, active: bool = True) -> bool:
    from src.kv import del_cache, set_cache

    if not active:
        return await del_cache(f"{EDIT_PREFIX}:{chat_id}")
    return await set_cache(f"{EDIT_PREFIX}:{chat_id}", "1", ttl_seconds=EDIT_TTL)


async def is_edit_state(chat_id: int) -> bool:
    from src.kv import get_cache

    return bool(await get_cache(f"{EDIT_PREFIX}:{chat_id}"))


async def set_scan_pending(chat_id: int, active: bool = True) -> bool:
    from src.kv import del_cache, set_cache

    if not active:
        return await del_cache(f"{SCAN_PREFIX}:{chat_id}")
    return await set_cache(f"{SCAN_PREFIX}:{chat_id}", "1", ttl_seconds=SCAN_TTL)


async def is_scan_pending(chat_id: int) -> bool:
    from src.kv import get_cache

    return bool(await get_cache(f"{SCAN_PREFIX}:{chat_id}"))


# --- Total harian (KV) ---

async def add_daily_total(chat_id: int, nominal: int, date_iso: Optional[str] = None) -> None:
    """Tambah total pengeluaran harian secara atomik (untuk balasan instan)."""
    if not nominal:
        return
    from src.kv import _kv_request

    try:
        date_str = date_iso or today_iso()
        key = f"{TOTAL_PREFIX}:{chat_id}:{date_str}"
        res = await _kv_request(["INCRBY", key, str(int(nominal))])
        if res is not None:
            await _kv_request(["EXPIRE", key, str(TOTAL_TTL)])
    except Exception as e:
        logger.warning("Gagal menambah total harian: %s", str(e))


async def get_daily_total(chat_id: int, date_iso: Optional[str] = None) -> int:
    from src.kv import _kv_request

    try:
        date_str = date_iso or today_iso()
        res = await _kv_request(["GET", f"{TOTAL_PREFIX}:{chat_id}:{date_str}"])
        if res and res.get("result"):
            return int(res["result"])
    except Exception:
        pass
    return 0


# --- Anti-duplikat ---

def _dedup_hash(deskripsi: str, nominal: int, tanggal: str) -> str:
    norm = re.sub(r"\s+", " ", (deskripsi or "").lower()).strip()
    payload = f"{norm}|{int(nominal)}|{tanggal}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


async def is_duplicate(chat_id: int, deskripsi: str, nominal: int, tanggal: str) -> bool:
    from src.kv import get_cache

    return bool(await get_cache(f"{DEDUP_PREFIX}:{chat_id}:{_dedup_hash(deskripsi, nominal, tanggal)}"))


async def mark_duplicate(chat_id: int, deskripsi: str, nominal: int, tanggal: str) -> None:
    from src.kv import set_cache

    await set_cache(
        f"{DEDUP_PREFIX}:{chat_id}:{_dedup_hash(deskripsi, nominal, tanggal)}",
        "1",
        ttl_seconds=DEDUP_TTL,
    )


# --- Audit log (KV) ---

async def log_expense_action(chat_id: int, action: str, data: dict) -> None:
    """Catat aksi pengeluaran ke audit log KV (maks 100 entri, TTL 30 hari)."""
    from src.kv import get_cache, set_cache

    try:
        key = f"{LOG_PREFIX}:{chat_id}"
        raw = await get_cache(key)
        logs = json.loads(raw) if raw else []
        if not isinstance(logs, list):
            logs = []
        logs.append({
            "waktu": wib_now().strftime("%Y-%m-%d %H:%M:%S"),
            "aksi": action,
            "nominal": data.get("nominal"),
            "kategori": data.get("kategori"),
            "sumber": data.get("sumber"),
            "deskripsi": (data.get("deskripsi") or "")[:80],
        })
        await set_cache(key, json.dumps(logs[-LOG_MAX:], ensure_ascii=False), ttl_seconds=LOG_TTL)
    except Exception as e:
        logger.warning("Gagal menulis audit log pengeluaran: %s", str(e))


# --- Index ID pendek (KV) ---

def short_page_id(page_id: str) -> str:
    """ID pendek 6 karakter dari page_id Notion untuk perintah hapus."""
    clean = re.sub(r"[^a-fA-F0-9]", "", page_id or "")
    return clean[-6:].upper() if clean else ""


async def save_expense_index(chat_id: int, items: list[dict]) -> None:
    """Simpan index short_id -> page_id agar user bisa /pengeluaran hapus <id>."""
    if not items:
        return
    from src.kv import get_cache, set_cache

    try:
        key = f"{INDEX_PREFIX}:{chat_id}"
        raw = await get_cache(key)
        index = json.loads(raw) if raw else {}
        if not isinstance(index, dict):
            index = {}
        for item in items:
            sid = short_page_id(item.get("page_id", ""))
            if sid:
                index[sid] = {
                    "page_id": item.get("page_id"),
                    "deskripsi": item.get("deskripsi", ""),
                    "nominal": item.get("nominal", 0),
                    "tanggal": item.get("tanggal", ""),
                }
        # Batasi index agar tidak tumbuh tanpa batas.
        if len(index) > 200:
            index = dict(list(index.items())[-200:])
        await set_cache(key, json.dumps(index, ensure_ascii=False), ttl_seconds=INDEX_TTL)
    except Exception as e:
        logger.warning("Gagal menyimpan index pengeluaran: %s", str(e))


async def resolve_expense_index(chat_id: int, short_id: str) -> Optional[dict]:
    from src.kv import get_cache

    try:
        raw = await get_cache(f"{INDEX_PREFIX}:{chat_id}")
        if raw:
            index = json.loads(raw)
            if isinstance(index, dict):
                return index.get((short_id or "").strip().upper())
    except (json.JSONDecodeError, TypeError):
        pass
    return None


# --- Backup lokal saat Notion down (best-effort) ---

async def backup_failed_items(chat_id: int, items: list[dict]) -> None:
    from src.kv import get_cache, set_cache

    try:
        key = f"{BACKUP_PREFIX}:{chat_id}"
        raw = await get_cache(key)
        backup = json.loads(raw) if raw else []
        if not isinstance(backup, list):
            backup = []
        backup.extend(items)
        await set_cache(key, json.dumps(backup[-BACKUP_MAX:], ensure_ascii=False), ttl_seconds=BACKUP_TTL)
    except Exception as e:
        logger.warning("Gagal backup pengeluaran ke KV: %s", str(e))


async def pop_backup_items(chat_id: int) -> list[dict]:
    """Ambil & hapus antrean backup untuk dicoba simpan ulang."""
    from src.kv import del_cache, get_cache

    try:
        key = f"{BACKUP_PREFIX}:{chat_id}"
        raw = await get_cache(key)
        if not raw:
            return []
        items = json.loads(raw)
        await del_cache(key)
        return items if isinstance(items, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# --- Builder teks ---

def item_line(item: dict) -> str:
    """Satu baris ringkas entri: 'Rp25.000 — kopi (Makan & Minum)'."""
    kategori = item.get("kategori") or "Lain-lain"
    emoji = CATEGORY_EMOJI.get(kategori, "📦")
    line = f"{format_rupiah(item.get('nominal'))} — {item.get('deskripsi') or '-'} ({emoji} {kategori})"
    if item.get("toko"):
        line += f" | {item['toko']}"
    return line


def build_saved_text(saved: list[dict], total_today: int, skipped: int = 0, failed: int = 0) -> str:
    if len(saved) == 1:
        item = saved[0]
        head = f"✅ Tercatat: {item_line(item)}"
    else:
        head = "✅ Tercatat:\n" + "\n".join(f"• {item_line(i)}" for i in saved)

    lines = [head]
    if skipped:
        lines.append(f"⚠️ {skipped} entri duplikat dilewati.")
    if failed:
        lines.append(f"⚠️ {failed} entri gagal disimpan (dicadangkan lokal, akan dicoba lagi).")
    lines.append(f"Total hari ini: {format_rupiah(total_today)}")
    return "\n".join(lines)


def build_confirm_text(draft: dict) -> str:
    items = draft.get("items", [])
    sumber = draft.get("sumber", "chat")

    if sumber == "struk" and len(items) == 1:
        item = items[0]
        lines = [
            "📸 Struk terbaca:",
            f"• Total: {format_rupiah(item.get('nominal'))}",
        ]
        if item.get("toko"):
            lines.append(f"• Toko: {item['toko']}")
        lines.append(f"• Tanggal: {format_short_date(item.get('tanggal') or today_iso())}")
        if item.get("kategori"):
            lines.append(f"• Kategori: {CATEGORY_EMOJI.get(item['kategori'], '📦')} {item['kategori']}")
        return "\n".join(lines)

    if len(items) == 1:
        return "🧾 Konfirmasi pengeluaran:\n" + f"• {item_line(items[0])}"
    return "🧾 Konfirmasi pengeluaran:\n" + "\n".join(f"• {item_line(i)}" for i in items)


def build_recap_text(entries: list[dict], label: str) -> str:
    """Bangun teks rekap: total + breakdown kategori terbesar."""
    if not entries:
        return f"📊 Belum ada pengeluaran {label.lower()}."

    total = sum(int(e.get("nominal") or 0) for e in entries)
    agg: dict[str, int] = {}
    for entry in entries:
        cat = entry.get("kategori") or "Lain-lain"
        agg[cat] = agg.get(cat, 0) + int(entry.get("nominal") or 0)

    lines = [f"📊 Pengeluaran {label}: {format_rupiah(total)}"]
    lines.append("")
    lines.append("Kategori terbesar:")
    for cat, value in sorted(agg.items(), key=lambda kv: kv[1], reverse=True):
        pct = round(value / total * 100) if total else 0
        emoji = CATEGORY_EMOJI.get(cat, "📦")
        lines.append(f"• {emoji} {cat}: {format_rupiah(value)} ({pct}%)")
    lines.append("")
    lines.append(f"Total {len(entries)} transaksi.")
    return "\n".join(lines)


def build_search_text(entries: list[dict], keyword: str) -> str:
    if not entries:
        return f"🔍 Tidak ada pengeluaran yang cocok dengan \"{keyword}\"."
    lines = [f"🔍 Hasil pencarian \"{keyword}\" ({len(entries)} entri):", ""]
    for entry in entries[:20]:
        sid = entry.get("short_id") or short_page_id(entry.get("id", ""))
        lines.append(
            f"• [{sid}] {format_short_date(entry.get('tanggal', ''))} — "
            f"{item_line(entry)}"
        )
    if len(entries) > 20:
        lines.append(f"... dan {len(entries) - 20} entri lain.")
    return "\n".join(lines)
