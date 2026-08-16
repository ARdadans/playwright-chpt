"""
FastAPI routes for Translation, Novels, Characters, Glossary, and Worker endpoints.
Implements all routes specified in PRD (prd-endpoint.md).
"""

import csv
import io
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from ..core.config import (
    TRANSLATION_MAX_TEXT_LENGTH,
    WORKER_KEY,
)
from .database import (
    archive_job,
    backup_database_to_zip,
    bulk_upsert_glossary,
    cancel_all_jobs,
    cancel_job,
    claim_next_pending_job,
    complete_job,
    create_character,
    create_glossary,
    create_job,
    delete_character,
    delete_glossary,
    delete_job_by_id,
    export_glossary_terms,
    fail_job,
    find_existing_job,
    get_character,
    get_context,
    get_database_stats,
    get_glossary,
    get_job,
    get_job_history,
    get_novel_history,
    get_novel_stats,
    get_running_jobs,
    list_chapters,
    list_characters,
    list_glossary,
    list_jobs,
    list_novels,
    reset_job_to_pending,
    restore_database_from_zip,
    restore_history,
    retry_job,
    update_character,
    update_glossary,
)
from .prompt import (
    SUPPORTED_SOURCE_LANGS,
    SUPPORTED_TARGET_LANGS,
    delimiters_to_markdown,
)
from .signals import job_notify

router = APIRouter()


def filter_fields(data: Any, fields: str | list[str] | set[str] | None) -> Any:
    """Filter fields in a dictionary or list of dictionaries based on comma-separated or list of field names.
    Supports top-level keys and nested dot notation (e.g. 'result.translation' or 'job.chapter_number').
    """
    if not fields or not isinstance(data, (dict, list)):
        return data

    if isinstance(fields, str):
        field_list = [f.strip() for f in fields.split(",") if f.strip()]
    else:
        field_list = [str(f).strip() for f in fields if str(f).strip()]

    if not field_list:
        return data

    if isinstance(data, list):
        return [filter_fields(item, field_list) for item in data]

    # For dict:
    result = {}
    top_level_fields = set()
    nested_fields: dict[str, list[str]] = {}

    for f in field_list:
        if "." in f:
            parent, child = f.split(".", 1)
            nested_fields.setdefault(parent, []).append(child)
        else:
            top_level_fields.add(f)

    for k, v in data.items():
        if k in top_level_fields:
            if k in nested_fields and isinstance(v, dict):
                result[k] = filter_fields(v, nested_fields[k])
            else:
                result[k] = v
        elif k in nested_fields and isinstance(v, dict):
            result[k] = filter_fields(v, nested_fields[k])
        elif k in nested_fields and isinstance(v, list):
            result[k] = [filter_fields(sub_item, nested_fields[k]) for sub_item in v]

    return result



def _verify_worker_access(x_worker_key: str | None = None):
    """Worker endpoints security check."""
    if WORKER_KEY and x_worker_key != WORKER_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: invalid worker key")


