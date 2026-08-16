# ChatGPT Web & Novel Translation Workspace

Workspace terpadu ekosistem terjemahan novel otomatis berbasis **ChatGPT Web Automation** yang terdiri dari backend adapter otomatisasi browser Playwright (`hermes-chatgpt-web`) dan aplikasi client manajemen CLI (`client-app`).

---

## 📁 Struktur Workspace

```
chatgpt-web/
├── hermes-chatgpt-web/      # Backend FastAPI service & Multi-Account Playwright runner
│   ├── src/                 # Source code hermes_chatgpt_web
│   │   ├── api/             # REST API routers (Public, Settings, Cookies, Gateway)
│   │   ├── chatgpt/         # Browser automation, cookie parsing, worker pool manager
│   │   ├── core/            # Config, logger, Playwright browser engine
│   │   ├── gateway/         # Playwright state & chat logic
│   │   ├── translation/     # SQLite DB, worker queue, continuity memory, smart cleaner
│   │   └── main.py          # Single process entrypoint
│   ├── cookies/             # Folder auto-import cookies JSON akun ChatGPT
│   ├── dokumentasi/         # PRD, spesifikasi endpoint, arsitektur lifecycle
│   ├── tests/               # Unit & integration test suite
│   └── pyproject.toml       # Backend dependencies
│
└── client-app/              # Client application & batch translation CLI
    ├── src/                 # Source code client-app
    │   ├── core/            # Config & system prompts
    │   ├── services/        # Hermes API client wrapper
    │   └── app.py           # Unified CLI interface (translate, batch, status, settings, etc.)
    ├── data-translate/      # Dataset bab novel raw (Korean/Japanese txt) & TOC generator
    │   ├── create-toc.py    # TOC generator & batch queue submitter
    │   └── office-worker-who-sees-fate/ # Novel data & chapters
    └── pyproject.toml       # Client dependencies
```

---

## 🚀 Komponen Utama

### 1. [`hermes-chatgpt-web`](hermes-chatgpt-web/)
Service backend modern berbasis **FastAPI** dan **Playwright (Chromium)** yang berjalan pada satu port (`18111`):
- **Per-Context Post-Job Cooldown**: Setiap worker context akun yang menyelesaikan job otomatis beristirahat selama **60 detik** (default) sebelum mengambil tugas baru. Context lain yang ready tetap berjalan paralel tanpa terhalang.
- **Dynamic Settings (`/settings`)**: Parameter `job_cooldown_seconds`, `worker_poll_interval`, dan batas timeout dapat dilihat (`GET`) dan diubah (`PATCH`) secara dinamis saat runtime dengan efek seketika.
- **Multi-Account Worker Pool**: Manajemen multi-cookie akun dengan isolasi browser context, round-robin dispatch, dan staged rate-limit cooldown (2 jam, 4 jam, expired).
- **Asynchronous Translation Queue (`/translate`)**: Antrean job SQLite dengan proteksi atomik, smart json cleaner, delimiter tags to clean markdown, image extractor & restorer.
- **Continuity Memory**: Ekstraksi dan persistensi otomatis entitas karakter (`/characters`) dan kamus istilah (`/glossary`) per bab.
- **Database Backup & Restore**: Snapshot ZIP archive (`/database/backup`) dan pemulihan instan (`/database/restore`).
- **OpenAI-Compatible Endpoint**: `/v1/chat/completions` (mendukung streaming SSE dan direct sync).

### 2. [`client-app`](client-app/)
Aplikasi client CLI untuk konsumsi API dan otomasi pipeline:
- **CLI Commands**:
  - `status` — Cek status server, worker pool, context cooldowns, dan konfigurasi aktif.
  - `settings` — Ubah durasi cooldown (misal `--set job_cooldown_seconds=30`) atau reset ke default.
  - `translate` — Terjemahkan bab file/teks via antrean queue dengan live polling progress.
  - `batch` — Kirim batch ratusan bab novel ke queue Hermes secara otomatis.
  - `cookies` — Pantau status akun cookie dan reset cooldown per worker.
  - `novel` — Cek statistik novel, daftar bab, karakter, dan kamus glossary.
  - `chat` — Sesi percakapan interaktif terminal.
- **TOC Generator (`data-translate/create-toc.py`)**: Scan folder novel, ekstrak nomor bab & judul, buat `toc.json` dan `toc.md`, serta submit batch chapters.

---

## 🛠️ Quick Start

### 1. Menjalankan Backend (`hermes-chatgpt-web`)

```bash
cd hermes-chatgpt-web
uv sync
python -m hermes_chatgpt_web.main
```

Banner startup akan muncul saat browser Playwright dan database telah siap:
```
──────────────────────────────────────────────
  ✓  Hermes is ready
  ➜  Adapter API  →  http://0.0.0.0:18111
──────────────────────────────────────────────
```

### 2. Menjalankan Client App (`client-app`)

```bash
cd client-app
uv sync

# Cek status server & worker pool cooldown
python src/app.py status

# Terjemahkan satu file bab novel dan tunggu hingga selesai
python src/app.py translate --file data-translate/office-worker-who-sees-fate/chapters/1.txt --novel-id office-worker --chapter 1 --wait

# Generate TOC dan submit batch chapters
python data-translate/create-toc.py --dir data-translate/office-worker-who-sees-fate/chapters --novel-id office-worker
```

---

## 📑 Ringkasan Endpoints Backend Hermes

| Kategori | Method & Path | Deskripsi |
|---|---|---|
| **Health** | `GET /health`, `GET /v1/models` | Status adapter & daftar model |
| **Settings** | `GET /settings`, `PATCH /settings`, `POST /settings/reset` | Pengaturan cooldown 60s dan polling interval |
| **Cookies** | `GET /cookies`, `POST /cookies`, `POST /cookies/{id}/reset-cooldown` | Manajemen multi-account dan reset cooldown |
| **Translate** | `POST /translate`, `GET /translate/{id}`, `POST /translate/{id}/retry` | Asynchronous translation queue |
| **Novels** | `GET /novels`, `GET /novels/{id}/chapters`, `GET /novels/{id}/stats` | Data bab dan agregasi statistik |
| **Continuity**| `GET/POST /novels/{id}/characters`, `GET/POST /novels/{id}/glossary` | Karakter dan kamus istilah |
| **Backup** | `GET /database/backup`, `POST /database/restore`, `GET /database/stats` | Backup & restore ZIP database |
| **Chat** | `POST /v1/chat/completions` | OpenAI-compatible chat API |

---

## 📚 Dokumentasi Lebih Lanjut

- [Dokumentasi Lengkap Endpoints (endpoints.md)](hermes-chatgpt-web/dokumentasi/endpoints.md)
- [PRD Endpoint Spesifikasi (prd-endpoint.md)](hermes-chatgpt-web/dokumentasi/prd-endpoint.md)
- [Panduan Client App (client-app/README.md)](client-app/README.md)
- [Pipeline Terjemahan (translate-pipeline.md)](hermes-chatgpt-web/dokumentasi/translate-pipeline.md)
- [Chat Gateway Lifecycle (chat-gateway-lifecycle.md)](hermes-chatgpt-web/dokumentasi/chat-gateway-lifecycle.md)

