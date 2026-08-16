import json
import os
from typing import Any

from playwright.async_api import async_playwright

from .config import BASE_DIR, TIMEZONE

LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-gpu",
    "--use-angle=swiftshader",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--window-size=1280,900",
]

STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = {runtime: {}};
"""


class PlaywrightBrowser:
    """
    Generic Async Playwright browser automation manager.
    Handles Chromium lifecycle, stealth initialization, multi-context management,
    page evaluation, screenshots, and cookie management.
    """

    def __init__(self, display: str = ":99", stealth: bool = True, timezone_id: str | None = None):
        self.display = display
        self.stealth = stealth
        self.timezone_id = timezone_id or TIMEZONE
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.contexts: dict[str, dict[str, Any]] = {}

    async def start(self, headless: bool = False, user_data_dir: str | None = None, launch_args: list[str] | None = None):
        if "DISPLAY" not in os.environ:
            os.environ["DISPLAY"] = self.display
        args = launch_args or LAUNCH_ARGS

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=args,
        )
        return self

    async def create_account_context(self, account_id: str, cookies: list | dict | str | None = None) -> tuple[Any, Any]:
        """Create an isolated BrowserContext + Page for a specific account."""
        if not self.browser:
            raise RuntimeError("Browser not started")

        if account_id in self.contexts:
            await self.close_account_context(account_id)

        ctx = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id=self.timezone_id,
        )
        page = await ctx.new_page()
        if self.stealth:
            await page.add_init_script(STEALTH)
            try:
                from playwright_stealth import Stealth

                s = Stealth()
                if hasattr(s, "apply_stealth_async"):
                    await s.apply_stealth_async(page)
                elif hasattr(s, "apply_stealth_sync"):
                    s.apply_stealth_sync(page)
            except Exception:
                pass

        if cookies:
            cookie_list = []
            if isinstance(cookies, str):
                try:
                    loaded = json.loads(cookies)
                    if isinstance(loaded, list):
                        cookie_list = loaded
                    elif isinstance(loaded, dict) and "cookies" in loaded:
                        raw = loaded["cookies"]
                        if isinstance(raw, str):
                            from ..chatgpt.cookies import parse_cookie_line

                            cookie_list = parse_cookie_line(raw)
                        elif isinstance(raw, list):
                            cookie_list = raw
                    else:
                        from ..chatgpt.cookies import parse_cookie_line

                        cookie_list = parse_cookie_line(cookies)
                except Exception:
                    from ..chatgpt.cookies import parse_cookie_line

                    cookie_list = parse_cookie_line(cookies)
            elif isinstance(cookies, list):
                cookie_list = cookies

            if cookie_list:
                from ..chatgpt.cookies import inject_chatgpt_cookies

                await inject_chatgpt_cookies(ctx, cookie_list)

        self.contexts[account_id] = {"context": ctx, "page": page}

        if not self.context:
            self.context = ctx
            self.page = page

        return ctx, page

    async def close_account_context(self, account_id: str):
        """Close context and clean up memory for specific account."""
        entry = self.contexts.pop(account_id, None)
        if entry:
            ctx = entry.get("context")
            if ctx:
                try:
                    await ctx.close()
                except Exception:
                    pass
            if self.context == ctx:
                if self.contexts:
                    first_k = next(iter(self.contexts))
                    self.context = self.contexts[first_k]["context"]
                    self.page = self.contexts[first_k]["page"]
                else:
                    self.context = None
                    self.page = None

    async def shot(self, path: str | None = None) -> str | None:
        if not self.page:
            return None
        path = path or os.path.join(BASE_DIR, "latest.png")
        await self.page.screenshot(path=path)
        return path

    def current(self) -> str:
        return self.page.url if self.page else ""

    async def eval(self, js: str) -> Any:
        return await self.page.evaluate(js) if self.page else None

    async def localStorage(self) -> str | None:
        return await self.eval(
            "JSON.stringify(Object.fromEntries(Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])))"
        )

    async def add_cookies(self, cookies: list[dict]) -> None:
        if self.context and cookies:
            await self.context.add_cookies(cookies)

    async def clear_cookies(self) -> None:
        if self.context:
            await self.context.clear_cookies()

    async def stop(self):
        for _acc_id, entry in list(self.contexts.items()):
            try:
                if entry.get("context"):
                    await entry["context"].close()
            except Exception:
                pass
        self.contexts.clear()
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None


# Backward-compatible attribute access
def __getattr__(name: str) -> Any:
    if name == "ChatGPTBrowser":
        from ..chatgpt.browser import ChatGPTBrowser

        return ChatGPTBrowser
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
