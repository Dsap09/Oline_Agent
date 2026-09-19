"""
Vercel Serverless Function – Entrypoint webhook untuk Oline Telegram Bot.
WSGI compliant handler.
"""

import asyncio
import json
import logging
import os
import sys

# Tambahkan root project ke path agar import src/ berfungsi
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Update
from src.bot import create_application

# Konfigurasi logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


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


async def _process_update(update_data: dict) -> None:
    """Proses Telegram update melalui bot application."""
    # Deduplikasi update_id: cegah respons ganda saat Telegram re-deliver update yang sama
    update_id = update_data.get("update_id")
    if update_id:
        try:
            from src.kv import is_duplicate_update
            if await is_duplicate_update(update_id):
                logger.info("Update %s sudah diproses (duplicate), dilewati.", update_id)
                return
        except Exception as dedup_err:
            logger.warning("Gagal cek duplicate update %s: %s", update_id, str(dedup_err))

    app = create_application()
    async with app:
        update = Update.de_json(update_data, app.bot)
        if update:
            await app.process_update(update)


def app(environ, start_response):
    """
    WSGI Application handler untuk Vercel Serverless.
    """
    method = environ.get("REQUEST_METHOD", "GET").upper()

    if method == "GET":
        status = "200 OK"
        response_headers = [("Content-Type", "application/json")]
        start_response(status, response_headers)
        body = json.dumps({
            "status": "ok",
            "bot": "Oline",
            "message": "Oline is running! 🤖",
        }).encode("utf-8")
        return [body]

    if method == "POST":
        try:
            # Validasi secret header jika dikonfigurasi
            if WEBHOOK_SECRET:
                secret_header = environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
                if secret_header != WEBHOOK_SECRET:
                    status = "403 Forbidden"
                    start_response(status, [("Content-Type", "text/plain")])
                    return [b"Forbidden"]

            # Baca request body dari WSGI input stream
            try:
                content_length = int(environ.get("CONTENT_LENGTH", 0))
            except (ValueError, TypeError):
                content_length = 0

            wsgi_input = environ.get("wsgi.input")
            if wsgi_input and content_length > 0:
                body_bytes = wsgi_input.read(content_length)
            elif wsgi_input:
                body_bytes = wsgi_input.read()
            else:
                body_bytes = b""

            if body_bytes:
                update_data = json.loads(body_bytes.decode("utf-8"))
                logger.info("Processing Telegram Update: %s", update_data.get("update_id"))
                run_async(_process_update(update_data))

        except Exception as e:
            logger.error("Error handling POST update: %s", str(e), exc_info=True)

        # Selalu kembalikan 200 OK ke Telegram
        status = "200 OK"
        response_headers = [("Content-Type", "application/json")]
        start_response(status, response_headers)
        return [b'{"ok": true}']

    # Default fallback
    status = "200 OK"
    start_response(status, [("Content-Type", "application/json")])
    return [b'{"ok": true}']
