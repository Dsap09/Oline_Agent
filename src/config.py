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
    "opencode_go": {
        "type": "langganan",
        "total": 0,
        "period": "bulan",
        "label": "OpenCode Go (Paket $10/bulan)",
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
    "OPENCODE_GO_API_KEY": {
        "service": "OpenCode Go",
        "test_url": "https://opencode.ai/zen/go/v1/models",
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


# --- Katalog Model AI Manual (command /models) ---
# Setiap entri model: key (preferensi tersimpan di KV), model_id (dikirim ke provider),
# label tampilan, emoji, deskripsi singkat, dan info harga (opsional).
# PENTING: hanya model yang mendukung function calling (uji via scripts/verify_models.py).
MODEL_CATALOG = {
    "opencode_go": {
        "label": "OpenCode Go",
        "note": "Kuota paket: $4/5 jam, $10/minggu, $20/bulan.",
        "models": [
            {
                "key": "ocg:deepseek-v4-flash",
                "model_id": "deepseek-v4-flash",
                "label": "DeepSeek V4 Flash",
                "emoji": "💎",
                "desc": "Coding sehari-hari, termurah & cepat",
                "price": "Termasuk langganan OpenCode Go",
                "recommended": True,
            },
            {
                "key": "ocg:deepseek-v4-pro",
                "model_id": "deepseek-v4-pro",
                "label": "DeepSeek V4 Pro",
                "emoji": "🚀",
                "desc": "Reasoning kompleks",
                "price": "Termasuk langganan OpenCode Go",
            },
            {
                "key": "ocg:glm-5.2",
                "model_id": "glm-5.2",
                "label": "GLM-5.2",
                "emoji": "🧠",
                "desc": "Model open frontier, reasoning kuat",
                "price": "Termasuk langganan OpenCode Go",
            },
            {
                "key": "ocg:kimi-k2.7-code",
                "model_id": "kimi-k2.7-code",
                "label": "Kimi K2.7 Code",
                "emoji": "⚡",
                "desc": "Coding terspesialisasi",
                "price": "Termasuk langganan OpenCode Go",
                "temperature": 1.0,
            },
            {
                "key": "ocg:mimo-v2.5",
                "model_id": "mimo-v2.5",
                "label": "MiMo-V2.5",
                "emoji": "🔮",
                "desc": "General-purpose, serbaguna",
                "price": "Termasuk langganan OpenCode Go",
            },
        ],
    },
    "deepinfra": {
        "label": "DeepInfra",
        "note": "Pay-as-you-go, dibayar per token.",
        "models": [
            {
                "key": "di:deepseek-ai/DeepSeek-V3.1-Terminus",
                "model_id": "deepseek-ai/DeepSeek-V3.1-Terminus",
                "label": "DeepSeek-V3.1-Terminus",
                "emoji": "💎",
                "desc": "Function calling, coding, agentic",
                "price": "$0.27 / $0.95 per 1M token",
                "recommended": True,
            },
            {
                "key": "di:Qwen/Qwen3-32B",
                "model_id": "Qwen/Qwen3-32B",
                "label": "Qwen3-32B",
                "emoji": "⚡",
                "desc": "Obrolan cepat & task ringan",
                "price": "$0.08 / $0.28 per 1M token",
            },
            {
                "key": "di:meta-llama/Llama-3.3-70B-Instruct-Turbo",
                "model_id": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                "label": "Llama 3.3 70B Turbo",
                "emoji": "🚀",
                "desc": "Function calling, umum",
                "price": "$0.10 / $0.32 per 1M token",
            },
        ],
    },
}

# Preset cepat untuk /models preset <nama>.
MODEL_PRESETS = {
    "ringan": "di:Qwen/Qwen3-32B",
    "pintar": "ocg:deepseek-v4-pro",
    "coding": "ocg:kimi-k2.7-code",
    "gratis": "auto",
}

# Pemetaan prefix key model ke provider (dipakai saat routing manual).
MODEL_KEY_PROVIDERS = {
    "ocg": "opencode_go",
    "di": "deepinfra",
}


def parse_model_key(key: str) -> tuple:
    """
    Memecah key model menjadi (provider, model_id).
    Contoh: 'ocg:deepseek-v4-flash' -> ('opencode_go', 'deepseek-v4-flash').
    Returns (None, None) bila key tidak dikenal / bukan model manual.
    """
    if not key or key == "auto":
        return None, None
    raw = str(key).strip()
    prefix, _, model_id = raw.partition(":")
    provider = MODEL_KEY_PROVIDERS.get(prefix)
    if not provider or not model_id:
        return None, None
    return provider, model_id


def get_model_entry(key: str) -> dict:
    """
    Mengambil entri katalog model berdasarkan key-nya.
    Returns dict berisi data model + provider & category, atau {} bila tidak ada.
    """
    if not key:
        return {}
    for category, group in MODEL_CATALOG.items():
        for entry in group.get("models", []):
            if entry.get("key") == key:
                return {**entry, "provider": category}
    return {}


def get_model_label(key: str) -> str:
    """Label tampilan model untuk notifikasi/pesan; 'AUTO' untuk rotasi otomatis."""
    if not key or key == "auto":
        return "AUTO"
    entry = get_model_entry(key)
    return entry.get("label") or key


def list_manual_models() -> list:
    """Daftar semua key model manual yang terdaftar di katalog."""
    return [
        entry["key"]
        for group in MODEL_CATALOG.values()
        for entry in group.get("models", [])
        if entry.get("key")
    ]

