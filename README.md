# Oline Agent 🤖

Oline Agent adalah bot Telegram asisten pribadi berpersona Gen-Z yang cerdas, cepat, dan serba bisa. Dibangun menggunakan **Python 3.10+**, **Google Gemini AI**, **Groq API**, **DeepInfra (DeepSeek V4 Flash)**, **OpenCode CLI**, **PyGithub**, **Neo4j AuraDB**, **Moondream VLM**, **Notion API**, dan dideploy di **Vercel Serverless Functions** + **Render Worker** untuk task berat.

---

## ✨ Fitur Utama

- **⚡ Fast Path (Groq API)** — Respon kilat untuk obrolan santai, sapaan, dan pertanyaan ringan menggunakan model `openai/gpt-oss-20b`.
- **🛠️ Slow Path (Google Gemini API & OpenRouter Rotation)** — Pemrosesan kecerdasan utama dengan rotasi model otomatis (`gemini-3.6-flash`, `gemini-3.5-flash-lite`, OpenRouter Model Rotation) dan Function Calling untuk tugas kompleks.
- **🛡️ Groq Slow Path Fallback** — Jika Gemini down (kuota habis/429/timeout), Oline otomatis fallback ke Groq dengan dukungan *Function Calling* (2-stage OpenAI tool execution) agar fitur bot tidak pernah mati.
- **🤖 Coding Agent (Self-Improving, Plan → Approval → Eksekusi)** — Oline bisa mengubah kodenya sendiri: user kirim perintah ("tambahkan fitur cek resi"), Oline menyusun **plan**, kirim tombol `✅ Setuju` / `✏️ Perbaiki` / `❌ Batalkan`, lalu eksekusi di **Render Worker** via **OpenCode CLI** (DeepSeek V4 Flash / DeepInfra) dengan fallback PyGithub tools. Hasilnya dikirim sebagai PR dengan tombol `🔍 Review` / `✅ Merge ke Main` / `❌ Hapus Branch` — semua keputusan dari Telegram, tanpa buka GitHub.
- **🏭 Render Worker untuk Task Berat** — Landing page & coding agent dijalankan di Web Service Render (bukan serverless) agar bebas timeout Vercel. Delegasi via `/api/delegate` (POST `RENDER_WORKER_URL/process`), hasilnya diperbarui pada **satu bubble Telegram yang sama** (`edit_message_text`) dan callback ke `/api/callback` untuk cleanup KV.
- **⏳ Alur Landing Page Async & Progress Satu Pesan Dinamis** — Pemrosesan pembuatan & revisi landing page secara *background async* (anti-hang/timeout di Vercel). Mengirimkan notifikasi status melalui **SATU pesan Telegram dinamis** yang diperbarui secara bertahap, dengan `task checkpoint` agar bisa lanjut dari langkah terakhir saat gagal.
- **🔍 Fitur Baca Log Vercel Cerdas & Hemat** — Pembacaan runtime log Vercel (`read_vercel_logs`) dengan filter cerdas: mode `error` (log error & warn saja saat tanya error) dan mode `semua` (10 log terakhir) dilengkapi caching Vercel KV 120 detik agar hemat API.
- **🖼️ Analisis Gambar (Moondream VLM)** — Analisis dan deskripsi foto/gambar otomatis dari chat Telegram menggunakan **Moondream3** (`merve/moondream3`) dan **Moondream2** (`vikhyatk/moondream2`) dengan penerjemahan & penggubahan ulang (*auto-translation & rephrasing*) ke Bahasa Indonesia khas Gen-Z.
- **🎨 Search & Inspeksi Referensi Desain Website** — Mencari website referensi di internet (DuckDuckGo) dan menginspeksi elemen desainnya (font, warna hex dominan, struktur layout, hero text `<h1>`) via `BeautifulSoup` & `lxml` sebagai inspirasi landing page.
- **🚀 Landing Page Preview & Vercel Deploy** — Pembuatan landing page modern anti-AI-slop dengan preview otomatis di CodePen (`preview_with_codepen`) dan deployment langsung ke Vercel (`deploy_to_vercel`).
- **🎓 Fitur Akademik / ERINE** — Dukungan riset akademik: ringkas dokumen (docx/pdf), jurnal ilmiah, sitasi APA 7, BibTeX, arxiv/scholar, dan referensi penelitian via integrasi ERINE API.
- **🔑 Cek & Perbarui Token (Token Health Check)** — `check_token_status` menguji semua API key (Groq, Gemini, OpenRouter, Vercel, Notion, GitHub, Google Drive, ERINE, dll.) dengan laporan valid/invalid/tidak dikonfigurasi. `renew_token` memperbarui token runtime via Vercel API (tanpa redeploy, tanpa commit secret).
- **🗺️ Lokasi & Tempat Terdekat** — Cari tempat terdekat (cafe, restoran, SPBU, apotek, ATM, dll.) via OpenStreetMap Overpass & haversine dari lokasi pengguna.
- **🔗 Activity Graph Log (Neo4j AuraDB)** — Penyimpanan & pencarian riwayat aktivitas pengguna dalam bentuk graph (`User` -[`:MELAKUKAN`]-> `Aktivitas`) dengan perekaman otomatis (*auto-log*) pada setiap aksi penting.
- **💻 Eksekusi Kode (Piston API)** — Jalankan potongan kode cepat (Python, JavaScript, C++, Java, dll.) langsung dari chat dengan pemotongan output otomatis max 1500 karakter dan timeout 15 detik.
- **📓 Integrasi Notion Database** — Simpan catatan (judul, isi, kategori, dan tanggal WIB) serta tambah/edit kolom database Notion secara otomatis (via Notion API).
- **⏰ Akurasi Waktu Real-Time (WIB)** — Penyuntikan otomatis hari, tanggal, bulan, tahun, dan jam WIB (UTC+7) ke system prompt di semua jalur AI agar jawaban waktu selalu akurat.
- **🎬 Rekomendasi Film & 🎵 Lagu** — Pencarian rekomendasi film (TMDb API) dan musik (iTunes Search API) berdasarkan genre, mood, atau kata kunci.
- **🌤️ Cek Cuaca** — Informasi cuaca real-time dan prakiraan cuaca 5 hari ke depan untuk berbagai kota (via OpenWeatherMap API).
- **📈 Saham Indonesia & IHSG** — Cek harga saham 4 huruf (BBCA, BBRI, TLKM, BUMI, GOTO, dsb.), ringkasan pergerakan IHSG, serta Top Gainer & Loser (via `yfinance`).
- **🎙️ Pesan Suara (Voice Note)** — Oline bisa bernyanyi, menggombal, atau membaca puisi dalam bentuk Voice Note Telegram bersuara natural (via ElevenLabs TTS).
- **🔍 Search Internet Real-time** — Pencarian berita terkini, fakta terbaru, dan definisi di internet (via DuckDuckGo Search `ddgs`).
- **📂 Google Drive Integration (Database Oline)** — Manajemen folder, listing file, pencarian file, upload foto/dokumen dari Telegram ke Drive, dan download file dari Drive langsung ke Telegram (via Google Drive OAuth 2.0 API).
- **📔 Jurnal Harian** — Pencatatan jurnal harian dan rekap harian/mingguan (via Vercel KV / Upstash Redis).
- **📊 Cek Kuota & Pemakaian API** — Tool `check_quota` untuk memantau sisa kuota harian Groq (Fast Path) dan Gemini (Slow Path) secara transparan.
- **🚨 Smart Rate Limiting** — Pembatasan rate limit 25 req/menit per user dengan pengecekan TTL otomatis di Redis pipeline untuk mencegah kunci permanen.
- **⚙️ Kelola Fitur & Health Check** — Aktifkan/nonaktifkan fitur per pengguna, cek status kesehatan semua fitur, dan monitor error otomatis.

