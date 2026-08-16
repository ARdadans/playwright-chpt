import os

from ..core.config import BASE_DIR

# ── URLs & Endpoints ──
CHATGPT_URL = "https://chatgpt.com/"
CHATGPT_AUTH_URL = "https://chatgpt.com/auth/login?screen_hint=password"

# ── Cookie candidate domains ──
COOKIE_DOMAINS = [".chatgpt.com", "chatgpt.com", ".openai.com", ".auth.openai.com"]

# ── Cookie security attributes ──
SECURE_COOKIES = {
    "__Secure-next-auth.session-token.0",
    "__Secure-next-auth.session-token.1",
    "__Secure-next-auth.session-token",
    "__Secure-oai-is",
    "__Secure-next-auth.callback-url",
    "__Host-next-auth.csrf-token",
    "cf_clearance",
    "__cf_bm",
    "_cfuvid",
}

HOST_ONLY_COOKIES = {"__Host-next-auth.csrf-token", "__Secure-next-auth.callback-url"}

# ── DOM Selectors ──
TEXTAREA_SELECTOR = "#prompt-textarea"
STOP_BUTTON_SELECTOR = '[data-testid="stop-button"]'
SEND_BUTTON_SELECTOR = '[data-testid="send-button"], button[type="submit"]'
NEW_CHAT_BUTTON_SELECTOR = '[data-testid="create-new-chat-button"]'
ASSISTANT_MESSAGE_SELECTOR = '[data-message-author-role="assistant"]'
MODAL_DISMISS_SELECTORS = [
    '[data-testid="close-button"]',
    "button:has-text('Close')",
    "button:has-text('Stay logged out')",
    "button:has-text('Got it')",
    "button:has-text('Dismiss')",
    "button:has-text('Okay')",
    "button:has-text('Next')",
    "button:has-text('Done')",
]

# ── Models ──
MODELS = [
    {"id": "gpt-5.6-luna", "object": "model", "owned_by": "openai"},
]

# ── Cookie file path resolver ──
def default_cookie_file() -> str:
    env = os.environ.get("CHATGPT_COOKIE_FILE")
    if env:
        return env
    candidate = os.path.join(BASE_DIR, "cookies_parsed.json")
    if os.path.exists(candidate):
        return candidate
    legacy = "/tmp/cookies_parsed.json"
    if os.path.exists(legacy):
        return legacy
    return candidate


COOKIE_FILE = default_cookie_file()
STATE_FILE = os.path.join(BASE_DIR, "state.json")
