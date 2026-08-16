"""
Background worker for Hermes Novel Translation System.

Polls translation_jobs for pending work, calls the LLM via internal gateway,
parses the structured translation output, persists continuity characters + glossary,
and logs transitions matching PRD specifications.
"""

import asyncio
import time
from typing import Any

from ..api.gateway_client import gw_chat_stream, gw_status
from ..core.config import (
    TRANSLATION_JOB_TIMEOUT,
    WORKER_POLL_INTERVAL,
    get_runtime_setting,
)
from ..core.logger import (
    log_job_retry,
    log_job_transition,
    log_worker,
)
from .database import (
    check_and_release_cooldowns,
    claim_next_pending_job,
    complete_job,
    fail_job,
    increment_retry,
    requeue_job_to_pending,
    upsert_characters,
    upsert_glossary,
)
from .image_processor import extract_images, restore_images
from .prompt import (
    build_translation_messages,
    delimiters_to_markdown,
    parse_llm_response,
)
from .signals import job_notify

MAX_LLM_API_RETRIES = 1
MAX_SQLITE_RETRIES = 3
LLM_BACKOFF_SCHEDULE = [2, 5, 10]  # seconds


class RateLimitException(RuntimeError):
    """Raised when an account hits ChatGPT usage limit and needs cooldown."""

    pass


async def _call_llm(messages: list[dict[str, str]], model: str) -> str:
    """
    Call the LLM through direct gateway async stream generator.
    Composes a prompt from messages and collects the full response.
    """
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        parts.append(f"[{role}] {content}")
    prompt = "\n".join(parts)

    body = {"prompt": prompt, "model": model, "reset": True}

    full_text = ""
    error = None

    async for ev in gw_chat_stream(body):
        if "error" in ev:
            err = ev["error"]
            if ev.get("rate_limited") or "rate_limit" in str(err).lower() or err == "no_available_workers":
                raise RateLimitException(f"Worker rate limit / cooldown: {err}")
            error = err
            break
        if ev.get("done"):
            full_text = ev.get("text", full_text)
        elif ev.get("text"):
            full_text = ev["text"]
        elif ev.get("delta"):
            full_text += ev["delta"]

    if error:
        raise RuntimeError(f"LLM gateway error: {error}")

    if not full_text.strip():
        raise RuntimeError("LLM returned empty response")

    return full_text


