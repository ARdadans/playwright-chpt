"""
SQLite database layer for Hermes Novel Translation System.

Implements all tables, indexes, CRUD operations, and aggregations according
to PRD: Hermes Novel Translation System (prd-endpoint.md).
"""

import io
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from ..core.config import (
    DEFAULT_CONTEXT_REFRESH_JOBS,
    DEFAULT_JOB_COOLDOWN_SECONDS,
    TRANSLATION_DB,
    TRANSLATION_JOB_TIMEOUT,
    TRANSLATION_MAX_TEXT_LENGTH,
    WORKER_CONCURRENCY,
    WORKER_POLL_INTERVAL,
    set_runtime_setting,
)

# ─── Schema Definition ────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS translation_jobs (
    id                  TEXT    PRIMARY KEY,
    novel_id            TEXT    NOT NULL,
    chapter_number      REAL    NOT NULL,
    source_lang         TEXT    NOT NULL,
    target_lang         TEXT    NOT NULL,
    source_text_raw     TEXT    NOT NULL,
    source_text_cleaned TEXT    NOT NULL,
    model               TEXT    NOT NULL DEFAULT 'gpt-5.6-luna',
    status              TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','running','done','failed','cancelled')),
    result_translation  TEXT,
    result_summary      TEXT,
    raw_response        TEXT,
    cleaned_response    TEXT,
    error_code          TEXT,
    error_message       TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    UNIQUE(novel_id, chapter_number)
);

