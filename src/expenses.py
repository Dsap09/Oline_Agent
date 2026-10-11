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

# Sumber dana (nilai kanonik HARUS sama persis dengan opsi properti
# "sumber dana" di database Notion "Keuangan Oline").
SUMBER_OPTIONS = [
    "Cash", "BNI", "Bank Jago", "Shopee Pay", "Ovo", "Gopay", "Dana",
]

SUMBER_EMOJI = {
    "Cash": "💵",
    "BNI": "🏦",
    "Bank Jago": "🏦",
    "Shopee Pay": "📱",
    "Ovo": "📱",
    "Gopay": "📱",
    "Dana": "📱",
}

# Bucket untuk entri lama / sumber yang tidak diketahui.
SUMBER_UNKNOWN = "Tidak diketahui"

# Slug tombol callback Telegram -> nilai kanonik.
SUMBER_SLUGS = {
    "cash": "Cash",
    "bni": "BNI",
    "jago": "Bank Jago",
    "shopeepay": "Shopee Pay",
    "ovo": "Ovo",
    "gopay": "Gopay",
    "dana": "Dana",
}

# Keyword deteksi sumber dana (case-insensitive). Dicocokkan dengan word
# boundary; "jago" & "dana" punya aturan posisi khusus di parse_sumber.
SUMBER_KEYWORDS = [
    ("Bank Jago", ["bank jago", "jago"]),
    ("Shopee Pay", ["shopee pay", "shopeepay"]),
    ("Gopay", ["gopay", "go pay"]),
    ("Cash", ["cash", "tunai", "uang fisik"]),
    ("BNI", ["bni", "bank bni"]),
    ("Ovo", ["ovo"]),
    ("Dana", ["dana"]),
]

