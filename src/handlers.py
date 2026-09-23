"""
Handler modul untuk pemrosesan pending task dan pembaruan progres satu pesan dinamis (edit_message_text).
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

from telegram import Bot

from src.gemini import chat_with_oline
from src.utils import clean_tool_call_text, clean_tool_calls
from src.kv import (
    clear_pending_task,
    clear_progress_message_id,
    clear_task_start,
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
        text = clean_tool_call_text(text)
        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await bot.send_message(chat_id=chat_id, text=text[i : i + 4096])
        else:
            await bot.send_message(chat_id=chat_id, text=text)
        return True
    except Exception as e:
        logger.error("Error sending Telegram message to %s: %s", chat_id, str(e))
        return False


async def delegate_to_worker(
    chat_id: int,
    perintah: str,
    intent: str,
    user_name: str = "Teman",
    message_id: Optional[int] = None,
) -> bool:
    """
    Melimpahkan task berat ke Render worker (POST /process).
    Hanya hand-off dengan timeout pendek; hasil dikirim worker langsung ke Telegram.
    Mengembalikan True jika worker menerima task (HTTP 200/202).
    """
    worker_url = os.environ.get("RENDER_WORKER_URL", "").strip().rstrip("/")
    worker_key = os.environ.get("OLINE_WORKER_KEY", "").strip()
    if not worker_url or not worker_key:
        logger.warning("RENDER_WORKER_URL / OLINE_WORKER_KEY belum diset; tidak bisa delegate.")
        return False

    # Timeout longgar untuk menahan cold start Render free tier (20-60 detik).
    # connect=30 memastikan instance Render yang tidur sempat bangun sebelum diputus.
    try:
        import httpx
        request_timeout = httpx.Timeout(90.0, connect=30.0)
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            resp = await client.post(
                f"{worker_url}/process",
                json={
                    "chat_id": chat_id,
                    "perintah": perintah,
                    "intent": intent,
                    "user_name": user_name,
                    "message_id": message_id,
                },
                headers={"X-Worker-Key": worker_key},
            )
            ok = resp.status_code in (200, 202)
            logger.info("Delegate ke Render: chat=%s intent=%s status=%s", chat_id, intent, resp.status_code)
            return ok
    except Exception as e:
        logger.warning("Gagal delegate ke Render: %s", str(e))
        return False


async def trigger_process_pending_endpoint() -> bool:
    """
    Memicu endpoint /api/process_pending secara mandiri (self-trigger) lewat invocation
    serverless terpisah. Berguna sebagai fallback saat Render down: task yang tersimpan
    di KV akan diproses oleh invocation ini, BUKAN proses in-instance yang mati saat
    webhook /api/index mengembalikan 200.
    """
    base = (
        os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "")
        or os.environ.get("VERCEL_URL", "")
    ).strip().rstrip("/")
    if not base:
        logger.warning("VERCEL_URL tidak tersedia; tidak bisa self-trigger /api/process_pending.")
        return False
    if not base.startswith("http"):
        base = f"https://{base}"
    url = f"{base}/api/process_pending"
    # Sertakan secret agar self-trigger lolos gate otorisasi /api/process_pending.
    headers = {}
    secret = (
        os.environ.get("PROCESS_PENDING_SECRET", "").strip()
        or os.environ.get("KEEPALIVE_SECRET", "").strip()
    )
    if secret:
        headers["X-Process-Pending-Secret"] = secret
        headers["X-Keepalive-Secret"] = secret
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url, headers=headers)
            logger.info("Self-trigger /api/process_pending: status=%s", resp.status_code)
            return resp.status_code == 200
    except Exception as e:
        logger.warning("Gagal self-trigger /api/process_pending: %s", str(e))
        return False


async def update_progress(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup=None,
) -> bool:
    """
    Memperbarui isi dari SATU pesan progres Telegram menggunakan edit_message_text.
    reply_markup (InlineKeyboardMarkup) opsional: tombol inline yang ikut di-update.
    """
    if not TELEGRAM_BOT_TOKEN or not message_id:
        return False

    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        from src.utils import append_elapsed_time
        edit_text = await append_elapsed_time(chat_id, text)
        edit_text = edit_text[:4096] if len(edit_text) > 4096 else edit_text
        kwargs = {}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=edit_text,
            **kwargs,
        )
        return True
    except Exception as e:
        logger.warning(
            "Error editing progress message %s for chat_id %s: %s",
            message_id, chat_id, str(e)
        )
        return False


async def send_or_edit_progress(
    chat_id: int,
    message_id: Optional[int],
    text: str,
    reply_markup=None,
) -> Optional[int]:
    """
    Helper bubble dinamis: edit pesan yang sudah ada (message_id) atau kirim pesan baru
    bila belum ada. Mengembalikan message_id bubble yang dipakai (untuk disimpan di KV).
    """
    if message_id:
        await update_progress(chat_id, message_id, text, reply_markup=reply_markup)
        return message_id
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    kwargs = {}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    sent = await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    return sent.message_id if sent else None


def coding_result_keyboard():
    """Tombol inline tahap hasil (Review / Merge / Hapus) — dipakai Vercel & worker."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Review", callback_data="pr:review"),
            InlineKeyboardButton("✅ Merge ke Main", callback_data="pr:merge"),
        ],
        [InlineKeyboardButton("❌ Hapus Branch", callback_data="pr:delete")],
    ])


