# Hermes Novel Translation System

Hermes adalah sistem terjemahan novel berbasis antrean asinkron yang menggunakan ChatGPT Web (via Playwright browser automation) sebagai backend LLM. Sistem berjalan sebagai **satu FastAPI application pada satu port**, menggabungkan Public REST API, Asynchronous Background Worker, dan True Concurrent Multi-Account Playwright Automation ke dalam satu service berkinerja tinggi.

---

## 🚀 Fitur Utama

- **Single Process & Single Port**: Seluruh API dan background worker berjalan terpadu pada port `18111` (dapat di-override via `ADAPTER_PORT`).
- **True Concurrent Multi-Account Execution**: Setiap akun ChatGPT memiliki isolated Playwright `BrowserContext` dan `Page` tersendiri. Request dan bab novel diterjemahkan secara **paralel murni (concurrent)** tanpa saling mengunci (*blocking*).
- **Per-Context Post-Job Cooldown**: Setiap worker context akun yang menyelesaikan job otomatis beristirahat selama durasi tertentu (default **60 detik**, dapat dikonfigurasi) sebelum mengambil tugas baru. Context lain yang ready tetap berjalan paralel tanpa terhalang.
- **Dynamic Settings (`/settings`)**: Konfigurasi server (`job_cooldown_seconds`, `worker_poll_interval`, `translation_job_timeout`, dll.) dapat diakses (`GET`) dan diperbarui (`PATCH`) secara dinamis saat runtime dengan efek seketika.
- **Multi-Account Worker Pool (`/cookies`)**: Manajemen multi-cookie akun dengan isolasi browser context, round-robin dispatch, reset cooldown instan, dan staged rate-limit cooldown (2 jam, 4 jam, expired).
- **OpenAI-Compatible API**: `/v1/chat/completions` (JSON & SSE streaming) dan `/v1/models`.
- **Asynchronous Translation Pipeline**: `/translate` dengan worker otomatis berbasis sinyal reaktif (`job_notify`), dynamic slot claiming, retry mechanism, dan image extraction/restoration.
- **Continuity Memory**: Registri otomatis untuk Karakter (`/novels/{novel_id}/characters`) dan Glosarium Istilah (`/novels/{novel_id}/glossary`) untuk menjaga konsistensi nama dan istilah antar-bab.
- **Audit & Version History**: Kemampuan `force: true` untuk re-translasi dan me-restore versi terjemahan lama (`/history/{history_id}/restore`).
- **Database Backup & Restore**: Snapshot ZIP archive (`/database/backup`) dan pemulihan instan (`/database/restore`).
- **SQLite WAL Mode**: Thread-safe with WAL mode, exponential backoff retries, and foreign key constraints.

---

## 🏗️ Arsitektur

```
Client (CLI / Web / API)
        │
        ▼ HTTP (Port 18111)
┌────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Unified Service                         │
│                                                                        │
│  ┌───────────────────────────┐        ┌─────────────────────────────┐  │
│  │   Public & Worker API     │        │    Background Worker Loop   │  │
│  │  - /translate             │        │  - Dynamic Slot Dispatcher  │  │
│  │  - /novels & /characters  │        │  - Concurrent Task Spawner  │  │
│  │  - /settings & /database  │        │  - Cooldown Releaser        │  │
│  └─────────────┬─────────────┘        └──────────────┬──────────────┘  │
│                │                                     │                 │
│                ▼                                     ▼                 │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │             Multi-Account Worker Pool (worker_pool.py)           │  │
│  │  - Isolated Account Context 1 (Lock 1) ──► Playwright Context 1  │  │
│  │  - Isolated Account Context 2 (Lock 2) ──► Playwright Context 2  │  │
│  │  - Isolated Account Context N (Lock N) ──► Playwright Context N  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                ┌──────────────────┴──────────────────┐
                ▼                                     ▼
      ┌──────────────────┐                  ┌──────────────────┐
      │   SQLite (WAL)   │                  │ Chromium (Xvfb)  │
      │  translation.db  │                  │  Multi-Context   │
      └──────────────────┘                  └──────────────────┘
```