CREATE INDEX IF NOT EXISTS idx_jobs_novel_status ON translation_jobs(novel_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_status       ON translation_jobs(status);

CREATE TABLE IF NOT EXISTS characters (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id             TEXT    NOT NULL,
    name                 TEXT    NOT NULL,
    native_name          TEXT    NOT NULL,
    gender               TEXT    NOT NULL DEFAULT 'unknown'
                             CHECK(gender IN ('male','female','unknown')),
    notes                TEXT    NOT NULL DEFAULT '',
    first_seen_chapter   REAL    NOT NULL,
    last_updated_chapter REAL    NOT NULL,
    appeared_chapters    TEXT    NOT NULL DEFAULT '[]',
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL,
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_characters_novel ON characters(novel_id);

CREATE TABLE IF NOT EXISTS glossary (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id             TEXT    NOT NULL,
    term_source          TEXT    NOT NULL,
    term_translation     TEXT    NOT NULL,
    notes                TEXT    NOT NULL DEFAULT '',
    first_seen_chapter   REAL    NOT NULL,
    last_updated_chapter REAL    NOT NULL,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL,
    UNIQUE(novel_id, term_source)
);

CREATE INDEX IF NOT EXISTS idx_glossary_novel ON glossary(novel_id);

CREATE TABLE IF NOT EXISTS translation_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id             TEXT    NOT NULL REFERENCES translation_jobs(id) ON DELETE CASCADE,
    novel_id           TEXT    NOT NULL,
    chapter_number     REAL    NOT NULL,
    result_translation TEXT,
    result_summary     TEXT,
    archived_at        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_job   ON translation_history(job_id);
CREATE INDEX IF NOT EXISTS idx_history_novel ON translation_history(novel_id, chapter_number);

CREATE TABLE IF NOT EXISTS account_cookies (
    id                   TEXT    PRIMARY KEY,
    name                 TEXT    NOT NULL,
    provider             TEXT    NOT NULL DEFAULT 'chatgpt',
    cookies_data         TEXT    NOT NULL,
    status               TEXT    NOT NULL DEFAULT 'ACTIVE'
                                 CHECK(status IN ('ACTIVE','BUSY','COOLDOWN','EXPIRED','PAUSED')),
    cooldown_count       INTEGER NOT NULL DEFAULT 0,
    cooldown_until       TEXT,
    last_used_at         TEXT,
    error_message        TEXT,
    total_jobs_processed INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL,
    UNIQUE(name, provider)
);

CREATE INDEX IF NOT EXISTS idx_accounts_status ON account_cookies(status);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _now() -> str:
    """Format current UTC datetime according to ISO 8601 UTC string."""
    return datetime.now(UTC).isoformat()


def _generate_uuid() -> str:
    return str(uuid.uuid4())


async def get_db() -> aiosqlite.Connection:
    """Open a connection with WAL mode and foreign keys enabled."""
    db = await aiosqlite.connect(TRANSLATION_DB)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA foreign_keys=ON;")
    return db


async def init_db():
    """Create all tables and indexes (idempotent), run migrations, and sync app_settings."""
    db = await get_db()
    try:
        await db.executescript(_SCHEMA)
        await db.commit()

        # Check and migrate columns if missing (e.g. when restored from older backup)
        cur = await db.execute("PRAGMA table_info(characters)")
        cols = {row["name"] for row in await cur.fetchall()}
        if "appeared_chapters" not in cols:
            await db.execute("ALTER TABLE characters ADD COLUMN appeared_chapters TEXT NOT NULL DEFAULT '[]'")
            # Backfill appeared_chapters for existing characters
            cur = await db.execute("SELECT id, first_seen_chapter, last_updated_chapter FROM characters")
            rows = await cur.fetchall()
            for r in rows:
                ch_list = [r["first_seen_chapter"]]
                if r["last_updated_chapter"] != r["first_seen_chapter"]:
                    ch_list.append(r["last_updated_chapter"])
                await db.execute(
                    "UPDATE characters SET appeared_chapters=? WHERE id=?",
                    (json.dumps(ch_list), r["id"]),
                )
            await db.commit()

        # Seed defaults if not present and load into RUNTIME_SETTINGS
        defaults = {
            "job_cooldown_seconds": str(DEFAULT_JOB_COOLDOWN_SECONDS),
            "context_refresh_jobs": str(DEFAULT_CONTEXT_REFRESH_JOBS),
            "worker_poll_interval": str(WORKER_POLL_INTERVAL),
            "worker_concurrency": str(WORKER_CONCURRENCY),
            "translation_job_timeout": str(TRANSLATION_JOB_TIMEOUT),
            "translation_max_text_length": str(TRANSLATION_MAX_TEXT_LENGTH),
        }
        now_str = _now()
        for k, v in defaults.items():
            await db.execute(
                "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (k, v, now_str),
            )
        await db.commit()

        # Sync to RUNTIME_SETTINGS
        cur = await db.execute("SELECT key, value FROM app_settings")
        rows = await cur.fetchall()
        for r in rows:
            k = r["key"]
            val_str = r["value"]
            if k in ("job_cooldown_seconds", "context_refresh_jobs", "worker_concurrency", "translation_job_timeout", "translation_max_text_length"):
                try:
                    set_runtime_setting(k, int(val_str))
                except (ValueError, TypeError):
                    set_runtime_setting(k, val_str)
            elif k in ("worker_poll_interval",):
                try:
                    set_runtime_setting(k, float(val_str))
                except (ValueError, TypeError):
                    set_runtime_setting(k, val_str)
            else:
                set_runtime_setting(k, val_str)
    finally:
        await db.close()



# ─── Translation Jobs CRUD ───────────────────────────────────────────────────


async def create_job(
    novel_id: str,
    chapter_number: float,
    source_lang: str,
    target_lang: str,
    source_text_raw: str,
    source_text_cleaned: str,
    model: str = "gpt-5.6-luna",
) -> dict[str, Any]:
    """Insert a new pending job. Returns the job dict."""
    jid = _generate_uuid()
    now = _now()
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO translation_jobs
               (id, novel_id, chapter_number, source_lang, target_lang,
                source_text_raw, source_text_cleaned, model, status, retry_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)""",
            (
                jid,
                novel_id,
                chapter_number,
                source_lang,
                target_lang,
                source_text_raw,
                source_text_cleaned,
                model,
                now,
                now,
            ),
        )
        await db.commit()
    finally:
        await db.close()

    return {
        "id": jid,
        "novel_id": novel_id,
        "chapter_number": chapter_number,
        "status": "pending",
        "source_lang": source_lang,
        "target_lang": target_lang,
        "model": model,
        "created_at": now,
        "updated_at": now,
    }


async def reset_job_to_pending(
    job_id: str,
    source_text_raw: str,
    source_text_cleaned: str,
    source_lang: str,
    target_lang: str,
    model: str = "gpt-5.6-luna",
) -> dict[str, Any]:
    """Reset an existing job to pending for force re-translation."""
    now = _now()
    db = await get_db()
    try:
        await db.execute(
            """UPDATE translation_jobs
               SET source_lang=?, target_lang=?, source_text_raw=?, source_text_cleaned=?,
                   model=?, status='pending', result_translation=NULL, result_summary=NULL,
                   raw_response=NULL, cleaned_response=NULL, error_code=NULL, error_message=NULL,
                   retry_count=0, updated_at=?
               WHERE id=?""",
            (source_lang, target_lang, source_text_raw, source_text_cleaned, model, now, job_id),
        )
        await db.commit()

        cur = await db.execute("SELECT * FROM translation_jobs WHERE id=?", (job_id,))
        row = await cur.fetchone()
        return dict(row)
    finally:
        await db.close()



async def find_existing_job(novel_id: str, chapter_number: float) -> dict[str, Any] | None:
    """Look up existing job by novel_id + chapter_number."""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM translation_jobs WHERE novel_id=? AND chapter_number=?",
            (novel_id, chapter_number),
        )
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_job(job_id: str) -> dict[str, Any] | None:
    """Fetch a single job by ID."""
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM translation_jobs WHERE id=?", (job_id,))
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def list_jobs(
    novel_id: str | None = None,
    status: str | None = None,
    page: int = 1,
    limit: int = 20,
    sort: str = "created_at:desc",
) -> dict[str, Any]:
    """List translation jobs with filter, pagination, and sorting."""
    page = max(1, page)
    limit = min(max(1, limit), 100)
    offset = (page - 1) * limit

    # Parse sort
    sort_field, _, sort_dir = sort.partition(":")
    allowed_fields = {"created_at", "updated_at", "chapter_number", "id", "novel_id", "status"}
    if sort_field not in allowed_fields:
        sort_field = "created_at"
    sort_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"

    where_clauses = []
    params: list[Any] = []

    if novel_id:
        where_clauses.append("novel_id = ?")
        params.append(novel_id)
    if status:
        where_clauses.append("status = ?")
        params.append(status)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    db = await get_db()
    try:
        # Count total
        count_cur = await db.execute(f"SELECT COUNT(*) FROM translation_jobs {where_sql}", params)
        total = (await count_cur.fetchone())[0]

        # Fetch page items
        query = f"SELECT * FROM translation_jobs {where_sql} ORDER BY {sort_field} {sort_dir} LIMIT ? OFFSET ?"
        cur = await db.execute(query, [*params, limit, offset])
        rows = await cur.fetchall()

        items = [dict(r) for r in rows]

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "items": items,
        }
    finally:
        await db.close()


async def claim_next_pending_job() -> dict[str, Any] | None:
    """
    Atomically select the oldest pending job and transition it to 'running'.
    Returns the job dict enriched with character and glossary context.
    """
    now = _now()
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT * FROM translation_jobs WHERE status='pending' ORDER BY created_at ASC LIMIT 1"
        )
        row = await cur.fetchone()
        if not row:
            await db.rollback()
            return None

        job = dict(row)
        await db.execute(
            "UPDATE translation_jobs SET status='running', updated_at=? WHERE id=?",
            (now, job["id"]),
        )
        await db.commit()

        # Load context
        chars_cur = await db.execute(
            "SELECT * FROM characters WHERE novel_id=?",
            (job["novel_id"],),
        )
        chars = [_format_character(r) for r in await chars_cur.fetchall()]

        gloss_cur = await db.execute(
            "SELECT term_source, term_translation, notes FROM glossary WHERE novel_id=?",
            (job["novel_id"],),
        )
        gloss = [dict(r) for r in await gloss_cur.fetchall()]

        return {
            "job_id": job["id"],
            "novel_id": job["novel_id"],
            "chapter_number": job["chapter_number"],
            "source_lang": job["source_lang"],
            "target_lang": job["target_lang"],
            "source_text_cleaned": job["source_text_cleaned"],
            "model": job["model"],
            "retry_count": job["retry_count"],
            "characters": chars,
            "glossary": gloss,
            "created_at": job["created_at"],
            "updated_at": now,
        }
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def requeue_job_to_pending(job_id: str) -> None:
    """Reset a running job back to pending (for failover / cooldown re-queue without failure penalty)."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE translation_jobs SET status='pending', updated_at=? WHERE id=?",
            (_now(), job_id),
        )
        await db.commit()
    finally:
        await db.close()


async def retry_job(job_id: str) -> tuple[bool, str | None]:
    """
    Reset a failed job back to pending.
    Returns (success, current_status).
    """
    db = await get_db()
    try:
        cur = await db.execute("SELECT status FROM translation_jobs WHERE id=?", (job_id,))
        row = await cur.fetchone()
        if not row:
            return False, None
        status = row[0]
        if status != "failed":
            return False, status

        await db.execute(
            """UPDATE translation_jobs
               SET status='pending', error_code=NULL, error_message=NULL, updated_at=?
               WHERE id=?""",
            (_now(), job_id),
        )
        await db.commit()
        return True, "pending"
    finally:
        await db.close()


async def cancel_job(job_id: str) -> tuple[bool, str | None]:
    """
    Cancel a job that is 'pending' or 'running'.
    Returns (success, current_status).
    """
    db = await get_db()
    try:
        cur = await db.execute("SELECT status FROM translation_jobs WHERE id=?", (job_id,))
        row = await cur.fetchone()
        if not row:
            return False, None
        status = row[0]
        if status not in ("pending", "running"):
            return False, status

        await db.execute(
            "UPDATE translation_jobs SET status='cancelled', updated_at=? WHERE id=?",
            (_now(), job_id),
        )
        await db.commit()
        return True, "cancelled"
    finally:
        await db.close()


async def cancel_all_jobs(status_filter: str = "all", novel_id: str | None = None) -> dict[str, Any]:
    """
    Cancel jobs that are 'pending', 'running', or both.
    status_filter: 'all', 'both', 'pending', or 'running'.
    novel_id: optional filter for a specific novel.
    Returns summary of cancelled jobs.
    """
    normalized_status = status_filter.strip().lower() if status_filter else "all"
    if normalized_status in ("all", "both"):
        target_statuses = ("pending", "running")
    elif normalized_status in ("pending", "running"):
        target_statuses = (normalized_status,)
    else:
        raise ValueError(f"Invalid status filter '{status_filter}'. Allowed: 'pending', 'running', 'all', 'both'.")

    placeholders = ",".join("?" for _ in target_statuses)
    params: list[Any] = list(target_statuses)

    query_select = f"SELECT id, novel_id, chapter_number, status FROM translation_jobs WHERE status IN ({placeholders})"
    if novel_id:
        query_select += " AND novel_id=?"
        params.append(novel_id)

    db = await get_db()
    try:
        cur = await db.execute(query_select, params)
        rows = await cur.fetchall()
        if not rows:
            return {
                "status": "cancelled",
                "filter": normalized_status,
                "novel_id": novel_id,
                "cancelled_count": 0,
                "cancelled_pending": 0,
                "cancelled_running": 0,
                "cancelled_job_ids": [],
                "cancelled_jobs": [],
            }

        job_ids = [row[0] for row in rows]
        cancelled_jobs = [
            {
                "id": row[0],
                "novel_id": row[1],
                "chapter_number": row[2],
                "previous_status": row[3],
            }
            for row in rows
        ]

        count_pending = sum(1 for j in cancelled_jobs if j["previous_status"] == "pending")
        count_running = sum(1 for j in cancelled_jobs if j["previous_status"] == "running")

        # Update matching jobs to cancelled
        id_placeholders = ",".join("?" for _ in job_ids)
        update_params = [_now(), *job_ids]
        await db.execute(
            f"UPDATE translation_jobs SET status='cancelled', updated_at=? WHERE id IN ({id_placeholders})",
            update_params,
        )
        await db.commit()

        return {
            "status": "cancelled",
            "filter": normalized_status,
            "novel_id": novel_id,
            "cancelled_count": len(cancelled_jobs),
            "cancelled_pending": count_pending,
            "cancelled_running": count_running,
            "cancelled_job_ids": job_ids,
            "cancelled_jobs": cancelled_jobs,
        }
    finally:
        await db.close()


async def delete_job_by_id(job_id: str) -> tuple[bool, str | None]:
    """
    Delete a job from DB. Only allowed if status is 'pending' or 'cancelled'.
    Returns (success, current_status_or_reason).
    """
    db = await get_db()
    try:
        cur = await db.execute("SELECT status FROM translation_jobs WHERE id=?", (job_id,))
        row = await cur.fetchone()
        if not row:
            return False, "not_found"
        status = row[0]
        if status not in ("pending", "cancelled"):
            return False, status

        await db.execute("DELETE FROM translation_jobs WHERE id=?", (job_id,))
        await db.commit()
        return True, "deleted"
    finally:
        await db.close()


async def complete_job(
    job_id: str,
    result_translation: str = "",
    result_summary: str = "",
    raw_response: str = "",
    cleaned_response: str = "",
    translation: str | None = None,
    summary: str | None = None,
):
    """Mark a job as done and store results."""
    final_translation = translation if translation is not None else result_translation
    final_summary = summary if summary is not None else result_summary

    db = await get_db()
    try:
        await db.execute(
            """UPDATE translation_jobs
               SET status='done', result_translation=?, result_summary=?,
                   raw_response=?, cleaned_response=?, error_code=NULL, error_message=NULL, updated_at=?
               WHERE id=? AND status != 'cancelled'""",
            (final_translation, final_summary, raw_response, cleaned_response, _now(), job_id),
        )
        await db.commit()
    finally:
        await db.close()


async def fail_job(
    job_id: str,
    error_code: str,
    error_message: str,
    retry_count: int,
    raw_response: str | None = None,
    cleaned_response: str | None = None,
):
    """Mark a job as failed with error code and details."""
    db = await get_db()
    try:
        await db.execute(
            """UPDATE translation_jobs
               SET status='failed', error_code=?, error_message=?, retry_count=?,
                   raw_response=COALESCE(?, raw_response), cleaned_response=COALESCE(?, cleaned_response), updated_at=?
               WHERE id=? AND status != 'cancelled'""",
            (error_code, error_message, retry_count, raw_response, cleaned_response, _now(), job_id),
        )
        await db.commit()
    finally:
        await db.close()


async def increment_retry(job_id: str):
    """Increment the retry count for a job."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE translation_jobs SET retry_count = retry_count + 1, updated_at=? WHERE id=?",
            (_now(), job_id),
        )
        await db.commit()
    finally:
        await db.close()


async def get_running_jobs() -> list[dict[str, Any]]:
    """List jobs with status 'running' for dead-job detection."""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id as job_id, novel_id, chapter_number, updated_at FROM translation_jobs WHERE status='running' ORDER BY updated_at ASC"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


# ─── Translation History CRUD ────────────────────────────────────────────────


async def archive_job(job_id: str):
    """Archive current job result into translation_history table before re-translating."""
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM translation_jobs WHERE id=?", (job_id,))
        row = await cur.fetchone()
        if row and (row["result_translation"] or row["result_summary"]):
            await db.execute(
                """INSERT INTO translation_history
                   (job_id, novel_id, chapter_number, result_translation, result_summary, archived_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    row["id"],
                    row["novel_id"],
                    row["chapter_number"],
                    row["result_translation"],
                    row["result_summary"],
                    _now(),
                ),
            )
            await db.commit()
    finally:
        await db.close()