PLAN_SYSTEM_PROMPT = (
    "Kamu adalah perencana teknis repository Python (bot Telegram Oline, Vercel Serverless). "
    "Diberi perintah perubahan kode, kamu menyusun rencana langkah demi langkah yang jelas. "
    "Balas HANYA JSON valid dengan skema: "
    '{"plan": "<judul singkat>", "langkah": ["<langkah 1>", ...], '
    '"file": ["<path file yang akan diubah/dibuat>", ...], '
    '"estimasi": "<perkiraan jumlah file/jumlah baris>"}. '
    "Jangan sertakan markdown, penjelasan tambahan, atau teks di luar JSON."
)


async def generate_coding_plan(perintah: str) -> dict:
    """
    Membuat rencana teknis (plan) untuk perintah coding user menggunakan DeepInfra
    (DeepSeek V4 Flash). Mengembalikan dict {plan, langkah, file, estimasi}.
    Fallback ke Gemini bila DeepInfra gagal.
    """
    try:
        from src.deepinfra import chat_deepinfra
        raw = await chat_deepinfra(
            system_prompt=PLAN_SYSTEM_PROMPT,
            history=[],
            user_message=perintah,
            tool_declarations=[],
            chat_id=0,
        )
        parsed = _parse_plan_json(raw)
        if parsed:
            return parsed
    except Exception as e:
        logger.warning("generate_coding_plan (DeepInfra) gagal: %s", str(e))

    # Fallback ke Gemini
    try:
        from src.gemini import chat_with_oline
        raw = await chat_with_oline(
            chat_id=0,
            user_message=perintah,
            user_name="System",
            use_gemini_only=True,
        )
        parsed = _parse_plan_json(raw)
        if parsed:
            return parsed
    except Exception as e:
        logger.warning("generate_coding_plan (Gemini) gagal: %s", str(e))

    # Plan sederhana default agar alur tetap berjalan walau model gagal
    return {
        "plan": f"Modifikasi: {perintah[:60]}",
        "langkah": [f"Terapkan perubahan untuk: {perintah}"],
        "file": [],
        "estimasi": "1 file diubah (perkiraan kasar)",
    }


