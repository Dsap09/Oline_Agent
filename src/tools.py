"""
Tool definitions dan executor functions untuk Gemini Function Calling.
Mencakup: rekomendasi film (TMDb), rekomendasi musik (iTunes),
cuaca (OpenWeatherMap), dan jurnal harian (Vercel KV).
"""

import asyncio
import logging
import math
import os
import re
from datetime import datetime
from typing import Any, Optional

import httpx

from src.kv import (
    get_journal_entries,
    get_monthly_tts_usage,
    get_today_groq_usage,
    get_today_usage,
    get_user_location,
    save_journal,
    save_tts_usage,
    save_user_location,
)

# NOTE: src.notion dan src.voice di-lazy-load di dalam fungsi masing-masing
# untuk mengurangi cold start time pada Vercel serverless.
from src.github_tools import (
    create_github_branch,
    create_pull_request,
    read_github_file,
    update_github_file,
)
from src.utils import format_date_indonesian, parse_relative_date
from src.vercel_logs import read_vercel_logs

logger = logging.getLogger(__name__)

# API Keys dari environment
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")

# Base URLs
TMDB_BASE_URL = "https://api.themoviedb.org/3"
ITUNES_BASE_URL = "https://itunes.apple.com"
OPENWEATHER_BASE_URL = "https://api.openweathermap.org/data/2.5"
MOONDREAM_SPACE_1 = os.environ.get("MOONDREAM_SPACE_1", "merve/moondream3")
MOONDREAM_SPACE_2 = os.environ.get("MOONDREAM_SPACE_2", "vikhyatk/moondream2")


# ============================================================
# Gemini Function/Tool Declarations (untuk dikirim ke Gemini API)
# ============================================================

TOOL_DECLARATIONS = [
    {
        "name": "get_movie_recommendation",
        "description": (
            "Mencari dan merekomendasikan film berdasarkan genre, mood, "
            "atau kata kunci pencarian. Gunakan saat pengguna meminta "
            "rekomendasi film."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Kata kunci pencarian film (misal: 'horror indonesia', 'komedi romantis').",
                },
                "genre": {
                    "type": "string",
                    "description": "Genre film (misal: 'horror', 'comedy', 'action', 'drama'). Opsional.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_music_recommendation",
        "description": (
            "Mencari dan merekomendasikan lagu/musik berdasarkan genre, "
            "artis, atau kata kunci. Gunakan saat pengguna meminta "
            "rekomendasi lagu atau musik."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Kata kunci pencarian musik (misal: 'pop indonesia', 'lagu chill').",
                },
                "artist": {
                    "type": "string",
                    "description": "Nama artis spesifik (opsional).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_weather_forecast",
        "description": (
            "Mengecek cuaca saat ini atau forecast untuk kota tertentu. "
            "Gunakan saat pengguna menanyakan kondisi cuaca."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Nama kota (misal: 'Bandung', 'Jakarta', 'Yogyakarta').",
                },
                "date": {
                    "type": "string",
                    "description": (
                        "Tanggal cuaca yang ingin dicek dalam format YYYY-MM-DD. "
                        "Jika tidak disebutkan, gunakan hari ini."
                    ),
                },
            },
            "required": ["city"],
        },
    },
    {
        "name": "save_journal_entry",
        "description": (
            "Menyimpan catatan jurnal harian pengguna. Gunakan saat pengguna "
            "ingin mencatat jurnal, diary, atau hal yang ingin diingat hari ini."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Isi catatan jurnal yang ingin disimpan.",
                },
                "date": {
                    "type": "string",
                    "description": "Tanggal jurnal (format YYYY-MM-DD). Default hari ini jika tidak disebutkan.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "get_journal_recap",
        "description": (
            "Mengambil rekap atau catatan jurnal sebelumnya. Gunakan saat "
            "pengguna meminta rekap jurnal, melihat catatan sebelumnya, "
            "atau bertanya apa yang ditulis pada tanggal tertentu."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Tanggal awal rentang (YYYY-MM-DD). Default 7 hari lalu.",
                },
                "end_date": {
                    "type": "string",
                    "description": "Tanggal akhir rentang (YYYY-MM-DD). Default hari ini.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "check_ai_quota",
        "description": (
            "Mengecek laporan kuota 6 AI provider (OpenRouter, Groq, Gemini, DeepInfra, Mistral, Cerebras) terpakai dan sisa kuota hari ini. "
            "WAJIB digunakan saat pengguna bertanya tentang kuota, sisa token, pemakaian AI, status AI, atau 'cek kuota'."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_quota",
        "description": (
            "Mengecek sisa kuota token dan pemakaian API hari ini (Gemini API & Groq API)."
        ),

        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "send_voice_message",
        "description": (
            "Mengirim pesan suara (voice note) ketika pengguna meminta Oline "
            "bernyanyi, membaca puisi, menggombal dengan suara, atau mengucapkan sesuatu dengan suara."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Teks kalimat, lirik, atau puisi yang akan diucapkan Oline dengan suara.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "search_internet",
        "description": (
            "Cari informasi terkini di internet. Gunakan saat pengguna bertanya "
            "hal yang memerlukan data real-time, berita, fakta terbaru, definisi, "
            "atau informasi di luar pengetahuan umum yang kamu miliki."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Kata kunci pencarian yang ingin dicari di internet.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_stock_price",
        "description": (
            "Ambil harga terkini dan perubahan harian suatu saham Indonesia. "
            "Kode saham 4 huruf (contoh: BBCA, BBRI, TLKM)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Kode saham 4 huruf, tanpa .JK (misal: BBCA, TLKM, BBRI).",
                }
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "create_drive_folder",
        "description": (
            "Membuat folder baru di Google Drive (Database Oline). "
            "Gunakan saat pengguna meminta membuat folder baru di drive/database."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "folder_name": {
                    "type": "string",
                    "description": "Nama folder baru yang ingin dibuat.",
                }
            },
            "required": ["folder_name"],
        },
    },
    {
        "name": "list_drive_files",
        "description": (
            "Melihat daftar isi file dan folder di Google Drive (Database Oline). "
            "Gunakan saat pengguna meminta tampilkan isi folder, lihat daftar file, atau isi database."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "folder_name": {
                    "type": "string",
                    "description": "Nama subfolder yang ingin dilihat isinya (opsional). Jika kosong, tampilkan root database.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "search_drive_files",
        "description": (
            "Mencari file berdasarkan nama di Google Drive (Database Oline). "
            "Gunakan saat pengguna mencari file spesifik."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Kata kunci atau nama file yang dicari.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "upload_to_drive",
        "description": (
            "Menyimpan file/dokumen/foto yang baru diterima dari pengguna ke Google Drive. "
            "Gunakan saat pengguna meminta menyimpan file/foto ke folder atau database."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "folder_name": {
                    "type": "string",
                    "description": "Nama subfolder tempat menyimpan file (opsional).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "download_from_drive",
        "description": (
            "Mengambil dan mengirimkan file/foto dari Google Drive kembali ke pengguna di Telegram. "
            "Gunakan saat pengguna meminta kirim file, minta foto, atau download dari drive."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_name": {
                    "type": "string",
                    "description": "Nama file atau foto yang ingin dikirimkan.",
                },
                "folder_name": {
                    "type": "string",
                    "description": "Nama subfolder tempat file tersimpan (opsional).",
                },
            },
            "required": ["file_name"],
        },
    },
    {
        "name": "get_nearby_places",
        "description": (
            "Mencari tempat terdekat dari lokasi pengguna yang tersimpan, "
            "berdasarkan kategori (misal: cafe, toko buku, restoran, mall)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Kategori tempat, misal 'cafe', 'restaurant', 'bookstore', 'mall', 'toko buku'.",
                },
                "radius_km": {
                    "type": "number",
                    "description": "Radius pencarian dalam kilometer. Default 2.0.",
                },
            },
            "required": ["category"],
        },
    },
    {
        "name": "search_places_by_city",
        "description": (
            "Mencari tempat berdasarkan nama kota/area dan kategori. "
            "Menggunakan geocoding untuk mendapatkan koordinat kota."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Nama kota atau area (misal: 'Surabaya', 'Bandung', 'Jakarta Selatan').",
                },
                "category": {
                    "type": "string",
                    "description": "Kategori tempat, misal 'toko buku', 'cafe', 'restoran', 'mall'.",
                },
            },
            "required": ["city", "category"],
        },
    },
    {
        "name": "execute_code",
        "description": (
            "Jalankan potongan kode menggunakan Piston API. Dukung banyak bahasa. "
            "Gunakan saat pengguna meminta mengeksekusi atau menjalankan kode."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "Bahasa pemrograman, misal: python, javascript, cpp, java, dll.",
                },
                "code": {
                    "type": "string",
                    "description": "Kode sumber yang akan dijalankan.",
                },
            },
            "required": ["language", "code"],
        },
    },
    {
        "name": "save_note_to_notion",
        "description": (
            "Menyimpan catatan umum, dokumen, ide proyek, artikel, atau tulisan bebas ke Database Catatan Notion. "
            "Gunakan HANYA saat pengguna meminta mencatat atau menyimpan catatan/dokumen/ide umum ke Notion "
            "(misal: 'catat ini ke Notion', 'simpan catatan rapat di Notion', 'masukkan ide proyek ini ke Notion'). "
            "JANGAN gunakan tool ini untuk menyimpan aturan/preferensi sistem."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Judul catatan.",
                },
                "content": {
                    "type": "string",
                    "description": "Isi lengkap catatan.",
                },
                "category": {
                    "type": "string",
                    "description": "Kategori catatan (misal: 'Umum', 'Skripsi', 'Riset', 'Pribadi'). Default: Umum.",
                },
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "save_memory_to_notion",
        "description": (
            "Menyimpan memori, aturan sistem, preferensi pengguna, atau instruksi khusus ke Database Memori Notion ('Memori Oline'). "
            "Gunakan HANYA saat pengguna meminta menyimpan atau mengingat aturan, preferensi pribadi, instruksi permanen, atau fakta pengguna "
            "(misal: 'ingat bahwa...', 'mulai sekarang panggil saya...', 'selalu gunakan...', 'simpan preferensi/aturan ini ke Notion'). "
            "JANGAN gunakan tool ini untuk menyimpan catatan/dokumen/ide biasa."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Judul singkat memori atau aturan (misal: 'Preferensi Panggilan', 'Aturan Bahasa').",
                },
                "content": {
                    "type": "string",
                    "description": "Isi lengkap memori, aturan, atau preferensi.",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["Aturan", "Preferensi", "Ringkasan", "Fakta"],
                    "description": "Tipe memori: 'Aturan', 'Preferensi', 'Ringkasan', atau 'Fakta'. Default: Aturan.",
                },
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "preview_with_codepen",
        "description": (
            "Mengunggah kode HTML/CSS/JS landing page ke CodePen/Preview Service dan mengembalikan link preview. "
            "Gunakan saat pengguna meminta dibuatkan landing page, website baru, atau meminta preview tampilan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Judul landing page atau website, misal: GYM MANIA.",
                },
                "html": {
                    "type": "string",
                    "description": "Kode HTML lengkap.",
                },
                "css": {
                    "type": "string",
                    "description": "Kode CSS lengkap.",
                },
                "js": {
                    "type": "string",
                    "description": "Kode JavaScript (opsional).",
                },
            },
            "required": ["title", "html", "css"],
        },
    },
    {
        "name": "deploy_to_vercel",
        "description": (
            "Mendeploy file statis (HTML, CSS, JS) ke Vercel dan mengembalikan URL live. "
            "Gunakan saat pengguna meminta mendeploy website, landing page, atau meng-online-kan kode. "
            "Kirimkan array object pada parameter `files`: [{'filename': 'index.html', 'content': '...'}]"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Nama project deployment (hanya huruf, angka, dash, misal: landing-page-minuman).",
                },
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": "Nama file statis, misal: index.html, style.css, script.js.",
                            },
                            "content": {
                                "type": "string",
                                "description": "Isi file lengkap.",
                            },
                        },
                        "required": ["filename", "content"],
                    },
                    "description": "Daftar file statis yang akan dideploy.",
                },
            },
            "required": ["project_name", "files"],
        },
    },
    {
        "name": "search_and_send_image",
        "description": (
            "Mencari gambar di internet dan mengirimkannya langsung sebagai foto ke chat Telegram. "
            "DEFAULT: Kirim 1 gambar saja (max_results=1). "
            "JANGAN set max_results > 1 kecuali pengguna secara eksplisit meminta jumlah lebih (misal: 'kirim 2 gambar', 'cari 3 foto')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Kata kunci pencarian gambar, misal 'ayam', 'pemandangan', 'logo kopi'.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Jumlah maksimal gambar yang ingin dicari/dikirim. DEFAULT 1. Hanya isi >1 jika pengguna meminta jumlah tertentu.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "add_notion_property",
        "description": (
            "Menambahkan atau mengedit kolom/properti pada database Notion (misal: tambah kolom 'File', 'Lampiran', 'Status', 'Link', 'Tanggal'). "
            "Gunakan saat pengguna meminta menambah, membuat, atau mengedit kolom/properti di Notion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nama kolom/properti yang ingin ditambahkan (misal: 'File', 'Lampiran', 'Status').",
                },
                "property_type": {
                    "type": "string",
                    "description": "Tipe data kolom (misal: 'files', 'url', 'select', 'date', 'checkbox', 'number', 'rich_text'). Default: 'files'.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_vercel_deployments",
        "description": (
            "Mengambil daftar deployment yang ada di Vercel. "
            "Gunakan saat pengguna meminta melihat daftar landing page atau deployment yang pernah dibuat."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "delete_vercel_deployment",
        "description": (
            "Menghapus deployment Vercel berdasarkan deployment ID. "
            "Gunakan saat pengguna ingin menghapus landing page atau deployment tertentu setelah mengonfirmasi pilihan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "deployment_id": {
                    "type": "string",
                    "description": "ID deployment Vercel yang akan dihapus (misal: dpl_12345).",
                }
            },
            "required": ["deployment_id"],
        },
    },
    {
        "name": "simpan_aktivitas_neo4j",
        "description": (
            "Menyimpan aktivitas pengguna ke database graph Neo4j. "
            "Gunakan saat pengguna meminta mencatat atau menyimpan aktivitas tertentu."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "aksi": {
                    "type": "string",
                    "description": "Jenis aksi yang dilakukan (misal: 'minta landing page', 'deploy', 'simpan catatan').",
                },
                "objek": {
                    "type": "string",
                    "description": "Objek atau target aksi (misal: 'GYM MANIA', 'catatan meeting').",
                },
            },
            "required": ["aksi", "objek"],
        },
    },
    {
        "name": "cari_aktivitas_neo4j",
        "description": (
            "Mencari dan menampilkan riwayat aktivitas terakhir pengguna dari database graph Neo4j. "
            "Gunakan saat pengguna meminta melihat aktivitas terakhir, log aktivitas, atau riwayat aksi."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Jumlah maksimal aktivitas yang ditampilkan. Default 10.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_design_reference",
        "description": (
            "Mencari website referensi di internet dan menganalisis desainnya "
            "(struktur, warna, font, copywriting). Gunakan saat pengguna meminta "
            "landing page dengan referensi desain dari website tertentu atau website sejenis."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Kata kunci untuk mencari website referensi, misal: 'website gym terkenal', 'kedai kopi modern', 'situs restoran elegan'.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_vercel_logs",
        "description": "Membaca log Vercel terbaru. Gunakan mode 'error' untuk masalah, atau mode 'semua' untuk log lengkap.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["error", "semua"],
                    "description": "Mode filter log. 'error' untuk error/warn saja, 'semua' untuk semua level.",
                }
            },
            "required": ["mode"],
        },
    },
    {
        "name": "read_github_file",
        "description": "Membaca isi file dari repository GitHub Oline.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path file, misal: src/personas.py, README.md.",
                },
                "branch": {
                    "type": "string",
                    "description": "Nama branch (opsional, default: main).",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "create_github_branch",
        "description": "Membuat branch baru di repository GitHub dari branch main.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch_name": {
                    "type": "string",
                    "description": "Nama branch baru, misal: oline-update/cek-kuota.",
                },
            },
            "required": ["branch_name"],
        },
    },
    {
        "name": "update_github_file",
        "description": "Menambah atau memperbarui file di branch tertentu di repository GitHub.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "description": "Nama branch tujuan.",
                },
                "path": {
                    "type": "string",
                    "description": "Path file (misal: src/personas.py).",
                },
                "content": {
                    "type": "string",
                    "description": "Isi lengkap file baru.",
                },
                "commit_message": {
                    "type": "string",
                    "description": "Pesan commit.",
                },
            },
            "required": ["branch", "path", "content", "commit_message"],
        },
    },
    {
        "name": "create_pull_request",
        "description": "Membuat Pull Request dari branch fitur ke main.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "description": "Nama branch sumber.",
                },
                "title": {
                    "type": "string",
                    "description": "Judul Pull Request.",
                },
                "body": {
                    "type": "string",
                    "description": "Deskripsi atau rincian perubahan Pull Request.",
                },
            },
            "required": ["branch", "title", "body"],
        },
    },
    {
        "name": "check_feature_health",
        "description": "Memeriksa kesehatan semua fitur Oline.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "toggle_feature",
        "description": "Mengaktifkan atau menonaktifkan fitur Oline.",
        "parameters": {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "description": "Nama fitur yang ingin diubah (misal: 'saham', 'cuaca', 'vision', 'notion', 'drive', 'deploy', 'landing_page', 'search').",
                },
                "status": {
                    "type": "boolean",
                    "description": "True untuk mengaktifkan, False untuk menonaktifkan.",
                },
            },
            "required": ["feature", "status"],
        },
    },
]

