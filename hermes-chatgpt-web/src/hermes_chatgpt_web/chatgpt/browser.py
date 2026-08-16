from ..core.browser import PlaywrightBrowser
from .config import STATE_FILE
from .cookies import inject_chatgpt_cookies
from .status import (
    detect_textarea,
    dismiss_modals,
    get_debug_info,
    save_chatgpt_state,
)


class ChatGPTBrowser(PlaywrightBrowser):
    """
    ChatGPT-specialized browser automation class.
    Extends generic async PlaywrightBrowser with ChatGPT session injection,
    DOM state inspection, and state harvesting.
    """

    async def inject_cookies(self, cookie_pairs: list) -> int:
        """Inject ChatGPT / OpenAI session cookies into the browser context."""
        return await inject_chatgpt_cookies(self.context, cookie_pairs)

    async def detect_textarea(self) -> dict | None:
        """Check if #prompt-textarea is visible and return bounding box rect."""
        return await detect_textarea(self.page)

    async def get_debug_info(self) -> dict:
        """DOM debug info matching PRD 5.10 /_internal/debug."""
        return await get_debug_info(self.page)

    async def dismiss_modals(self) -> bool:
        """Dismiss welcome / cookie / stay-logged-out popups."""
        return await dismiss_modals(self.page)

    async def save_state(self, state_file: str = STATE_FILE) -> dict:
        """Snapshot web token + cookies usable by the adapter."""
        return await save_chatgpt_state(self.context, self.page, state_file=state_file)
