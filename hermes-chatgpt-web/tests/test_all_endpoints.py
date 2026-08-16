import asyncio
import io
import json
import os
import tempfile
import zipfile

# Setup temp DB for tests
temp_dir = tempfile.TemporaryDirectory()
os.environ["CHATGPT_HOME"] = temp_dir.name
os.environ["HERMES_SKIP_BROWSER"] = "1"

import httpx  # noqa: E402

from hermes_chatgpt_web.api.app import app  # noqa: E402
from hermes_chatgpt_web.translation.database import init_db  # noqa: E402


async def run_tests():
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        print("Testing startup and health...")
        # Health endpoint
        res = await client.get("/health")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        assert res.json()["ok"] is True

        # Models endpoint
        res = await client.get("/v1/models")
        assert res.status_code == 200
        assert len(res.json()["data"]) >= 1

        print("Testing /translate endpoint...")
        # Create job
        payload = {
            "model": "gpt-5.6-luna",
            "source_lang": "ja",
            "target_lang": "id",
            "novel_id": "overlord",
            "chapter_number": 1.0,
            "text": "モモンガは玉座に座っていた。",
            "force": False,
        }
        res = await client.post("/translate", json=payload)
        assert res.status_code == 202, f"Expected 202, got {res.status_code}: {res.text}"
        job_data = res.json()
        job_id = job_data["id"]
        assert job_data["status"] == "pending"

        # Duplicate without force -> 409
        res_dup = await client.post("/translate", json=payload)
        assert res_dup.status_code == 409
        assert res_dup.json()["error"] == "chapter_already_translated"

        # Get job status
        res_get = await client.get(f"/translate/{job_id}")
        assert res_get.status_code == 200
        assert res_get.json()["job_id"] == job_id
        assert res_get.json()["status"] == "pending"

        # List jobs
        res_list = await client.get("/translate?novel_id=overlord")
        assert res_list.status_code == 200
        assert res_list.json()["total"] == 1
        assert len(res_list.json()["items"]) == 1

        print("Testing Characters CRUD...")
        # Add character
        char_payload = {
            "name": "Momonga",
            "native_name": "モモンガ",
            "gender": "male",
            "notes": "Protagonis utama",
            "first_seen_chapter": 1.0,
        }
        res_char = await client.post("/novels/overlord/characters", json=char_payload)
        assert res_char.status_code == 201
        char_resp = res_char.json()
        char_id = char_resp["id"]
        assert char_resp["appeared_chapters"] == [1.0]

        # Duplicate character -> 409
        res_char_dup = await client.post("/novels/overlord/characters", json=char_payload)
        assert res_char_dup.status_code == 409
        assert res_char_dup.json()["error"] == "character_already_exists"

        # Get characters
        res_chars = await client.get("/novels/overlord/characters")
        assert res_chars.status_code == 200
        assert res_chars.json()["total"] == 1
        assert res_chars.json()["items"][0]["appeared_chapters"] == [1.0]

        # Update character
        res_char_up = await client.put(
            f"/novels/overlord/characters/{char_id}",
            json={"notes": "Updated note", "appeared_chapters": [1.0, 2.0]},
        )
        assert res_char_up.status_code == 200
        assert res_char_up.json()["notes"] == "Updated note"
        assert res_char_up.json()["appeared_chapters"] == [1.0, 2.0]

        # Test upsert_characters logic (recurring character with note & appeared_chapters)
        from hermes_chatgpt_web.translation.database import get_character, upsert_characters
        await upsert_characters(
            novel_id="overlord",
            chapter_number=3.0,
            characters=[
                {"name": "Momonga", "notes": "Momonga memakai jubah baru", "status": "updated"},
                {"name": "Shalltear", "native_name": "シャルティア", "gender": "female", "notes": "Vampir", "status": "new"},
            ],
        )
        momonga_data = await get_character("overlord", char_id)
        assert momonga_data is not None
        assert momonga_data["last_updated_chapter"] == 3.0
        assert momonga_data["appeared_chapters"] == [1.0, 2.0, 3.0]
        assert "[Ch3] Momonga memakai jubah baru" in momonga_data["notes"]

        # Recurring shalltear in chapter 4 with no notes -> check appeared_chapters updated
        await upsert_characters(
            novel_id="overlord",
            chapter_number=4.0,
            characters=[{"name": "Shalltear", "notes": ""}],
        )
        res_shall = await client.get("/novels/overlord/characters?q=Shalltear")
        shall_item = res_shall.json()["items"][0]
        assert shall_item["last_updated_chapter"] == 4.0
        assert shall_item["appeared_chapters"] == [3.0, 4.0]

        # Test gender upgrade from unknown to detected gender
        await upsert_characters(
            novel_id="overlord",
            chapter_number=1.0,
            characters=[{"name": "Mare", "native_name": "マーレ", "gender": "unknown", "notes": "Dark Elf"}],
        )
        mare_res1 = (await client.get("/novels/overlord/characters?q=Mare")).json()["items"][0]
        assert mare_res1["gender"] == "unknown"

        # Now next chapter chat response detects Mare as male
        await upsert_characters(
            novel_id="overlord",
            chapter_number=2.0,
            characters=[{"name": "Mare", "gender": "male", "notes": "Twin brother"}],
        )
        mare_res2 = (await client.get("/novels/overlord/characters?q=Mare")).json()["items"][0]
        assert mare_res2["gender"] == "male"

        # If later chat response says unknown, it preserves detected "male"
        await upsert_characters(
            novel_id="overlord",
            chapter_number=3.0,
            characters=[{"name": "Mare", "gender": "unknown"}],
        )
        mare_res3 = (await client.get("/novels/overlord/characters?q=Mare")).json()["items"][0]
        assert mare_res3["gender"] == "male"
        # Clean up Mare so downstream stats assertions match
        await client.delete(f"/novels/overlord/characters/{mare_res1['id']}")

        print("Testing Glossary CRUD...")
        # Add glossary term
        gloss_payload = {
            "term_source": "YGGDRASIL",
            "term_translation": "Yggdrasil",
            "notes": "Game",
            "first_seen_chapter": 1.0,
        }
        res_gloss = await client.post("/novels/overlord/glossary", json=gloss_payload)
        assert res_gloss.status_code == 201
        res_gloss.json()["id"]

        # Bulk glossary
        bulk_payload = {
            "terms": [
                {"term_source": "YGGDRASIL", "term_translation": "Yggdrasil Online", "notes": "Updated"},
                {"term_source": "Guild", "term_translation": "Guild", "notes": "Organisasi"},
            ],
            "first_seen_chapter": 1.0,
        }
        res_bulk = await client.post("/novels/overlord/glossary/bulk", json=bulk_payload)
        assert res_bulk.status_code == 200
        assert res_bulk.json()["inserted"] == 1
        assert res_bulk.json()["updated"] == 1

        # Export glossary JSON
        res_exp = await client.get("/novels/overlord/glossary/export?format=json")
        assert res_exp.status_code == 200
        assert len(res_exp.json()["terms"]) == 2

        # Export glossary CSV
        res_csv = await client.get("/novels/overlord/glossary/export?format=csv")
        assert res_csv.status_code == 200
        assert "text/csv" in res_csv.headers["content-type"]

        print("Testing Novels stats and context...")
        # Novels list
        res_novels = await client.get("/novels")
        assert res_novels.status_code == 200
        assert len(res_novels.json()["novels"]) == 1

        # Novel stats
        res_stats = await client.get("/novels/overlord/stats")
        assert res_stats.status_code == 200
        assert res_stats.json()["total_jobs"] == 1
        assert res_stats.json()["total_characters"] == 2
        assert res_stats.json()["total_glossary_terms"] == 2

        # Novel context
        res_ctx = await client.get("/novels/overlord/context")
        assert res_ctx.status_code == 200
        assert len(res_ctx.json()["characters"]) == 2
        assert len(res_ctx.json()["glossary"]) == 2

        print("Testing worker endpoints...")
        # Worker get next job
        res_next = await client.get("/worker/jobs/next")
        assert res_next.status_code == 200
        claimed_job = res_next.json()
        assert claimed_job["job_id"] == job_id

        # Worker update status to done
        status_update = {
            "status": "done",
            "result_translation": "Bab 1: Momonga duduk di takhta.",
            "result_summary": "Summary bab 1",
            "raw_response": '{"translation": "...", "chapter_summary": "..."}',
            "cleaned_response": '{"translation": "...", "chapter_summary": "..."}',
        }
        res_status_patch = await client.patch(f"/worker/jobs/{job_id}/status", json=status_update)
        assert res_status_patch.status_code == 200

        # Verify done
        res_done = await client.get(f"/translate/{job_id}")
        assert res_done.status_code == 200
        assert res_done.json()["status"] == "done"
        assert res_done.json()["result"]["translation"] == "Bab 1: Momonga duduk di takhta."

        # Test force re-translate archiving
        res_force = await client.post("/translate", json={**payload, "force": True})
        assert res_force.status_code == 202
        new_job_id = res_force.json()["id"]

        # History verification
        res_hist = await client.get(f"/translate/{new_job_id}/history")
        assert res_hist.status_code == 200
        assert len(res_hist.json()["history"]) == 1
        hist_id = res_hist.json()["history"][0]["id"]

        # Restore history
        res_rest = await client.post(f"/history/{hist_id}/restore")
        assert res_rest.status_code == 200
        assert res_rest.json()["ok"] is True

        print("Testing job cancellation & deletion...")
        # Verify done job is not cancellable
        res_cancel_done = await client.post(f"/translate/{new_job_id}/cancel")
        assert res_cancel_done.status_code == 400
        assert res_cancel_done.json()["error"] == "job_not_cancellable"

        # Create a new pending job for cancellation test
        payload_cancel = {
            "model": "gpt-5.6-luna",
            "source_lang": "ja",
            "target_lang": "id",
            "novel_id": "overlord_v2",
            "chapter_number": 1.0,
            "text": "モモンガの物語。",
        }
        res_pending = await client.post("/translate", json=payload_cancel)
        assert res_pending.status_code == 202
        pending_job_id = res_pending.json()["id"]

        # Cancel pending job
        res_cancel = await client.post(f"/translate/{pending_job_id}/cancel")
        assert res_cancel.status_code == 200
        assert res_cancel.json()["status"] == "cancelled"

        # Delete cancelled job
        res_del = await client.delete(f"/translate/{pending_job_id}")
        assert res_del.status_code == 200
        assert res_del.json()["status"] == "deleted"

        print("Testing bulk job cancellation (POST /translate/cancel-all)...")
        # 1. Test invalid status filter -> 400
        res_invalid = await client.post("/translate/cancel-all?status=invalid_status")
        assert res_invalid.status_code == 400
        assert res_invalid.json()["error"] == "invalid_status_filter"

        # 2. Test cancel pending only
        p1 = await client.post("/translate", json={"model": "gpt-5.6-luna", "source_lang": "ja", "target_lang": "id", "novel_id": "bulk_novel", "chapter_number": 1.0, "text": "テキスト1"})
        p2 = await client.post("/translate", json={"model": "gpt-5.6-luna", "source_lang": "ja", "target_lang": "id", "novel_id": "bulk_novel", "chapter_number": 2.0, "text": "テキスト2"})
        assert p1.status_code == 202 and p2.status_code == 202
        j1_id = p1.json()["id"]
        j2_id = p2.json()["id"]

        res_cancel_pending = await client.post("/translate/cancel-all?status=pending&novel_id=bulk_novel")
        assert res_cancel_pending.status_code == 200
        pending_data = res_cancel_pending.json()
        assert pending_data["status"] == "cancelled"
        assert pending_data["cancelled_count"] == 2
        assert pending_data["cancelled_pending"] == 2
        assert pending_data["cancelled_running"] == 0
        assert j1_id in pending_data["cancelled_job_ids"]
        assert j2_id in pending_data["cancelled_job_ids"]

        # Verify their status is now cancelled
        chk1 = await client.get(f"/translate/{j1_id}")
        assert chk1.json()["status"] == "cancelled"

        # 3. Test cancel running only & both via JSON body
        # Seed jobs and claim one to make it 'running'
        p3 = await client.post("/translate", json={"model": "gpt-5.6-luna", "source_lang": "ja", "target_lang": "id", "novel_id": "bulk_novel_2", "chapter_number": 1.0, "text": "テキスト3"})
        p4 = await client.post("/translate", json={"model": "gpt-5.6-luna", "source_lang": "ja", "target_lang": "id", "novel_id": "bulk_novel_2", "chapter_number": 2.0, "text": "テキスト4"})
        j3_id = p3.json()["id"]
        j4_id = p4.json()["id"]

        # Claim j3 so its status becomes 'running'
        res_claim = await client.get("/worker/jobs/next")
        assert res_claim.status_code == 200
        assert res_claim.json()["job_id"] == j3_id
        chk_running = await client.get(f"/translate/{j3_id}")
        assert chk_running.json()["status"] == "running"

        # Cancel running only for bulk_novel_2
        res_cancel_running = await client.post("/translate/cancel-all", json={"status": "running", "novel_id": "bulk_novel_2"})
        assert res_cancel_running.status_code == 200
        running_data = res_cancel_running.json()
        assert running_data["cancelled_count"] == 1
        assert running_data["cancelled_running"] == 1
        assert running_data["cancelled_pending"] == 0
        assert j3_id in running_data["cancelled_job_ids"]

        # Now cancel all (pending + running) for bulk_novel_2
        res_cancel_all = await client.post("/translate/cancel-all", json={"status": "all", "novel_id": "bulk_novel_2"})
        assert res_cancel_all.status_code == 200
        all_data = res_cancel_all.json()
        assert all_data["cancelled_count"] == 1
        assert all_data["cancelled_pending"] == 1
        assert j4_id in all_data["cancelled_job_ids"]

        # Clean up test jobs
        for j_id in [j1_id, j2_id, j3_id, j4_id]:
            await client.delete(f"/translate/{j_id}")

        print("Testing /novels/{novel_id}/chapters endpoint...")
        # Create chapter 2 for overlord
        payload_ch2 = {
            "model": "gpt-5.6-luna",
            "source_lang": "ja",
            "target_lang": "id",
            "novel_id": "overlord",
            "chapter_number": 2.0,
            "text": "第2章:カルネ村の戦い",
            "force": False,

        }
        res_ch2 = await client.post("/translate", json=payload_ch2)
        assert res_ch2.status_code == 202

        # Get all chapters of novel
        res_chapters = await client.get("/novels/overlord/chapters")
        assert res_chapters.status_code == 200
        ch_data = res_chapters.json()
        assert ch_data["novel_id"] == "overlord"
        assert ch_data["total"] == 2
        assert len(ch_data["chapters"]) == 2
        assert ch_data["chapters"][0]["chapter_number"] == 1.0
        assert ch_data["chapters"][1]["chapter_number"] == 2.0

        # Filter chapters by status
        res_ch_done = await client.get("/novels/overlord/chapters?status=done")
        assert res_ch_done.status_code == 200
        assert res_ch_done.json()["total"] == 1
        assert res_ch_done.json()["chapters"][0]["chapter_number"] == 1.0

        res_ch_pending = await client.get("/novels/overlord/chapters?status=pending")
        assert res_ch_pending.status_code == 200
        assert res_ch_pending.json()["total"] == 1
        assert res_ch_pending.json()["chapters"][0]["chapter_number"] == 2.0

        # Sort chapters descending
        res_ch_desc = await client.get("/novels/overlord/chapters?sort=chapter_number:desc")
        assert res_ch_desc.status_code == 200
        assert res_ch_desc.json()["chapters"][0]["chapter_number"] == 2.0
        assert res_ch_desc.json()["chapters"][1]["chapter_number"] == 1.0

        print("Testing field filtering (fields=...) across crucial endpoints...")
        # 1. Chapters field filtering
        res_ch_f = await client.get("/novels/overlord/chapters?fields=chapter_number,status,has_translation")
        assert res_ch_f.status_code == 200
        item0 = res_ch_f.json()["chapters"][0]
        assert set(item0.keys()) == {"chapter_number", "status", "has_translation"}

        # 2. Translate list field filtering
        res_tr_f = await client.get("/translate?novel_id=overlord&fields=job_id,chapter_number,status")
        assert res_tr_f.status_code == 200
        assert set(res_tr_f.json()["items"][0].keys()) == {"job_id", "chapter_number", "status"}

        # 3. Translate detail field filtering (with nested dot notation)
        res_tr_det_f = await client.get(f"/translate/{new_job_id}?fields=job_id,status,result.translation")
        assert res_tr_det_f.status_code == 200
        det_data = res_tr_det_f.json()
        assert "job_id" in det_data
        assert "status" in det_data
        assert "result" in det_data
        assert "translation" in det_data["result"]
        assert "translate_md" not in det_data["result"]
        assert "raw_response" not in det_data

        # 4. Novels list field filtering
        res_nov_f = await client.get("/novels?fields=novel_id,latest_chapter")
        assert res_nov_f.status_code == 200
        assert set(res_nov_f.json()["novels"][0].keys()) == {"novel_id", "latest_chapter"}

        # 5. Novel stats field filtering
        res_stats_f = await client.get("/novels/overlord/stats?fields=novel_id,total_jobs")
        assert res_stats_f.status_code == 200
        assert set(res_stats_f.json().keys()) == {"novel_id", "total_jobs"}

        # 6. Novel jobs field filtering
        res_njobs_f = await client.get("/novels/overlord/jobs?fields=job_id,chapter_number")
        assert res_njobs_f.status_code == 200
        assert set(res_njobs_f.json()["items"][0].keys()) == {"job_id", "chapter_number"}

        # 7. Characters list field filtering
        res_chars_f = await client.get("/novels/overlord/characters?fields=id,name")
        assert res_chars_f.status_code == 200
        assert set(res_chars_f.json()["items"][0].keys()) == {"id", "name"}

        # 8. Character detail field filtering
        res_char_det_f = await client.get(f"/novels/overlord/characters/{char_id}?fields=name,gender")
        assert res_char_det_f.status_code == 200
        assert set(res_char_det_f.json().keys()) == {"name", "gender"}

        # 9. Glossary list field filtering
        res_gloss_f = await client.get("/novels/overlord/glossary?fields=id,term_source")
        assert res_gloss_f.status_code == 200
        assert set(res_gloss_f.json()["items"][0].keys()) == {"id", "term_source"}

        # 10. Glossary detail field filtering
        res_gloss_det_f = await client.get(f"/novels/overlord/glossary/{res_gloss.json()['id']}?fields=term_source,term_translation")
        assert res_gloss_det_f.status_code == 200
        assert set(res_gloss_det_f.json().keys()) == {"term_source", "term_translation"}

        print("Testing Database Backup and Restore...")
        # 1. Database stats before backup
        res_stats_db = await client.get("/database/stats")
        assert res_stats_db.status_code == 200
        stats_before = res_stats_db.json()["stats"]
        assert stats_before["translation_jobs"] >= 1
        assert stats_before["characters"] >= 1
        assert stats_before["glossary"] >= 1

        # 2. Database Backup (GET /database/backup)
        res_backup = await client.get("/database/backup")
        assert res_backup.status_code == 200
        assert res_backup.headers["content-type"] == "application/zip"
        assert "Content-Disposition" in res_backup.headers
        zip_bytes = res_backup.content
        assert len(zip_bytes) > 0

        # Inspect ZIP content
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            namelist = zf.namelist()
            assert "translation.db" in namelist
            assert "metadata.json" in namelist
            meta_json = json.loads(zf.read("metadata.json").decode("utf-8"))
            assert meta_json["stats"]["translation_jobs"] == stats_before["translation_jobs"]

        # 3. Modify / Add additional data in DB
        char_payload_2 = {
            "name": "Albedo",
            "native_name": "アルベド",
            "gender": "female",
            "notes": "Guardian Overseer",
            "first_seen_chapter": 1.0,
        }
        res_char_2 = await client.post("/novels/overlord/characters", json=char_payload_2)
        assert res_char_2.status_code == 201

        res_stats_mod = await client.get("/database/stats")
        assert res_stats_mod.json()["stats"]["characters"] == stats_before["characters"] + 1

        # 4. Restore database using multipart/form-data upload
        files = {"file": ("backup.zip", zip_bytes, "application/zip")}
        res_restore = await client.post("/database/restore", files=files)
        assert res_restore.status_code == 200
        restore_result = res_restore.json()
        assert restore_result["ok"] is True
        assert restore_result["stats"]["characters"] == stats_before["characters"]

        # Verify that Albedo character is gone (restored to previous state)
        res_chars_restored = await client.get("/novels/overlord/characters")
        assert res_chars_restored.status_code == 200
        char_names = [c["name"] for c in res_chars_restored.json()["items"]]
        assert "Momonga" in char_names
        assert "Albedo" not in char_names

        # 5. Restore database using binary body payload
        headers = {"Content-Type": "application/zip"}
        res_restore_bin = await client.post("/database/restore", content=zip_bytes, headers=headers)
        assert res_restore_bin.status_code == 200
        assert res_restore_bin.json()["ok"] is True

        # 6. Test restore error validation (corrupt / invalid data)
        res_invalid = await client.post("/database/restore", content=b"this is not a zip file", headers=headers)
        assert res_invalid.status_code == 400
        assert res_invalid.json()["error"] == "invalid_backup_zip"

        res_empty = await client.post("/database/restore", content=b"", headers=headers)
        assert res_empty.status_code == 400
        assert res_empty.json()["error"] == "missing_file"

        # 6.1 Test restore from legacy database without appeared_chapters column
        import sqlite3
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as legacy_db_f:
            legacy_db_path = legacy_db_f.name

        try:
            legacy_conn = sqlite3.connect(legacy_db_path)
            # Create old schema without appeared_chapters column
            legacy_conn.executescript("""
            CREATE TABLE translation_jobs (
                id TEXT PRIMARY KEY, novel_id TEXT NOT NULL, chapter_number REAL NOT NULL,
                source_lang TEXT NOT NULL, target_lang TEXT NOT NULL,
                source_text_raw TEXT NOT NULL, source_text_cleaned TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT 'gpt-5.6-luna', status TEXT NOT NULL DEFAULT 'pending',
                result_translation TEXT, result_summary TEXT, raw_response TEXT, cleaned_response TEXT,
                error_code TEXT, error_message TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(novel_id, chapter_number)
            );
            CREATE TABLE characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT, novel_id TEXT NOT NULL, name TEXT NOT NULL,
                native_name TEXT NOT NULL, gender TEXT NOT NULL DEFAULT 'unknown',
                notes TEXT NOT NULL DEFAULT '', first_seen_chapter REAL NOT NULL,
                last_updated_chapter REAL NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(novel_id, name)
            );
            CREATE TABLE glossary (
                id INTEGER PRIMARY KEY AUTOINCREMENT, novel_id TEXT NOT NULL, term_source TEXT NOT NULL,
                term_translation TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                first_seen_chapter REAL NOT NULL, last_updated_chapter REAL NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(novel_id, term_source)
            );
            CREATE TABLE translation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, novel_id TEXT NOT NULL,
                chapter_number REAL NOT NULL, result_translation TEXT, result_summary TEXT, archived_at TEXT NOT NULL
            );
            CREATE TABLE account_cookies (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'chatgpt',
                cookies_data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE',
                cooldown_count INTEGER NOT NULL DEFAULT 0, cooldown_until TEXT, last_used_at TEXT,
                error_message TEXT, total_jobs_processed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(name, provider)
            );
            CREATE TABLE app_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO characters (novel_id, name, native_name, gender, notes, first_seen_chapter, last_updated_chapter, created_at, updated_at)
            VALUES ('legacy_novel', 'Sebas', 'セバス', 'male', 'Butler', 1.0, 2.0, '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z');
            """)
            legacy_conn.commit()
            legacy_conn.close()

            legacy_zip_buf = io.BytesIO()
            with zipfile.ZipFile(legacy_zip_buf, "w", zipfile.ZIP_DEFLATED) as lzf:
                lzf.writestr("metadata.json", json.dumps({"version": "0.9.0", "stats": {"characters": 1}}))
                lzf.write(legacy_db_path, arcname="translation.db")
            legacy_zip_bytes = legacy_zip_buf.getvalue()

            res_legacy_restore = await client.post("/database/restore", content=legacy_zip_bytes, headers=headers)
            assert res_legacy_restore.status_code == 200
            assert res_legacy_restore.json()["ok"] is True

            # Verify migrated legacy character has appeared_chapters automatically backfilled
            res_legacy_char = await client.get("/novels/legacy_novel/characters")
            assert res_legacy_char.status_code == 200
            assert res_legacy_char.json()["total"] == 1
            sebas = res_legacy_char.json()["items"][0]
            assert sebas["name"] == "Sebas"
            assert sebas["appeared_chapters"] == [1.0, 2.0]
        finally:
            if os.path.exists(legacy_db_path):
                os.remove(legacy_db_path)

        # 7. Test Cookie Management & Multi-Account Worker Pool
        print("Testing Cookie Management & Staged Cooldowns...")
        # 7.1 Add Cookie via POST /cookies
        cookie_payload = {
            "name": "test_account_1",
            "provider": "chatgpt",
            "cookies": "oai-did=12345; __Secure-next-auth.session-token=secrettoken123",
        }
        res_ck = await client.post("/cookies", json=cookie_payload)
        assert res_ck.status_code == 200, f"Expected 200, got {res_ck.status_code}: {res_ck.text}"
        acc_data = res_ck.json()["account"]
        acc_id = acc_data["id"]
        assert acc_data["name"] == "test_account_1"
        assert acc_data["status"] == "ACTIVE"
        assert acc_data["cooldown_count"] == 0

        # Add second cookie
        cookie_payload2 = {
            "name": "test_account_2",
            "provider": "chatgpt",
            "cookies": "oai-did=67890; __Secure-next-auth.session-token=secrettoken456",
        }
        res_ck2 = await client.post("/cookies", json=cookie_payload2)
        assert res_ck2.status_code == 200
        acc_id2 = res_ck2.json()["account"]["id"]

        # 7.2 List Cookies via GET /cookies
        res_ck_list = await client.get("/cookies")
        assert res_ck_list.status_code == 200
        assert res_ck_list.json()["total_accounts"] >= 2
        assert "pool" in res_ck_list.json()

        # 7.3 Pause and Resume Cookie
        res_pause = await client.post(f"/cookies/{acc_id}/pause")
        assert res_pause.status_code == 200
        assert res_pause.json()["account"]["status"] == "PAUSED"

        res_resume = await client.post(f"/cookies/{acc_id}/resume")
        assert res_resume.status_code == 200
        assert res_resume.json()["account"]["status"] == "ACTIVE"

        # 7.4 Staged Cooldown Logic Unit Testing
        from hermes_chatgpt_web.translation.database import (
            set_account_cooldown,
        )

        # 1st Cooldown -> 2 hours
        cd1 = await set_account_cooldown(acc_id, "Rate limit hit 1")
        assert cd1["status"] == "COOLDOWN"
        assert cd1["cooldown_count"] == 1
        assert cd1["cooldown_until"] is not None
        assert cd1["cooldown_remaining_seconds"] > 7000  # ~2 hours (7200s)

        # 2nd Cooldown -> 4 hours
        cd2 = await set_account_cooldown(acc_id, "Rate limit hit 2")
        assert cd2["status"] == "COOLDOWN"
        assert cd2["cooldown_count"] == 2
        assert cd2["cooldown_until"] is not None
        assert cd2["cooldown_remaining_seconds"] > 14000  # ~4 hours (14400s)

        # 3rd Cooldown -> EXPIRED
        cd3 = await set_account_cooldown(acc_id, "Rate limit hit 3")
        assert cd3["status"] == "EXPIRED"
        assert cd3["cooldown_count"] == 3
        assert cd3["cooldown_until"] is None

        # Reset Cooldown via Endpoint
        res_reset_cd = await client.post(f"/cookies/{acc_id}/reset-cooldown")
        assert res_reset_cd.status_code == 200
        assert res_reset_cd.json()["account"]["status"] == "ACTIVE"
        assert res_reset_cd.json()["account"]["cooldown_count"] == 0

        # 7.5 Delete Cookie via DELETE /cookies/{id}
        res_del = await client.delete(f"/cookies/{acc_id2}")
        assert res_del.status_code == 200
        assert res_del.json()["ok"] is True

        # 8. Test Modular ChatGPT and Core Playwright abstractions
        print("Testing Modular ChatGPT & Core Playwright modules...")
        from hermes_chatgpt_web.chatgpt import (
            ChatGPTBrowser,
            check_generation_status,
            detect_textarea,
            parse_cookie_dict,
            parse_cookie_line,
        )
        from hermes_chatgpt_web.core.browser import (
            ChatGPTBrowser as LegacyChatGPTBrowser,
        )
        from hermes_chatgpt_web.core.browser import (
            PlaywrightBrowser,
        )

        # Cookie parsing
        cookies_str = "__Secure-next-auth.session-token=abc123xyz; cf_clearance=def456; theme=dark"
        parsed_list = parse_cookie_line(cookies_str)
        assert len(parsed_list) == 3
        assert parsed_list[0] == {"name": "__Secure-next-auth.session-token", "value": "abc123xyz"}
        assert parsed_list[1] == {"name": "cf_clearance", "value": "def456"}

        parsed_dict = parse_cookie_dict(cookies_str)
        assert parsed_dict["__Secure-next-auth.session-token"] == "abc123xyz"
        assert parsed_dict["theme"] == "dark"

        # Check generation status on None page
        status_none = await check_generation_status(None)
        assert status_none["gen"] is False
        assert status_none["sbtn_ok"] is False

        # Textarea detect on None page
        assert (await detect_textarea(None)) is None

        # PlaywrightBrowser and ChatGPTBrowser inheritance / backward compat
        assert issubclass(ChatGPTBrowser, PlaywrightBrowser)
        assert LegacyChatGPTBrowser is ChatGPTBrowser

        # 9. Test /settings endpoint & Per-Context Post-Job Cooldown
        print("Testing /settings endpoint & Per-Context Post-Job Cooldown...")
        from hermes_chatgpt_web.chatgpt.worker_pool import worker_pool

        # GET /settings
        from hermes_chatgpt_web.core.config import DEFAULT_JOB_COOLDOWN_SECONDS
        res_settings = await client.get("/settings")
        assert res_settings.status_code == 200
        settings_data = res_settings.json()["settings"]
        assert settings_data["job_cooldown_seconds"] == DEFAULT_JOB_COOLDOWN_SECONDS
        assert settings_data["worker_poll_interval"] == 2.0

        # PATCH /settings to change cooldown to 5 seconds
        res_patch_settings = await client.patch("/settings", json={"job_cooldown_seconds": 5})
        assert res_patch_settings.status_code == 200
        assert res_patch_settings.json()["settings"]["job_cooldown_seconds"] == 5

        # Invalid setting value -> 400
        res_bad_setting = await client.patch("/settings", json={"job_cooldown_seconds": "not-a-number"})
        assert res_bad_setting.status_code == 400

        # Setup 2 workers in worker_pool for testing per-context cooldown
        from hermes_chatgpt_web.translation.database import upsert_account_cookie
        worker_pool.workers.clear()
        acc1 = await upsert_account_cookie(name="user_cd_1", provider="chatgpt", cookies_data="dummy1")
        acc2 = await upsert_account_cookie(name="user_cd_2", provider="chatgpt", cookies_data="dummy2")
        acc1_id = acc1["id"]
        acc2_id = acc2["id"]
        await worker_pool.add_worker(acc1)
        await worker_pool.add_worker(acc2)

        # Execute a mock stream (job)
        events = [ev async for ev in worker_pool.execute_stream(prompt="Test prompt 1")]
        assert len(events) >= 1

        # Check pool status: 1 worker should be in cooldown, 1 worker idle
        pool_stat = worker_pool.get_status()
        assert pool_stat["cooling_down_workers"] == 1
        assert pool_stat["idle_workers"] >= 1

        # Acquire next worker -> must get the other worker (the one not cooling down)
        w2 = worker_pool.acquire_idle_worker()
        assert w2 is not None
        assert not w2.is_cooling_down()

        # Release w2 with cooldown
        worker_pool.release_worker(w2, apply_cooldown=True)
        assert w2.is_cooling_down() is True
        assert w2.cooldown_remaining() > 0

        # Now both workers are in cooldown
        pool_stat_all_cd = worker_pool.get_status()
        assert pool_stat_all_cd["cooling_down_workers"] == 2
        assert pool_stat_all_cd["idle_workers"] == 0

        # acquire_idle_worker() should return None when all are cooling down
        assert worker_pool.acquire_idle_worker() is None

        # Reset cooldown for acc1 via reset-cooldown endpoint
        res_reset_w1 = await client.post(f"/cookies/{acc1_id}/reset-cooldown")
        assert res_reset_w1.status_code == 200

        # acc1 should now be idle and available
        w1_again = worker_pool.acquire_idle_worker()
        assert w1_again is not None
        assert w1_again.account_id == acc1_id
        worker_pool.release_worker(w1_again, apply_cooldown=False)

        # Test single worker rule & dynamic 0s cooldown
        worker_pool.workers.clear()
        acc_single = await upsert_account_cookie(name="user_single", provider="chatgpt", cookies_data="dummy_single")
        await worker_pool.add_worker(acc_single)

        # 1st job with default 60s cooldown
        _ = [ev async for ev in worker_pool.execute_stream(prompt="Single worker prompt 1")]
        assert worker_pool.acquire_idle_worker() is None  # in 60s cooldown!
        stat_single = worker_pool.get_status()
        assert stat_single["cooling_down_workers"] == 1
        assert stat_single["idle_workers"] == 0

        # Change settings dynamically to 0s cooldown
        res_patch_zero = await client.patch("/settings", json={"job_cooldown_seconds": 0})
        assert res_patch_zero.status_code == 200
        assert res_patch_zero.json()["settings"]["job_cooldown_seconds"] == 0

        # Reset single worker cooldown
        await client.post(f"/cookies/{acc_single['id']}/reset-cooldown")
        assert worker_pool.acquire_idle_worker() is not None

        # Release with new 0s setting
        w_single = worker_pool.acquire_idle_worker()
        if w_single:
            worker_pool.release_worker(w_single, apply_cooldown=True)
            assert not w_single.is_cooling_down()
            assert worker_pool.get_status()["idle_workers"] == 1

        # Reset settings back to defaults
        res_reset_settings = await client.post("/settings/reset")
        assert res_reset_settings.status_code == 200
        assert res_reset_settings.json()["settings"]["job_cooldown_seconds"] == DEFAULT_JOB_COOLDOWN_SECONDS
        assert res_reset_settings.json()["settings"]["context_refresh_jobs"] == 10

        # 10. Test Automatic Browser Context Refresh after N completed jobs
        print("Testing Automatic Browser Context Refresh on 10 Completed Jobs threshold...")
        acc_refresh = await upsert_account_cookie(name="user_refresh_test", provider="chatgpt", cookies_data="dummy_refresh")
        await worker_pool.add_worker(acc_refresh)

        # Set threshold to 3 jobs and 0s cooldown for testing
        await client.patch("/settings", json={"context_refresh_jobs": 3, "job_cooldown_seconds": 0})

        # Run 1st job -> completed_jobs becomes 1
        _ = [ev async for ev in worker_pool.execute_stream(prompt="Job 1", specific_account_id=acc_refresh["id"])]
        stat1 = worker_pool.get_status()
        w_stat = next(w for w in stat1["workers"] if w["account_id"] == acc_refresh["id"])
        assert w_stat["completed_jobs"] == 1

        # Run 2nd job -> completed_jobs becomes 2
        _ = [ev async for ev in worker_pool.execute_stream(prompt="Job 2", specific_account_id=acc_refresh["id"])]
        stat2 = worker_pool.get_status()
        w_stat2 = next(w for w in stat2["workers"] if w["account_id"] == acc_refresh["id"])
        assert w_stat2["completed_jobs"] == 2

        # Run 3rd job -> reaches threshold of 3 -> triggers context refresh -> completed_jobs resets to 0
        _ = [ev async for ev in worker_pool.execute_stream(prompt="Job 3", specific_account_id=acc_refresh["id"])]
        # Allow short tick for async context refresh task to complete
        await asyncio.sleep(0.1)
        stat3 = worker_pool.get_status()
        w_stat3 = next(w for w in stat3["workers"] if w["account_id"] == acc_refresh["id"])
        assert w_stat3["completed_jobs"] == 0

        # Test manual context refresh endpoint
        res_manual_refresh = await client.post(f"/cookies/{acc_refresh['id']}/refresh")
        assert res_manual_refresh.status_code == 200
        assert res_manual_refresh.json()["ok"] is True

        # Reset settings
        await client.post("/settings/reset")

        # Clean up test workers
        await worker_pool.remove_worker(acc1_id)
        await worker_pool.remove_worker(acc2_id)
        await worker_pool.remove_worker(acc_single["id"])
        await worker_pool.remove_worker(acc_refresh["id"])



        print("\n==========================================")
        print("ALL ENDPOINT & WORKFLOW TESTS PASSED (100%)")
        print("==========================================")



if __name__ == "__main__":
    asyncio.run(run_tests())


