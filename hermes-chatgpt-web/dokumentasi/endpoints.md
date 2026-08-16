# Dokumentasi Lengkap API Endpoints Hermes Novel Translation System

Dokumen ini memuat seluruh daftar endpoint yang tersedia pada aplikasi **Hermes Novel Translation System** beserta contoh perintah `curl`, format request payload, query parameter, dan format response.

Sistem berjalan sebagai **satu FastAPI application pada satu port** (`18111`), menggabungkan Public REST API, Background Worker, dan Internal Playwright Gateway menjadi satu service tunggal.

---

## 📌 Ringkasan Arsitektur & Port

| Service / Komponen | Port Default | Env Variable | Deskripsi |
|---|---|---|---|
| **Hermes FastAPI Application** | `18111` | `ADAPTER_PORT` | Main single process application untuk seluruh Public REST API, Internal Worker, dan Gateway |

---

## 📑 Daftar Semua Endpoints

```
# Health & Info
GET    /health
GET    /v1/models

# Chat (OpenAI-compatible)
POST   /v1/chat/completions

# Cookie & Multi-Account Management
GET    /cookies
POST   /cookies
DELETE /cookies/{account_id}
POST   /cookies/{account_id}/pause
POST   /cookies/{account_id}/resume
POST   /cookies/{account_id}/reset-cooldown
POST   /cookies/{account_id}/refresh
POST   /cookies/inject-session

# System Settings & Post-Job Cooldown
GET    /settings
PATCH  /settings
POST   /settings/reset

# Translation Jobs
POST   /translate
GET    /translate
GET    /translate/{job_id}
POST   /translate/{job_id}/retry
POST   /translate/{job_id}/cancel
POST   /translate/cancel-all
DELETE /translate/{job_id}
GET    /translate/{job_id}/history

# History
POST   /history/{history_id}/restore

# Novels
GET    /novels
GET    /novels/{novel_id}/stats
GET    /novels/{novel_id}/chapters
GET    /novels/{novel_id}/jobs
GET    /novels/{novel_id}/jobs/{chapter_number}
GET    /novels/{novel_id}/context
GET    /novels/{novel_id}/history
GET    /novels/{novel_id}/history/{chapter_number}

# Characters
GET    /novels/{novel_id}/characters
POST   /novels/{novel_id}/characters
GET    /novels/{novel_id}/characters/{character_id}
PUT    /novels/{novel_id}/characters/{character_id}
DELETE /novels/{novel_id}/characters/{character_id}

# Glossary
GET    /novels/{novel_id}/glossary
POST   /novels/{novel_id}/glossary
POST   /novels/{novel_id}/glossary/bulk
GET    /novels/{novel_id}/glossary/export
GET    /novels/{novel_id}/glossary/{glossary_id}
PUT    /novels/{novel_id}/glossary/{glossary_id}
DELETE /novels/{novel_id}/glossary/{glossary_id}

# Database Backup & Restore
GET    /database/backup
POST   /database/backup
POST   /database/restore
GET    /database/stats

# Worker (internal, X-Worker-Key)
GET    /worker/jobs/next
PATCH  /worker/jobs/{job_id}/status
GET    /worker/jobs/running

# Internal Gateway (Playwright, X-Internal-Key)
GET    /_internal/status
GET    /_internal/debug
POST   /_internal/chat
POST   /_internal/chat/stream
```

---

## 1. Health & Info

### `GET /health`
Status adapter dan browser Playwright.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/health
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "gateway": {
    "ok": true,
    "title": "ChatGPT",
    "error": null,
    "turns": 4,
    "busy": false,
    "busy_since": null,
    "last_activity": 1771056789.12,
    "idle_s": 12.5
  }
}
```

---

### `GET /v1/models`
Daftar model yang tersedia dalam format OpenAI-compatible.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/v1/models
```

**Response `200 OK`:**
```json
{
  "object": "list",
  "data": [
    { "id": "gpt-5.6-luna", "object": "model", "owned_by": "openai" }
  ]
}
```

---

## 2. OpenAI-Compatible Chat

### `POST /v1/chat/completions`
Chat completions yang mendukung non-streaming (JSON) dan streaming SSE (`stream: true`).

**Contoh cURL (Non-Streaming):**
```bash
curl -X POST http://localhost:18111/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.6-luna",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "Halo, jelaskan secara singkat apa itu Hermes."}
    ],
    "stream": false
  }'
```

**Contoh cURL (Streaming SSE):**
```bash
curl -N -X POST http://localhost:18111/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.6-luna",
    "messages": [
      {"role": "user", "content": "Tuliskan satu bait puisi."}
    ],
    "stream": true
  }'
```

**Response `200 OK` (Non-streaming):**
```json
{
  "id": "chatcmpl-a1b2c3d4e5f6",
  "object": "chat.completion",
  "created": 1771056800,
  "model": "gpt-5.6-luna",
  "choices": [{
    "index": 0,
    "message": { "role": "assistant", "content": "Halo! Hermes adalah sistem otomatisasi translasi novel..." },
    "finish_reason": "stop"
  }],
  "usage": null
}
```

---

## 3. Cookie & Multi-Account Management

Sistem mendukung multi-akun / multi-cookie dengan **Dynamic Worker Pool**. Setiap akun aktif di database dijalankan dalam `BrowserContext` Playwright terisolasi untuk eksekusi translasi paralel.

### Mekanisme Staged Cooldown:
1. **Rate Limit #1:** Status diubah ke `COOLDOWN` selama **2 jam**. Job yang sedang dikerjakan otomatis di-requeue ke status `pending`.
2. **Rate Limit #2 (berurutan):** Status diubah ke `COOLDOWN` selama **4 jam**.
3. **Rate Limit #3:** Status diubah ke `EXPIRED`.
4. **Auto-Recovery:** Background scheduler secara periodik mengecek waktu cooldown dan memulihkan akun ke status `ACTIVE` saat masa cooldown habis.

---

