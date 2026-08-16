import argparse
import json
import re
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import core.config as config
from core.config import set_config
from interactive import run_interactive_menu
from services.api import (
    add_cookie,
    backup_database,
    bulk_import_glossary,
    cancel_all_jobs,
    cancel_job,
    chat_completion,
    check_health,
    create_character,
    create_glossary,
    delete_character,
    delete_cookie,
    delete_glossary,
    delete_job,
    export_glossary,
    get_character,
    get_database_stats,
    get_glossary,
    get_job,
    get_job_history,
    get_models,
    get_novel_chapters,
    get_novel_history,
    get_novel_stats,
    get_settings,
    list_characters,
    list_cookies,
    list_glossary,
    list_jobs,
    list_novels,
    pause_cookie,
    refresh_cookie,
    reset_cookie_cooldown,
    reset_settings,
    restore_database,
    restore_history,
    resume_cookie,
    retry_job,
    submit_translation_job,
    update_character,
    update_glossary,
    update_settings,
    wait_for_job,
)


def _print_header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ─── 1. Status & Health ───────────────────────────────────────────────────────


def cmd_status(args):
    """Display Hermes server health, worker pool cooldowns, and active settings."""
    _print_header("HERMES SERVER STATUS")
    print(f"Target Server: {config.get_base_url()}\n")

    health = check_health()
    if not health or not health.get("ok"):
        print("[-] Server status: UNREACHABLE / UNHEALTHY")
        return

    print("[+] Server status: ONLINE (OK)")
    gateway = health.get("gateway", {})
    if gateway:
        print(f"    - Title: {gateway.get('title', 'N/A')}")
        print(f"    - Turns processed: {gateway.get('turns', 0)}")
        print(f"    - Idle time: {gateway.get('idle_s', 0)}s")

    # Worker Pool & Cooldowns
    cookie_info = list_cookies()
    if cookie_info and "pool" in cookie_info:
        pool = cookie_info["pool"]
        print("\n--- WORKER POOL & CONTEXT COOLDOWNS ---")
        print(f"Total Workers        : {pool.get('total_workers', 0)}")
        print(f"Idle (Ready) Workers : {pool.get('idle_workers', 0)}")
        print(f"Busy Workers         : {pool.get('busy_workers', 0)}")
        print(f"Cooling Down Workers : {pool.get('cooling_down_workers', 0)}")

        workers = pool.get("workers", [])
        if workers:
            print("\nActive Context Accounts:")
            for w in workers:
                cd_str = f"COOLDOWN ({w.get('cooldown_remaining_s')}s left)" if w.get("cooling_down") else "READY"
                busy_str = "BUSY" if w.get("busy") else cd_str
                print(f"  * [{w.get('account_id', 'N/A')[:8]}] {w.get('name', 'N/A'):<15} | Status: {busy_str:<22} | Title: {w.get('title', 'ChatGPT')}")

    # Settings
    settings = get_settings()
    if settings:
        print("\n--- ACTIVE SERVER SETTINGS ---")
        print(f"Post-Job Cooldown    : {settings.get('job_cooldown_seconds', 60)}s (per context)")
        print(f"Worker Poll Interval : {settings.get('worker_poll_interval', 2.0)}s")
        print(f"Translation Timeout  : {settings.get('translation_job_timeout', 120)}s")
        print(f"Max Text Length      : {settings.get('translation_max_text_length', 100000)} chars")


# ─── 2. Settings Management ───────────────────────────────────────────────────


def cmd_settings(args):
    """View, update, or reset Hermes server settings."""
    _print_header("HERMES SETTINGS MANAGEMENT")
    print(f"Target Server: {config.get_base_url()}\n")

    if getattr(args, "reset", False):
        res = reset_settings()
        if res:
            print("[+] Settings reset to factory defaults:")
            print(json.dumps(res, indent=2))
        return

    if getattr(args, "set", None):
        updates = {}
        for item in args.set:
            if "=" in item:
                k, v = item.split("=", 1)
                try:
                    updates[k.strip()] = float(v.strip()) if "." in v else int(v.strip())
                except ValueError:
                    updates[k.strip()] = v.strip()
        if updates:
            res = update_settings(updates)
            if res:
                print("[+] Settings updated successfully:")
                print(json.dumps(res, indent=2))
            return

    # View only
    settings = get_settings()
    if settings:
        print(json.dumps(settings, indent=2))
    else:
        print("[-] Failed to fetch settings from server.")


# ─── 3. Cookie & Multi-Account Management ─────────────────────────────────────


