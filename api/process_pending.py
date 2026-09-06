"""
Vercel Serverless Function – Endpoint untuk memproses pending tasks Oline secara background.
WSGI compliant handler.
"""

import asyncio
import json
import logging
import os
import sys

# Tambahkan root project ke path agar import src/ berfungsi
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.handlers import process_pending_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


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
    WSGI Application handler untuk endpoint /api/process_pending.
    Mendukung HTTP GET & POST.
    """
    method = environ.get("REQUEST_METHOD", "GET").upper()

    if method not in ("GET", "POST"):
        status = "405 Method Not Allowed"
        start_response(status, [("Content-Type", "text/plain")])
        return [b"Method Not Allowed"]

    # Validasi secret header jika dikonfigurasi
    expected_secret = os.environ.get("PROCESS_PENDING_SECRET", "").strip()
    if expected_secret:
        provided_secret = (
            environ.get("HTTP_X_PROCESS_PENDING_SECRET", "")
            or environ.get("HTTP_X_KEEPALIVE_SECRET", "")
        )
        if provided_secret != expected_secret:
            status = "403 Forbidden"
            start_response(status, [("Content-Type", "text/plain")])
            return [b"Forbidden"]

    try:
        logger.info("Triggering background pending task processor from /api/process_pending...")
        result = run_async(process_pending_task())

        status = "200 OK"
        response_headers = [
            ("Content-Type", "application/json"),
            ("Cache-Control", "no-store"),
        ]
        start_response(status, response_headers)

        body = json.dumps({
            "status": "ok",
            "bot": "Oline",
            "endpoint": "process_pending",
            "result": result,
        }, ensure_ascii=False).encode("utf-8")
        return [body]

    except Exception as e:
        logger.error("Error executing /api/process_pending: %s", str(e), exc_info=True)
        status = "500 Internal Server Error"
        start_response(status, [("Content-Type", "application/json")])
        body = json.dumps({
            "status": "error",
            "error": str(e),
        }).encode("utf-8")
        return [body]
