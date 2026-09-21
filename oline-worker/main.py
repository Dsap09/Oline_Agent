"""
Oline Worker — layanan Render untuk task berat (pilot: landing page generator).

Worker menerima task dari Vercel (orchestrator) via POST /process, menjalankan
pipeline generasi landing page TANPA batas timeout (Render bukan serverless),
mengirimkan progres & hasil akhir langsung ke Telegram, lalu membersihkan
pending task di Vercel KV.

Worker di-deploy sebagai Web Service Render dengan root directory = oline-worker/.
Karena Render men-clone seluruh repo, worker mengimpor ulang modul src/ (gemini,
handlers, tools, personas, kv) lewat sys.path menuju root repo.
"""

import asyncio
import json
import logging
import os
import sys

# Repo root agar bisa import src/
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import httpx  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("oline-worker")

app = FastAPI(title="Oline Worker")

WORKER_KEY = os.environ.get("OLINE_WORKER_KEY", "").strip()
VERCEL_CALLBACK_URL = os.environ.get("VERCEL_CALLBACK_URL", "").strip()


def _auth_ok(auth_header: str) -> bool:
    if not WORKER_KEY:
        return False
    return (auth_header or "").strip() == WORKER_KEY


async def _get_msg_id(payload: dict, chat_id: int) -> int | None:
    msg_id = payload.get("message_id")
    if msg_id:
        try:
            return int(msg_id)
        except (TypeError, ValueError):
            pass
    # Fallback: baca dari KV (progres message disimpan oleh Vercel)
    try:
        from src.kv import get_progress_message_id
        return await get_progress_message_id(chat_id)
    except Exception as e:
        logger.warning("Gagal membaca progress message_id dari KV: %s", str(e))
    return None


async def _notify_vercel_callback(chat_id: int, status: str, message: str) -> None:
    """Best-effort: beri tahu Vercel bahwa task selesai (untuk monitoring/cleanup)."""
    if not VERCEL_CALLBACK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                VERCEL_CALLBACK_URL,
                json={"chat_id": chat_id, "status": status, "message": message[:500]},
                headers={"X-Worker-Key": WORKER_KEY},
            )
    except Exception as e:
        logger.warning("Callback ke Vercel gagal (non-critical): %s", str(e))


async def _run_task(chat_id: int, perintah: str, intent: str, user_name: str, msg_id: int | None) -> None:
    """Menjalankan pipeline task berat (landing page) dan mengirim hasil ke Telegram."""
    from src.handlers import (
        clear_pending_task,
        clear_progress_message_id,
        delete_checkpoint,
        get_checkpoint,
        save_checkpoint,
        send_telegram_message,
        update_progress,
    )
    from src.gemini import chat_with_oline

    is_landing = intent in ("preview", "deploy", "design_reference")

    try:
        # Inisialisasi/ambil checkpoint (khusus landing) untuk fallback
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
                    "waktu_terakhir": "",
                }
                await save_checkpoint(chat_id, checkpoint)

        if is_landing:
            if msg_id:
                await update_progress(chat_id, msg_id, "⏳ [1/4] Menyusun struktur HTML... Sisa 25 detik.")
                await update_progress(chat_id, msg_id, "⏳ [2/4] Membuat CSS & Tampilan Wabi-Sabi... Sisa 15 detik.")
                await update_progress(chat_id, msg_id, "⏳ [3/4] Menambahkan efek Canvas & menyiapkan preview... Sisa 8 detik.")
            response_text = await chat_with_oline(
                chat_id=chat_id,
                user_message=perintah,
                user_name=user_name,
                intent=intent,
                is_retry=True,
            )
            if checkpoint and "preview" not in checkpoint.get("langkah_selesai", []):
                checkpoint["langkah_selesai"].append("generate_html")
                checkpoint["langkah_selesai"].append("generate_css")
                checkpoint["langkah_selesai"].append("preview")
                checkpoint["langkah_sekarang"] = "selesai"
                await save_checkpoint(chat_id, checkpoint)
        else:
            if msg_id:
                await update_progress(chat_id, msg_id, "⏳ Sedang memproses permintaan kamu... Mohon tunggu sebentar ya.")
            else:
                await send_telegram_message(chat_id, "⏳ Baik, permintaan kamu sedang diproses. Aku kabari setelah selesai ya.")
            response_text = await chat_with_oline(
                chat_id=chat_id,
                user_message=perintah,
                user_name=user_name,
                intent=intent,
                is_retry=True,
            )

        # Kirim hasil final
        ok = False
        if msg_id:
            final_text = f"✅ Selesai!\n\n{response_text}"
            if len(final_text) <= 4096:
                ok = await update_progress(chat_id, msg_id, final_text)
            else:
                await update_progress(chat_id, msg_id, "✅ Selesai! Hasil preview/deploy dikirimkan di bawah ini:")
                ok = await send_telegram_message(chat_id, response_text)
        else:
            ok = await send_telegram_message(chat_id, response_text)

        await clear_pending_task(chat_id)
        await clear_progress_message_id(chat_id)
        if is_landing:
            await delete_checkpoint(chat_id)

        logger.info("[worker] Task selesai chat=%s ok=%s", chat_id, ok)
        await _notify_vercel_callback(chat_id, "success", str(response_text)[:500])

    except Exception as e:
        logger.error("[worker] Task gagal chat=%s: %s", chat_id, str(e), exc_info=True)
        try:
            fail_text = f"❌ Gagal memproses task. Penyebab: {str(e)[:100]}"
            # Edit pesan progres yang sama agar tidak ada bubble sisa yang membingungkan.
            if msg_id:
                edited = await update_progress(chat_id, msg_id, fail_text)
                if not edited:
                    await send_telegram_message(chat_id, fail_text)
            else:
                await send_telegram_message(chat_id, fail_text)
        except Exception as send_err:
            logger.warning("Gagal kirim pesan error: %s", str(send_err))
        try:
            await clear_pending_task(chat_id)
            await clear_progress_message_id(chat_id)
        except Exception as clean_err:
            logger.warning("Gagal cleanup pending task: %s", str(clean_err))
        await _notify_vercel_callback(chat_id, "error", str(e)[:500])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "oline-worker"}


@app.post("/process")
async def process(request: Request):
    auth = request.headers.get("X-Worker-Key", "")
    if not _auth_ok(auth):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)

    chat_id = payload.get("chat_id")
    perintah = payload.get("perintah") or payload.get("message")
    intent = payload.get("intent", "preview")
    user_name = payload.get("user_name", "Teman")

    if not chat_id or not perintah:
        return JSONResponse({"status": "error", "message": "chat_id & perintah wajib"}, status_code=400)

    msg_id = await _get_msg_id(payload, chat_id)

    logger.info("[worker] Menerima task chat=%s intent=%s msg_id=%s", chat_id, intent, msg_id)

    # Jalankan di background; balas 202 cepat (Render menahan proses hidup sampai selesai)
    asyncio.create_task(_run_task(chat_id, perintah, intent, user_name, msg_id))

    return JSONResponse({"status": "accepted", "message": "Task diterima, diproses di background."}, status_code=202)
