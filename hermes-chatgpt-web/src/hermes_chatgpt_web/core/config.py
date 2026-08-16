import os
from pathlib import Path
from typing import Any


def is_no_login() -> bool:
    """Check if anonymous / no-login mode is active."""
    return os.environ.get("HERMES_NO_LOGIN") == "1"


# Environment mode: 'dev' or 'prod' (default: 'prod')
def get_env_mode() -> str:
    return os.environ.get("HERMES_ENV", "prod")


def get_base_dir() -> str:
    """
    Resolve base data directory:
    - If CHATGPT_HOME is explicitly set in environment, use it.
    - Otherwise, default to local '.data/<prod|dev>' inside the project.
    """
    env_home = os.environ.get("CHATGPT_HOME")
    if env_home:
        return os.path.expanduser(env_home)
    # project root is 3 levels up from src/hermes_chatgpt_web/core/config.py -> hermes-chatgpt-web
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    subfolder = "dev" if get_env_mode() == "dev" else "prod"
    target = project_root / ".data" / subfolder
    return str(target)


def get_profile_dir() -> str:
    """Return runtime profile directory (profile_anon for no-login, profile for logged-in)."""
    sub = "profile_anon" if is_no_login() else "profile"
    dir_path = os.path.join(get_base_dir(), sub)
    os.makedirs(dir_path, exist_ok=True)
    return dir_path


BASE_DIR = get_base_dir()
os.makedirs(BASE_DIR, exist_ok=True)

STATE_FILE = os.path.join(BASE_DIR, "state.json")  # harvested token/cookies snapshot

# Timezone matching
TIMEZONE = os.environ.get("CHATGPT_TZ", "Asia/Kolkata")

# Ports
ADAPTER_PORT = int(os.environ.get("ADAPTER_PORT", "18111"))

# Security Keys for Internal & Worker endpoints
INTERNAL_KEY = os.environ.get("INTERNAL_KEY", "")
WORKER_KEY = os.environ.get("WORKER_KEY", "")


def default_cookie_file():
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

# ── Translation module ──
TRANSLATION_DB = os.path.join(BASE_DIR, "translation.db")
WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "1"))
WORKER_POLL_INTERVAL = float(os.environ.get("WORKER_POLL_INTERVAL", "2.0"))
TRANSLATION_MAX_TEXT_LENGTH = int(os.environ.get("TRANSLATION_MAX_TEXT_LENGTH", "100000"))
TRANSLATION_JOB_TIMEOUT = int(os.environ.get("TRANSLATION_JOB_TIMEOUT", "480"))
TRANSLATE_OVERRIDE_TOKEN = os.environ.get("TRANSLATE_OVERRIDE_TOKEN", "")
DEFAULT_JOB_COOLDOWN_SECONDS = int(os.environ.get("JOB_COOLDOWN_SECONDS", "10"))
DEFAULT_CONTEXT_REFRESH_JOBS = int(os.environ.get("CONTEXT_REFRESH_JOBS", "10"))

# In-memory runtime configuration cache (updated dynamically via /settings endpoint)
RUNTIME_SETTINGS: dict[str, Any] = {
    "job_cooldown_seconds": DEFAULT_JOB_COOLDOWN_SECONDS,
    "context_refresh_jobs": DEFAULT_CONTEXT_REFRESH_JOBS,
    "worker_poll_interval": WORKER_POLL_INTERVAL,
    "worker_concurrency": WORKER_CONCURRENCY,
    "translation_job_timeout": TRANSLATION_JOB_TIMEOUT,
    "translation_max_text_length": TRANSLATION_MAX_TEXT_LENGTH,
}


def get_runtime_setting(key: str, default: Any = None) -> Any:
    """Retrieve an active runtime setting."""
    return RUNTIME_SETTINGS.get(key, default)


def set_runtime_setting(key: str, value: Any) -> None:
    """Update an active runtime setting in-memory."""
    RUNTIME_SETTINGS[key] = value


def get_job_cooldown_seconds() -> int:
    """Get active job completion cooldown in seconds (default 60s)."""
    val = RUNTIME_SETTINGS.get("job_cooldown_seconds", DEFAULT_JOB_COOLDOWN_SECONDS)
    try:
        return int(val)
    except (ValueError, TypeError):
        return DEFAULT_JOB_COOLDOWN_SECONDS


def get_context_refresh_jobs() -> int:
    """Get completed jobs threshold to refresh browser context (default 10)."""
    val = RUNTIME_SETTINGS.get("context_refresh_jobs", DEFAULT_CONTEXT_REFRESH_JOBS)
    try:
        return int(val)
    except (ValueError, TypeError):
        return DEFAULT_CONTEXT_REFRESH_JOBS

