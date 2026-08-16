import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..chatgpt.config import MODELS as _MODELS
from ..chatgpt.worker_pool import worker_pool
from ..core.config import INTERNAL_KEY, is_no_login
from ..core.logger import log_session
from ..gateway.state import shared_state
from ..translation.database import (
    delete_account_cookie,
    get_account_cookie_by_id,
    get_all_account_cookies,
    get_all_settings,
    reset_account_cooldown,
    reset_settings,
    set_account_status,
    update_settings,
    upsert_account_cookie,
)
from .gateway_client import gw_chat_stream, gw_chat_sync, gw_status

router = APIRouter()
_prev_prompt = None


def _verify_internal_access(request: Request, x_internal_key: str | None = None):
    """Allow access if localhost or X-Internal-Key matches."""
    client_host = request.client.host if request.client else ""
    is_localhost = client_host in ("127.0.0.1", "localhost", "::1", "testclient")
    if is_localhost:
        return
    if INTERNAL_KEY and x_internal_key == INTERNAL_KEY:
        return
    if not INTERNAL_KEY:
        return
    raise HTTPException(status_code=403, detail="Forbidden: internal access only")


def _chunk(cid: str, created: int, model: str, delta: str, done: bool) -> str:
    payload = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": delta} if delta else {},
                "finish_reason": "stop" if done else None,
            }
        ],
    }
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


# ─── 5.1 Health & Info ────────────────────────────────────────────────────────


@router.get("/health")
async def health():
    """Status adapter and browser Playwright."""
    return {"ok": True, "gateway": gw_status()}


@router.get("/v1/models")
async def models():
    """List available models in OpenAI-compatible format."""
    return {"object": "list", "data": _MODELS}


# ─── 5.3 Cookie & Account Management ─────────────────────────────────────────


@router.get("/cookies")
@router.get("/accounts")
async def list_cookies():
    """List all stored ChatGPT account cookies and pool worker status."""
    accounts = await get_all_account_cookies()
    pool_stat = worker_pool.get_status()
    return {
        "ok": True,
        "total_accounts": len(accounts),
        "accounts": accounts,
        "pool": pool_stat,
    }


