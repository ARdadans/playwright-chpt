import json
from pathlib import Path
import re
import sys
import time

# Ensure safe UTF-8 output on all terminal platforms (Windows, Linux, macOS)
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

# ─── Terminal Styling Helpers ─────────────────────────────────────────────────

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

DIM = "\033[2m"
RESET = "\033[0m"


def _header(title: str):
    print("\n" + "=" * 65)
    print(f"  {BOLD}{CYAN}{title}{RESET}")
    print("=" * 65)


def _pause():
    input(f"\n{DIM}Tekan Enter untuk kembali ke menu...{RESET}")


def _prompt(label: str, default: str | None = None) -> str:
    if default is not None:
        p = f"{label} [{GREEN}{default}{RESET}]: "
    else:
        p = f"{label}: "
    val = input(p).strip()
    return val if val else (default or "")


def _extract_chapter_number(filename_or_path: str | Path) -> float:
    name = Path(filename_or_path).stem
    m = re.search(r"chapter[_\s-]*(\d+(?:\.\d+)?)", name, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"^(\d+(?:\.\d+)?)", name)
    if m:
        return float(m.group(1))
    return 1.0


# ─── 1. Status & Server Overview ──────────────────────────────────────────────


def interactive_status():
    _header("📊 HERMES SERVER STATUS & WORKER MONITOR")
    server_url = config.get_base_url()
    print(f"Target Server : {BOLD}{server_url}{RESET}")

    health = check_health()
    if not health or not health.get("ok"):
        print(f"Server Status : {RED}{BOLD}OFFLINE / UNREACHABLE{RESET}")
        print(f"\n{YELLOW}Tips:{RESET} Pastikan server Hermes aktif di {server_url} atau ubah target via Menu [10].")
        _pause()
        return

    print(f"Server Status : {GREEN}{BOLD}ONLINE (OK){RESET}")
    gw = health.get("gateway", {})
    if gw:
        print(f"Gateway Title : {gw.get('title', 'N/A')}")
        print(f"Turns / Idle  : {gw.get('turns', 0)} processed | {gw.get('idle_s', 0)}s idle")

    cookie_info = list_cookies()
    if cookie_info and "pool" in cookie_info:
        pool = cookie_info["pool"]
        print(f"\n{BOLD}Worker Pool Status:{RESET}")
        print(f"  - Total Workers : {pool.get('total_workers', 0)}")
        print(f"  - Idle Workers  : {GREEN}{pool.get('idle_workers', 0)}{RESET}")
        print(f"  - Busy Workers  : {YELLOW}{pool.get('busy_workers', 0)}{RESET}")
        print(f"  - In Cooldown   : {CYAN}{pool.get('cooling_down_workers', 0)}{RESET}")

        workers = pool.get("workers", [])
        if workers:
            print("\n  Active Accounts:")
            for w in workers:
                cd_status = f"{CYAN}COOLDOWN ({w.get('cooldown_remaining_s')}s left){RESET}" if w.get("cooling_down") else f"{GREEN}READY{RESET}"
                st = f"{YELLOW}BUSY{RESET}" if w.get("busy") else cd_status
                print(f"    * [{w.get('account_id', 'N/A')[:8]}] {w.get('name', 'N/A'):<15} | Status: {st:<28} | {w.get('title', 'ChatGPT')}")

    settings = get_settings()
    if settings:
        print(f"\n{BOLD}Active Settings:{RESET}")
        print(f"  - Post-Job Cooldown    : {settings.get('job_cooldown_seconds', 60)}s")
        print(f"  - Worker Poll Interval : {settings.get('worker_poll_interval', 2.0)}s")
        print(f"  - Translation Timeout  : {settings.get('translation_job_timeout', 120)}s")
        print(f"  - Max Text Length      : {settings.get('translation_max_text_length', 100000)} chars")

    _pause()


# ─── 2. Translate Single Chapter ──────────────────────────────────────────────