### `GET /cookies` (alias: `GET /accounts`)
Melihat daftar seluruh akun cookie, status (`ACTIVE`, `BUSY`, `COOLDOWN`, `EXPIRED`, `PAUSED`), sisa waktu cooldown dalam detik, serta status worker pool.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/cookies
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "total_accounts": 2,
  "accounts": [
    {
      "id": "7c12f4d1-8664-4b57-b2bf-0cbafb859942",
      "name": "user1",
      "provider": "chatgpt",
      "status": "ACTIVE",
      "cooldown_count": 0,
      "cooldown_until": null,
      "cooldown_remaining_seconds": null,
      "last_used_at": "2026-08-16T01:30:00+00:00",
      "total_jobs_processed": 5
    },
    {
      "id": "8d34e9a2-1122-4c33-9988-123456789abc",
      "name": "user2",
      "provider": "chatgpt",
      "status": "COOLDOWN",
      "cooldown_count": 1,
      "cooldown_until": "2026-08-16T03:30:00+00:00",
      "cooldown_remaining_seconds": 7180,
      "last_used_at": "2026-08-16T01:25:00+00:00",
      "total_jobs_processed": 12
    }
  ],
  "pool": {
    "ok": true,
    "total_workers": 2,
    "idle_workers": 1,
    "busy_workers": 0,
    "cooling_down_workers": 1,
    "workers": [
      {
        "account_id": "7c12f4d1-8664-4b57-b2bf-0cbafb859942",
        "name": "user1",
        "provider": "chatgpt",
        "busy": false,
        "cooling_down": false,
        "cooldown_remaining_s": null,
        "idle_s": 45.2,
        "title": "ChatGPT",
        "ok": true
      }
    ]
  }
}
```

---

### `POST /cookies` (alias: `POST /accounts`)
Menambah atau memperbarui akun cookie ke database dan mendaftarkannya ke worker pool.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/cookies \
  -H "Content-Type: application/json" \
  -d '{
    "name": "user1",
    "provider": "chatgpt",
    "cookies": "__Host-next-auth.csrf-token=...; __Secure-next-auth.session-token=..."
  }'
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "message": "Cookie account saved successfully",
  "account": {
    "id": "7c12f4d1-8664-4b57-b2bf-0cbafb859942",
    "name": "user1",
    "provider": "chatgpt",
    "status": "ACTIVE",
    "cooldown_count": 0,
    "cooldown_until": null,
    "cooldown_remaining_seconds": null
  }
}
```

---

### `DELETE /cookies/{account_id}` (alias: `DELETE /accounts/{account_id}`)
Menghapus akun cookie dari database dan menutup browser context terkait secara bersih.

**Contoh cURL:**
```bash
curl -X DELETE http://localhost:18111/cookies/7c12f4d1-8664-4b57-b2bf-0cbafb859942
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "message": "Account 'user1' deleted successfully"
}
```

---

### `POST /cookies/{account_id}/pause`
Mem-pause akun agar sementara tidak menerima job terjemahan baru.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/cookies/7c12f4d1-8664-4b57-b2bf-0cbafb859942/pause
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "message": "Account 'user1' paused",
  "account": {
    "id": "7c12f4d1-8664-4b57-b2bf-0cbafb859942",
    "status": "PAUSED"
  }
}
```

---

### `POST /cookies/{account_id}/resume`
Mengaktifkan kembali akun yang di-pause ke status `ACTIVE` dan mendaftarkannya kembali ke worker pool.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/cookies/7c12f4d1-8664-4b57-b2bf-0cbafb859942/resume
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "message": "Account 'user1' resumed",
  "account": {
    "id": "7c12f4d1-8664-4b57-b2bf-0cbafb859942",
    "status": "ACTIVE"
  }
}
```

---

### `POST /cookies/{account_id}/reset-cooldown`
Mereset paksa status cooldown akun (baik rate-limit ChatGPT maupun cooldown post-job) secara instan menjadi `ACTIVE` dan siap memproses job kembali.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/cookies/7c12f4d1-8664-4b57-b2bf-0cbafb859942/reset-cooldown
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "message": "Cooldown reset for 'user1'",
  "account": {
    "id": "7c12f4d1-8664-4b57-b2bf-0cbafb859942",
    "status": "ACTIVE",
    "cooldown_count": 0,
    "cooldown_until": null,
    "cooldown_remaining_seconds": null
  }
}
```

---

### `POST /cookies/{account_id}/refresh` (alias: `POST /accounts/{account_id}/refresh`)
Melakukan refresh context browser secara manual: menutup BrowserContext lama untuk membersihkan cache dan memory leak, lalu membuat BrowserContext baru yang bersih dan menginjeksi ulang session cookies akun.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/cookies/7c12f4d1-8664-4b57-b2bf-0cbafb859942/refresh
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "message": "Browser context refreshed for account 'user1'"
}
```

---

### `POST /cookies/inject-session` (Legacy Compatible)
Injeksi cookie langsung ke browser runtime dan menyimpannya ke database.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/cookies/inject-session \
  -H "Content-Type: application/json" \
  -d '{
    "name": "user1",
    "provider": "chatgpt",
    "cookies": "__Secure-next-auth.session-token=..."
  }'
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "message": "Session injected successfully",
  "account": {
    "id": "7c12f4d1-8664-4b57-b2bf-0cbafb859942",
    "name": "user1",
    "status": "ACTIVE"
  }
}
```

---

## 4. System Settings, Post-Job Cooldown & Auto Context Refresh

Hermes menerapkan manajemen memori dan cooldown otomatis:
- **Per-Context Post-Job Cooldown** (default **60 detik**): Setiap context/worker akun yang telah menyelesaikan suatu job otomatis masuk ke fase cooldown selama `job_cooldown_seconds` (default: 60 detik). Context lain yang idle dan tidak sedang cooldown dapat langsung memproses job berikutnya secara paralel tanpa terhalang.
- **Automatic Browser Context Refresh on 10 Completed Jobs** (`context_refresh_jobs`, default **10**): Setiap kali worker menyelesaikan 10 job translasi, browser context untuk akun tersebut akan di-refresh secara otomatis: menutup context lama untuk membersihkan seluruh cache & memory browser, kemudian membuat context baru dari awal dan menginjeksi kembali session cookies.
- Seluruh pengaturan dapat dikonfigurasi kapan saja via endpoint `/settings` dan **langsung berlaku seketika pada job berikutnya**.

---

### `GET /settings`
Melihat seluruh konfigurasi runtime dan sistem yang sedang aktif.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/settings
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "settings": {
    "job_cooldown_seconds": 60,
    "context_refresh_jobs": 10,
    "worker_poll_interval": 2.0,
    "worker_concurrency": 1,
    "translation_job_timeout": 120,
    "translation_max_text_length": 100000
  }
}
```

