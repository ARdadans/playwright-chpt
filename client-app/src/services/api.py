import datetime
import time
from pathlib import Path
from typing import Any, Callable

import requests

import core.config as config
from core.prompts import SYSTEM_PROMPT


def _get_headers() -> dict[str, str]:
    return config.get_headers()


# ─── 1. Health & Server Status ────────────────────────────────────────────────


def check_health(silent: bool = True) -> dict[str, Any] | None:
    """Check Hermes server health and internal gateway status."""
    try:
        response = requests.get(f"{config.API_BASE_URL}/health", headers=_get_headers(), timeout=3)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        if not silent:
            print(f"Error checking health: {e}")
        return None


def get_models() -> dict[str, Any] | None:
    """Fetch available OpenAI-compatible models."""
    try:
        response = requests.get(f"{config.API_BASE_URL}/v1/models", headers=_get_headers(), timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching models: {e}")
        return None


def check_status() -> dict[str, Any] | None:
    """Get realtime Playwright and worker pool status."""
    try:
        response = requests.get(f"{config.API_STATUS_URL}/_internal/status", headers=_get_headers(), timeout=5)
        if response.ok:
            return response.json()
        response_health = requests.get(f"{config.API_BASE_URL}/health", headers=_get_headers(), timeout=5)
        if response_health.ok:
            return response_health.json().get("gateway")
    except Exception:
        pass
    return None


# ─── 2. Settings Management ───────────────────────────────────────────────────


def get_settings() -> dict[str, Any] | None:
    """Retrieve active Hermes server settings."""
    try:
        response = requests.get(f"{config.API_BASE_URL}/settings", headers=_get_headers(), timeout=5)
        response.raise_for_status()
        return response.json().get("settings")
    except Exception as e:
        print(f"Error fetching settings: {e}")
        return None


def update_settings(updates: dict[str, Any]) -> dict[str, Any] | None:
    """Update Hermes server settings dynamically (e.g. {'job_cooldown_seconds': 30})."""
    try:
        response = requests.patch(f"{config.API_BASE_URL}/settings", json=updates, headers=_get_headers(), timeout=5)
        response.raise_for_status()
        return response.json().get("settings")
    except Exception as e:
        print(f"Error updating settings: {e}")
        return None


def reset_settings() -> dict[str, Any] | None:
    """Reset Hermes server settings to factory defaults."""
    try:
        response = requests.post(f"{config.API_BASE_URL}/settings/reset", headers=_get_headers(), timeout=5)
        response.raise_for_status()
        return response.json().get("settings")
    except Exception as e:
        print(f"Error resetting settings: {e}")
        return None


# ─── 3. Cookie & Multi-Account Management ─────────────────────────────────────


def list_cookies() -> dict[str, Any] | None:
    """List all accounts and worker pool status."""
    try:
        response = requests.get(f"{config.API_BASE_URL}/cookies", headers=_get_headers(), timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error listing cookies: {e}")
        return None


def add_cookie(name: str, provider: str, cookies: str | list | dict) -> dict[str, Any] | None:
    """Add or update an account cookie."""
    try:
        payload = {"name": name, "provider": provider, "cookies": cookies}
        response = requests.post(f"{config.API_BASE_URL}/cookies", json=payload, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error adding cookie: {e}")
        return None


def delete_cookie(account_id: str) -> bool:
    """Delete an account cookie."""
    try:
        response = requests.delete(f"{config.API_BASE_URL}/cookies/{account_id}", headers=_get_headers(), timeout=5)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error deleting cookie: {e}")
        return False


def pause_cookie(account_id: str) -> dict[str, Any] | None:
    """Pause an account cookie."""
    try:
        response = requests.post(f"{config.API_BASE_URL}/cookies/{account_id}/pause", headers=_get_headers(), timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error pausing cookie: {e}")
        return None


def resume_cookie(account_id: str) -> dict[str, Any] | None:
    """Resume a paused account cookie."""
    try:
        response = requests.post(f"{config.API_BASE_URL}/cookies/{account_id}/resume", headers=_get_headers(), timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error resuming cookie: {e}")
        return None


def reset_cookie_cooldown(account_id: str) -> dict[str, Any] | None:
    """Reset rate limit and context post-job cooldown for an account."""
    try:
        response = requests.post(
            f"{config.API_BASE_URL}/cookies/{account_id}/reset-cooldown", headers=_get_headers(), timeout=5
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error resetting cooldown: {e}")
        return None


def refresh_cookie(account_id: str) -> dict[str, Any] | None:
    """Clear browser context cache/memory and recreate clean BrowserContext."""
    try:
        response = requests.post(
            f"{config.API_BASE_URL}/cookies/{account_id}/refresh", headers=_get_headers(), timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error refreshing cookie context: {e}")
        return None


# ─── 4. Translation Queue & History API ───────────────────────────────────────


def submit_translation_job(
    novel_id: str,
    chapter_number: float,
    text: str,
    source_lang: str | None = None,
    target_lang: str | None = None,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """Submit a chapter translation job to the asynchronous Hermes queue."""
    url = f"{config.API_BASE_URL}/translate"
    payload = {
        "novel_id": novel_id,
        "chapter_number": chapter_number,
        "text": text,
        "source_lang": source_lang or config.DEFAULT_SOURCE_LANG,
        "target_lang": target_lang or config.DEFAULT_TARGET_LANG,
        "model": model or config.DEFAULT_MODEL,
        "force": force,
    }
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=15)
        if response.status_code == 409:
            data = response.json()
            print(f"Notice: Chapter already translated (Job ID: {data.get('job_id')}). Use force=True to re-translate.")
            return {"status": "conflict", "job_id": data.get("job_id")}
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        print(f"Error submitting job: {e}")
        return None


def get_job(job_id: str, fields: str | None = None) -> dict[str, Any] | None:
    """Get job status and translation result."""
    url = f"{config.API_BASE_URL}/translate/{job_id}"
    params = {}
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching job {job_id}: {e}")
        return None


def wait_for_job(
    job_id: str,
    poll_interval: float = 2.5,
    timeout: int | None = None,
    on_status_change: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    """Poll job status until done or failed."""
    t0 = time.time()
    last_status = None
    effective_timeout = timeout if timeout is not None and timeout > 0 else config.DEFAULT_TIMEOUT

    while time.time() - t0 < effective_timeout:
        job = get_job(job_id)
        if not job:
            time.sleep(poll_interval)
            continue

        status = job.get("status")
        if status != last_status:
            last_status = status
            if on_status_change:
                on_status_change(job)

        if status in ("done", "failed", "cancelled"):
            return job

        time.sleep(poll_interval)

    print(f"Timed out waiting for job {job_id} after {effective_timeout}s.")
    return None


def list_jobs(
    novel_id: str | None = None,
    status: str | None = None,
    page: int = 1,
    limit: int = 20,
    sort: str = "created_at:desc",
    fields: str | None = None,
) -> dict[str, Any] | None:
    """List translation jobs with filtering."""
    url = f"{config.API_BASE_URL}/translate"
    params: dict[str, Any] = {"page": page, "limit": limit, "sort": sort}
    if novel_id:
        params["novel_id"] = novel_id
    if status:
        params["status"] = status
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error listing jobs: {e}")
        return None


def retry_job(job_id: str) -> dict[str, Any] | None:
    """Retry a failed job."""
    try:
        response = requests.post(f"{config.API_BASE_URL}/translate/{job_id}/retry", headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error retrying job: {e}")
        return None


def cancel_job(job_id: str) -> dict[str, Any] | None:
    """Cancel a pending or running job."""
    try:
        response = requests.post(f"{config.API_BASE_URL}/translate/{job_id}/cancel", headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error cancelling job: {e}")
        return None


def cancel_all_jobs(status_filter: str = "all", novel_id: str | None = None) -> dict[str, Any] | None:
    """Cancel pending and/or running jobs in bulk."""
    try:
        params: dict[str, Any] = {"status": status_filter}
        if novel_id:
            params["novel_id"] = novel_id
        response = requests.post(f"{config.API_BASE_URL}/translate/cancel-all", params=params, headers=_get_headers(), timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error cancelling all jobs: {e}")
        return None


def delete_job(job_id: str) -> bool:
    """Delete a pending or cancelled job."""
    try:
        response = requests.delete(f"{config.API_BASE_URL}/translate/{job_id}", headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error deleting job: {e}")
        return False


def get_job_history(job_id: str, fields: str | None = None) -> dict[str, Any] | None:
    """Get version history of a job's translations."""
    url = f"{config.API_BASE_URL}/translate/{job_id}/history"
    params = {}
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching job history: {e}")
        return None


def restore_history(history_id: int) -> dict[str, Any] | None:
    """Restore an archived translation version back to active job."""
    try:
        response = requests.post(f"{config.API_BASE_URL}/history/{history_id}/restore", headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error restoring history ID {history_id}: {e}")
        return None


# ─── 5. Novel, Character & Glossary Management ────────────────────────────────


def list_novels(fields: str | None = None) -> list[dict[str, Any]]:
    """List all registered novels."""
    params = {}
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(f"{config.API_BASE_URL}/novels", params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json().get("novels", [])
    except Exception as e:
        print(f"Error listing novels: {e}")
        return []


def get_novel_stats(novel_id: str, fields: str | None = None) -> dict[str, Any] | None:
    """Get aggregated statistics for a novel."""
    params = {}
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(f"{config.API_BASE_URL}/novels/{novel_id}/stats", params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching novel stats: {e}")
        return None


def get_novel_chapters(
    novel_id: str,
    status: str | None = None,
    sort: str = "chapter_number:asc",
    page: int = 1,
    limit: int = 100,
    fields: str | None = None,
) -> dict[str, Any] | None:
    """Get list of chapters for a novel."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/chapters"
    params = {"page": page, "limit": limit, "sort": sort}
    if status:
        params["status"] = status
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching chapters: {e}")
        return None


def get_novel_context(novel_id: str, fields: str | None = None) -> dict[str, Any] | None:
    """Get characters and glossary context for a novel."""
    params = {}
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(f"{config.API_BASE_URL}/novels/{novel_id}/context", params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching context: {e}")
        return None


def get_novel_history(
    novel_id: str,
    chapter_number: float | None = None,
    page: int = 1,
    limit: int = 20,
    fields: str | None = None,
) -> dict[str, Any] | None:
    """Get translation history for an entire novel or single chapter."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/history"
    if chapter_number is not None:
        url += f"/{chapter_number}"
    params = {"page": page, "limit": limit}
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching novel history: {e}")
        return None


# ── Characters CRUD & Bulk ──


def list_characters(
    novel_id: str,
    q: str | None = None,
    gender: str | None = None,
    chapter_from: float | None = None,
    chapter_to: float | None = None,
    page: int = 1,
    limit: int = 50,
    fields: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """List characters for a novel."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/characters"
    params: dict[str, Any] = {"page": page, "limit": limit}
    if q:
        params["q"] = q
    if gender:
        params["gender"] = gender
    if chapter_from is not None:
        params["chapter_from"] = chapter_from
    if chapter_to is not None:
        params["chapter_to"] = chapter_to
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("items", []) if "items" in data else data.get("characters", [])
    except Exception as e:
        print(f"Error listing characters: {e}")
        return []


def create_character(
    novel_id: str,
    name: str,
    native_name: str | None = None,
    gender: str = "unknown",
    notes: str = "",
    first_seen_chapter: float = 1.0,
    appeared_chapters: list[float] | None = None,
) -> dict[str, Any] | None:
    """Add a new character for a novel."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/characters"
    payload = {
        "name": name,
        "native_name": native_name or name,
        "gender": gender,
        "notes": notes,
        "first_seen_chapter": first_seen_chapter,
    }
    if appeared_chapters is not None:
        payload["appeared_chapters"] = appeared_chapters
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=10)
        if response.status_code == 409:
            print(f"[-] Character '{name}' already exists (ID: {response.json().get('id')}).")
            return response.json()
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error creating character: {e}")
        return None


def get_character(novel_id: str, character_id: int, fields: str | None = None) -> dict[str, Any] | None:
    """Get single character by ID."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/characters/{character_id}"
    params = {}
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching character: {e}")
        return None


def update_character(
    novel_id: str,
    character_id: int,
    name: str | None = None,
    native_name: str | None = None,
    gender: str | None = None,
    notes: str | None = None,
    last_updated_chapter: float | None = None,
    appeared_chapters: list[float] | None = None,
) -> dict[str, Any] | None:
    """Update a character."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/characters/{character_id}"
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if native_name is not None:
        payload["native_name"] = native_name
    if gender is not None:
        payload["gender"] = gender
    if notes is not None:
        payload["notes"] = notes
    if last_updated_chapter is not None:
        payload["last_updated_chapter"] = last_updated_chapter
    if appeared_chapters is not None:
        payload["appeared_chapters"] = appeared_chapters
    try:
        response = requests.put(url, json=payload, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error updating character: {e}")
        return None


def delete_character(novel_id: str, character_id: int) -> bool:
    """Delete a character."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/characters/{character_id}"
    try:
        response = requests.delete(url, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error deleting character: {e}")
        return False


# ── Glossary CRUD, Bulk & Export ──


def list_glossary(
    novel_id: str,
    q: str | None = None,
    page: int = 1,
    limit: int = 50,
    fields: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """List glossary terms for a novel."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/glossary"
    params: dict[str, Any] = {"page": page, "limit": limit}
    if q:
        params["q"] = q
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("items", []) if "items" in data else data.get("glossary", [])
    except Exception as e:
        print(f"Error listing glossary: {e}")
        return []


def create_glossary(
    novel_id: str,
    term_source: str,
    term_translation: str,
    notes: str = "",
    first_seen_chapter: float = 1.0,
) -> dict[str, Any] | None:
    """Create a new glossary term."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/glossary"
    payload = {
        "term_source": term_source,
        "term_translation": term_translation,
        "notes": notes,
        "first_seen_chapter": first_seen_chapter,
    }
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=10)
        if response.status_code == 409:
            print(f"[-] Term '{term_source}' already exists (ID: {response.json().get('id')}).")
            return response.json()
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error creating glossary: {e}")
        return None


def get_glossary(novel_id: str, glossary_id: int, fields: str | None = None) -> dict[str, Any] | None:
    """Detail one glossary term."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/glossary/{glossary_id}"
    params = {}
    if fields:
        params["fields"] = fields
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching glossary term: {e}")
        return None


def update_glossary(
    novel_id: str,
    glossary_id: int,
    term_source: str | None = None,
    term_translation: str | None = None,
    notes: str | None = None,
    last_updated_chapter: float | None = None,
) -> dict[str, Any] | None:
    """Update a glossary term."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/glossary/{glossary_id}"
    payload: dict[str, Any] = {}
    if term_source is not None:
        payload["term_source"] = term_source
    if term_translation is not None:
        payload["term_translation"] = term_translation
    if notes is not None:
        payload["notes"] = notes
    if last_updated_chapter is not None:
        payload["last_updated_chapter"] = last_updated_chapter
    try:
        response = requests.put(url, json=payload, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error updating glossary: {e}")
        return None


def delete_glossary(novel_id: str, glossary_id: int) -> bool:
    """Delete a glossary term."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/glossary/{glossary_id}"
    try:
        response = requests.delete(url, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error deleting glossary: {e}")
        return False


def bulk_import_glossary(
    novel_id: str,
    terms: list[dict[str, Any]],
    first_seen_chapter: float = 1.0,
) -> dict[str, Any] | None:
    """Bulk upsert glossary terms for a novel."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/glossary/bulk"
    payload = {"terms": terms, "first_seen_chapter": first_seen_chapter}
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error bulk importing glossary: {e}")
        return None


def export_glossary(
    novel_id: str,
    export_format: str = "json",
    output_path: str | Path | None = None,
) -> dict[str, Any] | str | None:
    """Export glossary terms as JSON or CSV."""
    url = f"{config.API_BASE_URL}/novels/{novel_id}/glossary/export"
    params = {"format": export_format}
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=15)
        response.raise_for_status()
        if export_format == "csv":
            content_str = response.text
            if output_path:
                Path(output_path).write_text(content_str, encoding="utf-8")
            return content_str
        else:
            data = response.json()
            if output_path:
                import json
                Path(output_path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return data
    except Exception as e:
        print(f"Error exporting glossary: {e}")
        return None


# ─── 6. Database Backup & Restore API ─────────────────────────────────────────


def get_database_stats() -> dict[str, Any] | None:
    """Retrieve row counts and stats across all database tables."""
    try:
        response = requests.get(f"{config.API_BASE_URL}/database/stats", headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json().get("stats")
    except Exception as e:
        print(f"Error fetching database stats: {e}")
        return None


def backup_database(output_path: str | Path | None = None) -> tuple[bool, str, int]:
    """
    Download a full SQLite database backup (.zip) from Hermes server.
    Returns: (success, saved_file_path_or_error_msg, byte_count)
    """
    url = f"{config.API_BASE_URL}/database/backup"
    try:
        response = requests.get(url, headers=_get_headers(), timeout=60, stream=True)
        response.raise_for_status()

        if not output_path:
            ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
            output_file = Path.cwd() / f"hermes_backup_{ts}.zip"
        else:
            output_file = Path(output_path)

        output_file.parent.mkdir(parents=True, exist_ok=True)
        total_bytes = 0
        with open(output_file, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    total_bytes += len(chunk)

        return True, str(output_file), total_bytes
    except requests.exceptions.HTTPError as e:
        return False, f"HTTP Error {e.response.status_code}: {e.response.text}", 0
    except Exception as e:
        return False, str(e), 0


def restore_database(file_path: str | Path | None = None, zip_bytes: bytes | None = None) -> dict[str, Any] | None:
    """
    Restore Hermes SQLite database from a local .zip backup archive.
    """
    url = f"{config.API_BASE_URL}/database/restore"
    try:
        if zip_bytes is not None:
            headers = _get_headers()
            headers["Content-Type"] = "application/zip"
            response = requests.post(url, data=zip_bytes, headers=headers, timeout=60)
        elif file_path:
            p = Path(file_path)
            if not p.is_file():
                print(f"[-] Backup file not found: {p}")
                return None
            with open(p, "rb") as f:
                files = {"file": (p.name, f, "application/zip")}
                response = requests.post(url, files=files, headers=_get_headers(), timeout=60)
        else:
            print("[-] Either file_path or zip_bytes must be provided.")
            return None

        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        print(f"[-] HTTP Error {e.response.status_code}: {e.response.text}")
        return None
    except Exception as e:
        print(f"[-] Error during database restore: {e}")
        return None


# ─── 7. Direct OpenAI-Compatible Chat Fallback ────────────────────────────────


def chat_completion(model: str | None = None, prompt: str = "", system_prompt: str | None = None) -> str | None:
    """Direct synchronous chat call to OpenAI-compatible endpoint."""
    url = f"{config.API_BASE_URL}/v1/chat/completions"
    sys_content = system_prompt or SYSTEM_PROMPT
    payload = {
        "model": model or config.DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=120)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None
