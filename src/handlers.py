"""
Handler modul untuk pemrosesan pending task dan pembaruan progres satu pesan dinamis (edit_message_text).
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Optional

from telegram import Bot

from src.gemini import chat_with_oline
from src.kv import (
    clear_pending_task,
    clear_progress_message_id,
    delete_checkpoint,
    get_all_pending_tasks,
    get_checkpoint,
    get_pending_task,
    get_progress_message_id,
    save_checkpoint,
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


def build_context_prompt(checkpoint: dict) -> str:
    """
    Menyusun prompt konteks lengkap dari checkpoint untuk kelanjutan fallback (brief.md).
    """
    perintah = checkpoint.get("perintah_asli", "")
    style_guide = checkpoint.get("style_guide", "Desain profesional, bersih, dan responsif.")
    langkah_selesai = ", ".join(checkpoint.get("langkah_selesai", [])) or "(belum ada)"
    langkah_sekarang = checkpoint.get("langkah_sekarang", "generate_html")
    data_str = json.dumps(checkpoint.get("data", {}), indent=2, ensure_ascii=False)

    return f"""Kamu melanjutkan task yang belum selesai.

Perintah asli pengguna:
{perintah}

Style guide / aturan desain:
{style_guide}

Langkah yang sudah selesai:
{langkah_selesai}

Langkah yang sedang kamu kerjakan sekarang:
{langkah_sekarang}

Hasil dari langkah sebelumnya:
{data_str}

