"""
Helper untuk akses Vercel KV (Redis REST API) via httpx.
Menyediakan fungsi untuk memori percakapan dan penyimpanan jurnal harian.
"""

import base64
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional


import httpx

logger = logging.getLogger(__name__)

# Prefix keys
MEMORY_PREFIX = "memory"
JOURNAL_PREFIX = "jurnal"
HISTORY_PREFIX = "history"
RATE_PREFIX = "rate"
USAGE_PREFIX = "gemini_usage"
GROQ_USAGE_PREFIX = "groq_usage"
TTS_PREFIX = "tts_usage"
PENDING_FILE_PREFIX = "pending_file"
LOCATION_PREFIX = "location"
PENDING_TASK_PREFIX = "pending_task"
PROGRESS_MSG_PREFIX = "progres_msg_id"
ERROR_DIAGNOSIS_PREFIX = "last_error_diagnosis"
ERROR_SIGNATURE_PREFIX = "last_error_signature"
CHECKPOINT_PREFIX = "task_checkpoint"
AI_USAGE_PREFIX = "ai_usage"
FEATURE_FLAGS_KEY = "feature_flags"
FAILURE_COUNT_PREFIX = "failure_count"





def _get_kv_credentials() -> tuple[str, str]:
    """Mengambil credentials Upstash Redis / Vercel KV dari environment variables."""
    raw_url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL", "")
    raw_token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    url = raw_url.strip().rstrip("/")
    token = raw_token.strip()
    return url, token


async def _kv_request(command: list[str]) -> dict | None:
    """
    Mengirim command Redis ke Vercel KV / Upstash Redis REST API.
    Menggunakan pipeline endpoint untuk single command.
    """
    kv_url, kv_token = _get_kv_credentials()
    if not kv_url or not kv_token:
        logger.warning("Vercel KV / Upstash Redis credentials not configured. Skipping KV operation.")
        return None

    url = f"{kv_url}/pipeline"
    headers = {
        "Authorization": f"Bearer {kv_token}",
        "Content-Type": "application/json",
    }
    payload = [command]

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            # Pipeline returns array of results
            if isinstance(result, list) and len(result) > 0:
                return result[0]
            return result
    except Exception as e:
        logger.error("KV operation error: %s", str(e))
        return None


# --- Memory Functions ---

async def get_memory(chat_id: int) -> str:
    """Mengambil ringkasan memori percakapan untuk chat_id tertentu."""
    key = f"{MEMORY_PREFIX}:{chat_id}"
    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        return str(result["result"])
    return ""


async def save_memory(chat_id: int, memory_summary: str) -> bool:
    """
    Menyimpan ringkasan memori percakapan.
    Memori di-overwrite setiap kali diperbarui (ringkasan terbaru).
    TTL 30 hari (2592000 detik).
    """
    key = f"{MEMORY_PREFIX}:{chat_id}"
    result = await _kv_request(["SET", key, memory_summary, "EX", "2592000"])
    return result is not None


# --- Conversation History Functions ---

async def get_history(chat_id: int) -> list[dict]:
    """
    Mengambil riwayat percakapan terakhir (maks 15 pesan).
    Disimpan sebagai JSON string di KV.
    """
    key = f"{HISTORY_PREFIX}:{chat_id}"
    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        try:
            data = result["result"]
            if isinstance(data, str):
                return json.loads(data)
            return data
        except (json.JSONDecodeError, TypeError):
            return []
    return []


async def save_history(chat_id: int, history: list[dict]) -> bool:
    """
    Menyimpan riwayat percakapan (maks 15 pesan terakhir).
    TTL 24 jam (86400 detik).
    """
    # Potong hanya 15 pesan terakhir
    trimmed = history[-15:]
    key = f"{HISTORY_PREFIX}:{chat_id}"
    result = await _kv_request(
        ["SET", key, json.dumps(trimmed, ensure_ascii=False), "EX", "86400"]
    )
    return result is not None


# --- Journal Functions ---