async def get_job_history(job_id: str) -> list[dict[str, Any]]:
    """Get history entries for a specific job."""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id, result_translation, result_summary, archived_at FROM translation_history WHERE job_id=? ORDER BY archived_at DESC",
            (job_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_novel_history(
    novel_id: str,
    chapter_number: float | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """Get translation history for a novel, optionally filtered by chapter."""
    page = max(1, page)
    limit = min(max(1, limit), 100)
    offset = (page - 1) * limit

    db = await get_db()
    try:
        if chapter_number is not None:
            count_cur = await db.execute(
                "SELECT COUNT(*) FROM translation_history WHERE novel_id=? AND chapter_number=?",
                (novel_id, chapter_number),
            )
            total = (await count_cur.fetchone())[0]

            cur = await db.execute(
                """SELECT id, job_id, novel_id, chapter_number, result_translation, result_summary, archived_at
                   FROM translation_history
                   WHERE novel_id=? AND chapter_number=?
                   ORDER BY archived_at DESC LIMIT ? OFFSET ?""",
                (novel_id, chapter_number, limit, offset),
            )
        else:
            count_cur = await db.execute(
                "SELECT COUNT(*) FROM translation_history WHERE novel_id=?",
                (novel_id,),
            )
            total = (await count_cur.fetchone())[0]

            cur = await db.execute(
                """SELECT id, job_id, novel_id, chapter_number, result_translation, result_summary, archived_at
                   FROM translation_history
                   WHERE novel_id=?
                   ORDER BY archived_at DESC LIMIT ? OFFSET ?""",
                (novel_id, limit, offset),
            )

        rows = await cur.fetchall()
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "history": [dict(r) for r in rows],
        }
    finally:
        await db.close()


async def restore_history(history_id: int) -> dict[str, Any] | None:
    """
    Restore an archived translation to the active job.
    Returns {job_id, restored_from_history_id} if successful.
    """
    now = _now()
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM translation_history WHERE id=?",
            (history_id,),
        )
        hist = await cur.fetchone()
        if not hist:
            return None

        hist_dict = dict(hist)
        job_id = hist_dict["job_id"]

        # Update active job
        cur_job = await db.execute("SELECT id FROM translation_jobs WHERE id=?", (job_id,))
        if not await cur_job.fetchone():
            return None

        await db.execute(
            """UPDATE translation_jobs
               SET status='done', result_translation=?, result_summary=?, error_code=NULL, error_message=NULL, updated_at=?
               WHERE id=?""",
            (hist_dict["result_translation"], hist_dict["result_summary"], now, job_id),
        )
        await db.commit()
        return {
            "ok": True,
            "job_id": job_id,
            "restored_from_history_id": history_id,
        }
    finally:
        await db.close()