@router.post("/cookies")
@router.post("/accounts")
async def add_cookie(request: Request):
    """
    Add or update a ChatGPT account cookie.
    Accepts JSON body: {name: str, provider: str, cookies: str | list | dict}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}},
            status_code=400,
        )

    name = str(body.get("name") or "default_user").strip()
    provider = str(body.get("provider") or "chatgpt").strip()
    raw_cookies = body.get("cookies")
    if not raw_cookies:
        return JSONResponse(
            {"error": {"message": "Missing 'cookies' field", "type": "invalid_request_error"}},
            status_code=400,
        )

    acc = await upsert_account_cookie(name=name, provider=provider, cookies_data=raw_cookies)
    if acc.get("status") in ("ACTIVE", "BUSY"):
        await worker_pool.add_worker(acc)

    return {"ok": True, "message": "Cookie account saved successfully", "account": acc}


@router.delete("/cookies/{account_id}")
@router.delete("/accounts/{account_id}")
async def delete_cookie(account_id: str):
    """Delete an account cookie and close its browser worker context."""
    acc = await get_account_cookie_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail=f"Account with ID '{account_id}' not found")

    await worker_pool.remove_worker(account_id)
    await delete_account_cookie(account_id)
    return {"ok": True, "message": f"Account '{acc['name']}' deleted successfully"}


@router.post("/cookies/{account_id}/pause")
@router.post("/accounts/{account_id}/pause")
async def pause_cookie(account_id: str):
    """Pause an account (stops accepting new jobs)."""
    acc = await set_account_status(account_id, "PAUSED")
    if not acc:
        raise HTTPException(status_code=404, detail=f"Account with ID '{account_id}' not found")

    await worker_pool.remove_worker(account_id)
    return {"ok": True, "message": f"Account '{acc['name']}' paused", "account": acc}


@router.post("/cookies/{account_id}/resume")
@router.post("/accounts/{account_id}/resume")
async def resume_cookie(account_id: str):
    """Resume a paused account back to ACTIVE and re-register in worker pool."""
    acc = await set_account_status(account_id, "ACTIVE")
    if not acc:
        raise HTTPException(status_code=404, detail=f"Account with ID '{account_id}' not found")

    await worker_pool.add_worker(acc)
    return {"ok": True, "message": f"Account '{acc['name']}' resumed", "account": acc}


@router.post("/cookies/{account_id}/reset-cooldown")
@router.post("/accounts/{account_id}/reset-cooldown")
async def reset_cookie_cooldown(account_id: str):
    """Instantly reset rate limit cooldown to ACTIVE and clear worker context cooldown."""
    acc = await reset_account_cooldown(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail=f"Account with ID '{account_id}' not found")

    worker_pool.reset_worker_cooldown(account_id)
    await worker_pool.add_worker(acc)
    return {"ok": True, "message": f"Cooldown reset for '{acc['name']}'", "account": acc}


@router.post("/cookies/{account_id}/refresh")
@router.post("/accounts/{account_id}/refresh")
async def refresh_account_context(account_id: str):
    """Manually clear browser cache/memory and recreate clean BrowserContext for an account."""
    acc = await get_account_cookie_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail=f"Account with ID '{account_id}' not found")

    refreshed = await worker_pool.refresh_worker(account_id)
    if not refreshed:
        refreshed = await worker_pool.add_worker(acc)

    return {"ok": refreshed, "message": f"Browser context refreshed for account '{acc['name']}'"}


# ─── 5.4 Settings Management ──────────────────────────────────────────────────


@router.get("/settings")
async def get_settings():
    """Retrieve all current system settings."""
    settings = await get_all_settings()
    return {
        "ok": True,
        "settings": settings,
    }


@router.patch("/settings")
@router.put("/settings")
@router.post("/settings")
async def patch_settings(request: Request):
    """
    Update one or more system settings dynamically.
    Accepts JSON body: {"job_cooldown_seconds": 60, "worker_poll_interval": 2.0, ...}
    Changes take effect immediately on subsequent jobs.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}},
            status_code=400,
        )

    if not isinstance(body, dict) or not body:
        return JSONResponse(
            {"error": {"message": "Body must be a non-empty JSON object", "type": "invalid_request_error"}},
            status_code=400,
        )

    valid_keys = {
        "job_cooldown_seconds": (int, float),
        "worker_poll_interval": (int, float),
        "worker_concurrency": (int,),
        "translation_job_timeout": (int,),
        "translation_max_text_length": (int,),
    }
    cleaned_updates: dict[str, Any] = {}
    for k, v in body.items():
        if k in valid_keys:
            try:
                if valid_keys[k] == (int,):
                    cleaned_updates[k] = int(v)
                else:
                    cleaned_updates[k] = float(v) if isinstance(v, float) or "." in str(v) else int(v)
            except (ValueError, TypeError):
                return JSONResponse(
                    {"error": {"message": f"Invalid value for setting '{k}': must be numeric", "type": "invalid_request_error"}},
                    status_code=400,
                )
        else:
            cleaned_updates[k] = v

    updated = await update_settings(cleaned_updates)
    return {
        "ok": True,
        "message": "Settings updated successfully",
        "settings": updated,
    }


@router.post("/settings/reset")
async def reset_system_settings():
    """Reset all system settings back to default values."""
    defaults = await reset_settings()
    return {
        "ok": True,
        "message": "Settings reset to factory defaults",
        "settings": defaults,
    }


