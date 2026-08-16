import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ..chatgpt.worker_pool import worker_pool
from ..core.browser import PlaywrightBrowser
from ..core.config import ADAPTER_PORT
from ..core.logger import (
    log_browser,
    log_db,
    log_error,
    log_server,
    log_startup,
    print_banner,
)
from ..gateway.state import shared_state
from ..translation.database import get_all_account_cookies, init_db, upsert_account_cookie
from ..translation.routes import router as translation_router
from ..translation.worker import worker_loop
from .routes import router as api_router


async def _scan_and_import_cookies():
    """Scan cookies directory for valid JSON accounts and upsert to database."""
    candidates = [
        Path(__file__).parent.parent.parent.parent / "cookies",
        Path(__file__).parent.parent.parent / "cookies",
        Path.cwd() / "cookies",
    ]
    cookie_dir = None
    for c in candidates:
        if c.exists() and c.is_dir():
            cookie_dir = c
            break

    if not cookie_dir:
        return

    imported_count = 0
    for file_path in cookie_dir.glob("*.json"):
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict) and "name" in data and "provider" in data and "cookies" in data:
                name = str(data["name"]).strip()
                provider = str(data["provider"]).strip()
                cookies_val = data["cookies"]
                if name and provider and cookies_val:
                    await upsert_account_cookie(name=name, provider=provider, cookies_data=cookies_val)
                    imported_count += 1
                    log_browser(f"Imported cookie account [{name}] from {file_path.name}")
        except Exception as e:
            log_browser(f"Failed parsing cookie file {file_path.name}: {e}", level="WARN")

    if imported_count > 0:
        log_db(f"Synchronized {imported_count} cookie accounts from {cookie_dir}")


async def _boot_browser_pool(headless: bool, accounts: list[dict]):
    """Asynchronous initialization of Playwright browser and multi-account pool."""
    b = await PlaywrightBrowser().start(headless=headless)
    shared_state["browser"] = b
    log_browser(f"Chromium launched (headless={str(headless).lower()})")

    await worker_pool.init_pool(b, accounts, headless=headless)
    shared_state["ok"] = True
    shared_state["last_activity"] = time.time()
    shared_state["title"] = "ChatGPT Multi-Account Pool"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup & shutdown lifecycle for the unified Hermes application.
    Executes database init, cookie auto-import, browser worker pool, and background scheduler.
    """
    # ── Startup Sequence ──
    log_startup("Hermes Novel Translation System")

    # 1. Database init
    log_startup("Initializing database...")
    try:
        await init_db()
        log_db("WAL mode enabled")
        log_db("Foreign keys enabled")
        log_db("Schema applied (5 tables, 7 indexes)")
    except Exception as e:
        log_error(f"Database initialization failed: {e}")
        sys.exit(1)

    # 2. Scan and import cookies from cookies/ folder
    try:
        await _scan_and_import_cookies()
    except Exception as e:
        log_browser(f"Cookie scan warning: {e}", level="WARN")

    # 3. Load active accounts from DB
    accounts = await get_all_account_cookies()
    active_accounts = [a for a in accounts if a.get("status") in ("ACTIVE", "BUSY")]

    # 4. Playwright Browser launch & Worker Pool init
    skip_browser = os.environ.get("HERMES_SKIP_BROWSER") == "1"
    if not skip_browser:
        log_startup(f"Launching Playwright browser with {len(active_accounts)} active worker contexts...")
        try:
            headless = os.environ.get("HERMES_HEADLESS", "0").lower() in ("1", "true")
            await _boot_browser_pool(headless, active_accounts)
        except Exception as e:
            log_error(f"Browser launch failed: {e}")
            sys.exit(1)
    else:
        await worker_pool.init_pool(None, active_accounts, headless=False)
        shared_state["ok"] = True
        shared_state["title"] = "ChatGPT (Mock Pool)"
        log_browser(f"Mock browser pool ready with {len(active_accounts)} accounts (HERMES_SKIP_BROWSER=1)")

    # 5. Background worker task start
    worker_task = asyncio.create_task(worker_loop())
    log_startup("Background worker task started")

    # 6. Print readiness banner
    print_banner(ADAPTER_PORT)
    log_server(f"Listening on http://0.0.0.0:{ADAPTER_PORT}")

    yield

    # ── Shutdown ──
    log_server("Shutting down services...")
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    browser = shared_state.get("browser")
    if browser:
        await browser.stop()
    log_server("Shutdown completed")


app = FastAPI(
    title="Hermes Novel Translation System",
    description="Unified translation system using ChatGPT Web backend.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(api_router)
app.include_router(translation_router)