---

## 🛠️ Tech Stack

| Komponen | Teknologi |
|----------|-----------|
| **Bahasa** | Python 3.10+ |
| **Hosting Utama** | Vercel (Serverless Functions) |
| **Hosting Task Berat** | Render Web Service (FastAPI + uvicorn) |
| **Database / KV** | Vercel KV / Upstash Redis (REST API Pipeline) |
| **Graph Database** | Neo4j AuraDB (`neo4j` official driver) |
| **Coding Agent** | OpenCode CLI (`opencode run`, konfigurasi `opencode.json`) + PyGithub fallback |
| **Vision Language Model** | Moondream3 (`merve/moondream3`) + Moondream2 (`vikhyatk/moondream2`) |
| **Git / GitHub Engine** | `PyGithub` (GitHub REST API Client) |
| **HTML Parsing & Inspection** | `beautifulsoup4` & `lxml` |
| **AI Primary Engine** | Google Gemini API (`google-genai` SDK) & OpenRouter API |
| **AI Fast Engine & Fallback** | Groq API (`openai/gpt-oss-20b`) |
| **AI Landing Page Generator & Plan** | DeepInfra API (`DeepSeek-V4-Flash-0731`) |
| **Akademik** | ERINE API |
| **Catatan / Productivity** | Notion API (`https://api.notion.com`) |
| **Eksekusi Kode** | Piston API (`https://emkc.org/api/v2/piston/execute`) |
| **TTS Voice Engine** | ElevenLabs API |
| **Cloud Storage** | Google Drive API (OAuth 2.0) |
| **Integrasi API** | TMDb, OpenWeatherMap, iTunes, yfinance, DuckDuckGo (`ddgs`), OpenStreetMap Overpass, Vercel Logs API |
| **Telegram Framework** | `python-telegram-bot` v22+ (Webhook Mode) |
| **HTTP Client** | `httpx` (async) |