def cmd_cookies(args):
    """Manage cookie accounts, clear context cooldowns, and refresh contexts."""
    _print_header("COOKIE ACCOUNT MANAGEMENT")
    print(f"Target Server: {config.get_base_url()}\n")
    sub = getattr(args, "cookie_action", None) or "list"

    if sub == "list":
        data = list_cookies()
        if not data:
            print("[-] Unable to fetch cookies.")
            return
        accounts = data.get("accounts", [])
        print(f"Total Stored Accounts: {len(accounts)}\n")
        for a in accounts:
            cd_rem = a.get("cooldown_remaining_seconds")
            cd_str = f" (CD: {cd_rem}s)" if cd_rem else ""
            print(f"ID: {a['id']}")
            print(f"  Name: {a['name']} | Status: {a['status']}{cd_str} | Jobs: {a.get('total_jobs_processed', 0)}")
            print(f"  Cooldown Count: {a.get('cooldown_count', 0)} | Last Used: {a.get('last_used_at', 'Never')}")
            print("-" * 40)

    elif sub == "add":
        if not getattr(args, "name", None):
            print("[-] Error: --name is required for adding a cookie.")
            return
        cookies_val = None
        if getattr(args, "file", None):
            fp = Path(args.file)
            if not fp.is_file():
                print(f"[-] File not found: {fp}")
                return
            content = fp.read_text(encoding="utf-8").strip()
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "cookies" in parsed:
                    cookies_val = parsed["cookies"]
                else:
                    cookies_val = parsed
            except Exception:
                cookies_val = content
        elif getattr(args, "cookies", None):
            try:
                cookies_val = json.loads(args.cookies)
            except Exception:
                cookies_val = args.cookies
        else:
            print("[-] Error: Either --file or --cookies must be provided.")
            return

        res = add_cookie(name=args.name, provider=getattr(args, "provider", "chatgpt") or "chatgpt", cookies=cookies_val)
        if res:
            print(f"[+] Account '{args.name}' successfully added/updated:")
            print(json.dumps(res, indent=2))

    elif sub == "delete":
        if not getattr(args, "account_id", None):
            print("[-] Error: --account-id is required.")
            return
        if delete_cookie(args.account_id):
            print(f"[+] Account ID '{args.account_id}' deleted successfully.")
        else:
            print(f"[-] Failed to delete account ID '{args.account_id}'.")

    elif sub == "pause":
        if not getattr(args, "account_id", None):
            print("[-] Error: --account-id is required.")
            return
        res = pause_cookie(args.account_id)
        if res:
            print(f"[+] Account ID '{args.account_id}' paused successfully.")

    elif sub == "resume":
        if not getattr(args, "account_id", None):
            print("[-] Error: --account-id is required.")
            return
        res = resume_cookie(args.account_id)
        if res:
            print(f"[+] Account ID '{args.account_id}' resumed to ACTIVE.")

    elif sub == "refresh":
        if not getattr(args, "account_id", None):
            print("[-] Error: --account-id is required.")
            return
        res = refresh_cookie(args.account_id)
        if res:
            print(f"[+] Browser context refreshed: {res.get('message', 'OK')}")

    elif sub == "reset-cd":
        if not getattr(args, "account_id", None):
            print("[-] Error: --account-id is required for reset-cd.")
            return
        res = reset_cookie_cooldown(args.account_id)
        if res:
            print(f"[+] Cooldown successfully reset for account ID: {args.account_id}")
        else:
            print(f"[-] Failed to reset cooldown for account ID: {args.account_id}")


# ─── 4. Translation & Pipeline ────────────────────────────────────────────────


def _extract_chapter_number(filename_or_path: str | Path) -> float:
    name = Path(filename_or_path).stem
    m = re.search(r"chapter[_\s-]*(\d+(?:\.\d+)?)", name, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"^(\d+(?:\.\d+)?)", name)
    if m:
        return float(m.group(1))
    return 1.0