def _format_job_item(job: dict[str, Any]) -> dict[str, Any]:
    """Helper to format a job row into standard API JSON response."""
    raw_trans = job.get("result_translation")
    translate_md = delimiters_to_markdown(raw_trans) if raw_trans else None

    result = None
    if job.get("status") == "done":
        result = {
            "translation": raw_trans,
            "translate_md": translate_md,
            "chapter_summary": job.get("result_summary"),
        }

    error = None
    if job.get("status") == "failed":
        error = {
            "code": job.get("error_code"),
            "message": job.get("error_message"),
            "retry_count": job.get("retry_count", 0),
        }

    return {
        "job_id": job.get("id"),
        "novel_id": job.get("novel_id"),
        "chapter_number": job.get("chapter_number"),
        "status": job.get("status"),
        "source_lang": job.get("source_lang"),
        "target_lang": job.get("target_lang"),
        "model": job.get("model"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "result": result,
        "raw_response": job.get("raw_response"),
        "cleaned_response": job.get("cleaned_response"),
        "error": error,
    }


# ─── 5.4 Translation Jobs ─────────────────────────────────────────────────────


@router.post("/translate", status_code=202)
async def create_translation(request: Request):
    """Submit a new translation job to the queue."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "invalid_json", "message": "Invalid JSON body"},
            status_code=400,
        )

    model = body.get("model")
    source_lang = body.get("source_lang")
    target_lang = body.get("target_lang")
    novel_id = body.get("novel_id")
    chapter_number = body.get("chapter_number")
    text = body.get("text")
    force = bool(body.get("force", False))

    if not model or not str(model).strip():
        return JSONResponse(
            {"error": "model_required", "message": "'model' is required"},
            status_code=400,
        )

    if not source_lang or source_lang not in SUPPORTED_SOURCE_LANGS:
        return JSONResponse(
            {
                "error": "unsupported_source_lang",
                "message": f"'source_lang' must be one of {sorted(SUPPORTED_SOURCE_LANGS)}",
            },
            status_code=400,
        )

    if not target_lang or target_lang not in SUPPORTED_TARGET_LANGS:
        return JSONResponse(
            {
                "error": "unsupported_target_lang",
                "message": f"'target_lang' must be one of {sorted(SUPPORTED_TARGET_LANGS)}",
            },
            status_code=400,
        )

    if not novel_id or not str(novel_id).strip():
        return JSONResponse(
            {"error": "novel_id_required", "message": "'novel_id' is required"},
            status_code=400,
        )

    if chapter_number is None or not isinstance(chapter_number, (int, float)):
        return JSONResponse(
            {"error": "invalid_chapter_number", "message": "'chapter_number' must be a number"},
            status_code=400,
        )

    if not text or not str(text).strip():
        return JSONResponse(
            {"error": "text_empty", "message": "'text' cannot be empty"},
            status_code=400,
        )

    text_cleaned = str(text).strip()
    if len(text_cleaned) > TRANSLATION_MAX_TEXT_LENGTH:
        return JSONResponse(
            {
                "error": "LLM_CONTEXT_OVERFLOW",
                "message": f"Text exceeds maximum limit of {TRANSLATION_MAX_TEXT_LENGTH} chars.",
            },
            status_code=400,
        )

    ch_num = float(chapter_number)
    nov_id = str(novel_id).strip()

    # Check existing job
    existing = await find_existing_job(nov_id, ch_num)
    if existing:
        if not force:
            return JSONResponse(
                {
                    "error": "chapter_already_translated",
                    "hint": "use force:true to re-translate",
                    "job_id": existing["id"],
                },
                status_code=409,
            )

        # Force re-translate: archive old result and reset job to pending
        await archive_job(existing["id"])
        job = await reset_job_to_pending(
            job_id=existing["id"],
            source_text_raw=text_cleaned,
            source_text_cleaned=text_cleaned,
            source_lang=source_lang,
            target_lang=target_lang,
            model=str(model).strip(),
        )
    else:
        # Create new job
        job = await create_job(
            novel_id=nov_id,
            chapter_number=ch_num,
            source_lang=source_lang,
            target_lang=target_lang,
            source_text_raw=text_cleaned,
            source_text_cleaned=text_cleaned,
            model=str(model).strip(),
        )

    # Wake worker
    job_notify.set()

    return JSONResponse(
        {
            "id": job["id"],
            "novel_id": job["novel_id"],
            "chapter_number": job["chapter_number"],
            "status": "pending",
            "source_lang": job["source_lang"],
            "target_lang": job["target_lang"],
            "model": job["model"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        },
        status_code=202,
    )



@router.get("/translate")
async def get_translations(
    novel_id: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort: str = "created_at:desc",
    fields: str | None = None,
):
    """List all translation jobs with filter, sorting, and pagination."""
    res = await list_jobs(novel_id=novel_id, status=status, page=page, limit=limit, sort=sort)
    items = [_format_job_item(item) for item in res["items"]]
    if fields:
        items = filter_fields(items, fields)
    res["items"] = items
    return res


@router.get("/translate/{job_id}")
async def get_translation(job_id: str, fields: str | None = None):
    """Polling status and translation result."""
    job = await get_job(job_id)
    if not job:
        return JSONResponse(
            {"error": "job_not_found", "message": f"Job '{job_id}' not found."},
            status_code=404,
        )
    formatted = _format_job_item(job)
    if fields:
        return filter_fields(formatted, fields)
    return formatted


@router.post("/translate/{job_id}/retry")
async def retry_translation(job_id: str):
    """Retry manual for failed jobs."""
    success, current_status = await retry_job(job_id)
    if not success:
        if current_status is None:
            return JSONResponse(
                {"error": "job_not_found", "message": f"Job '{job_id}' not found."},
                status_code=404,
            )
        return JSONResponse(
            {"error": "job_not_retryable", "current_status": current_status},
            status_code=400,
        )

    job_notify.set()
    return {"status": "pending", "job_id": job_id}


@router.post("/translate/{job_id}/cancel")
async def cancel_translation(job_id: str):
    """Cancel a pending or running job."""
    success, current_status = await cancel_job(job_id)
    if not success:
        if current_status is None:
            return JSONResponse(
                {"error": "job_not_found", "message": f"Job '{job_id}' not found."},
                status_code=404,
            )
        return JSONResponse(
            {"error": "job_not_cancellable", "current_status": current_status},
            status_code=400,
        )
    return {"status": "cancelled", "job_id": job_id}


@router.post("/translate/cancel-all")
async def cancel_all_translations(
    request: Request,
    status: str = Query("all", description="Target status to cancel: 'pending', 'running', or 'all'/'both'"),
    novel_id: str | None = Query(None, description="Optional filter by novel_id"),
):
    """
    Cancel all jobs that are pending and/or running.
    Allows specifying status filter: 'pending', 'running', 'all', or 'both'.
    Accepts options via query params or JSON body.
    """
    body_data: dict[str, Any] = {}
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body_data = await request.json()
        except Exception:
            body_data = {}

    target_status = body_data.get("status", status) if isinstance(body_data, dict) else status
    target_novel_id = body_data.get("novel_id", novel_id) if isinstance(body_data, dict) else novel_id

    if not isinstance(target_status, str):
        return JSONResponse(
            {
                "error": "invalid_status_filter",
                "message": "status must be a string: 'pending', 'running', 'all', or 'both'.",
            },
            status_code=400,
        )

    norm_status = target_status.strip().lower()
    if norm_status not in ("pending", "running", "all", "both"):
        return JSONResponse(
            {
                "error": "invalid_status_filter",
                "message": f"Invalid status '{target_status}'. Must be one of: 'pending', 'running', 'all', 'both'.",
            },
            status_code=400,
        )

    result = await cancel_all_jobs(status_filter=norm_status, novel_id=target_novel_id)
    return result


@router.delete("/translate/{job_id}")
async def delete_translation(job_id: str):
    """Delete a pending or cancelled job."""
    success, reason = await delete_job_by_id(job_id)
    if not success:
        if reason == "not_found":
            return JSONResponse(
                {"error": "job_not_found", "message": f"Job '{job_id}' not found."},
                status_code=404,
            )
        return JSONResponse(
            {"error": "job_not_deletable", "hint": "cancel the job first"},
            status_code=400,
        )
    return {"status": "deleted", "job_id": job_id}


@router.get("/translate/{job_id}/history")
async def get_job_translation_history(job_id: str, fields: str | None = None):
    """Get history of translation versions for a job."""
    history = await get_job_history(job_id)
    if fields:
        history = filter_fields(history, fields)
    return {"job_id": job_id, "history": history}


# ─── 5.5 History ──────────────────────────────────────────────────────────────


@router.post("/history/{history_id}/restore")
async def restore_translation_history(history_id: int):
    """Restore an archived translation version to the active job."""
    res = await restore_history(history_id)
    if not res:
        return JSONResponse(
            {"error": "history_not_found", "message": f"History ID {history_id} not found."},
            status_code=404,
        )
    return res


# ─── 5.6 Novels ───────────────────────────────────────────────────────────────


@router.get("/novels")
async def get_novels(fields: str | None = None):
    """List all novels aggregated from jobs."""
    novels = await list_novels()
    if fields:
        novels = filter_fields(novels, fields)
    return {"novels": novels}


@router.get("/novels/{novel_id}/stats")
async def get_single_novel_stats(novel_id: str, fields: str | None = None):
    """Get complete statistics for a novel."""
    stats = await get_novel_stats(novel_id)
    if not stats:
        return JSONResponse(
            {"error": "novel_not_found", "message": f"Novel '{novel_id}' not found."},
            status_code=404,
        )
    if fields:
        return filter_fields(stats, fields)
    return stats


@router.get("/novels/{novel_id}/chapters")
async def get_novel_chapters(
    novel_id: str,
    status: str | None = None,
    sort: str = "chapter_number:asc",
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    fields: str | None = None,
):
    """List all chapters belonging to a novel with status filter, sorting, pagination, and fields filtering."""
    res = await list_chapters(novel_id=novel_id, status=status, page=page, limit=limit, sort=sort)
    if fields:
        res["chapters"] = filter_fields(res["chapters"], fields)
    return res


@router.get("/novels/{novel_id}/jobs")
async def get_novel_jobs(
    novel_id: str,
    status: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort: str = "created_at:desc",
    fields: str | None = None,
):
    """List jobs belonging to a novel."""
    res = await list_jobs(novel_id=novel_id, status=status, page=page, limit=limit, sort=sort)
    items = [_format_job_item(item) for item in res["items"]]
    if fields:
        items = filter_fields(items, fields)
    res["items"] = items
    return res


@router.get("/novels/{novel_id}/jobs/{chapter_number}")
async def get_novel_chapter_job(novel_id: str, chapter_number: float, fields: str | None = None):
    """Get specific chapter job along with character and glossary context."""
    job = await find_existing_job(novel_id, chapter_number)
    if not job:
        return JSONResponse(
            {"error": "job_not_found", "message": f"Chapter {chapter_number} not found for novel '{novel_id}'."},
            status_code=404,
        )

    formatted_job = _format_job_item(job)
    context = await get_context(novel_id)

    data = {
        "job": formatted_job,
        "characters": context["characters"],
        "glossary": context["glossary"],
    }
    if fields:
        return filter_fields(data, fields)
    return data


@router.get("/novels/{novel_id}/context")
async def get_novel_full_context(novel_id: str, fields: str | None = None):
    """All characters and glossary context for a novel."""
    context = await get_context(novel_id)
    data = {
        "novel_id": novel_id,
        "characters": context["characters"],
        "glossary": context["glossary"],
    }
    if fields:
        return filter_fields(data, fields)
    return data


@router.get("/novels/{novel_id}/history")
async def get_novel_all_history(
    novel_id: str,
    chapter_number: float | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    fields: str | None = None,
):
    """Get translation history for a novel."""
    res = await get_novel_history(novel_id, chapter_number=chapter_number, page=page, limit=limit)
    if fields:
        res["history"] = filter_fields(res["history"], fields)
    return res


@router.get("/novels/{novel_id}/history/{chapter_number}")
async def get_novel_chapter_history(
    novel_id: str,
    chapter_number: float,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    fields: str | None = None,
):
    """Get history for a specific chapter."""
    res = await get_novel_history(novel_id, chapter_number=chapter_number, page=page, limit=limit)
    if fields:
        res["history"] = filter_fields(res["history"], fields)
    return res


# ─── 5.7 Characters ───────────────────────────────────────────────────────────


@router.get("/novels/{novel_id}/characters")
async def get_characters(
    novel_id: str,
    q: str | None = None,
    gender: str | None = None,
    chapter_from: float | None = None,
    chapter_to: float | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    fields: str | None = None,
):
    """List characters with filters and pagination."""
    res = await list_characters(
        novel_id=novel_id,
        q=q,
        gender=gender,
        chapter_from=chapter_from,
        chapter_to=chapter_to,
        page=page,
        limit=limit,
    )
    if fields:
        res["items"] = filter_fields(res["items"], fields)
    return res


@router.post("/novels/{novel_id}/characters", status_code=201)
async def add_character(novel_id: str, request: Request):
    """Create a new character."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json", "message": "Invalid JSON body"}, status_code=400)

    name = body.get("name")
    if not name or not str(name).strip():
        return JSONResponse({"error": "name_required", "message": "'name' is required"}, status_code=400)

    appeared_chapters = body.get("appeared_chapters")
    if appeared_chapters is not None:
        try:
            appeared_chapters = [float(x) for x in appeared_chapters]
        except (ValueError, TypeError):
            appeared_chapters = None

    char, existing_id = await create_character(
        novel_id=novel_id,
        name=str(name).strip(),
        native_name=body.get("native_name", str(name).strip()),
        gender=body.get("gender", "unknown"),
        notes=body.get("notes", ""),
        first_seen_chapter=float(body.get("first_seen_chapter", 1.0)),
        appeared_chapters=appeared_chapters,
    )

    if not char:
        return JSONResponse(
            {"error": "character_already_exists", "id": existing_id},
            status_code=409,
        )

    return char