async def save_journal(chat_id: int, text: str, date_str: Optional[str] = None) -> bool:
    """
    Menyimpan entri jurnal harian.
    Key format: jurnal:<chat_id>:YYYY-MM-DD
    Jika sudah ada entri di hari yang sama, teks di-append.
    TTL 90 hari (7776000 detik).
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    key = f"{JOURNAL_PREFIX}:{chat_id}:{date_str}"

    # Cek apakah sudah ada entri hari ini
    existing = await _kv_request(["GET", key])
    if existing and existing.get("result"):
        existing_text = str(existing["result"])
        text = existing_text + "\n---\n" + text

    result = await _kv_request(["SET", key, text, "EX", "7776000"])
    return result is not None


async def get_journal_entries(
    chat_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, str]:
    """
    Mengambil entri jurnal dalam rentang tanggal.
    Default: 7 hari terakhir.
    Returns dict {tanggal: teks_jurnal}.
    """
    if end_date is None:
        end_dt = datetime.now()
    else:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    if start_date is None:
        start_dt = end_dt - timedelta(days=6)
    else:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")

    entries: dict[str, str] = {}
    current = start_dt

    while current <= end_dt:
        date_key = current.strftime("%Y-%m-%d")
        key = f"{JOURNAL_PREFIX}:{chat_id}:{date_key}"
        result = await _kv_request(["GET", key])
        if result and result.get("result"):
            entries[date_key] = str(result["result"])
        current += timedelta(days=1)

    return entries


# --- Gemini Usage Tracking ---

async def save_usage(chat_id: int, tokens: int) -> bool:
    """
    Menambahkan jumlah token terpakai hari ini ke Vercel KV.
    Key format: gemini_usage:<chat_id>:YYYY-MM-DD
    Menggunakan INCRBY untuk operasi atomik.
    TTL 48 jam (172800 detik) agar key otomatis expired keesokan harinya.
    """
    if tokens <= 0:
        return True

    date_str = datetime.now().strftime("%Y-%m-%d")
    key = f"{USAGE_PREFIX}:{chat_id}:{date_str}"

    # INCRBY secara atomik menambahkan nilai (membuat key jika belum ada)
    result = await _kv_request(["INCRBY", key, str(tokens)])
    if result is not None:
        # Set TTL agar key auto-expire (tidak menumpuk selamanya)
        await _kv_request(["EXPIRE", key, "172800"])
        return True
    return False


async def get_today_usage(chat_id: int) -> int:
    """
    Mengambil total token yang terpakai hari ini untuk chat_id tertentu.
    Returns 0 jika belum ada data.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    key = f"{USAGE_PREFIX}:{chat_id}:{date_str}"

    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        try:
            return int(result["result"])
        except (ValueError, TypeError):
            return 0
    return 0


# --- Groq Usage Tracking ---

async def save_groq_usage(chat_id: int, tokens: int) -> bool:
    """
    Menambahkan jumlah token terpakai hari ini untuk Groq API ke Vercel KV.
    Key format: groq_usage:<chat_id>:YYYY-MM-DD
    TTL 48 jam (172800 detik).
    """
    if tokens <= 0:
        return True

    date_str = datetime.now().strftime("%Y-%m-%d")
    key = f"{GROQ_USAGE_PREFIX}:{chat_id}:{date_str}"

    result = await _kv_request(["INCRBY", key, str(tokens)])
    if result is not None:
        await _kv_request(["EXPIRE", key, "172800"])
        return True
    return False


