"""
Vercel Serverless Function – Endpoint untuk menerima hasil/callback dari Render worker.
WSGI compliant handler.
"""

import asyncio
import json
import logging
import os
import sys

# Tambahkan root project ke path agar import src/ berfungsi
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kv import clear_pending_task, clear_progress_message_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

WORKER_KEY = os.environ.get("OLINE_WORKER_KEY", "").strip()


def run_async(coro):
    """Run async coroutine safely on Vercel Serverless."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        except Exception:
            pass


def app(environ, start_response):
    """
    WSGI Application handler untuk endpoint /api/callback.
    Menerima POST dari worker Render berisi {chat_id, status, message}.
    Validasi X-Worker-Key, lalu bersihkan pending task di KV.
    """
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        start_response("405 Method Not Allowed", [("Content-Type", "text/plain")])
        return [b"Method Not Allowed"]

    # Validasi shared key dari worker
    provided_key = environ.get("HTTP_X_WORKER_KEY", "")
    if WORKER_KEY and provided_key != WORKER_KEY:
        start_response("403 Forbidden", [("Content-Type", "text/plain")])
        return [b"Forbidden"]

    try:
        content_length = int(environ.get("CONTENT_LENGTH", 0) or 0)
        wsgi_input = environ.get("wsgi.input")
        body_bytes = wsgi_input.read(content_length) if wsgi_input and content_length > 0 else (wsgi_input.read() if wsgi_input else b"")
        payload = json.loads(body_bytes.decode("utf-8") or "{}")
    except Exception as e:
        logger.error("Gagal membaca payload callback: %s", str(e))
        start_response("400 Bad Request", [("Content-Type", "application/json")])
        return [json.dumps({"status": "error", "message": "Bad Request"}).encode("utf-8")]

    chat_id = payload.get("chat_id")
    status = payload.get("status", "success")
    message = payload.get("message", "")

    if chat_id:
        run_async(clear_pending_task(chat_id))
        run_async(clear_progress_message_id(chat_id))
        logger.info("Callback dari worker: chat=%s status=%s msg=%s", chat_id, status, message[:80])

    body = json.dumps({"status": "ok", "received": True}, ensure_ascii=False).encode("utf-8")
    start_response("200 OK", [("Content-Type", "application/json"), ("Cache-Control", "no-store")])
    return [body]
