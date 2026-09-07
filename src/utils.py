"""
Utility functions untuk Oline bot.
Berisi parser tanggal Indonesia dan helper umum.
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


def get_current_time_context() -> str:
    """
    Mengembalikan konteks waktu saat ini dalam WIB (UTC+7)
    dengan format hari, tanggal, bulan, tahun, dan jam.
    Contoh: "Sekarang adalah hari Kamis, 27 Agustus 2026, pukul 19:20 WIB."
    """
    wib = timezone(timedelta(hours=7))
    now = datetime.now(wib)
    hari_names = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    bulan_names = [
        "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember",
    ]
    nama_hari = hari_names[now.weekday()]
    nama_bulan = bulan_names[now.month]
    return (
        f"Sekarang adalah hari {nama_hari}, "
        f"{now.day} {nama_bulan} {now.year}, pukul {now.strftime('%H:%M')} WIB."
    )


def parse_relative_date(text: str) -> Optional[str]:
    """
    Parse referensi tanggal relatif dalam bahasa Indonesia.
    Returns tanggal dalam format YYYY-MM-DD, atau None jika tidak ditemukan.
    """
    text_lower = text.lower().strip()
    today = datetime.now()

    # Hari ini
    if any(word in text_lower for word in ["hari ini", "sekarang", "saat ini"]):
        return today.strftime("%Y-%m-%d")

    # Kemarin
    if "kemarin" in text_lower or "kemaren" in text_lower:
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")

    # Besok
    if "besok" in text_lower or "besuk" in text_lower:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # Lusa
    if "lusa" in text_lower:
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")

    # Mapping hari Indonesia ke index (Senin=0, Minggu=6)
    hari_map = {
        "senin": 0, "selasa": 1, "rabu": 2, "kamis": 3,
        "jumat": 4, "sabtu": 5, "minggu": 6,
    }

    for hari, target_day in hari_map.items():
        if hari in text_lower:
            current_day = today.weekday()
            days_ahead = target_day - current_day
            if days_ahead <= 0:
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # Tanggal eksplisit: "tanggal 3 Agustus", "3 agustus 2026", dll
    bulan_map = {
        "januari": 1, "februari": 2, "maret": 3, "april": 4,
        "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
        "september": 9, "oktober": 10, "november": 11, "desember": 12,
    }

    # Pattern: tanggal <angka> <bulan> [tahun]
    date_pattern = re.compile(
        r"(?:tanggal\s+)?(\d{1,2})\s+("
        + "|".join(bulan_map.keys())
        + r")(?:\s+(\d{4}))?",
        re.IGNORECASE,
    )
    match = date_pattern.search(text_lower)
    if match:
        day = int(match.group(1))
        month = bulan_map.get(match.group(2).lower(), 1)
        year = int(match.group(3)) if match.group(3) else today.year
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def format_date_indonesian(date_str: str) -> str:
    """
    Format tanggal YYYY-MM-DD ke format Indonesia readable.
    Contoh: "2026-07-29" -> "29 Juli 2026"
    """
    bulan_names = [
        "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember",
    ]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.day} {bulan_names[dt.month]} {dt.year}"
    except ValueError:
        return date_str


def truncate_text(text: str, max_length: int = 500) -> str:
    """Truncate text dengan ellipsis jika terlalu panjang."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


async def notify_process(
    chat_id: int,
    action: Optional[str] = "typing",
    message: Optional[str] = None,
    context: Optional[Any] = None,
) -> None:
    """
    Mengirimkan chat action (typing, upload_photo, dll.)
    dan/atau pesan status proses ke pengguna Telegram.
    """
    if not chat_id or chat_id == 0:
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    try:
        bot = None
        if context and hasattr(context, "bot"):
            bot = context.bot
        elif token:
            from telegram import Bot
            bot = Bot(token=token)

        if not bot:
            return

        if action:
            await bot.send_chat_action(chat_id=chat_id, action=action)
        if message and message.strip():
            await bot.send_message(chat_id=chat_id, text=message.strip())
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("notify_process error for chat_id %s: %s", chat_id, str(e))


def clean_tool_calls(text: str) -> str:
    """
    Menghapus pola pemanggilan tool/function call yang bocor ke teks respons
    (seperti '[toggle_feature: enable]', '[tool: argument]', '[check_ai_quota]', dll)
    dan merapikan spasi/newline.
    """
    if not text or not isinstance(text, str):
        return ""

    # 1. Hapus pola [tool_name: argument] atau [tool: arg=val]
    cleaned = re.sub(r"\[[a-zA-Z0-9_]+\s*:\s*[^\]]*\]", "", text)

    # 2. Hapus pola [tool_name(args)] atau [tool_name()]
    cleaned = re.sub(r"\[[a-zA-Z0-9_]+\s*\([^\]]*\)\]", "", cleaned)

    # 3. Hapus pola [tool_name] jika merujuk ke nama tool/fungsi yang dikenal atau snake_case tool call
    known_tools = {
        "toggle_feature", "check_ai_quota", "check_quota", "check_feature_health",
        "get_movie_recommendation", "get_music_recommendation", "get_weather_forecast",
        "save_journal_entry", "get_journal_recap", "send_voice_message", "search_internet",
        "get_stock_price", "get_market_summary", "create_drive_folder", "list_drive_files",
        "search_drive_files", "upload_to_drive", "download_from_drive", "get_nearby_places",
        "search_places_by_city", "execute_code", "save_note_to_notion", "save_memory_to_notion",
        "add_notion_property", "preview_with_codepen", "deploy_to_vercel", "list_vercel_deployments",
        "delete_vercel_deployment", "search_and_send_image", "simpan_aktivitas_neo4j",
        "cari_aktivitas_neo4j", "search_design_reference", "analyze_image", "identify_image_subject",
        "read_vercel_logs", "read_github_file", "create_github_branch", "update_github_file",
        "create_pull_request", "tool", "tools", "function", "action", "call", "tool_call",
    }

    def _replace_standalone_bracket(match: re.Match) -> str:
        name = match.group(1).lower()
        if name in known_tools or ("_" in name and not name.isupper()):
            return ""
        return match.group(0)

    cleaned = re.sub(r"\[([a-zA-Z0-9_]+)\]", _replace_standalone_bracket, cleaned)

    # 4. Merapikan baris dan spasi berlebih
    lines = cleaned.splitlines()
    cleaned_lines = []
    for line in lines:
        stripped = re.sub(r"[ \t]+", " ", line).strip()
        cleaned_lines.append(stripped)

    result_lines = []
    for line in cleaned_lines:
        if line or (result_lines and result_lines[-1]):
            result_lines.append(line)

    return "\n".join(result_lines).strip()

