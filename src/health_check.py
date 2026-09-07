"""
Modul Health Check & Health Monitoring System untuk Oline Bot.
Memeriksa kesehatan layanan internal & external (Vercel, Notion, Drive, Neo4j, Moondream, Search).
Menyediakan fitur otomatis circuit-breaker (disable fitur setelah 3x gagal berturut-turut + notifikasi).
"""

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

from src.kv import (
    get_failure_count,
    get_feature_status,
    increment_failure_count,
    log_error,
    reset_failure_count,
    set_feature_status,
)

logger = logging.getLogger(__name__)


async def check_vercel_api() -> bool:
    """Mengecek konektivitas dan kesehatan Vercel API."""
    token = (os.environ.get("VERCEL_API_TOKEN", "") or os.environ.get("VERCEL_TOKEN", "")).strip()
    if not token:
        logger.warning("HealthCheck Vercel: VERCEL_API_TOKEN / VERCEL_TOKEN tidak diset.")
        return False

    url = "https://api.vercel.com/v13/deployments"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            return resp.status_code in (200, 201)
    except Exception as e:
        logger.warning("HealthCheck Vercel error: %s", str(e))
        return False


async def check_moondream_space() -> bool:
    """Mengecek ketersediaan HuggingFace Moondream Space untuk Vision VLM."""
    try:
        space_name = os.environ.get("MOONDREAM_SPACE_1", "merve/moondream3")
        # Fast async ping to HuggingFace space endpoint
        url = f"https://huggingface.co/api/spaces/{space_name}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except Exception as e:
        logger.warning("HealthCheck Moondream error: %s", str(e))
        return False


async def check_notion_api() -> bool:
    """Mengecek konektivitas dan otentikasi ke Notion API."""
    token = os.environ.get("NOTION_API_KEY", "").strip()
    if not token:
        logger.warning("HealthCheck Notion: NOTION_API_KEY tidak diset.")
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
    }
    url = "https://api.notion.com/v1/users/me"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            return resp.status_code == 200
    except Exception as e:
        logger.warning("HealthCheck Notion error: %s", str(e))
        return False


async def check_drive_api() -> bool:
    """Mengecek kredensial OAuth / Service Account dan ketersediaan Google Drive API."""
    refresh_token = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN", "").strip()
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "").strip()
    creds_json = os.environ.get("GOOGLE_DRIVE_CREDENTIALS", "").strip()

    if not (refresh_token and client_id) and not creds_json:
        logger.warning("HealthCheck Drive: Google Drive OAuth / Credentials tidak diset.")
        return False

    try:
        from src.drive import get_drive_service
        service = await asyncio.to_thread(get_drive_service)
        return service is not None
    except Exception as e:
        logger.warning("HealthCheck Drive error: %s", str(e))
        return False


async def check_neo4j() -> bool:
    """Mengecek konektivitas database Neo4j AuraDB."""
    neo4j_uri = os.environ.get("NEO4J_URI", "").strip()
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j").strip()
    neo4j_pass = os.environ.get("NEO4J_PASSWORD", "").strip()

    if not neo4j_uri or not neo4j_pass:
        logger.warning("HealthCheck Neo4j: Env vars tidak lengkap.")
        return False

    try:
        from src.neo4j_client import _get_driver
        driver = await asyncio.to_thread(_get_driver)
        if not driver:
            return False
        # Test session verification
        def _verify():
            with driver.session() as session:
                res = session.run("RETURN 1 AS num")
                return res.single()["num"] == 1
        return await asyncio.to_thread(_verify)
    except Exception as e:
        logger.warning("HealthCheck Neo4j error: %s", str(e))
        return False


async def check_ddg() -> bool:
    """Mengecek ketersediaan pencarian internet (DuckDuckGo & Bing fallback)."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get("https://html.duckduckgo.com/html/?q=test")
            if resp.status_code == 200:
                return True
            resp_bing = await client.get("https://www.bing.com/search?q=test")
            return resp_bing.status_code == 200
    except Exception as e:
        logger.warning("HealthCheck Search error: %s", str(e))
        return False


async def check_calendar() -> bool:
    """Mengecek status ketersediaan fitur calendar (via Notion DB / Google Calendar)."""
    notion_ok = await check_notion_api()
    return notion_ok


async def check_all_features() -> dict[str, bool]:
    """
    Memeriksa kesehatan semua fitur Oline secara paralel.
    Returns: dict {"feature_name": bool_status}
    """
    checks = {
        "deploy": check_vercel_api(),
        "landing_page": check_vercel_api(),
        "vision": check_moondream_space(),
        "notion": check_notion_api(),
        "drive": check_drive_api(),
        "neo4j": check_neo4j(),
        "search": check_ddg(),
        "calendar": check_calendar(),
    }

    feature_names = list(checks.keys())
    results_list = await asyncio.gather(*checks.values(), return_exceptions=True)

    results: dict[str, bool] = {}
    for name, res in zip(feature_names, results_list):
        if isinstance(res, bool):
            results[name] = res
        else:
            results[name] = False

    return results


async def run_monitoring_and_notify(chat_id: Optional[int] = None) -> dict[str, Any]:
    """
    Jalankan health check untuk semua fitur, kelola failure counter,
    otomatis nonaktifkan fitur jika gagal 3x berturut-turut, dan kirimkan notifikasi ke chat Telegram.
    """
    results = await check_all_features()
    disabled_features = []
    status_summary = {}

    for feature, ok in results.items():
        status_summary[feature] = ok
        if ok:
            await reset_failure_count(feature)
        else:
            count = await increment_failure_count(feature)
            if count >= 3:
                # Cek apakah sebelumnya aktif
                currently_enabled = await get_feature_status(feature)
                if currently_enabled:
                    await set_feature_status(feature, False)
                    disabled_features.append((feature, count))
                    await log_error(f"Fitur '{feature}' dinonaktifkan otomatis setelah {count}x gagal berturut-turut.")

    # Kirim notifikasi jika ada fitur yang baru saja dinonaktifkan
    if disabled_features and chat_id:
        from src.handlers import send_telegram_message
        for feat, count in disabled_features:
            alert_text = f"⚠️ Fitur {feat} terdeteksi bermasalah (gagal {count}x) dan dinonaktifkan sementara."
            await send_telegram_message(chat_id, alert_text)

    return {
        "results": results,
        "disabled_count": len(disabled_features),
        "disabled_features": [f[0] for f in disabled_features],
    }