def interactive_translate():
    _header("🚀 TERJEMAHKAN BAB NOVEL (TRANSLATE)")
    print("Pilih metode input:")
    print("  [1] Dari File Teks (.txt)")
    print("  [2] Masukkan Teks Langsung")
    print("  [0] Batal")
    choice = input("\nPilihan: ").strip()
    if choice == "0":
        return

    content = ""
    default_ch = 1.0
    default_novel = "novel"

    if choice == "1":
        file_path_str = _prompt("Path file bab (.txt)")
        if not file_path_str:
            print(f"{RED}Path file tidak boleh kosong.{RESET}")
            _pause()
            return
        fp = Path(file_path_str.strip('"').strip("'"))
        if not fp.is_file():
            print(f"{RED}File tidak ditemukan: {fp}{RESET}")
            _pause()
            return
        content = fp.read_text(encoding="utf-8")
        default_ch = _extract_chapter_number(fp)
        default_novel = fp.parent.parent.name if fp.parent.name == "chapters" else (fp.parent.name or "novel")
    elif choice == "2":
        print(f"{CYAN}Ketik/tempel teks lalu tekan Enter:{RESET}")
        content = input("> ").strip()
        if not content:
            print(f"{RED}Teks tidak boleh kosong.{RESET}")
            _pause()
            return
    else:
        return

    novel_id = _prompt("Novel ID", default=default_novel)
    ch_num_str = _prompt("Nomor Bab", default=str(default_ch))
    try:
        ch_num = float(ch_num_str)
    except ValueError:
        ch_num = 1.0

    src_lang = _prompt("Bahasa Sumber (ko/ja/zh/en)", default=config.DEFAULT_SOURCE_LANG)
    tgt_lang = _prompt("Bahasa Target (id/en)", default=config.DEFAULT_TARGET_LANG)
    model = _prompt("Model LLM", default=config.DEFAULT_MODEL)
    force_in = _prompt("Paksa terjemahkan ulang jika sudah ada? (y/n)", default="n")
    force = force_in.lower().startswith("y")
    wait_in = _prompt("Tunggu proses queue sampai selesai (--wait)? (y/n)", default="y")
    wait_flag = wait_in.lower().startswith("y")
    out_file = _prompt("Simpan output Markdown ke file? (kosongkan jika tidak)", default="")

    print(f"\n{BOLD}[*] Mengirimkan bab ke antrean Hermes...{RESET}")
    res = submit_translation_job(
        novel_id=novel_id,
        chapter_number=ch_num,
        text=content,
        source_lang=src_lang,
        target_lang=tgt_lang,
        model=model,
        force=force,
    )

    if not res:
        print(f"{RED}[-] Gagal mengirim job.{RESET}")
        _pause()
        return

    if res.get("status") == "conflict":
        job_id = res.get("job_id")
        print(f"{YELLOW}[!] Bab sudah pernah diterjemahkan sebelumnya (Job ID: {job_id}). Gunakan force=y untuk re-translate.{RESET}")
        if not wait_flag:
            _pause()
            return
    else:
        job_id = res.get("id")
        print(f"{GREEN}[+] Job berhasil dikirim! Job ID: {BOLD}{job_id}{RESET}")

    if wait_flag or out_file:
        print(f"[*] Menunggu pengerjaan worker (polling realtime)...")

        def on_status_change(j):
            print(f"    Status beralih -> {BOLD}{j.get('status')}{RESET}")

        done_job = wait_for_job(job_id, poll_interval=2.5, on_status_change=on_status_change)
        if done_job and done_job.get("status") == "done":
            result = done_job.get("result", {})
            trans_text = result.get("translation") or result.get("translate_md") or ""
            print(f"\n{GREEN}{BOLD}=== TERJEMAHAN SELESAI ==={RESET}")
            if out_file:
                Path(out_file).write_text(trans_text, encoding="utf-8")
                print(f"{GREEN}[+] Output berhasil disimpan ke: {BOLD}{out_file}{RESET}")
            else:
                preview = trans_text[:800] + ("\n... [dipotong]" if len(trans_text) > 800 else "")
                print(preview)
        elif done_job and done_job.get("status") == "failed":
            print(f"{RED}[-] Terjemahan gagal: {done_job.get('error_code')} - {done_job.get('error_message')}{RESET}")
        else:
            print(f"{YELLOW}[!] Status akhir: {done_job.get('status') if done_job else 'Timeout'}{RESET}")

    _pause()


# ─── 3. Batch Submit Directory ────────────────────────────────────────────────


def interactive_batch():
    _header("📑 BATCH SUBMIT NOVEL CHAPTERS")
    dir_path_str = _prompt("Direktori folder berisi file bab (.txt)")
    if not dir_path_str:
        return
    dir_path = Path(dir_path_str.strip('"').strip("'"))
    if not dir_path.is_dir():
        print(f"{RED}Direktori tidak ditemukan: {dir_path}{RESET}")
        _pause()
        return

    default_novel = dir_path.parent.name if dir_path.name == "chapters" else dir_path.name
    novel_id = _prompt("Novel ID", default=default_novel)
    from_ch_str = _prompt("Mulai dari bab (kosongkan untuk semua)", default="")
    to_ch_str = _prompt("Sampai bab (kosongkan untuk semua)", default="")
    from_ch = float(from_ch_str) if from_ch_str else None
    to_ch = float(to_ch_str) if to_ch_str else None

    src_lang = _prompt("Bahasa Sumber", default=config.DEFAULT_SOURCE_LANG)
    tgt_lang = _prompt("Bahasa Target", default=config.DEFAULT_TARGET_LANG)
    model = _prompt("Model", default=config.DEFAULT_MODEL)
    force_in = _prompt("Force re-translate jika sudah ada? (y/n)", default="n")
    force = force_in.lower().startswith("y")

    files = sorted(list(dir_path.glob("*.txt")), key=lambda p: _extract_chapter_number(p))
    if not files:
        print(f"{RED}Tidak ada file .txt di {dir_path}{RESET}")
        _pause()
        return

    filtered = []
    for f in files:
        ch = _extract_chapter_number(f)
        if from_ch is not None and ch < from_ch:
            continue
        if to_ch is not None and ch > to_ch:
            continue
        filtered.append((ch, f))

    print(f"\nDitemukan {BOLD}{len(filtered)}{RESET} bab untuk dikirim ke antrean Hermes.")
    confirm = _prompt("Lanjutkan pengiriman? (y/n)", default="y")
    if not confirm.lower().startswith("y"):
        return

    submitted = 0
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
                force=force,
            )
            st = res.get("status", "submitted") if res else "error"
            jid = res.get("id") or res.get("job_id", "N/A")
            print(f"  * Ch {ch:<5} -> {fpath.name[:35]:<35} | Status: {st:<10} | Job: {jid}")
            if res and "id" in res:
                submitted += 1
            time.sleep(0.1)
        except Exception as e:
            print(f"  * Ch {ch:<5} -> Error: {e}")

    print(f"\n{GREEN}[+] Batch selesai. {submitted} job baru masuk antrean.{RESET}")
    _pause()