def cmd_translate(args):
    """Translate a single chapter file or text."""
    _print_header("HERMES TRANSLATION PIPELINE")
    print(f"Target Server: {config.get_base_url()}\n")

    if not getattr(args, "file", None) and not getattr(args, "text", None):
        print("[-] Please provide --file <path> or --text '<raw text>'.")
        return

    content = ""
    ch_num = getattr(args, "chapter", None) if getattr(args, "chapter", None) is not None else 1.0
    if getattr(args, "file", None):
        file_path = Path(args.file)
        if not file_path.is_file():
            print(f"[-] File not found: {file_path}")
            return
        content = file_path.read_text(encoding="utf-8")
        if getattr(args, "chapter", None) is None:
            ch_num = _extract_chapter_number(file_path)
    else:
        content = args.text

    novel_id = getattr(args, "novel_id", None) or "default_novel"
    src_lang = getattr(args, "source_lang", None) or config.DEFAULT_SOURCE_LANG
    tgt_lang = getattr(args, "target_lang", None) or config.DEFAULT_TARGET_LANG
    model = getattr(args, "model", None) or config.DEFAULT_MODEL
    wait_timeout = getattr(args, "timeout", None) or config.DEFAULT_TIMEOUT

    print(f"Novel ID       : {novel_id}")
    print(f"Chapter Number : {ch_num}")
    print(f"Source Language: {src_lang} -> Target: {tgt_lang}")
    print(f"Model          : {model}")
    print(f"Text Length    : {len(content)} characters\n")

    if getattr(args, "direct", False):
        print("[*] Direct chat completion requested (bypassing queue)...")
        res = chat_completion(model=model, prompt=content)
        if res:
            print("\n--- Translation Result ---")
            print(res)
            if getattr(args, "output", None):
                Path(args.output).write_text(res, encoding="utf-8")
                print(f"\n[+] Saved to {args.output}")
        return

    print("[*] Submitting to Hermes translation queue...")
    sub_res = submit_translation_job(
        novel_id=novel_id,
        chapter_number=ch_num,
        text=content,
        source_lang=src_lang,
        target_lang=tgt_lang,
        model=model,
        force=getattr(args, "force", False),
    )

    if not sub_res:
        print("[-] Job submission failed.")
        return

    if sub_res.get("status") == "conflict":
        job_id = sub_res.get("job_id")
        print(f"[!] Chapter already translated (Job ID: {job_id}).")
        if not getattr(args, "wait", False):
            return
    else:
        job_id = sub_res.get("id")
        print(f"[+] Job submitted successfully! Job ID: {job_id}")

    if getattr(args, "wait", False) or getattr(args, "output", None):
        print("[*] Waiting for job completion (polling Hermes queue)...")

        def on_status(j):
            print(f"    Status transition -> {j.get('status')}")

        done_job = wait_for_job(job_id, poll_interval=2.5, timeout=wait_timeout, on_status_change=on_status)
        if done_job and done_job.get("status") == "done":
            result = done_job.get("result", {})
            translation_text = result.get("translation") or result.get("translate_md") or ""
            print("\n--- TRANSLATION COMPLETED ---")
            if getattr(args, "output", None):
                Path(args.output).write_text(translation_text, encoding="utf-8")
                print(f"[+] Output successfully written to: {args.output}")
            else:
                print(translation_text[:500] + ("\n... [truncated]" if len(translation_text) > 500 else ""))
        elif done_job and done_job.get("status") == "failed":
            print(f"[-] Translation failed: {done_job.get('error_code')} - {done_job.get('error_message')}")
        else:
            print(f"[!] Job ended with status: {done_job.get('status') if done_job else 'Timeout'}")


def cmd_batch(args):
    """Batch submit chapters from a novel directory to Hermes queue."""
    _print_header("BATCH NOVEL TRANSLATION SUBMISSION")
    print(f"Target Server: {config.get_base_url()}\n")
    dir_path = Path(args.dir)
    if not dir_path.is_dir():
        print(f"[-] Directory not found: {dir_path}")
        return

    novel_id = getattr(args, "novel_id", None) or dir_path.parent.name or "novel"
    src_lang = getattr(args, "source_lang", None) or config.DEFAULT_SOURCE_LANG
    tgt_lang = getattr(args, "target_lang", None) or config.DEFAULT_TARGET_LANG
    model = getattr(args, "model", None) or config.DEFAULT_MODEL

    files = sorted(list(dir_path.glob("*.txt")), key=lambda p: _extract_chapter_number(p))
    if not files:
        print(f"[-] No .txt files found in {dir_path}")
        return

    filtered = []
    for f in files:
        ch = _extract_chapter_number(f)
        if getattr(args, "from_ch", None) is not None and ch < args.from_ch:
            continue
        if getattr(args, "to_ch", None) is not None and ch > args.to_ch:
            continue
        filtered.append((ch, f))

    print(f"Found {len(filtered)} chapter(s) to process for novel '{novel_id}'.")
    submitted = []
    for ch, fpath in filtered:
        try:
            txt = fpath.read_text(encoding="utf-8")
            res = submit_translation_job(
                novel_id=novel_id,
                chapter_number=ch,
                text=txt,
                source_lang=src_lang,
                target_lang=tgt_lang,
                model=model,
                force=getattr(args, "force", False),
            )
            status = res.get("status", "submitted") if res else "error"
            jid = res.get("id") or res.get("job_id", "N/A")
            print(f"  * Ch {ch:<5} -> {fpath.name[:35]:<35} | Status: {status:<10} | Job: {jid}")
            if res and "id" in res:
                submitted.append(res["id"])
            if getattr(args, "delay", None):
                time.sleep(args.delay)
        except Exception as e:
            print(f"  * Ch {ch:<5} -> Error: {e}")

    print(f"\n[+] Batch submission complete. {len(submitted)} new job(s) queued.")


# ─── 5. Jobs & History Management ─────────────────────────────────────────────