TOOLS_BY_INTENT = {
    "cuaca": ["get_weather_forecast"],
    "rekomendasi": ["get_movie_recommendation", "get_music_recommendation"],
    "suara": ["send_voice_message"],
    "jurnal": ["save_journal_entry", "get_journal_recap"],
    "kuota": ["check_ai_quota", "check_quota"],
    "health": ["check_feature_health"],
    "kelola_fitur": ["toggle_feature", "check_feature_health"],
    "search": ["search_internet", "read_vercel_logs"],
    "saham": ["get_stock_price", "get_market_summary"],
    "drive": [
        "create_drive_folder",
        "list_drive_files",
        "search_drive_files",
        "upload_to_drive",
        "download_from_drive",
    ],
    "lokasi": ["get_nearby_places", "search_places_by_city"],
    "coding": ["execute_code", "read_vercel_logs"],
    "notion": ["save_note_to_notion", "save_memory_to_notion", "add_notion_property"],
    "preview": ["preview_with_codepen", "search_design_reference"],
    "deploy": [
        "deploy_to_vercel",
        "list_vercel_deployments",
        "delete_vercel_deployment",
    ],
    "gambar": ["search_and_send_image"],
    "neo4j": ["simpan_aktivitas_neo4j", "cari_aktivitas_neo4j"],
    "design_reference": ["search_design_reference"],
    "vercel_logs": ["read_vercel_logs"],
    "github": [
        "read_github_file",
        "create_github_branch",
        "update_github_file",
        "create_pull_request",
    ],
}




def get_tools_for_intent(intent: Optional[str]) -> list[dict]:
    """
    Mengembalikan deklarasi tool yang terfilter sesuai intent pengguna.
    Jika intent None (Fast Path), mengembalikan list kosong [].
    """
    if not intent:
        return []

    allowed_names = set(TOOLS_BY_INTENT.get(intent, []))
    if not allowed_names:
        return []

    return [decl for decl in TOOL_DECLARATIONS if decl["name"] in allowed_names]


# ============================================================
# Tool Executor Functions
# ============================================================



async def get_movie_recommendation(
    query: str, genre: Optional[str] = None
) -> dict[str, Any]:
    """Mencari film di TMDb API berdasarkan query dan genre."""
    if not TMDB_API_KEY:
        return {"error": "TMDb API key tidak dikonfigurasi."}

    # Genre mapping TMDb
    genre_map = {
        "action": 28, "adventure": 12, "animation": 16, "comedy": 35,
        "crime": 80, "documentary": 99, "drama": 18, "family": 10751,
        "fantasy": 14, "history": 36, "horror": 27, "music": 10402,
        "mystery": 9648, "romance": 10749, "sci-fi": 878, "thriller": 53,
        "war": 10752, "western": 37, "komedi": 35, "horor": 27,
        "aksi": 28, "petualangan": 12, "animasi": 16, "dokumenter": 99,
        "keluarga": 10751, "fantasi": 14, "sejarah": 36, "misteri": 9648,
        "romantis": 10749, "perang": 10752,
    }

    params: dict[str, Any] = {
        "api_key": TMDB_API_KEY,
        "language": "id-ID",
        "query": query,
        "page": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Coba search dulu
            response = await client.get(
                f"{TMDB_BASE_URL}/search/movie", params=params
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])

            # Coba deteksi genre dari query jika genre belum diisi
            target_genre = genre
            if not target_genre:
                for g_name in genre_map:
                    if g_name in query.lower():
                        target_genre = g_name
                        break

            # Jika search kosong, coba discover berdasarkan genre
            if not results and target_genre:
                genre_id = genre_map.get(target_genre.lower())
                if genre_id:
                    discover_params: dict[str, Any] = {
                        "api_key": TMDB_API_KEY,
                        "language": "id-ID",
                        "with_genres": genre_id,
                        "sort_by": "popularity.desc",
                        "page": 1,
                    }
                    response = await client.get(
                        f"{TMDB_BASE_URL}/discover/movie", params=discover_params
                    )
                    response.raise_for_status()
                    data = response.json()
                    results = data.get("results", [])

            # Ambil top 5
            movies = []
            for movie in results[:5]:
                movies.append({
                    "title": movie.get("title", "Unknown"),
                    "original_title": movie.get("original_title", ""),
                    "year": movie.get("release_date", "")[:4],
                    "rating": movie.get("vote_average", 0),
                    "overview": movie.get("overview", "")[:200],
                })

            if not movies:
                return {"message": "Tidak ditemukan film yang cocok dengan pencarian."}

            return {"movies": movies, "total_results": data.get("total_results", 0)}

    except httpx.HTTPStatusError as e:
        logger.error("TMDb HTTP error: %s", e.response.status_code)
        if e.response.status_code == 401:
            return {"error": "TMDb API key tidak valid atau belum diaktifkan."}
        return {"error": f"Gagal mengakses TMDb API (Status {e.response.status_code})."}
    except httpx.RequestError as e:
        logger.error("TMDb API error: %s", str(e))
        return {"error": f"Gagal mengakses TMDb API: {str(e)}"}