@router.get("/novels/{novel_id}/characters/{character_id}")
async def get_single_character(novel_id: str, character_id: int, fields: str | None = None):
    """Detail one character."""
    char = await get_character(novel_id, character_id)
    if not char:
        return JSONResponse({"error": "character_not_found", "message": "Character not found"}, status_code=404)
    if fields:
        return filter_fields(char, fields)
    return char


@router.put("/novels/{novel_id}/characters/{character_id}")
async def update_single_character(novel_id: str, character_id: int, request: Request):
    """Update a character."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json", "message": "Invalid JSON body"}, status_code=400)

    appeared_chapters = body.get("appeared_chapters")
    if appeared_chapters is not None:
        try:
            appeared_chapters = [float(x) for x in appeared_chapters]
        except (ValueError, TypeError):
            appeared_chapters = None

    updated = await update_character(
        novel_id=novel_id,
        character_id=character_id,
        name=body.get("name"),
        native_name=body.get("native_name"),
        gender=body.get("gender"),
        notes=body.get("notes"),
        last_updated_chapter=float(body["last_updated_chapter"]) if "last_updated_chapter" in body else None,
        appeared_chapters=appeared_chapters,
    )

    if not updated:
        return JSONResponse({"error": "character_not_found", "message": "Character not found"}, status_code=404)
    return updated


@router.delete("/novels/{novel_id}/characters/{character_id}")
async def delete_single_character(novel_id: str, character_id: int):
    """Delete a character."""
    deleted = await delete_character(novel_id, character_id)
    if not deleted:
        return JSONResponse({"error": "character_not_found", "message": "Character not found"}, status_code=404)
    return {"ok": True, "deleted_id": character_id}


# ─── 5.8 Glossary ─────────────────────────────────────────────────────────────


@router.get("/novels/{novel_id}/glossary")
async def get_glossary_list(
    novel_id: str,
    q: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    fields: str | None = None,
):
    """List glossary terms."""
    res = await list_glossary(novel_id=novel_id, q=q, page=page, limit=limit)
    if fields:
        res["items"] = filter_fields(res["items"], fields)
    return res


@router.post("/novels/{novel_id}/glossary", status_code=201)
async def add_glossary_term(novel_id: str, request: Request):
    """Add a glossary term."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json", "message": "Invalid JSON body"}, status_code=400)

    term_source = body.get("term_source")
    term_trans = body.get("term_translation")

    if not term_source or not str(term_source).strip():
        return JSONResponse({"error": "term_source_required", "message": "'term_source' is required"}, status_code=400)
    if not term_trans or not str(term_trans).strip():
        return JSONResponse({"error": "term_translation_required", "message": "'term_translation' is required"}, status_code=400)

    term, existing_id = await create_glossary(
        novel_id=novel_id,
        term_source=str(term_source).strip(),
        term_translation=str(term_trans).strip(),
        notes=body.get("notes", ""),
        first_seen_chapter=float(body.get("first_seen_chapter", 1.0)),
    )

    if not term:
        return JSONResponse(
            {"error": "term_already_exists", "id": existing_id},
            status_code=409,
        )

    return term