---

### `PATCH /settings` (alias: `PUT /settings`, `POST /settings`)
Memperbarui satu atau lebih parameter sistem secara dinamis. Nilai baru tersimpan permanen di database `app_settings` dan langsung disinkronkan ke memori server seketika.

**Contoh cURL:**
```bash
curl -X PATCH http://localhost:18111/settings \
  -H "Content-Type: application/json" \
  -d '{
    "job_cooldown_seconds": 30,
    "context_refresh_jobs": 10,
    "worker_poll_interval": 2.0
  }'
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "message": "Settings updated successfully",
  "settings": {
    "job_cooldown_seconds": 30,
    "context_refresh_jobs": 10,
    "worker_poll_interval": 2.0,
    "worker_concurrency": 1,
    "translation_job_timeout": 120,
    "translation_max_text_length": 100000
  }
}
```

---

### `POST /settings/reset`
Mengembalikan seluruh pengaturan sistem ke nilai default bawaan.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/settings/reset
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "message": "Settings reset to default",
  "settings": {
    "job_cooldown_seconds": 60,
    "context_refresh_jobs": 10,
    "worker_poll_interval": 2.0,
    "worker_concurrency": 1,
    "translation_job_timeout": 120,
    "translation_max_text_length": 100000
  }
}
```

---

## 5. Translation Jobs

### `POST /translate`
Mendaftarkan job terjemahan bab baru ke antrean SQLite.

**Request Body:**
| Field | Type | Required | Default | Keterangan |
|---|---|---|---|---|
| `model` | string | Yes | — | Model identifier (misal: `gpt-5.6-luna`) |
| `source_lang` | string | Yes | — | Kode bahasa asal (`ja`/`zh`/`ko`/`en`) |
| `target_lang` | string | Yes | — | Kode bahasa tujuan (`id`/`en`/...) |
| `novel_id` | string | Yes | — | ID unik novel (slug/identifier) |
| `chapter_number` | number | Yes | — | Nomor bab (bisa float seperti `1.5`) |
| `text` | string | Yes | — | Teks mentah bab |
| `force` | boolean | No | `false` | Paksa re-translasi (mengarsipkan hasil lama ke history) |

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/translate \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.6-luna",
    "source_lang": "ja",
    "target_lang": "id",
    "novel_id": "overlord",
    "chapter_number": 1.0,
    "text": "第1章　終わりの始まり\n\nモモンガは玉座に座っていた...",
    "force": false
  }'
```

**Response `202 Accepted`:**
```json
{
  "id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
  "novel_id": "overlord",
  "chapter_number": 1.0,
  "status": "pending",
  "source_lang": "ja",
  "target_lang": "id",
  "model": "gpt-5.6-luna",
  "created_at": "2026-08-15T08:00:00.000000+00:00",
  "updated_at": "2026-08-15T08:00:00.000000+00:00"
}
```

**Response `409 Conflict` (jika sudah ada dan `force: false`):**
```json
{
  "error": "chapter_already_translated",
  "hint": "use force:true to re-translate",
  "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12"
}
```

---

### `GET /translate`
Mendapatkan list semua job dengan filter dan pagination.
- **Query Params:** `novel_id`, `status`, `page` (default `1`), `limit` (default `20`, max `100`), `sort` (default `created_at:desc`).

**Contoh cURL:**
```bash
curl -s "http://localhost:18111/translate?novel_id=overlord&status=done&page=1&limit=20&sort=created_at:desc"
```

**Response `200 OK`:**
```json
{
  "total": 120,
  "page": 1,
  "limit": 20,
  "items": [
    {
      "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
      "status": "done",
      "novel_id": "overlord",
      "chapter_number": 1.0,
      "created_at": "2026-08-15T08:00:00.000000+00:00",
      "updated_at": "2026-08-15T08:00:25.000000+00:00",
      "result": {
        "translation": "Bab 1: Hari Awal\n\nMomonga sedang duduk di atas takhta.",
        "chapter_summary": "Pengenalan awal Momonga di Great Tomb of Nazarick."
      }
    }
  ]
}
```

---

### `GET /translate/{job_id}`
Polling status dan hasil terjemahan. Mendukung selective field filtering melalui query parameter `fields`.

**Contoh cURL (Detail Lengkap):**
```bash
curl -s http://localhost:18111/translate/c1f7b0a9-8356-4c22-9856-78fa3e9e1c12
```

**Contoh cURL (Dengan Field Filtering):**
```bash
curl -s "http://localhost:18111/translate/c1f7b0a9-8356-4c22-9856-78fa3e9e1c12?fields=job_id,status,result.translation"
```

**Response `200 OK` (Status `done`):**
```json
{
  "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
  "status": "done",
  "novel_id": "overlord",
  "chapter_number": 1.0,
  "created_at": "2026-08-15T08:00:00.000000+00:00",
  "updated_at": "2026-08-15T08:00:25.000000+00:00",
  "result": {
    "translation": "Bab 1: Hari Awal\n\nMomonga sedang duduk di atas takhta.",
    "translate_md": "Bab 1: Hari Awal\n\nMomonga sedang duduk di atas takhta.",
    "chapter_summary": "Pengenalan awal Momonga di Great Tomb of Nazarick."
  },
  "raw_response": "{...}",
  "cleaned_response": "{...}",
  "error": null
}
```