async def get_music_recommendation(
    query: str, artist: Optional[str] = None
) -> dict[str, Any]:
    """Mencari lagu di iTunes Search API berdasarkan query dan artis."""
    search_term = query
    if artist:
        search_term = f"{artist} {query}"

    params = {
        "term": search_term,
        "media": "music",
        "entity": "song",
        "limit": 5,
        "country": "ID",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{ITUNES_BASE_URL}/search", params=params)
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            songs = []
            for track in results:
                songs.append({
                    "title": track.get("trackName", "Unknown"),
                    "artist": track.get("artistName", "Unknown"),
                    "album": track.get("collectionName", ""),
                    "genre": track.get("primaryGenreName", ""),
                    "preview_url": track.get("previewUrl", ""),
                    "track_url": track.get("trackViewUrl", ""),
                })

            if not songs:
                return {"message": "Tidak ditemukan lagu yang cocok dengan pencarian."}

            return {"songs": songs}

    except httpx.RequestError as e:
        logger.error("iTunes API error: %s", str(e))
        return {"error": f"Gagal mengakses iTunes API: {str(e)}"}


async def get_weather_forecast(
    city: str, date: Optional[str] = None
) -> dict[str, Any]:
    """
    Mengecek cuaca untuk kota tertentu via OpenWeatherMap API.
    Mendukung cuaca saat ini dan forecast hingga 5 hari ke depan.
    """
    if not OPENWEATHER_API_KEY:
        return {"error": "OpenWeatherMap API key tidak dikonfigurasi."}

    target_date = date or datetime.now().strftime("%Y-%m-%d")
    cache_key = f"cache:weather:{city.lower().strip()}:{target_date}"
    try:
        from src.kv import get_cache
        cached_val = await get_cache(cache_key)
        if cached_val:
            return json.loads(cached_val)
    except Exception:
        pass

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Ambil forecast 5 hari
            params: dict[str, Any] = {
                "q": city,
                "appid": OPENWEATHER_API_KEY,
                "units": "metric",
                "lang": "id",
            }

            # Jika tanggal hari ini atau tidak disebut, gunakan current weather
            target_date = date or datetime.now().strftime("%Y-%m-%d")
            today_str = datetime.now().strftime("%Y-%m-%d")

            if target_date == today_str:
                response = await client.get(
                    f"{OPENWEATHER_BASE_URL}/weather", params=params
                )
                response.raise_for_status()
                data = response.json()

                res = {
                    "city": data.get("name", city),
                    "date": format_date_indonesian(today_str),
                    "temp": data["main"]["temp"],
                    "temp_min": data["main"]["temp_min"],
                    "temp_max": data["main"]["temp_max"],
                    "humidity": data["main"]["humidity"],
                    "condition": data["weather"][0]["description"],
                    "wind_speed": data["wind"]["speed"],
                }
                try:
                    from src.kv import set_cache
                    await set_cache(cache_key, json.dumps(res), ttl_seconds=600)
                except Exception:
                    pass
                return res
            else:
                # Gunakan forecast
                response = await client.get(
                    f"{OPENWEATHER_BASE_URL}/forecast", params=params
                )
                response.raise_for_status()
                data = response.json()

                # Cari forecast untuk tanggal target
                target_forecasts = []
                for item in data.get("list", []):
                    item_date = item["dt_txt"][:10]
                    if item_date == target_date:
                        target_forecasts.append(item)

                if not target_forecasts:
                    return {
                        "error": (
                            f"Data cuaca untuk tanggal {format_date_indonesian(target_date)} "
                            "belum tersedia. Forecast hanya tersedia hingga 5 hari ke depan."
                        )
                    }

                # Ambil rata-rata dari forecasts hari itu
                temps = [f["main"]["temp"] for f in target_forecasts]
                humidity = [f["main"]["humidity"] for f in target_forecasts]
                conditions = [f["weather"][0]["description"] for f in target_forecasts]
                winds = [f["wind"]["speed"] for f in target_forecasts]

                # Kondisi cuaca paling sering muncul
                most_common_condition = max(set(conditions), key=conditions.count)

                return {
                    "city": data.get("city", {}).get("name", city),
                    "date": format_date_indonesian(target_date),
                    "temp": round(sum(temps) / len(temps), 1),
                    "temp_min": round(min(temps), 1),
                    "temp_max": round(max(temps), 1),
                    "humidity": round(sum(humidity) / len(humidity)),
                    "condition": most_common_condition,
                    "wind_speed": round(sum(winds) / len(winds), 1),
                }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"error": f"Kota '{city}' tidak ditemukan. Coba nama kota lain ya."}
        logger.error("OpenWeatherMap HTTP error: %s", e.response.status_code)
        return {"error": "Gagal mengakses data cuaca."}
    except httpx.RequestError as e:
        logger.error("OpenWeatherMap request error: %s", str(e))
        return {"error": f"Gagal mengakses OpenWeatherMap API: {str(e)}"}


async def execute_save_journal(chat_id: int, text: str, date: Optional[str] = None) -> dict[str, Any]:
    """Menyimpan entri jurnal harian ke Vercel KV."""
    journal_date = date or datetime.now().strftime("%Y-%m-%d")
    success = await save_journal(chat_id, text, journal_date)

    if success:
        return {
            "status": "saved",
            "date": format_date_indonesian(journal_date),
            "message": "Jurnal berhasil disimpan.",
        }
    else:
        return {"error": "Gagal menyimpan jurnal. Coba lagi nanti ya."}


async def execute_get_journal_recap(
    chat_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, Any]:
    """Mengambil rekap jurnal dari Vercel KV."""
    entries = await get_journal_entries(chat_id, start_date, end_date)

    if not entries:
        return {"message": "Belum ada catatan jurnal di rentang tanggal tersebut."}

    formatted = {}
    for date_key, text in entries.items():
        formatted[format_date_indonesian(date_key)] = text

    return {"entries": formatted, "total_entries": len(formatted)}


def hitung_sisa(provider: str, terpakai: Any) -> str:
    """
    Menghitung sisa kuota/limit untuk provider AI tertentu (brief.md).
    """
    from src.config import PROVIDER_LIMITS

    limit = PROVIDER_LIMITS.get(provider)
    if not limit:
        return "tidak tersedia"

    limit_type = limit.get("type")
    total_limit = limit.get("total", 0)

    try:
        if limit_type == "saldo":
            val = float(terpakai) if terpakai else 0.0
            sisa_saldo = max(0.0, float(total_limit) - val)
            return f"${sisa_saldo:.2f}"
        else:
            val = int(terpakai) if terpakai else 0
            sisa = max(0, int(total_limit) - val)
            if limit_type == "token":
                period = limit.get("period", "hari")
                period_suffix = f"/{period}" if period != "hari" else ""
                return f"{sisa:,} token{period_suffix}"
            else:
                return f"{sisa} request"
    except (ValueError, TypeError):
        return "tidak tersedia"


def format_quota_report(usage_data: dict) -> str:
    """
    Menyusun laporan kuota AI hari ini dengan format Terpakai, Sisa, dan Status (brief.md).
    """
    from src.config import PROVIDER_LIMITS

    lines = ["📊 Laporan Kuota AI Hari Ini\n"]

    total_request = 0
    total_token = 0

    for provider, limit_info in PROVIDER_LIMITS.items():
        label = limit_info.get("label", provider)
        data = usage_data.get(provider, {})
        req = data.get("request", 0) if isinstance(data, dict) else 0
        token = data.get("token", 0) if isinstance(data, dict) else 0

        limit_type = limit_info.get("type", "request")
        if limit_type == "request":
            terpakai_val = req
            terpakai_str = f"{req} request"
        elif limit_type == "token":
            terpakai_val = token
            terpakai_str = f"{token:,} token"
        elif limit_type == "saldo":
            terpakai_val = data.get("saldo_terpakai", 0.0) if isinstance(data, dict) else 0.0
            terpakai_str = f"${terpakai_val:.2f}"
        else:
            terpakai_val = req
            terpakai_str = f"{req} request"

        sisa_str = hitung_sisa(provider, terpakai_val)
        status = "✅ Aman" if "0 token" not in sisa_str and "0 request" not in sisa_str else "❌ Habis"

        lines.append(f"{label}")
        lines.append(f"   Terpakai: {terpakai_str}")
        lines.append(f"   Sisa: {sisa_str}")
        lines.append(f"   Status: {status}")
        lines.append("")

        total_request += req
        total_token += token

    lines.append("Ringkasan:")
    lines.append(f"• Total request: {total_request}")
    lines.append(f"• Total token: {total_token:,}")

    return "\n".join(lines)


async def check_ai_quota(chat_id: int = 0) -> str:
    """
    Menampilkan laporan pemakaian dan sisa semua model AI (brief.md).
    """
    from src.kv import get_all_ai_usage
    usage_data = await get_all_ai_usage()
    return format_quota_report(usage_data)


async def execute_check_quota(chat_id: int) -> dict[str, Any]:
    """
    Mengecek pemakaian API (OpenRouter Model Rotation, Groq, & Gemini) hari ini.
    Menampilkan model aktif, daftar rotasi, pemakaian per model, dan sisa antrean sebelum fallback.
    """
    from src.kv import get_all_ai_usage
    usage_data = await get_all_ai_usage()
    formatted_report = format_quota_report(usage_data)
    GEMINI_LIMIT = 1_000_000
    GROQ_LIMIT = 14_400_000

    gemini_used = await get_today_usage(chat_id)
    groq_used = await get_today_groq_usage(chat_id)

    gemini_remaining = max(0, GEMINI_LIMIT - gemini_used)
    groq_remaining = max(0, GROQ_LIMIT - groq_used)

    # OpenRouter Model Rotation Quota Info
    openrouter_info = {}
    try:
        from src.openrouter import get_openrouter_quota_info
        openrouter_info = await get_openrouter_quota_info(chat_id)
    except Exception as e:
        logger.warning("Gagal mengambil info kuota OpenRouter: %s", str(e))
        openrouter_info = {
            "active_model": "Belum dikonfigurasi",
            "active_model_remaining_requests": "N/A",
            "active_model_remaining_tokens": "N/A",
            "next_model": "N/A",
            "models_status": [],
            "remaining_in_rotation": 0,
            "total_requests_today": 0,
            "fallback_queue": ["Groq API", "Gemini API"],
        }

    active_model = openrouter_info.get("active_model", "N/A")
    active_req_rem = openrouter_info.get("active_model_remaining_requests", "N/A")
    active_tok_rem = openrouter_info.get("active_model_remaining_tokens", "N/A")
    next_model = openrouter_info.get("next_model", "N/A")
    rem_models = openrouter_info.get("remaining_in_rotation", 0)
    total_reqs = openrouter_info.get("total_requests_today", 0)
    models_status = openrouter_info.get("models_status", [])

    status_lines = []
    for item in models_status:
        m_name = item.get("model", "")
        m_stat = item.get("status", "")
        m_usage = item.get("usage", 0)
        status_lines.append(f"  • [{item.get('order')}] {m_name} — {m_stat} ({m_usage} req hari ini)")

    status_str = "\n".join(status_lines) if status_lines else "  • (Tidak ada model terdaftar)"

    return {
        "date": format_date_indonesian(datetime.now().strftime("%Y-%m-%d")),
        "openrouter_active_model": active_model,
        "openrouter_active_model_remaining_requests": active_req_rem,
        "openrouter_active_model_remaining_tokens": active_tok_rem,
        "openrouter_next_model": next_model,
        "openrouter_remaining_models_before_fallback": rem_models,
        "openrouter_total_requests_today": total_reqs,
        "openrouter_summary": (
            f"🌐 OpenRouter Model Rotation (Jalur Utama Chat & Fitur Umum):\n"
            f"  - Model Aktif: {active_model}\n"
            f"  - Sisa Request Model Aktif: {active_req_rem}\n"
            f"  - Sisa Token Model Aktif: {active_tok_rem}\n"
            f"  - Model Selanjutnya Jika Rotasi: {next_model}\n"
            f"  - Total Pemakaian OpenRouter Hari Ini: {total_reqs} request\n"
            f"  - Sisa Model Antrean Sebelum Fallback: {rem_models} model\n"
            f"  - Daftar Rotasi Model:\n{status_str}"
        ),
        "groq_fast_path": (
            f"⚡ Groq API (Cadangan Fast Path): {groq_used:,} / {GROQ_LIMIT:,} token "
            f"({round((groq_used / GROQ_LIMIT) * 100, 1)}%), sisa {groq_remaining:,} token"
        ),
        "gemini_slow_path": (
            f"🛠️ Gemini API (Cadangan Slow Path): {gemini_used:,} / {GEMINI_LIMIT:,} token "
            f"({round((gemini_used / GEMINI_LIMIT) * 100, 1)}%), sisa {gemini_remaining:,} token"
        ),
        "fallback_queue_info": "Cadangan jika semua model OpenRouter limit: 1. Groq API -> 2. Gemini API",
        "groq_tokens_used": groq_used,
        "groq_tokens_remaining": groq_remaining,
        "total_tokens_used_today": gemini_used + groq_used,
    }