---

## 📁 Struktur Proyek

```
.
├── api/
│   ├── index.py                    # Entrypoint serverless Vercel (webhook Telegram, WSGI)
│   ├── keepalive.py                # Keep-alive warm-up endpoint
│   ├── process_pending.py          # Background processor endpoint untuk pending tasks (cron / fallback)
│   ├── token_check.py              # Endpoint cek status token (tanpa AI)
│   ├── delegate.py                 # Endpoint delegasi task berat ke Render worker
│   └── callback.py                 # Endpoint callback dari worker (cleanup KV, auth X-Worker-Key)
├── src/
│   ├── bot.py                      # Telegram Bot handlers, intent routing, coding agent flow & callback buttons
│   ├── handlers.py                 # Background pending task processor, bubble dinamis (edit_text), plan generator
│   ├── vercel_logs.py              # Vercel Runtime Logs fetcher & AI analyzer dengan KV caching
│   ├── github_tools.py             # PyGithub integration (read, branch, update, PR, merge, delete branch)
│   ├── gemini.py                   # Gemini AI client, model rotation & system prompt context
│   ├── groq.py                     # Groq Fast Path & Slow Path fallback (Function Calling)
│   ├── openrouter.py               # OpenRouter API client & model rotation
│   ├── deepinfra.py                # DeepInfra API client (DeepSeek V4 Flash untuk landing page & plan)
│   ├── tools.py                    # Deklarasi tools, OpenAI format converter & executor registry
│   ├── neo4j_client.py             # Neo4j AuraDB graph database client (simpan/cari aktivitas)
│   ├── notion.py                   # Notion REST API helper (save notes, properties & database ID)
│   ├── drive.py                    # Google Drive API integration helper
│   ├── voice.py                    # ElevenLabs TTS & Telegram Voice Note helper
│   ├── kv.py                       # Vercel KV / Upstash Redis REST helper (pipeline, rate limit, plan state)
│   ├── autocorrect_utils.py        # Normalisasi kata & pembersihan typo
│   ├── personas.py                 # System prompt & kepribadian Gen-Z Oline
│   └── utils.py                    # Helper tanggal, waktu WIB real-time & format Indonesia
├── oline-worker/                   # Render Web Service terpisah (FastAPI) untuk task berat
│   ├── main.py                     # Endpoint /process & /health, eksekusi landing page + coding agent (OpenCode CLI)
│   ├── render_start.sh             # Bootstrap Render: install node/npm/git/opencode CLI bila belum ada
│   ├── Procfile                    # web: bash render_start.sh
│   └── requirements.txt
├── scripts/
│   ├── set_webhook.py              # Script setup & inspeksi Webhook Telegram
│   └── set_commands.py             # Daftarkan daftar command ke Telegram (tombol Menu) / --clear / --info
├── tests/                          # 30+ unit & integration test (stdlib unittest)
├── opencode.json                   # Konfigurasi OpenCode CLI (DeepSeek V4 Flash via DeepInfra)
├── requirements.txt
├── vercel.json
├── .env.example
└── README.md
```