def _parse_plan_json(raw: str) -> Optional[dict]:
    """Mengekstrak dict plan dari respons model (strip markdown fences bila ada)."""
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
            text = text[start:end + 1]
        data = json.loads(text)
        if isinstance(data, dict) and data.get("langkah"):
            return {
                "plan": str(data.get("plan", "Rencana modifikasi kode")),
                "langkah": [str(l) for l in data.get("langkah", [])],
                "file": [str(f) for f in data.get("file", [])],
                "estimasi": str(data.get("estimasi", "")),
            }
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Gagal parse plan JSON: %s", str(e))
    return None


def format_plan_text(plan: dict) -> str:
    """Memformat dict plan menjadi teks bubble yang rapi untuk Telegram."""
    lines = [f"📋 Plan: {plan.get('plan', 'Rencana')}", ""]
    langkah = plan.get("langkah", [])
    if langkah:
        lines.append("Langkah:")
        for i, l in enumerate(langkah, 1):
            lines.append(f"{i}. {l}")
    file = plan.get("file", [])
    if file:
        lines.append("")
        lines.append("File:")
        for f in file:
            lines.append(f"• {f}")
    estimasi = plan.get("estimasi", "")
    if estimasi:
        lines.append("")
        lines.append(f"Estimasi: {estimasi}")
    return "\n".join(lines)


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

        # Task yang sudah dilimpahkan ke Render worker diproses di sana,
        # bukan di Vercel (hindari duplikasi oleh cron /api/process_pending).
        if task.get("delegated"):
            logger.info("Skip task chat_id %s (delegated ke Render worker).", chat_id)
            continue

        logger.info(
            "Processing background pending task for chat_id %s (msg_id: %s): %s",
            chat_id, msg_id, perintah[:60]
        )

        is_landing = intent in ("preview", "deploy", "design_reference")

        # Inisialisasi atau ambil task checkpoint tersimpan per brief.md (khusus alur landing page)
        checkpoint = None
        if is_landing:
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
            if is_landing:
                # Langkah 1: Menyusun struktur HTML
                if msg_id:
                    await update_progress(
                        chat_id, msg_id,
                        "⏳ Menyusun struktur landing page..."
                    )

                # Langkah 2: Membuat CSS & Tampilan
                if msg_id:
                    await update_progress(
                        chat_id, msg_id,
                        "⏳ Merancang gaya visual & mencari referensi desain..."
                    )

                # Langkah 3: Menyiapkan Preview & Link
                if msg_id:
                    await update_progress(
                        chat_id, msg_id,
                        "⏳ Menyiapkan preview & link..."
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
            else:
                # --- Alur generik "Acknowledge First, Process Later" (brief.md) ---
                # Menangani intent slow selain landing page (misal: akademik/ERINE).
                if msg_id:
                    await update_progress(
                        chat_id, msg_id,
                        "⏳ Sedang memproses permintaan kamu... Mohon tunggu sebentar ya."
                    )
                else:
                    await send_telegram_message(
                        chat_id,
                        "⏳ Baik, permintaan kamu sedang diproses. Aku kabari setelah selesai ya."
                    )

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

            # Trigger self_monitor untuk intent berat yang diproses di background
            # (paritas perilaku dengan slow path sinkron sebelumnya).
            if not is_landing:
                try:
                    from src.self_monitor import self_monitor
                    asyncio.create_task(self_monitor(chat_id))
                except Exception as sm_err:
                    logger.warning("Failed to trigger self_monitor: %s", str(sm_err))

            # Bersihkan task, checkpoint & progress message_id setelah sukses penuh
            await clear_pending_task(chat_id)
            await clear_progress_message_id(chat_id)
            await clear_task_start(chat_id)
            if is_landing:
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

            # Increment retry count pada checkpoint per brief.md (khusus alur landing page)
            if is_landing and checkpoint:
                checkpoint["retry_count"] = checkpoint.get("retry_count", 0) + 1
                if checkpoint["retry_count"] >= checkpoint.get("max_retry", 3):
                    await delete_checkpoint(chat_id)
                    fail_text = f"❌ Task ini gagal setelah beberapa percobaan. Penyebab: {err_msg[:100]}. Mau dilanjutkan lagi?"
                else:
                    await save_checkpoint(chat_id, checkpoint)
                    fail_text = f"❌ Gagal di langkah {checkpoint.get('langkah_sekarang')}. Penyebab: {err_msg[:100]}. Mau coba lagi?"
            else:
                fail_text = f"❌ Gagal memproses task kamu. Penyebab: {err_msg[:100]}. Mau coba lagi?"

            if msg_id:
                edited = await update_progress(chat_id, msg_id, fail_text)
                if not edited:
                    await send_telegram_message(chat_id, fail_text)
            else:
                await send_telegram_message(chat_id, fail_text)

            await clear_pending_task(chat_id)
            await clear_progress_message_id(chat_id)
            await clear_task_start(chat_id)

            # Jalankan health check otomatis setelah slow path selesai (brief.md)
            try:
                from src.health_check import run_monitoring_and_notify
                asyncio.create_task(run_monitoring_and_notify(chat_id))
            except Exception as hc_err:
                logger.warning("Failed to trigger health monitoring: %s", str(hc_err))

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
    intent: Optional[str] = None,
) -> str:
    """
    Eksekusi alur pemanggilan model AI dengan urutan fallback optimal per jalur (brief.md):
    - 'fast' (Chat Ringan): Groq -> Mistral -> Cerebras -> OpenRouter -> Gemini
    - 'tools' (Tools Ringan): Mistral -> Gemini -> Cerebras -> OpenRouter -> Groq
    - 'landing' (Landing Page/Deploy): DeepSeek -> Mistral -> Gemini -> Cerebras -> OpenRouter

    Sebelum model menjawab, gate grounding memastikan data nyata (dari tool wajib intent)
    tersedia di konteks; jika tidak, Oline jujur menyatakan data tak tersedia / minta info.
    """
    if jalur == "fast":
        order = ["groq", "mistral", "cerebras", "openrouter", "gemini"]
    elif jalur == "tools":
        order = ["mistral", "gemini", "cerebras", "openrouter", "groq"]
    elif jalur == "landing":
        order = ["deepseek", "mistral", "gemini", "cerebras", "openrouter"]
    else:
        order = ["groq", "mistral", "gemini"]

    # --- Grounding wajib: ambil data nyata sebelum model menjawab (anti-halu) ---
    try:
        from src.grounding import (
            GROUNDED, NEED_INFO, SKIP, UNAVAILABLE,
            build_grounding_augment, prepare_grounding,
        )
        g_status, g_tool, g_result, g_msg = await prepare_grounding(chat_id, intent, user_message)
        if g_status == NEED_INFO and g_msg:
            return f"Biar aku kasih data yang akurat, aku butuh info dulu nih: {g_msg}"
        if g_status == UNAVAILABLE and g_msg:
            return (
                f"Hmm, aku belum bisa dapat data akuratnya nih ({g_msg}). "
                "Jadi aku nggak mau asal nebak. Coba lagi nanti ya."
            )
        if g_status == GROUNDED and g_tool:
            system_prompt = f"{system_prompt}\n{build_grounding_augment(g_tool, g_result)}"
    except Exception as g_err:
        logger.warning("Grounding prepare gagal (lanjut tanpa grounding): %s", str(g_err))

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
                from src.gemini import _build_tools, _format_history_for_gemini, _gemini_generate_with_tools
                gemini_tools = _build_tools(tools) if tools else None
                contents = _format_history_for_gemini(history)
                contents.append({"role": "user", "parts": [{"text": user_message}]})
                # Jalankan function-calling loop sungguhan agar hasil tool diumpankan
                # balik (bukan return langsung yang bisa halu karena tool tak dieksekusi).
                res, _ = await _gemini_generate_with_tools(
                    system_prompt=system_prompt,
                    contents=contents,
                    tools=gemini_tools,
                    jalur=jalur,
                    chat_id=chat_id,
                )
                if res and res.strip():
                    return res.strip()
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
