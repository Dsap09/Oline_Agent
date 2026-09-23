"""
Script untuk mengelola daftar perintah (autocomplete) Oline di Telegram
via Bot API setMyCommands / deleteMyCommands.

CATATAN: Untuk menghilangkan tombol "Menu" (daftar perintah) dari UI Telegram,
jalankan script ini dengan flag `--clear` SEKALI, lalu JANGAN jalankan script
tanpa flag di deploy berikutnya (tombol muncul selama daftar perintah ada).

Penggunaan:
    python scripts/set_commands.py          # daftarkan command ke bot (default)
    python scripts/set_commands.py --clear  # KOSONGKAN daftar command (hapus tombol Menu)
    python scripts/set_commands.py --info   # lihat command yang sudah terdaftar

Environment variables yang dibutuhkan:
    - TELEGRAM_BOT_TOKEN: Token bot Telegram
"""

import os
import sys

import httpx
from dotenv import load_dotenv

# Load .env jika ada (untuk development lokal)
load_dotenv()

# Daftar command yang terdaftar di src/bot.py (create_application).
# Sesuaikan urutan & deskripsi dengan implementasi terbaru.
COMMANDS = [
    {"command": "start", "description": "Sambutan & onboarding Oline"},
    {"command": "help", "description": "Daftar semua perintah (ketik: /help <cmd>)"},
    {"command": "menu", "description": "Menu interaktif (tombol)"},
    {"command": "status", "description": "Lihat status task aktif & kuota AI"},
    {"command": "clear", "description": "Bersihkan konteks & pending task"},
    {"command": "batal", "description": "Batalkan task yang berjalan"},
    {"command": "cuaca", "description": "Cek cuaca: /cuaca <kota>"},
    {"command": "saham", "description": "Cek saham: /saham <ticker>"},
    {"command": "cari", "description": "Cari info internet: /cari <topik>"},
    {"command": "gambar", "description": "Cari gambar: /gambar <topik>"},
    {"command": "kuota", "description": "Cek kuota pemakaian AI"},
    {"command": "list", "description": "Lihat daftar deployment di Vercel"},
    {"command": "preview", "description": "Lihat daftar preview landing page yang aktif"},
    {"command": "landing", "description": "Buat landing page: /landing <deskripsi>"},
    {"command": "deploy", "description": "Deploy landing page ke Vercel"},
    {"command": "tasks", "description": "Lihat task aktif"},
    {"command": "fitur", "description": "Lihat status kesehatan semua fitur"},
    {"command": "aktifkan", "description": "Aktifkan fitur: /aktifkan <fitur>"},
    {"command": "matikan", "description": "Nonaktifkan fitur: /matikan <fitur>"},
    {"command": "log", "description": "Lihat analisis log error Vercel"},
    {"command": "persona", "description": "Atur gaya komunikasi: /persona <gaya>"},
    {"command": "jurnal", "description": "Simpan catatan jurnal: /jurnal <teks>"},
    {"command": "set_token", "description": "Simpan token layanan: /set_token <layanan> <token>"},
]


def _get_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN environment variable is not set.")
        sys.exit(1)
    return token


def set_commands() -> None:
    """Daftarkan daftar perintah ke Telegram via setMyCommands."""
    token = _get_token()
    api_url = f"https://api.telegram.org/bot{token}/setMyCommands"
    response = httpx.post(api_url, json={"commands": COMMANDS}, timeout=30.0)
    result = response.json()

    if result.get("ok"):
        print(f"Berhasil mendaftarkan {len(COMMANDS)} command. [SUCCESS]")
        for c in COMMANDS:
            print(f"  /{c['command']} — {c['description']}")
    else:
        print(f"Gagal mendaftarkan command [FAIL]")
        print(f"Error: {result.get('description', 'Unknown error')}")
        sys.exit(1)


def get_commands_info() -> None:
    """Tampilkan daftar command yang saat ini terdaftar."""
    token = _get_token()
    api_url = f"https://api.telegram.org/bot{token}/getMyCommands"
    response = httpx.get(api_url, timeout=30.0)
    result = response.json()

    if result.get("ok"):
        commands = result.get("result", [])
        if not commands:
            print("Belum ada command terdaftar.")
            return
        print("Command yang terdaftar:")
        for c in commands:
            print(f"  /{c.get('command')} — {c.get('description')}")
    else:
        print(f"Gagal mengambil daftar command: {result.get('description', 'Unknown error')}")


def clear_commands() -> None:
    """
    Kosongkan daftar perintah di Telegram via deleteMyCommands.
    Menghilangkan tombol "Menu" dari UI, tanpa menghapus handler command di kode —
    command tetap bisa diketik manual (/help, /cuaca, dll).
    """
    token = _get_token()
    api_url = f"https://api.telegram.org/bot{token}/deleteMyCommands"
    response = httpx.post(api_url, json={}, timeout=30.0)
    result = response.json()

    if result.get("ok"):
        print("Berhasil menghapus daftar command. Tombol 'Menu' akan hilang dari UI Telegram. [SUCCESS]")
    else:
        print(f"Gagal menghapus daftar command [FAIL]")
        print(f"Error: {result.get('description', 'Unknown error')}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--info":
        get_commands_info()
    elif len(sys.argv) > 1 and sys.argv[1] == "--clear":
        clear_commands()
    else:
        set_commands()