# ─── 4. Jobs & Revision History ───────────────────────────────────────────────


def interactive_jobs():
    while True:
        _header("📋 MANAJEMEN TRANSLATION JOBS & HISTORY")
        print("  [1] Daftar Job (List Jobs dengan filter / paging)")
        print("  [2] Detail Satu Job (Get Job by ID)")
        print("  [3] Batalkan Satu Job (Cancel Job)")
        print("  [4] Batalkan SEMUA Job Pending/Running (Cancel All)")
        print("  [5] Retry Job yang Gagal")
        print("  [6] Hapus Job")
        print("  [7] Lihat Riwayat Revisi Terjemahan (Job History)")
        print("  [8] Rollback / Pulihkan Versi Terjemahan Lama")
        print("  [0] Kembali ke Menu Utama")

        choice = input("\nPilihan [0-8]: ").strip()
        if choice == "0":
            break
        elif choice == "1":
            nov = _prompt("Filter per novel ID (kosongkan untuk semua)", default="")
            st = _prompt("Filter status (pending/running/done/failed/cancelled)", default="")
            page_str = _prompt("Halaman (page)", default="1")
            page = int(page_str) if page_str.isdigit() else 1
            data = list_jobs(novel_id=nov or None, status=st or None, page=page, limit=15)
            if data:
                items = data.get("items", [])
                total = data.get("total", len(items))
                print(f"\nTotal: {total} jobs (Halaman {data.get('page', 1)}):")
                for j in items:
                    jid = j.get("job_id") or j.get("id")
                    st_col = GREEN if j.get("status") == "done" else (RED if j.get("status") == "failed" else YELLOW)
                    print(f"  * [{jid}] {j.get('novel_id')} Ch {j.get('chapter_number')} | Status: {st_col}{j.get('status')}{RESET} | Model: {j.get('model')}")
            else:
                print(f"{RED}Gagal mengambil daftar job.{RESET}")
            _pause()
        elif choice == "2":
            jid = _prompt("Job ID")
            if jid:
                job = get_job(jid)
                if job:
                    print(json.dumps(job, indent=2, ensure_ascii=False))
                else:
                    print(f"{RED}Job tidak ditemukan.{RESET}")
            _pause()
        elif choice == "3":
            jid = _prompt("Job ID yang ingin dibatalkan")
            if jid:
                res = cancel_job(jid)
                print(f"Hasil pembatalan: {res}")
            _pause()
        elif choice == "4":
            st = _prompt("Status target (all/pending/running)", default="all")
            nov = _prompt("Filter novel ID (kosongkan untuk semua)", default="")
            confirm = _prompt("Yakin ingin membatalkan seluruh job target? (y/n)", default="n")
            if confirm.lower().startswith("y"):
                res = cancel_all_jobs(status_filter=st, novel_id=nov or None)
                print(f"{GREEN}[+] Hasil pembatalan massal:{RESET}\n{json.dumps(res, indent=2)}")
            _pause()
        elif choice == "5":
            jid = _prompt("Job ID untuk di-retry")
            if jid:
                res = retry_job(jid)
                print(f"Hasil retry: {res}")
            _pause()
        elif choice == "6":
            jid = _prompt("Job ID untuk dihapus")
            if jid:
                if delete_job(jid):
                    print(f"{GREEN}[+] Job berhasil dihapus.{RESET}")
                else:
                    print(f"{RED}[-] Gagal menghapus job.{RESET}")
            _pause()
        elif choice == "7":
            jid = _prompt("Job ID")
            if jid:
                res = get_job_history(jid)
                if res and "history" in res:
                    hist_list = res["history"]
                    print(f"\nDitemukan {len(hist_list)} versi arsip revisi:\n")
                    for h in hist_list:
                        print(f"  * [History ID: {h.get('id')}] Archived: {h.get('archived_at')}")
                        print(f"    Summary: {h.get('result_summary') or 'N/A'}")
                        print(f"    Preview: {str(h.get('result_translation', ''))[:100]}...\n")
                else:
                    print(f"{YELLOW}Tidak ada riwayat untuk job ini.{RESET}")
            _pause()
        elif choice == "8":
            hid_str = _prompt("History ID yang ingin dipulihkan/rollback")
            if hid_str and hid_str.isdigit():
                res = restore_history(int(hid_str))
                if res:
                    print(f"{GREEN}[+] Terjemahan berhasil di-rollback ke versi History ID {hid_str}!{RESET}")
                    print(json.dumps(res, indent=2))
            _pause()