def cmd_jobs(args):
    """Inspect and manage Hermes translation jobs and revision history."""
    _print_header("HERMES TRANSLATION JOBS")
    print(f"Target Server: {config.get_base_url()}\n")
    sub = getattr(args, "job_action", None) or "list"

    if sub == "list":
        data = list_jobs(
            novel_id=getattr(args, "novel_id", None),
            status=getattr(args, "status", None),
            page=getattr(args, "page", 1) or 1,
            limit=getattr(args, "limit", 20) or 20,
        )
        if not data:
            print("[-] Unable to fetch jobs.")
            return
        total = data.get("total", 0)
        limit = data.get("limit", 20) or 20
        total_pages = ((total - 1) // limit) + 1 if total > 0 else 1
        print(f"Total Jobs: {total} (Page {data.get('page', 1)}/{total_pages})\n")
        for j in data.get("items", []):
            print(f"Job ID: {j['job_id'] if 'job_id' in j else j.get('id')}")
            print(f"  Novel: {j.get('novel_id')} | Ch: {j.get('chapter_number')} | Status: {j.get('status', 'N/A'):<9} | Model: {j.get('model')}")
            print(f"  Created: {j.get('created_at')}")
            if j.get("error"):
                err = j["error"]
                print(f"  Error: {err.get('code')} - {err.get('message')}")
            elif j.get("error_code"):
                print(f"  Error: {j.get('error_code')} - {j.get('error_message')}")
            print("-" * 50)

    elif sub == "get":
        if not getattr(args, "job_id", None):
            print("[-] --job-id is required.")
            return
        job = get_job(args.job_id)
        if job:
            print(json.dumps(job, indent=2, ensure_ascii=False))
        else:
            print(f"[-] Job {args.job_id} not found.")

    elif sub == "retry":
        if not getattr(args, "job_id", None):
            print("[-] --job-id is required.")
            return
        res = retry_job(args.job_id)
        print(f"[+] Retry result: {res}")

    elif sub in ("cancel", "cancel-all"):
        if getattr(args, "all", False) or sub == "cancel-all":
            st_filter = getattr(args, "status", "all") or "all"
            nov_filter = getattr(args, "novel_id", None)
            res = cancel_all_jobs(status_filter=st_filter, novel_id=nov_filter)
            print(f"[+] Bulk cancel result: {json.dumps(res, indent=2)}")
        else:
            if not getattr(args, "job_id", None):
                print("[-] --job-id is required (or use --all / cancel-all).")
                return
            res = cancel_job(args.job_id)
            print(f"[+] Cancel result: {res}")

    elif sub == "delete":
        if not getattr(args, "job_id", None):
            print("[-] --job-id is required.")
            return
        res = delete_job(args.job_id)
        print(f"[+] Delete result: {res}")

    elif sub == "history":
        if not getattr(args, "job_id", None):
            print("[-] --job-id is required.")
            return
        res = get_job_history(args.job_id)
        if res and "history" in res:
            hist_list = res["history"]
            print(f"Found {len(hist_list)} archived version(s) for job {args.job_id}:\n")
            for h in hist_list:
                print(f"  * History ID: {h.get('id')} | Archived At: {h.get('archived_at')}")
                print(f"    Summary   : {h.get('result_summary') or 'N/A'}")
                print(f"    Preview   : {str(h.get('result_translation', ''))[:100]}...")
                print("-" * 40)
        else:
            print(f"[-] No history found for job {args.job_id}.")

    elif sub == "rollback":
        if not getattr(args, "history_id", None):
            print("[-] --history-id is required for rollback.")
            return
        res = restore_history(args.history_id)
        if res:
            print(f"[+] History ID {args.history_id} restored to active job:")
            print(json.dumps(res, indent=2))


# ─── 6. Novel, Character & Glossary Management ────────────────────────────────


def cmd_novel(args):
    """View novel statistics, chapters, characters, glossary, and revision history."""
    _print_header("NOVEL & CONTINUITY MANAGEMENT")
    print(f"Target Server: {config.get_base_url()}\n")
    sub = getattr(args, "novel_action", None) or "list"

    if sub == "list":
        novels = list_novels()
        print(f"Total Registered Novels: {len(novels)}\n")
        for n in novels:
            print(f"  * Novel ID: {n.get('novel_id'):<20} | Total Jobs: {n.get('total_jobs', 0):<5} | Latest Ch: {n.get('latest_chapter')}")

    elif sub == "stats":
        if not getattr(args, "novel_id", None):
            print("[-] --novel-id is required.")
            return
        stats = get_novel_stats(args.novel_id)
        if stats:
            print(json.dumps(stats, indent=2))
        else:
            print(f"[-] Stats not found for {args.novel_id}")

    elif sub == "chapters":
        if not getattr(args, "novel_id", None):
            print("[-] --novel-id is required.")
            return
        res = get_novel_chapters(
            args.novel_id,
            status=getattr(args, "status", None),
            page=getattr(args, "page", 1) or 1,
            limit=getattr(args, "limit", 100) or 100,
        )
        if res:
            print(f"Chapters for novel '{args.novel_id}' (Total: {res.get('total', 0)}):")
            for c in res.get("chapters", []):
                trans_ind = "[✓ Translated]" if c.get("has_translation") else "[ ]"
                print(f"  Ch {c['chapter_number']:<6} | Status: {c['status']:<9} | {trans_ind} {c.get('job_id')}")

    elif sub == "history":
        if not getattr(args, "novel_id", None):
            print("[-] --novel-id is required.")
            return
        ch = getattr(args, "chapter", None)
        res = get_novel_history(args.novel_id, chapter_number=ch, page=getattr(args, "page", 1) or 1, limit=getattr(args, "limit", 20) or 20)
        if res and "history" in res:
            hist_list = res["history"]
            print(f"History entries for novel '{args.novel_id}' (Total: {res.get('total', len(hist_list))}):")
            for h in hist_list:
                print(f"  * [Hist #{h.get('id')}] Ch {h.get('chapter_number')} | Archived: {h.get('archived_at')}")
                print(f"    Summary: {h.get('result_summary') or 'N/A'}")
        else:
            print(f"[-] No history found for novel '{args.novel_id}'.")

    elif sub == "characters":
        if not getattr(args, "novel_id", None):
            print("[-] --novel-id is required.")
            return
        act = getattr(args, "char_action", "list") or "list"

        if act == "list":
            chars = list_characters(args.novel_id, q=getattr(args, "q", None), gender=getattr(args, "gender", None))
            if isinstance(chars, dict):
                chars = chars.get("items", [])
            print(f"Characters for novel '{args.novel_id}' (Total: {len(chars)}):")
            for ch in chars:
                print(f"  * [ID: {ch['id']}] {ch['name']} ({ch.get('native_name', '')}) | Gender: {ch.get('gender')} | 1st Ch: {ch.get('first_seen_chapter')}")
                if ch.get("notes"):
                    print(f"    Notes: {ch['notes']}")

        elif act == "add":
            if not getattr(args, "name", None):
                print("[-] --name is required.")
                return
            res = create_character(
                novel_id=args.novel_id,
                name=args.name,
                native_name=getattr(args, "native", None),
                gender=getattr(args, "gender", "unknown") or "unknown",
                notes=getattr(args, "notes", "") or "",
                first_seen_chapter=getattr(args, "first_ch", 1.0) or 1.0,
            )
            if res:
                print(f"[+] Character added/retrieved:\n{json.dumps(res, indent=2)}")

        elif act == "get":
            if not getattr(args, "char_id", None):
                print("[-] --char-id is required.")
                return
            res = get_character(args.novel_id, args.char_id)
            if res:
                print(json.dumps(res, indent=2))
            else:
                print(f"[-] Character ID {args.char_id} not found.")

        elif act == "update":
            if not getattr(args, "char_id", None):
                print("[-] --char-id is required.")
                return
            res = update_character(
                novel_id=args.novel_id,
                character_id=args.char_id,
                name=getattr(args, "name", None),
                native_name=getattr(args, "native", None),
                gender=getattr(args, "gender", None),
                notes=getattr(args, "notes", None),
                last_updated_chapter=getattr(args, "last_ch", None),
            )
            if res:
                print(f"[+] Character updated:\n{json.dumps(res, indent=2)}")

        elif act == "delete":
            if not getattr(args, "char_id", None):
                print("[-] --char-id is required.")
                return
            if delete_character(args.novel_id, args.char_id):
                print(f"[+] Character ID {args.char_id} deleted.")
            else:
                print(f"[-] Failed to delete character ID {args.char_id}.")

    elif sub == "glossary":
        if not getattr(args, "novel_id", None):
            print("[-] --novel-id is required.")
            return
        act = getattr(args, "gloss_action", "list") or "list"

        if act == "list":
            gloss = list_glossary(args.novel_id, q=getattr(args, "q", None))
            if isinstance(gloss, dict):
                gloss = gloss.get("items", [])
            print(f"Glossary terms for novel '{args.novel_id}' (Total: {len(gloss)}):")
            for g in gloss:
                print(f"  * [ID: {g['id']}] {g['term_source']} -> {g['term_translation']} (Ch: {g.get('first_seen_chapter')})")
                if g.get("notes"):
                    print(f"    Notes: {g['notes']}")

        elif act == "add":
            if not getattr(args, "source", None) or not getattr(args, "trans", None):
                print("[-] Both --source and --trans are required.")
                return
            res = create_glossary(
                novel_id=args.novel_id,
                term_source=args.source,
                term_translation=args.trans,
                notes=getattr(args, "notes", "") or "",
                first_seen_chapter=getattr(args, "first_ch", 1.0) or 1.0,
            )
            if res:
                print(f"[+] Glossary term created:\n{json.dumps(res, indent=2)}")

        elif act == "get":
            if not getattr(args, "term_id", None):
                print("[-] --term-id is required.")
                return
            res = get_glossary(args.novel_id, args.term_id)
            if res:
                print(json.dumps(res, indent=2))
            else:
                print(f"[-] Term ID {args.term_id} not found.")

        elif act == "update":
            if not getattr(args, "term_id", None):
                print("[-] --term-id is required.")
                return
            res = update_glossary(
                novel_id=args.novel_id,
                glossary_id=args.term_id,
                term_source=getattr(args, "source", None),
                term_translation=getattr(args, "trans", None),
                notes=getattr(args, "notes", None),
                last_updated_chapter=getattr(args, "last_ch", None),
            )
            if res:
                print(f"[+] Term updated:\n{json.dumps(res, indent=2)}")

        elif act == "delete":
            if not getattr(args, "term_id", None):
                print("[-] --term-id is required.")
                return
            if delete_glossary(args.novel_id, args.term_id):
                print(f"[+] Term ID {args.term_id} deleted.")
            else:
                print(f"[-] Failed to delete term ID {args.term_id}.")

        elif act == "bulk":
            if not getattr(args, "file", None):
                print("[-] --file is required for bulk import.")
                return
            fp = Path(args.file)
            if not fp.is_file():
                print(f"[-] File not found: {fp}")
                return
            terms_data = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(terms_data, dict) and "terms" in terms_data:
                terms_data = terms_data["terms"]
            res = bulk_import_glossary(args.novel_id, terms_data, first_seen_chapter=getattr(args, "first_ch", 1.0) or 1.0)
            if res:
                print(f"[+] Bulk glossary import result:\n{json.dumps(res, indent=2)}")

        elif act == "export":
            fmt = getattr(args, "format", "json") or "json"
            out = getattr(args, "output", None)
            res = export_glossary(args.novel_id, export_format=fmt, output_path=out)
            if out:
                print(f"[+] Glossary exported to {out}")
            else:
                print(res if isinstance(res, str) else json.dumps(res, indent=2, ensure_ascii=False))


# ─── 7. Database Backup & Restore ─────────────────────────────────────────────


def cmd_database(args):
    """Backup, restore, or view database statistics for Hermes."""
    _print_header("HERMES DATABASE BACKUP & RESTORE")
    print(f"Target Server: {config.get_base_url()}\n")
    sub = getattr(args, "db_action", None) or "stats"

    if sub == "stats":
        stats = get_database_stats()
        if stats:
            print("Database Table Row Counts & Statistics:")
            for k, v in stats.items():
                print(f"  * {k:<22}: {v}")
        else:
            print("[-] Failed to retrieve database stats.")

    elif sub == "backup":
        out_path = getattr(args, "output", None)
        print("[*] Requesting SQLite database backup snapshot from Hermes...")
        ok, path_or_err, byte_count = backup_database(output_path=out_path)
        if ok:
            mb = byte_count / (1024 * 1024)
            print("[+] Backup successfully downloaded!")
            print(f"    - File : {path_or_err}")
            print(f"    - Size : {byte_count:,} bytes ({mb:.2f} MB)")
        else:
            print(f"[-] Backup failed: {path_or_err}")

    elif sub == "restore":
        in_file = getattr(args, "file", None)
        if not in_file:
            print("[-] Error: --file <path_to_backup.zip> is required for restore.")
            return
        fp = Path(in_file)
        if not fp.is_file():
            print(f"[-] File not found: {fp}")
            return

        print(f"[*] Restoring Hermes database from backup archive: {fp.name}...")
        res = restore_database(file_path=fp)
        if res:
            print("\n[+] Database restored successfully!")
            print(f"    Message        : {res.get('message', 'OK')}")
            print(f"    Tables Restored: {res.get('tables_restored', 0)}")
            print(f"    Exported At    : {res.get('metadata', {}).get('exported_at', 'N/A')}")
            if "stats_after_restore" in res:
                print("\nTable Statistics After Restore:")
                for k, v in res["stats_after_restore"].items():
                    print(f"      - {k:<20}: {v}")
        else:
            print("[-] Database restore failed.")


# ─── 8. Direct Interactive Chat ───────────────────────────────────────────────


def cmd_chat(args):
    """Start interactive OpenAI-compatible chat session."""
    _print_header("HERMES INTERACTIVE CHAT")
    print(f"Target Server: {config.get_base_url()}\n")
    models_data = get_models()
    model = getattr(args, "model", None) or (models_data["data"][0]["id"] if models_data and models_data.get("data") else config.DEFAULT_MODEL)
    print(f"Active Model: {model}")
    print("Type 'exit' or 'quit' to end session.\n")

    while True:
        try:
            prompt = input("You: ").strip()
            if prompt.lower() in ("exit", "quit"):
                break
            if not prompt:
                continue
            res = chat_completion(model=model, prompt=prompt)
            if res:
                print(f"\nAssistant:\n{res}\n")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break


# ─── Argument Parser Builder ──────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--server",
        "-s",
        "--url",
        dest="server",
        type=str,
        default=None,
        help="Hermes API server URL (e.g. http://localhost:18111, http://192.168.1.50:18111, or :18111)",
    )
    common_parser.add_argument(
        "--internal-key",
        "-k",
        dest="internal_key",
        type=str,
        default=None,
        help="Hermes internal API security key (X-Internal-Key)",
    )
    common_parser.add_argument(
        "--model",
        "-m",
        dest="model",
        type=str,
        default=None,
        help="LLM Model identifier (e.g. gpt-5.6-luna)",
    )
    common_parser.add_argument(
        "--source-lang",
        "--src",
        dest="source_lang",
        type=str,
        default=None,
        help="Source language (ko/ja/zh/en)",
    )
    common_parser.add_argument(
        "--target-lang",
        "--tgt",
        dest="target_lang",
        type=str,
        default=None,
        help="Target language (id/en)",
    )
    common_parser.add_argument(
        "--timeout",
        "-t",
        dest="timeout",
        type=int,
        default=None,
        help="Timeout in seconds for API requests / polling",
    )

    parser = argparse.ArgumentParser(
        description="Hermes ChatGPT Web Unified Client App & Automation Tool",
        parents=[common_parser],
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # 1. status
    p_status = subparsers.add_parser("status", parents=[common_parser], help="Show Hermes server, worker cooldowns, and settings")
    p_status.set_defaults(func=cmd_status)

    # 2. settings
    p_settings = subparsers.add_parser("settings", parents=[common_parser], help="View or update Hermes server settings")
    p_settings.add_argument("--set", nargs="+", help="Update settings (e.g. --set job_cooldown_seconds=30)")
    p_settings.add_argument("--reset", action="store_true", help="Reset settings to factory defaults")
    p_settings.set_defaults(func=cmd_settings)

    # 3. cookies
    p_cookies = subparsers.add_parser("cookies", parents=[common_parser], help="Manage account cookies, cooldowns & context")
    p_cookies.add_argument("cookie_action", nargs="?", choices=["list", "add", "delete", "pause", "resume", "refresh", "reset-cd"], default="list")
    p_cookies.add_argument("--account-id", type=str, help="Account ID for operations")
    p_cookies.add_argument("--name", type=str, help="Account name for add")
    p_cookies.add_argument("--provider", type=str, default="chatgpt", help="Account provider (default: chatgpt)")
    p_cookies.add_argument("--file", type=str, help="Cookie JSON file path for add")
    p_cookies.add_argument("--cookies", type=str, help="Raw cookie JSON string for add")
    p_cookies.set_defaults(func=cmd_cookies)

    # 4. translate
    p_trans = subparsers.add_parser("translate", parents=[common_parser], help="Translate single chapter file or text")
    p_trans.add_argument("--file", type=str, help="Path to chapter text file")
    p_trans.add_argument("--text", type=str, help="Raw text string to translate")
    p_trans.add_argument("--novel-id", type=str, default="novel", help="Novel identifier")
    p_trans.add_argument("--chapter", type=float, help="Chapter number")
    p_trans.add_argument("--force", action="store_true", help="Force re-translation if already exists")
    p_trans.add_argument("--wait", action="store_true", help="Poll queue until completed and output result")
    p_trans.add_argument("--output", type=str, help="Save translated text to output file path")
    p_trans.add_argument("--direct", action="store_true", help="Direct OpenAI chat completion (bypass queue)")
    p_trans.set_defaults(func=cmd_translate)

    # 5. batch
    p_batch = subparsers.add_parser("batch", parents=[common_parser], help="Batch submit novel chapters directory to queue")
    p_batch.add_argument("--dir", type=str, required=True, help="Directory containing .txt chapter files")
    p_batch.add_argument("--novel-id", type=str, help="Novel identifier")
    p_batch.add_argument("--from-ch", type=float, help="Start chapter number")
    p_batch.add_argument("--to-ch", type=float, help="End chapter number")
    p_batch.add_argument("--force", action="store_true", help="Force re-translation")
    p_batch.add_argument("--delay", type=float, default=0.1, help="Delay between submissions (seconds)")
    p_batch.set_defaults(func=cmd_batch)

    # 6. jobs
    p_jobs = subparsers.add_parser("jobs", parents=[common_parser], help="List, inspect, cancel, retry, and rollback translation jobs")
    p_jobs.add_argument("job_action", nargs="?", choices=["list", "get", "retry", "cancel", "cancel-all", "delete", "history", "rollback"], default="list")
    p_jobs.add_argument("--job-id", type=str, help="Job ID")
    p_jobs.add_argument("--history-id", type=int, help="History version ID for rollback")
    p_jobs.add_argument("--novel-id", type=str, help="Filter per novel")
    p_jobs.add_argument("--status", type=str, help="Filter per status (pending/running/done/failed/cancelled/all)")
    p_jobs.add_argument("--all", action="store_true", help="Cancel all pending and running jobs")
    p_jobs.add_argument("--page", type=int, default=1)
    p_jobs.add_argument("--limit", type=int, default=20)
    p_jobs.set_defaults(func=cmd_jobs)

    # 7. novel
    p_novel = subparsers.add_parser("novel", parents=[common_parser], help="Inspect novel metadata, chapters, characters, glossary & history")
    p_novel.add_argument("novel_action", nargs="?", choices=["list", "stats", "chapters", "history", "characters", "glossary"], default="list")
    p_novel.add_argument("--novel-id", type=str, help="Novel ID")
    p_novel.add_argument("--chapter", type=float, help="Chapter number for history")
    p_novel.add_argument("--status", type=str, help="Chapter status filter")
    p_novel.add_argument("--page", type=int, default=1)
    p_novel.add_argument("--limit", type=int, default=100)
    # Characters sub-args
    p_novel.add_argument("--char-action", choices=["list", "add", "get", "update", "delete"], default="list")
    p_novel.add_argument("--char-id", type=int, help="Character ID")
    p_novel.add_argument("--name", type=str, help="Character name")
    p_novel.add_argument("--native", type=str, help="Character native name")
    p_novel.add_argument("--gender", type=str, help="Character gender (male/female/unknown)")
    p_novel.add_argument("--first-ch", type=float, default=1.0, help="First seen chapter")
    p_novel.add_argument("--last-ch", type=float, help="Last updated chapter")
    # Glossary sub-args
    p_novel.add_argument("--gloss-action", choices=["list", "add", "get", "update", "delete", "bulk", "export"], default="list")
    p_novel.add_argument("--term-id", type=int, help="Glossary term ID")
    p_novel.add_argument("--source", type=str, help="Glossary term source")
    p_novel.add_argument("--trans", type=str, help="Glossary term translation")
    p_novel.add_argument("--notes", type=str, help="Character / glossary notes")
    p_novel.add_argument("--file", type=str, help="File path for glossary bulk import")
    p_novel.add_argument("--format", choices=["json", "csv"], default="json", help="Glossary export format")
    p_novel.add_argument("--output", type=str, help="Output file path for glossary export")
    p_novel.add_argument("--q", type=str, help="Search keyword for characters/glossary")
    p_novel.set_defaults(func=cmd_novel)

    # 8. database / db
    p_db = subparsers.add_parser("db", parents=[common_parser], aliases=["database"], help="Database backup, restore, and table statistics")
    p_db.add_argument("db_action", nargs="?", choices=["stats", "backup", "restore"], default="stats")
    p_db.add_argument("--file", type=str, help="Path to .zip backup archive for restore")
    p_db.add_argument("--output", type=str, help="Output .zip path for backup")
    p_db.set_defaults(func=cmd_database)

    # 9. backup alias (top-level)
    p_bak = subparsers.add_parser("backup", parents=[common_parser], help="Download a complete SQLite database backup .zip")
    p_bak.add_argument("--output", type=str, help="Output .zip path for backup")
    p_bak.set_defaults(func=lambda a: cmd_database(argparse.Namespace(db_action="backup", output=a.output, **vars(a))))

    # 10. restore alias (top-level)
    p_res = subparsers.add_parser("restore", parents=[common_parser], help="Restore SQLite database from a .zip backup archive")
    p_res.add_argument("--file", type=str, required=True, help="Path to .zip backup archive")
    p_res.set_defaults(func=lambda a: cmd_database(argparse.Namespace(db_action="restore", file=a.file, **vars(a))))

    # 11. chat
    p_chat = subparsers.add_parser("chat", parents=[common_parser], help="Interactive OpenAI chat session")
    p_chat.set_defaults(func=cmd_chat)

    # 12. interactive / menu
    p_menu = subparsers.add_parser("menu", parents=[common_parser], aliases=["interactive", "ui"], help="Launch Interactive Terminal Dashboard")
    p_menu.set_defaults(func=lambda a: run_interactive_menu())

    # Top-level args for backward compatibility
    parser.add_argument("--interactive", "-i", action="store_true", help="Launch interactive terminal dashboard menu")
    parser.add_argument("--file", type=str, help="Legacy translate file parameter")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Configure global settings dynamically from flags
    set_config(
        server=getattr(args, "server", None),
        internal_key=getattr(args, "internal_key", None),
        default_model=getattr(args, "model", None),
        source_lang=getattr(args, "source_lang", None),
        target_lang=getattr(args, "target_lang", None),
        timeout=getattr(args, "timeout", None),
    )

    if getattr(args, "command", None):
        args.func(args)
    elif getattr(args, "file", None):
        # Legacy translate file invocation
        cmd_translate(args)
    else:
        # Default: Launch Interactive Dashboard Menu loop!
        run_interactive_menu()


if __name__ == "__main__":
    main()

