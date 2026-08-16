# PRD: Hermes Novel Translation System

**Version:** 1.0.0
**Target:** LLM Coding Agent
**Stack:** Python · FastAPI · SQLite · Playwright (Chromium)

---

## 1. Overview

Hermes adalah sistem terjemahan novel berbasis antrian asinkron yang menggunakan ChatGPT Web (via Playwright browser automation) sebagai LLM backend. Sistem berjalan sebagai **satu FastAPI application pada satu port**, menggabungkan Public REST API, Background Worker, dan Internal Playwright Gateway menjadi satu service tunggal.

---

## 2. Arsitektur

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

**Prinsip arsitektur:**

- Satu port (`18111`), satu proses FastAPI
- Playwright dipanggil via `asyncio.to_thread()` — tidak boleh blocking event loop
- Internal gateway diakses via **function call langsung**, bukan HTTP internal terpisah
- SQLite WAL mode + foreign keys aktif sejak koneksi pertama
- Background worker berjalan sebagai asyncio task di dalam proses yang sama

---

## 3. Startup & Terminal Logging

### 3.1 Urutan Startup

Startup dilakukan secara berurutan dan blocking — **service tidak boleh menerima request publik sebelum semua tahap selesai**.

```
[STARTUP] Hermes Novel Translation System
[STARTUP] Initializing database...
[DB]      WAL mode enabled
[DB]      Foreign keys enabled
[DB]      Schema applied (4 tables, 6 indexes)
[STARTUP] Launching Playwright browser...
[BROWSER] Chromium launched (headless=true)
[BROWSER] Loading session cookies...
[BROWSER] Navigating to ChatGPT Web...
[BROWSER] Page ready — title: "ChatGPT"
[BROWSER] Textarea detected at (300, 750)
[STARTUP] Background worker task started
──────────────────────────────────────────────
  ✓  Hermes is ready
  ➜  Adapter API  →  http://0.0.0.0:18111
──────────────────────────────────────────────
```

**Aturan:**

- Baris `✓  Hermes is ready` dan `➜  Adapter API → http://0.0.0.0:18111` **hanya muncul setelah Playwright benar-benar siap** (textarea terdeteksi, page title valid)
- Jika browser gagal siap dalam timeout, log `[ERROR]` lalu exit dengan kode non-zero
- Port yang tampil di log mengikuti nilai aktual `ADAPTER_PORT` (default `18111`)

### 3.2 Format Log

Setiap baris log menggunakan format:

```
[TIMESTAMP] [LEVEL] [TAG] pesan
```

Contoh:

```
2026-08-15 08:00:01.123 INFO  [DB]      Schema applied (4 tables, 6 indexes)
2026-08-15 08:00:03.456 INFO  [BROWSER] Page ready — title: "ChatGPT"
2026-08-15 08:00:03.789 INFO  [WORKER]  Background task started, polling interval: 2s
2026-08-15 08:00:04.001 INFO  [SERVER]  Listening on http://0.0.0.0:18111
```

**Tag yang digunakan:**

| Tag | Digunakan untuk |
|---|---|
| `[STARTUP]` | Inisialisasi awal sebelum komponen siap |
| `[DB]` | Operasi database (init, migration, error) |
| `[BROWSER]` | Status Playwright dan Chromium |
| `[WORKER]` | Background job worker |
| `[SERVER]` | FastAPI server lifecycle |
| `[JOB]` | Status perubahan job terjemahan |
| `[SESSION]` | Injeksi cookie/session |
| `[ERROR]` | Error fatal |

### 3.3 Log Perubahan Status Job

Setiap transisi status job **wajib** menghasilkan satu baris log:

```
2026-08-15 08:01:00.000 INFO  [JOB] c1f7b0a9 | overlord ch.1   | pending   → running
2026-08-15 08:01:25.000 INFO  [JOB] c1f7b0a9 | overlord ch.1   | running   → done      (25.3s)
```

Format:

```
[JOB] {job_id_short} | {novel_id} ch.{chapter} | {status_lama:<10} → {status_baru:<10} ({durasi})
```