# ─── 5. Novel, Characters & Glossary ──────────────────────────────────────────


def interactive_novel():
    while True:
        _header("📚 MANAJEMEN NOVEL, KARAKTER & GLOSSARY")
        print("  [1] Daftar Seluruh Novel")
        print("  [2] Statistik Novel (Total Jobs, Characters, Glossary)")
        print("  [3] Daftar Bab Novel (Chapters)")
        print("  [4] Kelola Karakter (Characters CRUD)")
        print("  [5] Kelola Glosarium Istilah (Glossary CRUD, Bulk, Export)")
        print("  [6] Riwayat Revisi Bab per Novel (Novel History)")
        print("  [0] Kembali ke Menu Utama")

        choice = input("\nPilihan [0-6]: ").strip()
        if choice == "0":
            break
        elif choice == "1":
            novels = list_novels()
            print(f"\nTotal Novel Terdaftar: {len(novels)}\n")
            for n in novels:
                print(f"  * {BOLD}{n.get('novel_id')}{RESET:<25} | Total Jobs: {n.get('total_jobs', 0):<5} | Latest Ch: {n.get('latest_chapter')}")
            _pause()
        elif choice == "2":
            nov_id = _prompt("Novel ID")
            if nov_id:
                st = get_novel_stats(nov_id)
                if st:
                    print(json.dumps(st, indent=2))
                else:
                    print(f"{RED}Novel tidak ditemukan.{RESET}")
            _pause()
        elif choice == "3":
            nov_id = _prompt("Novel ID")
            if nov_id:
                res = get_novel_chapters(nov_id)
                if res:
                    print(f"\nBab pada novel '{nov_id}' (Total: {res.get('total', 0)}):")
                    for c in res.get("chapters", []):
                        t_ind = f"{GREEN}[✓ Selesai]{RESET}" if c.get("has_translation") else f"{DIM}[ ]{RESET}"
                        print(f"  Ch {c['chapter_number']:<6} | Status: {c['status']:<9} | {t_ind} {c.get('job_id')}")
            _pause()
        elif choice == "4":
            _interactive_characters()
        elif choice == "5":
            _interactive_glossary()
        elif choice == "6":
            nov_id = _prompt("Novel ID")
            if nov_id:
                ch_str = _prompt("Nomor bab spesifik (kosongkan untuk semua bab)", default="")
                ch = float(ch_str) if ch_str else None
                res = get_novel_history(nov_id, chapter_number=ch)
                if res and "history" in res:
                    print(f"\nRiwayat revisi bab pada novel '{nov_id}':")
                    for h in res["history"]:
                        print(f"  * [Hist #{h.get('id')}] Ch {h.get('chapter_number')} | {h.get('archived_at')} | {h.get('result_summary')}")
            _pause()


