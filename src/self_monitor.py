"""
Modul Self-Monitoring untuk memeriksa log Vercel setelah eksekusi tools/fitur berat (slow path).
Menganalisis error dengan AI ringan dan mengirim notifikasi jika ditemukan masalah baru.
"""

import hashlib
import json
import logging
from typing import Optional

from src.kv import (
    get_last_error_signature,
    save_last_error_diagnosis,
    save_last_error_signature,
)
from src.vercel_logs import fetch_vercel_logs

logger = logging.getLogger(__name__)


async def ai_analyze_logs(logs: str) -> dict:
    """
    Menganalisis log Vercel dengan AI ringan dan mengembalikan dict format JSON:
    {"error": "...", "file": "...", "saran": "..."}
    """
    prompt = (
        f"Berikut log Vercel:\n"
        f"{logs}\n\n"
        f"Tolong identifikasi:\n"
        f"1. Error utama apa yang terjadi?\n"
        f"2. File mana yang kemungkinan bermasalah? (misal: src/tools.py, src/bot.py, src/handlers.py)\n"
        f"3. Saran perbaikan singkat.\n\n"
        f"Kembalikan HANYA format JSON valid berikut (tanpa markdown backtick):\n"
        f'{{"error": "...", "file": "...", "saran": "..."}}'
    )

    try:
        from src.gemini import chat_with_oline
        resp = await chat_with_oline(
            chat_id=0,
            user_message=prompt,
            user_name="System",
            use_gemini_only=True,
        )
        if resp:
            cleaned = resp.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()
            data = json.loads(cleaned)
            if isinstance(data, dict) and "error" in data:
                return data
    except Exception as e:
        logger.warning("Failed to parse AI log analysis: %s", str(e))

    # Fallback default dict
    file_guess = "src/tools.py"
    logs_lower = logs.lower()
    if "handlers" in logs_lower:
        file_guess = "src/handlers.py"
    elif "bot" in logs_lower:
        file_guess = "src/bot.py"
    elif "gemini" in logs_lower:
        file_guess = "src/gemini.py"
    elif "notion" in logs_lower:
        file_guess = "src/notion.py"

    return {
        "error": logs[:250].replace("\n", " "),
        "file": file_guess,
        "saran": "Periksa penanganan error dan pastikan fungsi berjalan sesuai ekspektasi.",
    }


async def self_monitor(chat_id: int) -> None:
    """
    Otomatis memeriksa log Vercel setelah eksekusi tools/fitur berat (slow path).
    Jika ditemukan error baru, menganalisis dan mengirim notifikasi via Telegram.
    """
    if not chat_id:
        return

    try:
        # Ambil log error dan warn terbaru
        err_logs = await fetch_vercel_logs(limit=10, level="error")
        warn_logs = await fetch_vercel_logs(limit=10, level="warn")

        logs_combined = ""
        if (
            err_logs
            and not err_logs.startswith("Token")
            and not err_logs.startswith("Gagal")
            and not err_logs.startswith("Error")
            and "Tidak ada log" not in err_logs
        ):
            logs_combined += err_logs

        if (
            warn_logs
            and not warn_logs.startswith("Token")
            and not warn_logs.startswith("Gagal")
            and not warn_logs.startswith("Error")
            and "Tidak ada log" not in warn_logs
        ):
            if logs_combined:
                logs_combined += "\n" + warn_logs
            else:
                logs_combined = warn_logs

        if not logs_combined.strip():
            return  # Tidak ada error, diam

        # Cek signature MD5 untuk hindari notifikasi duplikat
        signature = hashlib.md5(logs_combined.encode("utf-8")).hexdigest()
        last_sig = await get_last_error_signature(chat_id)
        if last_sig == signature:
            return  # Error yang sama sudah diberitahukan sebelumnya

        # Analisis dengan AI
        diagnosis = await ai_analyze_logs(logs_combined)
        diagnosis["signature"] = signature

        # Simpan diagnosis & signature ke KV
        await save_last_error_diagnosis(chat_id, diagnosis)
        await save_last_error_signature(chat_id, signature)

        # Kirim notifikasi Telegram ke pengguna via send_telegram_message
        from src.handlers import send_telegram_message
        error_title = diagnosis.get("error", "Error pada runtime Vercel")
        notif_text = f"⚠️ Oline mendeteksi error:\n{error_title}\n\nMau diperbaiki?"
        await send_telegram_message(chat_id, notif_text)

    except Exception as e:
        logger.error("Error in self_monitor: %s", str(e))
