"""
Konfigurasi limit dan parameter provider AI untuk Oline bot.
"""

import os
from dotenv import load_dotenv

load_dotenv()

PROVIDER_LIMITS = {
    "openrouter": {
        "type": "request",
        "total": 50,
        "period": "hari",
        "label": "OpenRouter (Chat Utama)",
    },
    "groq": {
        "type": "token",
        "total": 14_400_000,
        "period": "hari",
        "label": "Groq (Cadangan Fast Path)",
    },
    "gemini": {
        "type": "token",
        "total": 1_000_000,
        "period": "hari",
        "label": "Gemini (Cadangan Slow Path)",
    },
    "deepinfra": {
        "type": "saldo",
        "total": 5.0,
        "period": "saldo",
        "label": "DeepSeek (Landing Page)",
    },
    "mistral": {
        "type": "token",
        "total": 1_000_000_000,
        "period": "bulan",
        "label": "Mistral AI (Tools)",
    },
    "cerebras": {
        "type": "token",
        "total": 1_000_000,
        "period": "hari",
        "label": "Cerebras (Cadangan)",
    },
}

FITUR_LIST = [
    "chat",
    "saham",
    "cuaca",
    "vision",
    "landing_page",
    "preview",
    "deploy",
    "notion",
    "drive",
    "neo4j",
    "search",
    "calendar",
    "akademik",
    "cek_token",
    "renew_token",
    "coding_agent",
]

# Registry token API Oline untuk tool check_token_status (Token Health Check).
# Setiap entri: env var, nama layanan, dan cara uji validitas token.
TOKEN_REGISTRY = {
    "GROQ_API_KEY": {
        "service": "Groq",
        "test_url": "https://api.groq.com/openai/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
    },
    "GEMINI_API_KEY": {
        "service": "Gemini",
        "test_url": "https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        "headers": lambda key: {},
    },
    "MISTRAL_API_KEY": {
        "service": "Mistral",
        "test_url": "https://api.mistral.ai/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
    },
    "OPENROUTER_API_KEY": {
        "service": "OpenRouter",
        "test_url": "https://openrouter.ai/api/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
    },
    "DEEPINFRA_API_KEY": {
        "service": "DeepInfra",
        "test_url": "https://api.deepinfra.com/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
    },
    "CEREBRAS_API_KEY": {
        "service": "Cerebras",
        "test_url": "https://api.cerebras.ai/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
    },
    "VERCEL_API_TOKEN": {
        "service": "Vercel",
        "test_url": "https://api.vercel.com/v2/user",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
    },
    "NOTION_API_KEY": {
        "service": "Notion",
        "test_url": "https://api.notion.com/v1/users/me",
        "headers": lambda key: {
            "Authorization": f"Bearer {key}",
            "Notion-Version": "2022-06-28",
        },
    },
    "GITHUB_TOKEN": {
        "service": "GitHub",
        "test_url": "https://api.github.com/user",
        "headers": lambda key: {"Authorization": f"token {key}"},
    },
    "GOOGLE_DRIVE_REFRESH_TOKEN": {
        "service": "Google Drive",
        "test_url": "oauth",
    },
    "GOOGLE_CALENDAR_REFRESH_TOKEN": {
        "service": "Google Calendar",
        "test_url": "oauth",
    },
    "ERINE_API_KEY": {
        "service": "ERINE",
        "test_url": "{ERINE_API_URL}/health",
        "headers": lambda key: {"x-api-key": key},
    },
}

# Token yang bisa dirotasi/diperbarui runtime oleh Oline (OAuth refresh token).
# Oline tidak bisa generate sendiri, tapi bisa "memasang" token baru yang dikirim user
# dengan meng-update Environment Variable di Vercel via Vercel API (bukan git, bukan KV).
# key = env var yang diupdate; service harus match dengan TOKEN_REGISTRY.
RENEWABLE_TOKENS = {
    "GOOGLE_DRIVE_REFRESH_TOKEN": {
        "service": "Google Drive",
        "guide": (
            "Untuk memperbarui token Google Drive:\n"
            "1. Buka https://developers.google.com/oauthplayground\n"
            "2. Klik ikon gerigi (⚙️) → centang 'Use your own OAuth credentials'\n"
            "3. Masukkan Client ID & Client Secret Oline\n"
            "4. Pilih scope: https://www.googleapis.com/auth/drive.file\n"
            "5. Klik Authorize → Exchange authorization code for tokens\n"
            "6. Salin 'refresh_token', lalu kirim ke Oline:\n"
            "   /set_token drive <refresh_token>"
        ),
    },
    "GOOGLE_CALENDAR_REFRESH_TOKEN": {
        "service": "Google Calendar",
        "guide": (
            "Untuk memperbarui token Google Calendar:\n"
            "1. Buka https://developers.google.com/oauthplayground\n"
            "2. Klik ikon gerigi (⚙️) → centang 'Use your own OAuth credentials'\n"
            "3. Masukkan Client ID & Client Secret Oline\n"
            "4. Pilih scope: https://www.googleapis.com/auth/calendar\n"
            "5. Klik Authorize → Exchange authorization code for tokens\n"
            "6. Salin 'refresh_token', lalu kirim ke Oline:\n"
            "   /set_token calendar <refresh_token>"
        ),
    },
}


def token_key_for_env(env_key: str) -> str:
    """
    Memetakan nama env var ke key penyimpanan token di Vercel KV (pendek & stabil).
    Contoh: GOOGLE_DRIVE_REFRESH_TOKEN -> "drive".
    """
    mapping = {
        "GOOGLE_DRIVE_REFRESH_TOKEN": "drive",
        "GOOGLE_CALENDAR_REFRESH_TOKEN": "calendar",
    }
    return mapping.get(env_key, env_key.lower())