# Map keyword lokal -> kategori. Dicek dengan word boundary (kata utuh + sufiks ringan).
CATEGORY_KEYWORDS = {
    "Makan & Minum": [
        "makan", "minum", "kopi", "teh", "nasi", "ayam", "bakso", "mie", "mi",
        "sate", "martabak", "jajan", "snack", "camilan", "resto", "restoran",
        "warung", "kafe", "cafe", "coffee", "sarapan", "lunch", "dinner",
        "geprek", "seblak", "boba", "es", "jus", "roti", "kue", "gofood",
        "grabfood", "shopeefood", "makanan", "minuman", "catering", "catering",
        "indomie", "warteg", "padang", "pizza", "burger", "sushi", "dimsum",
        # Item minimarket (struk) yang umum:
        "air mineral", "air putih", "air galon", "galon", "aqua", "aquviva",
        "le minerale", "cleo", "susu", "ultra", "indomilk", "cimory", "yogurt",
        "yoghurt", "biskuit", "wafer", "chitato", "oreo", "coklat", "chocolate",
        "permen", "es krim", "ice cream", "pocari", "mizone", "isotonik",
        "teh botol", "juice", "sereal", "cereal", "keju", "mentega", "telur",
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
        "listrik", "pln", "pdam", "tagihan air", "air pdam", "internet", "wifi", "indihome",
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
    "Baca foto struk/nota belanja ini dengan sangat teliti. Balas HANYA satu objek "
    "JSON valid tanpa markdown/penjelasan, dengan skema: "
    '{"toko": "<nama toko>", "tanggal": "YYYY-MM-DD", "jam": "HH:MM", '
    '"items": [{"nama": "<nama item>", "qty": <angka>, "harga": <harga satuan>, '
    '"subtotal": <qty x harga>}], '
    '"diskon": <total potongan, 0 bila tidak ada>, '
    '"total_item": <jumlah sebelum diskon>, '
    '"total_belanja": <jumlah yang dibayar setelah diskon>, '
    '"metode_bayar": "<tunai/qris/debit>", "kasir": "<nama>"}. '
    "PENTING: tulis SEMUA baris item beserta harganya, jangan diringkas; "
    "total_item = jumlah sebelum diskon; total_belanja = yang benar-benar dibayar "
    "setelah diskon. Jika bukan struk belanja, balas {\"total_belanja\": 0}."
)

RECEIPT_ITEMS_PROMPT = (
    "Baca semua baris item pada struk belanja ini. Balas HANYA JSON valid: "
    '{"items": [{"nama": "<nama item>", "qty": <angka>, "harga": <harga satuan>, '
    '"subtotal": <subtotal>}], "diskon": <potongan, 0 bila tidak ada>, '
    '"total_item": <jumlah sebelum diskon>, "total_belanja": <jumlah dibayar>}. '
    "Sertakan semua item, jangan diringkas."
)

# Kata kunci caption foto yang menandakan struk belanja.
RECEIPT_CAPTION_KEYWORDS = ["struk", "nota", "bon", "receipt", "nota belanja"]

DEFAULT_SETTINGS = {
    "autosave": True,
    "confirm": False,
    # None = tanya sumber dana tiap kali; string = pakai default itu.
    "default_source": None,
}

SETTINGS_PREFIX = "expense_settings"
DRAFT_PREFIX = "expense_draft"
EDIT_PREFIX = "expense_edit"
SCAN_PREFIX = "expense_scan"
SRC_PREFIX = "expense_src"
TOTAL_PREFIX = "expense_total"
DEDUP_PREFIX = "expense_dedup"
LOG_PREFIX = "expense_log"
INDEX_PREFIX = "expense_index"
BACKUP_PREFIX = "expense_backup"

SETTINGS_TTL = 365 * 24 * 3600
DRAFT_TTL = 1800
EDIT_TTL = 900
SCAN_TTL = 600
SRC_TTL = 900
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
        return f"{dt.day:02d} {_MONTHS_ID.get(dt.month, dt.strftime('%b'))} {dt.year}"
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


def parse_sumber(text: str) -> Optional[str]:
    """
    Deteksi sumber dana dari teks (case-insensitive, word boundary).
    Nilai kanonik: Cash/BNI/Bank Jago/Shopee Pay/Ovo/Gopay/Dana.
    "jago" dan "dana" polos hanya valid di akhir teks DAN menempel pada pesan
    bernominal atau berdiri sendiri (mencegah false positive seperti "dia jago
    masak", "makasih jago", atau "dana darurat").
    """
    low = (text or "").lower().strip()
    if not low:
        return None

    for canonical, keywords in SUMBER_KEYWORDS:
        for kw in keywords:
            if kw in ("jago", "dana"):
                if not re.search(rf"\b{re.escape(kw)}\b\s*[.!?]*\s*$", low):
                    continue
                if re.search(r"\d", low) or len(low.split()) == 1:
                    return canonical
                continue
            elif " " in kw:
                if re.search(rf"\b{re.escape(kw)}\b", low):
                    return canonical
            elif re.search(rf"\b{re.escape(kw)}\b", low):
                return canonical
    return None


def strip_sumber_words(text: str) -> str:
    """Buang kata penanda sumber dana dari deskripsi pengeluaran."""
    if not text:
        return ""
    result = text
    phrases = [
        "uang fisik", "bank jago", "bank bni", "shopee pay", "shopeepay", "go pay",
    ]
    for phrase in phrases:
        result = re.sub(rf"\b{re.escape(phrase)}\b", " ", result, flags=re.IGNORECASE)
    for token in ("cash", "tunai", "gopay", "bni", "ovo"):
        result = re.sub(rf"\b{token}\b", " ", result, flags=re.IGNORECASE)
    result = re.sub(r"(?:\s+|^)(jago|dana)\s*[.!?]*$", " ", result, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", result).strip()


def detect_expense_entry(text: str) -> Optional[dict]:
    """
    Deteksi pengeluaran dari chat bebas.

    Returns dict {"items": [{nominal, deskripsi, kategori, sumber_dana,
    confidence}], "confidence": float, "sumber": "chat"} atau None bila bukan
    pengeluaran. "sumber" = asal entri (chat), "sumber_dana" = dompet/rekening.

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

        sumber_dana = parse_sumber(part)
        deskripsi = _clean_description(part, match)
        if deskripsi and sumber_dana:
            cleaned = strip_sumber_words(deskripsi)
            if cleaned:
                deskripsi = cleaned
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
            "sumber_dana": sumber_dana,
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

def _keyword_hit(low: str, kw: str) -> bool:
    """Cocokkan keyword dengan batas kata + sufiks ringan Indonesia (-an/-nya)."""
    return re.search(rf"\b{re.escape(kw)}(?:an|nya|ku|mu)?\b", low) is not None


def _category_scores(text: str) -> dict[str, int]:
    """Hitung jumlah keyword yang cocok per kategori dari sebuah teks."""
    scores: dict[str, int] = {}
    low = (text or "").lower()
    if not low:
        return scores
    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = sum(1 for kw in keywords if _keyword_hit(low, kw))
        if hits:
            scores[category] = hits
    return scores


def categorize_local(deskripsi: str) -> Optional[str]:
    """Kategori via map keyword lokal. None bila tidak ada yang cocok."""
    if not deskripsi:
        return None
    scores = _category_scores(deskripsi)
    if not scores:
        return None
    priority = {cat: idx for idx, cat in enumerate(CATEGORIES)}
    return max(scores.items(), key=lambda kv: (kv[1], -priority.get(kv[0], 99)))[0]


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


RECEIPT_DEFAULT_CATEGORY = "Belanja"


def categorize_receipt(toko: str, items: list[dict]) -> Optional[str]:
    """
    Kategori struk berbasis item (bobot 2) + nama toko (bobot 1).
    None bila tidak ada keyword lokal yang cocok.
    """
    scores: dict[str, int] = {}
    for item in items or []:
        for cat, sc in _category_scores(str(item.get("nama") or "")).items():
            scores[cat] = scores.get(cat, 0) + sc * 2
    for cat, sc in _category_scores(toko or "").items():
        scores[cat] = scores.get(cat, 0) + sc
    if not scores:
        return None
    priority = {cat: idx for idx, cat in enumerate(CATEGORIES)}
    return max(scores.items(), key=lambda kv: (kv[1], -priority.get(kv[0], 99)))[0]


async def categorize_receipt_async(toko: str, items: list[dict]) -> str:
    """
    Kategori struk: keyword item/toko dulu, fallback AI, default 'Belanja'
    (struk belanja tidak pernah masuk 'Lain-lain').
    """
    local = categorize_receipt(toko, items)
    if local:
        return local
    combined = " ".join(
        filter(None, [toko or ""] + [str(i.get("nama") or "") for i in (items or [])])
    ).strip()[:300]
    if combined:
        try:
            cat = await categorize_expense(combined)
            if cat and cat != "Lain-lain":
                return cat
        except Exception as e:
            logger.warning("Kategori struk AI gagal: %s", str(e))
    return RECEIPT_DEFAULT_CATEGORY


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


_MONTH_NAME_MAP = {
    "januari": "January", "februari": "February", "maret": "March",
    "april": "April", "mei": "May", "juni": "June", "juli": "July",
    "agustus": "August", "september": "September", "oktober": "October",
    "november": "November", "desember": "December",
    "jan": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr", "mei": "May",
    "jun": "Jun", "jul": "Jul", "agu": "Aug", "ags": "Aug", "okt": "Oct",
    "nov": "Nov", "des": "Dec",
}


def _normalize_receipt_date(raw: Any) -> str:
    """Terima berbagai format tanggal struk, kembalikan YYYY-MM-DD (fallback hari ini)."""
    if not raw:
        return today_iso()
    text = str(raw).strip()
    text = text.replace(".", "-") if re.fullmatch(r"\d{1,2}[.-]\d{1,2}[.-]\d{4}", text) else text
    words = text.split()
    for idx, word in enumerate(words):
        clean = re.sub(r"[^\w]", "", word).lower()
        if clean in _MONTH_NAME_MAP:
            words[idx] = _MONTH_NAME_MAP[clean]
    text = " ".join(words)

    for fmt in (
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
        "%d %B %Y", "%d %b %Y", "%d %B %y", "%d %b %y",
    ):
        try:
            return datetime.strptime(text[:20].strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return match.group(0)
    return today_iso()


def _normalize_receipt_time(raw: Any) -> str:
    """Terima 'HH:MM' / 'HH.MM' dari struk; kosong bila tidak valid."""
    match = re.search(r"(\d{1,2})[:.](\d{2})", str(raw or ""))
    if not match:
        return ""
    jam, menit = int(match.group(1)), int(match.group(2))
    if 0 <= jam <= 23 and 0 <= menit <= 59:
        return f"{jam:02d}:{menit:02d}"
    return ""


def _amount_from_any(value: Any) -> Optional[int]:
    """Konversi nilai JSON (angka/teks) menjadi nominal int Rupiah."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            result = int(round(float(value)))
            return result if result > 0 else None
        except (TypeError, ValueError):
            return None
    return parse_amount(str(value))


def _sanitize_store(raw: Any) -> str:
    """Nama toko valid: bukan angka, mengandung minimal 2 huruf."""
    toko = re.sub(r"\s+", " ", str(raw or "")).strip()[:80]
    if not toko or re.fullmatch(r"[\d.,\s:/\\-]+", toko):
        return ""
    if len(re.sub(r"[^A-Za-z]", "", toko)) < 2:
        return ""
    return toko


def _normalize_receipt_items(raw_items: Any) -> list[dict]:
    """Normalisasi daftar item vision (dict/string) menjadi list dict terstruktur."""
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if isinstance(raw_items, str):
        raw_items = [line.strip() for line in re.split(r"[\n;]", raw_items) if line.strip()]
    if not isinstance(raw_items, list):
        return []

    items: list[dict] = []
    for raw in raw_items[:30]:
        if isinstance(raw, dict):
            nama = str(
                raw.get("nama") or raw.get("name") or raw.get("item") or ""
            ).strip()
            qty_raw = raw.get("qty") or raw.get("jumlah") or raw.get("quantity") or 1
            try:
                qty = float(str(qty_raw).replace(",", "."))
            except (TypeError, ValueError):
                qty = 1.0
            if qty <= 0:
                qty = 1.0
            harga = _amount_from_any(
                raw.get("harga") if raw.get("harga") is not None else raw.get("price")
            )
            if harga is None:
                harga = _amount_from_any(raw.get("harga_satuan"))
            subtotal = _amount_from_any(
                raw.get("subtotal") if raw.get("subtotal") is not None else raw.get("total")
            )
            if subtotal is None and harga is not None:
                subtotal = int(round(harga * qty))
            if harga is None and subtotal is not None:
                harga = int(round(subtotal / qty)) if qty else subtotal
            if not nama and not subtotal:
                continue
            items.append({
                "nama": nama[:60] or "Item",
                "qty": qty,
                "harga": harga,
                "subtotal": subtotal,
            })
        else:
            text = str(raw).strip()
            if not text:
                continue
            price_match = re.search(r"([\d][\d.,]*)\s*$", text)
            subtotal = parse_amount(price_match.group(1)) if price_match else None
            nama = text[:price_match.start()] if price_match else text
            nama = re.sub(r"[\s\-–—x*:]+$", "", nama).strip()
            qty_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:x|×)", text, re.IGNORECASE)
            try:
                qty = float(qty_match.group(1).replace(",", ".")) if qty_match else 1.0
            except (TypeError, ValueError):
                qty = 1.0
            items.append({
                "nama": (nama or text)[:60],
                "qty": qty if qty > 0 else 1.0,
                "harga": None,
                "subtotal": subtotal,
            })
    return items


def format_qty(qty: Any) -> str:
    """1.0 -> '1', 1.5 -> '1.5'."""
    try:
        value = float(qty)
    except (TypeError, ValueError):
        return "1"
    return str(int(value)) if value == int(value) else str(round(value, 2))


def format_receipt_items_chat(items: list[dict], limit: int = 3) -> list[str]:
    """Baris item untuk bubble Telegram (maks `limit`, sisanya diringkas)."""
    lines = []
    for item in (items or [])[:limit]:
        harga = item.get("subtotal") or item.get("harga") or 0
        lines.append(
            f"• {item.get('nama') or 'Item'} ({format_qty(item.get('qty'))}x) — "
            f"{format_rupiah(harga)}"
        )
    if len(items or []) > limit:
        lines.append(f"• … +{len(items) - limit} item lain")
    return lines


def format_receipt_catatan(
    items: list[dict],
    diskon: Optional[int] = None,
    total_item: Optional[int] = None,
    metode_bayar: str = "",
    max_chars: int = 1800,
) -> str:
    """Susun catatan terstruktur untuk properti 'Catatan' Notion (maks 1800 char)."""
    if not items and not diskon and not total_item and not metode_bayar:
        return ""
    lines = ["Items:"]
    shown = 0
    for item in items or []:
        harga = item.get("subtotal") or item.get("harga") or 0
        line = (
            f"- {item.get('nama') or 'Item'} ({format_qty(item.get('qty'))}x) — "
            f"{format_rupiah(harga)}"
        )
        if sum(len(existing) + 1 for existing in lines) + len(line) > max_chars - 100:
            lines.append(f"- … {len(items) - shown} item lainnya")
            break
        lines.append(line)
        shown += 1
    if diskon:
        lines.append(f"Diskon: -{format_rupiah(diskon)}")
    if total_item:
        lines.append(f"Total Item: {format_rupiah(total_item)}")
    if metode_bayar:
        lines.append(f"Pembayaran: {metode_bayar}")
    return "\n".join(lines)[:max_chars]


def _regex_total(raw: str) -> Optional[int]:
    """Fallback regex: prioritas 'total belanja' lalu 'grand total', hindari 'total item'."""
    text = (raw or "").lower()
    patterns = (
        r"total\s*belanja[^\d]{0,20}([\d.,]+)",
        r"grand\s*total[^\d]{0,20}([\d.,]+)",
        r"total\s*(?:bayar|pembayaran|akhir)[^\d]{0,20}([\d.,]+)",
        r"jumlah\s*(?:bayar|belanja|total)[^\d]{0,20}([\d.,]+)",
        r"total(?!\s*(?:item|qty|harga))[^\d]{0,20}([\d.,]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            nominal = parse_amount(match.group(1))
            if nominal:
                return nominal
    match = re.search(r"rp\.?\s*([\d.,]+)", text)
    if match:
        return parse_amount(match.group(1))
    return None


def parse_receipt_result(raw: str) -> Optional[dict]:
    """
    Parse output Moondream menjadi draft pengeluaran terstruktur.
    Nominal utama = total belanja (setelah diskon), fallback total item.
    Returns dict {nominal, deskripsi, toko, tanggal, jam, catatan,
    detail_items, diskon, subtotal, confidence, peringatan, sumber} atau None.
    """
    data = _extract_json_object(raw)
    peringatan: list[str] = []
    toko = ""
    tanggal_raw = ""
    waktu = ""
    metode_bayar = ""
    items: list[dict] = []
    diskon: Optional[int] = None
    total_item: Optional[int] = None
    total_belanja: Optional[int] = None

    if data:
        toko = _sanitize_store(data.get("toko") or data.get("store") or data.get("merchant"))
        tanggal_raw = data.get("tanggal") or data.get("date") or ""
        waktu = _normalize_receipt_time(data.get("jam") or data.get("waktu") or data.get("time"))
        items = _normalize_receipt_items(data.get("items") or data.get("item") or [])
        for key in ("diskon", "discount", "potongan", "potongan_harga", "disc"):
            diskon = _amount_from_any(data.get(key))
            if diskon is not None:
                diskon = abs(diskon)
                break
        for key in ("total_item", "total_sebelum_diskon", "subtotal", "sub_total"):
            total_item = _amount_from_any(data.get(key))
            if total_item is not None:
                break
        for key in (
            "total_belanja", "total_bayar", "total_pembayaran", "grand_total",
            "grandtotal", "jumlah_bayar", "total_akhir", "total", "nominal",
        ):
            total_belanja = _amount_from_any(data.get(key))
            if total_belanja is not None:
                break
        metode_bayar = str(
            data.get("metode_bayar") or data.get("metode_pembayaran") or data.get("payment") or ""
        ).strip()[:40]

    items_sum = sum(int(i.get("subtotal") or 0) for i in items)
    if total_item is None and items_sum > 0:
        total_item = items_sum
    if total_belanja is None:
        if total_item is not None:
            total_belanja = max(total_item - (diskon or 0), 0) or total_item
        elif items_sum > 0:
            total_belanja = max(items_sum - (diskon or 0), 0) or items_sum
    if not total_belanja:
        total_belanja = _regex_total(raw)
    if not total_belanja:
        return None

    if total_item and diskon and abs((total_item - diskon) - total_belanja) > 100:
        peringatan.append("Total belanja tidak sama dengan total item - diskon, mohon dicek")
    if not items:
        peringatan.append("Rincian item tidak terbaca")
    if diskon and not total_item and items_sum == 0:
        peringatan.append("Diskon terbaca tanpa rincian item")

    tanggal = _normalize_receipt_date(tanggal_raw)
    try:
        if datetime.strptime(tanggal, "%Y-%m-%d").date() > wib_now().date():
            peringatan.append("Tanggal struk di masa depan, memakai tanggal hari ini")
            tanggal = today_iso()
    except (ValueError, TypeError):
        tanggal = today_iso()

    if not toko:
        match = re.search(
            r"(?:toko|store|toserba|minimarket|market)[:\s]+([^\n,]{2,50})",
            raw or "", re.IGNORECASE,
        )
        if match:
            toko = _sanitize_store(match.group(1))

    catatan = format_receipt_catatan(items, diskon, total_item, metode_bayar)
    deskripsi = toko or (items[0]["nama"] if items else "Belanja struk")

    if data and items:
        confidence = 0.95
    elif data:
        confidence = 0.8
    else:
        confidence = 0.65

    return {
        "nominal": total_belanja,
        "deskripsi": deskripsi[:80],
        "toko": toko,
        "tanggal": tanggal,
        "jam": waktu,
        "catatan": catatan,
        "detail_items": items,
        "diskon": diskon,
        "subtotal": total_item,
        "metode_bayar": metode_bayar,
        "confidence": confidence,
        "peringatan": peringatan,
        "sumber": "struk",
    }


MAX_RECEIPT_NOMINAL = 1_000_000_000


def validate_receipt_draft(item: dict) -> list[str]:
    """
    Validasi wajib sebelum simpan struk. Mengembalikan daftar peringatan
    (bukan blocker; user tetap bisa Simpan/Edit lewat konfirmasi).
    """
    warnings = list(item.get("peringatan") or [])
    try:
        nominal = int(item.get("nominal") or 0)
    except (TypeError, ValueError):
        nominal = 0
    if nominal <= 0:
        warnings.append("Nominal tidak terbaca / nol")
    elif nominal > MAX_RECEIPT_NOMINAL:
        warnings.append("Nominal tidak wajar (lebih dari Rp1 miliar)")
    tanggal = (item.get("tanggal") or today_iso())[:10]
    try:
        if datetime.strptime(tanggal, "%Y-%m-%d").date() > wib_now().date():
            warnings.append("Tanggal di masa depan")
    except (ValueError, TypeError):
        warnings.append("Tanggal tidak valid")
    if not item.get("detail_items") and not any(
        "item" in warning.lower() for warning in warnings
    ):
        warnings.append("Item tidak terdeteksi")
    return warnings


async def compute_total_for_date(chat_id: int, date_iso: Optional[str] = None) -> int:
    """
    Total pengeluaran akurat untuk satu tanggal.
    Sumber kebenaran = query Notion ulang; fallback KV bila Notion kosong/gagal.
    """
    date_str = (date_iso or today_iso())[:10]
    total = 0
    try:
        from src.notion import query_expenses

        entries = await query_expenses(start_date=date_str, end_date=date_str)
        total = sum(int(e.get("nominal") or 0) for e in entries)
    except Exception as e:
        logger.warning("Gagal query Notion untuk total %s: %s", date_str, str(e))
        total = 0

    if total > 0:
        try:
            from src.kv import set_cache

            await set_cache(
                f"{TOTAL_PREFIX}:{chat_id}:{date_str}", str(total), ttl_seconds=TOTAL_TTL
            )
        except Exception:
            pass
        return total

    return await get_daily_total(chat_id, date_str)


def total_label_for_date(date_iso: Optional[str]) -> str:
    """Label total: 'Total hari ini' atau 'Total 04 Okt 2026'."""
    date_str = (date_iso or today_iso())[:10]
    if date_str == today_iso():
        return "Total hari ini"
    return f"Total {format_short_date(date_str)}"


# --- Pengaturan user (KV) ---

async def get_expense_settings(chat_id: int) -> dict:
    """Pengaturan pengeluaran user (default: autosave ON, konfirmasi OFF, sumber tanya)."""
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
                if data.get("default_source") in SUMBER_OPTIONS:
                    settings["default_source"] = data["default_source"]
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Error parsing expense settings: %s", str(e))
    return settings


async def set_expense_settings(chat_id: int, **updates) -> bool:
    """Simpan pengaturan pengeluaran user (autosave/confirm/default_source)."""
    from src.kv import set_cache

    settings = await get_expense_settings(chat_id)
    if isinstance(updates.get("autosave"), bool):
        settings["autosave"] = updates["autosave"]
    if isinstance(updates.get("confirm"), bool):
        settings["confirm"] = updates["confirm"]
    if "default_source" in updates:
        value = updates.get("default_source")
        if value in (None, "", "tanya", "ask"):
            settings["default_source"] = None
        elif str(value) in SUMBER_OPTIONS:
            settings["default_source"] = str(value)
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


async def set_source_pending(chat_id: int, active: bool = True) -> bool:
    """State: draft pengeluaran sedang menunggu jawaban sumber dana."""
    from src.kv import del_cache, set_cache

    if not active:
        return await del_cache(f"{SRC_PREFIX}:{chat_id}")
    return await set_cache(f"{SRC_PREFIX}:{chat_id}", "1", ttl_seconds=SRC_TTL)


async def is_source_pending(chat_id: int) -> bool:
    from src.kv import get_cache

    return bool(await get_cache(f"{SRC_PREFIX}:{chat_id}"))


def apply_source_to_draft(draft: dict, source: str, force: bool = False) -> int:
    """
    Isi sumber dana ke item draft. force=True menimpa semua item;
    force=False hanya item yang belum punya sumber. Returns jumlah item diisi.
    """
    filled = 0
    for item in draft.get("items") or []:
        if force or not item.get("sumber_dana"):
            item["sumber_dana"] = source
            filled += 1
    return filled


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
        entry = {
            "waktu": wib_now().strftime("%Y-%m-%d %H:%M:%S"),
            "aksi": action,
            "nominal": data.get("nominal"),
            "kategori": data.get("kategori"),
            "sumber": data.get("sumber"),
            "sumber_dana": data.get("sumber_dana"),
            "deskripsi": (data.get("deskripsi") or "")[:80],
        }
        # Audit khusus scan struk (ID foto saja, bukan gambarnya).
        if data.get("file_id"):
            entry["file_id"] = str(data["file_id"])[:64]
        if data.get("vision_raw") is not None:
            entry["vision_raw"] = str(data["vision_raw"])[:800]
        if data.get("items_count") is not None:
            entry["items_count"] = int(data["items_count"])
        if data.get("peringatan"):
            entry["peringatan"] = [str(w)[:120] for w in data["peringatan"]][:10]
        logs.append(entry)
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
                    "sumber_dana": item.get("sumber_dana", ""),
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
    """Satu baris ringkas entri: 'Rp25.000 — kopi (Makan & Minum) | 💵 Cash'."""
    kategori = item.get("kategori") or "Lain-lain"
    emoji = CATEGORY_EMOJI.get(kategori, "📦")
    line = f"{format_rupiah(item.get('nominal'))} — {item.get('deskripsi') or '-'} ({emoji} {kategori})"
    if item.get("toko"):
        line += f" | {item['toko']}"
    sumber = item.get("sumber_dana")
    if sumber:
        line += f" | {SUMBER_EMOJI.get(sumber, '📱')} {sumber}"
    return line


def sumber_line(item: dict) -> str:
    """Baris sumber dana untuk bubble konfirmasi."""
    sumber = item.get("sumber_dana")
    if sumber:
        return f"{SUMBER_EMOJI.get(sumber, '📱')} Sumber: {sumber}"
    return "❓ Sumber: belum dipilih"


def build_source_prompt_text(draft: dict) -> str:
    """Bubble pertanyaan sumber dana (chat bebas & struk)."""
    items = draft.get("items") or []
    lines: list[str] = []
    if draft.get("sumber") == "struk" and len(items) == 1:
        item = items[0]
        lines.append("📸 Struk terbaca:")
        if item.get("toko"):
            lines.append(f"🏪 {item['toko']}")
        lines.append(f"💰 {format_rupiah(item.get('nominal'))}")
        lines.append("")
        lines.append("Sumber pengeluaran?")
    elif len(items) == 1:
        item = items[0]
        lines.append("🏦 Sumber pengeluaran?")
        lines.append(f"💰 {format_rupiah(item.get('nominal'))} — {item.get('deskripsi') or '-'}")
    else:
        lines.append("🏦 Sumber pengeluaran?")
        for item in items[:5]:
            lines.append(f"• {format_rupiah(item.get('nominal'))} — {item.get('deskripsi') or '-'}")
    lines.append("")
    lines.append(
        "Pilih tombol di bawah, atau balas: cash / bni / bank jago / shopee pay / ovo / gopay / dana"
    )
    return "\n".join(lines)


def build_saved_text(
    saved: list[dict],
    total_today: int,
    skipped: int = 0,
    failed: int = 0,
    total_label: str = "Total hari ini",
) -> str:
    lines: list[str] = []
    if len(saved) == 1 and (saved[0].get("sumber") == "struk" or saved[0].get("detail_items")):
        item = saved[0]
        lines.append("✅ Tercatat!")
        if item.get("toko"):
            lines.append(f"\n🏪 {item['toko']}")
        lines.append(f"💰 {format_rupiah(item.get('nominal'))}")
        if item.get("kategori"):
            emoji = CATEGORY_EMOJI.get(item["kategori"], "📦")
            lines.append(f"{emoji} {item['kategori']}")
        if item.get("sumber_dana"):
            lines.append(f"{SUMBER_EMOJI.get(item['sumber_dana'], '📱')} {item['sumber_dana']}")
        tanggal = format_short_date(item.get("tanggal") or today_iso())
        waktu = item.get("waktu") or ""
        lines.append(f"📅 {tanggal}" + (f", {waktu}" if waktu else ""))
    elif len(saved) == 1:
        lines.append(f"✅ Tercatat: {item_line(saved[0])}")
    else:
        lines.append("✅ Tercatat:\n" + "\n".join(f"• {item_line(i)}" for i in saved))

    if skipped:
        lines.append(f"⚠️ {skipped} entri duplikat dilewati.")
    if failed:
        lines.append(f"⚠️ {failed} entri gagal disimpan (dicadangkan lokal, akan dicoba lagi).")
    lines.append("")
    lines.append(f"{total_label}: {format_rupiah(total_today)}")
    return "\n".join(lines)


def build_confirm_text(draft: dict) -> str:
    items = draft.get("items", [])
    sumber = draft.get("sumber", "chat")

    if sumber == "struk" and len(items) == 1:
        item = items[0]
        lines = ["📸 Struk terbaca:"]
        if item.get("toko"):
            lines.append(f"\n🏪 Toko: {item['toko']}")
        tanggal = format_short_date(item.get("tanggal") or today_iso())
        waktu = item.get("waktu") or ""
        lines.append(f"📅 Tanggal: {tanggal}" + (f", {waktu}" if waktu else ""))
        detail = item.get("detail_items") or []
        if detail:
            lines.append("\n🛒 Item:")
            lines.extend(format_receipt_items_chat(detail))
        elif item.get("catatan"):
            lines.append("\n🛒 Item:")
            lines.extend(f"• {line}" for line in item["catatan"].splitlines()[:4])
        if item.get("diskon"):
            lines.append(f"\n💸 Diskon: -{format_rupiah(item['diskon'])}")
        lines.append(f"💰 Total: {format_rupiah(item.get('nominal'))}")
        if item.get("kategori"):
            emoji = CATEGORY_EMOJI.get(item["kategori"], "📦")
            lines.append(f"\n{emoji} Kategori: {item['kategori']}")
        lines.append(f"\n{sumber_line(item)}")
        for warning in item.get("peringatan") or []:
            lines.append(f"⚠️ {warning}")
        return "\n".join(lines)

    base = "🧾 Konfirmasi pengeluaran:\n" + "\n".join(
        f"• {item_line(i)}" for i in items
    )
    if any(not i.get("sumber_dana") for i in items):
        base += "\n\n❓ Sumber dana belum dipilih — tekan tombol sumber di bawah."
    return base


def _entry_sumber(entry: dict) -> str:
    """Sumber dana entri; nilai asing/kosong -> 'Tidak diketahui'."""
    sumber = str(entry.get("sumber_dana") or "").strip()
    return sumber if sumber in SUMBER_OPTIONS else SUMBER_UNKNOWN


def build_recap_text(
    entries: list[dict],
    label: str,
    sumber_dana: Optional[str] = None,
    breakdown_only: bool = False,
) -> str:
    """
    Bangun teks rekap: total + breakdown sumber dana + kategori terbesar.
    sumber_dana=... memfilter entri; breakdown_only=True hanya menampilkan
    breakdown per sumber.
    """
    if sumber_dana:
        entries = [e for e in entries if _entry_sumber(e) == sumber_dana]
        label = f"{sumber_dana} {label}"
    if not entries:
        return f"📊 Belum ada pengeluaran {label.lower()}."

    total = sum(int(e.get("nominal") or 0) for e in entries)
    src_agg: dict[str, int] = {}
    cat_agg: dict[str, int] = {}
    for entry in entries:
        value = int(entry.get("nominal") or 0)
        src = _entry_sumber(entry)
        src_agg[src] = src_agg.get(src, 0) + value
        cat = entry.get("kategori") or "Lain-lain"
        cat_agg[cat] = cat_agg.get(cat, 0) + value

    def _pct(value: int) -> int:
        return round(value / total * 100) if total else 0

    lines = [f"📊 Pengeluaran {label}: {format_rupiah(total)}"]

    known_sources = [s for s in src_agg if s != SUMBER_UNKNOWN]
    if known_sources or breakdown_only:
        lines.append("")
        lines.append("Per sumber:")
        for src, value in sorted(src_agg.items(), key=lambda kv: kv[1], reverse=True):
            emoji = SUMBER_EMOJI.get(src, "❓")
            lines.append(f"• {emoji} {src}: {format_rupiah(value)} ({_pct(value)}%)")

    if breakdown_only:
        lines.append("")
        lines.append(f"Total {len(entries)} transaksi.")
        return "\n".join(lines)

    lines.append("")
    lines.append("Kategori terbesar:")
    for cat, value in sorted(cat_agg.items(), key=lambda kv: kv[1], reverse=True):
        emoji = CATEGORY_EMOJI.get(cat, "📦")
        lines.append(f"• {emoji} {cat}: {format_rupiah(value)} ({_pct(value)}%)")
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
