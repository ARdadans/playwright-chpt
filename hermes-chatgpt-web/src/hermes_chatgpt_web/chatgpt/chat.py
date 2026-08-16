import time
from typing import Any

from ..core.config import is_no_login
from .config import NEW_CHAT_BUTTON_SELECTOR, TEXTAREA_SELECTOR
from .status import check_generation_status, dismiss_modals


def new_chat(page: Any):
    """Trigger the 'New Chat' action in ChatGPT Web."""
    try:
        btn = page.locator(NEW_CHAT_BUTTON_SELECTOR).first
        if btn.count():
            btn.click(timeout=6000)
            time.sleep(0.8)
    except Exception:
        pass


def ask_stream(page: Any, prompt: str, model: str | None = None, reset: bool = False, state_dict: dict[str, Any] | None = None):
    """
    Stream tokens from ChatGPT Web page response.
    Updates state_dict with busy, last_activity, turns if provided.
    """
    model = model or "auto"
    if state_dict is not None and "lock" in state_dict:
        with state_dict["lock"]:
            state_dict["busy"] = True
            state_dict["busy_since"] = time.time()
            state_dict["last_activity"] = time.time()
            try:
                yield from _ask_locked(page, prompt, model, reset, state_dict)
            finally:
                state_dict["busy"] = False
                state_dict["busy_since"] = None
    else:
        yield from _ask_locked(page, prompt, model, reset, state_dict)


def _ask_locked(page: Any, prompt: str, model: str, reset: bool, state_dict: dict[str, Any] | None = None):
    if is_no_login():
        try:
            page.reload(wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
        except Exception:
            pass
    elif reset:
        new_chat(page)

    try:
        turns0 = page.evaluate("document.querySelectorAll('[data-message-author-role=\"assistant\"]').length")
    except Exception:
        turns0 = 0

    try:
        if reset:
            page.evaluate("""() => {
                for (const b of [...document.querySelectorAll('[role="dialog"] button, [data-testid="close-button"], button[aria-label*="close" i]')]) {
                    if (b.offsetParent !== null) { try { b.click(); } catch (e) {} }
                }
                const el = document.querySelector('#prompt-textarea');
                if (el) { el.scrollIntoView({block: 'center'}); el.focus(); }
            }""")
            time.sleep(0.4)

        # Auto-dismiss any active modals before prompt insertion (skip safely on error/absence)
        try:
            dismiss_modals(page)
        except Exception:
            pass

        try:
            page.wait_for_selector(TEXTAREA_SELECTOR, state="visible", timeout=10000)
        except Exception:
            pass

        ta = page.locator(TEXTAREA_SELECTOR).first
        try:
            ta.click(timeout=8000)
        except Exception:
            page.evaluate("() => { const el = document.querySelector('#prompt-textarea'); if (el) el.click(); }")
    except Exception as e:
        yield {"error": f"composer not found: {str(e)[:200]}"}
        return

    try:
        inserted = page.evaluate(
            """(txt) => {
            const el = document.querySelector('#prompt-textarea');
            if (!el) return false;
            el.focus();
            const ok = document.execCommand('insertText', false, txt);
            return ok === true || el.innerText.trim().length > 0;
        }""",
            prompt,
        )
        if not inserted:
            raise RuntimeError("insertText returned falsy")
    except Exception:
        try:
            # Use clipboard-based paste to preserve non-Latin characters
            page.evaluate(
                """(txt) => {
                const el = document.querySelector('#prompt-textarea');
                if (!el) throw new Error('no textarea');
                el.focus();
                const dt = new DataTransfer();
                dt.setData('text/plain', txt);
                const pe = new ClipboardEvent('paste', {
                    clipboardData: dt, bubbles: true, cancelable: true
                });
                el.dispatchEvent(pe);
            }""",
                prompt,
            )
        except Exception:
            yield {"error": "prompt insert failed"}
            return

    time.sleep(0.3)
    try:
        page.keyboard.press("Enter")
    except Exception:
        yield {"error": "send failed"}
        return

    t0 = time.time()
    last = ""
    idle = 0
    s = {}
    while time.time() - t0 < 480:
        time.sleep(0.25)
        s = check_generation_status(page)
        if state_dict is not None:
            state_dict["last_activity"] = time.time()
            if s.get("turns", 0) > turns0:
                state_dict["turns"] = s["turns"]

        cur = s.get("cur", "")
        if cur != last:
            delta = cur[len(last) :] if cur.startswith(last) and last else cur
            last = cur
            idle = 0
            if delta:
                yield {"delta": delta, "text": cur}
            continue

        idle += 1
        new_turn = s.get("turns", 0) > turns0
        if new_turn and not s.get("gen") and s.get("sbtn_ok") and idle >= 4:
            break
        if new_turn and not s.get("gen") and idle >= 12:
            break
        if idle > 60:
            break

    if state_dict is not None:
        state_dict["turns"] = s.get("turns", state_dict.get("turns", 0))
    yield {"done": True, "text": last}


def ask(page: Any, prompt: str, model: str | None = None, reset: bool = False, state_dict: dict[str, Any] | None = None) -> dict[str, Any]:
    """Synchronous chat execution."""
    out = ""
    error = None
    for ev in ask_stream(page, prompt, model, reset=reset, state_dict=state_dict):
        if "error" in ev:
            error = ev["error"]
        elif ev.get("done"):
            out = ev.get("text", out)
    if error:
        return {"error": error}
    return {"text": out}