- `job_id_short` = 8 karakter pertama UUID
- Durasi hanya tampil saat transisi ke `done` atau `failed` (dihitung dari `running → done/failed`)
- Untuk `failed`, tambahkan error code:

```
2026-08-15 08:01:30.000 ERROR [JOB] c1f7b0a9 | overlord ch.1   | running   → failed    (30.1s) [LLM_INVALID_JSON]
```

### 3.4 Log Retry

```
2026-08-15 08:02:00.000 WARN  [JOB] c1f7b0a9 | overlord ch.1   | retry #1 scheduled
2026-08-15 08:02:05.000 INFO  [JOB] c1f7b0a9 | overlord ch.1   | pending   → running   (retry #1)
```

### 3.5 Log Worker Idle

Jika tidak ada job pending, worker tidak spam log. Cukup log sekali saat pertama idle dan sekali saat kembali aktif:

```
2026-08-15 08:05:00.000 INFO  [WORKER] No pending jobs — idle
2026-08-15 08:10:00.000 INFO  [WORKER] Job found — resuming
```

### 3.6 Log Session Inject

```
2026-08-15 09:00:00.000 INFO  [SESSION] Injecting new session cookies...
2026-08-15 09:00:01.000 INFO  [SESSION] Session applied — 12 cookies set
```

---

## 4. Database Schema

### 4.1 Inisialisasi (jalankan di setiap koneksi)

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
```

### 4.2 Tabel: `translation_jobs`

```sql
CREATE TABLE IF NOT EXISTS translation_jobs (
    id                  TEXT    PRIMARY KEY,
    novel_id            TEXT    NOT NULL,
    chapter_number      REAL    NOT NULL,
    source_lang         TEXT    NOT NULL,
    target_lang         TEXT    NOT NULL,
    source_text_raw     TEXT    NOT NULL,
    source_text_cleaned TEXT    NOT NULL,
    model               TEXT    NOT NULL DEFAULT 'gpt-5.6-luna',
    status              TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','running','done','failed','cancelled')),
    result_translation  TEXT,
    result_summary      TEXT,
    raw_response        TEXT,
    cleaned_response    TEXT,
    error_code          TEXT,
    error_message       TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    UNIQUE(novel_id, chapter_number)
);