---

### `POST /translate/{job_id}/retry`
Retry manual untuk job yang berstatus `failed`. Status job akan diubah kembali ke `pending` dan sinyal eksekusi worker akan dipicu secara otomatis.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/translate/c1f7b0a9-8356-4c22-9856-78fa3e9e1c12/retry
```

**Response `200 OK`:**
```json
{
  "status": "pending",
  "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12"
}
```

**Response `400 Bad Request` (Jika status bukan `failed`):**
```json
{
  "error": "job_not_retryable",
  "current_status": "done"
}
```

**Response `404 Not Found`:**
```json
{
  "error": "job_not_found",
  "message": "Job 'c1f7b0a9-8356-4c22-9856-78fa3e9e1c12' not found."
}
```

---

### `POST /translate/{job_id}/cancel`
Membatalkan job yang masih `pending` atau `running`.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/translate/c1f7b0a9-8356-4c22-9856-78fa3e9e1c12/cancel
```

**Response `200 OK`:**
```json
{
  "status": "cancelled",
  "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12"
}
```

**Response `400 Bad Request` (Jika sudah status final `done` atau `cancelled`):**
```json
{
  "error": "job_not_cancellable",
  "current_status": "done"
}
```

---

### `POST /translate/cancel-all`
Membatalkan seluruh job yang sedang berjalan atau antre secara massal (bulk cancel). Dapat memilih untuk membatalkan job yang `pending` saja, `running` saja, atau keduanya (`all` / `both`), serta opsional filter per `novel_id`.

**Parameter (Query Parameters atau JSON Body):**
- `status` (string, optional, default: `"all"`): Status target pembatalan:
  - `"all"` atau `"both"`: Batalkan semua job `pending` dan `running`.
  - `"pending"`: Batalkan hanya job yang berstatus `pending`.
  - `"running"`: Batalkan hanya job yang berstatus `running`.
- `novel_id` (string, optional): Batalkan hanya job yang terkait dengan `novel_id` tertentu.

**Contoh cURL (Query Params):**
```bash
# 1. Batalkan semua job (pending & running)
curl -X POST "http://localhost:18111/translate/cancel-all?status=all"

# 2. Batalkan hanya job pending
curl -X POST "http://localhost:18111/translate/cancel-all?status=pending"

# 3. Batalkan hanya job running
curl -X POST "http://localhost:18111/translate/cancel-all?status=running"

# 4. Batalkan job novel tertentu saja
curl -X POST "http://localhost:18111/translate/cancel-all?status=all&novel_id=overlord"
```

**Contoh cURL (JSON Body):**
```bash
curl -X POST http://localhost:18111/translate/cancel-all \
  -H "Content-Type: application/json" \
  -d '{
    "status": "pending",
    "novel_id": "overlord"
  }'
```

**Response `200 OK`:**
```json
{
  "status": "cancelled",
  "filter": "all",
  "novel_id": "overlord",
  "cancelled_count": 2,
  "cancelled_pending": 1,
  "cancelled_running": 1,
  "cancelled_job_ids": [
    "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
    "d2a8b1c0-9467-5d33-0967-89ab4f0f2d23"
  ],
  "cancelled_jobs": [
    {
      "id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
      "novel_id": "overlord",
      "chapter_number": 1.0,
      "previous_status": "pending"
    },
    {
      "id": "d2a8b1c0-9467-5d33-0967-89ab4f0f2d23",
      "novel_id": "overlord",
      "chapter_number": 2.0,
      "previous_status": "running"
    }
  ]
}
```

**Response `400 Bad Request` (Jika nilai parameter `status` tidak valid):**
```json
{
  "error": "invalid_status_filter",
  "message": "Invalid status 'done'. Must be one of: 'pending', 'running', 'all', 'both'."
}
```

---

### `DELETE /translate/{job_id}`
Menghapus job dari database (hanya diperbolehkan jika status `pending` atau `cancelled`).

**Contoh cURL:**
```bash
curl -X DELETE http://localhost:18111/translate/c1f7b0a9-8356-4c22-9856-78fa3e9e1c12
```

**Response `200 OK`:**
```json
{
  "status": "deleted",
  "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12"
}
```

**Response `400 Bad Request` (Jika job masih aktif atau status `running`/`done`):**
```json
{
  "error": "job_not_deletable",
  "hint": "cancel the job first"
}
```

---

### `GET /translate/{job_id}/history`
Riwayat versi terjemahan job yang telah diarsipkan saat translasi ulang (`force: true`).

**Contoh cURL:**
```bash
curl -s http://localhost:18111/translate/c1f7b0a9-8356-4c22-9856-78fa3e9e1c12/history
```

**Response `200 OK`:**
```json
{
  "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
  "history": [
    {
      "id": 1,
      "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
      "model": "gpt-5.6-luna",
      "archived_at": "2026-08-15T09:00:00+00:00",
      "result": {
        "translation": "Versi lama terjemahan bab 1...",
        "chapter_summary": "Summary versi lama..."
      }
    }
  ]
}
```

---

## 6. History