# ─── Novels Aggregations ─────────────────────────────────────────────────────


async def list_novels() -> list[dict[str, Any]]:
    """List all registered novels aggregated from jobs."""
    db = await get_db()
    try:
        cur = await db.execute(
            """SELECT novel_id, COUNT(*) as total_jobs, MAX(chapter_number) as latest_chapter
               FROM translation_jobs
               GROUP BY novel_id
               ORDER BY novel_id ASC"""
        )
        rows = await cur.fetchall()
        return [
            {
                "novel_id": r["novel_id"],
                "total_jobs": r["total_jobs"],
                "latest_chapter": float(r["latest_chapter"]) if r["latest_chapter"] is not None else 0.0,
            }
            for r in rows
        ]
    finally:
        await db.close()


async def list_chapters(
    novel_id: str,
    status: str | None = None,
    page: int = 1,
    limit: int = 100,
    sort: str = "chapter_number:asc",
) -> dict[str, Any]:
    """List all chapters for a novel with status filter, sorting, and pagination."""
    page = max(1, page)
    limit = min(max(1, limit), 500)
    offset = (page - 1) * limit

    sort_field, _, sort_dir = sort.partition(":")
    allowed_fields = {"chapter_number", "created_at", "updated_at", "status", "id"}
    if sort_field not in allowed_fields:
        sort_field = "chapter_number"
    sort_dir = "DESC" if sort_dir.lower() == "desc" else "ASC"

    where_clauses = ["novel_id = ?"]
    params: list[Any] = [novel_id]

    if status:
        where_clauses.append("status = ?")
        params.append(status)

    where_sql = "WHERE " + " AND ".join(where_clauses)

    db = await get_db()
    try:
        count_cur = await db.execute(f"SELECT COUNT(*) FROM translation_jobs {where_sql}", params)
        total = (await count_cur.fetchone())[0]

        query = f"""
            SELECT id as job_id, novel_id, chapter_number, status, source_lang, target_lang, model,
                   created_at, updated_at,
                   (result_translation IS NOT NULL AND length(result_translation) > 0) as has_translation,
                   result_summary as chapter_summary
            FROM translation_jobs
            {where_sql}
            ORDER BY {sort_field} {sort_dir}
            LIMIT ? OFFSET ?
        """
        cur = await db.execute(query, [*params, limit, offset])
        rows = await cur.fetchall()

        chapters = []
        for r in rows:
            d = dict(r)
            d["has_translation"] = bool(d["has_translation"])
            chapters.append(d)

        return {
            "novel_id": novel_id,
            "total": total,
            "page": page,
            "limit": limit,
            "chapters": chapters,
        }
    finally:
        await db.close()



async def get_novel_stats(novel_id: str) -> dict[str, Any] | None:
    """Get complete statistics for a single novel."""
    db = await get_db()
    try:
        # Check if novel has any jobs/characters/glossary
        job_cur = await db.execute(
            "SELECT COUNT(*) as total_jobs, MAX(chapter_number) as latest_chapter FROM translation_jobs WHERE novel_id=?",
            (novel_id,),
        )
        job_info = await job_cur.fetchone()
        total_jobs = job_info["total_jobs"] if job_info else 0
        latest_chapter = float(job_info["latest_chapter"]) if job_info and job_info["latest_chapter"] is not None else 0.0

        # Status counts
        status_cur = await db.execute(
            "SELECT status, COUNT(*) as count FROM translation_jobs WHERE novel_id=? GROUP BY status",
            (novel_id,),
        )
        status_rows = await status_cur.fetchall()
        by_status = {"done": 0, "pending": 0, "running": 0, "failed": 0, "cancelled": 0}
        for r in status_rows:
            if r["status"] in by_status:
                by_status[r["status"]] = r["count"]

        # Character count
        char_cur = await db.execute("SELECT COUNT(*) FROM characters WHERE novel_id=?", (novel_id,))
        total_chars = (await char_cur.fetchone())[0]

        # Glossary count
        gloss_cur = await db.execute("SELECT COUNT(*) FROM glossary WHERE novel_id=?", (novel_id,))
        total_gloss = (await gloss_cur.fetchone())[0]

        if total_jobs == 0 and total_chars == 0 and total_gloss == 0:
            return None

        return {
            "novel_id": novel_id,
            "total_jobs": total_jobs,
            "by_status": by_status,
            "total_characters": total_chars,
            "total_glossary_terms": total_gloss,
            "latest_chapter": latest_chapter,
        }
    finally:
        await db.close()


# ─── Characters CRUD ─────────────────────────────────────────────────────────


def _normalize_gender(g: str | None, default: str = "unknown") -> str:
    if not g:
        return default
    g_str = str(g).strip().lower()
    if g_str in ("male", "pria", "laki-laki", "l", "m"):
        return "male"
    elif g_str in ("female", "wanita", "perempuan", "p", "f"):
        return "female"
    elif g_str in ("unknown",):
        return "unknown"
    return default


def _format_character(row: Any) -> dict[str, Any]:
    """Helper to format a character row into standard API JSON response with parsed appeared_chapters."""
    if not row:
        return {}
    d = dict(row)
    app = d.get("appeared_chapters")
    if isinstance(app, str):
        try:
            d["appeared_chapters"] = json.loads(app) if app else []
        except (ValueError, TypeError):
            d["appeared_chapters"] = []
    elif app is None:
        d["appeared_chapters"] = []
    return d


