"""
Worker pool manager for multi-account / multi-cookie ChatGPT Web automation.

Manages isolated Playwright BrowserContexts per account, round-robin / dynamic
dispatching to idle workers, rate-limit error detection, and cooldown staging.
"""

import asyncio
import os
import threading
import time
from collections.abc import Iterator
from typing import Any

from ..core.config import get_context_refresh_jobs, get_job_cooldown_seconds
from ..core.logger import log_browser, log_worker
from ..translation.database import (
    set_account_cooldown,
)
from .chat import ask_stream as _cg_ask_stream
from .config import CHATGPT_URL, TEXTAREA_SELECTOR
from .status import detect_textarea, dismiss_modals

RATE_LIMIT_KEYWORDS = [
    "you've reached your usage limit",
    "you have reached your limit",
    "too many requests in 1 hour",
    "rate limit reached",
    "please try again later",
    "you've hit the plus limit",
    "you've hit the free limit",
    "you've reached the free plan limit",
    "you've reached the plus plan limit",
    "unusual activity from your system",
]


class AccountWorker:
    """Represents a single active worker bound to a specific cookie account context."""

    def __init__(
        self,
        account_id: str,
        name: str,
        provider: str,
        context: Any,
        page: Any,
        cookies: Any = None,
    ):
        self.account_id = account_id
        self.name = name
        self.provider = provider
        self.context = context
        self.page = page
        self.cookies = cookies
        self.busy = False
        self.busy_since: float | None = None
        self.cooldown_until: float = 0.0
        self.last_activity: float = time.time()
        self.turns: int = 0
        self.completed_jobs: int = 0
        self.title: str = "ChatGPT"
        self.ok: bool = True
        self.error: str | None = None
        self.lock = threading.Lock()

    def is_cooling_down(self) -> bool:
        """Check if worker is currently in post-job cooldown period."""
        return time.time() < self.cooldown_until

    def cooldown_remaining(self) -> float:
        """Return remaining cooldown in seconds, or 0.0 if not in cooldown."""
        rem = self.cooldown_until - time.time()
        return max(0.0, round(rem, 1))

    def reset_cooldown(self):
        """Manually clear any active cooldown."""
        self.cooldown_until = 0.0

    def as_state_dict(self) -> dict[str, Any]:
        """Provides backward-compatible state dictionary for chat routines."""
        return {
            "ok": self.ok,
            "error": self.error,
            "title": self.title,
            "busy": self.busy,
            "busy_since": self.busy_since,
            "cooldown_until": self.cooldown_until,
            "cooling_down": self.is_cooling_down(),
            "last_activity": self.last_activity,
            "turns": self.turns,
            "completed_jobs": self.completed_jobs,
            "lock": self.lock,
            "page": self.page,
            "context": self.context,
            "account_id": self.account_id,
            "name": self.name,
        }