@router.post("/cookies/inject-session")
async def inject_session(request: Request):
    """
    Inject cookie/session into live browser without restarting server (legacy compatible).
    Accepts {token: ..., cookies: ...} or {name: ..., provider: ..., cookies: ...}.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}},
            status_code=400,
        )

    log_session("Injecting new session cookies...")

    name = str(body.get("name") or "user1").strip()
    provider = str(body.get("provider") or "chatgpt").strip()
    raw_cookies = body.get("cookies")
    if not raw_cookies:
        return JSONResponse(
            {"error": {"message": "Missing 'cookies' field", "type": "invalid_request_error"}},
            status_code=400,
        )

    acc = await upsert_account_cookie(name=name, provider=provider, cookies_data=raw_cookies)
    await worker_pool.add_worker(acc)

    return {"ok": True, "message": "Session injected successfully", "account": acc}


# ─── 5.2 Chat (OpenAI-Compatible) ─────────────────────────────────────────────


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions endpoint."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}},
            status_code=400,
        )

    messages = body.get("messages") or []
    if not messages:
        return JSONResponse(
            {"error": {"message": "messages required", "type": "invalid_request_error"}},
            status_code=400,
        )

    stream = bool(body.get("stream", False))
    model = body.get("model") or "auto"

    user_parts = []
    for m in messages:
        if m.get("role") == "system":
            user_parts.append(f"[system] {m.get('content', '')}")
        else:
            user_parts.append(f"[{m.get('role', 'user')}] {m.get('content', '')}")
    prompt = "\n".join(user_parts)

    global _prev_prompt
    if (
        not is_no_login()
        and _prev_prompt is not None
        and prompt.startswith(_prev_prompt)
        and len(prompt) > len(_prev_prompt)
    ):
        gw_body = {"prompt": prompt[len(_prev_prompt) :], "model": model, "reset": False}
    else:
        gw_body = {"prompt": prompt, "model": model, "reset": True}
    _prev_prompt = prompt

    if not shared_state.get("ok"):
        return JSONResponse(
            {"error": "BROWSER_NOT_READY", "message": shared_state.get("error") or "Playwright not ready"},
            status_code=503,
        )

    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if stream:
        async def sse_gen():
            try:
                async for ev in gw_chat_stream(gw_body):
                    if ev.get("error"):
                        yield f"data: {json.dumps({'error': {'message': ev['error'], 'type': 'backend_error'}}, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    delta = ev.get("delta", "")
                    done = ev.get("done", False)
                    yield _chunk(cid, created, model, delta, done)
            except Exception as e:
                yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'backend_error'}}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse_gen(), media_type="text/event-stream")

    # Non-streaming
    res = await gw_chat_sync(gw_body)
    if "error" in res:
        return JSONResponse(
            {"error": {"message": res["error"], "type": "backend_error"}},
            status_code=500,
        )

    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": res.get("text", "")},
                "finish_reason": "stop",
            }
        ],
        "usage": None,
    }


# ─── 5.10 Internal Gateway (Playwright Debug & Monitoring) ────────────────────


@router.get("/_internal/status")
async def internal_status(request: Request, x_internal_key: str | None = Header(None)):
    """Status browser Playwright."""
    _verify_internal_access(request, x_internal_key)
    return gw_status()


@router.get("/_internal/debug")
async def internal_debug(request: Request, x_internal_key: str | None = Header(None)):
    """Live DOM evaluation of ChatGPT Web."""
    _verify_internal_access(request, x_internal_key)
    browser = shared_state.get("browser")
    if not browser:
        return {"ok": False, "error": "browser not running"}
    info = await browser.get_debug_info()
    return {"ok": shared_state.get("ok", False), "info": info}


@router.post("/_internal/chat")
async def internal_chat(
    request: Request,
    body: dict[str, Any],
    x_internal_key: str | None = Header(None),
):
    """Direct synchronous chat call to browser."""
    _verify_internal_access(request, x_internal_key)
    if not shared_state.get("ok"):
        return JSONResponse(
            {"error": "BROWSER_NOT_READY", "message": shared_state.get("error") or "gateway not booted"},
            status_code=503,
        )
    res = await gw_chat_sync(body)
    if "error" in res:
        return JSONResponse({"error": res["error"]}, status_code=500)
    return res


@router.post("/_internal/chat/stream")
async def internal_chat_stream(
    request: Request,
    body: dict[str, Any],
    x_internal_key: str | None = Header(None),
):
    """Direct SSE streaming chat call to browser."""
    _verify_internal_access(request, x_internal_key)
    if not shared_state.get("ok"):
        return JSONResponse(
            {"error": "BROWSER_NOT_READY", "message": shared_state.get("error") or "gateway not booted"},
            status_code=503,
        )

    async def event_gen():
        try:
            async for ev in gw_chat_stream(body):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")