async def execute_send_voice_message(chat_id: int, text: str) -> dict[str, Any]:
    """
    Mengirim pesan suara (voice note) dengan suara ElevenLabs TTS.
    Memeriksa kuota gratis bulanan (10.000 karakter/bulan).
    """
    MONTHLY_CHAR_LIMIT = 10_000

    if not text or not text.strip():
        return {"error": "Teks untuk voice message kosong."}

    char_count = len(text)

    # 1. Cek kuota bulanan
    used_chars = await get_monthly_tts_usage(chat_id)
    if used_chars + char_count > MONTHLY_CHAR_LIMIT:
        return {
            "quota_exceeded": True,
            "message": (
                "Layanan pesan suara telah mencapai batas kuota penggunaan bulanan."
            ),
        }

    # Lazy import voice module (mengurangi cold start)
    from src.voice import (
        generate_elevenlabs_tts,
        send_chat_action_record_voice,
        send_voice_note_to_telegram,
    )

    # 2. Kirim chat action "record_voice" agar Telegram menampilkan indikator merekam
    await send_chat_action_record_voice(chat_id)

    # 3. Generate audio via ElevenLabs
    try:
        audio_bytes = await generate_elevenlabs_tts(text)
    except Exception as e:
        logger.error("Failed to generate TTS: %s", str(e))
        return {
            "error": "Failed to generate TTS audio",
            "message": f"Gagal menghasilkan pesan suara ({str(e)}).",
        }

    # 4. Simpan pemakaian karakter ke KV
    await save_tts_usage(chat_id, char_count)

    # 5. Kirim voice note ke Telegram
    sent = await send_voice_note_to_telegram(
        chat_id=chat_id,
        audio_bytes=audio_bytes,
        caption="🎙️ Pesan suara dari Oline",
    )

    if sent:
        return {
            "status": "success",
            "text": text,
            "message": "Voice note berhasil dikirim langsung ke chat Telegram pengguna.",
        }
    else:
        return {
            "error": "Failed to send voice to Telegram",
            "message": "Gagal mengirimkan voice note ke Telegram 😢 Coba lagi nanti ya.",
        }


async def search_internet(query: str) -> dict[str, Any]:
    """
    Mencari informasi terkini di internet menggunakan DDGS (DuckDuckGo Search).
    """
    try:
        def _do_search():
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=5))

        results = await asyncio.to_thread(_do_search)
        await asyncio.sleep(2)

        if not results:
            return {"message": "Informasi yang dicari tidak ditemukan."}

        snippets = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            if title and body:
                snippets.append(f"{title}: {body} (Sumber: {href})")
            elif title or body:
                snippets.append(f"{title or body} (Sumber: {href})")

        return {"results": "\n".join(snippets)}
    except Exception as e:
        logger.error("DuckDuckGo search error: %s", str(e))
        return {"error": "Gagal mengakses koneksi internet. Silakan coba beberapa saat lagi."}


def _get_top_movers(is_gainer: bool = True, limit: int = 3) -> str:
    """Ambil top gainer atau loser dari saham populer IHSG, dengan fallback."""
    import yfinance as yf
    try:
        tickers = ['BBCA', 'BBRI', 'TLKM', 'ASII', 'UNVR', 'ADRO', 'ANTM', 'ICBP']
        movers = []
        for t in tickers:
            stock = yf.Ticker(t + '.JK')
            data = stock.history(period='1d')
            if not data.empty:
                close = float(data['Close'].iloc[-1])
                prev = float(stock.info.get('previousClose', close))
                pct = ((close - prev) / prev) * 100 if prev else 0.0
                movers.append((t, pct))
        movers.sort(key=lambda x: x[1], reverse=is_gainer)
        return ", ".join([f"{m[0]} ({'+' if m[1]>=0 else ''}{m[1]:.1f}%)" for m in movers[:limit]])
    except Exception as e:
        logger.warning("Error fetching top movers: %s", str(e))
        return ""


async def get_stock_price(ticker: str) -> dict[str, Any]:
    """
    Mengecek harga terkini dan perubahan harian suatu saham Indonesia via yfinance.
    """
    import yfinance as yf
    import time

    ticker_clean = ticker.upper().strip()
    cache_key = f"cache:stock:{ticker_clean}"
    try:
        from src.kv import get_cache
        cached_val = await get_cache(cache_key)
        if cached_val:
            return json.loads(cached_val)
    except Exception:
        pass

    full_ticker = ticker_clean
    if not full_ticker.endswith('.JK') and full_ticker.isalpha() and len(full_ticker) == 4:
        full_ticker += '.JK'

    def _fetch():
        time.sleep(0.5)
        stock = yf.Ticker(full_ticker)
        data = stock.history(period='1d')
        if data.empty:
            return {"error": f"Data {ticker_clean} kosong. Mungkin kode salah atau market lagi tutup ya~"}

        latest = float(data['Close'].iloc[-1])
        prev_close = float(stock.info.get('previousClose', latest))
        change = latest - prev_close
        change_pct = (change / prev_close) * 100 if prev_close else 0.0

        formatted = (
            f"{ticker_clean} sekarang Rp {latest:,.0f} "
            f"({'📈 +' if change >= 0 else '📉 '}{change:,.0f}, "
            f"{'+' if change >= 0 else ''}{change_pct:.2f}%)"
        )
        return {
            "ticker": ticker_clean,
            "latest_price": latest,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "formatted_result": formatted,
        }

    try:
        res = await asyncio.to_thread(_fetch)
        if isinstance(res, dict) and "error" not in res:
            try:
                from src.kv import set_cache
                await set_cache(cache_key, json.dumps(res), ttl_seconds=600)
            except Exception:
                pass
        return res
    except Exception as e:
        logger.error("Error getting stock price for %s: %s", ticker, str(e))
        return {"error": f"Gagal ambil data {ticker_clean}: {str(e)}"}


async def get_market_summary() -> dict[str, Any]:
    """
    Mengecek ringkasan pergerakan IHSG hari ini dan top movers via yfinance.
    """
    import yfinance as yf
    import time

    def _fetch():
        time.sleep(0.5)
        ihsg = yf.Ticker('^JKSE')
        data = ihsg.history(period='1d')
        if data.empty:
            return {"error": "Data IHSG belum tersedia hari ini."}

        latest = float(data['Close'].iloc[-1])
        prev_close = float(ihsg.info.get('previousClose', latest))
        change = latest - prev_close
        change_pct = (change / prev_close) * 100 if prev_close else 0.0

        lines = [
            f"IHSG: Rp {latest:,.0f} "
            f"({'📈 +' if change >= 0 else '📉 '}{change:,.0f}, "
            f"{'+' if change >= 0 else ''}{change_pct:.2f}%)"
        ]

        top_gainer = _get_top_movers(is_gainer=True)
        top_loser = _get_top_movers(is_gainer=False)
        if top_gainer:
            lines.append(f"Top Gainer: {top_gainer}")
        if top_loser:
            lines.append(f"Top Loser: {top_loser}")

        return {
            "index_name": "IHSG",
            "latest_price": latest,
            "change": change,
            "change_pct": change_pct,
            "top_gainer": top_gainer,
            "top_loser": top_loser,
            "formatted_summary": "\n".join(lines),
        }

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.error("Error getting market summary: %s", str(e))
        return {"error": f"Gagal ambil data IHSG: {str(e)}"}


async def execute_create_drive_folder(folder_name: str) -> dict[str, Any]:
    """Membuat folder baru di Google Drive (Database Oline)."""
    try:
        from src.drive import create_folder, get_drive_service
        service = get_drive_service()
        folder_id, is_new = await asyncio.to_thread(create_folder, service, folder_name)
        if is_new:
            return {
                "status": "success",
                "folder_name": folder_name,
                "message": f"Folder '{folder_name}' berhasil dibuat di Database Oline! 📂",
            }
        else:
            return {
                "status": "exists",
                "folder_name": folder_name,
                "message": f"Folder '{folder_name}' sudah ada di Database Oline.",
            }
    except Exception as e:
        logger.error("Error in create_drive_folder: %s", str(e))
        return {"error": f"Gagal membuat folder '{folder_name}': {str(e)}"}


async def execute_list_drive_files(folder_name: Optional[str] = None) -> dict[str, Any]:
    """Melihat daftar isi file & folder di Google Drive."""
    try:
        from src.drive import get_drive_service, list_files
        service = get_drive_service()
        items = await asyncio.to_thread(list_files, service, folder_name)
        if not items:
            loc = f"folder '{folder_name}'" if folder_name else "Database Oline"
            return {"message": f"Belum ada file tersimpan pada {loc}."}

        formatted_items = []
        for item in items:
            icon = "📂" if item["is_folder"] else "📄"
            formatted_items.append(f"{icon} {item['name']}")

        return {
            "location": folder_name or "Root Database",
            "total_items": len(items),
            "items": formatted_items,
        }
    except Exception as e:
        logger.error("Error in list_drive_files: %s", str(e))
        return {"error": f"Gagal mengambil daftar file: {str(e)}"}


async def execute_search_drive_files(query: str) -> dict[str, Any]:
    """Mencari file di Google Drive berdasarkan nama."""
    try:
        from src.drive import get_drive_service, search_files
        service = get_drive_service()
        items = await asyncio.to_thread(search_files, service, query)
        if not items:
            return {"message": f"Tidak ditemukan file yang cocok dengan nama '{query}'."}

        formatted_items = []
        for item in items:
            icon = "📂" if item["is_folder"] else "📄"
            formatted_items.append(f"{icon} {item['name']}")

        return {"query": query, "total_found": len(items), "results": formatted_items}
    except Exception as e:
        logger.error("Error in search_drive_files: %s", str(e))
        return {"error": f"Gagal mencari file: {str(e)}"}