### `POST /history/{history_id}/restore`
Restore versi terjemahan lama dari history ke job aktif.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/history/1/restore
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
  "restored_from_history_id": 1
}
```

**Response `404 Not Found`:**
```json
{
  "error": "history_not_found",
  "message": "History ID 1 not found."
}
```

---

## 7. Novels

### `GET /novels`
List semua novel yang terdaftar (agregasi dari seluruh jobs).

**Contoh cURL:**
```bash
curl -s http://localhost:18111/novels
```

**Response `200 OK`:**
```json
{
  "novels": [
    {
      "novel_id": "overlord",
      "total_jobs": 120,
      "latest_chapter": 120.0
    }
  ]
}
```

---

### `GET /novels/{novel_id}/stats`
Statistik komprehensif satu novel (jumlah job per status, total karakter, total glossary).

**Contoh cURL:**
```bash
curl -s http://localhost:18111/novels/overlord/stats
```

**Response `200 OK`:**
```json
{
  "novel_id": "overlord",
  "total_jobs": 120,
  "status_counts": {
    "done": 115,
    "pending": 3,
    "running": 1,
    "failed": 1,
    "cancelled": 0
  },
  "total_characters": 18,
  "total_glossary": 45,
  "latest_chapter": 120.0
}
```

---

### `GET /novels/{novel_id}/chapters`
Mendapatkan daftar seluruh bab (chapters) yang terdaftar untuk novel tertentu.
- **Query Params:**
  - `status`: filter status bab (`pending`, `running`, `done`, `failed`, `cancelled`)
  - `sort`: default `chapter_number:asc` (mendukung `chapter_number:desc`, `created_at:desc`, dll.)
  - `page`: nomor halaman (default `1`)
  - `limit`: jumlah item per halaman (default `100`, max `500`)
  - `fields`: filter fields tertentu (misal: `fields=chapter_number,status,has_translation`)

**Contoh cURL:**
```bash
curl -s "http://localhost:18111/novels/overlord/chapters?status=done&sort=chapter_number:asc&page=1&limit=100"
```

**Response `200 OK`:**
```json
{
  "novel_id": "overlord",
  "total": 2,
  "page": 1,
  "limit": 100,
  "chapters": [
    {
      "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
      "novel_id": "overlord",
      "chapter_number": 1.0,
      "status": "done",
      "source_lang": "ja",
      "target_lang": "id",
      "model": "gpt-5.6-luna",
      "created_at": "2026-08-15T08:00:00.000000+00:00",
      "updated_at": "2026-08-15T08:00:25.000000+00:00",
      "has_translation": true,
      "chapter_summary": "Pengenalan awal Momonga di Great Tomb of Nazarick."
    }
  ]
}
```

---

### `GET /novels/{novel_id}/jobs`
Semua job milik novel tertentu dengan filter pagination dan status.
- **Query Params:** `status`, `page` (default `1`), `limit` (default `20`, max `100`), `sort` (default `created_at:desc`), `fields`.

**Contoh cURL:**
```bash
curl -s "http://localhost:18111/novels/overlord/jobs?status=done&page=1&limit=20"
```

**Response `200 OK`:**
```json
{
  "novel_id": "overlord",
  "total": 120,
  "page": 1,
  "limit": 20,
  "items": [
    {
      "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
      "chapter_number": 1.0,
      "status": "done",
      "created_at": "2026-08-15T08:00:00.000000+00:00"
    }
  ]
}
```

---

### `GET /novels/{novel_id}/jobs/{chapter_number}`
Job bab spesifik beserta context karakter dan glossary novel yang relevan.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/novels/overlord/jobs/1.0
```

**Response `200 OK`:**
```json
{
  "job": {
    "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
    "novel_id": "overlord",
    "chapter_number": 1.0,
    "status": "done",
    "result": {
      "translation": "Bab 1: Hari Awal...",
      "chapter_summary": "Pengenalan awal Momonga..."
    }
  },
  "characters": [
    { "id": 1, "name": "Ainz Ooal Gown", "gender": "male" }
  ],
  "glossary": [
    { "id": 1, "term_source": "ナザリック地下大墳墓", "term_translation": "Great Tomb of Nazarick" }
  ]
}
```

---

### `GET /novels/{novel_id}/context`
Seluruh karakter dan glossary novel (biasanya digunakan untuk prompt context builder).

**Contoh cURL:**
```bash
curl -s http://localhost:18111/novels/overlord/context
```

**Response `200 OK`:**
```json
{
  "novel_id": "overlord",
  "characters": [
    { "id": 1, "name": "Ainz Ooal Gown", "native_name": "アインズ・ウール・ゴウン", "gender": "male" }
  ],
  "glossary": [
    { "id": 1, "term_source": "ナザリック地下大墳墓", "term_translation": "Great Tomb of Nazarick" }
  ]
}
```

---

### `GET /novels/{novel_id}/history`
Semua history terjemahan per novel.
- **Query Params:** `chapter_number`, `page` (default `1`), `limit` (default `20`), `fields`.

**Contoh cURL:**
```bash
curl -s "http://localhost:18111/novels/overlord/history?chapter_number=1.0&page=1&limit=20"
```

**Response `200 OK`:**
```json
{
  "novel_id": "overlord",
  "total": 1,
  "page": 1,
  "limit": 20,
  "history": [
    {
      "id": 1,
      "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
      "chapter_number": 1.0,
      "archived_at": "2026-08-15T09:00:00+00:00"
    }
  ]
}
```

---

### `GET /novels/{novel_id}/history/{chapter_number}`
Riwayat arsip versi terjemahan untuk satu bab spesifik.

**Contoh cURL:**
```bash
curl -s "http://localhost:18111/novels/overlord/history/1.0?page=1&limit=20"
```

**Response `200 OK`:**
```json
{
  "novel_id": "overlord",
  "chapter_number": 1.0,
  "total": 1,
  "page": 1,
  "limit": 20,
  "history": [
    {
      "id": 1,
      "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
      "archived_at": "2026-08-15T09:00:00+00:00"
    }
  ]
}
```

---

## 8. Characters

### `GET /novels/{novel_id}/characters`
Melihat daftar karakter novel dengan filter pencarian dan pagination.
- **Query Params:** `q` (search name), `gender` (`male`/`female`/`unknown`), `chapter_from`, `chapter_to`, `page` (default `1`), `limit` (default `50`), `fields`.

**Contoh cURL:**
```bash
curl -s "http://localhost:18111/novels/overlord/characters?q=Ainz&gender=male&page=1&limit=50"
```