@router.post("/novels/{novel_id}/glossary/bulk")
async def bulk_import_glossary(novel_id: str, request: Request):
    """Bulk import glossary terms with upsert."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json", "message": "Invalid JSON body"}, status_code=400)

    terms = body.get("terms", [])
    ch = float(body.get("first_seen_chapter", 1.0))

    result = await bulk_upsert_glossary(novel_id=novel_id, terms=terms, first_seen_chapter=ch)
    return result


@router.get("/novels/{novel_id}/glossary/export")
async def export_glossary(novel_id: str, format: str = Query("json", pattern="^(json|csv)$")):
    """Export glossary in JSON or CSV format."""
    terms = await export_glossary_terms(novel_id)

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["term_source", "term_translation", "notes", "first_seen_chapter", "last_updated_chapter"])
        for t in terms:
            writer.writerow([
                t.get("term_source", ""),
                t.get("term_translation", ""),
                t.get("notes", ""),
                t.get("first_seen_chapter", 1.0),
                t.get("last_updated_chapter", 1.0),
            ])
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{novel_id}_glossary.csv"'},
        )

    return {
        "novel_id": novel_id,
        "exported_at": datetime.now(UTC).isoformat(),
        "terms": terms,
    }


@router.get("/novels/{novel_id}/glossary/{glossary_id}")
async def get_single_glossary(novel_id: str, glossary_id: int, fields: str | None = None):
    """Detail one glossary term."""
    term = await get_glossary(novel_id, glossary_id)
    if not term:
        return JSONResponse({"error": "term_not_found", "message": "Term not found"}, status_code=404)
    if fields:
        return filter_fields(term, fields)
    return term



@router.put("/novels/{novel_id}/glossary/{glossary_id}")
async def update_single_glossary(novel_id: str, glossary_id: int, request: Request):
    """Update a glossary term."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json", "message": "Invalid JSON body"}, status_code=400)

    updated = await update_glossary(
        novel_id=novel_id,
        glossary_id=glossary_id,
        term_source=body.get("term_source"),
        term_translation=body.get("term_translation"),
        notes=body.get("notes"),
        last_updated_chapter=float(body["last_updated_chapter"]) if "last_updated_chapter" in body else None,
    )

    if not updated:
        return JSONResponse({"error": "term_not_found", "message": "Term not found"}, status_code=404)
    return updated