async def list_characters(
    novel_id: str,
    q: str | None = None,
    gender: str | None = None,
    chapter_from: float | None = None,
    chapter_to: float | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """List characters with filters and pagination."""
    page = max(1, page)
    limit = min(max(1, limit), 100)
    offset = (page - 1) * limit

    where = ["novel_id = ?"]
    params: list[Any] = [novel_id]

    if q:
        where.append("(name LIKE ? OR native_name LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if gender:
        where.append("gender = ?")
        params.append(_normalize_gender(gender))
    if chapter_from is not None:
        where.append("first_seen_chapter >= ?")
        params.append(chapter_from)
    if chapter_to is not None:
        where.append("last_updated_chapter <= ?")
        params.append(chapter_to)

    where_sql = "WHERE " + " AND ".join(where)

    db = await get_db()
    try:
        count_cur = await db.execute(f"SELECT COUNT(*) FROM characters {where_sql}", params)
        total = (await count_cur.fetchone())[0]

        cur = await db.execute(
            f"SELECT * FROM characters {where_sql} ORDER BY id ASC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )

        rows = await cur.fetchall()
        return {
            "total": total,
            "items": [_format_character(r) for r in rows],
        }
    finally:
        await db.close()


async def create_character(
    novel_id: str,
    name: str,
    native_name: str,
    gender: str = "unknown",
    notes: str = "",
    first_seen_chapter: float = 1.0,
    appeared_chapters: list[float] | None = None,
) -> tuple[dict[str, Any] | None, int | None]:
    """
    Create a character. If already exists for this novel, returns (None, existing_id).
    Otherwise returns (created_character_dict, None).
    """
    now = _now()
    gender = _normalize_gender(gender)
    if appeared_chapters is None:
        app_list = [first_seen_chapter]
    else:
        app_list = list(appeared_chapters)
        if first_seen_chapter not in app_list:
            app_list.append(first_seen_chapter)
            app_list.sort()
    app_json = json.dumps(app_list)

    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id FROM characters WHERE novel_id=? AND name=?",
            (novel_id, name),
        )
        row = await cur.fetchone()
        if row:
            return None, row[0]

        cur = await db.execute(
            """INSERT INTO characters
               (novel_id, name, native_name, gender, notes, first_seen_chapter, last_updated_chapter, appeared_chapters, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                novel_id,
                name,
                native_name,
                gender,
                notes,
                first_seen_chapter,
                first_seen_chapter,
                app_json,
                now,
                now,
            ),
        )
        char_id = cur.lastrowid
        await db.commit()

        return {
            "id": char_id,
            "novel_id": novel_id,
            "name": name,
            "native_name": native_name,
            "gender": gender,
            "notes": notes,
            "first_seen_chapter": first_seen_chapter,
            "last_updated_chapter": first_seen_chapter,
            "appeared_chapters": app_list,
            "created_at": now,
            "updated_at": now,
        }, None
    finally:
        await db.close()


async def get_character(novel_id: str, character_id: int) -> dict[str, Any] | None:
    """Fetch single character by id."""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM characters WHERE novel_id=? AND id=?",
            (novel_id, character_id),
        )
        row = await cur.fetchone()
        return _format_character(row) if row else None
    finally:
        await db.close()


async def update_character(
    novel_id: str,
    character_id: int,
    name: str | None = None,
    native_name: str | None = None,
    gender: str | None = None,
    notes: str | None = None,
    last_updated_chapter: float | None = None,
    appeared_chapters: list[float] | None = None,
) -> dict[str, Any] | None:
    """Update fields on character."""
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM characters WHERE novel_id=? AND id=?", (novel_id, character_id))
        row = await cur.fetchone()
        if not row:
            return None

        current = _format_character(row)
        new_name = name if name is not None else current["name"]
        new_native = native_name if native_name is not None else current["native_name"]
        new_gender = _normalize_gender(gender, default=current["gender"]) if gender is not None else current["gender"]
        new_notes = notes if notes is not None else current["notes"]
        new_ch = last_updated_chapter if last_updated_chapter is not None else current["last_updated_chapter"]
        if appeared_chapters is not None:
            new_app_list = sorted(list(set(appeared_chapters)))
        else:
            new_app_list = current["appeared_chapters"]
        now = _now()

        await db.execute(
            """UPDATE characters
               SET name=?, native_name=?, gender=?, notes=?, last_updated_chapter=?, appeared_chapters=?, updated_at=?
               WHERE novel_id=? AND id=?""",
            (new_name, new_native, new_gender, new_notes, new_ch, json.dumps(new_app_list), now, novel_id, character_id),
        )
        await db.commit()

        current.update(
            {
                "name": new_name,
                "native_name": new_native,
                "gender": new_gender,
                "notes": new_notes,
                "last_updated_chapter": new_ch,
                "appeared_chapters": new_app_list,
                "updated_at": now,
            }
        )
        return current
    finally:
        await db.close()


async def delete_character(novel_id: str, character_id: int) -> bool:
    """Delete character by ID."""
    db = await get_db()
    try:
        cur = await db.execute(
            "DELETE FROM characters WHERE novel_id=? AND id=?",
            (novel_id, character_id),
        )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def upsert_characters(novel_id: str, chapter_number: float, characters: list[dict[str, Any]]):
    """
    Insert new characters or merge updated ones (append notes with chapter tag, update gender if unknown, and track appeared_chapters).
    """
    now = _now()
    db = await get_db()
    try:
        for char in characters:
            name = (char.get("name") or "").strip()
            if not name:
                continue
            native_name = (char.get("native_name") or name).strip()
            gender = _normalize_gender(char.get("gender"))
            new_note = (char.get("notes") or "").strip()

            cur = await db.execute(
                "SELECT id, native_name, gender, notes, first_seen_chapter, last_updated_chapter, appeared_chapters FROM characters WHERE novel_id=? AND name=?",
                (novel_id, name),
            )
            row = await cur.fetchone()

            if row:
                row_dict = dict(row)
                existing_notes = row_dict.get("notes") or ""
                existing_gender = row_dict.get("gender") or "unknown"
                app_raw = row_dict.get("appeared_chapters")
                app_list = []
                if isinstance(app_raw, str) and app_raw:
                    try:
                        app_list = json.loads(app_raw)
                    except Exception:
                        app_list = []
                if not isinstance(app_list, list):
                    app_list = []

                if chapter_number not in app_list:
                    app_list.append(chapter_number)
                    app_list.sort()

                if new_note:
                    if existing_notes:
                        if new_note not in existing_notes:
                            merged_notes = f"{existing_notes}\n[Ch{chapter_number:g}] {new_note}".strip()
                        else:
                            merged_notes = existing_notes
                    else:
                        merged_notes = f"[Ch{chapter_number:g}] {new_note}".strip()
                else:
                    merged_notes = existing_notes

                updated_native = native_name if native_name != name and (not row_dict.get("native_name") or row_dict.get("native_name") == name) else (row_dict.get("native_name") or native_name)
                # If existing gender is unknown and new chat response has detected gender (male/female), use detected gender
                if gender in ("male", "female") and existing_gender not in ("male", "female"):
                    updated_gender = gender
                else:
                    updated_gender = existing_gender

                await db.execute(
                    """UPDATE characters
                       SET notes=?, last_updated_chapter=?, appeared_chapters=?,
                           native_name=?, gender=?, updated_at=?
                       WHERE novel_id=? AND id=?""",
                    (
                        merged_notes,
                        chapter_number,
                        json.dumps(app_list),
                        updated_native,
                        updated_gender,
                        now,
                        novel_id,
                        row_dict["id"],
                    ),
                )
            else:
                initial_note = f"[Ch{chapter_number:g}] {new_note}".strip() if new_note else ""
                app_list = [chapter_number]
                await db.execute(
                    """INSERT INTO characters
                       (novel_id, name, native_name, gender, notes,
                        first_seen_chapter, last_updated_chapter, appeared_chapters, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        novel_id,
                        name,
                        native_name,
                        gender,
                        initial_note,
                        chapter_number,
                        chapter_number,
                        json.dumps(app_list),
                        now,
                        now,
                    ),
                )
        await db.commit()
    finally:
        await db.close()


# ─── Glossary CRUD ───────────────────────────────────────────────────────────


async def list_glossary(
    novel_id: str,
    q: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """List glossary terms with search and pagination."""
    page = max(1, page)
    limit = min(max(1, limit), 100)
    offset = (page - 1) * limit

    where = ["novel_id = ?"]
    params: list[Any] = [novel_id]

    if q:
        where.append("(term_source LIKE ? OR term_translation LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])

    where_sql = "WHERE " + " AND ".join(where)

    db = await get_db()
    try:
        count_cur = await db.execute(f"SELECT COUNT(*) FROM glossary {where_sql}", params)
        total = (await count_cur.fetchone())[0]

        cur = await db.execute(
            f"SELECT * FROM glossary {where_sql} ORDER BY id ASC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )

        rows = await cur.fetchall()
        return {
            "total": total,
            "items": [dict(r) for r in rows],
        }
    finally:
        await db.close()


async def create_glossary(
    novel_id: str,
    term_source: str,
    term_translation: str,
    notes: str = "",
    first_seen_chapter: float = 1.0,
) -> tuple[dict[str, Any] | None, int | None]:
    """
    Create a glossary term. If term_source already exists, returns (None, existing_id).
    Otherwise returns (created_dict, None).
    """
    now = _now()
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id FROM glossary WHERE novel_id=? AND term_source=?",
            (novel_id, term_source),
        )
        row = await cur.fetchone()
        if row:
            return None, row[0]

        cur = await db.execute(
            """INSERT INTO glossary
               (novel_id, term_source, term_translation, notes, first_seen_chapter, last_updated_chapter, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                novel_id,
                term_source,
                term_translation,
                notes,
                first_seen_chapter,
                first_seen_chapter,
                now,
                now,
            ),
        )
        gloss_id = cur.lastrowid
        await db.commit()

        return {
            "id": gloss_id,
            "novel_id": novel_id,
            "term_source": term_source,
            "term_translation": term_translation,
            "notes": notes,
            "first_seen_chapter": first_seen_chapter,
            "last_updated_chapter": first_seen_chapter,
            "created_at": now,
            "updated_at": now,
        }, None
    finally:
        await db.close()


async def get_glossary(novel_id: str, glossary_id: int) -> dict[str, Any] | None:
    """Fetch a single glossary term by id."""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM glossary WHERE novel_id=? AND id=?",
            (novel_id, glossary_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def update_glossary(
    novel_id: str,
    glossary_id: int,
    term_source: str | None = None,
    term_translation: str | None = None,
    notes: str | None = None,
    last_updated_chapter: float | None = None,
) -> dict[str, Any] | None:
    """Update fields on a glossary term."""
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM glossary WHERE novel_id=? AND id=?", (novel_id, glossary_id))
        row = await cur.fetchone()
        if not row:
            return None

        current = dict(row)
        new_source = term_source if term_source is not None else current["term_source"]
        new_trans = term_translation if term_translation is not None else current["term_translation"]
        new_notes = notes if notes is not None else current["notes"]
        new_ch = last_updated_chapter if last_updated_chapter is not None else current["last_updated_chapter"]
        now = _now()

        await db.execute(
            """UPDATE glossary
               SET term_source=?, term_translation=?, notes=?, last_updated_chapter=?, updated_at=?
               WHERE novel_id=? AND id=?""",
            (new_source, new_trans, new_notes, new_ch, now, novel_id, glossary_id),
        )
        await db.commit()

        current.update(
            {
                "term_source": new_source,
                "term_translation": new_trans,
                "notes": new_notes,
                "last_updated_chapter": new_ch,
                "updated_at": now,
            }
        )
        return current
    finally:
        await db.close()


async def delete_glossary(novel_id: str, glossary_id: int) -> bool:
    """Delete a glossary term by ID."""
    db = await get_db()
    try:
        cur = await db.execute(
            "DELETE FROM glossary WHERE novel_id=? AND id=?",
            (novel_id, glossary_id),
        )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def bulk_upsert_glossary(
    novel_id: str,
    terms: list[dict[str, Any]],
    first_seen_chapter: float = 1.0,
) -> dict[str, int]:
    """
    Bulk import glossary terms with upsert behavior.
    Returns {"inserted": X, "updated": Y, "skipped": Z}.
    """
    now = _now()
    inserted = 0
    updated = 0
    skipped = 0

    db = await get_db()
    try:
        for t in terms:
            term_source = (t.get("term_source") or "").strip()
            term_trans = (t.get("term_translation") or "").strip()
            notes = t.get("notes", "")
            if not term_source:
                skipped += 1
                continue

            cur = await db.execute(
                "SELECT id FROM glossary WHERE novel_id=? AND term_source=?",
                (novel_id, term_source),
            )
            row = await cur.fetchone()
            if row:
                # Update
                await db.execute(
                    """UPDATE glossary
                       SET term_translation=?, notes=?, last_updated_chapter=?, updated_at=?
                       WHERE id=?""",
                    (term_trans, notes, first_seen_chapter, now, row[0]),
                )
                updated += 1
            else:
                # Insert
                await db.execute(
                    """INSERT INTO glossary
                       (novel_id, term_source, term_translation, notes, first_seen_chapter, last_updated_chapter, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (novel_id, term_source, term_trans, notes, first_seen_chapter, first_seen_chapter, now, now),
                )
                inserted += 1

        await db.commit()
        return {"inserted": inserted, "updated": updated, "skipped": skipped}
    finally:
        await db.close()


async def export_glossary_terms(novel_id: str) -> list[dict[str, Any]]:
    """Export all glossary terms for a novel."""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT term_source, term_translation, notes, first_seen_chapter, last_updated_chapter FROM glossary WHERE novel_id=? ORDER BY term_source ASC",
            (novel_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def upsert_glossary(novel_id: str, chapter_number: float, glossary: list[dict[str, Any]]):
    """Insert new glossary terms if not existing."""
    now = _now()
    db = await get_db()
    try:
        for entry in glossary:
            term_source = (entry.get("term_source") or "").strip()
            term_trans = (entry.get("term_translation") or "").strip()
            if not term_source:
                continue

            cur = await db.execute(
                "SELECT id FROM glossary WHERE novel_id=? AND LOWER(term_source)=LOWER(?)",
                (novel_id, term_source),
            )
            if await cur.fetchone():
                continue

            await db.execute(
                """INSERT INTO glossary
                   (novel_id, term_source, term_translation, notes,
                    first_seen_chapter, last_updated_chapter, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    novel_id,
                    term_source,
                    term_trans,
                    entry.get("notes", ""),
                    chapter_number,
                    chapter_number,
                    now,
                    now,
                ),
            )
        await db.commit()
    finally:
        await db.close()


# ─── Context for Prompts ─────────────────────────────────────────────────────


async def get_context(novel_id: str) -> dict[str, Any]:
    """Return characters and glossary for novel context."""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM characters WHERE novel_id=?",
            (novel_id,),
        )
        chars = [_format_character(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT term_source, term_translation, notes FROM glossary WHERE novel_id=?",
            (novel_id,),
        )
        gloss = [dict(r) for r in await cur.fetchall()]

        return {"characters": chars, "glossary": gloss}
    finally:
        await db.close()


# ─── Account Cookies Management ──────────────────────────────────────────────


def _format_account_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    d = dict(row)
    cooldown_until = d.get("cooldown_until")
    rem_sec = None
    if cooldown_until and d.get("status") == "COOLDOWN":
        try:
            target_dt = datetime.fromisoformat(cooldown_until)
            now_dt = datetime.now(UTC)
            rem = (target_dt - now_dt).total_seconds()
            rem_sec = max(0, int(rem))
        except Exception:
            rem_sec = 0
    d["cooldown_remaining_seconds"] = rem_sec
    return d


async def upsert_account_cookie(
    name: str,
    provider: str,
    cookies_data: str | list | dict,
    status: str = "ACTIVE",
) -> dict[str, Any]:
    """
    Insert or update account cookie entry in database.
    If exists, updates cookies_data without overwriting COOLDOWN/PAUSED/BUSY status.
    """
    cookies_data_str = json.dumps(cookies_data) if isinstance(cookies_data, (list, dict)) else str(cookies_data)

    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id FROM account_cookies WHERE name = ? AND provider = ?",
            (name, provider),
        )
        row = await cur.fetchone()
        now_str = _now()
        if row:
            acc_id = row["id"]
            await db.execute(
                """
                UPDATE account_cookies
                SET cookies_data = ?, updated_at = ?
                WHERE id = ?
                """,
                (cookies_data_str, now_str, acc_id),
            )
            await db.commit()
        else:
            acc_id = _generate_uuid()
            await db.execute(
                """
                INSERT INTO account_cookies (
                    id, name, provider, cookies_data, status, cooldown_count,
                    cooldown_until, last_used_at, error_message, total_jobs_processed,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, NULL, NULL, NULL, 0, ?, ?)
                """,
                (acc_id, name, provider, cookies_data_str, status, now_str, now_str),
            )
            await db.commit()

        cur = await db.execute("SELECT * FROM account_cookies WHERE id = ?", (acc_id,))
        acc_row = await cur.fetchone()
        return _format_account_row(acc_row)
    finally:
        await db.close()


async def get_all_account_cookies() -> list[dict[str, Any]]:
    """Retrieve all account cookies with dynamic cooldown remaining seconds."""
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM account_cookies ORDER BY created_at ASC")
        rows = await cur.fetchall()
        return [_format_account_row(r) for r in rows]
    finally:
        await db.close()


async def get_account_cookie_by_id(account_id: str) -> dict[str, Any] | None:
    """Retrieve single account cookie by ID."""
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM account_cookies WHERE id = ?", (account_id,))
        row = await cur.fetchone()
        return _format_account_row(row) if row else None
    finally:
        await db.close()


async def delete_account_cookie(account_id: str) -> bool:
    """Delete an account cookie by ID."""
    db = await get_db()
    try:
        cur = await db.execute("DELETE FROM account_cookies WHERE id = ?", (account_id,))
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def set_account_status(
    account_id: str, status: str, error_message: str | None = None
) -> dict[str, Any] | None:
    """Explicitly set status of an account (e.g. ACTIVE, BUSY, PAUSED, EXPIRED)."""
    db = await get_db()
    try:
        now_str = _now()
        await db.execute(
            """
            UPDATE account_cookies
            SET status = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error_message, now_str, account_id),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM account_cookies WHERE id = ?", (account_id,))
        row = await cur.fetchone()
        return _format_account_row(row) if row else None
    finally:
        await db.close()


async def set_account_cooldown(
    account_id: str, error_message: str | None = None
) -> dict[str, Any] | None:
    """
    Set staged cooldown for an account upon encountering rate limit:
    - 1st rate limit -> 2 hours COOLDOWN
    - 2nd rate limit -> 4 hours COOLDOWN
    - 3rd rate limit -> EXPIRED
    """
    db = await get_db()
    try:
        cur = await db.execute("SELECT cooldown_count FROM account_cookies WHERE id = ?", (account_id,))
        row = await cur.fetchone()
        if not row:
            return None

        current_count = row["cooldown_count"] or 0
        new_count = current_count + 1
        now_dt = datetime.now(UTC)
        now_str = now_dt.isoformat()

        if new_count == 1:
            cooldown_until = (now_dt + timedelta(hours=2)).isoformat()
            new_status = "COOLDOWN"
        elif new_count == 2:
            cooldown_until = (now_dt + timedelta(hours=4)).isoformat()
            new_status = "COOLDOWN"
        else:
            cooldown_until = None
            new_status = "EXPIRED"

        await db.execute(
            """
            UPDATE account_cookies
            SET status = ?, cooldown_count = ?, cooldown_until = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_status, new_count, cooldown_until, error_message, now_str, account_id),
        )
        await db.commit()

        cur = await db.execute("SELECT * FROM account_cookies WHERE id = ?", (account_id,))
        row = await cur.fetchone()
        return _format_account_row(row) if row else None
    finally:
        await db.close()


async def reset_account_cooldown(account_id: str) -> dict[str, Any] | None:
    """Manually reset account cooldown and restore status to ACTIVE."""
    db = await get_db()
    try:
        now_str = _now()
        await db.execute(
            """
            UPDATE account_cookies
            SET status = 'ACTIVE', cooldown_count = 0, cooldown_until = NULL, error_message = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now_str, account_id),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM account_cookies WHERE id = ?", (account_id,))
        row = await cur.fetchone()
        return _format_account_row(row) if row else None
    finally:
        await db.close()


async def record_account_job_done(account_id: str) -> None:
    """Record job completion on account: increment total_jobs_processed, reset cooldown count."""
    db = await get_db()
    try:
        now_str = _now()
        await db.execute(
            """
            UPDATE account_cookies
            SET status = 'ACTIVE', total_jobs_processed = total_jobs_processed + 1,
                last_used_at = ?, cooldown_count = 0, error_message = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now_str, now_str, account_id),
        )
        await db.commit()
    finally:
        await db.close()


async def check_and_release_cooldowns() -> list[str]:
    """Check expired cooldown timers and release accounts back to ACTIVE."""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id, cooldown_until FROM account_cookies WHERE status = 'COOLDOWN'"
        )
        rows = await cur.fetchall()
        now_dt = datetime.now(UTC)
        released = []
        for r in rows:
            cd_until = r["cooldown_until"]
            if cd_until:
                try:
                    dt = datetime.fromisoformat(cd_until)
                    if now_dt >= dt:
                        await db.execute(
                            """
                            UPDATE account_cookies
                            SET status = 'ACTIVE', cooldown_until = NULL, error_message = NULL, updated_at = ?
                            WHERE id = ?
                            """,
                            (_now(), r["id"]),
                        )
                        released.append(r["id"])
                except Exception:
                    pass
        if released:
            await db.commit()
        return released
    finally:
        await db.close()


# ─── Database Backup & Restore ──────────────────────────────────────────────


async def get_database_stats() -> dict[str, Any]:
    """Get count of rows across all tables in the database."""
    db = await get_db()
    try:
        cur = await db.execute("SELECT COUNT(*) FROM translation_jobs")
        jobs_count = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM characters")
        characters_count = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM glossary")
        glossary_count = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM translation_history")
        history_count = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(DISTINCT novel_id) FROM translation_jobs")
        novels_count = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM account_cookies")
        accounts_count = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM account_cookies WHERE status = 'ACTIVE'")
        active_accounts = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM account_cookies WHERE status = 'COOLDOWN'")
        cooldown_accounts = (await cur.fetchone())[0]

        return {
            "translation_jobs": jobs_count,
            "characters": characters_count,
            "glossary": glossary_count,
            "translation_history": history_count,
            "novels_count": novels_count,
            "account_cookies": accounts_count,
            "active_accounts": active_accounts,
            "cooldown_accounts": cooldown_accounts,
        }
    finally:
        await db.close()


async def backup_database_to_zip() -> tuple[bytes, dict[str, Any]]:
    """
    Perform a consistent SQLite database backup,
    package translation.db and metadata.json into a ZIP archive,
    and return (zip_bytes, metadata).
    """
    # 1. Ensure DB exists and flush WAL
    if os.path.exists(TRANSLATION_DB):
        db = await get_db()
        try:
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        finally:
            await db.close()
    else:
        await init_db()

    stats = await get_database_stats()
    now_iso = _now()
    metadata = {
        "version": "1.0.0",
        "exported_at": now_iso,
        "database_file": "translation.db",
        "stats": stats,
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))

        # Safely copy DB content using SQLite backup API for online consistency
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_f:
            tmp_path = tmp_f.name

        try:
            src_conn = sqlite3.connect(TRANSLATION_DB)
            dst_conn = sqlite3.connect(tmp_path)
            with dst_conn:
                src_conn.backup(dst_conn)
            src_conn.close()
            dst_conn.close()

            zf.write(tmp_path, arcname="translation.db")
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    zip_bytes = zip_buffer.getvalue()
    return zip_bytes, metadata


async def restore_database_from_zip(zip_bytes: bytes) -> dict[str, Any]:
    """
    Validate and restore SQLite database from a provided ZIP archive.
    """
    if not zip_bytes:
        raise ValueError("ZIP archive is empty")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception as e:
        raise ValueError(f"Invalid ZIP archive: {e}") from e


    namelist = zf.namelist()

    # Look for database file in ZIP
    db_filename = None
    for candidate in ["translation.db", "database.db"]:
        if candidate in namelist:
            db_filename = candidate
            break
    if not db_filename:
        for name in namelist:
            if name.endswith(".db") or name.endswith(".sqlite") or name.endswith(".sqlite3"):
                db_filename = name
                break

    if not db_filename:
        raise ValueError("No valid SQLite database file found inside ZIP archive (expected translation.db or *.db)")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_f:
        tmp_path = tmp_f.name

    try:
        db_content = zf.read(db_filename)
        if len(db_content) < 100 or not db_content.startswith(b"SQLite format 3\000"):
            raise ValueError("Corrupted database file: Missing valid SQLite header")

        with open(tmp_path, "wb") as f:
            f.write(db_content)

        # Run integrity check using sqlite3
        conn = sqlite3.connect(tmp_path)
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            row = cursor.fetchone()
            if not row or row[0] != "ok":
                raise ValueError(f"SQLite integrity check failed: {row[0] if row else 'unknown error'}")
        finally:
            conn.close()

        # Database is valid. Prepare destination directory and replace TRANSLATION_DB
        db_dir = os.path.dirname(TRANSLATION_DB)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        # Remove existing WAL/SHM files to prevent state mismatch
        for ext in ["-wal", "-shm"]:
            wal_file = TRANSLATION_DB + ext
            if os.path.exists(wal_file):
                try:
                    os.remove(wal_file)
                except Exception:
                    pass

        # Overwrite destination database
        with open(TRANSLATION_DB, "wb") as f_out:
            f_out.write(db_content)

        # Run init_db to ensure WAL mode, foreign keys, and all expected schema/indexes are active
        await init_db()

        # Fetch stats of restored database
        stats = await get_database_stats()

        return {
            "ok": True,
            "message": "Database successfully restored from backup.",
            "stats": stats,
            "restored_at": _now(),
        }
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ─── Settings CRUD ────────────────────────────────────────────────────────────


async def get_all_settings() -> dict[str, Any]:
    """Retrieve all current system settings from DB and synced with runtime."""
    db = await get_db()
    try:
        cur = await db.execute("SELECT key, value FROM app_settings")
        rows = await cur.fetchall()
        settings = {}
        for r in rows:
            k = r["key"]
            val_str = r["value"]
            if k in ("job_cooldown_seconds", "context_refresh_jobs", "worker_concurrency", "translation_job_timeout", "translation_max_text_length"):
                try:
                    settings[k] = int(val_str)
                except (ValueError, TypeError):
                    settings[k] = val_str
            elif k in ("worker_poll_interval",):
                try:
                    settings[k] = float(val_str)
                except (ValueError, TypeError):
                    settings[k] = val_str
            else:
                settings[k] = val_str
        return settings
    finally:
        await db.close()


async def get_setting(key: str, default: Any = None) -> Any:
    """Get a single setting value from DB."""
    db = await get_db()
    try:
        cur = await db.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        if not row:
            return default
        val_str = row["value"]
        if key in ("job_cooldown_seconds", "context_refresh_jobs", "worker_concurrency", "translation_job_timeout", "translation_max_text_length"):
            try:
                return int(val_str)
            except (ValueError, TypeError):
                return val_str
        elif key in ("worker_poll_interval",):
            try:
                return float(val_str)
            except (ValueError, TypeError):
                return val_str
        return val_str
    finally:
        await db.close()


async def update_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Update system settings in DB and immediately sync into runtime config."""
    db = await get_db()
    try:
        now_str = _now()
        for k, v in updates.items():
            str_val = str(v)
            await db.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (k, str_val, now_str),
            )
            # Update runtime config
            if k in ("job_cooldown_seconds", "context_refresh_jobs", "worker_concurrency", "translation_job_timeout", "translation_max_text_length"):
                try:
                    set_runtime_setting(k, int(v))
                except (ValueError, TypeError):
                    set_runtime_setting(k, v)
            elif k in ("worker_poll_interval",):
                try:
                    set_runtime_setting(k, float(v))
                except (ValueError, TypeError):
                    set_runtime_setting(k, v)
            else:
                set_runtime_setting(k, v)

        await db.commit()
        return await get_all_settings()
    finally:
        await db.close()


async def reset_settings() -> dict[str, Any]:
    """Reset all settings back to factory defaults."""
    defaults = {
        "job_cooldown_seconds": DEFAULT_JOB_COOLDOWN_SECONDS,
        "context_refresh_jobs": DEFAULT_CONTEXT_REFRESH_JOBS,
        "worker_poll_interval": WORKER_POLL_INTERVAL,
        "worker_concurrency": WORKER_CONCURRENCY,
        "translation_job_timeout": TRANSLATION_JOB_TIMEOUT,
        "translation_max_text_length": TRANSLATION_MAX_TEXT_LENGTH,
    }
    return await update_settings(defaults)