**Response `200 OK`:**
```json
{
  "novel_id": "overlord",
  "total": 1,
  "page": 1,
  "limit": 50,
  "items": [
    {
      "id": 1,
      "name": "Ainz Ooal Gown",
      "native_name": "アインズ・ウール・ゴウン",
      "gender": "male",
      "notes": "Penguasa Agung Makam Nazarick",
      "first_seen_chapter": 1.0,
      "last_updated_chapter": 1.0,
      "appeared_chapters": [1.0]
    }
  ]
}
```

---

### `POST /novels/{novel_id}/characters`
Menambahkan karakter baru ke novel.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/novels/overlord/characters \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Ainz Ooal Gown",
    "native_name": "アインズ・ウール・ゴウン",
    "gender": "male",
    "notes": "Penguasa Agung Makam Nazarick",
    "first_seen_chapter": 1.0,
    "appeared_chapters": [1.0]
  }'
```

**Response `201 Created`:**
```json
{
  "id": 1,
  "novel_id": "overlord",
  "name": "Ainz Ooal Gown",
  "native_name": "アインズ・ウール・ゴウン",
  "gender": "male",
  "notes": "Penguasa Agung Makam Nazarick",
  "first_seen_chapter": 1.0,
  "last_updated_chapter": 1.0,
  "appeared_chapters": [1.0]
}
```

**Response `409 Conflict` (Jika nama karakter sudah ada):**
```json
{
  "error": "character_already_exists",
  "id": 1
}
```

---

### `GET /novels/{novel_id}/characters/{character_id}`
Mengambil detail satu data karakter.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/novels/overlord/characters/1
```

**Response `200 OK`:**
```json
{
  "id": 1,
  "novel_id": "overlord",
  "name": "Ainz Ooal Gown",
  "native_name": "アインズ・ウール・ゴウン",
  "gender": "male",
  "notes": "Penguasa Agung Makam Nazarick",
  "first_seen_chapter": 1.0,
  "last_updated_chapter": 1.0,
  "appeared_chapters": [1.0]
}
```

---

### `PUT /novels/{novel_id}/characters/{character_id}`
Memperbarui informasi data karakter.

**Contoh cURL:**
```bash
curl -X PUT http://localhost:18111/novels/overlord/characters/1 \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Ainz Ooal Gown",
    "native_name": "アインズ・ウール・ゴウン",
    "gender": "male",
    "notes": "Penguasa Agung Makam Nazarick (Momonga)",
    "last_updated_chapter": 5.0,
    "appeared_chapters": [1.0, 5.0]
  }'
```

**Response `200 OK`:**
```json
{
  "id": 1,
  "novel_id": "overlord",
  "name": "Ainz Ooal Gown",
  "native_name": "アインズ・ウール・ゴウン",
  "gender": "male",
  "notes": "Penguasa Agung Makam Nazarick (Momonga)",
  "first_seen_chapter": 1.0,
  "last_updated_chapter": 5.0,
  "appeared_chapters": [1.0, 5.0]
}
```

---

### `DELETE /novels/{novel_id}/characters/{character_id}`
Menghapus karakter dari novel.

**Contoh cURL:**
```bash
curl -X DELETE http://localhost:18111/novels/overlord/characters/1
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "deleted_id": 1
}
```

---

## 9. Glossary

### `GET /novels/{novel_id}/glossary`
Melihat daftar glosarium istilah untuk novel tertentu.
- **Query Params:** `q` (search term), `page` (default `1`), `limit` (default `50`), `fields`.

**Contoh cURL:**
```bash
curl -s "http://localhost:18111/novels/overlord/glossary?q=Nazarick&page=1&limit=50"
```

**Response `200 OK`:**
```json
{
  "novel_id": "overlord",
  "total": 1,
  "page": 1,
  "limit": 50,
  "items": [
    {
      "id": 1,
      "term_source": "ナザリック地下大墳墓",
      "term_translation": "Makam Bawah Tanah Agung Nazarick",
      "notes": "Basis utama",
      "first_seen_chapter": 1.0,
      "last_updated_chapter": 1.0
    }
  ]
}
```

---

### `POST /novels/{novel_id}/glossary`
Menambahkan satu istilah baru ke dalam glosarium.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/novels/overlord/glossary \
  -H "Content-Type: application/json" \
  -d '{
    "term_source": "ナザリック地下大墳墓",
    "term_translation": "Makam Bawah Tanah Agung Nazarick",
    "notes": "Basis utama",
    "first_seen_chapter": 1.0
  }'
```

**Response `201 Created`:**
```json
{
  "id": 1,
  "novel_id": "overlord",
  "term_source": "ナザリック地下大墳墓",
  "term_translation": "Makam Bawah Tanah Agung Nazarick",
  "notes": "Basis utama",
  "first_seen_chapter": 1.0,
  "last_updated_chapter": 1.0
}
```

**Response `409 Conflict` (Jika term source sudah ada):**
```json
{
  "error": "term_already_exists",
  "id": 1
}
```

---

### `POST /novels/{novel_id}/glossary/bulk`
Bulk import / upsert banyak istilah sekaligus ke glosarium novel.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/novels/overlord/glossary/bulk \
  -H "Content-Type: application/json" \
  -d '{
    "first_seen_chapter": 1.0,
    "terms": [
      {
        "term_source": "ナザリック地下大墳墓",
        "term_translation": "Great Tomb of Nazarick",
        "notes": "Basis utama"
      },
      {
        "term_source": "ユグドラシル",
        "term_translation": "Yggdrasil",
        "notes": "Nama game DMMO-RPG"
      }
    ]
  }'
```

**Response `200 OK`:**
```json
{
  "inserted": 2,
  "updated": 0,
  "total": 2
}
```

---

### `GET /novels/{novel_id}/glossary/export`
Export data glosarium dalam format `json` atau `csv`.
- **Query Params:** `format` (`json` atau `csv`, default: `json`).

**Contoh cURL (Export JSON):**
```bash
curl -s "http://localhost:18111/novels/overlord/glossary/export?format=json"
```

