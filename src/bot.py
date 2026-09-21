"""
Telegram Bot setup dan handler untuk Oline.
Mengelola penerimaan pesan dan routing ke Gemini pipeline.
"""

import asyncio
import logging
import os
import re
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.gemini import chat_with_oline, retry_pending_task

from src.kv import check_rate_limit, get_history, save_journal

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")



def create_application() -> Application:
    """
    Membuat dan mengkonfigurasi Application python-telegram-bot.
    Untuk mode webhook (stateless per-request di Vercel).
    """
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN environment variable is not set. "
            "Cannot initialize Telegram bot."
        )

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .updater(None)  # Tidak pakai polling, hanya webhook
        .build()
    )

    # Register handlers
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("help", handle_help))
    application.add_handler(CommandHandler("jurnal", handle_jurnal_command))
    application.add_handler(CommandHandler("set_token", handle_set_token))
    application.add_handler(
        MessageHandler(filters.LOCATION, handle_location_message)
    )
    application.add_handler(
        MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file_message)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    return application



async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler untuk command /start."""
    if not update.effective_chat:
        return

    welcome_text = (
        "Halo! Saya Oline, asisten AI pribadi Anda. 👋\n\n"
        "Saya dapat membantu Anda menyelesaikan berbagai tugas:\n"
        "💬 Pertanyaan & diskusi informasi\n"
        "🎬 Rekomendasi film & musik\n"
        "🌤️ Informasi cuaca terkini\n"
        "📔 Catatan jurnal & memori\n"
        "🌐 Preview & deploy landing page\n\n"
        "Silakan langsung sampaikan permintaan Anda secara jelas!"
    )
    await update.effective_chat.send_message(welcome_text)


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler untuk command /help."""
    if not update.effective_chat:
        return

    help_text = (
        "Berikut adalah beberapa layanan yang dapat saya bantu:\n\n"
        "💬 Chat & Informasi — Pertanyaan umum atau analisis data\n"
        "🎬 Rekomendasi Film — \"rekomendasi film horor\"\n"
        "🎵 Rekomendasi Lagu — \"cari lagu klasik\"\n"
        "🌤️ Informasi Cuaca — \"cuaca besok di Bandung\"\n"
        "📈 Saham & IHSG — \"cek saham BBCA\" / \"IHSG hari ini\"\n"
        "📔 Jurnal — /jurnal [catatan] atau \"catat jurnal hari ini: ...\"\n"
        "📋 Rekap Jurnal — \"rekap jurnal minggu ini\"\n\n"
        "Anda dapat menyampaikan permintaan secara langsung."
    )
    await update.effective_chat.send_message(help_text, parse_mode="Markdown")