async def process_job(job: dict[str, Any]):
    """
    Process a single claimed translation job.
    """
    job_id = job["job_id"]
    novel_id = job["novel_id"]
    chapter_number = job["chapter_number"]
    source_text = job["source_text_cleaned"]
    source_lang = job["source_lang"]
    target_lang = job["target_lang"]
    model = job.get("model", "gpt-5.6-luna")
    existing_chars = job.get("characters", [])
    retry_count = job.get("retry_count", 0)

    # Log pending -> running transition
    log_job_transition(
        job_id=job_id,
        novel_id=novel_id,
        chapter_number=chapter_number,
        old_status="pending",
        new_status="running",
    )

    t_start = time.time()

    try:
        # 1. Extract images to avoid messing with LLM prompt
        cleaned_source_text, extracted_images = extract_images(
            source_text,
            novel_id=novel_id,
            chapter_number=chapter_number,
        )

        # 2. Build prompt messages
        messages = build_translation_messages(
            source_text=cleaned_source_text,
            existing_characters=existing_chars,
            source_lang=source_lang,
            target_lang=target_lang,
        )

        # 3. Call LLM with retry
        raw_response = None
        last_error_code = "LLM_API_ERROR"
        last_error_msg = ""

        for attempt in range(MAX_LLM_API_RETRIES):
            try:
                raw_response = await asyncio.wait_for(
                    _call_llm(messages, model),
                    timeout=TRANSLATION_JOB_TIMEOUT,
                )
                break
            except RateLimitException as rle:
                log_worker(f"Job {job_id} encountered rate limit / cooldown ({rle}). Re-queuing to pending.")
                await requeue_job_to_pending(job_id)
                log_job_transition(
                    job_id=job_id,
                    novel_id=novel_id,
                    chapter_number=chapter_number,
                    old_status="running",
                    new_status="pending",
                )
                return
            except TimeoutError:
                last_error_code = "LLM_TIMEOUT"
                last_error_msg = "Model did not respond within timeout limit."
                retry_count += 1
                await increment_retry(job_id)
                log_job_retry(job_id, novel_id, chapter_number, retry_count)
                if attempt < MAX_LLM_API_RETRIES - 1:
                    await asyncio.sleep(LLM_BACKOFF_SCHEDULE[min(attempt, len(LLM_BACKOFF_SCHEDULE) - 1)])
            except RuntimeError as e:
                err_str = str(e)
                if "context" in err_str.lower() or "length" in err_str.lower():
                    last_error_code = "LLM_CONTEXT_OVERFLOW"
                else:
                    last_error_code = "LLM_API_ERROR"
                last_error_msg = err_str
                retry_count += 1
                await increment_retry(job_id)
                log_job_retry(job_id, novel_id, chapter_number, retry_count)
                if attempt < MAX_LLM_API_RETRIES - 1:
                    await asyncio.sleep(LLM_BACKOFF_SCHEDULE[min(attempt, len(LLM_BACKOFF_SCHEDULE) - 1)])

        if raw_response is None:
            duration = time.time() - t_start
            await fail_job(
                job_id=job_id,
                error_code=last_error_code,
                error_message=last_error_msg,
                retry_count=retry_count,
            )
            log_job_transition(
                job_id=job_id,
                novel_id=novel_id,
                chapter_number=chapter_number,
                old_status="running",
                new_status="failed",
                duration=duration,
                error_code=last_error_code,
            )
            return

        # 4. Parse structured JSON output
        parsed = None
        parse_error = None
        cleaned_response = raw_response

        try:
            parsed = parse_llm_response(raw_response)
        except ValueError as e:
            parse_error = str(e)
            from .smart_cleaner import repair_json

            repaired = repair_json(raw_response)
            if repaired:
                try:
                    parsed = parse_llm_response(repaired)
                    cleaned_response = repaired
                    parse_error = None
                except ValueError:
                    pass

        if not parsed:
            duration = time.time() - t_start
            await fail_job(
                job_id=job_id,
                error_code="JSON_PARSE_ERROR",
                error_message=f"Failed to parse LLM structured response: {parse_error}",
                raw_response=raw_response,
                retry_count=retry_count,
            )
            log_job_transition(
                job_id=job_id,
                novel_id=novel_id,
                chapter_number=chapter_number,
                old_status="running",
                new_status="failed",
                duration=duration,
                error_code="JSON_PARSE_ERROR",
            )
            return

        # 5. Restore inline images into translated text
        translation_text = parsed.get("translation", "")
        summary_text = parsed.get("summary", "")

        # Convert delimiter tags to clean markdown
        translation_text = delimiters_to_markdown(translation_text)
        summary_text = delimiters_to_markdown(summary_text)

        if extracted_images:
            translation_text = restore_images(translation_text, extracted_images)

        # 6. Database writes (SQLite retry logic)
        for attempt in range(MAX_SQLITE_RETRIES):
            try:
                # Upsert characters
                if parsed.get("characters"):
                    await upsert_characters(
                        novel_id=novel_id,
                        chapter_number=chapter_number,
                        characters=parsed["characters"],
                    )

                # Upsert glossary
                if parsed.get("glossary"):
                    await upsert_glossary(
                        novel_id=novel_id,
                        chapter_number=chapter_number,
                        glossary=parsed["glossary"],
                    )

                # Complete translation job
                await complete_job(
                    job_id=job_id,
                    result_translation=translation_text,
                    result_summary=summary_text,
                    raw_response=raw_response,
                    cleaned_response=cleaned_response,
                )
                break
            except Exception as e:
                if attempt < MAX_SQLITE_RETRIES - 1:
                    await asyncio.sleep(0.5 * (2**attempt))
                else:
                    raise e

        duration = time.time() - t_start
        log_job_transition(
            job_id=job_id,
            novel_id=novel_id,
            chapter_number=chapter_number,
            old_status="running",
            new_status="done",
            duration=duration,
        )

    except RateLimitException as rle:
        log_worker(f"Job {job_id} encountered rate limit ({rle}). Re-queuing to pending.")
        await requeue_job_to_pending(job_id)
        log_job_transition(
            job_id=job_id,
            novel_id=novel_id,
            chapter_number=chapter_number,
            old_status="running",
            new_status="pending",
        )
    except Exception as e:
        duration = time.time() - t_start
        await fail_job(
            job_id=job_id,
            error_code="UNEXPECTED_ERROR",
            error_message=str(e),
            retry_count=retry_count,
        )
        log_job_transition(
            job_id=job_id,
            novel_id=novel_id,
            chapter_number=chapter_number,
            old_status="running",
            new_status="failed",
            duration=duration,
            error_code="UNEXPECTED_ERROR",
        )


async def worker_loop():
    """
    Background worker loop with multi-worker concurrency.
    Dispatches jobs across available account workers in parallel.
    Periodically checks and releases expired cooldowns.
    """
    is_idle = False
    log_worker(f"Background task started, polling interval: {WORKER_POLL_INTERVAL}s")
    last_cooldown_check = 0.0
    active_tasks: set[asyncio.Task] = set()

    while True:
        try:
            now = time.time()
            # Periodic cooldown release check (every 15s)
            if now - last_cooldown_check >= 15:
                released = await check_and_release_cooldowns()
                if released:
                    log_worker(f"Released {len(released)} accounts from cooldown back to ACTIVE: {released}")
                last_cooldown_check = now

            # Check gateway readiness
            status = gw_status()
            if not status.get("ok"):
                await asyncio.sleep(2)
                continue

            pool_info = status.get("pool", {})
            idle_workers = pool_info.get("idle_workers", 1)
            available_slots = idle_workers - len(active_tasks)

            if available_slots <= 0:
                # All workers currently busy or active tasks already occupy them, wait briefly
                await asyncio.sleep(0.5)
                continue

            # Atomically claim next job
            job = await claim_next_pending_job()

            poll_interval = float(get_runtime_setting("worker_poll_interval", WORKER_POLL_INTERVAL))

            if not job:
                if not is_idle and not active_tasks:
                    log_worker("No pending jobs — idle")
                    is_idle = True

                # Wait for notify signal or timeout
                try:
                    await asyncio.wait_for(job_notify.wait(), timeout=poll_interval)
                except TimeoutError:
                    pass
                job_notify.clear()
                continue

            # Found job
            if is_idle:
                log_worker("Job found — resuming")
                is_idle = False

            t = asyncio.create_task(process_job(job))
            active_tasks.add(t)
            t.add_done_callback(active_tasks.discard)

            await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            log_worker("Background worker task cancelled")
            for t in list(active_tasks):
                t.cancel()
            break
        except Exception as e:
            log_worker(f"Worker loop error: {e}", level="ERROR")
            err_poll = float(get_runtime_setting("worker_poll_interval", WORKER_POLL_INTERVAL))
            await asyncio.sleep(err_poll)

