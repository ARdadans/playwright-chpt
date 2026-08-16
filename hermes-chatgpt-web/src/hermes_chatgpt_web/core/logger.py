from datetime import datetime

# Supported Tags: [STARTUP], [DB], [BROWSER], [WORKER], [SERVER], [JOB], [SESSION], [ERROR]

def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log(tag: str, message: str, level: str = "INFO"):
    """
    Standard logger adhering to PRD 3.2 format:
    [TIMESTAMP] [LEVEL] [TAG] pesan
    e.g. 2026-08-15 08:00:01.123 INFO  [DB]      Schema applied (4 tables, 6 indexes)
    """
    tag_clean = f"[{tag}]".ljust(10)
    level_clean = level.ljust(5)
    print(f"{_timestamp()} {level_clean} {tag_clean} {message}", flush=True)


def log_startup(message: str):
    log("STARTUP", message, level="INFO")


def log_db(message: str, level: str = "INFO"):
    log("DB", message, level=level)


def log_browser(message: str, level: str = "INFO"):
    log("BROWSER", message, level=level)


def log_worker(message: str, level: str = "INFO"):
    log("WORKER", message, level=level)


def log_server(message: str, level: str = "INFO"):
    log("SERVER", message, level=level)


def log_session(message: str, level: str = "INFO"):
    log("SESSION", message, level=level)


def log_error(message: str):
    log("ERROR", message, level="ERROR")


def log_job_transition(
    job_id: str,
    novel_id: str,
    chapter_number: float,
    old_status: str,
    new_status: str,
    duration: float | None = None,
    error_code: str | None = None,
):
    """
    Format per PRD 3.3:
    [JOB] {job_id_short} | {novel_id} ch.{chapter} | {status_lama:<10} → {status_baru:<10} ({durasi})
    """
    job_id_short = str(job_id)[:8]
    ch_str = f"{chapter_number:g}" if isinstance(chapter_number, (int, float)) else str(chapter_number)
    novel_ch = f"{novel_id} ch.{ch_str}"

    transition = f"{old_status:<10} → {new_status:<10}"
    extra = ""
    level = "INFO"

    if new_status == "done" and duration is not None:
        extra = f" ({duration:.1f}s)"
    elif new_status == "failed":
        level = "ERROR"
        if duration is not None:
            extra = f" ({duration:.1f}s)"
        if error_code:
            extra += f" [{error_code}]"

    msg = f"{job_id_short} | {novel_ch:<15} | {transition}{extra}"
    log("JOB", msg, level=level)


def log_job_retry(job_id: str, novel_id: str, chapter_number: float, retry_count: int):
    """Log retry scheduled per PRD 3.4"""
    job_id_short = str(job_id)[:8]
    ch_str = f"{chapter_number:g}" if isinstance(chapter_number, (int, float)) else str(chapter_number)
    novel_ch = f"{novel_id} ch.{ch_str}"
    msg = f"{job_id_short} | {novel_ch:<15} | retry #{retry_count} scheduled"
    log("JOB", msg, level="WARN")


def print_banner(port: int):
    banner = f"""──────────────────────────────────────────────
  ✓  Hermes is ready
  ➜  Adapter API  →  http://0.0.0.0:{port}
──────────────────────────────────────────────"""
    print(banner, flush=True)