def _interactive_characters():
    while True:
        _header("👥 MANAJEMEN KARAKTER (CONTINUITY)")
        print("  [1] Lihat Daftar Karakter")
        print("  [2] Tambah Karakter Baru")
        print("  [3] Detail Satu Karakter")
        print("  [4] Update Karakter")
        print("  [5] Hapus Karakter")
        print("  [0] Kembali")

        c = input("\nPilihan [0-5]: ").strip()
        if c == "0":
            break
        elif c == "1":
            nov_id = _prompt("Novel ID")
            q = _prompt("Kata kunci pencarian (opsional)", default="")
            chars = list_characters(nov_id, q=q or None)
            if isinstance(chars, dict):
                chars = chars.get("items", [])
            print(f"\nDaftar Karakter ({len(chars)} ditemukan):")
            for ch in chars:
                print(f"  * [ID: {ch['id']}] {BOLD}{ch['name']}{RESET} ({ch.get('native_name', '')}) | Gender: {ch.get('gender')} | 1st Ch: {ch.get('first_seen_chapter')}")
                if ch.get("notes"):
                    print(f"    Notes: {ch['notes']}")
            _pause()
        elif c == "2":
            nov_id = _prompt("Novel ID")
            name = _prompt("Nama Karakter (e.g. Han Yujin)")
            native = _prompt("Nama Asli / Native (e.g. 한유진)", default=name)
            gender = _prompt("Gender (male/female/unknown)", default="unknown")
            notes = _prompt("Catatan / Traits", default="")
            f_ch = float(_prompt("Pertama kali muncul di Bab", default="1.0"))
            res = create_character(nov_id, name, native_name=native, gender=gender, notes=notes, first_seen_chapter=f_ch)
            if res:
                print(f"{GREEN}[+] Karakter berhasil ditambahkan:{RESET}\n{json.dumps(res, indent=2)}")
            _pause()
        elif c == "3":
            nov_id = _prompt("Novel ID")
            cid = int(_prompt("Character ID"))
            res = get_character(nov_id, cid)
            if res:
                print(json.dumps(res, indent=2))
            _pause()
        elif c == "4":
            nov_id = _prompt("Novel ID")
            cid = int(_prompt("Character ID"))
            name = _prompt("Nama baru (kosongkan jika tidak ubah)", default="")
            native = _prompt("Native name baru", default="")
            gender = _prompt("Gender baru", default="")
            notes = _prompt("Notes baru", default="")
            res = update_character(
                nov_id,
                cid,
                name=name or None,
                native_name=native or None,
                gender=gender or None,
                notes=notes or None,
            )
            if res:
                print(f"{GREEN}[+] Karakter berhasil diupdate:{RESET}\n{json.dumps(res, indent=2)}")
            _pause()
        elif c == "5":
            nov_id = _prompt("Novel ID")
            cid = int(_prompt("Character ID untuk dihapus"))
            if delete_character(nov_id, cid):
                print(f"{GREEN}[+] Karakter berhasil dihapus.{RESET}")
            else:
                print(f"{RED}[-] Gagal menghapus karakter.{RESET}")
            _pause()


def _interactive_glossary():
    while True:
        _header("📖 MANAJEMEN KAMUS GLOSARIUM")
        print("  [1] Lihat Daftar Istilah (List)")
        print("  [2] Tambah Istilah Baru (Add)")
        print("  [3] Detail Istilah (Get)")
        print("  [4] Update Istilah (Update)")
        print("  [5] Hapus Istilah (Delete)")
        print("  [6] Import Massal dari File JSON (Bulk Import)")
        print("  [7] Ekspor Glosarium (JSON / CSV)")
        print("  [0] Kembali")

        c = input("\nPilihan [0-7]: ").strip()
        if c == "0":
            break
        elif c == "1":
            nov_id = _prompt("Novel ID")
            q = _prompt("Kata kunci pencarian (opsional)", default="")
            gloss = list_glossary(nov_id, q=q or None)
            if isinstance(gloss, dict):
                gloss = gloss.get("items", [])
            print(f"\nDaftar Glosarium ({len(gloss)} istilah):")
            for g in gloss:
                print(f"  * [ID: {g['id']}] {BOLD}{g['term_source']}{RESET} -> {GREEN}{g['term_translation']}{RESET} (Ch: {g.get('first_seen_chapter')})")
                if g.get("notes"):
                    print(f"    Notes: {g['notes']}")
            _pause()
        elif c == "2":
            nov_id = _prompt("Novel ID")
            src = _prompt("Istilah Asal / Source Term")
            trans = _prompt("Terjemahan Istilah / Translation")
            notes = _prompt("Catatan Penggunaan", default="")
            f_ch = float(_prompt("Pertama muncul di Bab", default="1.0"))
            res = create_glossary(nov_id, src, trans, notes=notes, first_seen_chapter=f_ch)
            if res:
                print(f"{GREEN}[+] Istilah berhasil ditambahkan:{RESET}\n{json.dumps(res, indent=2)}")
            _pause()
        elif c == "3":
            nov_id = _prompt("Novel ID")
            gid = int(_prompt("Glossary ID"))
            res = get_glossary(nov_id, gid)
            if res:
                print(json.dumps(res, indent=2))
            _pause()
        elif c == "4":
            nov_id = _prompt("Novel ID")
            gid = int(_prompt("Glossary ID"))
            src = _prompt("Source term baru", default="")
            trans = _prompt("Translation baru", default="")
            notes = _prompt("Notes baru", default="")
            res = update_glossary(nov_id, gid, term_source=src or None, term_translation=trans or None, notes=notes or None)
            if res:
                print(f"{GREEN}[+] Istilah berhasil diupdate.{RESET}")
            _pause()
        elif c == "5":
            nov_id = _prompt("Novel ID")
            gid = int(_prompt("Glossary ID untuk dihapus"))
            if delete_glossary(nov_id, gid):
                print(f"{GREEN}[+] Istilah berhasil dihapus.{RESET}")
            _pause()
        elif c == "6":
            nov_id = _prompt("Novel ID")
            fp_str = _prompt("Path file JSON berisi daftar istilah")
            fp = Path(fp_str.strip('"').strip("'"))
            if fp.is_file():
                data = json.loads(fp.read_text(encoding="utf-8"))
                terms = data.get("terms", data) if isinstance(data, dict) else data
                res = bulk_import_glossary(nov_id, terms)
                print(f"{GREEN}[+] Bulk import berhasil:{RESET}\n{json.dumps(res, indent=2)}")
            _pause()
        elif c == "7":
            nov_id = _prompt("Novel ID")
            fmt = _prompt("Format ekspor (json/csv)", default="json")
            out = _prompt("Simpan ke file (opsional)", default=f"{nov_id}_glossary.{fmt}")
            res = export_glossary(nov_id, export_format=fmt, output_path=out)
            print(f"{GREEN}[+] Glosarium berhasil diekspor ke: {out}{RESET}")
            _pause()


