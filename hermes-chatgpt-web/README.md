# Hermes Novel Translation System

Hermes adalah sistem terjemahan novel berbasis antrean asinkron yang menggunakan ChatGPT Web (via Playwright browser automation) sebagai backend LLM. Sistem berjalan sebagai **satu FastAPI application pada satu port**, menggabungkan Public REST API, Background Worker, dan Internal Playwright Gateway menjadi satu service tunggal.

---

## 🚀 Fitur Utama

- **Single Process & Single Port**: Berjalan pada port `18111` (dapat di-override via `ADAPTER_PORT`).
- **Per-Context 60s Post-Job Cooldown**: Setiap worker context akun yang menyelesaikan job otomatis beristirahat selama **60 detik** (default) sebelum mengambil tugas baru. Context lain yang ready tetap berjalan paralel tanpa terhalang.
- **Dynamic Settings (`/settings`)**: Konfigurasi server (`job_cooldown_seconds`, `worker_poll_interval`, `translation_job_timeout`, dll.) dapat diakses (`GET`) dan diperbarui (`PATCH`) secara dinamis saat runtime dengan efek seketika.
- **Multi-Account Worker Pool (`/cookies`)**: Manajemen multi-cookie akun dengan isolasi browser context, round-robin dispatch, dan staged rate-limit cooldown (2 jam, 4 jam, expired).
- **OpenAI-Compatible API**: `/v1/chat/completions` (JSON & SSE streaming) dan `/v1/models`.
- **Asynchronous Translation Pipeline**: `/translate` dengan worker otomatis, retry mechanism, dead-job detection, dan image extraction/restoration.
- **Continuity Memory**: Registri otomatis untuk Karakter (`/novels/{novel_id}/characters`) dan Glosarium Istilah (`/novels/{novel_id}/glossary`) untuk menjaga konsistensi nama dan istilah antar-bab.
- **Audit & Version History**: Kemampuan `force: true` untuk re-translasi dan me-restore versi terjemahan lama (`/history/{history_id}/restore`).
- **Database Backup & Restore**: Snapshot ZIP archive (`/database/backup`) dan pemulihan instan (`/database/restore`).
- **SQLite WAL Mode**: Thread-safe with WAL mode and foreign key constraints.

---

## 🏗️ Arsitektur

```
Client (HTTP)
     │
     ▼
┌──────────────────────────────────────┐
│   FastAPI App  :18111                │
│                                      │
│  ┌────────────────────────────────┐  │
│  │  Public Router                 │  │
│  │  /v1/*  /translate/*           │  │
│  │  /cookies/*  /novels/*         │  │
│  │  /worker/*  /health            │  │
│  └────────────┬───────────────────┘  │
│               │ asyncio.to_thread()  │
│  ┌────────────▼───────────────────┐  │
│  │  Internal Gateway Module       │  │
│  │  (Playwright browser runner)   │  │
│  │  /_internal/status             │  │
│  │  /_internal/debug              │  │
│  │  /_internal/chat               │  │
│  │  /_internal/chat/stream        │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
          │                  │
          ▼                  ▼
   ┌─────────────┐    ┌─────────────┐
   │  SQLite DB  │    │  Chromium   │
   │  (WAL mode) │    │ (Playwright)│
   └─────────────┘    └─────────────┘
```

---

## ⚙️ Variabel Lingkungan (Environment Variables)

| Variable | Default | Deskripsi |
|---|---|---|
| `ADAPTER_PORT` | `18111` | Port aplikasi FastAPI |
| `CHATGPT_HOME` | `~/.chatgpt-adapter` | Direktori state, profil browser, dan database SQLite |
| `CHATGPT_COOKIE_FILE` | `~/.chatgpt-adapter/cookies_parsed.json` | Path file cookie sesi |
| `HERMES_NO_LOGIN` | `0` | Set `1` untuk mode anonim tanpa login |
| `HERMES_HEADLESS` | `0` | Default `0` (headful). Set `1` untuk menjalankan browser headless |
| `WORKER_POLL_INTERVAL`| `2.0` | Interval polling background worker (detik) |
| `WORKER_KEY` | `""` | Kunci proteksi endpoint `/worker/*` |
| `INTERNAL_KEY` | `""` | Kunci proteksi endpoint `/_internal/*` jika diakses non-localhost |

---

## 🏃 Cara Menjalankan

### 1. Menggunakan uv / python virtual environment

```bash
# Masuk ke direktori hermes
cd hermes-chatgpt-web

# Install dependencies
uv sync

# Menjalankan service (default: headful)
python -m hermes_chatgpt_web.main
```

### 2. Argumen CLI Tambahan

```bash
# Menjalankan dengan port kustom
python -m hermes_chatgpt_web.main --port 18111

# Menjalankan mode headless (default adalah headful)
python -m hermes_chatgpt_web.main --headless

# Menjalankan tanpa login (anonymous)
python -m hermes_chatgpt_web.main --no-login
```

### 3. Deploy Menggunakan Docker

```bash
# Build Docker image
docker build -t hermes-chatgpt-web .

# Jalankan container
docker run -d \
  -p 18111:18111 \
  --shm-size=1g \
  -v hermes_data:/app/.data \
  -v $(pwd)/cookies:/app/cookies \
  hermes-chatgpt-web
```

---

## 📖 Dokumentasi Endpoint Lengkap

Dokumentasi detail endpoint, schema, dan contoh payload dapat dilihat di:
- [`dokumentasi/prd-endpoint.md`](dokumentasi/prd-endpoint.md)
- [`dokumentasi/endpoints.md`](dokumentasi/endpoints.md)
- [`dokumentasi/translate-endpoint.md`](dokumentasi/translate-endpoint.md)

## Ref
- [https://github.com/s4chdev/hermes-chatgpt-web](https://github.com/s4chdev/hermes-chatgpt-web)