@router.delete("/novels/{novel_id}/glossary/{glossary_id}")
async def delete_single_glossary(novel_id: str, glossary_id: int):
    """Delete a glossary term."""
    deleted = await delete_glossary(novel_id, glossary_id)
    if not deleted:
        return JSONResponse({"error": "term_not_found", "message": "Term not found"}, status_code=404)
    return {"ok": True, "deleted_id": glossary_id}



# ─── 5.9 Worker Endpoints (Internal) ──────────────────────────────────────────


@router.get("/worker/jobs/next")
async def worker_get_next_job(x_worker_key: str | None = Header(None)):
    """Atomically claim the next pending job."""
    _verify_worker_access(x_worker_key)
    job = await claim_next_pending_job()
    if not job:
        return Response(status_code=204)
    return job


@router.patch("/worker/jobs/{job_id}/status")
async def worker_update_job_status(
    job_id: str,
    request: Request,
    x_worker_key: str | None = Header(None),
):
    """Update job status from worker (done or failed)."""
    _verify_worker_access(x_worker_key)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    status = body.get("status")
    if status == "done":
        await complete_job(
            job_id=job_id,
            result_translation=body.get("result_translation", ""),
            result_summary=body.get("result_summary", ""),
            raw_response=body.get("raw_response", ""),
            cleaned_response=body.get("cleaned_response", ""),
        )
        return {"ok": True}
    if status == "failed":
        await fail_job(
            job_id=job_id,
            error_code=body.get("error_code", "WORKER_ERROR"),
            error_message=body.get("error_message", "Worker reported failure"),
            retry_count=int(body.get("retry_count", 0)),
            raw_response=body.get("raw_response"),
            cleaned_response=body.get("cleaned_response"),
        )
        return {"ok": True}

    return JSONResponse({"error": "invalid_status"}, status_code=400)