# ─── 6. Cookie & Multi-Account Management ─────────────────────────────────────


def interactive_cookies():
    while True:
        _header("🍪 MANAJEMEN AKUN COOKIE & PLAYWRIGHT CONTEXT")
        print("  [1] Daftar Akun Cookie & Status Cooldown")
        print("  [2] Tambah Akun Cookie Baru (dari file JSON)")
        print("  [3] Jeda Akun (Pause)")
        print("  [4] Lanjutkan Akun (Resume)")
        print("  [5] Refresh Context Browser (Bersihkan cache & re-create context)")
        print("  [6] Reset Cooldown Akun Secara Instan")
        print("  [7] Hapus Akun Cookie")
        print("  [0] Kembali ke Menu Utama")

        c = input("\nPilihan [0-7]: ").strip()
        if c == "0":
            break
        elif c == "1":
            data = list_cookies()
            if data:
                accs = data.get("accounts", [])
                print(f"\nTotal Akun Tersimpan: {len(accs)}\n")
                for a in accs:
                    cd = a.get("cooldown_remaining_seconds")
                    cd_str = f" {CYAN}(Cooldown {cd}s){RESET}" if cd else ""
                    st_col = GREEN if a.get("status") == "ACTIVE" else (YELLOW if a.get("status") == "PAUSED" else RED)
                    print(f"ID: {BOLD}{a['id']}{RESET}")
                    print(f"  Name: {a['name']:<15} | Status: {st_col}{a['status']}{RESET}{cd_str} | Jobs Processed: {a.get('total_jobs_processed', 0)}")
                    print(f"  Last Used: {a.get('last_used_at', 'Never')} | Cooldown Count: {a.get('cooldown_count', 0)}")
                    print("-" * 50)
            _pause()
        elif c == "2":
            name = _prompt("Nama Akun (e.g. user2)")
            provider = _prompt("Provider", default="chatgpt")
            file_str = _prompt("Path file cookie JSON")
            fp = Path(file_str.strip('"').strip("'"))
            if fp.is_file():
                raw = fp.read_text(encoding="utf-8")
                try:
                    cdata = json.loads(raw)
                    if isinstance(cdata, dict) and "cookies" in cdata:
                        cdata = cdata["cookies"]
                except Exception:
                    cdata = raw
                res = add_cookie(name, provider, cdata)
                if res:
                    print(f"{GREEN}[+] Akun '{name}' berhasil disimpan dan diregistrasi ke pool!{RESET}")
            _pause()
        elif c == "3":
            aid = _prompt("Account ID untuk di-pause")
            if aid and pause_cookie(aid):
                print(f"{YELLOW}[+] Akun ID '{aid}' dijeda (PAUSED).{RESET}")
            _pause()
        elif c == "4":
            aid = _prompt("Account ID untuk di-resume")
            if aid and resume_cookie(aid):
                print(f"{GREEN}[+] Akun ID '{aid}' diaktifkan kembali (ACTIVE).{RESET}")
            _pause()
        elif c == "5":
            aid = _prompt("Account ID untuk di-refresh context browser-nya")
            if aid:
                res = refresh_cookie(aid)
                if res:
                    print(f"{GREEN}[+] Context browser berhasil di-refresh:{RESET} {res.get('message')}")
            _pause()
        elif c == "6":
            aid = _prompt("Account ID untuk reset cooldown")
            if aid:
                res = reset_cookie_cooldown(aid)
                if res:
                    print(f"{GREEN}[+] Cooldown akun '{aid}' berhasil di-reset ke 0!{RESET}")
            _pause()
        elif c == "7":
            aid = _prompt("Account ID untuk dihapus")
            if aid and delete_cookie(aid):
                print(f"{GREEN}[+] Akun ID '{aid}' berhasil dihapus.{RESET}")
            _pause()


# ─── 7. Settings Management ───────────────────────────────────────────────────