**Contoh cURL (Export CSV):**
```bash
curl -OJ "http://localhost:18111/novels/overlord/glossary/export?format=csv"
```

**Response `200 OK` (Format JSON):**
```json
{
  "novel_id": "overlord",
  "exported_at": "2026-08-15T12:00:00.000000+00:00",
  "terms": [
    {
      "term_source": "ナザリック地下大墳墓",
      "term_translation": "Great Tomb of Nazarick",
      "notes": "Basis utama",
      "first_seen_chapter": 1.0,
      "last_updated_chapter": 1.0
    }
  ]
}
```

---

### `GET /novels/{novel_id}/glossary/{glossary_id}`
Mengambil detail satu entri glosarium.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/novels/overlord/glossary/1
```

**Response `200 OK`:**
```json
{
  "id": 1,
  "novel_id": "overlord",
  "term_source": "ナザリック地下大墳墓",
  "term_translation": "Great Tomb of Nazarick",
  "notes": "Basis utama",
  "first_seen_chapter": 1.0,
  "last_updated_chapter": 1.0
}
```

---

### `PUT /novels/{novel_id}/glossary/{glossary_id}`
Memperbarui terjemahan atau catatan entri glosarium.

**Contoh cURL:**
```bash
curl -X PUT http://localhost:18111/novels/overlord/glossary/1 \
  -H "Content-Type: application/json" \
  -d '{
    "term_source": "ナザリック地下大墳墓",
    "term_translation": "Great Tomb of Nazarick",
    "notes": "Updated translation note",
    "last_updated_chapter": 3.0
  }'
```

**Response `200 OK`:**
```json
{
  "id": 1,
  "novel_id": "overlord",
  "term_source": "ナザリック地下大墳墓",
  "term_translation": "Great Tomb of Nazarick",
  "notes": "Updated translation note",
  "first_seen_chapter": 1.0,
  "last_updated_chapter": 3.0
}
```

---

### `DELETE /novels/{novel_id}/glossary/{glossary_id}`
Menghapus satu entri glosarium.

**Contoh cURL:**
```bash
curl -X DELETE http://localhost:18111/novels/overlord/glossary/1
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "deleted_id": 1
}
```

---

## 10. Worker Endpoints (Internal, `X-Worker-Key`)

Endpoint internal untuk komunikasi background translation worker. Memerlukan header `X-Worker-Key` jika dikonfigurasi via env `WORKER_SECRET_KEY`.

### `GET /worker/jobs/next`
Atomically claim job berstatus `pending` menjadi `running`. Mengembalikan `204 No Content` jika tidak ada job dalam antrean.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/worker/jobs/next \
  -H "X-Worker-Key: your-worker-secret-key"
```

**Response `200 OK` (Job ditemukan & di-claim):**
```json
{
  "id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
  "novel_id": "overlord",
  "chapter_number": 1.0,
  "status": "running",
  "model": "gpt-5.6-luna",
  "source_lang": "ja",
  "target_lang": "id",
  "text": "第1章　終わりの始まり..."
}
```

---

### `PATCH /worker/jobs/{job_id}/status`
Update status penyelesaian translasi dari worker (`done` atau `failed`).

**Contoh cURL (Status `done`):**
```bash
curl -X PATCH http://localhost:18111/worker/jobs/c1f7b0a9-8356-4c22-9856-78fa3e9e1c12/status \
  -H "X-Worker-Key: your-worker-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "done",
    "result_translation": "Bab 1: Hari Awal\n\nMomonga sedang duduk di atas takhta.",
    "result_summary": "Pengenalan awal Momonga di Great Tomb of Nazarick.",
    "raw_response": "{\"translation\": \"...\"}",
    "cleaned_response": "{\"translation\": \"...\"}"
  }'
```

**Contoh cURL (Status `failed`):**
```bash
curl -X PATCH http://localhost:18111/worker/jobs/c1f7b0a9-8356-4c22-9856-78fa3e9e1c12/status \
  -H "X-Worker-Key: your-worker-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "failed",
    "error_message": "LLM timeout after 120s"
  }'
```

**Response `200 OK`:**
```json
{
  "ok": true
}
```

---

### `GET /worker/jobs/running`
List seluruh running jobs untuk keperluan monitoring dan deteksi dead-job / timeout.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/worker/jobs/running \
  -H "X-Worker-Key: your-worker-secret-key"
```

**Response `200 OK`:**
```json
{
  "running_jobs": [
    {
      "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
      "novel_id": "overlord",
      "chapter_number": 1.0,
      "started_at": "2026-08-15T08:00:05.000000+00:00"
    }
  ]
}
```

---

## 11. Internal Gateway Endpoints (`X-Internal-Key` / Localhost)

Endpoint internal tingkat rendah untuk komunikasi langsung ke instance browser Playwright. Memerlukan header `X-Internal-Key` jika diakses di luar localhost.

### `GET /_internal/status`
Status kesehatan dan metrik operasional browser Playwright.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/_internal/status \
  -H "X-Internal-Key: your-internal-secret-key"
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "title": "ChatGPT",
  "error": null,
  "turns": 4,
  "busy": false,
  "busy_since": null,
  "last_activity": 1771056789.12,
  "idle_s": 12.5
}
```

---

### `GET /_internal/debug`
Evaluasi DOM live dan detail state halaman ChatGPT Web.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/_internal/debug \
  -H "X-Internal-Key: your-internal-secret-key"
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "info": {
    "url": "https://chatgpt.com/",
    "has_input": true,
    "is_logged_in": true,
    "last_turn_count": 4
  }
}
```

---

### `POST /_internal/chat`
Eksekusi interaksi prompt synchronous langsung ke browser.

**Contoh cURL:**
```bash
curl -X POST http://localhost:18111/_internal/chat \
  -H "X-Internal-Key: your-internal-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Translate this sentence to Indonesian: Hello world",
    "model": "gpt-5.6-luna",
    "reset": true
  }'
