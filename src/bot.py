"""
Telegram Bot setup dan handler untuk Oline.
Mengelola penerimaan pesan dan routing ke Gemini pipeline.
"""

import asyncio
import logging
import os
import re

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
    "jurnal": ["jurnal", "catat", "rekap jurnal"],
    "kuota": ["kuota", "token", "quota"],
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
    if any(kw in text_lower for kw in ["list", "daftar", "hapus", "delete"]):
        return False

    return True


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

    # Deteksi intent untuk menentukan Fast Path / Slow Path (dengan dukungan konteks percakapan)
    intent = await detect_intent_async(user_message, chat_id)

    # --- Async Landing Page Path (Anti Gantung & Notifikasi Progres Satu Pesan) ---
    if is_landing_page_generation_request(user_message, intent):
        from src.handlers import process_pending_task
        from src.kv import save_pending_task, save_progress_message_id

        # Kirim SATU pesan progres awal sesuai brief.md
        progres_msg = await update.effective_chat.send_message(
            "⏳ Permintaan diterima. Estimasi 30 detik."
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

        # Jalankan pemrosesan background secara instan jika event loop berjalan
        asyncio.create_task(process_pending_task(target_chat_id=chat_id))
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

    # Otomatis panggil self_monitor untuk slow path (fitur berat) per brief.md
    if intent is not None:
        try:
            from src.self_monitor import self_monitor
            asyncio.create_task(self_monitor(chat_id))
        except Exception as sm_err:
            logger.warning("Failed to trigger self_monitor: %s", str(sm_err))

    # Kirim respons (split jika terlalu panjang)
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



