import os

# Base Hermes API Server URL (can be overridden with HERMES_API_URL or API_BASE_URL env)
API_BASE_URL = os.environ.get("HERMES_API_URL") or os.environ.get("API_BASE_URL") or "http://localhost:18111"
API_BASE_URL = API_BASE_URL.rstrip("/")
API_STATUS_URL = API_BASE_URL

# Default translation parameters
DEFAULT_MODEL = os.environ.get("HERMES_DEFAULT_MODEL", "gpt-5.6-luna")
DEFAULT_SOURCE_LANG = os.environ.get("HERMES_SOURCE_LANG", "ko")
DEFAULT_TARGET_LANG = os.environ.get("HERMES_TARGET_LANG", "id")

# Security keys if needed
INTERNAL_KEY = os.environ.get("INTERNAL_KEY", "")
WORKER_KEY = os.environ.get("WORKER_KEY", "")

# Default request timeout (seconds)
DEFAULT_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "300"))


def format_server_url(url: str) -> str:
    """Format and normalize server URL."""
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        if url.startswith(":"):
            url = f"http://localhost{url}"
        elif url.isdigit():
            url = f"http://localhost:{url}"
        else:
            url = f"http://{url}"
    return url.rstrip("/")


def set_config(
    server: str | None = None,
    internal_key: str | None = None,
    default_model: str | None = None,
    source_lang: str | None = None,
    target_lang: str | None = None,
    timeout: int | None = None,
) -> None:
    """Update global configuration dynamically from CLI arguments."""
    global API_BASE_URL, API_STATUS_URL, INTERNAL_KEY, DEFAULT_MODEL, DEFAULT_SOURCE_LANG, DEFAULT_TARGET_LANG, DEFAULT_TIMEOUT

    if server:
        API_BASE_URL = format_server_url(server)
        API_STATUS_URL = API_BASE_URL
    if internal_key is not None:
        INTERNAL_KEY = internal_key.strip()
    if default_model is not None and default_model.strip():
        DEFAULT_MODEL = default_model.strip()
    if source_lang is not None and source_lang.strip():
        DEFAULT_SOURCE_LANG = source_lang.strip()
    if target_lang is not None and target_lang.strip():
        DEFAULT_TARGET_LANG = target_lang.strip()
    if timeout is not None and timeout > 0:
        DEFAULT_TIMEOUT = timeout


def get_base_url() -> str:
    return API_BASE_URL


def get_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if INTERNAL_KEY:
        headers["X-Internal-Key"] = INTERNAL_KEY
    return headers
