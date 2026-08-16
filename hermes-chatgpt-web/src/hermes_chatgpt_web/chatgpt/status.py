import json
import os
import time
from typing import Any

from .config import (
    MODAL_DISMISS_SELECTORS,
    STATE_FILE,
    TEXTAREA_SELECTOR,
)


async def detect_textarea(page: Any) -> dict | None:
    """Check if #prompt-textarea is visible and return bounding box rect {x, y, w, h}."""
    if not page:
        return None
    try:
        ta = page.locator(TEXTAREA_SELECTOR).first
        if ta and await ta.is_visible():
            box = await ta.bounding_box()
            if box:
                return {
                    "x": int(box["x"]),
                    "y": int(box["y"]),
                    "w": int(box["width"]),
                    "h": int(box["height"]),
                }
    except Exception:
        pass
    return None


async def get_debug_info(page: Any) -> dict:
    """DOM debug info matching PRD 5.10 /_internal/debug."""
    if not page:
        return {"error": "no active page"}
    try:
        js = """(() => {
            const ta = document.querySelector('#prompt-textarea');
            const r = ta ? ta.getBoundingClientRect() : null;
            const cx = r ? Math.round(r.left + r.width/2) : Math.round(innerWidth/2);
            const cy = r ? Math.round(r.top + r.height/2) : Math.round(innerHeight/2);
            const at = document.elementFromPoint(cx, cy);
            const dialogs = [...document.querySelectorAll('[role="dialog"], [data-testid*="modal" i], [data-testid*="Modal"]')]
                .filter(d => d.offsetParent !== null).map(d => (d.outerHTML || '').slice(0, 150));
            const toasts = [...document.querySelectorAll('[role="alert"]')].map(t => (t.innerText || '').slice(0, 120));
            return JSON.stringify({
                title: document.title,
                taPresent: !!ta,
                taRect: r ? {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), vw: innerWidth, vh: innerHeight} : null,
                atCenter: at ? at.tagName + '|' + (at.className || '').toString().slice(0, 100) : null,
                dialogs: dialogs.slice(0, 5), toasts: toasts.slice(0, 5),
                body: (document.body.innerText || '').slice(0, 250)
            });
        })()"""
        info_str = await page.evaluate(js)
        return json.loads(info_str or "{}")
    except Exception as e:
        return {"debug_error": str(e)}


async def check_generation_status(page: Any) -> dict:
    """
    Check if ChatGPT is actively generating / paused / idle.
    Returns {cur, turns, gen (bool), sbtn_ok (bool)}.
    """
    if not page:
        return {"cur": "", "turns": 0, "gen": False, "sbtn_ok": False}
    try:
        snap = await page.evaluate(
            """JSON.stringify((() => {
                const els = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
                const spb = document.querySelector('[data-testid="stop-button"]');
                const sbtn = document.querySelector('[data-testid="send-button"], button[type="submit"]');
                return {
                    cur: els.length ? els[els.length - 1].innerText : '',
                    turns: els.length,
                    gen: !!(spb && spb.offsetParent !== null),
                    sbtn_ok: !!(sbtn && !sbtn.disabled)
                };
            })())"""
        )
        return json.loads(snap or "{}")
    except Exception:
        return {"cur": "", "turns": 0, "gen": False, "sbtn_ok": False}


async def dismiss_modals(page: Any) -> bool:
    """Dismiss any modal dialogs, popups, or 'Stay logged out' prompts."""
    if not page:
        return False
    dismissed = False
    try:
        for sel in MODAL_DISMISS_SELECTORS:
            try:
                btn = page.locator(sel).first
                if (await btn.count()) and (await btn.is_visible()):
                    await btn.click(timeout=2000)
                    dismissed = True
                    break
            except Exception:
                pass
    except Exception:
        pass
    return dismissed


async def save_chatgpt_state(context: Any, page: Any, state_file: str = STATE_FILE) -> dict:
    """Snapshot web token + cookies usable by the adapter."""
    if not page or not context:
        return {}
    try:
        ls_str = await page.evaluate(
            "JSON.stringify(Object.fromEntries(Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])))"
        )
        ls = json.loads(ls_str or "{}")
    except Exception:
        ls = {}

    try:
        cookies = await context.cookies("https://chatgpt.com")
    except Exception:
        cookies = []

    snapshot = {
        "saved_at": time.time(),
        "url": page.url if page else "",
        "localStorage": ls,
        "cookies": cookies,
    }
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    try:
        os.chmod(state_file, 0o600)
    except OSError:
        pass
    return snapshot