@router.get("/worker/jobs/running")
async def worker_get_running_jobs(x_worker_key: str | None = Header(None)):
    """List jobs currently in 'running' status for dead-job detection."""
    _verify_worker_access(x_worker_key)
    running = await get_running_jobs()
    return {"running": running}


# ─── 5.10 Database Backup & Restore Endpoints ─────────────────────────────────


@router.get("/database/stats")
async def database_stats():
    """Return row counts and summary statistics across all database tables."""
    stats = await get_database_stats()
    return {"ok": True, "stats": stats}


@router.get("/database/backup")
@router.post("/database/backup")
async def database_backup():
    """
    Download a ZIP archive containing a consistent SQLite database backup and metadata.json.
    """
    try:
        zip_bytes, metadata = await backup_database_to_zip()
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"hermes_backup_{ts}.zip"
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Hermes-Backup-Date": metadata.get("exported_at", ""),
            },
        )
    except Exception as e:
        return JSONResponse(
            {"error": "backup_failed", "message": str(e)},
            status_code=500,
        )


@router.post("/database/restore")
async def database_restore(
    request: Request,
    file: UploadFile | None = File(None),  # noqa: B008
):
    """
    Restore SQLite database from an uploaded ZIP archive containing translation.db.
    Supports multipart/form-data ('file' field) or raw binary ZIP payload in request body.
    """
    try:
        zip_bytes = b""
        if file is not None:
            zip_bytes = await file.read()
        else:
            zip_bytes = await request.body()

        if not zip_bytes:
            return JSONResponse(
                {"error": "missing_file", "message": "No ZIP file provided in upload or request body."},
                status_code=400,
            )

        result = await restore_database_from_zip(zip_bytes)
        return JSONResponse(result, status_code=200)
    except ValueError as ve:
        return JSONResponse(
            {"error": "invalid_backup_zip", "message": str(ve)},
            status_code=400,
        )
    except Exception as e:
        return JSONResponse(
            {"error": "restore_failed", "message": str(e)},
            status_code=500,
        )

