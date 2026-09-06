"""
Handler modul untuk pemrosesan pending task dan pembaruan progres satu pesan dinamis (edit_message_text).
"""

import asyncio
import logging
import os
from typing import Any, Optional

from telegram import Bot

from src.gemini import chat_with_oline
from src.kv import (
    clear_pending_task,
    clear_progress_message_id,
    get_all_pending_tasks,
    get_pending_task,
    get_progress_message_id,
)

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")


async def send_telegram_message(chat_id: int, text: str) -> bool:
    """
    Mengirimkan pesan baru ke chat Telegram pengguna.
    Memotong pesan jika melebihi 4096 karakter.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not configured.")
        return False

    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await bot.send_message(chat_id=chat_id, text=text[i : i + 4096])
        else:
            await bot.send_message(chat_id=chat_id, text=text)
        return True
    except Exception as e:
        logger.error("Error sending Telegram message to %s: %s", chat_id, str(e))
        return False


async def update_progress(chat_id: int, message_id: int, text: str) -> bool:
    """
    Memperbarui isi dari SATU pesan progres Telegram menggunakan edit_message_text.
    """
    if not TELEGRAM_BOT_TOKEN or not message_id:
        return False

    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        edit_text = text[:4096] if len(text) > 4096 else text
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=edit_text,
        )
        return True
    except Exception as e:
        logger.warning(
            "Error editing progress message %s for chat_id %s: %s",
            message_id, chat_id, str(e)
        )
        return False


async def process_pending_task(target_chat_id: Optional[int] = None) -> dict[str, Any]:
    """
    Memproses pending task landing page / background tasks yang tersimpan di KV.
    Meng-update SATU pesan progres secara bertahap via edit_message_text.
    
    Args:
        target_chat_id: Opsional, spesifik chat_id yang akan diproses. Jika None, memproses semua pending task.
        
    Returns:
        Dict hasil pemrosesan.
    """
    tasks: list[dict] = []

    if target_chat_id:
        single_task = await get_pending_task(target_chat_id)
        if single_task:
            single_task["chat_id"] = target_chat_id
            tasks.append(single_task)
    else:
        tasks = await get_all_pending_tasks()

    if not tasks:
        return {"status": "ok", "message": "Tidak ada task.", "processed": 0}

    processed_count = 0
    results = []

    for task in tasks:
        chat_id = task.get("chat_id")
        perintah = task.get("perintah") or task.get("message")
        intent = task.get("intent", "preview")
        user_name = task.get("user_name", "Teman")
        msg_id = task.get("message_id") or await get_progress_message_id(chat_id)

        if not chat_id or not perintah:
            continue

        logger.info(
            "Processing background pending task for chat_id %s (msg_id: %s): %s",
            chat_id, msg_id, perintah[:60]
        )

        try:
            # Langkah 1: Menyusun struktur HTML
            if msg_id:
                await update_progress(
                    chat_id, msg_id,
                    "⏳ [1/4] Menyusun struktur HTML... Sisa 25 detik."
                )

            # Langkah 2: Membuat CSS & Tampilan
            if msg_id:
                await update_progress(
                    chat_id, msg_id,
                    "⏳ [2/4] Membuat CSS & Tampilan Wabi-Sabi... Sisa 15 detik."
                )

            # Langkah 3: Menyiapkan Preview & Link
            if msg_id:
                await update_progress(
                    chat_id, msg_id,
                    "⏳ [3/4] Menambahkan efek Canvas & menyiapkan preview... Sisa 8 detik."
                )

            # Eksekusi pipeline generasi landing page via chat_with_oline (DeepSeek / tools)
            response_text = await chat_with_oline(
                chat_id=chat_id,
                user_message=perintah,
                user_name=user_name,
                intent=intent,
                is_retry=True,
            )

            # Edit pesan terakhir menjadi Selesai jika message_id tersedia
            success_edited = False
            if msg_id:
                final_text = f"✅ Selesai!\n\n{response_text}"
                if len(final_text) <= 4096:
                    success_edited = await update_progress(chat_id, msg_id, final_text)
                else:
                    await update_progress(
                        chat_id, msg_id,
                        "✅ Selesai! Hasil preview/deploy dikirimkan di bawah ini:"
                    )
                    success_edited = await send_telegram_message(chat_id, response_text)

            if not success_edited:
                await send_telegram_message(chat_id, response_text)

            # Bersihkan task & progress message_id setelah selesai
            await clear_pending_task(chat_id)
            await clear_progress_message_id(chat_id)

            processed_count += 1
            results.append({
                "chat_id": chat_id,
                "status": "success",
                "perintah": perintah[:60],
            })

        except Exception as e:
            err_msg = str(e)
            logger.error("Failed to process pending task for chat_id %s: %s", chat_id, err_msg)
            
            # Edit pesan awal menjadi info kegagalan jika message_id ada
            fail_text = f"❌ Gagal di proses landing page. Penyebab: {err_msg[:150]}. Mau coba lagi?"
            if msg_id:
                edited = await update_progress(chat_id, msg_id, fail_text)
                if not edited:
                    await send_telegram_message(chat_id, fail_text)
            else:
                await send_telegram_message(chat_id, fail_text)

            await clear_pending_task(chat_id)
            await clear_progress_message_id(chat_id)

            results.append({
                "chat_id": chat_id,
                "status": "error",
                "error": err_msg,
            })

    return {
        "status": "ok",
        "processed": processed_count,
        "results": results,
    }
