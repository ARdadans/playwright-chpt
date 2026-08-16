import threading

WATCHDOG_TIMEOUT = 240

shared_state = {
    "ok": False,
    "error": None,
    "title": "",
    "busy": False,
    "busy_since": None,
    "last_activity": 0.0,
    "turns": 0,
    "lock": threading.Lock(),
    "browser": None,
    "page": None,
}
