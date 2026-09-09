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
]

