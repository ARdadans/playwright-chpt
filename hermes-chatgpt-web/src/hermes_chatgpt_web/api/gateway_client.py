import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from ..chatgpt.worker_pool import worker_pool
from ..gateway.chat import ask_stream
from ..gateway.state import shared_state


def gw_status() -> dict[str, Any]:
    """Return status dictionary matching PRD 5.1 and 5.10 with pool info."""
    pool_stat = worker_pool.get_status()
    now = time.time()
    last_act = shared_state.get("last_activity", 0.0)
    base_stat = {
        "ok": bool(shared_state.get("ok")) or pool_stat["ok"],
        "title": shared_state.get("title", ""),
        "error": shared_state.get("error"),
        "turns": shared_state.get("turns", 0),
        "busy": bool(shared_state.get("busy")) or pool_stat["busy_workers"] > 0,
        "busy_since": shared_state.get("busy_since"),
        "last_activity": last_act,
        "idle_s": round(now - last_act, 1) if last_act else None,
        "pool": pool_stat,
    }
    return base_stat


async def gw_chat_stream(body: dict[str, Any], account_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
    """Direct async generator for chat stream tokens via WorkerPool."""
    prompt = body.get("prompt", "")
    model = body.get("model", "auto")
    reset = bool(body.get("reset", False))

    if worker_pool.workers:
        async for ev in worker_pool.execute_stream(
            prompt=prompt,
            model=model,
            reset=reset,
            specific_account_id=account_id,
        ):
            yield ev
        return

    # Fallback to shared_state single page
    page = shared_state.get("page")
    if not page or not shared_state.get("ok"):
        yield {"error": shared_state.get("error") or "gateway not booted"}
        return
    async for ev in ask_stream(page, prompt, model, reset=reset):
        yield ev


async def gw_chat_sync(body: dict[str, Any], account_id: str | None = None) -> dict[str, Any]:
    """Asynchronous chat completion via direct function call."""
    out = ""
    error = None
    rate_limited = False
    async for ev in gw_chat_stream(body, account_id=account_id):
        if "error" in ev:
            error = ev["error"]
            if ev.get("rate_limited"):
                rate_limited = True
            break
        if ev.get("done"):
            out = ev.get("text", out)
        elif ev.get("text"):
            out = ev["text"]

    if error:
        res = {"error": error}
        if rate_limited:
            res["rate_limited"] = True
        return res
    return {"text": out}


async def heal_gateway() -> bool:
    """Best effort heal if page went into bad state."""
    page = shared_state.get("page")
    if not page:
        return False
    try:
        await page.reload(wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        shared_state["title"] = (await page.title()) or ""
        shared_state["ok"] = True
        return True
    except Exception as e:
        shared_state["error"] = str(e)
        return False
