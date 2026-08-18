"""
Hermes Backup Reader Service
Provides robust, read-only inspection, querying, and exporting of SQLite database
archives (.zip) and raw database files (.db) created by Hermes ChatGPT Web.
"""

from __future__ import annotations

import datetime
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any


class BackupReader:
    """
    Offline Inspector and Query Engine for Hermes Backup Archives.
    Supports both .zip packages (containing translation.db and metadata.json)
    and direct .db SQLite database files.
    """

    def __init__(self, backup_path: str | Path):
        self.raw_path = Path(backup_path)
        self.file_path = self._resolve_path(self.raw_path)
        self._temp_dir: tempfile.TemporaryDirectory | None = None
        self._db_file: Path | None = None
        self._metadata: dict[str, Any] = {}
        self._conn: sqlite3.Connection | None = None
        self._is_open = False

    def _resolve_path(self, path: Path) -> Path:
        """Resolve relative and absolute paths cleanly."""
        if path.is_file():
            return path.resolve()

        # Try relative to current working directory
        cwd_p = Path.cwd() / path
        if cwd_p.is_file():
            return cwd_p.resolve()

        # Try stripped of quotes
        cleaned = Path(str(path).strip('"').strip("'"))
        if cleaned.is_file():
            return cleaned.resolve()

        cwd_cleaned = Path.cwd() / cleaned
        if cwd_cleaned.is_file():
            return cwd_cleaned.resolve()

        return path.resolve()

    def open(self) -> "BackupReader":
        """Open archive/db, extract database if zipped, and establish read-only connection."""
        if not self.file_path.exists():
            raise FileNotFoundError(f"Backup file not found: {self.raw_path}")

        if zipfile.is_zipfile(self.file_path):
            self._temp_dir = tempfile.TemporaryDirectory(prefix="hermes_backup_reader_")
            temp_dir_path = Path(self._temp_dir.name)

            with zipfile.ZipFile(self.file_path, "r") as zf:
                namelist = zf.namelist()

                # 1. Parse metadata.json if present
                if "metadata.json" in namelist:
                    try:
                        self._metadata = json.loads(zf.read("metadata.json").decode("utf-8"))
                    except Exception:
                        self._metadata = {}

                # 2. Extract SQLite DB file
                db_member = None
                for candidate in ["translation.db", "hermes.db", "database.db"]:
                    if candidate in namelist:
                        db_member = candidate
                        break
                if not db_member:
                    # Fallback to any .db file in archive
                    for name in namelist:
                        if name.endswith(".db") or name.endswith(".sqlite") or name.endswith(".sqlite3"):
                            db_member = name
                            break

                if not db_member:
                    raise ValueError(f"No valid SQLite database file found inside ZIP archive: {self.file_path.name}")

                extracted_db = temp_dir_path / "translation.db"
                with zf.open(db_member) as src, open(extracted_db, "wb") as dst:
                    shutil.copyfileobj(src, dst)

                self._db_file = extracted_db
        else:
            # Assume direct SQLite DB file
            self._db_file = self.file_path
            self._metadata = {}

        # Connect to SQLite DB in read-only mode with URI
        db_uri = f"file:{self._db_file.as_posix()}?mode=ro"
        self._conn = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._is_open = True
        return self

    def close(self):
        """Close SQLite connection and cleanup temporary files."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

        if self._temp_dir:
            try:
                self._temp_dir.cleanup()
            except Exception:
                pass
            self._temp_dir = None

        self._is_open = False

    def __enter__(self) -> "BackupReader":
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _ensure_open(self):
        if not self._is_open or not self._conn:
            self.open()

    def get_file_info(self) -> dict[str, Any]:
        """Return basic file attributes of the backup."""
        sz = self.file_path.stat().st_size if self.file_path.exists() else 0
        mtime = self.file_path.stat().st_mtime if self.file_path.exists() else 0
        dt = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        return {
            "path": str(self.file_path),
            "filename": self.file_path.name,
            "size_bytes": sz,
            "size_mb": round(sz / (1024 * 1024), 2),
            "modified_at": dt,
            "is_zip": zipfile.is_zipfile(self.file_path) if self.file_path.exists() else False,
        }

    def get_metadata(self) -> dict[str, Any]:
        """Get backup metadata (from metadata.json if available)."""
        self._ensure_open()
        return dict(self._metadata)

    def get_table_counts(self) -> dict[str, int]:
        """Get row counts for each table present in the backup DB."""
        self._ensure_open()
        counts: dict[str, int] = {}
        cursor = self._conn.cursor()
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [r[0] for r in cursor.fetchall()]
            for tbl in tables:
                cursor.execute(f"SELECT COUNT(*) FROM \"{tbl}\"")
                counts[tbl] = cursor.fetchone()[0]
        except Exception as e:
            counts["_error"] = str(e)
        return counts

    def get_overview(self) -> dict[str, Any]:
        """Comprehensive summary of backup contents, stats, and novels."""
        self._ensure_open()
        file_info = self.get_file_info()
        meta = self.get_metadata()
        tables = self.get_table_counts()

        cursor = self._conn.cursor()

        # Job status breakdown
        status_counts: dict[str, int] = {
            "done": 0,
            "failed": 0,
            "cancelled": 0,
            "pending": 0,
            "processing": 0,
        }
        if "translation_jobs" in tables:
            cursor.execute("SELECT status, COUNT(*) FROM translation_jobs GROUP BY status")
            for st, cnt in cursor.fetchall():
                status_counts[st] = cnt

        novels = self.list_novels()

        return {
            "file": file_info,
            "metadata": meta,
            "tables": tables,
            "job_statuses": status_counts,
            "total_jobs": tables.get("translation_jobs", 0),
            "total_novels": len(novels),
            "total_characters": tables.get("characters", 0),
            "total_glossary": tables.get("glossary", 0),
            "total_cookies": tables.get("account_cookies", 0),
            "novels": novels,
        }

    def list_novels(self) -> list[dict[str, Any]]:
        """List all novels stored in the backup with per-novel statistics."""
        self._ensure_open()
        cursor = self._conn.cursor()

        # Collect distinct novel IDs from translation_jobs, characters, and glossary
        novel_ids = set()
        tables = self.get_table_counts()

        if "translation_jobs" in tables:
            cursor.execute("SELECT DISTINCT novel_id FROM translation_jobs WHERE novel_id IS NOT NULL")
            novel_ids.update([r[0] for r in cursor.fetchall() if r[0]])

        if "characters" in tables:
            cursor.execute("SELECT DISTINCT novel_id FROM characters WHERE novel_id IS NOT NULL")
            novel_ids.update([r[0] for r in cursor.fetchall() if r[0]])

        if "glossary" in tables:
            cursor.execute("SELECT DISTINCT novel_id FROM glossary WHERE novel_id IS NOT NULL")
            novel_ids.update([r[0] for r in cursor.fetchall() if r[0]])

        results = []
        for nid in sorted(novel_ids):
            stats = self.get_novel_stats(nid)
            results.append(stats)

        return results

    def get_novel_stats(self, novel_id: str) -> dict[str, Any]:
        """Get detailed statistics for a specific novel."""
        self._ensure_open()
        cursor = self._conn.cursor()

        # Job stats
        total_chapters = 0
        done_chapters = 0
        failed_chapters = 0
        cancelled_chapters = 0
        min_chapter = None
        max_chapter = None
        source_lang = None
        target_lang = None
        latest_update = None

        tables = self.get_table_counts()
        if "translation_jobs" in tables:
            cursor.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END),
                    MIN(chapter_number),
                    MAX(chapter_number),
                    source_lang,
                    target_lang,
                    MAX(updated_at)
                FROM translation_jobs
                WHERE novel_id = ?
                """,
                (novel_id,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                total_chapters = row[0] or 0
                done_chapters = row[1] or 0
                failed_chapters = row[2] or 0
                cancelled_chapters = row[3] or 0
                min_chapter = row[4]
                max_chapter = row[5]
                source_lang = row[6]
                target_lang = row[7]
                latest_update = row[8]

        # Character count
        char_count = 0
        if "characters" in tables:
            cursor.execute("SELECT COUNT(*) FROM characters WHERE novel_id = ?", (novel_id,))
            char_count = cursor.fetchone()[0]

        # Glossary count
        glossary_count = 0
        if "glossary" in tables:
            cursor.execute("SELECT COUNT(*) FROM glossary WHERE novel_id = ?", (novel_id,))
            glossary_count = cursor.fetchone()[0]

        progress_pct = round((done_chapters / total_chapters * 100), 1) if total_chapters > 0 else 0.0

        return {
            "novel_id": novel_id,
            "total_chapters": total_chapters,
            "done_chapters": done_chapters,
            "failed_chapters": failed_chapters,
            "cancelled_chapters": cancelled_chapters,
            "progress_percent": progress_pct,
            "min_chapter": min_chapter,
            "max_chapter": max_chapter,
            "source_lang": source_lang or "N/A",
            "target_lang": target_lang or "N/A",
            "characters_count": char_count,
            "glossary_count": glossary_count,
            "latest_update": latest_update or "N/A",
        }

    def list_chapters(
        self,
        novel_id: str,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        order: str = "ASC",
    ) -> list[dict[str, Any]]:
        """List chapters for a novel with preview snippets and status."""
        self._ensure_open()
        cursor = self._conn.cursor()

        order_clause = "DESC" if order.upper() == "DESC" else "ASC"
        query = """
            SELECT
                id, novel_id, chapter_number, source_lang, target_lang,
                model, status, error_code, error_message, retry_count,
                result_summary,
                SUBSTR(COALESCE(result_translation, ''), 1, 140) as translation_snippet,
                SUBSTR(COALESCE(source_text_raw, ''), 1, 100) as source_snippet,
                LENGTH(COALESCE(result_translation, '')) as translation_len,
                created_at, updated_at
            FROM translation_jobs
            WHERE novel_id = ?
        """
        params: list[Any] = [novel_id]

        if status:
            query += " AND status = ?"
            params.append(status.lower())

        query += f" ORDER BY chapter_number {order_clause}"

        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def get_chapter(self, novel_id: str, chapter_number: float) -> dict[str, Any] | None:
        """Get full chapter details including source and translated texts."""
        self._ensure_open()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM translation_jobs
            WHERE novel_id = ? AND chapter_number = ?
            ORDER BY id DESC LIMIT 1
            """,
            (novel_id, chapter_number),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_characters(self, novel_id: str, search: str | None = None) -> list[dict[str, Any]]:
        """List character lore entries for a novel."""
        self._ensure_open()
        cursor = self._conn.cursor()
        tables = self.get_table_counts()
        if "characters" not in tables:
            return []

        query = "SELECT * FROM characters WHERE novel_id = ?"
        params: list[Any] = [novel_id]

        if search:
            query += " AND (name LIKE ? OR native_name LIKE ? OR notes LIKE ?)"
            s_param = f"%{search}%"
            params.extend([s_param, s_param, s_param])

        query += " ORDER BY name ASC"
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]

    def list_glossary(self, novel_id: str, search: str | None = None) -> list[dict[str, Any]]:
        """List glossary terms for a novel."""
        self._ensure_open()
        cursor = self._conn.cursor()
        tables = self.get_table_counts()
        if "glossary" not in tables:
            return []

        query = "SELECT * FROM glossary WHERE novel_id = ?"
        params: list[Any] = [novel_id]

        if search:
            query += " AND (term_source LIKE ? OR term_translation LIKE ? OR notes LIKE ?)"
            s_param = f"%{search}%"
            params.extend([s_param, s_param, s_param])

        query += " ORDER BY term_source ASC"
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]

    def list_cookies(self) -> list[dict[str, Any]]:
        """List cookies and worker accounts saved in backup (with masked tokens)."""
        self._ensure_open()
        cursor = self._conn.cursor()
        tables = self.get_table_counts()
        if "account_cookies" not in tables:
            return []

        cursor.execute("SELECT * FROM account_cookies ORDER BY id ASC")
        rows = cursor.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            # Mask cookies_data for safety
            if d.get("cookies_data"):
                raw_len = len(d["cookies_data"])
                d["cookies_data_masked"] = f"[{raw_len} chars hidden]"
                del d["cookies_data"]
            results.append(d)
        return results

    def get_app_settings(self) -> dict[str, Any]:
        """Retrieve dynamic settings snapshot stored in backup."""
        self._ensure_open()
        cursor = self._conn.cursor()
        tables = self.get_table_counts()
        if "app_settings" not in tables:
            return {}

        cursor.execute("SELECT key, value, updated_at FROM app_settings")
        settings: dict[str, Any] = {}
        for r in cursor.fetchall():
            val = r["value"]
            try:
                val = json.loads(val)
            except Exception:
                pass
            settings[r["key"]] = val
        return settings

    def export_chapters(
        self,
        novel_id: str,
        output_dir: str | Path,
        format: str = "txt",
        status_filter: str = "done",
        single_file: bool = False,
        include_summary: bool = True,
    ) -> tuple[int, Path]:
        """
        Export novel chapters to disk.
        Returns: (exported_count, destination_path)
        """
        self._ensure_open()
        out_path = Path(output_dir)

        chapters = self.list_chapters(novel_id, status=status_filter if status_filter != "all" else None, order="ASC")
        if not chapters:
            return 0, out_path

        fmt = format.lower()
        if fmt not in ("txt", "md", "json"):
            fmt = "txt"

        if single_file:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if out_path.is_dir() or out_path.suffix == "":
                target_file = out_path / f"{novel_id}_complete.{fmt}"
            else:
                target_file = out_path

            count = 0
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(f"# {novel_id.replace('-', ' ').title()}\n\n")
                f.write(f"*Exported from Hermes Backup: {self.file_path.name}*\n\n---\n\n")

                for ch_meta in chapters:
                    ch_full = self.get_chapter(novel_id, ch_meta["chapter_number"])
                    if not ch_full:
                        continue
                    ch_num = ch_meta["chapter_number"]
                    trans_text = ch_full.get("result_translation") or ""
                    summary = ch_full.get("result_summary") or ""

                    f.write(f"## Chapter {ch_num:g}\n\n")
                    if include_summary and summary:
                        f.write(f"> **Summary**: {summary}\n\n")
                    f.write(f"{trans_text}\n\n---\n\n")
                    count += 1

            return count, target_file
        else:
            novel_out_dir = out_path / novel_id if out_path.name != novel_id else out_path
            novel_out_dir.mkdir(parents=True, exist_ok=True)

            count = 0
            for ch_meta in chapters:
                ch_full = self.get_chapter(novel_id, ch_meta["chapter_number"])
                if not ch_full:
                    continue
                ch_num = ch_meta["chapter_number"]
                trans_text = ch_full.get("result_translation") or ""
                summary = ch_full.get("result_summary") or ""

                filename = f"chapter_{int(ch_num):04d}.{fmt}" if ch_num.is_integer() else f"chapter_{ch_num:06.2f}.{fmt}"
                file_dest = novel_out_dir / filename

                with open(file_dest, "w", encoding="utf-8") as f:
                    if fmt == "md":
                        f.write(f"# Chapter {ch_num:g}\n\n")
                        if include_summary and summary:
                            f.write(f"> **Summary**: {summary}\n\n")
                        f.write(f"{trans_text}\n")
                    elif fmt == "json":
                        json.dump(ch_full, f, indent=2, ensure_ascii=False)
                    else:
                        f.write(f"=== Chapter {ch_num:g} ===\n\n")
                        if include_summary and summary:
                            f.write(f"[Summary: {summary}]\n\n")
                        f.write(f"{trans_text}\n")
                count += 1

            return count, novel_out_dir

    def export_glossary_and_characters(self, novel_id: str, output_file: str | Path) -> bool:
        """Export characters and glossary for a novel to a JSON file."""
        self._ensure_open()
        out_p = Path(output_file)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        chars = self.list_characters(novel_id)
        gloss = self.list_glossary(novel_id)

        data = {
            "novel_id": novel_id,
            "backup_source": self.file_path.name,
            "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "characters_count": len(chars),
            "glossary_count": len(gloss),
            "characters": chars,
            "glossary": gloss,
        }

        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return True
