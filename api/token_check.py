"""
Vercel Serverless Function – Endpoint /api/token_check.
Memeriksa status semua token di TOKEN_REGISTRY dan mengembalikan JSON.
Autentikasi via query param `key`. Eksekusi langsung (tanpa AI).
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

# Tambahkan root project ke path agar import src/ berfungsi
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def _parse_qs(query_string: str) -> dict[str, str]:
    """Parse query string sederhana (key=value&...)."""
    params: dict[str, str] = {}
    if not query_string:
        return params
    for pair in query_string.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = v
    return params


def app(environ, start_response):
    """
    WSGI Application untuk endpoint /api/token_check.
    Metode: GET. Auth: ?key=<TOKEN_CHECK_SECRET>.
    """
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "GET":
        start_response("405 Method Not Allowed", [("Content-Type", "application/json")])
        return [b'{"status":"error","message":"Method Not Allowed"}']

    query = environ.get("QUERY_STRING", "")
    params = _parse_qs(query)
    provided_key = params.get("key", "")

    expected_secret = (
        os.environ.get("TOKEN_CHECK_SECRET", "")
        or os.environ.get("PROCESS_PENDING_SECRET", "")
    ).strip()

    if not expected_secret:
        body = json.dumps({"status": "error", "message": "TOKEN_CHECK_SECRET belum dikonfigurasi."}).encode("utf-8")
        start_response("503 Service Unavailable", [("Content-Type", "application/json")])
        return [body]

    if not provided_key or provided_key != expected_secret:
        body = json.dumps({"status": "error", "message": "Unauthorized"}).encode("utf-8")
        start_response("403 Forbidden", [("Content-Type", "application/json")])
        return [body]

    try:
        from src.tools import check_token_status_report
        results = run_async(check_token_status_report())

        summary = {"valid": 0, "invalid": 0, "unconfigured": 0, "warning": 0, "unavailable": 0}
        for r in results:
            st = r.get("status", "warning")
            summary[st] = summary.get(st, 0) + 1

        payload = {
            "status": "ok",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "results": results,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Cache-Control", "no-store"),
        ])
        return [body]
    except Exception as e:
        logger.error("Error in /api/token_check: %s", str(e), exc_info=True)
        body = json.dumps({"status": "error", "error": str(e)}).encode("utf-8")
        start_response("500 Internal Server Error", [("Content-Type", "application/json")])
        return [body]