```

**Response `200 OK`:**
```json
{
  "text": "Halo dunia",
  "turns": 5
}
```

---

### `POST /_internal/chat/stream`
Eksekusi streaming SSE chat langsung dari engine Playwright.

**Contoh cURL:**
```bash
curl -N -X POST http://localhost:18111/_internal/chat/stream \
  -H "X-Internal-Key: your-internal-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Translate this sentence to Indonesian: Hello world",
    "model": "gpt-5.6-luna",
    "reset": true
  }'
```

---

## 12. Database Backup & Restore Endpoints

Endpoint untuk mencadangkan (backup) seluruh database SQLite ke dalam file ZIP terkompresi, serta memulihkan (restore) database dari file ZIP backup.

### `GET /database/backup` (atau `POST /database/backup`)
Mendownload ZIP archive konsisten dari database (`translation.db`) beserta metadata snapshot (`metadata.json`).

**Response Header:**
- `Content-Type: application/zip`
- `Content-Disposition: attachment; filename="hermes_backup_YYYYMMDD_HHMMSS.zip"`
- `X-Hermes-Backup-Date: 2026-08-15T16:15:00+00:00`

**Isi file ZIP:**
1. `translation.db` (Snapshot database SQLite)
2. `metadata.json` (Detail versi, timestamp ISO, dan statistik record per tabel)

**Contoh cURL:**
```bash
curl -OJ http://localhost:18111/database/backup
```

---

### `POST /database/restore`
Memulihkan database SQLite dari file ZIP backup.
Mendukung upload via `multipart/form-data` (`file`) maupun raw binary payload `application/zip`.

**Contoh cURL (Multipart Form):**
```bash
curl -X POST http://localhost:18111/database/restore \
  -F "file=@hermes_backup_20260815_161500.zip"
```

**Contoh cURL (Binary Data):**
```bash
curl -X POST http://localhost:18111/database/restore \
  -H "Content-Type: application/zip" \
  --data-binary @hermes_backup_20260815_161500.zip
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "message": "Database successfully restored from backup.",
  "stats": {
    "translation_jobs": 12,
    "characters": 5,
    "glossary": 20,
    "translation_history": 8,
    "novels_count": 2
  },
  "restored_at": "2026-08-15T16:15:30.123456+00:00"
}
```

**Response `400 Bad Request` (Invalid ZIP / Corrupt Header):**
```json
{
  "error": "invalid_backup_zip",
  "message": "No valid SQLite database file found inside ZIP archive (expected translation.db or *.db)"
}
```

---

### `GET /database/stats`
Melihat ringkasan total record pada seluruh tabel database saat ini.

**Contoh cURL:**
```bash
curl -s http://localhost:18111/database/stats
```

**Response `200 OK`:**
```json
{
  "ok": true,
  "stats": {
    "translation_jobs": 12,
    "characters": 5,
    "glossary": 20,
    "translation_history": 8,
    "novels_count": 2
  }
}
```

---

## 13. Selective Field Filtering (`?fields=...`)

API mendukung parameter query `fields` pada beberapa endpoint krusial untuk mengambil data spesifik, memangkas payload berukuran besar (seperti `result`, `raw_response`, `cleaned_response`), dan mengoptimalkan performa transfer data.

Format: string dipisahkan koma (comma-separated), mendukung top-level fields dan nested dot-notation (misal `result.translation`).

### Contoh Penggunaan:
1. **List Chapters:**
   ```bash
   curl -s "http://localhost:18111/novels/overlord/chapters?fields=chapter_number,status,has_translation"
   ```
   ```json
   {
     "novel_id": "overlord",
     "total": 2,
     "page": 1,
     "limit": 100,
     "chapters": [
       { "chapter_number": 1.0, "status": "done", "has_translation": true },
       { "chapter_number": 2.0, "status": "pending", "has_translation": false }
     ]
   }
   ```

2. **Job Detail (Hanya translasi teks bersih):**
   ```bash
   curl -s "http://localhost:18111/translate/c1f7b0a9-8356-4c22-9856-78fa3e9e1c12?fields=job_id,status,result.translation"
   ```
   ```json
   {
     "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12",
     "status": "done",
     "result": {
       "translation": "Bab 1: ..."
     }
   }
   ```

3. **Characters List:**
   ```bash
   curl -s "http://localhost:18111/novels/overlord/characters?fields=id,name,gender"
   ```

4. **Glossary List:**
   ```bash
   curl -s "http://localhost:18111/novels/overlord/glossary?fields=id,term_source,term_translation"
   ```

---

## 14. Error Codes Standar

Semua error response menggunakan format standar:
```json
{ "error": "<code>", "message": "..." }
```

| Code | HTTP | Keterangan |
|---|---|---|
| `chapter_already_translated` | 409 | Job sudah ada, gunakan `force:true` |
| `job_not_found` | 404 | Job ID tidak ditemukan |
| `job_not_retryable` | 400 | Status bukan `failed` |
| `job_not_cancellable` | 400 | Status sudah final (`done`/`cancelled`) |
| `job_not_deletable` | 400 | Job masih aktif, batalkan terlebih dahulu |
| `character_already_exists` | 409 | Nama karakter duplikat di novel ini |
| `character_not_found` | 404 | Karakter tidak ditemukan |
| `term_already_exists` | 409 | Term source duplikat di novel ini |
| `term_not_found` | 404 | Term glosarium tidak ditemukan |
| `invalid_backup_zip` | 400 | File ZIP backup rusak atau tidak memuat DB SQLite valid |
| `missing_file` | 400 | Tidak ada file ZIP yang dikirim pada request restore |
| `LLM_INVALID_JSON` | worker | Response LLM bukan JSON valid |
| `LLM_TIMEOUT` | worker | Model tidak merespons dalam batas waktu |
| `LLM_CONTEXT_OVERFLOW` | 400/worker | Teks terlalu panjang untuk diproses |
| `BROWSER_NOT_READY` | 503 | Playwright belum siap atau session belum aktif |
