"""
Modul untuk mengambil dan menganalisis log Vercel (Runtime Logs API) dengan filter cerdas & caching KV.
"""

import json
import logging
import os
from typing import Optional

import httpx

from src.kv import get_cache, set_cache

logger = logging.getLogger(__name__)

VERCEL_API_TOKEN = os.environ.get("VERCEL_API_TOKEN", "") or os.environ.get("VERCEL_TOKEN", "")
VERCEL_LOGS_URL = "https://api.vercel.com/v4/runtime-logs"


async def fetch_vercel_logs(limit: int = 10, level: Optional[str] = None) -> str:
    """
    Mengambil runtime log dari Vercel API berdasarkan limit dan level (misal: error, warn).
    """
    token = os.environ.get("VERCEL_API_TOKEN", "") or os.environ.get("VERCEL_TOKEN", "")
    if not token:
        logger.warning("VERCEL_API_TOKEN environment variable is not set.")
        return "Token Vercel belum dikonfigurasi."

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    params = {"limit": limit}
    if level:
        params["level"] = level

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(VERCEL_LOGS_URL, headers=headers, params=params)
            if resp.status_code == 200:
                data = resp.json()
                logs = data.get("logs", [])
                if not logs:
                    return "Tidak ada log ditemukan."
                lines = []
                for log in logs:
                    log_level = log.get("level", "info")
                    raw_msg = log.get("message", "") or log.get("text", "") or str(log)
                    message = raw_msg[:250].replace("\n", " ")
                    lines.append(f"[{log_level}] {message}")
                return "\n".join(lines)
            else:
                return f"Gagal ambil log: HTTP {resp.status_code}"
    except Exception as e:
        logger.error("Error fetching Vercel logs: %s", str(e))
        return f"Error: {str(e)}"


async def read_vercel_logs(mode: str = "error") -> str:
    """
    Membaca dan menganalisis log Vercel terbaru.
    
    Args:
        mode: 'error' untuk filter log error & warn saja, 'semua' untuk 10 log terbaru semua level.
        
    Returns:
        Teks analisis log dari Vercel / penjelasan sederhana.
    """
    cache_key = f"log_cache:{mode}"
    try:
        cached = await get_cache(cache_key)
        if cached:
            logger.info("Returning cached Vercel log analysis for mode '%s'", mode)
            return cached
    except Exception as cache_err:
        logger.warning("Failed to check log_cache in KV: %s", str(cache_err))

    # Ambil log sesuai mode
    if mode == "error":
        logs = await fetch_vercel_logs(limit=10, level="error")
        warn_logs = await fetch_vercel_logs(limit=10, level="warn")
        if not logs.startswith("Token") and not logs.startswith("Gagal") and not logs.startswith("Error"):
            if not warn_logs.startswith("Token") and not warn_logs.startswith("Gagal") and not warn_logs.startswith("Error") and "Tidak ada log" not in warn_logs:
                logs += "\n" + warn_logs
    else:
        logs = await fetch_vercel_logs(limit=10)

    # Jika terjadi error konfigurasi/koneksi atau log kosong, langsung kembalikan
    if (
        logs.startswith("Token")
        or logs.startswith("Gagal")
        or logs.startswith("Error")
        or logs == "Tidak ada log ditemukan."
    ):
        return logs

    # Susun penjelasan sederhana & informatif
    prompt = (
        f"Berikut adalah runtime log Vercel terbaru (mode: {mode}):\n"
        f"{logs}\n\n"
        f"Tolong jelaskan secara singkat dan profesional:\n"
        f"1. Error atau peristiwa apa yang terjadi?\n"
        f"2. Apa dampaknya?\n"
        f"3. Apa saran perbaikan jika ada masalah?"
    )

    try:
        from src.gemini import chat_with_oline
        explanation = await chat_with_oline(
            chat_id=0,
            user_message=prompt,
            user_name="System",
            use_gemini_only=True,
        )
        result = explanation if explanation else f"Log Vercel terbaru:\n{logs}"
    except Exception as ai_err:
        logger.warning("Error generating AI explanation for Vercel logs: %s", str(ai_err))
        result = f"Log Vercel terbaru ({mode}):\n{logs}"

    # Simpan hasil ke cache Vercel KV selama 120 detik (2 menit)
    try:
        await set_cache(cache_key, result, ttl_seconds=120)
    except Exception as set_cache_err:
        logger.warning("Failed to save log_cache to KV: %s", str(set_cache_err))

    return result