Kerjakan HANYA langkah yang belum selesai ({langkah_sekarang}). Jangan mengubah hasil langkah sebelumnya. Tetap sesuai perintah asli dan style guide."""


async def process_pending_task(target_chat_id: Optional[int] = None) -> dict[str, Any]:
    """
    Memproses pending task landing page / background tasks yang tersimpan di KV.
    Meng-update SATU pesan progres secara bertahap via edit_message_text dan memanfaatkan task checkpoint.
    
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

        # Inisialisasi atau ambil task checkpoint tersimpan per brief.md
        checkpoint = await get_checkpoint(chat_id)
        if not checkpoint:
            checkpoint = {
                "perintah_asli": perintah,
                "style_guide": "Desain modern, responsif, kontras tinggi, typography berkarakter, tanpa placeholder abu-abu",
                "langkah_selesai": [],
                "langkah_sekarang": "generate_html",
                "data": {},
                "retry_count": 0,
                "max_retry": 3,
                "waktu_terakhir": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            await save_checkpoint(chat_id, checkpoint)

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

            # Gunakan context prompt jika checkpoint memiliki langkah selesai sebelumnya
            effective_prompt = build_context_prompt(checkpoint) if checkpoint.get("langkah_selesai") else perintah

            # Eksekusi pipeline generasi landing page via chat_with_oline
            response_text = await chat_with_oline(
                chat_id=chat_id,
                user_message=effective_prompt,
                user_name=user_name,
                intent=intent,
                is_retry=True,
            )

            # Update checkpoint setelah berhasil
            if "preview" not in checkpoint.get("langkah_selesai", []):
                checkpoint["langkah_selesai"].append("generate_html")
                checkpoint["langkah_selesai"].append("generate_css")
                checkpoint["langkah_selesai"].append("preview")
                checkpoint["langkah_sekarang"] = "selesai"
                await save_checkpoint(chat_id, checkpoint)

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

            # Bersihkan task, checkpoint & progress message_id setelah sukses penuh
            await clear_pending_task(chat_id)
            await clear_progress_message_id(chat_id)
            await delete_checkpoint(chat_id)

            processed_count += 1
            results.append({
                "chat_id": chat_id,
                "status": "success",
                "perintah": perintah[:60],
            })

        except Exception as e:
            err_msg = str(e)
            logger.error("Failed to process pending task for chat_id %s: %s", chat_id, err_msg)
            
            # Increment retry count pada checkpoint per brief.md
            checkpoint["retry_count"] = checkpoint.get("retry_count", 0) + 1
            if checkpoint["retry_count"] >= checkpoint.get("max_retry", 3):
                await delete_checkpoint(chat_id)
                fail_text = f"❌ Task ini gagal setelah beberapa percobaan. Penyebab: {err_msg[:100]}. Mau dilanjutkan lagi?"
            else:
                await save_checkpoint(chat_id, checkpoint)
                fail_text = f"❌ Gagal di langkah {checkpoint.get('langkah_sekarang')}. Penyebab: {err_msg[:100]}. Mau coba lagi?"

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



async def call_model_with_fallback(
    jalur: str,
    system_prompt: str,
    history: list[dict[str, Any]],
    user_message: str,
    tools: Optional[list[dict]] = None,
    chat_id: int = 0,
) -> str:
    """
    Eksekusi alur pemanggilan model AI dengan urutan fallback optimal per jalur (brief.md):
    - 'fast' (Chat Ringan): Groq -> Mistral -> Cerebras -> OpenRouter -> Gemini
    - 'tools' (Tools Ringan): Mistral -> Gemini -> Cerebras -> OpenRouter -> Groq
    - 'landing' (Landing Page/Deploy): DeepSeek -> Mistral -> Gemini -> Cerebras -> OpenRouter
    """
    if jalur == "fast":
        order = ["groq", "mistral", "cerebras", "openrouter", "gemini"]
    elif jalur == "tools":
        order = ["mistral", "gemini", "cerebras", "openrouter", "groq"]
    elif jalur == "landing":
        order = ["deepseek", "mistral", "gemini", "cerebras", "openrouter"]
    else:
        order = ["groq", "mistral", "gemini"]

    for provider in order:
        try:
            if provider == "groq" and os.environ.get("GROQ_API_KEY", "").strip():
                from src.groq import chat_groq, chat_groq_with_tools
                if tools:
                    res = await chat_groq_with_tools(system_prompt=system_prompt, history=history, user_message=user_message, tools=tools, chat_id=chat_id)
                else:
                    res = await chat_groq(system_prompt, history, user_message, chat_id=chat_id)
                if res and res.strip():
                    return res.strip()

            elif provider == "mistral" and os.environ.get("MISTRAL_API_KEY", "").strip():
                logger.info("Mencoba Mistral... (jalur: %s)", jalur)
                from src.mistral import chat_mistral
                res = await chat_mistral(system_prompt=system_prompt, history=history, user_message=user_message, tool_declarations=tools, chat_id=chat_id)
                if res and res.strip():
                    return res.strip()

            elif provider == "cerebras" and os.environ.get("CEREBRAS_API_KEY", "").strip():
                logger.info("Mencoba Cerebras... (jalur: %s)", jalur)
                from src.cerebras import chat_cerebras
                res = await chat_cerebras(system_prompt=system_prompt, history=history, user_message=user_message, tool_declarations=tools, chat_id=chat_id)
                if res and res.strip():
                    return res.strip()

            elif provider == "openrouter" and os.environ.get("OPENROUTER_API_KEY", "").strip():
                from src.openrouter import chat_openrouter
                res = await chat_openrouter(system_prompt=system_prompt, history=history, user_message=user_message, tool_declarations=tools, chat_id=chat_id)
                if res and res.strip():
                    return res.strip()

            elif provider == "deepseek" and os.environ.get("DEEPINFRA_API_KEY", "").strip():
                from src.deepinfra import chat_deepinfra
                res = await chat_deepinfra(system_prompt=system_prompt, history=history, user_message=user_message, tool_declarations=tools, chat_id=chat_id)
                if res and res.strip():
                    return res.strip()

            elif provider == "gemini" and os.environ.get("GEMINI_API_KEY", "").strip():
                from src.gemini import _build_tools, _format_history_for_gemini, _generate_content_with_fallback
                gemini_tools = _build_tools(tools) if tools else None
                contents = _format_history_for_gemini(history)
                contents.append({"role": "user", "parts": [{"text": user_message}]})
                resp, _, _ = await _generate_content_with_fallback(system_prompt, gemini_tools, contents, timeout_seconds=8.0)
                if hasattr(resp, "text") and resp.text and resp.text.strip():
                    return resp.text.strip()
        except Exception as e:
            logger.warning("Provider '%s' failed on fallback chain ('%s'): %s", provider, jalur, str(e))
            continue

    return "Semua model AI sedang error. Coba lagi nanti ya."


async def lakukan_perbaikan_via_github(chat_id: int, diagnosis: dict) -> str:
    """
    Menjalankan alur perbaikan otomatis berbasis GitHub (brief.md):
    1. Buat branch baru (oline-fix/YYYYMMDD-HHMMSS).
    2. Baca file bermasalah via GitHub API.
    3. Minta AI perbaiki kode via ai_fix_code.
    4. Commit & push file ke GitHub.
    5. Buat Pull Request dan kirimkan link PR.
    """
    from src.github_tools import (
        ai_fix_code,
        create_github_branch,
        create_pull_request,
        read_github_file,
        update_github_file,
    )

    error_msg = diagnosis.get("error", "Error runtime Vercel")
    file_path = diagnosis.get("file", "src/tools.py")

    # 1. Buat branch baru
    timestamp_str = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch = f"oline-fix/{timestamp_str}"
    branch_res = await create_github_branch(branch)
    if "Gagal" in branch_res or "Credentials" in branch_res:
        return f"Gagal membuat branch perbaikan: {branch_res}"

    # 2. Baca file yang bermasalah dari GitHub main
    current_code = await read_github_file(file_path, branch="main")
    if current_code.startswith("Credentials") or current_code.startswith("File "):
        return f"Gagal membaca file '{file_path}': {current_code}"

    # 3. Minta AI perbaiki kode
    fixed_code = await ai_fix_code(current_code, error_msg)

    # 4. Update file di GitHub
    commit_msg = f"fix: {error_msg[:50]}"
    update_res = await update_github_file(branch, file_path, fixed_code, commit_msg)
    if update_res.startswith("Gagal") or update_res.startswith("Credentials"):
        return f"Gagal memperbarui file di branch '{branch}': {update_res}"

    # 5. Buat Pull Request
    pr_title = f"Fix: {error_msg[:50]}"
    pr_body = "Perbaikan otomatis oleh Oline."
    pr_res = await create_pull_request(branch=branch, title=pr_title, body=pr_body)

    return f"Pull Request perbaikan sudah dibuat: {pr_res}"