---

## 🚀 Setup & Deployment

### 1. Clone & Install Dependencies

```bash
git clone https://github.com/Dsap09/Oline_Agent.git
cd Oline_Agent
pip install -r requirements.txt
```

### 2. Konfigurasi Environment Variables

Salin file `.env.example` menjadi `.env` lalu lengkapi nilainya:

```bash
cp .env.example .env
```

Isi variabel utama:
- `TELEGRAM_BOT_TOKEN`: Dari [@BotFather](https://t.me/BotFather)
- `GEMINI_API_KEY`: Dari [Google AI Studio](https://aistudio.google.com/apikey)
- `GROQ_API_KEY`: Dari [Groq Console](https://console.groq.com/keys)
- `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO`: Token Fine-grained & repositori dari [GitHub Settings](https://github.com/settings/tokens) (Permissions: Contents & Pull Requests)
- `VERCEL_API_TOKEN`: Dari [Vercel Tokens](https://vercel.com/account/tokens) untuk log runtime & deployment
- `RENDER_WORKER_URL` & `OLINE_WORKER_KEY`: URL worker Render (mis. `https://oline-worker.onrender.com`) dan shared key yang **harus sama** di Vercel & Render
- `DELEGATE_SECRET`, `PROCESS_PENDING_SECRET`, `KEEPALIVE_SECRET`, `WEBHOOK_SECRET`: Secret endpoint
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`: Dari [Neo4j AuraDB Console](https://console.neo4j.io/)
- `MOONDREAM_SPACE_1` & `MOONDREAM_SPACE_2`: Space Moondream utama (`merve/moondream3`) dan fallback (`vikhyatk/moondream2`)
- `DEEPINFRA_API_KEY` & `DEEPINFRA_MODEL`: Dari [DeepInfra Console](https://deepinfra.com/)
- `NOTION_API_KEY` & `NOTION_DATABASE_ID`: Dari [Notion Integrations](https://www.notion.so/my-integrations)
- `TMDB_API_KEY`: Dari [TMDb API](https://www.themoviedb.org/settings/api)
- `OPENWEATHER_API_KEY`: Dari [OpenWeatherMap](https://openweathermap.org/api)
- `KV_REST_API_URL` & `KV_REST_API_TOKEN`: Dari [Vercel KV / Upstash Redis](https://vercel.com/storage/kv)
- `ELEVENLABS_API_KEY` & `ELEVENLABS_VOICE_ID`: Dari [ElevenLabs](https://elevenlabs.io/)
- `GOOGLE_DRIVE_*`: Client ID, Client Secret, Refresh Token & Folder ID dari Google Cloud Console.
- `ERINE_API_URL` & `ERINE_API_KEY`: Endpoint & key ERINE (fitur akademik).

### 3. Deploy Worker Render (task berat)

1. Buat **Web Service** baru di [Render](https://render.com) dengan **root directory** = `oline-worker/`.
2. **Build Command**: `pip install -r requirements.txt`
3. **Start Command**: `bash render_start.sh` (atau via `Procfile`)
4. Set env di Render: `OLINE_WORKER_KEY` (sama dengan Vercel), `TELEGRAM_BOT_TOKEN`, `DEEPINFRA_API_KEY`, `KV_REST_API_URL`, `KV_REST_API_TOKEN`, `VERCEL_CALLBACK_URL`, `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO`.
5. `render_start.sh` otomatis menginstall node/npm/git + OpenCode CLI (`opencode-ai`) bila belum tersedia.

### 4. Deploy ke Vercel

```bash
# Install Vercel CLI
npm i -g vercel

# Deploy ke Vercel
vercel --prod
```

Pastikan semua variabel lingkungan dari `.env` diisi pada menu **Settings > Environment Variables** di Vercel Dashboard.

### 5. Set Webhook Telegram

Setelah aplikasi di-deploy ke Vercel, daftarkan Webhook Telegram:

```bash
python scripts/set_webhook.py https://your-project.vercel.app
```

Cek status webhook:
```bash
python scripts/set_webhook.py --info
```

### 6. (Opsional) Daftarkan Command Menu

Untuk menampilkan tombol **Menu** biru + saran `/` di Telegram:

```bash
python scripts/set_commands.py        # daftarkan command
python scripts/set_commands.py --info # lihat yang terdaftar
python scripts/set_commands.py --clear # kosongkan (sembunyikan tombol)
```

---

## 💬 Contoh Penggunaan

```text
User: Hai Oline, apa kabar?
Oline: Haii! Aku baik nih, kamu gimana? Ada yang bisa Oline bantu hari ini? 😊

User: Buat landing page Sakura Brew
Oline: (1 pesan progres yang terus di-edit secara dinamis, diproses di Render worker)
       ⏳ Menyusun struktur landing page...
       ⏳ Merancang gaya visual & mencari referensi desain...
       ⏳ Menyiapkan preview & link...
       ✅ Selesai! Link: https://codepen.io/pen/define/xxx

User: Olin, tambahkan fitur cek resi ke dirimu
Oline: 📋 Plan: Tambah Fitur Cek Resi
       1. Buat file src/tools/resi.py
       2. Tambah fungsi cek_resi()
       3. Daftarkan di handler intent
       4. Update system prompt
       Estimasi: 3 file diubah, 1 file baru.
       [✅ Setuju] [✏️ Perbaiki] [❌ Batalkan]
User: (klik ✅ Setuju)
Oline: ⏳ Memproses Plan: Tambah Fitur Cek Resi
       [1/5] Menyiapkan worker... ✅
       ...
       [5/5] Membuat PR... ✅
       ✅ Selesai: Tambah Fitur Cek Resi
       🌿 Branch: oline-feature/tambah-fitur-cek-resi
       📌 PR: https://github.com/.../pull/42
       [🔍 Review] [✅ Merge ke Main] [❌ Hapus Branch]
User: (klik ✅ Merge ke Main)
Oline: ✅ Berhasil di-Merge ke Main
       🌿 Branch dihapus. 🚀 Vercel auto-deploy sedang berjalan...

User: Cek saham BBCA dong
Oline: BBCA sekarang Rp 10,250 📈 (+150, +1.49%)
```

---

## 🧪 Testing

Framework: stdlib **`unittest`** (pytest tidak terpasang). Jalankan dari root repo:

```bash
python -m unittest tests/test_<nama>.py
```

Banyak test merupakan integration test yang memanggil API asli dan membutuhkan `.env` terisi — kegagalan network/kredensial biasanya masalah lingkungan, bukan bug kode.

---

## 📝 Lisensi

Personal project oleh Doni.