---

## ⚙️ Variabel Lingkungan (Environment Variables)

| Variable | Default | Deskripsi |
|---|---|---|
| `ADAPTER_PORT` | `18111` | Port aplikasi FastAPI |
| `CHATGPT_HOME` | `<project>/.data/prod` | Direktori state, profil browser, dan database SQLite |
| `CHATGPT_TZ` | `Asia/Kolkata` | Timezone emulasi browser |
| `HERMES_NO_LOGIN` | `0` | Set `1` untuk mode anonim tanpa login |
| `HERMES_HEADLESS` | `0` | Default `0` (headful under Xvfb). Set `1` untuk mode headless |
| `HERMES_SKIP_BROWSER` | `0` | Set `1` untuk mock browser pool (unit test / benchmark) |
| `WORKER_POLL_INTERVAL`| `2.0` | Interval polling background worker (detik) |
| `JOB_COOLDOWN_SECONDS`| `60` | Durasi post-job cooldown per context akun (detik) |
| `CONTEXT_REFRESH_JOBS`| `10` | Ambang batas job selesai untuk auto-refresh context browser |
| `TRANSLATION_JOB_TIMEOUT` | `480` | Batas waktu maksimal eksekusi satu job terjemahan (detik) |
| `WORKER_KEY` | `""` | Kunci proteksi endpoint internal `/worker/*` |
| `INTERNAL_KEY` | `""` | Kunci proteksi endpoint `/_internal/*` jika diakses non-localhost |

---

## 🏃 Cara Menjalankan

### 1. Menggunakan uv / python virtual environment

```bash
# Masuk ke direktori hermes
cd hermes-chatgpt-web

# Install dependencies
uv sync

# Menjalankan service (default: headful under Xvfb/display)
python -m hermes_chatgpt_web.main
```

### 2. Argumen CLI Tambahan

```bash
# Menjalankan dengan port kustom
python -m hermes_chatgpt_web.main --port 18111

# Menjalankan mode headless
python -m hermes_chatgpt_web.main --headless

# Menjalankan tanpa login (anonymous)
python -m hermes_chatgpt_web.main --no-login
```

### 3. Deploy Menggunakan Docker

Dockerfile telah dioptimasi penuh untuk **True Concurrent Multi-Account Playwright** dengan pre-installed font multibahasa (Korea/Jepang/China), Xvfb GLX extension, and healthcheck:

```bash
# Build Docker image dari Dockerfile yang telah dioptimasi
cd hermes-chatgpt-web
docker build -t hermes-chatgpt-web .

# Jalankan container dengan alokasi shared memory (--shm-size=2g) untuk konkurensi optimal
docker run -d \
  --name hermes-chatgpt-web \
  --restart unless-stopped \
  -p 18111:18111 \
  --shm-size=2g \
  -v hermes_data:/app/.data \
  -v $(pwd)/cookies:/app/cookies \
  hermes-chatgpt-web
```

---

## 📖 Dokumentasi Endpoint & Pipeline Lengkap

Dokumentasi detail endpoint, arsitektur, dan contoh payload dapat dilihat di:
- [`AGENT_BUILD.md`](AGENT_BUILD.md) — Kontrak teknis arsitektur dan non-negotiables
- [`dokumentasi/endpoints.md`](dokumentasi/endpoints.md) — Daftar lengkap semua endpoint dan contoh curl
- [`dokumentasi/prd-endpoint.md`](dokumentasi/prd-endpoint.md) — Spesifikasi PRD dan status code HTTP
- [`dokumentasi/translate-pipeline.md`](dokumentasi/translate-pipeline.md) — Detail pipeline penerjemahan dan worker slot
- [`dokumentasi/chat-gateway-lifecycle.md`](dokumentasi/chat-gateway-lifecycle.md) — Siklus DOM streaming dan anti-bot stealth

## Ref
- [https://github.com/s4chdev/hermes-chatgpt-web](https://github.com/s4chdev/hermes-chatgpt-web)