async def execute_upload_to_drive(
    chat_id: int = 0, folder_name: Optional[str] = None
) -> dict[str, Any]:
    """Menyimpan file yang baru dikirim pengguna ke Google Drive."""
    try:
        from src.drive import get_drive_service, upload_file
        from src.kv import clear_pending_file, get_pending_file

        pending = await get_pending_file(chat_id)
        if not pending:
            return {
                "error": "File tidak ditemukan",
                "message": (
                    "Tidak ditemukan file baru yang dikirimkan. "
                    "Silakan kirimkan kembali file atau foto yang ingin disimpan."
                ),
            }

        service = get_drive_service()
        result = await asyncio.to_thread(
            upload_file,
            service,
            pending["file_name"],
            pending["file_bytes"],
            pending["mime_type"],
            folder_name,
        )

        await clear_pending_file(chat_id)

        target_dest = f"folder '{folder_name}'" if folder_name else "Database Oline"
        return {
            "status": "success",
            "file_name": result["name"],
            "destination": target_dest,
            "web_link": result.get("web_link", ""),
            "message": f"File '{result['name']}' berhasil tersimpan rapi di {target_dest}! 📁✨",
        }
    except Exception as e:
        logger.error("Error in upload_to_drive: %s", str(e))
        return {"error": f"Gagal mengunggah file ke Google Drive: {str(e)}"}


async def execute_download_from_drive(
    chat_id: int = 0, file_name: str = "", folder_name: Optional[str] = None
) -> dict[str, Any]:
    """Mendownload file dari Google Drive dan mengirimkannya ke Telegram."""
    try:
        from src.drive import download_file, get_drive_service, list_files, search_files

        service = get_drive_service()

        matching_files = (
            await asyncio.to_thread(list_files, service, folder_name)
            if folder_name
            else await asyncio.to_thread(search_files, service, file_name)
        )

        target_file = None
        for item in matching_files:
            if not item["is_folder"] and file_name.lower() in item["name"].lower():
                target_file = item
                break

        if not target_file and matching_files:
            for item in matching_files:
                if not item["is_folder"]:
                    target_file = item
                    break

        if not target_file:
            return {"error": f"File '{file_name}' tidak ditemukan di Drive 😢"}

        file_bytes, real_name, mime_type = await asyncio.to_thread(
            download_file, service, target_file["id"]
        )

        from src.bot import send_drive_file_to_telegram

        sent = await send_drive_file_to_telegram(
            chat_id=chat_id, file_bytes=file_bytes, file_name=real_name, mime_type=mime_type
        )

        if sent:
            return {
                "status": "success",
                "file_name": real_name,
                "message": f"File '{real_name}' udah Oline kirim langsung ke chat ini yaa! 📄✨",
            }
        else:
            return {"error": f"Gagal mengirimkan file '{real_name}' ke Telegram."}

    except Exception as e:
        logger.error("Error in download_from_drive: %s", str(e))
        return {"error": f"Gagal mengambil file dari Google Drive: {str(e)}"}


