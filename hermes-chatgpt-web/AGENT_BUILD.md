# Agent Contract & Architecture: Hermes Novel Translation System

**Audience:** AI coding agents working in this repository.  
**State:** Unified single-process FastAPI application with **True Concurrent Multi-Account Playwright Automation**.

Use this document to:
1. Understand the architectural contracts and non-negotiables.
2. Maintain and extend the true concurrent multi-worker translation pipeline.
3. Understand database schemas, worker pool dispatching, and REST API conventions.

---

## 1. System Overview

Hermes is a unified, high-performance novel translation service combining:
- **FastAPI Single-Process REST API** (Port `18111`).
- **True Concurrent Multi-Account Playwright Chromium Pool**: Independent `BrowserContext` per ChatGPT account executing queries in parallel.
- **Asynchronous SQLite WAL Job Queue**: Atomic job claiming, rate-limit cooldown staging, and concurrency slot scaling.
- **Continuity Memory**: Automated extraction and persistence of character entities and glossary terms across chapters.
- **Database Backup & Snapshot Engine**: Online non-blocking `.zip` archive creation and restoration.

```
Client (CLI / Web)
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

## 2. Non-Negotiables & Core Rules

1. **SPA Driving Only**: Never send raw POST requests to internal ChatGPT backend APIs with token headers. Always interact with the live SPA DOM via Playwright.
2. **True Concurrency via Multi-Context**:
   - Each account cookie runs in its own isolated `BrowserContext` and `Page`.
   - Each worker has its own dedicated `asyncio.Lock()`.
   - Operations on worker A never block worker B. All network I/O and DOM polling use asynchronous generators (`async for`, `gw_chat_stream`).
3. **Single Process Architecture**: Everything runs inside `hermes_chatgpt_web.main` under FastAPI lifespan management. No external sub-processes or separate gateway servers.
4. **Database Thread-Safety & WAL Mode**: SQLite runs in WAL mode (`journal_mode=WAL`) with `busy_timeout=5000`. Database writes must handle transient locks gracefully with exponential backoff retries.
5. **Headful under Xvfb in Docker / Production**: Default is headful under display `:99` for anti-bot stealth. Headless mode can be toggled via `HERMES_HEADLESS=1`.

---

## 3. Directory Layout & Module Roles

```
hermes-chatgpt-web/
├── Dockerfile                  # Production container definition (Xvfb + uv + Chromium)
├── docker-compose.yml          # Container orchestration with 2GB shm_size
├── entrypoint.sh               # Container init (Xvfb :99, cookie sync)
├── pyproject.toml              # Dependencies & CLI entrypoints (start, dev)
├── cookies/                    # Directory for auto-imported account cookies (*.json)
├── dokumentasi/                # Comprehensive API and pipeline specifications
├── src/hermes_chatgpt_web/
│   ├── main.py                 # CLI launcher & Uvicorn runner
│   ├── api/
│   │   ├── app.py              # FastAPI app & lifespan management
│   │   ├── routes.py           # REST endpoints (Settings, Cookies, Chat, Models, Backup)
│   │   └── gateway_client.py   # In-process async gateway client adapter
│   ├── chatgpt/
│   │   ├── browser.py          # ChatGPTBrowser specialization
│   │   ├── chat.py             # DOM interaction, prompt injection, and stream polling
│   │   ├── config.py           # Selectors, models, and constants
│   │   ├── cookies.py          # Cookie parsing and session injection
│   │   ├── status.py           # DOM inspect, modal dismisser, debug info
│   │   └── worker_pool.py      # Multi-account pool, context manager, round-robin dispatch
│   ├── core/
│   │   ├── browser.py          # Generic Async Playwright manager & stealth init
│   │   ├── config.py           # Runtime settings cache & environment resolution
│   │   ├── executor.py         # Async executor utilities
│   │   └── logger.py           # Structured event logging & startup banners
│   └── translation/
│       ├── database.py         # aiosqlite schemas, CRUD, and atomic claim transactions
│       ├── image_processor.py  # Image placeholder extraction and Markdown restoration
│       ├── prompt.py           # Translation prompt assembly and delimiter parser
│       ├── routes.py           # /translate, /novels, /characters, /glossary endpoints
│       ├── signals.py          # asyncio.Event (job_notify) reactive trigger
│       ├── smart_cleaner.py    # Fallback JSON / delimiter repair state machine
│       └── worker.py           # Background queue loop with parallel job execution
```

---

## 4. True Concurrency Worker Mechanism

### Dynamic Slot Allocation & Dispatch
The translation background worker (`translation/worker.py`) does not process jobs sequentially. Instead:
1. It queries `worker_pool.get_status()` to determine `idle_workers`.
2. It calculates `available_slots = idle_workers - len(active_tasks)`.
3. If `available_slots > 0`, it atomically claims the next pending job from SQLite (`claim_next_pending_job()`).
4. It dispatches the job immediately in an unblocked background task via `asyncio.create_task(process_job(job))`.
5. When the job completes (or fails/requeues), the task callback frees the slot.

### Per-Context Cooldown & Rate Limiting
- **Post-Job Cooldown**: Upon completing a translation, an account worker enters a cooldown period (default `60s`, configurable via `/settings`). Other ready workers continue processing jobs concurrently.
- **Rate Limit Cooldown**: When ChatGPT shows rate limit / usage limit indicators:
  - The job is safely returned to `pending` status (`requeue_job_to_pending`).
  - The specific account is staged into `COOLDOWN` in the database and pool (stage 1: 2 hours, stage 2: 4 hours).
  - Other non-limited accounts continue serving requests.
  - Expired cooldowns are checked and restored automatically every 15 seconds.

---

## 5. Runtime Configuration & Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ADAPTER_PORT` | `18111` | FastAPI HTTP listening port |
| `CHATGPT_HOME` | `<project>/.data/prod` | Base runtime directory for SQLite DB and screenshots |
| `CHATGPT_TZ` | `Asia/Kolkata` | Browser emulation timezone |
| `HERMES_HEADLESS` | `0` | `0` = headful under Xvfb/display, `1` = headless Chromium |
| `HERMES_SKIP_BROWSER` | `0` | `1` = mock browser pool for unit tests |
| `WORKER_POLL_INTERVAL` | `2.0` | Polling interval for background worker in seconds |
| `JOB_COOLDOWN_SECONDS` | `60` | Post-job worker cooldown duration in seconds |
| `TRANSLATION_JOB_TIMEOUT`| `480` | Maximum translation execution timeout (seconds) |
| `INTERNAL_KEY` | `""` | Optional secret key for internal/admin endpoints |
| `WORKER_KEY` | `""` | Optional secret key for worker endpoints |

---

## 6. Docker & Containerization Best Practices

- **Optimized Dockerfile**: Includes CJK and emoji fonts, Xvfb with GLX extensions, multi-stage uv layer caching, and built-in healthchecks.
- **Shared Memory (`--shm-size=2g`)**: Essential when running multiple Playwright Chromium browser contexts concurrently.
- **Volumes**:
  - `/app/.data`: Persists `translation.db` and runtime state across container restarts.
  - `/app/cookies`: Mount local directory containing account JSON files for automatic initialization.
- **Startup Command**:
  ```bash
  docker run -d --name hermes-chatgpt-web --restart unless-stopped -p 18111:18111 --shm-size=2g -v hermes_data:/app/.data -v $(pwd)/cookies:/app/cookies hermes-chatgpt-web
  ```