async def handle_set_token(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handler untuk command /set_token <layanan> <token>.
    Menyimpan token baru ke Vercel KV (Kelola Token via KV), bukan ke env.
    Token langsung aktif (dibaca drive.py dari KV) tanpa redeploy.
    Format: /set_token drive <refresh_token>
    """
    if not update.effective_chat or not update.message:
        return

    text = (update.message.text or "").strip()
    parts = text.split(None, 2)
    if len(parts) < 3:
        await update.effective_chat.send_message(
            "Format: /set_token <layanan> <token>\nContoh: /set_token drive 1//abc123..."
        )
        return

    service = parts[1]
    new_token = parts[2].strip()
    if not new_token:
        await update.effective_chat.send_message("Token tidak boleh kosong.")
        return

    from src.config import token_key_for_env
    from src.kv import reset_failure_count, save_token, set_user_feature
    from src.tools import resolve_token_service

    env_key, service_label = resolve_token_service(service)
    if not env_key:
        await update.effective_chat.send_message(
            f"Layanan '{service}' tidak dikenal. Contoh: /set_token drive <token>"
        )
        return

    token_key = token_key_for_env(env_key)
    ok = await save_token(token_key, new_token)
    if not ok:
        logger.error("set_token gagal disimpan ke KV untuk service '%s' (key %s)", service, token_key)
        await update.effective_chat.send_message(
            "❌ Gagal menyimpan token ke Vercel KV. Pastikan KV_REST_API_URL / KV_REST_API_TOKEN sudah dikonfigurasi."
        )
        return

    # Bersihkan catatan kegagalan & pastikan fitur aktif (user sedang memperbaiki token)
    await reset_failure_count(token_key)
    await set_user_feature(token_key, True)

    masked = (new_token[:4] + "…") if len(new_token) > 4 else "***"
    logger.info("set_token berhasil: service='%s' key='%s' token=%s", service, token_key, masked)
    await update.effective_chat.send_message(
        f"✅ Token {service_label} berhasil disimpan di Vercel KV dan langsung aktif."
    )


async def handle_jurnal_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handler untuk command /jurnal <teks>.
    Shortcut langsung simpan jurnal tanpa lewat Gemini.
    """
    if not update.effective_chat or not update.message:
        return

    chat_id = update.effective_chat.id

    # Rate limiting
    if not await check_rate_limit(chat_id):
        await update.effective_chat.send_message(
            "Mohon tunggu sebentar sebelum mengirim pesan kembali."
        )
        return
    # Ambil teks setelah /jurnal
    text = ""
    if update.message.text:
        text = update.message.text.replace("/jurnal", "", 1).strip()

    if not text:
        await update.effective_chat.send_message(
            "Silakan sertakan catatan jurnal setelah perintah /jurnal.\n"
            "Contoh: `/jurnal Menghadiri rapat proyek dan menyelesaikan laporan.`"
        )
        return

    # Simpan langsung ke KV
    success = await save_journal(chat_id, text)
    if success:
        await update.effective_chat.send_message(
            "Catatan jurnal Anda telah berhasil disimpan. 📖✨\n"
            "Untuk melihat rekap, Anda dapat meminta 'rekap jurnal minggu ini'."
        )
    else:
        await update.effective_chat.send_message(
            "Gagal menyimpan catatan jurnal. Silakan coba kembali beberapa saat lagi."
        )


POPULAR_STOCK_TICKERS = [
    "bbca", "bbri", "bmri", "bbni", "tlkm", "asii", "unvr", "adro", "antm", "icbp",
    "indf", "bumi", "pgas", "wskt", "sido", "myrx", "goto", "buka", "ptba", "medc",
    "emtk", "brpt", "tpia", "inkp", "tkim", "doid", "mbma", "mka", "hrum", "essa",
    "aces", "bsde", "ctra", "smra", "pwon", "eraa", "cpin", "jpfa", "smgr", "intp",
    "bren", "ammn", "cuan", "dewa", "film", "klbf", "mcap"
]

HEAVY_KEYWORDS = {
    "github": [
        "github", "baca file github", "baca file repo", "edit dirimu", "tambahkan fitur",
        "perbaiki bug", "self update", "update dirimu", "push ke github", "pull request",
        "buat pr", "create pr", "baca github",
    ],
    "vercel_logs": [
        "kenapa error", "kenapa gagal", "ada masalah apa", "ada masalah",
        "error apa", "cek log", "bacakan log", "log vercel", "log terakhir",
        "baca log", "lihat log", "log server",
    ],
    "notion": [
        "notion", "catat ke notion", "simpan ke notion", "notes notion", "catatan notion",
        "tambah kolom", "buat kolom", "edit kolom", "tambah properti", "buat properti",
        "kolom file", "kolom notion", "tambah atribut", "simpan aturan", "simpan preferensi",
        "simpan memori", "catat aturan", "catat preferensi",
    ],
    "cuaca": ["cuaca", "hujan", "panas", "suhu", "cerah"],
    "rekomendasi": ["rekomendasi", "film", "lagu", "seri", "anime"],
    "suara": ["suara", "nyanyi", "gombal", "puisi", "voice note", "vn"],
    "jurnal": ["catat jurnal", "rekap jurnal", "jurnal harian"],
    "kuota": ["kuota", "token", "quota", "cek kuota ai", "kuota ai", "pemakaian ai", "status ai", "cek ai quota", "sisa kuota", "sisa token"],
    "health": ["cek kesehatan", "cek fitur", "health check", "fitur rusak", "kesehatan fitur"],
    "kelola_fitur": [
        "aktifkan fitur", "nonaktifkan fitur", "matikan fitur", "hidupkan fitur",
        "disable fitur", "enable fitur", "fitur nonaktif", "fitur aktif",
        "toggle fitur", "kelola fitur", "fitur apa saja",
    ],
    "drive": [
        "drive", "database", "folder", "simpan file", "buat folder",
        "cari file", "tampilkan isi", "kirim file", "upload", "download", "file",
    ],
    "design_reference": [
        "referensi desain", "cari referensi website", "inspirasi desain",
        "contoh website", "cari desain website", "referensi landing page",
        "referensi dari website", "referensinya dari",
    ],
    "deploy": [
        "deploy sekarang", "deploy ke vercel", "deploy", "onlinekan", "publish",
        "live", "hosting ke vercel", "hosting", "list landing page", "daftar landing page", "hapus landing page",
        "hapus deployment", "list deployment", "daftar deployment", "delete deployment",
    ],
    "preview": [
        "buatkan website", "buatkan landing page", "buat web", "bikin website",
        "bikin landing page", "preview", "buat halaman", "desain web", "desain website",
        "buatkan web", "bikin web", "buat landing page",
        "landing page", "website untuk", "halaman untuk", "buat website",
        "lanjutkan pembuatan", "lanjutkan preview", "lanjutkan landing", "lanjutkan web",
        "aplikasi", "buat aplikasi", "bikin aplikasi", "buatkan aplikasi",
        "to do list", "todo list", "kalkulator", "aplikasi web", "buat app", "bikin app",
    ],
    "lokasi": [
        "terdekat", "dekat", "toko buku", "cafe", "kafe", "restoran", "restaurant",
        "mall", "tempat makan", "kedai", "coffee", "cari tempat", "cari cafe", "spbu",
        "pom bensin", "apotek", "rumah sakit", "bank", "atm", "lokasi terdekat", "lokasi saya",
    ],
    "search": ["search", "apa itu", "siapa", "kapan", "dimana", "berita", "definisi", "pengertian", "cari berita", "cari info"],
    "saham": [
        "saham", "ihsg", "indeks", "index", "market", "bursa", "gainer", "loser",
    ] + POPULAR_STOCK_TICKERS,
    "coding": [
        "jalankan", "eksekusi", "run code", "jalankan kode", "execute",
        "kode python", "kode javascript", "script", "debug", "coding",
        "contoh kode", "print", "buat fungsi", "buat script",
    ],
    "gambar": [
        "gambar", "foto", "image", "kirim gambar", "cari gambar", "cari foto",
        "tampilkan gambar", "kirimi gambar", "cariin gambar", "minta foto", "minta gambar",
    ],
    "neo4j": [
        "aktivitas", "simpan aktivitas", "catat aktivitas",
        "riwayat aktivitas", "tampilkan aktivitas", "log aktivitas",
        "forensik", "neo4j", "graph",
    ],
    "akademik": [
        "jurnal", "skripsi", "sitasi", "bibtex", "referensi",
        "rangkum docx", "tanya pdf", "paper", "penelitian", "literatur",
        "karya ilmiah", "arxiv", "scholar", "apa 7", "rangkum dokumen",
        "erine", "jurnal ilmiah",
    ],
    "cek_token": [
        "cek token", "status token", "token oline", "validasi token",
        "token valid", "cek kredensial", "cek api key", "cek semua token",
        "token kadaluwarsa", "token expired", "audit token",
    ],
    "renew_token": [
        "perbarui token", "update token", "renew token", "ganti token",
        "token baru", "refresh token", "rotasi token", "set token",
        "perbarui kredensial", "ganti api key",
    ],
}

# Intent berat (AI-driven/tool lambat) yang diproses di BACKGROUND (Acknowledge First,
# Process Later) agar webhook cepat balas 200 dan tidak timeout (504) di Vercel.
# Intent cepat (kuota, health, kelola_fitur, cek_token, renew_token, list/delete deploy,
# simpan catatan Notion imperatif) tetap diproses sinkron.
HEAVY_BACKGROUND_INTENTS = {
    "saham", "cuaca", "rekomendasi", "suara", "jurnal", "drive",
    "search", "gambar", "neo4j", "coding", "github", "vercel_logs", "lokasi",
}




def detect_intent(text: str) -> str | None:
    """
    Mendeteksi apakah pesan pengguna membutuhkan tools (heavy intent).
    Jika tidak ada kata kunci yang cocok, mengembalikan None (Fast Path).
    """
    text_lower = text.lower().strip()
    words = text_lower.split()

    # 1. Cek kata kunci persis/substring
    for intent, keywords in HEAVY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return intent

    # 2. Deteksi otomatis kode saham 4 huruf standalone (misal: "BUMI", "BBCA")
    if len(words) == 1 and len(words[0]) == 4 and words[0].isalpha():
        return "saham"

    return None


async def detect_intent_async(text: str, chat_id: int | None = None) -> str | None:
    """
    Mendeteksi intent dengan konteks percakapan sebelumnya.
    """
    intent = detect_intent(text)
    if intent:
        return intent

    # Jika pengguna mengirim pesan pendek (misal: 1-3 kata seperti "bumi", "bagaimana bumi"),
    # dan percakapan sebelumnya membahas saham, klasifikasikan sebagai intent 'saham'.
    if chat_id:
        try:
            words = text.strip().split()
            if len(words) <= 3:
                history = await get_history(chat_id)
                if history:
                    recent_texts = " ".join([m.get("text", "") for m in history[-3:]]).lower()
                    if any(kw in recent_texts for kw in HEAVY_KEYWORDS["saham"]):
                        return "saham"
        except Exception as e:
            logger.warning("Error checking history for intent context: %s", str(e))

    return None


RULE_KEYWORDS = [
    "jangan panggil", "mulai sekarang", "kedepannya", "ke depannya",
    "selalu", "ingat bahwa", "jangan lupa", "kalo aku minta", "setiap kali",
    "panggil aku", "panggil saya", "ingat ya", "simpan aturan",
    "simpan preferensi", "simpan memori", "preferensi saya", "aturan saya",
]

NOTE_REQUEST_KEYWORDS = [
    "catat ke notion", "simpan ke notion", "catat di notion", "simpan di notion",
    "simpan ide", "catatan rapat", "simpan catatan",
]


SKIP_KEYWORDS = [
    "skip", "gak usah", "gak usah deh", "nggak usah", "batal",
    "hentikan", "abaikan", "gausah", "tidak usah", "ndak usah",
    "cancle", "cancel", "nggak usah ya",
]

RETRY_KEYWORDS = [
    "apakah sudah", "udah belum", "udah bisa", "gimana tadi",
    "sudah bisa", "coba lagi", "retry", "yang tadi",
    "udah jadi", "gimana yang tadi", "masih error", "ulang",
    "jalankan", "coba", "pukul", "eksekusi",
]

STATUS_KEYWORDS = [
    "status task", "status tugas", "status pekerjaan", "status saya",
    "kabar task", "kabar tugas", "progresnya", "progressnya",
    "selesai belum", "jadi belum", "beres belum", "udah beres", "sudah beres",
    "masih diproses", "masih jalan", "masih diproses gak", "prosesnya gimana",
    "gimana status", "cek status", "cek task",
]

# --- Klarifikasi Ask-Before-Act (brief.md) ---
AMBIGUOUS_KEYWORDS = ["cek", "lihat", "status", "kondisi", "info"]

CLEAR_COMMANDS = [
    "cek koneksi", "cek kesehatan", "cek kuota ai", "cek kuota",
    "cek cuaca", "cek saham", "status task", "status tugas",
    "cek semua fitur", "health check", "cek fitur", "cek status",
    "kondisi fitur", "cek lokasi",
]

AMBIGUOUS_TARGETS = {
    "notion": "Maksud kamu cek status koneksi Notion, atau lihat isi database Notion?\n(1) Status koneksi\n(2) Isi database",
    "ai": "Mau cek status model AI, atau kuota pemakaian AI?\n(1) Status model\n(2) Kuota pemakaian",
    "drive": "Mau cek status Google Drive, atau isi folder Drive?\n(1) Status koneksi\n(2) Isi folder",
    "koneksi": "Mau cek status koneksi, atau ada hal lain yang perlu dicek?",
    "kuota": "Mau cek kuota AI, atau status model AI?",
}

CONFIRM_KEYWORDS = ["ya", "iya", "betul", "benar", "1", "2", "status", "koneksi", "kuota", "isi", "model", "folder", "oke", "ok"]

PERBAIKAN_KEYWORDS = ["perbaiki", "benerin", "fix", "solusi"]


def is_fix_request(text: str) -> bool:
    """Mendeteksi apakah pesan pengguna meminta perbaikan error / bug."""
    if not text:
        return False
    text_lower = text.lower().strip()
    return any(kw in text_lower for kw in PERBAIKAN_KEYWORDS)


def is_skip_request(text: str) -> bool:
    """Mendeteksi apakah pesan pengguna meminta mengabaikan/skip pending task."""
    if not text:
        return False
    text_lower = text.lower().strip()
    return any(kw in text_lower for kw in SKIP_KEYWORDS)


def is_retry_request(text: str) -> bool:
    """Mendeteksi apakah pesan pengguna menanyakan status pending task / minta retry."""
    if not text:
        return False
    text_lower = text.lower().strip()
    return any(kw in text_lower for kw in RETRY_KEYWORDS)


def is_status_request(text: str) -> bool:
    """Mendeteksi apakah pesan pengguna menanyakan status pending task yang sedang berjalan."""
    if not text:
        return False
    text_lower = text.lower().strip()
    return any(kw in text_lower for kw in STATUS_KEYWORDS)


def is_ambiguous(text: str) -> bool:
    """
    Mendeteksi apakah perintah pengguna ambigu (Ask-Before-Act).
    Mengembalikan False jika perintah sudah jelas (CLEAR_COMMANDS) atau tidak mengandung kata ambigu.
    """
    if not text:
        return False
    text_lower = text.lower().strip()
    if any(clear in text_lower for clear in CLEAR_COMMANDS):
        return False
    return any(kw in text_lower for kw in AMBIGUOUS_KEYWORDS)


def get_clarify_question(text: str) -> str:
    """Menyusun pertanyaan klarifikasi berdasarkan target fitur pada perintah ambigu."""
    text_lower = text.lower().strip()
    for target, question in AMBIGUOUS_TARGETS.items():
        if target in text_lower:
            return question
    return "Maksud kamu apa ya? Bisa lebih spesifik?"


def build_task_status_message(pending_task: dict) -> str:
    """
    Menyusun pesan status pending task dengan estimasi berdasarkan waktu berjalan.
    """
    import datetime as _dt

    perintah = pending_task.get("perintah") or pending_task.get("message") or "task"
    perintah_short = perintah if len(perintah) <= 60 else perintah[:60] + "..."

    elapsed_minutes = None
    raw_waktu = pending_task.get("waktu") or pending_task.get("timestamp")
    if raw_waktu:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                start = _dt.datetime.strptime(str(raw_waktu)[:19], fmt)
                elapsed_minutes = int((_dt.datetime.now() - start).total_seconds() // 60)
                break
            except ValueError:
                continue

    if elapsed_minutes is not None and elapsed_minutes > 5:
        estimasi = "masih diproses ya. Sudah sekitar beberapa menit, kalau lama banget bisa bilang 'coba lagi' ya."
    elif elapsed_minutes is not None and elapsed_minutes > 2:
        estimasi = "masih diproses. Estimasi selesai 1-2 menit lagi."
    else:
        estimasi = "masih diproses. Estimasi selesai sekitar 1 menit lagi."

    return (
        f"⏳ Task \"{perintah_short}\" {estimasi}\n"
        "Aku kabari begitu selesai ya!"
    )


def generate_memory_title(rule_text: str) -> str:
    """
    Menghasilkan judul singkat (maksimal 50-80 karakter) dari kalimat aturan/preferensi.
    Mencari keyword yang muncul paling awal dalam teks.
    """
    if not rule_text:
        return "Aturan Memori"

    rule_clean = rule_text.strip()
    rule_lower = rule_clean.lower()
    keywords = ["panggil", "jangan", "selalu", "setiap", "kalo", "kalau", "jika", "mulai sekarang", "kedepannya", "ke depannya", "deploy"]

    earliest_idx = -1
    for kw in keywords:
        idx = rule_lower.find(kw)
        if idx != -1:
            if earliest_idx == -1 or idx < earliest_idx:
                earliest_idx = idx

    if earliest_idx != -1:
        extracted = rule_clean[earliest_idx:].strip()
        if len(extracted) > 5:
            res_title = extracted[:60].strip()
            if len(extracted) > 60:
                res_title += "..."
            return res_title

    res_title = rule_clean[:60].strip()
    if len(rule_clean) > 60:
        res_title += "..."
    return res_title


def is_rule_message(text: str) -> bool:
    """
    Mendeteksi apakah pesan pengguna berisi instruksi aturan/preferensi baru.
    """
    if not text:
        return False
    text_lower = text.lower().strip()
    if any(kw in text_lower for kw in NOTE_REQUEST_KEYWORDS) and not any(rkw in text_lower for rkw in ["aturan", "preferensi", "ingat bahwa"]):
        return False
    return any(kw in text_lower for kw in RULE_KEYWORDS)


def is_landing_page_generation_request(text: str, intent: str | None) -> bool:
    """
    Mendeteksi apakah pesan pengguna merupakan permintaan generasi/revisi/deploy landing page.
    List & Delete deployment diselesaikan secara langsung (fast path).
    """
    if intent not in ("preview", "deploy", "design_reference"):
        return False

    text_lower = text.lower().strip()
    # Hanya blokir perintah LISTING/DELETE deployment (fast path), bukan kata 'list'
    # di nama aplikasi seperti "to do list". Gunakan frasa spesifik deployment.
    if any(kw in text_lower for kw in [
        "list landing", "list deployment", "list website", "daftar landing",
        "daftar deployment", "daftar website", "hapus landing", "hapus deployment",
        "hapus website", "delete landing", "delete deployment", "delete website",
        "list web", "daftar web", "hapus web",
    ]):
        return False

    return True


def _extract_notion_note(text: str) -> Optional[tuple[str, str]]:
    """
    Mengekstrak (title, content) untuk disimpan ke database CATATAN Notion.
    Mengembalikan None jika pesan bukan permintaan simpan catatan, atau justru
    permintaan memori/aturan/preferensi (yang ditangani save_memory_to_notion).
    """
    if not text:
        return None

    text_lower = text.lower().strip()
    # Permintaan memori/aturan/preferensi → bukan simpan catatan umum
    if any(rk in text_lower for rk in [
        "aturan", "preferensi", "ingat bahwa", "ingat bahwa saya",
        "panggil aku", "mulai sekarang", "simpan memori", "catat aturan", "catat preferensi",
    ]):
        return None

    save_phrases = NOTE_REQUEST_KEYWORDS + [
        "catatan notion", "notes notion", "simpan catatan", "tulis catatan",
        "catat ini", "simpan ini", "simpan ke catatan",
    ]
    if not any(sp in text_lower for sp in save_phrases):
        return None

    # Buang prefiks perintah (simpan/catat/tulis ... ke notion :)
    cleaned = re.sub(
        r'^(simpan|catat|tulis|rekam|save|buat)\s*(ke|di|k)?\s*(catatan|note|notes|notion)?\s*(notion)?\s*[:,\-]?\s*',
        '', text.strip(), flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip(" :,;-")
    if not cleaned:
        return None

    title = (cleaned.split('.')[0].strip()[:60]) or "Catatan"
    return title, cleaned


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handler utama untuk semua pesan teks biasa.
    Meneruskan pesan ke Gemini pipeline untuk diproses (Fast Path vs Slow Path).
    """
    if not update.effective_chat or not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_message = update.message.text.strip()

    if not user_message:
        return

    # --- Klarifikasi Ask-Before-Act: tangani jawaban user atas pertanyaan klarifikasi (brief.md) ---
    from src.kv import clear_clarify_state, get_clarify_state
    clarify_state = await get_clarify_state(chat_id)
    if clarify_state:
        answer_lower = user_message.lower()
        await clear_clarify_state(chat_id)
        if any(k in answer_lower for k in CONFIRM_KEYWORDS):
            # User mengonfirmasi — proses ulang perintah asli yang ambigu lewat pipeline normal
            user_message = clarify_state.get("perintah_asli") or user_message
        elif any(k in answer_lower for k in SKIP_KEYWORDS):
            await update.effective_chat.send_message("Baik, abaikan saja pertanyaan tadi. Ada lagi yang bisa aku bantu?")
            return
        # selain itu (jawaban tidak jelas), lanjutkan proses pesan apa adanya

    # Rate limiting

    if not await check_rate_limit(chat_id):
        await update.effective_chat.send_message(
            "Mohon tunggu sebentar sebelum mengirim pesan kembali."
        )
        return

    # Ambil nama pengguna dari Telegram
    user_name = "Teman"
    if update.effective_user and update.effective_user.first_name:
        user_name = update.effective_user.first_name

    # --- Pending Task: Cek keputusan pengguna untuk pending task ---
    from src.kv import clear_pending_task, get_pending_task
    pending_task = await get_pending_task(chat_id)
    if pending_task:
        if is_skip_request(user_message):
            await clear_pending_task(chat_id)
            await update.effective_chat.send_message("Baik, task telah dilewati. Apakah ada hal lain yang bisa saya bantu?")
            return

        if is_status_request(user_message):
            await update.effective_chat.send_message(build_task_status_message(pending_task))
            return

        if is_retry_request(user_message):
            await update.effective_chat.send_action("typing")
            retry_result = await retry_pending_task(chat_id)
            if retry_result:
                prefix = "Perintah Anda yang sebelumnya telah berhasil diproses: 🎉\n\n"
                response = prefix + retry_result
                if len(response) > 4096:
                    for i in range(0, len(response), 4096):
                        await update.effective_chat.send_message(response[i : i + 4096])
                else:
                    await update.effective_chat.send_message(response)
                return
            else:
                await update.effective_chat.send_message(
                    "Proses ini mengalami kendala teknis. Apakah Anda ingin mencoba ulang atau melewati task ini?"
                )
                return

    # Status task ditanyakan padahal tidak ada task berjalan
    if is_status_request(user_message) and not pending_task:
        await update.effective_chat.send_message(
            "Saat ini tidak ada task yang sedang diproses. Mau aku bantu apa lagi? 😊"
        )
        return

    # Deteksi jika pesan berisi aturan/preferensi baru untuk disimpan ke Notion
    if is_rule_message(user_message):
        try:
            from src.notion import save_memory_to_notion
            rule_title = generate_memory_title(user_message)
            await save_memory_to_notion(title=rule_title, content=user_message, memory_type="Aturan")
        except Exception as rule_err:
            logger.warning("Gagal menyimpan aturan ke Notion: %s", str(rule_err))

    # Deteksi jika pesan merupakan permintaan perbaikan error (brief.md poin 4)
    if is_fix_request(user_message):
        from src.handlers import lakukan_perbaikan_via_github
        from src.kv import get_last_error_diagnosis
        diagnosis = await get_last_error_diagnosis(chat_id)
        if diagnosis:
            await update.effective_chat.send_action("typing")
            pr_res = await lakukan_perbaikan_via_github(chat_id, diagnosis)
            await update.effective_chat.send_message(pr_res)
            return
        else:
            await update.effective_chat.send_message(
                "Aku belum punya diagnosis error. Coba tanya dulu 'ada error apa?'"
            )
            return

    # --- Klarifikasi Ask-Before-Act: deteksi perintah ambigu (brief.md) ---
    # Hanya bertanya jika benar-benar ambigu dan menyangkut fitur spesifik; perintah jelas langsung lanjut.
    if is_ambiguous(user_message):
        target_question = get_clarify_question(user_message)
        if target_question != "Maksud kamu apa ya? Bisa lebih spesifik?":
            from src.kv import save_clarify_state
            await save_clarify_state(chat_id, user_message)
            await update.effective_chat.send_message(target_question)
            return

    # Deteksi intent untuk menentukan Fast Path / Slow Path (dengan dukungan konteks percakapan)
    intent = await detect_intent_async(user_message, chat_id)

    # --- Feature Flag Check Sebelum Dipakai (brief.md) ---
    if intent is not None and intent not in ("health", "kelola_fitur"):
        intent_to_feature = {
            "saham": "saham",
            "cuaca": "cuaca",
            "gambar": "vision",
            "vision": "vision",
            "preview": "landing_page",
            "deploy": "deploy",
            "notion": "notion",
            "drive": "drive",
            "calendar": "calendar",
            "search": "search",
            "suara": "suara",
            "jurnal": "jurnal",
            "neo4j": "neo4j",
            "coding": "coding",
            "akademik": "akademik",
            "cek_token": "cek_token",
            "renew_token": "renew_token",
        }
        feat_name = intent_to_feature.get(intent, intent)
        from src.kv import is_feature_active
        if not await is_feature_active(feat_name):
            await update.effective_chat.send_message(
                f"Fitur {feat_name} sedang dinonaktifkan. Mau diaktifkan lagi?"
            )
            return

    # --- Notion: pastikan simpan catatan BENAR-BENAR memanggil tool & terverifikasi (anti mengarang) ---
    # Simpan catatan dieksekusi langsung, bukan diserahkan ke keputusan model yang bisa mengarang.
    if intent == "notion":
        extracted = _extract_notion_note(user_message)
        if extracted:
            title, content = extracted
            from src.notion import save_note_to_notion
            result = await save_note_to_notion(title=title, content=content)
            ok = result.get("status") == "success"
            logger.info(
                "Notion note-save (imperatif): title=%r status=%s",
                title, "success" if ok else result.get("error"),
            )
            if ok:
                await update.effective_chat.send_message(
                    f"✅ Catatan '{result.get('title')}' berhasil disimpan ke Notion."
                )
            else:
                await update.effective_chat.send_message(
                    f"❌ Gagal menyimpan catatan ke Notion: {result.get('error')}"
                )
            return

    # --- Kuota AI: jalankan langsung (anti model menjawab generik tanpa list) ---
    # Kuota dieksekusi imperatif agar SELALU menampilkan rincian list model AI,
    # tidak diserahkan ke keputusan model yang bisa meringkas menjadi satu baris.
    if intent == "kuota":
        try:
            from src.tools import check_ai_quota
            from src.kv import save_history
            report = await check_ai_quota(chat_id)
            await update.effective_chat.send_message(report)
            try:
                history = await get_history(chat_id)
                history.append({"role": "user", "text": user_message})
                history.append({"role": "model", "text": report})
                await save_history(chat_id, history)
            except Exception as hist_err:
                logger.warning("Gagal simpan history kuota: %s", str(hist_err))
            return
        except Exception as quota_err:
            logger.error("Gagal eksekusi kuota imperatif: %s", str(quota_err))

    # --- Async Landing Page Path (Anti Gantung & Notifikasi Progres Satu Pesan) ---
    if is_landing_page_generation_request(user_message, intent):
        from src.handlers import delegate_to_worker
        from src.kv import save_pending_task, save_progress_message_id, save_task_start

        # Catat waktu mulai task agar pesan progres menampilkan waktu proses nyata.
        await save_task_start(chat_id)

        # Kirim SATU pesan progres awal sesuai brief.md
        progres_msg = await update.effective_chat.send_message(
            "⏳ Permintaan diterima, mulai memproses..."
        )
        msg_id = progres_msg.message_id if progres_msg else None

        if msg_id:
            await save_progress_message_id(chat_id, msg_id)

        # Simpan pending task (ditandai delegated agar cron /api/process_pending tidak memproses ulang)
        await save_pending_task(
            chat_id=chat_id,
            user_message=user_message,
            intent=intent,
            user_name=user_name,
            message_id=msg_id,
            delegated=True,
        )

        # Delegate ke Render worker; fallback ke proses lokal bila Render down
        delegated = await delegate_to_worker(chat_id, user_message, intent, user_name, msg_id)
        if not delegated:
            logger.warning("Delegate ke Render gagal; fallback proses lokal chat=%s", chat_id)
            await save_pending_task(
                chat_id=chat_id,
                user_message=user_message,
                intent=intent,
                user_name=user_name,
                message_id=msg_id,
                delegated=False,
            )
            # Trigger endpoint background terpisah (invocation serverless baru yang
            # tetap hidup) agar task diproses — bukan proses in-instance yang mati
            # saat webhook /api/index mengembalikan 200.
            from src.handlers import trigger_process_pending_endpoint
            asyncio.create_task(trigger_process_pending_endpoint())
        return

    # --- Akademik (ERINE): Acknowledge First, Process Later (brief.md) ---
    # Intent akademik diproses di background agar tidak memblokir webhook (ERINE bisa lambat/timeout).
    if intent == "akademik":
        from src.kv import save_pending_task, save_progress_message_id

        progres_msg = await update.effective_chat.send_message(
            "⏳ Baik, permintaan kamu sedang diproses. Aku kabari setelah selesai ya."
        )
        msg_id = progres_msg.message_id if progres_msg else None

        if msg_id:
            await save_progress_message_id(chat_id, msg_id)

        await save_pending_task(
            chat_id=chat_id,
            user_message=user_message,
            intent=intent,
            user_name=user_name,
            message_id=msg_id,
        )

        from src.handlers import trigger_process_pending_endpoint
        asyncio.create_task(trigger_process_pending_endpoint())
        return

    # --- Intent berat lainnya: Acknowledge First, Process Later (anti 504 webhook) ---
    # Diproses di background agar webhook balas 200 cepat; hasil dikirim setelah selesai.
    if intent in HEAVY_BACKGROUND_INTENTS:
        from src.kv import save_pending_task, save_progress_message_id

        progres_msg = await update.effective_chat.send_message(
            "⏳ Baik, permintaan kamu sedang diproses. Aku kabari setelah selesai ya."
        )
        msg_id = progres_msg.message_id if progres_msg else None

        if msg_id:
            await save_progress_message_id(chat_id, msg_id)

        await save_pending_task(
            chat_id=chat_id,
            user_message=user_message,
            intent=intent,
            user_name=user_name,
            message_id=msg_id,
        )

        from src.handlers import trigger_process_pending_endpoint
        asyncio.create_task(trigger_process_pending_endpoint())
        return

    # Kirim "typing" action HANYA untuk Slow Path (fitur berat) untuk memangkas latensi Fast Path
    if intent is not None:
        await update.effective_chat.send_action("typing")

    # Proses lewat pipeline AI
    response = await chat_with_oline(
        chat_id,
        user_message,
        user_name=user_name,
        intent=intent,
    )

    # Otomatis panggil self_monitor & health monitoring untuk slow path (fitur berat) per brief.md
    if intent is not None:
        try:
            from src.self_monitor import self_monitor
            asyncio.create_task(self_monitor(chat_id))
        except Exception as sm_err:
            logger.warning("Failed to trigger self_monitor: %s", str(sm_err))

        try:
            from src.health_check import run_monitoring_and_notify
            asyncio.create_task(run_monitoring_and_notify(chat_id))
        except Exception as hc_err:
            logger.warning("Failed to trigger health monitoring: %s", str(hc_err))

    # Kirim respons (split jika terlalu panjang)
    from src.utils import clean_tool_call_text
    response = clean_tool_call_text(response)
    logger.info("[SEND] chat_id=%s, intent=%s, resp_len=%s, resp_head=%r",
                chat_id, intent, len(response), response[:60])
    if len(response) > 4096:
        # Telegram max 4096 chars per pesan
        for i in range(0, len(response), 4096):
            chunk = response[i : i + 4096]
            await update.effective_chat.send_message(chunk)
    else:
        await update.effective_chat.send_message(response)


async def download_telegram_file_with_retry(
    context: ContextTypes.DEFAULT_TYPE, file_id: str, max_retries: int = 3
) -> bytes | None:
    """
    Mengunduh file dari Telegram API dengan percobaan ulang (retry 3x) jika terjadi timeout/network error.
    """
    for attempt in range(1, max_retries + 1):
        try:
            file_obj = await context.bot.get_file(file_id)
            byte_arr = await file_obj.download_as_bytearray()
            return bytes(byte_arr)
        except Exception as e:
            logger.warning("Attempt %d/%d download Telegram file (%s) failed: %s", attempt, max_retries, file_id, str(e))
            if attempt < max_retries:
                await asyncio.sleep(attempt * 1.0)
    return None


async def handle_file_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handler untuk menerima foto atau dokumen yang dikirim pengguna.
    Menggunakan retry otomatis, kompresi foto, dan analisis Moondream VLM.
    """
    if not update.effective_chat or not update.message:
        return

    chat_id = update.effective_chat.id

    if not await check_rate_limit(chat_id):
        await update.effective_chat.send_message(
            "Mohon tunggu sebentar sebelum mengirim pesan kembali."
        )
        return

    file_id = None
    file_name = "file_oline"
    mime_type = "application/octet-stream"

    is_photo = False
    if update.message.document:
        doc = update.message.document
        file_name = doc.file_name or "dokumen_oline"
        mime_type = doc.mime_type or "application/octet-stream"
        file_id = doc.file_id
    elif update.message.photo:
        is_photo = True
        photo = update.message.photo[-1]
        file_name = f"foto_{photo.file_unique_id}.jpg"
        mime_type = "image/jpeg"
        file_id = photo.file_id

    if not file_id:
        return

    await update.effective_chat.send_action("typing")
    file_bytes = await download_telegram_file_with_retry(context, file_id)

    if not file_bytes:
        await update.effective_chat.send_message(
            "Gagal mengunduh berkas dari Telegram. Silakan coba kirim kembali."
        )
        return

    # Batasi ukuran file agar tidak melebihi batas payload Vercel (~4.5 MB)
    MAX_SIZE = 4 * 1024 * 1024  # 4 MB
    if len(file_bytes) > MAX_SIZE:
        await update.effective_chat.send_message(
            "Fotonya kegedean, kirim sebagai file ya~ (maksimal 4 MB)"
        )
        return

    caption = (update.message.caption or "").strip()

    # Jika foto dan bukan permintaan simpan ke drive secara eksplisit, gunakan Moondream VLM
    is_drive_request = any(
        kw in caption.lower() for kw in ["simpan", "folder", "drive", "upload", "database"]
    )
    if is_photo and not is_drive_request:
        await update.effective_chat.send_action("typing")
        caption_lower = caption.lower()
        is_identification = any(kw in caption_lower for kw in ["ini apa", "ini siapa", "identifikasi", "apa ini", "siapa ini", "merek apa", "siapa dia"])

        if is_identification:
            status_msg = await update.effective_chat.send_message("Menganalisis gambar... 🔍")
            from src.tools import identify_image_subject
            raw_result = await identify_image_subject(file_bytes, context_hint=caption)
        else:
            status_msg = await update.effective_chat.send_message("Menganalisis tampilan gambar... 🔍")
            from src.tools import analyze_image

            if any(kw in caption_lower for kw in ["deteksi objek", "objek apa", "ada apa saja", "objek"]):
                task = "Object Detection"
            elif caption and ("?" in caption or len(caption.split()) > 2):
                task = "Visual Question Answering"
            else:
                task = "Caption"

            english_question = caption if caption else ("objects" if task == "Object Detection" else "Describe this image.")

            await update.effective_chat.send_action("typing")
            raw_result = await analyze_image(file_bytes, question=english_question, task=task)

        if "mataku lagi error" in raw_result or "Gagal menganalisis" in raw_result:
            try:
                await status_msg.edit_text(raw_result)
            except Exception:
                await update.effective_chat.send_message(raw_result)
            return

        user_name = "Teman"
        if update.effective_user and update.effective_user.first_name:
            user_name = update.effective_user.first_name

        translation_prompt = (
            f"Berikut adalah hasil analisis gambar dari model vision (dalam bahasa Inggris):\n"
            f"\"{raw_result}\"\n\n"
            f"Pertanyaan/caption pengguna: \"{caption or 'Deskripsikan gambar ini'}\"\n\n"
            f"Tolong sampaikan ulang kepada pengguna dalam Bahasa Indonesia yang profesional, jelas, dan mudah dipahami, sesuai persona Oline. DILARANG menampilkan istilah teknis seperti 'Reasoning:' atau 'Answer:'."
        )

        await update.effective_chat.send_action("typing")
        response = await chat_with_oline(chat_id, translation_prompt, user_name=user_name)

        # Preservasi hasil Moondream agar tidak hilang jika AI pipeline mengembalikan teks kosong
        if not response or not response.strip():
            response = f"Oline mengidentifikasi objek berikut: {raw_result}"

        try:
            await status_msg.edit_text(response)
        except Exception:
            await update.effective_chat.send_message(response)
        return

    from src.kv import save_pending_file
    await save_pending_file(chat_id, file_name, file_bytes, mime_type)

    user_name = "Teman"
    if update.effective_user and update.effective_user.first_name:
        user_name = update.effective_user.first_name

    if caption:
        await update.effective_chat.send_action("typing")
        response = await chat_with_oline(
            chat_id, caption, user_name=user_name, intent="drive"
        )
        await update.effective_chat.send_message(response)
    else:
        await update.effective_chat.send_message(
            f"File '{file_name}' telah diterima. 📄✨\n\n"
            "Mohon tentukan folder tujuan atau perintah penyimpanan yang Anda inginkan "
            "(misal: \"simpan ke folder Skripsi\")."
        )


async def handle_location_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handler untuk pesan lokasi Telegram (latitude & longitude).
    Menyimpan koordinat lokasi pengguna ke Vercel KV.
    """
    if not update.effective_chat or not update.message or not update.message.location:
        return

    chat_id = update.effective_chat.id

    if not await check_rate_limit(chat_id):
        await update.effective_chat.send_message(
            "Mohon tunggu sebentar sebelum mengirim pesan kembali."
        )
        return

    location = update.message.location
    lat = location.latitude
    lon = location.longitude

    from src.kv import save_user_location
    success = await save_user_location(chat_id, lat, lon)

    if success:
        await update.effective_chat.send_message(
            "Lokasi Anda telah berhasil disimpan. 📍✨\n"
            "Sekarang Anda dapat mencari rekomendasi tempat di sekitar lokasi Anda "
            "(misal: \"cafe terdekat\" atau \"toko buku terdekat\")."
        )
    else:
        await update.effective_chat.send_message(
            "Gagal menyimpan lokasi Anda. Silakan coba kirim kembali titik lokasi Anda."
        )


async def send_drive_file_to_telegram(
    chat_id: int, file_bytes: bytes, file_name: str, mime_type: str
) -> bool:
    """
    Mengirimkan file atau foto dari Google Drive kembali ke chat Telegram pengguna.
    """
    if not TELEGRAM_BOT_TOKEN:
        return False

    try:
        from telegram import Bot

        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        if "image" in mime_type.lower() or file_name.lower().endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".gif")
        ):
            await bot.send_photo(
                chat_id=chat_id,
                photo=file_bytes,
                caption=f"📷 {file_name} dari Database Oline",
            )
        else:
            await bot.send_document(
                chat_id=chat_id,
                document=file_bytes,
                filename=file_name,
                caption=f"📄 {file_name} dari Database Oline",
            )
        return True
    except Exception as e:
        logger.error("Failed to send Drive file to Telegram: %s", str(e))
        return False