# ============================================================
# Location & OpenStreetMap Helper Functions
# ============================================================


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Menghitung jarak garis lurus (Haversine formula) dalam km antar 2 titik koordinat."""
    R = 6371.0  # km
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def overpass_query(lat: float, lon: float, category: str, radius_m: int = 2000) -> list[dict]:
    """Melakukan query ke Overpass API (OpenStreetMap) untuk mencari node POI."""
    tag_map = {
        "cafe": "amenity=cafe",
        "kafe": "amenity=cafe",
        "restaurant": "amenity=restaurant",
        "restoran": "amenity=restaurant",
        "toko buku": "shop=books",
        "bookstore": "shop=books",
        "mall": "shop=mall",
        "bar": "amenity=bar",
        "minimarket": "shop=convenience",
        "supermarket": "shop=supermarket",
        "spbu": "amenity=fuel",
        "pom bensin": "amenity=fuel",
        "apotek": "amenity=pharmacy",
        "rumah sakit": "amenity=hospital",
        "bank": "amenity=bank",
        "atm": "amenity=atm",
    }
    cat_lower = category.lower().strip()
    tag = tag_map.get(cat_lower, f"amenity={cat_lower}")

    query = f"""
    [out:json][timeout:10];
    node[{tag}](around:{radius_m},{lat},{lon});
    out body 10;
    """
    try:
        response = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            headers={"User-Agent": "OlineBot/1.0"},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("elements", [])
    except Exception as e:
        logger.error("Overpass API query error: %s", str(e))
        return []


async def get_nearby_places(
    chat_id: int = 0, category: str = "", radius_km: float = 2.0
) -> dict[str, Any]:
    """Mencari tempat terdekat dari lokasi pengguna yang tersimpan."""
    if not category:
        return {"error": "Kategori tempat harus diisi (misal: 'cafe', 'toko buku', 'restoran')."}

    loc = await get_user_location(chat_id)
    if not loc:
        return {
            "error": "Lokasi belum disimpan",
            "message": (
                "Lokasi Anda belum tersimpan. "
                "Silakan kirimkan titik lokasi Anda via Telegram (tombol lampiran > Lokasi) 📍"
            ),
        }

    lat, lon = loc["lat"], loc["lon"]
    radius_m = int(radius_km * 1000)

    elements = await asyncio.to_thread(overpass_query, lat, lon, category, radius_m)
    if not elements:
        return {
            "message": f"Tidak ditemukan {category} dalam radius {radius_km} km dari lokasi Anda."
        }

    results = []
    for p in elements:
        plat = p.get("lat", lat)
        plon = p.get("lon", lon)
        dist = haversine(lat, lon, plat, plon)
        tags = p.get("tags", {})
        name = tags.get("name") or tags.get("brand") or "Tanpa Nama"
        street = tags.get("addr:street", "")
        city_tag = tags.get("addr:city", "")
        address = ", ".join(filter(None, [street, city_tag])) or "Alamat tidak spesifik"
        results.append({
            "name": name,
            "distance_km": round(dist, 2),
            "address": address,
            "lat": plat,
            "lon": plon,
        })

    results.sort(key=lambda x: x["distance_km"])
    return {
        "category": category,
        "total_found": len(results),
        "places": results[:5],
    }


async def search_places_by_city(city: str, category: str) -> dict[str, Any]:
    """Mencari tempat berdasarkan nama kota/area dan kategori via Nominatim & Overpass API."""
    def _geocode_and_search():
        try:
            geo_resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": city, "format": "json", "limit": 1},
                headers={"User-Agent": "OlineBot/1.0"},
                timeout=10.0,
            )
            geo_resp.raise_for_status()
            geo_data = geo_resp.json()
            if not geo_data:
                return {"error": f"Kota/area '{city}' tidak ditemukan."}

            lat = float(geo_data[0]["lat"])
            lon = float(geo_data[0]["lon"])
            display_name = geo_data[0].get("display_name", city)

            elements = overpass_query(lat, lon, category, radius_m=5000)
            if not elements:
                return {"message": f"Tidak ditemukan {category} di area {city}."}

            results = []
            for p in elements[:5]:
                tags = p.get("tags", {})
                name = tags.get("name") or tags.get("brand") or "Tanpa Nama"
                street = tags.get("addr:street", "")
                address = street if street else "Alamat tidak spesifik"
                results.append({
                    "name": name,
                    "address": address,
                })

            return {
                "city": city,
                "display_name": display_name,
                "category": category,
                "total_found": len(results),
                "places": results,
            }
        except Exception as e:
            logger.error("Error in search_places_by_city: %s", str(e))
            return {"error": f"Gagal mencari tempat di {city}: {str(e)}"}

    return await asyncio.to_thread(_geocode_and_search)


async def execute_code(language: str, code: str) -> dict[str, Any]:
    """
    Eksekusi kode via Piston API (dengan fallback safe Python execution jika Piston whitelist-only / 401).
    Return output atau error dalam format dictionary.
    """
    if not language or not code:
        return {"error": "Bahasa dan kode harus diisi."}

    lang_lower = language.lower().strip()
    payload = {
        "language": lang_lower,
        "version": "*",
        "files": [{"content": code}],
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(PISTON_EXECUTE_URL, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                run = data.get("run", {})
                stdout = (run.get("stdout") or "").strip()
                stderr = (run.get("stderr") or "").strip()
                output = (run.get("output") or "").strip()

                MAX_LEN = 1500
                if len(stdout) > MAX_LEN:
                    stdout = stdout[:MAX_LEN] + "\n...(output dipotong)"
                if len(stderr) > MAX_LEN:
                    stderr = stderr[:MAX_LEN] + "\n...(error dipotong)"
                if len(output) > MAX_LEN:
                    output = output[:MAX_LEN] + "\n...(output dipotong)"

                return {
                    "language": language,
                    "stdout": stdout,
                    "stderr": stderr,
                    "output": output or stdout,
                    "exit_code": run.get("code", 0),
                }
    except Exception as e:
        logger.warning("Piston API request error: %s", str(e))

    # Fallback untuk Python jika Piston API whitelist-only (401) / error
    if lang_lower in ("python", "py", "python3"):
        try:
            import contextlib
            import io

            buffer = io.StringIO()
            safe_builtins = {
                "print": print, "range": range, "len": len, "str": str, "int": int,
                "float": float, "list": list, "dict": dict, "set": set, "tuple": tuple,
                "sum": sum, "max": max, "min": min, "abs": abs, "round": round,
                "sorted": sorted, "enumerate": enumerate, "zip": zip, "map": map,
                "filter": filter, "bool": bool, "type": type, "isinstance": isinstance,
                "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
            }
            globals_dict = {"__builtins__": safe_builtins}
            with contextlib.redirect_stdout(buffer):
                exec(code, globals_dict)

            out_str = buffer.getvalue().strip()
            return {
                "language": "python",
                "stdout": out_str or "(kode berhasil dijalankan tanpa output stdout)",
                "stderr": "",
                "output": out_str or "(kode berhasil dijalankan)",
                "exit_code": 0,
            }
        except Exception as py_err:
            return {
                "language": "python",
                "stdout": "",
                "stderr": f"{type(py_err).__name__}: {str(py_err)}",
                "output": f"{type(py_err).__name__}: {str(py_err)}",
                "exit_code": 1,
            }

    return {
        "error": (
            f"Public Piston API saat ini memerlukan whitelist per 2026. "
            f"Untuk bahasa {language}, silakan konfigurasikan Piston instance terdedikasi."
        )
    }


async def deploy_to_vercel(
    project_name: str, files: list[dict[str, Any]], is_update: bool = False
) -> dict[str, Any]:
    """
    Mendeploy file statis (HTML, CSS, JS) ke Vercel via REST API v13.
    Hanya mengembalikan status SUKSES jika Vercel API benar-benar mengembalikan URL .vercel.app yang valid.
    """
    token = os.environ.get("VERCEL_API_TOKEN", "").strip()
    if not token:
        return {
            "status": "error",
            "result_code": "ERROR",
            "error": "ERROR: Token Vercel (VERCEL_API_TOKEN) belum dikonfigurasi.",
            "url": None,
        }

    if not project_name or not files:
        return {
            "status": "error",
            "result_code": "ERROR",
            "error": "ERROR: Nama project dan daftar file tidak boleh kosong.",
            "url": None,
        }

    clean_name = re.sub(r"[^a-z0-9-]", "", project_name.lower().replace(" ", "-")).strip("-")
    if not clean_name:
        clean_name = "oline-app"

    slug = clean_name

    # Defensive parsing untuk parameter `files`
    if isinstance(files, str):
        try:
            files = json.loads(files)
        except Exception:
            try:
                import ast
                files = ast.literal_eval(files)
            except Exception:
                files = []

    # Jika `files` berupa dict:
    if isinstance(files, dict):
        if "filename" in files or "content" in files:
            files = [files]
        else:
            converted = []
            for fname, fcontent in files.items():
                converted.append({"filename": fname, "content": fcontent})
            files = converted

    if not isinstance(files, list):
        files = []

    file_payload = []
    for f in files:
        if isinstance(f, dict):
            fname = f.get("filename") or f.get("file") or f.get("name")
            fdata = f.get("content") or f.get("data") or f.get("code") or ""
            if fname:
                file_payload.append({
                    "file": str(fname).strip(),
                    "data": str(fdata),
                })

    if not file_payload:
        return {
            "status": "error",
            "result_code": "ERROR",
            "error": (
                "ERROR: Format parameter 'files' tidak valid atau kosong. "
                "Pastikan mengirimkan daftar file dengan format "
                "[{\"filename\": \"index.html\", \"content\": \"...\"}]"
            ),
            "url": None,
        }

    payload = {
        "name": slug,
        "files": file_payload,
        "projectSettings": {"framework": None},
        "target": "production",
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post("https://api.vercel.com/v13/deployments", json=payload, headers=headers)
            if resp.status_code in (200, 201):
                data = resp.json()
                raw_url = data.get("url", "")
                if raw_url.startswith("//"):
                    live_url = "https:" + raw_url
                elif not raw_url.startswith("http"):
                    live_url = f"https://{raw_url}"
                else:
                    live_url = raw_url

                # Verifikasi format URL
                if live_url.startswith("https://") and ".vercel.app" in live_url:
                    return {
                        "status": "success",
                        "result_code": "SUKSES",
                        "project_name": slug,
                        "url": live_url,
                        "message": f"SUKSES: Deployment berhasil! Website live di: {live_url}",
                    }
                else:
                    return {
                        "status": "error",
                        "result_code": "ERROR",
                        "error": "ERROR: Respons Vercel API tidak mengandung URL valid.",
                        "url": None,
                    }
            else:
                err_text = resp.text[:250]
                logger.error("Vercel API error (Status %d): %s", resp.status_code, err_text)
                return {
                    "status": "error",
                    "result_code": "ERROR",
                    "error": f"ERROR: Vercel API status {resp.status_code} - {err_text}",
                    "url": None,
                }
    except httpx.TimeoutException:
        return {
            "status": "error",
            "result_code": "ERROR",
            "error": "ERROR: Deployment ke Vercel mengalami timeout (20 detik).",
            "url": None,
        }
    except Exception as e:
        logger.error("Error in deploy_to_vercel: %s", str(e))
        return {
            "status": "error",
            "result_code": "ERROR",
            "error": f"ERROR: Error saat deploy ke Vercel: {str(e)}",
            "url": None,
        }


async def list_vercel_deployments() -> dict[str, Any]:
    """
    Mengambil daftar deployment yang ada di Vercel via REST API v13.
    """
    token = os.environ.get("VERCEL_API_TOKEN", "").strip()
    if not token:
        return {"error": "VERCEL_API_TOKEN belum dikonfigurasi di environment variables."}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://api.vercel.com/v13/deployments", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                raw_deployments = data.get("deployments", [])
                if not raw_deployments:
                    return {"message": "Belum ada deployment di Vercel."}

                deployments = []
                for d in raw_deployments[:10]:
                    raw_url = d.get("url", "")
                    url = f"https://{raw_url}" if raw_url and not raw_url.startswith("http") else raw_url
                    deployments.append({
                        "id": d.get("uid") or d.get("id", ""),
                        "name": d.get("name", "tanpa nama"),
                        "url": url,
                        "created_at": d.get("created"),
                    })

                return {
                    "status": "success",
                    "total": len(deployments),
                    "deployments": deployments,
                }
            else:
                err_text = resp.text[:250]
                logger.error("Vercel list deployments API error (Status %d): %s", resp.status_code, err_text)
                return {"error": f"Gagal mengambil daftar deployment (Status {resp.status_code}): {err_text}"}
    except httpx.TimeoutException:
        return {"error": "Gagal mengambil daftar deployment Vercel: Timeout (15 detik)."}
    except Exception as e:
        logger.error("Error in list_vercel_deployments: %s", str(e))
        return {"error": f"Error saat mengambil daftar deployment Vercel: {str(e)}"}


async def delete_vercel_deployment(deployment_id: str) -> dict[str, Any]:
    """
    Menghapus deployment Vercel berdasarkan deployment ID via REST API v13.
    """
    token = os.environ.get("VERCEL_API_TOKEN", "").strip()
    if not token:
        return {"error": "VERCEL_API_TOKEN belum dikonfigurasi di environment variables."}

    if not deployment_id or not str(deployment_id).strip():
        return {"error": "deployment_id tidak boleh kosong."}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    clean_id = str(deployment_id).strip()
    url = f"https://api.vercel.com/v13/deployments/{clean_id}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(url, headers=headers)
            if resp.status_code in (200, 204):
                return {
                    "status": "success",
                    "deployment_id": clean_id,
                    "message": f"Deployment '{clean_id}' berhasil dihapus dari Vercel.",
                }
            else:
                err_text = resp.text[:250]
                logger.error("Vercel delete deployment API error (Status %d): %s", resp.status_code, err_text)
                return {"error": f"Gagal menghapus deployment (Status {resp.status_code}): {err_text}"}
    except httpx.TimeoutException:
        return {"error": "Gagal menghapus deployment Vercel: Timeout (15 detik)."}
    except Exception as e:
        logger.error("Error in delete_vercel_deployment: %s", str(e))
        return {"error": f"Error saat menghapus deployment Vercel: {str(e)}"}


async def search_and_send_image(
    chat_id: int, query: str, max_results: int = 1
) -> dict[str, Any]:
    """
    Mencari gambar via DuckDuckGo Images (dengan fallback ke Wikipedia PageImages),
    mengunduh bytes, dan mengirimkan foto langsung ke Telegram chat pengguna.
    Default: 1 gambar. Jika max_results > 1, mengirimkan sejumlah max_results gambar yang valid.
    """
    if not query or not query.strip():
        return {"error": "Kata kunci pencarian gambar tidak boleh kosong."}

    target_count = min(max(1, max_results), 5)
    image_urls = []

    def _do_ddgs_images():
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                return [r.get("image") for r in ddgs.images(query.strip(), max_results=target_count * 5) if r.get("image")]
        except Exception as e:
            logger.warning("DDGS image search warning: %s", str(e))
            return []

    try:
        ddgs_urls = await asyncio.to_thread(_do_ddgs_images)
        image_urls.extend(ddgs_urls)
    except Exception as e:
        logger.warning("DDGS thread error: %s", str(e))

    # Fallback ke Wikipedia PageImages API jika DDGS kosong / terblokir DNS ISP
    if not image_urls:
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                headers = {"User-Agent": "OlineBot/1.0"}
                wiki_resp = await client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "format": "json",
                        "generator": "search",
                        "gsrsearch": query.strip(),
                        "gsrlimit": target_count * 3,
                        "prop": "pageimages",
                        "pithumbsize": 800,
                    },
                    headers=headers,
                )
                if wiki_resp.status_code == 200:
                    pages = wiki_resp.json().get("query", {}).get("pages", {})
                    for page in pages.values():
                        thumb = page.get("thumbnail", {}).get("source")
                        if thumb:
                            image_urls.append(thumb)
        except Exception as wiki_err:
            logger.warning("Wikipedia image fallback error: %s", str(wiki_err))

    if not image_urls:
        return {"message": f"Tidak ditemukan gambar yang cocok untuk '{query}'."}

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    sent_count = 0

    for img_url in image_urls:
        if sent_count >= target_count:
            break

        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(img_url)
                if resp.status_code != 200:
                    continue

                img_bytes = resp.content
                if not img_bytes or len(img_bytes) > 10 * 1024 * 1024:
                    continue

            if token and chat_id:
                from telegram import Bot
                bot = Bot(token=token)
                caption = f"🖼️ {query.strip()}" if target_count == 1 else f"🖼️ {query.strip()} ({sent_count + 1}/{target_count})"
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=img_bytes,
                    caption=caption,
                )
                sent_count += 1
            else:
                sent_count += 1

        except Exception as err:
            logger.warning("Failed to fetch/send image candidate %s: %s", img_url, str(err))
            continue

    if sent_count > 0:
        return {
            "status": "success",
            "query": query.strip(),
            "sent_count": sent_count,
            "message": f"Berhasil mengirimkan {sent_count} gambar '{query.strip()}' langsung ke chat Telegram pengguna.",
        }

    return {"error": f"Gagal mengunduh atau mengirimkan gambar untuk '{query}'. Coba kata kunci lain ya."}


async def preview_with_codepen(
    title: str, html: str, css: str = "", js: str = ""
) -> dict[str, Any]:
    """
    Mengunggah kode HTML/CSS/JS ke JSFiddle / CodePen Preview Service
    dan mengembalikan URL preview (SUKSES: https://jsfiddle.net/... atau https://codepen.io/...).
    JANGAN mendeploy ke Vercel di fungsi ini.
    """
    if not title or not title.strip():
        title = "Landing Page Oline"
    if not html or not html.strip():
        return {
            "status": "error",
            "result_code": "ERROR",
            "error": "ERROR: Kode HTML tidak boleh kosong.",
            "url": None,
        }

    clean_title = title.strip()
    clean_html = html.strip()
    clean_css = (css or "").strip()
    clean_js = (js or "").strip()

    # 1. Primary Engine: JSFiddle API (Sangat stabil & mengembalikan real preview URL)
    try:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            BeautifulSoup = None

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=15.0) as client:
            r1 = await client.get("https://jsfiddle.net/")
            csrf = client.cookies.get("csrftoken") or ""
            payload = {}

            if BeautifulSoup and r1.status_code == 200:
                soup = BeautifulSoup(r1.text, "html.parser")
                form = soup.find("form")
                if form:
                    for inp in form.find_all("input"):
                        name = inp.get("name")
                        if not name:
                            continue
                        itype = inp.get("type")
                        if itype in ("radio", "checkbox"):
                            if inp.get("checked") is not None:
                                payload[name] = inp.get("value", "")
                        else:
                            payload[name] = inp.get("value", "")
                    for sel in form.find_all("select"):
                        name = sel.get("name")
                        if not name:
                            continue
                        selected_opt = sel.find("option", selected=True) or sel.find("option")
                        if selected_opt:
                            payload[name] = selected_opt.get("value", "")

            payload.update({
                "title": clean_title,
                "description": "Generated by Oline AI",
                "code_html": clean_html,
                "code_css": clean_css,
                "code_js": clean_js,
            })
            if "doctype" not in payload or not payload["doctype"]:
                payload["doctype"] = "3"

            headers_save = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "X-CSRFToken": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://jsfiddle.net/",
            }

            r2 = await client.post("https://jsfiddle.net/_save/", data=payload, headers=headers_save)
            if r2.status_code in (200, 201):
                data = r2.json()
                preview_url = data.get("url") or f"https://jsfiddle.net/{data.get('slug')}/"
                if preview_url and preview_url.startswith("http") and "jsfiddle.net" in preview_url:
                    logger.info("JSFiddle preview successfully created: %s", preview_url)
                    return {
                        "status": "success",
                        "result_code": "SUKSES",
                        "url": preview_url,
                        "message": f"SUKSES: Preview JSFiddle berhasil dibuat! Buka link ini untuk melihat: {preview_url}",
                    }
    except Exception as jsf_err:
        logger.warning("JSFiddle API preview failed: %s. Trying CodePen fallback...", str(jsf_err))

    # 2. Fallback Engine: CodePen API
    payload = {
        "title": clean_title,
        "html": clean_html,
        "css": clean_css,
        "js": clean_js,
    }
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                "https://codepen.io/pen/define",
                headers=headers,
                data={"data": json.dumps(payload)},
                follow_redirects=False,
            )
            if resp.status_code in (200, 302, 301):
                location = resp.headers.get("location") or ""
                if location.startswith("/"):
                    preview_url = f"https://codepen.io{location}"
                elif location.startswith("http"):
                    preview_url = location
                else:
                    preview_url = ""

                if preview_url and preview_url != "https://codepen.io/pen/define":
                    return {
                        "status": "success",
                        "result_code": "SUKSES",
                        "url": preview_url,
                        "message": f"SUKSES: Preview CodePen berhasil dibuat! Buka link ini untuk melihat: {preview_url}",
                    }
    except Exception as cp_err:
        logger.warning("CodePen prefill API failed: %s", str(cp_err))

    return {
        "status": "error",
        "result_code": "ERROR",
        "error": "ERROR: Gagal membuat link preview di JSFiddle dan CodePen.",
        "url": None,
    }


# --- Lazy Wrapper Functions untuk Notion (mengurangi cold start) ---

async def _lazy_save_note_to_notion(**kwargs):
    """Lazy wrapper: import src.notion hanya saat tool ini dipanggil."""
    from src.notion import save_note_to_notion
    return await save_note_to_notion(**kwargs)


async def _lazy_save_memory_to_notion(**kwargs):
    """Lazy wrapper: import src.notion hanya saat tool ini dipanggil."""
    from src.notion import save_memory_to_notion
    res = await save_memory_to_notion(**kwargs)
    if isinstance(res, str):
        return {"status": "success", "message": res} if "berhasil" in res.lower() else {"error": res}
    return res


async def _lazy_add_notion_property(**kwargs):
    """Lazy wrapper: import src.notion hanya saat tool ini dipanggil."""

    from src.notion import add_notion_property
    return await add_notion_property(**kwargs)


# --- Lazy Wrapper Functions untuk Neo4j (mengurangi cold start) ---

async def _lazy_simpan_aktivitas_neo4j(chat_id: int = 0, **kwargs):
    """Lazy wrapper: import src.neo4j_client hanya saat tool ini dipanggil."""
    from src.neo4j_client import simpan_aktivitas
    aksi = kwargs.get("aksi", "")
    objek = kwargs.get("objek", "")
    if not aksi or not objek:
        return {"status": "error", "message": "Parameter 'aksi' dan 'objek' wajib diisi."}
    success = await simpan_aktivitas(str(chat_id), aksi, objek)
    if success:
        return {"status": "success", "message": f"Aktivitas '{aksi}' pada '{objek}' berhasil disimpan ke graph."}
    return {"status": "error", "message": "Gagal menyimpan aktivitas. Neo4j mungkin belum dikonfigurasi."}


async def _lazy_cari_aktivitas_neo4j(chat_id: int = 0, **kwargs):
    """Lazy wrapper: import src.neo4j_client hanya saat tool ini dipanggil."""
    from src.neo4j_client import cari_aktivitas
    limit = kwargs.get("limit", 10)
    if not isinstance(limit, int) or limit < 1:
        limit = 10
    aktivitas_list = await cari_aktivitas(str(chat_id), limit=limit)
    if aktivitas_list:
        return {"status": "success", "aktivitas": aktivitas_list, "total": len(aktivitas_list)}
    return {"status": "success", "aktivitas": [], "total": 0, "message": "Belum ada aktivitas yang tercatat."}


def extract_design_info(html: str) -> dict:
    """
    Ekstraksi informasi penting desain dari HTML website (font, warna hex, struktur, hero text).
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return {"title": "", "fonts": [], "colors": [], "structure": [], "hero_text": ""}

    info = {
        "title": soup.title.string.strip() if (soup.title and soup.title.string) else "",
        "fonts": [],
        "colors": [],
        "structure": [],
        "hero_text": "",
    }

    # Fonts
    for css in soup.find_all(["link", "style"]):
        text = css.get("href", "") if css.name == "link" else (css.string or "")
        fonts = re.findall(r'font-family:\s*([^;}]+)', text)
        info["fonts"].extend([f.strip("'\" ") for f in fonts])
    info["fonts"] = list(dict.fromkeys(info["fonts"]))[:5]

    # Colors (Hex codes)
    hex_colors = re.findall(r'#[0-9a-fA-F]{6}', str(soup))
    info["colors"] = list(dict.fromkeys(hex_colors))[:10]

    # Structure
    for tag in ["header", "section", "footer", "nav", "main", "div"]:
        for elem in soup.find_all(tag, class_=True):
            classes = elem.get("class")
            if isinstance(classes, list):
                if any(c.lower() in ["hero", "navbar", "pricing", "gallery", "about", "contact", "footer", "features", "cta", "header"] for c in classes):
                    info["structure"].append(f"{tag}.{'.'.join(classes)}")
    info["structure"] = list(dict.fromkeys(info["structure"]))[:10]

    # Hero text
    hero = soup.find("h1")
    if hero:
        info["hero_text"] = hero.get_text(strip=True)[:150]

    return info


async def search_design_reference(query: str) -> dict[str, Any]:
    """
    Mencari website referensi di internet via DDGS dan menginspeksi desainnya (font, warna, struktur, hero text).
    """
    def _search_and_scrape():
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        search_query = query if "website" in query.lower() else f"{query} website"
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(search_query, max_results=5))
        except Exception as e:
            logger.error("DDGS search error for design reference: %s", str(e))
            return {"error": f"Gagal mencari kandidat website: {str(e)}"}

        if not results:
            return {"error": "Tidak ada kandidat website ditemukan."}

        summaries = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        for r in results:
            url = r.get("href")
            if not url or not url.startswith("http"):
                continue

            try:
                with httpx.Client(timeout=8.0, follow_redirects=True, headers=headers) as client:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        continue

                    info = extract_design_info(resp.text)
                    font_list = ", ".join(info["fonts"]) if info["fonts"] else "tidak terdeteksi"
                    color_list = ", ".join(info["colors"]) if info["colors"] else "tidak terdeteksi"
                    structure_list = ", ".join(info["structure"]) if info["structure"] else "tidak terdeteksi"

                    summaries.append(
                        f"Website: {url}\n"
                        f"Judul: {info['title']}\n"
                        f"Font: {font_list}\n"
                        f"Warna dominan: {color_list}\n"
                        f"Struktur utama: {structure_list}\n"
                        f"Hero text: {info['hero_text']}\n"
                        "---"
                    )
                    if len(summaries) >= 3:
                        break
            except Exception as e:
                logger.warning("Scraping %s failed: %s", url, str(e))
                continue

        if not summaries:
            return {"error": "Gagal mengambil dan mengekstrak data dari kandidat website."}

        return {"status": "success", "results": "\n\n".join(summaries)}