async def get_today_groq_usage(chat_id: int) -> int:
    """
    Mengambil total token Groq yang terpakai hari ini untuk chat_id tertentu.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    key = f"{GROQ_USAGE_PREFIX}:{chat_id}:{date_str}"

    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        try:
            return int(result["result"])
        except (ValueError, TypeError):
            return 0
    return 0


# --- ElevenLabs TTS Usage Tracking ---


async def save_tts_usage(chat_id: int, char_count: int) -> bool:
    """
    Menambahkan jumlah karakter TTS terpakai bulan ini ke Vercel KV.
    Key format: tts_usage:<chat_id>:YYYY-MM
    TTL 60 hari (5184000 detik).
    """
    if char_count <= 0:
        return True

    month_str = datetime.now().strftime("%Y-%m")
    key = f"{TTS_PREFIX}:{chat_id}:{month_str}"

    result = await _kv_request(["INCRBY", key, str(char_count)])
    if result is not None:
        await _kv_request(["EXPIRE", key, "5184000"])
        return True
    return False


async def get_monthly_tts_usage(chat_id: int) -> int:
    """
    Mengambil total karakter TTS terpakai bulan ini untuk chat_id tertentu.
    """
    month_str = datetime.now().strftime("%Y-%m")
    key = f"{TTS_PREFIX}:{chat_id}:{month_str}"

    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        try:
            return int(result["result"])
        except (ValueError, TypeError):
            return 0
    return 0


async def _kv_pipeline(commands: list[list[str]]) -> list | None:
    """
    Mengirim multiple command Redis sekaligus (pipeline) ke Vercel KV / Upstash Redis REST API.
    """
    kv_url, kv_token = _get_kv_credentials()
    if not kv_url or not kv_token:
        logger.warning("Vercel KV / Upstash Redis credentials not configured. Skipping KV operation.")
        return None

    url = f"{kv_url}/pipeline"
    headers = {
        "Authorization": f"Bearer {kv_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(url, headers=headers, json=commands)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error("KV pipeline operation error: %s", str(e))
        return None


async def check_rate_limit(chat_id: int, max_requests: int = 25) -> bool:
    """
    Rate limiting: maks `max_requests` pesan per menit per chat_id.
    Returns True jika masih dalam batas, False jika melebihi.
    Pemeriksaan TTL dilakukan secara otomatis untuk mencegah key tersimpan permanen tanpa expiration.
    """
    key = f"{RATE_PREFIX}:{chat_id}"

    # Jalankan INCR dan TTL sekaligus dalam 1 request pipeline
    res = await _kv_pipeline([["INCR", key], ["TTL", key]])
    if not res or not isinstance(res, list) or len(res) < 2:
        return True

    try:
        current_count = int(res[0].get("result", 0)) if isinstance(res[0], dict) else 0
        ttl = int(res[1].get("result", -2)) if isinstance(res[1], dict) else -2
    except (ValueError, TypeError):
        return True

    # Jika key baru (count == 1) atau TTL = -1 (stuck tanpa expiration), set TTL 60 detik
    if current_count == 1 or ttl == -1:
        await _kv_request(["EXPIRE", key, "60"])

    if current_count > max_requests:
        if ttl <= 0:
            await _kv_request(["EXPIRE", key, "60"])
        return False

    return True



# --- Pending File Cache for Telegram Uploads ---

async def save_pending_file(
    chat_id: int, file_name: str, file_bytes: bytes, mime_type: str
) -> bool:
    """
    Menyimpan file sementara yang baru diunggah pengguna ke KV.
    TTL 10 menit (600 detik).
    """
    key = f"{PENDING_FILE_PREFIX}:{chat_id}"
    b64_str = base64.b64encode(file_bytes).decode("utf-8")
    payload = json.dumps({
        "file_name": file_name,
        "mime_type": mime_type,
        "file_bytes": b64_str,
    })
    result = await _kv_request(["SET", key, payload, "EX", "600"])
    return result is not None


async def get_pending_file(chat_id: int) -> Optional[dict]:
    """
    Mengambil file sementara pengguna dari KV.
    """
    key = f"{PENDING_FILE_PREFIX}:{chat_id}"
    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        try:
            data = json.loads(result["result"])
            data["file_bytes"] = base64.b64decode(data["file_bytes"])
            return data
        except Exception as e:
            logger.warning("Error reading pending file from KV: %s", str(e))
    return None


async def clear_pending_file(chat_id: int) -> bool:
    """
    Menghapus file sementara pengguna setelah diunggah ke Drive.
    """
    key = f"{PENDING_FILE_PREFIX}:{chat_id}"
    result = await _kv_request(["DEL", key])
    return result is not None


# --- Location Storage Functions ---

async def save_user_location(chat_id: int, lat: float, lon: float) -> bool:
    """
    Menyimpan lokasi (koordinat lat/lon) pengguna ke Vercel KV.
    Key format: location:<chat_id>
    TTL 30 hari (2592000 detik).
    """
    key = f"{LOCATION_PREFIX}:{chat_id}"
    payload = json.dumps({"lat": lat, "lon": lon})
    result = await _kv_request(["SET", key, payload, "EX", "2592000"])
    return result is not None


async def get_user_location(chat_id: int) -> Optional[dict[str, float]]:
    """
    Mengambil lokasi tersimpan pengguna dari Vercel KV.
    Returns dict {"lat": lat, "lon": lon} atau None jika belum tersimpan.
    """
    key = f"{LOCATION_PREFIX}:{chat_id}"
    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        try:
            data = json.loads(result["result"])
            if isinstance(data, dict) and "lat" in data and "lon" in data:
                return {"lat": float(data["lat"]), "lon": float(data["lon"])}
        except Exception as e:
            logger.warning("Error reading user location from KV: %s", str(e))
    return None


# --- Generic Cache & Error Log Functions ---

async def get_cache(key: str) -> Optional[str]:
    """
    Mengambil data cache dari Vercel KV berdasarkan key string.
    Returns str value jika ada, atau None jika tidak ditemukan / expired.
    """
    if not key:
        return None
    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        return str(result["result"])
    return None


async def set_cache(key: str, value: str, ttl_seconds: int = 600) -> bool:
    """
    Menyimpan nilai cache ke Vercel KV dengan batas waktu TTL (default 10 menit / 600s).
    """
    if not key or value is None:
        return False
    result = await _kv_request(["SET", key, str(value), "EX", str(ttl_seconds)])
    return result is not None


async def del_cache(key: str) -> bool:
    """
    Menghapus nilai cache dari Vercel KV.
    """
    if not key:
        return False
    result = await _kv_request(["DEL", key])
    return result is not None


async def log_error(error_message: str) -> bool:
    """
    Mencatat log kesalahan teknis ke Vercel KV pada key error_logs:YYYY-MM-DD.
    TTL 48 jam (172800 detik).
    """
    if not error_message:
        return False
    date_str = datetime.now().strftime("%Y-%m-%d")
    key = f"error_logs:{date_str}"
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {error_message}"

    existing = await _kv_request(["GET", key])
    if existing and existing.get("result"):
        entry = str(existing["result"]) + "\n" + entry

    result = await _kv_request(["SET", key, entry, "EX", "172800"])
    return result is not None


# --- Progress Message ID Functions ---

async def save_progress_message_id(chat_id: int, message_id: int) -> bool:
    """
    Menyimpan message_id pesan progres Telegram ke KV.
    TTL 1 jam (3600 detik).
    """
    key = f"{PROGRESS_MSG_PREFIX}:{chat_id}"
    result = await _kv_request(["SET", key, str(message_id), "EX", "3600"])
    return result is not None


async def get_progress_message_id(chat_id: int) -> Optional[int]:
    """
    Mengambil message_id pesan progres tersimpan dari KV.
    """
    key = f"{PROGRESS_MSG_PREFIX}:{chat_id}"
    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        try:
            return int(result["result"])
        except (ValueError, TypeError):
            return None
    return None


async def clear_progress_message_id(chat_id: int) -> bool:
    """
    Menghapus message_id pesan progres dari KV setelah task selesai.
    """
    key = f"{PROGRESS_MSG_PREFIX}:{chat_id}"
    result = await _kv_request(["DEL", key])
    return result is not None


# --- Pending Task Functions (Auto-Retry on Failure) ---

async def save_pending_task(
    chat_id: int,
    user_message: str,
    intent: Optional[str] = None,
    user_name: str = "Teman",
    error_reason: str = "",
    message_id: Optional[int] = None,
) -> bool:
    """
    Menyimpan perintah yang gagal/pending dieksekusi ke KV untuk dicoba ulang nanti.
    Hanya menyimpan 1 pending task per user (overwrite yang lama).
    TTL 1 jam (3600 detik).
    """
    key = f"{PENDING_TASK_PREFIX}:{chat_id}"
    safe_error = str(error_reason)[:200] if error_reason else ""
    payload = json.dumps({
        "perintah": user_message,
        "message": user_message,
        "intent": intent,
        "user_name": user_name,
        "error_reason": safe_error,
        "message_id": message_id,
        "waktu": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "retry_count": 0,
        "max_retry": 1,
    }, ensure_ascii=False)
    result = await _kv_request(["SET", key, payload, "EX", "3600"])
    if result is not None:
        logger.info("Pending task saved for chat_id %s: %s", chat_id, user_message[:80])
    return result is not None


async def get_pending_task(chat_id: int) -> Optional[dict]:
    """
    Mengambil pending task (perintah gagal) dari KV.
    Returns dict jika ada, None jika tidak ada / expired.
    """
    key = f"{PENDING_TASK_PREFIX}:{chat_id}"
    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        try:
            data = result["result"]
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data, dict) and ("message" in data or "perintah" in data):
                if "message" not in data and "perintah" in data:
                    data["message"] = data["perintah"]
                if "perintah" not in data and "message" in data:
                    data["perintah"] = data["message"]
                return data
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Error parsing pending task from KV: %s", str(e))
    return None


async def get_all_pending_tasks() -> list[dict]:
    """
    Mengambil semua pending task yang tersimpan di KV (key: pending_task:*).
    Returns list of dict pending tasks, tiap dict berisi chat_id.
    """
    pattern = f"{PENDING_TASK_PREFIX}:*"
    res = await _kv_request(["KEYS", pattern])
    if not res or not res.get("result"):
        return []

    keys = res["result"]
    if not isinstance(keys, list):
        return []

    tasks = []
    for key in keys:
        if not isinstance(key, str) or not key.startswith(f"{PENDING_TASK_PREFIX}:"):
            continue
        try:
            chat_id_str = key.split(":")[-1]
            chat_id = int(chat_id_str)
            task = await get_pending_task(chat_id)
            if task:
                task["chat_id"] = chat_id
                tasks.append(task)
        except (ValueError, TypeError):
            continue
    return tasks


async def clear_pending_task(chat_id: int) -> bool:
    """
    Menghapus pending task setelah berhasil dieksekusi ulang atau dikonfirmasi skip.
    """
    key = f"{PENDING_TASK_PREFIX}:{chat_id}"
    result = await _kv_request(["DEL", key])
    if result is not None:
        logger.info("Pending task cleared for chat_id %s", chat_id)
    return result is not None


async def update_pending_task_retry_count(chat_id: int, task: dict) -> bool:
    """
    Increment retry_count pada pending task yang ada.
    Maksimal auto-retry adalah 1x (max_retry: 1).
    """
    current_count = task.get("retry_count", 0)
    max_retry = task.get("max_retry", 1)
    if current_count >= max_retry:
        logger.info("Pending task reached max_retry (%d) for chat_id %s", max_retry, chat_id)
        return False

    key = f"{PENDING_TASK_PREFIX}:{chat_id}"
    task["retry_count"] = current_count + 1
    payload = json.dumps(task, ensure_ascii=False)
    result = await _kv_request(["SET", key, payload, "EX", "3600"])
    return result is not None


# --- Error Diagnosis & Self-Monitoring Functions ---

async def save_last_error_diagnosis(chat_id: int, diagnosis: dict) -> bool:
    """
    Menyimpan hasil diagnosis error Vercel log terbaru ke KV.
    TTL 24 jam (86400 detik).
    """
    key = f"{ERROR_DIAGNOSIS_PREFIX}:{chat_id}"
    payload = json.dumps(diagnosis, ensure_ascii=False)
    result = await _kv_request(["SET", key, payload, "EX", "86400"])
    if result is not None:
        logger.info("Saved last error diagnosis for chat_id %s", chat_id)
    return result is not None


async def get_last_error_diagnosis(chat_id: int) -> Optional[dict]:
    """
    Mengambil diagnosis error Vercel log tersimpan dari KV.
    """
    key = f"{ERROR_DIAGNOSIS_PREFIX}:{chat_id}"
    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        try:
            data = result["result"]
            if isinstance(data, str):
                return json.loads(data)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning("Error reading error diagnosis from KV: %s", str(e))
    return None


async def save_last_error_signature(chat_id: int, signature: str) -> bool:
    """
    Menyimpan MD5 hash signature log error terbaru ke KV.
    TTL 24 jam (86400 detik).
    """
    key = f"{ERROR_SIGNATURE_PREFIX}:{chat_id}"
    result = await _kv_request(["SET", key, signature, "EX", "86400"])
    return result is not None


async def get_last_error_signature(chat_id: int) -> Optional[str]:
    """
    Mengambil MD5 hash signature log error tersimpan dari KV.
    """
    key = f"{ERROR_SIGNATURE_PREFIX}:{chat_id}"
    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        return str(result["result"])
    return None


# --- Task Checkpoint Functions ---

async def save_checkpoint(chat_id: int, checkpoint: dict) -> bool:
    """
    Menyimpan checkpoint task ke KV.
    TTL 2 jam (7200 detik).
    """
    key = f"{CHECKPOINT_PREFIX}:{chat_id}"
    payload = json.dumps(checkpoint, ensure_ascii=False)
    result = await _kv_request(["SET", key, payload, "EX", "7200"])
    if result is not None:
        logger.info("Saved task checkpoint for chat_id %s (step: %s)", chat_id, checkpoint.get("langkah_sekarang"))
    return result is not None


async def get_checkpoint(chat_id: int) -> Optional[dict]:
    """
    Mengambil checkpoint task tersimpan dari KV.
    """
    key = f"{CHECKPOINT_PREFIX}:{chat_id}"
    result = await _kv_request(["GET", key])
    if result and result.get("result"):
        try:
            data = result["result"]
            if isinstance(data, str):
                return json.loads(data)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning("Error reading task checkpoint from KV: %s", str(e))
    return None


async def delete_checkpoint(chat_id: int) -> bool:
    """
    Menghapus checkpoint task dari KV setelah selesai atau dibatalkan.
    """
    key = f"{CHECKPOINT_PREFIX}:{chat_id}"
    result = await _kv_request(["DEL", key])
    if result is not None:
        logger.info("Deleted task checkpoint for chat_id %s", chat_id)
    return result is not None


# --- Multi-Provider AI Usage Tracking (brief.md) ---

async def increment_usage(provider: str, usage: dict) -> bool:
    """
    Menambahkan jumlah request & token terpakai hari ini (zona waktu WIB) ke Vercel KV.
    Key format: ai_usage:<provider>:YYYY-MM-DD
    TTL 24 jam (86400 detik).
    """
    if not provider:
        return False

    wib = timezone(timedelta(hours=7))
    today = datetime.now(wib).strftime("%Y-%m-%d")
    key = f"{AI_USAGE_PREFIX}:{provider}:{today}"

    req_add = usage.get("request", 1)
    tok_add = usage.get("token", 0)

    existing = await _kv_request(["GET", key])
    current = {"request": 0, "token": 0}
    if existing and existing.get("result"):
        try:
            data = existing["result"]
            if isinstance(data, str):
                current = json.loads(data)
            elif isinstance(data, dict):
                current = data
        except Exception:
            current = {"request": 0, "token": 0}

    current["request"] = current.get("request", 0) + req_add
    current["token"] = current.get("token", 0) + tok_add

    res = await _kv_request(["SET", key, json.dumps(current, ensure_ascii=False), "EX", "86400"])
    return res is not None


async def get_all_ai_usage() -> dict[str, dict[str, int]]:
    """
    Mengambil data pemakaian AI hari ini (WIB) untuk semua provider:
    openrouter, groq, gemini, deepinfra, mistral, cerebras.
    """
    wib = timezone(timedelta(hours=7))
    today = datetime.now(wib).strftime("%Y-%m-%d")
    providers = ["openrouter", "groq", "gemini", "deepinfra", "mistral", "cerebras"]

    result = {}
    for provider in providers:
        key = f"{AI_USAGE_PREFIX}:{provider}:{today}"
        kv_res = await _kv_request(["GET", key])
        if kv_res and kv_res.get("result"):
            try:
                data = kv_res["result"]
                if isinstance(data, str):
                    result[provider] = json.loads(data)
                elif isinstance(data, dict):
                    result[provider] = data
                else:
                    result[provider] = {"request": 0, "token": 0}
            except Exception:
                result[provider] = {"request": 0, "token": 0}
        else:
            result[provider] = {"request": 0, "token": 0}

    return result


# --- Feature Flag & Failure Tracking (brief.md) ---

DEFAULT_FEATURE_FLAGS = {
    "landing_page": True,
    "vision": True,
    "notion": True,
    "drive": True,
    "neo4j": True,
    "calendar": True,
    "search": True,
    "deploy": True,
}


async def get_all_feature_flags() -> dict[str, bool]:
    """Mengambil dict status semua feature flags dari Vercel KV."""
    res = await _kv_request(["GET", FEATURE_FLAGS_KEY])
    flags = dict(DEFAULT_FEATURE_FLAGS)
    if res and res.get("result"):
        try:
            data = res["result"]
            if isinstance(data, str):
                parsed = json.loads(data)
            elif isinstance(data, dict):
                parsed = data
            else:
                parsed = {}
            if isinstance(parsed, dict):
                flags.update(parsed)
        except Exception as e:
            logger.warning("Error parsing feature_flags from KV: %s", str(e))
    return flags


async def get_feature_status(feature: str) -> bool:
    """Mengambil status aktif/nonaktif fitur tertentu dari Vercel KV."""
    flags = await get_all_feature_flags()
    return flags.get(feature, False)


async def set_feature_status(feature: str, status: bool) -> bool:
    """Menyimpan status aktif/nonaktif fitur tertentu ke Vercel KV."""
    flags = await get_all_feature_flags()
    flags[feature] = status
    payload = json.dumps(flags, ensure_ascii=False)
    res = await _kv_request(["SET", FEATURE_FLAGS_KEY, payload])
    return res is not None


async def is_feature_enabled(feature: str) -> bool:
    """Memeriksa apakah fitur diizinkan/aktif."""
    status = await get_feature_status(feature)
    return status is True


async def increment_failure_count(feature: str) -> int:
    """Increment jumlah kegagalan fitur berturut-turut di KV. TTL 24 jam (86400s)."""
    key = f"{FAILURE_COUNT_PREFIX}:{feature}"
    res = await _kv_request(["INCR", key])
    if res and res.get("result") is not None:
        try:
            count = int(res["result"])
            await _kv_request(["EXPIRE", key, "86400"])
            return count
        except (ValueError, TypeError):
            pass
    return 1


async def get_failure_count(feature: str) -> int:
    """Mengambil jumlah kegagalan fitur tersimpan dari KV."""
    key = f"{FAILURE_COUNT_PREFIX}:{feature}"
    res = await _kv_request(["GET", key])
    if res and res.get("result"):
        try:
            return int(res["result"])
        except (ValueError, TypeError):
            return 0
    return 0


async def reset_failure_count(feature: str) -> bool:
    """Mereset jumlah kegagalan fitur dari KV ketika health check berhasil."""
    key = f"{FAILURE_COUNT_PREFIX}:{feature}"
    res = await _kv_request(["DEL", key])
    return res is not None


async def toggle_feature(feature: str, status: bool) -> bool:
    """Mengubah status aktif/nonaktif fitur di Vercel KV (brief.md)."""
    return await set_feature_status(feature, status)


async def is_feature_active(feature: str) -> bool:
    """Memeriksa apakah fitur aktif di Vercel KV (default True jika belum di-set)."""
    flags = await get_all_feature_flags()
    return flags.get(feature, True)