class WorkerPool:
    """
    Central pool managing multiple isolated account workers.
    """

    def __init__(self):
        self.browser: Any = None
        self.workers: dict[str, AccountWorker] = {}
        self._pool_lock = threading.Lock()
        self._initialized = False

    def init_pool(self, browser: Any, accounts: list[dict[str, Any]], headless: bool = False):
        """Initialize browser contexts for all provided active accounts."""
        with self._pool_lock:
            self.browser = browser
            skip_browser = os.environ.get("HERMES_SKIP_BROWSER") == "1"

            for acc in accounts:
                if acc.get("status") not in ("ACTIVE", "BUSY"):
                    continue
                acc_id = acc["id"]
                name = acc["name"]
                provider = acc.get("provider", "chatgpt")
                cookies = acc.get("cookies_data")

                if skip_browser:
                    # Mock worker for tests
                    worker = AccountWorker(acc_id, name, provider, context=None, page=None, cookies=cookies)
                    worker.title = "ChatGPT (Mock)"
                    self.workers[acc_id] = worker
                    log_browser(f"Worker [{name}] initialized (Mock)")
                    continue

                try:
                    ctx, page = self.browser.create_account_context(acc_id, cookies)
                    worker = AccountWorker(acc_id, name, provider, ctx, page, cookies=cookies)
                    self._prepare_page(worker)
                    self.workers[acc_id] = worker
                    log_browser(f"Worker [{name}] ready — title: \"{worker.title}\"")
                except Exception as e:
                    log_browser(f"Failed to initialize worker [{name}]: {e}", level="ERROR")

            self._initialized = True

    def _prepare_page(self, worker: AccountWorker):
        """Navigate and prepare ChatGPT page for a worker with reload fallback and validation."""
        if not worker.page:
            return
        page = worker.page
        log_browser(f"Navigating worker [{worker.name}] to ChatGPT...")
        try:
            page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=120000)
        except Exception as e:
            log_browser(f"Worker [{worker.name}] navigation warning: {e}", level="WARN")

        # Settle Cloudflare / challenges
        t0 = time.time()
        while time.time() - t0 < 45:
            time.sleep(1.5)
            try:
                cur = (page.title() or "").strip()
            except Exception:
                cur = ""
            if cur and "just a moment" not in cur.lower() and "security" not in cur.lower():
                break

        # Dismiss any popup/onboarding modal
        try:
            dismiss_modals(page)
        except Exception:
            pass

        # Check if textarea is ready
        ta_ready = False
        try:
            page.wait_for_selector(TEXTAREA_SELECTOR, state="visible", timeout=15000)
            ta_ready = True
        except Exception:
            log_browser(
                f"Worker [{worker.name}] textarea not immediately visible after initial load. Trying 1x page reload...",
                level="WARN",
            )

        # Fallback: 1x reload if not ready
        if not ta_ready:
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
                time.sleep(2)
                try:
                    dismiss_modals(page)
                except Exception:
                    pass
                page.wait_for_selector(TEXTAREA_SELECTOR, state="visible", timeout=20000)
                ta_ready = True
            except Exception as e:
                log_browser(
                    f"Worker [{worker.name}] textarea failed to become ready after 1x reload: {e}",
                    level="ERROR",
                )
                worker.ok = False
                worker.error = f"Textarea not ready: {e}"
                return

        worker.title = (page.title() or "ChatGPT").strip()
        detect_textarea(page)
        time.sleep(1.5)  # Buffer for client-side React hydration settling
        worker.ok = True
        worker.error = None
        worker.last_activity = time.time()

    def add_worker(self, account: dict[str, Any]) -> bool:
        """Dynamically add and boot a new account worker into the pool."""
        with self._pool_lock:
            acc_id = account["id"]
            name = account["name"]
            provider = account.get("provider", "chatgpt")
            cookies = account.get("cookies_data")

            # If worker already exists, remove first
            if acc_id in self.workers:
                self._remove_worker_locked(acc_id)

            skip_browser = os.environ.get("HERMES_SKIP_BROWSER") == "1"
            if skip_browser or not self.browser:
                worker = AccountWorker(acc_id, name, provider, None, None, cookies=cookies)
                worker.title = "ChatGPT (Mock)"
                self.workers[acc_id] = worker
                return True

            try:
                ctx, page = self.browser.create_account_context(acc_id, cookies)
                worker = AccountWorker(acc_id, name, provider, ctx, page, cookies=cookies)
                self._prepare_page(worker)
                self.workers[acc_id] = worker
                return True
            except Exception as e:
                log_browser(f"Failed to add worker [{name}]: {e}", level="ERROR")
                return False

    def remove_worker(self, account_id: str):
        """Remove and clean up a worker context."""
        with self._pool_lock:
            self._remove_worker_locked(account_id)

    def _remove_worker_locked(self, account_id: str):
        worker = self.workers.pop(account_id, None)
        if worker and self.browser:
            try:
                self.browser.close_account_context(account_id)
            except Exception:
                pass

    def refresh_worker_context(self, worker: AccountWorker) -> bool:
        """
        Close and recreate an isolated BrowserContext + Page for this worker,
        clearing memory/cache leaks and re-injecting fresh session cookies.
        """
        with self._pool_lock:
            return self._refresh_worker_locked(worker)

    def _refresh_worker_locked(self, worker: AccountWorker) -> bool:
        """Internal context refresh implementation while lock is held."""
        skip_browser = os.environ.get("HERMES_SKIP_BROWSER") == "1"
        threshold = get_context_refresh_jobs()
        if threshold > 0 and worker.completed_jobs >= threshold:
            log_browser(
                f"Worker [{worker.name}] reached completed jobs threshold ({worker.completed_jobs}/{threshold} done). "
                f"Refreshing browser context to clear cache & memory, and re-injecting session..."
            )
        else:
            log_browser(
                f"Worker [{worker.name}] context refresh triggered ({worker.completed_jobs} jobs executed). "
                f"Refreshing browser context to clear cache & memory, and re-injecting session..."
            )

        if not skip_browser and self.browser:
            try:
                self.browser.close_account_context(worker.account_id)
                ctx, page = self.browser.create_account_context(worker.account_id, worker.cookies)
                worker.context = ctx
                worker.page = page
                self._prepare_page(worker)
                worker.ok = True
                worker.error = None
                log_browser(f"Worker [{worker.name}] browser context refreshed and ready (memory cleared).")
            except Exception as e:
                log_browser(f"Failed to refresh browser context for worker [{worker.name}]: {e}", level="ERROR")
                worker.ok = False
                worker.error = str(e)
                return False
        else:
            log_browser(f"Worker [{worker.name}] context reset (Mock mode).")

        worker.completed_jobs = 0
        return True

    def refresh_worker(self, account_id: str) -> bool:
        """Manually trigger a context refresh for a specific worker by account_id."""
        with self._pool_lock:
            worker = self.workers.get(account_id)
            if not worker:
                return False
            return self._refresh_worker_locked(worker)

    def acquire_idle_worker(self, specific_account_id: str | None = None) -> AccountWorker | None:
        """
        Find and lock an idle worker (status ACTIVE, not busy, not in cooldown).
        Uses FIFO / Round-Robin among available idle workers.
        """
        with self._pool_lock:
            if specific_account_id:
                worker = self.workers.get(specific_account_id)
                if worker and not worker.busy and not worker.is_cooling_down() and worker.ok:
                    worker.busy = True
                    worker.busy_since = time.time()
                    return worker
                return None

            # Sort by last_activity (oldest idle first for fair round-robin)
            available = [w for w in self.workers.values() if not w.busy and not w.is_cooling_down() and w.ok]
            if not available:
                return None

            available.sort(key=lambda w: w.last_activity)
            selected = available[0]
            selected.busy = True
            selected.busy_since = time.time()
            return selected

    def release_worker(self, worker: AccountWorker, apply_cooldown: bool = True):
        """Release a locked worker back to idle state, check context refresh threshold, and apply post-job cooldown."""
        threshold = get_context_refresh_jobs()
        if threshold > 0 and worker.completed_jobs >= threshold:
            self._refresh_worker_locked(worker)

        worker.busy = False
        worker.busy_since = None
        worker.last_activity = time.time()
        if apply_cooldown:
            cd_sec = get_job_cooldown_seconds()
            if cd_sec > 0:
                worker.cooldown_until = time.time() + cd_sec
            else:
                worker.cooldown_until = 0.0
        else:
            worker.cooldown_until = 0.0

    def reset_worker_cooldown(self, account_id: str):
        """Manually clear cooldown for a specific worker."""
        with self._pool_lock:
            w = self.workers.get(account_id)
            if w:
                w.reset_cooldown()

    def get_status(self) -> dict[str, Any]:
        """Summary of active workers, cooldowns, and pool capacity."""
        with self._pool_lock:
            total = len(self.workers)
            busy_count = sum(1 for w in self.workers.values() if w.busy)
            cooling_down_count = sum(1 for w in self.workers.values() if not w.busy and w.is_cooling_down())
            idle_count = sum(1 for w in self.workers.values() if not w.busy and not w.is_cooling_down() and w.ok)
            refresh_threshold = get_context_refresh_jobs()
            workers_info = []
            for w in self.workers.values():
                is_cd = w.is_cooling_down()
                workers_info.append(
                    {
                        "account_id": w.account_id,
                        "name": w.name,
                        "provider": w.provider,
                        "busy": w.busy,
                        "cooling_down": is_cd,
                        "cooldown_remaining_s": w.cooldown_remaining() if is_cd else None,
                        "completed_jobs": w.completed_jobs,
                        "turns": w.turns,
                        "refresh_threshold_jobs": refresh_threshold,
                        "idle_s": round(time.time() - w.last_activity, 1) if w.last_activity else None,
                        "title": w.title,
                        "ok": w.ok,
                    }
                )

            return {
                "ok": total > 0,
                "total_workers": total,
                "idle_workers": idle_count,
                "busy_workers": busy_count,
                "cooling_down_workers": cooling_down_count,
                "context_refresh_jobs": refresh_threshold,
                "workers": workers_info,
            }


    def execute_stream(
        self,
        prompt: str,
        model: str = "auto",
        reset: bool = False,
        specific_account_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """
        Stream ChatGPT tokens using an acquired worker.
        Detects rate limit and triggers staged cooldown if encountered.
        """
        worker = self.acquire_idle_worker(specific_account_id)
        if not worker:
            yield {"error": "no_available_workers", "message": "All account workers are busy or cooling down"}
            return

        state_dict = worker.as_state_dict()
        rate_limit_hit = False
        full_text = ""

        try:
            # Check mock mode
            if not worker.page:
                # Fast mock response for unit tests / skip-browser
                full_text = f"[Mock Translation via {worker.name}]: {prompt[:50]}..."
                yield {"delta": full_text, "text": full_text}
                yield {"done": True, "text": full_text}
                return

            for ev in _cg_ask_stream(
                worker.page,
                prompt,
                model=model,
                reset=reset,
                state_dict=state_dict,
            ):
                if "error" in ev:
                    err_msg = str(ev["error"]).lower()
                    if any(k in err_msg for k in RATE_LIMIT_KEYWORDS):
                        rate_limit_hit = True
                    yield ev
                    break

                if ev.get("delta"):
                    full_text += ev["delta"]
                    lower_delta = ev["delta"].lower()
                    if any(k in lower_delta for k in RATE_LIMIT_KEYWORDS):
                        rate_limit_hit = True

                if ev.get("text"):
                    full_text = ev["text"]
                    lower_text = full_text.lower()
                    if any(k in lower_text for k in RATE_LIMIT_KEYWORDS):
                        rate_limit_hit = True

                yield ev

            if rate_limit_hit:
                yield {
                    "error": "rate_limit_exceeded",
                    "rate_limited": True,
                    "account_id": worker.account_id,
                    "account_name": worker.name,
                }
        finally:
            if rate_limit_hit:
                log_worker(
                    f"Rate limit detected on worker [{worker.name}] (id: {worker.account_id}). Triggering cooldown.",
                    level="WARN",
                )
                # Apply staged cooldown in background thread/sync
                try:
                    asyncio.run(set_account_cooldown(worker.account_id, "Rate limit exceeded"))
                except Exception:
                    pass
                self.remove_worker(worker.account_id)
            else:
                worker.turns += 1
                worker.completed_jobs += 1
                self.release_worker(worker)


# Global singleton worker pool
worker_pool = WorkerPool()