def clean_moondream_response(result: Any) -> str:
    """
    Pembersih hasil output Moondream VLM:
    - Menghilangkan awalan/bagian teknis seperti 'Answer:' atau 'Reasoning:'
    - Merapikan karakter newline dan whitespace.
    """
    if isinstance(result, (tuple, list)) and len(result) > 1:
        text = str(result[1])
    else:
        text = str(result)

    if "Answer:" in text:
        text = text.split("Answer:")[-1].strip()

    if "Reasoning:" in text:
        parts = text.split("Reasoning:")
        text = parts[0].strip() if parts[0].strip() else parts[-1].strip()

    text = text.replace("\\n", "\n").strip()
    return text


def optimize_image_bytes(
    image_bytes: bytes, max_size_bytes: int = 3 * 1024 * 1024, max_dimension: int = 1280
) -> bytes:
    """
    Mengompresi dan meresize gambar jika ukurannya melebihi max_size_bytes (misal > 3MB).
    Dimensi gambar dibatasi hingga max_dimension piksel dan dikompresi ke format JPEG (quality 85).
    Lazy loading `from PIL import Image`.
    """
    if not image_bytes:
        return image_bytes

    if len(image_bytes) <= max_size_bytes:
        return image_bytes

    try:
        import io
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        width, height = img.size
        if width > max_dimension or height > max_dimension:
            if width > height:
                new_width = max_dimension
                new_height = int(height * (max_dimension / width))
            else:
                new_height = max_dimension
                new_width = int(width * (max_dimension / height))
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        out_buf = io.BytesIO()
        img.save(out_buf, format="JPEG", quality=85, optimize=True)
        optimized = out_buf.getvalue()
        logger.info("Gambar dioptimasi: %d bytes -> %d bytes", len(image_bytes), len(optimized))
        return optimized
    except Exception as e:
        logger.warning("Gagal mengompresi gambar: %s, menggunakan bytes asli.", str(e))
        return image_bytes


async def analyze_image(
    image_bytes: bytes,
    question: str = "Describe this image.",
    task: str = "Caption",
) -> str:
    """
    Menganalisis gambar dari bytearray/bytes menggunakan Moondream3 VLM (Primary)
    dengan fallback otomatis ke Moondream2. Menggunakan timeout 25 detik per Space dan logging eksplisit.
    """
    if not image_bytes:
        return "Gagal menganalisis gambar: data gambar kosong."

    # Otomatis kompresi gambar jika > 3MB
    image_bytes = optimize_image_bytes(image_bytes)

    spaces = [
        MOONDREAM_SPACE_1 or "merve/moondream3",
        MOONDREAM_SPACE_2 or "vikhyatk/moondream2",
    ]

    import tempfile

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
            tmp_file.write(image_bytes)
            tmp_path = tmp_file.name

        prompt_text = question if question else ("objects" if task == "Object Detection" else "Describe this image.")

        def _predict_single_space(space_url: str):
            from gradio_client import Client, handle_file
            logger.info("Mencoba analisis gambar via Moondream Space: %s (task: %s)...", space_url, task)
            client = Client(space_url)

            if "moondream3" in space_url.lower():
                raw_result = client.predict(
                    image=handle_file(tmp_path),
                    prompt=prompt_text,
                    task_type=task,
                    max_objects=10,
                    api_name="/detect_objects",
                )
                ans = clean_moondream_response(raw_result)
                if ans:
                    logger.info("Moondream VLM (%s) berhasil mengembalikan hasil (len=%d): %s", space_url, len(ans), ans[:150])
                    return ans
            else:
                try:
                    raw_result = client.predict(
                        img=handle_file(tmp_path),
                        prompt=prompt_text,
                        api_name="/answer_question",
                    )
                    ans = clean_moondream_response(raw_result)
                    if ans:
                        logger.info("Moondream VLM (%s) berhasil mengembalikan hasil (len=%d): %s", space_url, len(ans), ans[:150])
                        return ans
                except Exception as ep_err:
                    logger.warning("Space %s /answer_question gagal: %s, mencoba fallback /answer_question_1...", space_url, str(ep_err))
                    raw_result = client.predict(
                        img=handle_file(tmp_path),
                        prompt=prompt_text,
                        api_name="/answer_question_1",
                    )
                    ans = clean_moondream_response(raw_result)
                    if ans:
                        logger.info("Moondream VLM (%s) berhasil mengembalikan hasil (len=%d): %s", space_url, len(ans), ans[:150])
                        return ans
            return None

        for space_url in spaces:
            if not space_url:
                continue

            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(_predict_single_space, space_url),
                    timeout=25.0,
                )
                if result:
                    return result
            except asyncio.TimeoutError:
                logger.warning("Space %s timeout 25s terlampaui. Mencoba fallback...", space_url)
                continue
            except Exception as space_err:
                logger.warning("Space %s gagal / offline: %s. Mencoba fallback...", space_url, str(space_err))
                continue

        return "Gagal menganalisis gambar: Layanan vision sedang tidak responsif. Silakan coba kembali."

    except Exception as e:
        logger.error("Moondream image analysis error: %s", str(e))
        return "Gagal menganalisis gambar: Layanan vision sedang tidak responsif. Silakan coba kembali."
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    return await asyncio.to_thread(_predict)