def interactive_settings():
    while True:
        _header("⚙️ PENGATURAN SERVER HERMES (DYNAMIC SETTINGS)")
        print("  [1] Lihat Seluruh Pengaturan Aktif")
        print("  [2] Ubah Nilai Pengaturan (Update Setting)")
        print("  [3] Reset Pengaturan ke Default Pabrik")
        print("  [0] Kembali ke Menu Utama")

        c = input("\nPilihan [0-3]: ").strip()
        if c == "0":
            break
        elif c == "1":
            st = get_settings()
            if st:
                print(f"\n{BOLD}Pengaturan Runtime Hermes Server:{RESET}")
                for k, v in st.items():
                    print(f"  * {k:<30}: {GREEN}{v}{RESET}")
            _pause()
        elif c == "2":
            key = _prompt("Nama Setting (e.g. job_cooldown_seconds, worker_poll_interval, translation_job_timeout)")
            val = _prompt("Nilai Baru")
            if key and val:
                try:
                    num_val = float(val) if "." in val else int(val)
                except ValueError:
                    num_val = val
                res = update_settings({key: num_val})
                if res:
                    print(f"{GREEN}[+] Pengaturan berhasil diperbarui:{RESET}\n{json.dumps(res, indent=2)}")
            _pause()
        elif c == "3":
            confirm = _prompt("Yakin ingin mereset seluruh settings ke default? (y/n)", default="n")
            if confirm.lower().startswith("y"):
                res = reset_settings()
                if res:
                    print(f"{GREEN}[+] Settings berhasil di-reset ke default:{RESET}\n{json.dumps(res, indent=2)}")
            _pause()


# ─── 8. Database Backup & Restore ─────────────────────────────────────────────


def interactive_database():
    while True:
        _header("💾 SISTEM BACKUP & RESTORE DATABASE SQLITE")
        print("  [1] Lihat Statistik Baris Seluruh Tabel Database")
        print("  [2] Unduh Snapshot Cadangan Database (.zip)")
        print("  [3] Pulihkan / Restore Database dari File Backup (.zip)")
        print("  [0] Kembali ke Menu Utama")

        c = input("\nPilihan [0-3]: ").strip()
        if c == "0":
            break
        elif c == "1":
            st = get_database_stats()
            if st:
                print(f"\n{BOLD}Statistik Jumlah Baris Tabel Hermes SQLite:{RESET}")
                for k, v in st.items():
                    print(f"  * {k:<25}: {GREEN}{v:,}{RESET} rows")
            _pause()
        elif c == "2":
            ts = time.strftime("%Y%m%d_%H%M%S")
            def_path = f"hermes_backup_{ts}.zip"
            out = _prompt("Simpan file backup ke path", default=def_path)
            print(f"[*] Mengunduh snapshot backup database dari server...")
            ok, path_or_err, bytes_sz = backup_database(output_path=out)
            if ok:
                mb = bytes_sz / (1024 * 1024)
                print(f"\n{GREEN}{BOLD}[+] Backup Berhasil Disimpan!{RESET}")
                print(f"    - File : {BOLD}{path_or_err}{RESET}")
                print(f"    - Size : {bytes_sz:,} bytes ({mb:.2f} MB)")
            else:
                print(f"{RED}[-] Backup gagal: {path_or_err}{RESET}")
            _pause()
        elif c == "3":
            fp_str = _prompt("Path file backup ZIP yang akan di-restore")
            fp = Path(fp_str.strip('"').strip("'"))
            if not fp.is_file():
                print(f"{RED}File tidak ditemukan: {fp}{RESET}")
                _pause()
                continue
            confirm = _prompt(f"{YELLOW}PERINGATAN: Database server akan ditimpa dengan isi file backup ini. Lanjutkan? (y/n){RESET}", default="n")
            if not confirm.lower().startswith("y"):
                continue

            print(f"[*] Mengunggah dan merestore database dari {fp.name}...")
            res = restore_database(file_path=fp)
            if res:
                print(f"\n{GREEN}{BOLD}[+] Database Berhasil Dipulihkan!{RESET}")
                print(f"    Pesan         : {res.get('message')}")
                print(f"    Tabel Restored: {res.get('tables_restored')}")
                if "stats_after_restore" in res:
                    print(f"\nStatistik Tabel Setelah Restore:")
                    for k, v in res["stats_after_restore"].items():
                        print(f"      - {k:<20}: {v}")
            _pause()


# ─── 9. Interactive Chat ──────────────────────────────────────────────────────


def interactive_chat_session():
    _header("💬 INTERACTIVE CHAT TERMINAL")
    server_url = config.get_base_url()
    print(f"Target Server : {server_url}")
    model = _prompt("Model LLM", default=config.DEFAULT_MODEL)
    print(f"\n{DIM}Ketik 'exit' atau 'quit' untuk kembali ke menu.{RESET}\n")

    while True:
        try:
            prompt = input(f"{BOLD}You:{RESET} ").strip()
            if prompt.lower() in ("exit", "quit", "kembali"):
                break
            if not prompt:
                continue
            print(f"{DIM}[*] Menunggu respons...{RESET}")
            res = chat_completion(model=model, prompt=prompt)
            if res:
                print(f"\n{GREEN}{BOLD}Assistant:{RESET}\n{res}\n")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting chat...")
            break