CREATE INDEX IF NOT EXISTS idx_jobs_novel_status ON translation_jobs(novel_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_status       ON translation_jobs(status);
```

### 4.3 Tabel: `characters`

```sql
CREATE TABLE IF NOT EXISTS characters (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id             TEXT    NOT NULL,
    name                 TEXT    NOT NULL,
    native_name          TEXT    NOT NULL,
    gender               TEXT    NOT NULL DEFAULT 'unknown'
                             CHECK(gender IN ('male','female','unknown')),
    notes                TEXT    NOT NULL DEFAULT '',
    first_seen_chapter   REAL    NOT NULL,
    last_updated_chapter REAL    NOT NULL,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL,
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_characters_novel ON characters(novel_id);
```

### 4.4 Tabel: `glossary`

```sql
CREATE TABLE IF NOT EXISTS glossary (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id             TEXT    NOT NULL,
    term_source          TEXT    NOT NULL,
    term_translation     TEXT    NOT NULL,
    notes                TEXT    NOT NULL DEFAULT '',
    first_seen_chapter   REAL    NOT NULL,
    last_updated_chapter REAL    NOT NULL,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL,
    UNIQUE(novel_id, term_source)
);

CREATE INDEX IF NOT EXISTS idx_glossary_novel ON glossary(novel_id);
```

### 4.5 Tabel: `translation_history`

```sql
CREATE TABLE IF NOT EXISTS translation_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id             TEXT    NOT NULL REFERENCES translation_jobs(id) ON DELETE CASCADE,
    novel_id           TEXT    NOT NULL,
    chapter_number     REAL    NOT NULL,
    result_translation TEXT,
    result_summary     TEXT,
    archived_at        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_job   ON translation_history(job_id);
CREATE INDEX IF NOT EXISTS idx_history_novel ON translation_history(novel_id, chapter_number);
```

### 4.6 Tabel: `account_cookies`

```sql
CREATE TABLE IF NOT EXISTS account_cookies (
    id                   TEXT    PRIMARY KEY,
    name                 TEXT    NOT NULL,
    provider             TEXT    NOT NULL DEFAULT 'chatgpt',
    cookies_data         TEXT    NOT NULL,
    status               TEXT    NOT NULL DEFAULT 'ACTIVE'
                                 CHECK(status IN ('ACTIVE','BUSY','COOLDOWN','EXPIRED','PAUSED')),
    cooldown_count       INTEGER NOT NULL DEFAULT 0,
    cooldown_until       TEXT,
    last_used_at         TEXT,
    error_message        TEXT,
    total_jobs_processed INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL,
    UNIQUE(name, provider)
);

CREATE INDEX IF NOT EXISTS idx_accounts_status ON account_cookies(status);
```

### 4.7 Tabel: `app_settings`

```sql
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

---

## 5. Semua Endpoint


**Base URL:** `http://host:18111`

---

### 5.1 Health & Info

#### `GET /health`

Status adapter dan browser Playwright.

**Response 200:**
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

#### `GET /v1/models`

Daftar model tersedia, format OpenAI-compatible.

**Response 200:**
```json
{
  "object": "list",
  "data": [
    { "id": "gpt-5.6-luna", "object": "model", "owned_by": "openai" }
  ]
}
```

---

### 5.2 Chat (OpenAI-Compatible)

#### `POST /v1/chat/completions`

Chat completions, mendukung streaming SSE dan non-streaming.

**Request Body:**

| Field | Type | Required | Default | Keterangan |
|---|---|---|---|---|
| `model` | string | No | `"auto"` | Nama model |
| `messages` | array | Yes | — | `role`: system/user/assistant, `content`: string |
| `stream` | boolean | No | `false` | SSE jika `true` |

```json
{
  "model": "gpt-5.6-luna",
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user",   "content": "Halo" }
  ],
  "stream": false
}
```

**Response 200 (non-streaming):**
```json
{
  "id": "chatcmpl-a1b2c3",
  "object": "chat.completion",
  "created": 1771056800,
  "model": "gpt-5.6-luna",
  "choices": [{
    "index": 0,
    "message": { "role": "assistant", "content": "Halo! ..." },
    "finish_reason": "stop"
  }],
  "usage": null
}
```

**Response 200 (streaming `stream: true`):**
```
Content-Type: text/event-stream

data: {"id":"chatcmpl-a1b2c3","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Halo"},"finish_reason":null}]}

data: [DONE]
```

---

### 5.3 Session & Cookies

#### `POST /cookies/inject-session`

Injeksi cookie/session ke Chromium tanpa restart server.

**Request Body:**
```json
{
  "token": "ey...",
  "cookies": [
    { "name": "__Secure-next-auth.session-token", "value": "..." }
  ]
}
```

**Response 200:**
```json
{ "ok": true, "message": "Session injected successfully", "output": "added 12 cookie assignments..." }
```

**Response 500:**
```json
{ "error": { "message": "Session injection failed", "details": "..." } }
```

---

### 5.4 System Settings & Post-Job Cooldown

Hermes menerapkan **Per-Context Post-Job Cooldown** (default **60 detik**):
- Setiap context/worker akun yang telah menyelesaikan suatu job otomatis masuk ke fase cooldown selama `job_cooldown_seconds` (default: 60 detik).
- Context lain yang idle dan tidak sedang cooldown dapat langsung memproses job berikutnya secara paralel tanpa terhalang.
- Jika hanya tersedia 1 context akun, context tersebut tetap beristirahat selama 60 detik sebelum mengambil job antrean selanjutnya.
- Durasi cooldown dapat dikonfigurasi kapan saja via endpoint `/settings` dan **langsung berlaku seketika pada job berikutnya**.

#### `GET /settings`
Mengambil semua setting aktif saat ini.

**Response 200:**
```json
{
  "ok": true,
  "settings": {
    "job_cooldown_seconds": 60,
    "worker_poll_interval": 2.0,
    "worker_concurrency": 1,
    "translation_job_timeout": 120,
    "translation_max_text_length": 100000
  }
}
```

#### `PATCH /settings` (alias: `PUT /settings`, `POST /settings`)
Memperbarui pengaturan sistem secara dinamis.

**Request Body:**
```json
{
  "job_cooldown_seconds": 30
}
```

**Response 200:**
```json
{
  "ok": true,
  "message": "Settings updated successfully",
  "settings": {
    "job_cooldown_seconds": 30,
    "worker_poll_interval": 2.0,
    "worker_concurrency": 1,
    "translation_job_timeout": 120,
    "translation_max_text_length": 100000
  }
}
```

#### `POST /settings/reset`
Mengembalikan pengaturan ke nilai default.

---

### 5.5 Translation Jobs

#### `POST /translate`

Daftarkan job terjemahan baru ke antrian.


**Request Body:**

| Field | Type | Required | Default | Keterangan |
|---|---|---|---|---|
| `model` | string | Yes | — | Model identifier |
| `source_lang` | string | Yes | — | Kode bahasa asal (ja/zh/ko/en) |
| `target_lang` | string | Yes | — | Kode bahasa tujuan (id/en/...) |
| `novel_id` | string | Yes | — | ID unik novel |
| `chapter_number` | number | Yes | — | Nomor bab, bisa float (1.5) |
| `text` | string | Yes | — | Teks mentah bab |
| `force` | boolean | No | `false` | Paksa re-translasi jika sudah ada |

```json
{
  "model": "gpt-5.6-luna",
  "source_lang": "ja",
  "target_lang": "id",
  "novel_id": "overlord",
  "chapter_number": 1,
  "text": "第1話：始まりの日\n\nモモンガは玉座に座っていた。",
  "force": false
}
```

**Response 202:**
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

**Response 409** (sudah ada dan `force: false`):
```json
{
  "error": "chapter_already_translated",
  "hint": "use force:true to re-translate",
  "job_id": "c1f7b0a9-8356-4c22-9856-78fa3e9e1c12"
}
```

> Jika `force: true`: arsipkan terjemahan lama ke `translation_history`, kemudian reset job ke `pending`.

---

#### `GET /translate`

List semua job dengan filter dan pagination.

**Query Params:**

| Param | Type | Default | Keterangan |
|---|---|---|---|
| `novel_id` | string | — | Filter per novel |
| `status` | string | — | pending/running/done/failed/cancelled |
| `page` | int | `1` | Nomor halaman |
| `limit` | int | `20` | Jumlah per halaman (max 100) |
| `sort` | string | `created_at:desc` | `field:asc` atau `field:desc` |

**Response 200:**
```json
{
  "total": 120,
  "page": 1,
  "limit": 20,
  "items": [{ "...job fields..." }]
}
```

---

#### `GET /translate/{job_id}`

Polling status dan hasil terjemahan.

**Response 200 (status `done`):**
```json
{
  "job_id": "c1f7b0a9-...",
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

**Response 200 (status `failed`):**
```json
{
  "job_id": "c1f7b0a9-...",
  "status": "failed",
  "result": null,
  "error": {
    "code": "LLM_INVALID_JSON",
    "message": "Model response could not be parsed as valid JSON.",
    "retry_count": 2
  },
  "raw_response": "...",
  "cleaned_response": "..."
}
```

---

#### `POST /translate/{job_id}/retry`

Retry manual job yang berstatus `failed`.

**Response 200:**
```json
{ "status": "pending", "job_id": "c1f7b0a9-..." }
```

**Response 400** (status bukan `failed`):
```json
{ "error": "job_not_retryable", "current_status": "done" }
```

---

#### `POST /translate/{job_id}/cancel`

Cancel job yang masih `pending` atau `running`.

**Response 200:**
```json
{ "status": "cancelled", "job_id": "c1f7b0a9-..." }
```

**Response 400:**
```json
{ "error": "job_not_cancellable", "current_status": "done" }
```

---

#### `POST /translate/cancel-all`

Cancel massal seluruh job yang berstatus `pending`, `running`, atau keduanya (`all` / `both`), dengan opsional filter `novel_id`.

**Query Parameters / Request Body:**
- `status`: `"pending"`, `"running"`, `"all"`, atau `"both"` (default: `"all"`)
- `novel_id`: opsional filter per novel ID

**Response 200:**
```json
{
  "status": "cancelled",
  "filter": "all",
  "novel_id": "overlord",
  "cancelled_count": 2,
  "cancelled_pending": 1,
  "cancelled_running": 1,
  "cancelled_job_ids": ["c1f7b0a9-...", "d2a8b1c0-..."],
  "cancelled_jobs": [
    { "id": "c1f7b0a9-...", "novel_id": "overlord", "chapter_number": 1.0, "previous_status": "pending" },
    { "id": "d2a8b1c0-...", "novel_id": "overlord", "chapter_number": 2.0, "previous_status": "running" }
  ]
}
```

---

#### `DELETE /translate/{job_id}`

Hapus job dari database. Hanya boleh jika status `pending` atau `cancelled`.

**Response 200:**
```json
{ "status": "deleted", "job_id": "c1f7b0a9-..." }
```

**Response 400:**
```json
{ "error": "job_not_deletable", "hint": "cancel the job first" }
```

---

#### `GET /translate/{job_id}/history`

Riwayat semua versi terjemahan job (diarsipkan saat `force: true`).

**Response 200:**
```json
{
  "job_id": "c1f7b0a9-...",
  "history": [
    {
      "id": 1,
      "result_translation": "...",
      "result_summary": "...",
      "archived_at": "2026-08-15T09:00:00.000000+00:00"
    }
  ]
}
```

---

### 5.5 History

#### `POST /history/{history_id}/restore`

Restore versi terjemahan lama dari history ke job aktif.

**Response 200:**
```json
{ "ok": true, "job_id": "c1f7b0a9-...", "restored_from_history_id": 1 }
```

---

### 5.6 Novels

#### `GET /novels`

List semua novel yang terdaftar (agregasi dari jobs).

**Response 200:**
```json
{
  "novels": [
    { "novel_id": "overlord", "total_jobs": 120, "latest_chapter": 120.0 }
  ]
}
```

---

#### `GET /novels/{novel_id}/stats`

Statistik lengkap satu novel.

**Response 200:**
```json
{
  "novel_id": "overlord",
  "total_jobs": 120,
  "by_status": {
    "done": 100,
    "pending": 15,
    "running": 0,
    "failed": 5,
    "cancelled": 0
  },
  "total_characters": 42,
  "total_glossary_terms": 200,
  "latest_chapter": 120.0
}
```

---

#### `GET /novels/{novel_id}/chapters`

Mendapatkan daftar semua chapter untuk suatu novel beserta status dan informasi ketersediaan terjemahan.

- **Query Params:**
  - `status`: filter status (`pending`, `running`, `done`, `failed`, `cancelled`)
  - `sort`: default `chapter_number:asc` (mendukung `chapter_number:desc`, `created_at:desc`, dll.)
  - `page`: nomor halaman (default `1`)
  - `limit`: jumlah item (default `100`, max `500`)
  - `fields`: filter fields tertentu (misal `fields=chapter_number,status,has_translation`)

**Response 200:**
```json
{
  "novel_id": "overlord",
  "total": 120,
  "page": 1,
  "limit": 100,
  "chapters": [
    {
      "job_id": "c1f7b0a9-...",
      "novel_id": "overlord",
      "chapter_number": 1.0,
      "status": "done",
      "source_lang": "ja",
      "target_lang": "id",
      "model": "gpt-5.6-luna",
      "created_at": "2026-08-15T08:00:00.000000+00:00",
      "updated_at": "2026-08-15T08:00:25.000000+00:00",
      "has_translation": true,
      "chapter_summary": "..."
    }
  ]
}
```

---

#### `GET /novels/{novel_id}/jobs`

Semua job milik satu novel. Query params sama dengan `GET /translate` (tanpa `novel_id`). Mendukung parameter `fields`.


---

#### `GET /novels/{novel_id}/jobs/{chapter_number}`

Job spesifik satu chapter beserta context karakter dan glossary.

**Response 200:**
```json
{
  "job": {
    "id": "...",
    "novel_id": "overlord",
    "chapter_number": 1.0,
    "status": "done",
    "result_translation": "...",
    "translate_md": "...",
    "result_summary": "..."
  },
  "characters": [{ "...character fields..." }],
  "glossary": [{ "...glossary fields..." }]
}
```

---

#### `GET /novels/{novel_id}/context`

Seluruh karakter dan glossary novel (dipakai saat inject context ke prompt LLM).

**Response 200:**
```json
{
  "novel_id": "overlord",
  "characters": [
    {
      "name": "Momonga",
      "native_name": "モモンガ",
      "gender": "male",
      "notes": "Protagonis utama, Guildmaster Ainz Ooal Gown.",
      "first_seen_chapter": 1.0
    }
  ],
  "glossary": [
    {
      "term_source": "YGGDRASIL",
      "term_translation": "Yggdrasil",
      "notes": "Nama game DMMO-RPG."
    }
  ]
}
```

---

#### `GET /novels/{novel_id}/history`

Semua history terjemahan per novel.

**Query Params:** `chapter_number` (optional), `page`, `limit`

---

#### `GET /novels/{novel_id}/history/{chapter_number}`

History terjemahan untuk chapter spesifik.

---

### 5.7 Characters

#### `GET /novels/{novel_id}/characters`

List semua karakter novel.

**Query Params:** `q` (search name/native_name), `gender`, `chapter_from`, `chapter_to`, `page`, `limit`

**Response 200:**
```json
{
  "total": 42,
  "items": [
    {
      "id": 1,
      "novel_id": "overlord",
      "name": "Momonga",
      "native_name": "モモンガ",
      "gender": "male",
      "notes": "Protagonis utama.",
      "first_seen_chapter": 1.0,
      "last_updated_chapter": 1.0
    }
  ]
}
```

---

#### `POST /novels/{novel_id}/characters`

Tambah karakter baru.

```json
{
  "name": "Momonga",
  "native_name": "モモンガ",
  "gender": "male",
  "notes": "Protagonis utama.",
  "first_seen_chapter": 1.0
}
```

**Response 201:** Data karakter yang baru dibuat.

**Response 409:**
```json
{ "error": "character_already_exists", "id": 1 }
```

---

#### `GET /novels/{novel_id}/characters/{id}`

Detail satu karakter.

---

#### `PUT /novels/{novel_id}/characters/{id}`

Update karakter. Semua field opsional.

```json
{
  "name": "Ainz Ooal Gown",
  "notes": "Nama Momonga sebagai Overlord.",
  "last_updated_chapter": 5.0
}
```

---

#### `DELETE /novels/{novel_id}/characters/{id}`

Hapus karakter.

**Response 200:**
```json
{ "ok": true, "deleted_id": 1 }
```

---

### 5.8 Glossary

#### `GET /novels/{novel_id}/glossary`

List semua istilah. **Query Params:** `q` (search term_source), `page`, `limit`

---

#### `POST /novels/{novel_id}/glossary`

Tambah satu istilah baru.

```json
{
  "term_source": "灵气",
  "term_translation": "Energi Spiritual",
  "notes": "Energi alam semesta.",
  "first_seen_chapter": 1.0
}
```

**Response 201:** Data glossary yang baru dibuat.

**Response 409:**
```json
{ "error": "term_already_exists", "id": 5 }
```

---

#### `GET /novels/{novel_id}/glossary/{id}`

Detail satu istilah.

---

#### `PUT /novels/{novel_id}/glossary/{id}`

Update terjemahan atau notes.

---

#### `DELETE /novels/{novel_id}/glossary/{id}`

Hapus satu istilah.

**Response 200:**
```json
{ "ok": true, "deleted_id": 5 }
```

---

#### `POST /novels/{novel_id}/glossary/bulk`

Import banyak istilah sekaligus. Jika term sudah ada, di-update (upsert).

```json
{
  "terms": [
    { "term_source": "灵气", "term_translation": "Energi Spiritual", "notes": "" },
    { "term_source": "丹田", "term_translation": "Dantian", "notes": "Pusat energi dalam tubuh." }
  ],
  "first_seen_chapter": 1.0
}
```

**Response 200:**
```json
{ "inserted": 3, "updated": 1, "skipped": 0 }
```

---

#### `GET /novels/{novel_id}/glossary/export`

Export semua glossary novel.

**Query Params:** `format` = `json` (default) | `csv`

**Response (JSON):**
```json
{
  "novel_id": "overlord",
  "exported_at": "2026-08-15T08:00:00.000000+00:00",
  "terms": [
    { "term_source": "YGGDRASIL", "term_translation": "Yggdrasil", "notes": "Nama game DMMO-RPG." }
  ]
}
```

---

### 5.9 Worker (Internal)

> Lindungi dengan header `X-Worker-Key`. Digunakan oleh background worker asyncio task.

#### `GET /worker/jobs/next`

Ambil satu job `pending` secara atomik dan langsung set status ke `running`.

**Response 200:**
```json
{
  "job_id": "c1f7b0a9-...",
  "novel_id": "overlord",
  "chapter_number": 1.0,
  "source_lang": "ja",
  "target_lang": "id",
  "source_text_cleaned": "...",
  "model": "gpt-5.6-luna",
  "retry_count": 0,
  "characters": [{ "...character context..." }],
  "glossary": [{ "...glossary context..." }]
}
```

**Response 204:** Tidak ada job pending.

---

#### `PATCH /worker/jobs/{job_id}/status`

Update hasil terjemahan atau error dari worker.

**Body (sukses):**
```json
{
  "status": "done",
  "result_translation": "...",
  "result_summary": "...",
  "raw_response": "...",
  "cleaned_response": "..."
}
```

**Body (gagal):**
```json
{
  "status": "failed",
  "error_code": "LLM_INVALID_JSON",
  "error_message": "Model response could not be parsed as valid JSON."
}
```

---

#### `GET /worker/jobs/running`

List job yang berstatus `running` untuk dead-job detection.

**Response 200:**
```json
{
  "running": [
    {
      "job_id": "...",
      "novel_id": "...",
      "chapter_number": 1.0,
      "updated_at": "2026-08-15T08:00:00.000000+00:00"
    }
  ]
}
```

---

### 5.10 Internal Gateway (Playwright)

> Dipanggil via **function call langsung** dari FastAPI — bukan HTTP terpisah. Route `/_internal/*` tersedia untuk debug/monitoring. Batasi akses ke `localhost` atau header `X-Internal-Key`.

#### `GET /_internal/status`

Status browser Playwright.

**Response 200:**
```json
{
  "ok": true,
  "title": "ChatGPT",
  "error": null,
  "turns": 2,
  "busy": false,
  "busy_since": null,
  "last_activity": 1771056800.0,
  "idle_s": 5.2
}
```

---

#### `GET /_internal/debug`

Evaluasi DOM live ChatGPT Web.

**Response 200:**
```json
{
  "ok": true,
  "info": {
    "title": "ChatGPT",
    "taPresent": true,
    "taRect": { "x": 300, "y": 750, "w": 800, "h": 50, "vw": 1280, "vh": 800 },
    "dialogs": [],
    "toasts": [],
    "body": "..."
  }
}
```

---

#### `POST /_internal/chat`

Kirim prompt tunggal ke browser, synchronous, non-streaming.

**Request Body:**
```json
{ "prompt": "Hello", "model": "auto", "reset": false }
```

**Response 200:**
```json
{ "text": "Hello! How can I help you today?" }
```

---

#### `POST /_internal/chat/stream`

Kirim prompt ke browser dan terima streaming token via SSE.

**Request Body:**
```json
{ "prompt": "Translate this...", "model": "auto", "reset": true }
```

**Response Stream (`text/event-stream`):**
```
data: {"delta": "Hello", "text": "Hello"}
data: {"delta": " world", "text": "Hello world"}
data: {"done": true, "text": "Hello world"}
```

---

## 6. Ringkasan Semua Endpoint

```
# Health & Info
GET    /health
GET    /v1/models

# Chat (OpenAI-compatible)
POST   /v1/chat/completions

# Session
POST   /cookies/inject-session

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
GET    /novels/{novel_id}/jobs
GET    /novels/{novel_id}/jobs/{chapter_number}
GET    /novels/{novel_id}/context
GET    /novels/{novel_id}/history
GET    /novels/{novel_id}/history/{chapter_number}

# Characters
GET    /novels/{novel_id}/characters
POST   /novels/{novel_id}/characters
GET    /novels/{novel_id}/characters/{id}
PUT    /novels/{novel_id}/characters/{id}
DELETE /novels/{novel_id}/characters/{id}

# Glossary
GET    /novels/{novel_id}/glossary
POST   /novels/{novel_id}/glossary
GET    /novels/{novel_id}/glossary/{id}
PUT    /novels/{novel_id}/glossary/{id}
DELETE /novels/{novel_id}/glossary/{id}
POST   /novels/{novel_id}/glossary/bulk
GET    /novels/{novel_id}/glossary/export

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

## 7. Error Codes Standar

Semua error response menggunakan format:
```json
{ "error": "<code>", "message": "..." }
```

| Code | HTTP | Keterangan |
|---|---|---|
| `chapter_already_translated` | 409 | Job sudah ada, gunakan `force:true` |
| `job_not_found` | 404 | Job ID tidak ditemukan |
| `job_not_retryable` | 400 | Status bukan `failed` |
| `job_not_cancellable` | 400 | Status sudah final (done/cancelled) |
| `job_not_deletable` | 400 | Job masih aktif, cancel dulu |
| `character_already_exists` | 409 | Nama karakter duplikat di novel ini |
| `term_already_exists` | 409 | Term source duplikat di novel ini |
| `LLM_INVALID_JSON` | worker | Response LLM bukan JSON valid |
| `LLM_TIMEOUT` | worker | Model tidak merespons dalam batas waktu |
| `LLM_CONTEXT_OVERFLOW` | worker | Teks terlalu panjang untuk model |
| `BROWSER_BUSY` | 503 | Browser sedang dipakai proses lain |
| `BROWSER_NOT_READY` | 503 | Playwright belum siap atau session expired |

---

## 8. Aturan Implementasi untuk Agent

1. **Satu entry point `main.py`** — mount semua router, inisialisasi DB, jalankan background worker loop, dan start Playwright sebelum server menerima request.

2. **Urutan startup wajib diikuti** — DB init → Playwright launch → cookie load → page ready check → worker task start → baru print banner `✓ Hermes is ready` dan mulai listen port.

3. **Playwright wajib `asyncio.to_thread()`** — semua call ke Playwright dibungkus agar tidak memblokir event loop FastAPI.

4. **Database init otomatis** — jalankan seluruh DDL (`CREATE TABLE`, `CREATE INDEX`, `PRAGMA`) saat startup jika tabel belum ada.

5. **Worker loop** — background `asyncio.Task` yang polling `GET /worker/jobs/next` setiap interval (default 2s), lalu memanggil Playwright dan `PATCH /worker/jobs/{job_id}/status`.

6. **`force: true` pada `POST /translate`** — sebelum insert ulang, arsipkan data terjemahan lama ke `translation_history`, kemudian reset job ke `pending`.

7. **Atomik ambil job** — saat `GET /worker/jobs/next` dipanggil, gunakan satu query `UPDATE ... RETURNING` atau `SELECT + UPDATE` dalam satu transaksi untuk mencegah race condition.

8. **Log setiap transisi status** — setiap perubahan status job wajib menghasilkan satu baris log dengan format `[JOB] {id_short} | {novel} ch.{n} | {lama} → {baru} ({durasi})`.

9. **Worker idle tidak spam log** — log hanya saat pertama kali idle dan saat kembali aktif.

10. **Error response konsisten** — semua error menggunakan format `{ "error": "<code>", "message": "..." }`.

11. **Port** — default `18111`, bisa di-override via env var `ADAPTER_PORT`. Nilai aktual yang dipakai tampil di banner startup.

12. **`/_internal/*` endpoint** — lindungi dengan middleware yang hanya mengizinkan `localhost` atau header `X-Internal-Key`.

13. **`/worker/*` endpoint** — lindungi dengan header `X-Worker-Key`.

14. **Timestamps** — semua `created_at` dan `updated_at` dalam format ISO 8601 UTC (`2026-08-15T08:00:00.000000+00:00`).