async def identify_image_subject(
    image_bytes: bytes, context_hint: str = ""
) -> str:
    """
    Mengidentifikasi objek, orang, tempat, hewan, makanan, kendaraan, tanaman, atau benda apa pun
    dalam gambar dengan menganalisis gambar via Moondream VLM lalu mencari di internet via DuckDuckGo.
    """
    if not image_bytes:
        return "Gagal menganalisis gambar: data gambar kosong."

    prompt = "Describe this image in detail, including type of object, person, place, animal, food, vehicle, plant, or thing."
    deskripsi = await analyze_image(image_bytes, question=prompt, task="Caption")

    if "mataku lagi error" in deskripsi or "Gagal menganalisis" in deskripsi:
        return deskripsi

    hint_clean = (context_hint or "").strip()
    query_hint = re.sub(r"\b(ini|apa|siapa|identifikasi|tolong|dong|ya|kah|apakah)\b", "", hint_clean, flags=re.IGNORECASE).strip()

    search_query = f"{query_hint} {deskripsi[:120]}".strip() if query_hint else deskripsi[:120].strip()

    def _do_ddgs():
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                return list(ddgs.text(search_query, max_results=3))
        except Exception as e:
            logger.warning("DDGS text search error in identify_image_subject: %s", str(e))
            return []

    try:
        results = await asyncio.to_thread(_do_ddgs)
    except Exception as err:
        logger.warning("Thread error in identify_image_subject: %s", str(err))
        results = []

    if not results:
        return f"Deskripsi visual: {deskripsi}. Namun identitas spesifik belum dapat dipastikan secara akurat."

    kandidat = [r.get("title", "") for r in results if r.get("title")]
    candidates_str = ", ".join(kandidat[:2]) if kandidat else "tidak terdeteksi"
    return f"Dari gambar ini, kemungkinan besar adalah {candidates_str}. ({deskripsi})"


async def execute_check_feature_health() -> str:
    """
    Memeriksa kesehatan semua fitur Oline (brief.md).
    Returns formatted string dengan status kesehatan fitur.
    """
    from src.health_check import check_all_features
    from src.kv import get_failure_count, is_feature_enabled

    results = await check_all_features()
    lines = ["🩺 Status Kesehatan Fitur:\n"]

    for feature, ok in results.items():
        enabled = await is_feature_enabled(feature)
        fail_count = await get_failure_count(feature)

        if ok and enabled:
            lines.append(f"✅ {feature}")
        elif not ok and fail_count >= 3:
            lines.append(f"❌ {feature} (gagal {fail_count}x, dinonaktifkan sementara)")
        elif not ok:
            lines.append(f"❌ {feature} (gagal {fail_count}x)")
        elif not enabled:
            lines.append(f"❌ {feature} (dinonaktifkan)")
        else:
            lines.append(f"✅ {feature}")

    return "\n".join(lines)


async def execute_toggle_feature(feature: str, status: bool) -> str:
    """
    Mengaktifkan atau menonaktifkan fitur Oline di Vercel KV (brief.md).
    """
    from src.config import FITUR_LIST
    from src.kv import set_feature_status

    clean_feat = (feature or "").strip().lower()
    if clean_feat not in FITUR_LIST:
        return f"Fitur '{feature}' tidak dikenal. Fitur yang tersedia: {', '.join(FITUR_LIST)}"

    await set_feature_status(clean_feat, status)
    state = "diaktifkan" if status else "dinonaktifkan"
    return f"Fitur '{clean_feat}' berhasil {state}."


# Map nama tool ke executor function
TOOL_EXECUTORS = {
    "get_movie_recommendation": get_movie_recommendation,
    "get_music_recommendation": get_music_recommendation,
    "get_weather_forecast": get_weather_forecast,
    "save_journal_entry": execute_save_journal,
    "get_journal_recap": execute_get_journal_recap,
    "check_ai_quota": check_ai_quota,
    "check_quota": check_ai_quota,
    "check_feature_health": execute_check_feature_health,
    "toggle_feature": execute_toggle_feature,
    "send_voice_message": execute_send_voice_message,
    "search_internet": search_internet,
    "get_stock_price": get_stock_price,
    "get_market_summary": get_market_summary,
    "create_drive_folder": execute_create_drive_folder,
    "list_drive_files": execute_list_drive_files,
    "search_drive_files": execute_search_drive_files,
    "upload_to_drive": execute_upload_to_drive,
    "download_from_drive": execute_download_from_drive,
    "get_nearby_places": get_nearby_places,
    "search_places_by_city": search_places_by_city,
    "execute_code": execute_code,
    "save_note_to_notion": _lazy_save_note_to_notion,
    "save_memory_to_notion": _lazy_save_memory_to_notion,
    "add_notion_property": _lazy_add_notion_property,
    "preview_with_codepen": preview_with_codepen,
    "deploy_to_vercel": deploy_to_vercel,
    "list_vercel_deployments": list_vercel_deployments,
    "delete_vercel_deployment": delete_vercel_deployment,
    "search_and_send_image": search_and_send_image,
    "simpan_aktivitas_neo4j": _lazy_simpan_aktivitas_neo4j,
    "cari_aktivitas_neo4j": _lazy_cari_aktivitas_neo4j,
    "search_design_reference": search_design_reference,
    "analyze_image": analyze_image,
    "identify_image_subject": identify_image_subject,
    "read_vercel_logs": read_vercel_logs,
    "read_github_file": read_github_file,
    "create_github_branch": create_github_branch,
    "update_github_file": update_github_file,
    "create_pull_request": create_pull_request,
}

TOOL_HANDLERS = TOOL_EXECUTORS


def convert_tools_to_openai_format(tool_declarations: list[dict]) -> list[dict]:
    """
    Mengonversi deklarasi tools (format Gemini / dict biasa) ke format OpenAI/Groq function calling.
    """
    if not tool_declarations:
        return []

    openai_tools = []
    for decl in tool_declarations:
        if "type" in decl and "function" in decl:
            openai_tools.append(decl)
        else:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": decl.get("name"),
                    "description": decl.get("description", ""),
                    "parameters": decl.get("parameters", {"type": "object", "properties": {}}),
                },
            })
    return openai_tools


async def execute_tool(
    func_name: str, func_args: dict, chat_id: int = 0
) -> dict[str, Any]:
    """
    Menjalankan fungsi tool berdasarkan nama dan mengembalikan hasilnya.
    Mendukung penyuntikan parameter otomatis (seperti chat_id).
    """
    logger.info("Executing function call: %s with args: %s", func_name, func_args)

    executor = TOOL_EXECUTORS.get(func_name)
    if not executor:
        return {"error": f"Unknown function: {func_name}"}

    args = dict(func_args) if func_args else {}

    # Notifikasi proses berjalan (per brief.md)
    if chat_id and chat_id != 0:
        try:
            from src.utils import notify_process
            tool_notifs = {
                "preview_with_codepen": ("typing", "Preview sedang dibuat..."),
                "deploy_to_vercel": ("typing", "Proses deploy sedang berlangsung... 🚀"),
                "search_design_reference": ("typing", "Mencari referensi desain..."),
                "search_internet": ("typing", "Mencari informasi di internet..."),
                "get_weather_forecast": ("typing", None),
                "get_stock_price": ("typing", None),
                "identify_image_subject": ("typing", "Menganalisis gambar... 🔍"),
            }
            if func_name in tool_notifs:
                act, msg = tool_notifs[func_name]
                await notify_process(chat_id=chat_id, action=act, message=msg)
        except Exception as notif_err:
            logger.warning("Notification trigger failed in execute_tool: %s", str(notif_err))

    if func_name in ("save_journal_entry", "get_journal_recap"):
        args["chat_id"] = chat_id
        if func_name == "save_journal_entry":
            return await executor(
                chat_id=chat_id,
                text=args.get("text", ""),
                date=args.get("date"),
            )
        else:
            return await executor(
                chat_id=chat_id,
                start_date=args.get("start_date"),
                end_date=args.get("end_date"),
            )
    elif func_name in ("check_quota", "check_ai_quota"):
        return await check_ai_quota(chat_id=chat_id)
    elif func_name == "check_feature_health":
        return await execute_check_feature_health()
    elif func_name == "toggle_feature":
        return await execute_toggle_feature(
            feature=args.get("feature", ""),
            status=bool(args.get("status", True)),
        )
    elif func_name == "send_voice_message":
        return await executor(chat_id=chat_id, text=args.get("text", ""))
    elif func_name == "get_nearby_places":
        return await executor(chat_id=chat_id, **args)
    elif func_name in ("upload_to_drive", "download_from_drive", "search_and_send_image"):
        return await executor(chat_id=chat_id, **args)
    elif func_name in ("simpan_aktivitas_neo4j", "cari_aktivitas_neo4j"):
        return await executor(chat_id=chat_id, **args)
    else:
        result = await executor(**args)
        # Auto-log aktivitas penting ke Neo4j & update Task Checkpoint data
        if chat_id and chat_id != 0:
            try:
                from src.kv import get_checkpoint, save_checkpoint
                cp = await get_checkpoint(chat_id)
                if cp and isinstance(cp, dict):
                    cp_data = cp.get("data", {})
                    if func_name == "preview_with_codepen":
                        cp_data["html"] = args.get("html")
                        cp_data["css"] = args.get("css")
                        cp_data["js"] = args.get("js")
                        if isinstance(result, dict) and result.get("url"):
                            cp_data["preview_url"] = result.get("url")
                        elif isinstance(result, str) and "http" in result:
                            cp_data["preview_url"] = result
                        if "preview" not in cp.get("langkah_selesai", []):
                            cp["langkah_selesai"].append("preview")
                    elif func_name == "deploy_to_vercel":
                        if isinstance(result, dict) and result.get("url"):
                            cp_data["deploy_url"] = result.get("url")
                        if "deploy" not in cp.get("langkah_selesai", []):
                            cp["langkah_selesai"].append("deploy")
                    cp["data"] = cp_data
                    await save_checkpoint(chat_id, cp)
            except Exception as cp_err:
                logger.warning("Failed to update checkpoint data in execute_tool: %s", str(cp_err))

        if isinstance(result, dict) and result.get("status") == "success":
            try:
                from src.neo4j_client import auto_log_aktivitas
                if func_name == "deploy_to_vercel" and result.get("result_code") == "SUKSES":
                    await auto_log_aktivitas(chat_id, "deploy", args.get("project_name", "unknown"))
                elif func_name == "preview_with_codepen":
                    await auto_log_aktivitas(chat_id, "preview", args.get("title", "unknown"))
                elif func_name in ("save_note_to_notion", "save_memory_to_notion"):
                    await auto_log_aktivitas(chat_id, "simpan catatan", args.get("title", "unknown"))
            except Exception as auto_log_err:
                logger.warning("Auto-log Neo4j gagal (non-critical): %s", str(auto_log_err))
        return result