# ─── 10. Server Connection & Config ───────────────────────────────────────────


def interactive_change_config():
    _header("🌐 PENGATURAN KONEKSI SERVER & CLIENT CONFIG")
    cur_server = config.get_base_url()
    print(f"Server URL Saat Ini : {BOLD}{cur_server}{RESET}")
    print(f"Internal Key        : {BOLD}{config.INTERNAL_KEY or '(none)'}{RESET}")
    print(f"Default Model       : {BOLD}{config.DEFAULT_MODEL}{RESET}")
    print(f"Default Source Lang : {BOLD}{config.DEFAULT_SOURCE_LANG}{RESET}")
    print(f"Default Target Lang : {BOLD}{config.DEFAULT_TARGET_LANG}{RESET}")
    print(f"Request Timeout     : {BOLD}{config.DEFAULT_TIMEOUT}s{RESET}")

    new_srv = _prompt("\nServer URL Baru (e.g. http://192.168.1.50:18111 atau kosongkan)", default=cur_server)
    new_key = _prompt("Internal Key (X-Internal-Key)", default=config.INTERNAL_KEY)
    new_mod = _prompt("Default Model", default=config.DEFAULT_MODEL)
    new_src = _prompt("Default Source Lang", default=config.DEFAULT_SOURCE_LANG)
    new_tgt = _prompt("Default Target Lang", default=config.DEFAULT_TARGET_LANG)

    set_config(
        server=new_srv,
        internal_key=new_key,
        default_model=new_mod,
        source_lang=new_src,
        target_lang=new_tgt,
    )

    print(f"\n{GREEN}[+] Konfigurasi aktif berhasil diperbarui!{RESET}")
    _pause()


# ─── Main Menu Loop ───────────────────────────────────────────────────────────


def run_interactive_menu():
    """Main interactive terminal dashboard loop."""
    while True:
        server_url = config.get_base_url()
        health = check_health()
        status_badge = f"{GREEN}[ONLINE]{RESET}" if (health and health.get("ok")) else f"{RED}[OFFLINE]{RESET}"

        print("\n" + "=" * 65)
        print(f"  🌟 {BOLD}{CYAN}HERMES NOVEL TRANSLATION CLIENT{RESET} - Interactive Terminal")
        print("=" * 65)
        print(f"  📡 Server Target : {BOLD}{server_url}{RESET} {status_badge}")
        print(f"  🧠 Default Model : {BOLD}{config.DEFAULT_MODEL}{RESET} | Lang: {config.DEFAULT_SOURCE_LANG} -> {config.DEFAULT_TARGET_LANG}")
        print("=" * 65)
        print(f"  {CYAN}[1]{RESET}  📊 Status Server & Worker Pool Cooldowns")
        print(f"  {CYAN}[2]{RESET}  🚀 Terjemahkan Satu Bab Novel (File / Text)")
        print(f"  {CYAN}[3]{RESET}  📑 Batch Ingestion Seluruh Bab Folder Novel")
        print(f"  {CYAN}[4]{RESET}  📋 Manajemen Jobs & Riwayat Revisi (History/Rollback)")
        print(f"  {CYAN}[5]{RESET}  📚 Manajemen Novel (Karakter, Glossary, Chapters, Stats)")
        print(f"  {CYAN}[6]{RESET}  🍪 Akun Cookie & Browser Context (Refresh/Cooldown/CRUD)")
        print(f"  {CYAN}[7]{RESET}  ⚙️ Pengaturan Server Dinamis (/settings)")
        print(f"  {CYAN}[8]{RESET}  💾 Backup & Restore Database SQLite Hermes")
        print(f"  {CYAN}[9]{RESET}  💬 Interactive Terminal Chat (ChatGPT Web Backend)")
        print(f"  {CYAN}[10]{RESET} 🌐 Ganti Alamat Server URL / Model / Config")
        print(f"  {DIM}[0]  🚪 Keluar (Exit){RESET}")
        print("=" * 65)

        try:
            choice = input(f"{BOLD}Pilih menu [0-10]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{CYAN}Sampai jumpa!{RESET}")
            sys.exit(0)

        if choice == "0" or choice.lower() in ("exit", "quit", "q"):
            print(f"\n{CYAN}Keluar dari Hermes Client. Sampai jumpa!{RESET}\n")
            break
        elif choice == "1":
            interactive_status()
        elif choice == "2":
            interactive_translate()
        elif choice == "3":
            interactive_batch()
        elif choice == "4":
            interactive_jobs()
        elif choice == "5":
            interactive_novel()
        elif choice == "6":
            interactive_cookies()
        elif choice == "7":
            interactive_settings()
        elif choice == "8":
            interactive_database()
        elif choice == "9":
            interactive_chat_session()
        elif choice == "10":
            interactive_change_config()
        else:
            print(f"{YELLOW}Pilihan tidak valid. Silakan pilih 0-10.{RESET}")
            time.sleep(0.8)
