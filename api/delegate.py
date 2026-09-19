"""
Vercel Serverless Function – Endpoint untuk mendelegasikan task berat ke Render worker.
WSGI compliant handler.
"""

import asyncio
import json
import logging
import os
import sys

# Tambahkan root project ke path agar import src/ berfungsi
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.handlers import delegate_to_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DELEGATE_SECRET = os.environ.get("DELEGATE_SECRET", "").strip()


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
    WSGI Application handler untuk endpoint /api/delegate.
    Menerima POST {chat_id, perintah, intent, user_name, message_id}
    dan meneruskannya ke Render worker.
    """
    method = environ.get("REQUEST_METHOD", "GET").upper()

    if method != "POST":
        start_response("405 Method Not Allowed", [("Content-Type", "text/plain")])
        return [b"Method Not Allowed"]

    # Validasi secret header jika dikonfigurasi
    if DELEGATE_SECRET:
        provided = environ.get("HTTP_X_DELEGATE_SECRET", "")
        if provided != DELEGATE_SECRET:
            start_response("403 Forbidden", [("Content-Type", "text/plain")])
            return [b"Forbidden"]

    try:
        content_length = int(environ.get("CONTENT_LENGTH", 0) or 0)
        wsgi_input = environ.get("wsgi.input")
        body_bytes = wsgi_input.read(content_length) if wsgi_input and content_length > 0 else (wsgi_input.read() if wsgi_input else b"")
        payload = json.loads(body_bytes.decode("utf-8") or "{}")
    except Exception as e:
        logger.error("Gagal membaca payload delegate: %s", str(e))
        start_response("400 Bad Request", [("Content-Type", "application/json")])
        return [json.dumps({"status": "error", "message": "Bad Request"}).encode("utf-8")]

    chat_id = payload.get("chat_id")
    perintah = payload.get("perintah") or payload.get("message")
    intent = payload.get("intent", "preview")
    user_name = payload.get("user_name", "Teman")
    message_id = payload.get("message_id")

    if not chat_id or not perintah:
        start_response("400 Bad Request", [("Content-Type", "application/json")])
        return [json.dumps({"status": "error", "message": "chat_id & perintah wajib"}).encode("utf-8")]

    ok = run_async(delegate_to_worker(chat_id, perintah, intent, user_name, message_id))

    body = json.dumps({
        "status": "ok",
        "delegated": ok,
    }, ensure_ascii=False).encode("utf-8")
    start_response("200 OK", [("Content-Type", "application/json"), ("Cache-Control", "no-store")])
    return [body]
