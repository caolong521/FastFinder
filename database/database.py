from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Iterable

from database.schema import SCHEMA_SQL, FTS_SQL
from database.migrations import run_migrations
from utils.path_utils import normalize_path


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Cached after initialize().  Search runs very frequently, so avoid
        # opening a second SQLite connection only to ask whether FTS exists.
        self._fts_available: bool | None = None

    def connect(self) -> sqlite3.Connection:
        """Open a lightweight working connection.

        IMPORTANT: journal_mode is deliberately NOT set here.  The old code ran
        PRAGMA journal_mode=WAL for every search / history / UI query.  On a
        large database this can create unnecessary locking and startup latency.
        WAL mode is configured once by initialize().
        """
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        # Per-connection read tuning.  Reduced from 64 MiB to 16 MiB to lower
        # memory footprint when multiple connections are alive simultaneously.
        conn.execute("PRAGMA cache_size=-16384;")       # ~16 MiB page cache
        try:
            conn.execute("PRAGMA mmap_size=268435456;")  # up to 256 MiB mmap
        except sqlite3.DatabaseError:
            pass
        return conn

    def initialize(self) -> None:
        """Fast startup initialization only.

        This method must never scan all file_entries or rebuild FTS.  It only
        ensures schema/FTS objects exist.  Any expensive compatibility work is
        moved to ensure_fts_ready(), which is called later from a worker thread.
        """
        with self.connect() as conn:
            # Set WAL once for the database rather than on every connection.
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.DatabaseError:
                pass
            conn.executescript(SCHEMA_SQL)
            run_migrations(conn)
            try:
                conn.executescript(FTS_SQL)
                self._fts_available = True
            except sqlite3.OperationalError:
                # Very rare Python/SQLite builds may not include FTS5.
                self._fts_available = False

    def has_fts5(self) -> bool:
        if self._fts_available is not None:
            return self._fts_available
        try:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_search' LIMIT 1"
                ).fetchone()
                self._fts_available = row is not None
                return self._fts_available
        except Exception:
            self._fts_available = False
            return False

    def ensure_fts_ready(self) -> dict:
        """Repair an old empty FTS index without delaying app startup.

        Only LIMIT 1 probes are used.  If an old database contains file_entries
        but its FTS table is empty, the rebuild can be expensive; callers should
        therefore invoke this method from a background worker after the UI has
        already appeared.
        """
        started = time.perf_counter()
        result = {"status": "skipped", "rebuilt": False, "elapsed": 0.0}
        if not self.has_fts5():
            result["status"] = "fts_unavailable"
            return result

        with self.connect() as conn:
            has_entries = conn.execute("SELECT 1 FROM file_entries LIMIT 1").fetchone() is not None
            if not has_entries:
                result["status"] = "empty_database"
            else:
                has_fts_rows = conn.execute("SELECT rowid FROM file_search LIMIT 1").fetchone() is not None
                if has_fts_rows:
                    result["status"] = "ready"
                else:
                    conn.execute("PRAGMA busy_timeout=60000;")
                    conn.execute("INSERT INTO file_search(file_search) VALUES('rebuild')")
                    conn.commit()
                    result["status"] = "rebuilt"
                    result["rebuilt"] = True
        result["elapsed"] = time.perf_counter() - started
        return result

    def estimated_entry_count(self) -> int:
        """Fast O(number-of-roots) count for startup/status display.

        search_roots.item_count is maintained after indexing.  Using SUM here
        avoids SELECT COUNT(*) over a hundreds-of-megabytes file_entries table
        during MainWindow construction.
        """
        try:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(item_count), 0) FROM search_roots"
                ).fetchone()
                return int(row[0] or 0)
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Search roots
    # ------------------------------------------------------------------
    def add_search_root(self, path: str) -> int:
        full = os.path.abspath(os.path.normpath(path))
        norm = normalize_path(full)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO search_roots(path, path_norm, enabled)
                VALUES (?, ?, 1)
                ON CONFLICT(path_norm) DO UPDATE SET enabled=1, path=excluded.path
                """,
                (full, norm),
            )
            row = conn.execute(
                "SELECT id FROM search_roots WHERE path_norm=?", (norm,)
            ).fetchone()
            return int(row[0])

    def list_search_roots(self, enabled_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM search_roots"
        params: tuple = ()
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY path COLLATE NOCASE"
        with self.connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    def get_root(self, root_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM search_roots WHERE id=?", (root_id,)).fetchone()

    def set_root_enabled(self, root_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE search_roots SET enabled=? WHERE id=?",
                (1 if enabled else 0, root_id),
            )

    def remove_search_root(self, root_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM search_roots WHERE id=?", (root_id,))

    def clear_root_entries(self, root_id: int, conn: sqlite3.Connection | None = None) -> None:
        own = conn is None
        conn = conn or self.connect()
        try:
            conn.execute("DELETE FROM file_entries WHERE root_id=?", (root_id,))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def clear_root_archive(self, root_id: int, pause_root: bool = True) -> None:
        """
        清空某个长期索引目录已经写入 SQLite 的文件/文件夹记录，
        但保留 search_roots 中的目录配置。

        默认同时暂停该目录，避免 watchdog 或后台自动扫描马上把数据重新写回。
        用户之后可重新勾选“启用”并点击“重新扫描”恢复索引。
        """
        with self.connect() as conn:
            conn.execute("DELETE FROM file_entries WHERE root_id=?", (root_id,))
            conn.execute(
                """
                UPDATE search_roots
                SET item_count=0,
                    last_scan_time=NULL,
                    last_scan_duration=0,
                    next_scan_time=NULL,
                    update_interval_minutes=NULL,
                    enabled=CASE WHEN ? THEN 0 ELSE enabled END
                WHERE id=?
                """,
                (1 if pause_root else 0, root_id),
            )

    def delete_stale_root_entries(
        self,
        root_id: int,
        current_generation: int,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """删除某索引目录中没有被本轮完整扫描重新确认的旧记录。"""
        own = conn is None
        conn = conn or self.connect()
        try:
            conn.execute(
                "DELETE FROM file_entries WHERE root_id=? AND indexed_time<?",
                (root_id, current_generation),
            )
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def update_root_schedule(
        self,
        root_id: int,
        interval_minutes: int | None,
        next_scan_time: int | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE search_roots
                SET update_interval_minutes=?, next_scan_time=?
                WHERE id=?
                """,
                (interval_minutes, next_scan_time, int(root_id)),
            )

    def clear_next_scan_times(self) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE search_roots SET next_scan_time=NULL, update_interval_minutes=NULL"
            )

    def get_index_update_summary(self) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    MAX(last_scan_time) AS last_scan_time,
                    MIN(CASE WHEN enabled=1 THEN next_scan_time END) AS next_scan_time,
                    SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) AS enabled_roots
                FROM search_roots
                """
            ).fetchone()
            return {
                "last_scan_time": row["last_scan_time"] if row else None,
                "next_scan_time": row["next_scan_time"] if row else None,
                "enabled_roots": int((row["enabled_roots"] if row else 0) or 0),
            }

    def touch_root_update(self, root_id: int, update_time: int | None = None) -> None:
        """文件监听器发现变化时，只刷新“上次更新”时间，不触发全量扫描。"""
        with self.connect() as conn:
            conn.execute(
                "UPDATE search_roots SET last_scan_time=? WHERE id=?",
                (update_time or int(time.time()), root_id),
            )

    def update_root_scan_stats(
        self,
        root_id: int,
        item_count: int,
        duration: float,
        scan_time: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        own = conn is None
        conn = conn or self.connect()
        try:
            conn.execute(
                """
                UPDATE search_roots
                SET item_count=?, last_scan_time=?, last_scan_duration=?
                WHERE id=?
                """,
                (item_count, scan_time or int(time.time()), duration, root_id),
            )
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    # ------------------------------------------------------------------
    # File entries
    # ------------------------------------------------------------------
    def begin_incremental_scan(self, conn: sqlite3.Connection) -> None:
        """Create a connection-local seen-path table for one root scan.

        The previous implementation updated every file row only to refresh a
        generation marker.  On large indexes that caused unnecessary SQLite and
        FTS writes.  A TEMP seen table lets unchanged rows stay untouched.
        """
        conn.execute("DROP TABLE IF EXISTS temp_scan_seen")
        conn.execute(
            "CREATE TEMP TABLE temp_scan_seen(path_norm TEXT PRIMARY KEY) WITHOUT ROWID"
        )

    def abort_incremental_scan(self, conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE IF EXISTS temp_scan_seen")

    def sync_scan_batch(
        self,
        root_id: int,
        rows: list[tuple],
        conn: sqlite3.Connection,
    ) -> dict:
        """Persist only new/changed rows while marking every scanned path seen."""
        if not rows:
            return {"added": 0, "updated": 0, "unchanged": 0}

        conn.executemany(
            "INSERT OR IGNORE INTO temp_scan_seen(path_norm) VALUES (?)",
            ((row[7],) for row in rows),
        )

        # Load only the columns needed for comparison, not full Row objects.
        existing: dict[str, tuple] = {}
        path_norms = [row[7] for row in rows]
        # Keep each IN query well below SQLite's traditional parameter limit.
        for start in range(0, len(path_norms), 400):
            chunk = path_norms[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            sql = f"""
                SELECT id, name, name_norm, stem, stem_norm, extension,
                       full_path, parent_path, is_directory, size, created_time,
                       modified_time, depth
                FROM file_entries
                WHERE root_id=? AND path_norm IN ({placeholders})
            """
            for old in conn.execute(sql, (int(root_id), *chunk)):
                existing[str(old["path_norm"])] = tuple(old)

        inserts: list[tuple] = []
        updates: list[tuple] = []
        unchanged = 0

        for row in rows:
            old = existing.get(row[7])
            if old is None:
                inserts.append(row)
                continue

            # Compare using tuple indices instead of Row field access
            same = (
                old[1] == row[1]   # name
                and old[2] == row[2]  # name_norm
                and old[3] == row[3]  # stem
                and old[4] == row[4]  # stem_norm
                and old[5] == row[5]  # extension
                and old[6] == row[6]  # full_path
                and old[8] == row[8]  # parent_path
                and int(old[9] or 0) == int(row[9])   # is_directory
                and int(old[10] or 0) == int(row[10])  # size
                and int(old[11] or 0) == int(row[11])  # created_time
                and int(old[12] or 0) == int(row[12])  # modified_time
                and int(old[13] or 0) == int(row[13])  # depth
            )
            if same:
                unchanged += 1
                continue

            updates.append((
                row[0], row[1], row[2], row[3], row[4], row[5], row[6],
                row[7], row[8], row[9], row[10], row[11], row[12], row[13],
                row[14], int(old[0]),  # indexed_time, id
            ))

        if inserts:
            conn.executemany(self._insert_sql(), inserts)
        if updates:
            conn.executemany(
                """
                UPDATE file_entries SET
                    root_id=?, name=?, name_norm=?, stem=?, stem_norm=?, extension=?,
                    full_path=?, path_norm=?, parent_path=?, is_directory=?, size=?,
                    created_time=?, modified_time=?, depth=?, indexed_time=?
                WHERE id=?
                """,
                updates,
            )

        return {
            "added": len(inserts),
            "updated": len(updates),
            "unchanged": unchanged,
        }

    def finish_incremental_scan(
        self,
        root_id: int,
        conn: sqlite3.Connection,
    ) -> int:
        cursor = conn.execute(
            """
            DELETE FROM file_entries
            WHERE root_id=?
              AND NOT EXISTS (
                    SELECT 1 FROM temp_scan_seen s
                    WHERE s.path_norm=file_entries.path_norm
              )
            """,
            (int(root_id),),
        )
        removed = cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0
        conn.execute("DROP TABLE IF EXISTS temp_scan_seen")
        return int(max(0, removed))

    @staticmethod
    def _insert_sql() -> str:
        return """
            INSERT INTO file_entries(
                root_id, name, name_norm, stem, stem_norm, extension,
                full_path, path_norm, parent_path, is_directory,
                size, created_time, modified_time, depth, indexed_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(full_path) DO UPDATE SET
                root_id=excluded.root_id,
                name=excluded.name,
                name_norm=excluded.name_norm,
                stem=excluded.stem,
                stem_norm=excluded.stem_norm,
                extension=excluded.extension,
                path_norm=excluded.path_norm,
                parent_path=excluded.parent_path,
                is_directory=excluded.is_directory,
                size=excluded.size,
                created_time=excluded.created_time,
                modified_time=excluded.modified_time,
                depth=excluded.depth,
                indexed_time=excluded.indexed_time
        """

    def insert_entries(self, rows: Iterable[tuple], conn: sqlite3.Connection | None = None) -> None:
        own = conn is None
        conn = conn or self.connect()
        try:
            conn.executemany(self._insert_sql(), rows)
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def upsert_single(self, row: tuple) -> None:
        with self.connect() as conn:
            conn.execute(self._insert_sql(), row)

    def delete_path(self, full_path: str, is_directory: bool = False) -> None:
        full_path = os.path.abspath(os.path.normpath(full_path))
        with self.connect() as conn:
            if is_directory:
                prefix = full_path.rstrip("\\/") + os.sep + "%"
                conn.execute(
                    "DELETE FROM file_entries WHERE full_path=? OR full_path LIKE ?",
                    (full_path, prefix),
                )
            else:
                conn.execute("DELETE FROM file_entries WHERE full_path=?", (full_path,))

    def count_entries(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM file_entries").fetchone()[0])

    def get_root_for_path(self, path: str):
        norm = normalize_path(os.path.abspath(path))
        roots = self.list_search_roots(enabled_only=True)
        best = None
        for root in roots:
            root_norm = root["path_norm"]
            if norm == root_norm or norm.startswith(root_norm.rstrip("\\/") + os.sep.casefold()):
                if best is None or len(root_norm) > len(best["path_norm"]):
                    best = root
        return best

    # ------------------------------------------------------------------
    # Search history / favorites / usage
    # ------------------------------------------------------------------
    def record_search_history(self, query: str) -> None:
        query = " ".join((query or "").strip().split())
        if len(query) < 2:
            return
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO search_history(query, use_count, last_used_time)
                VALUES (?, 1, ?)
                ON CONFLICT(query) DO UPDATE SET
                    use_count=search_history.use_count +
                        CASE WHEN excluded.last_used_time-search_history.last_used_time >= 2 THEN 1 ELSE 0 END,
                    last_used_time=excluded.last_used_time
                """,
                (query, now),
            )

    def list_search_history(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT id, query, use_count, last_used_time
                    FROM search_history
                    ORDER BY last_used_time DESC, use_count DESC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            )

    def delete_search_history(self, history_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM search_history WHERE id=?", (int(history_id),))

    def clear_search_history(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM search_history")

    def add_favorite(self, full_path: str, is_directory: bool | None = None) -> None:
        full = os.path.abspath(os.path.normpath(full_path))
        norm = normalize_path(full)
        if is_directory is None:
            is_directory = os.path.isdir(full)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO favorites(full_path, path_norm, name, is_directory, created_time)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path_norm) DO UPDATE SET
                    full_path=excluded.full_path,
                    name=excluded.name,
                    is_directory=excluded.is_directory
                """,
                (full, norm, os.path.basename(full.rstrip("\\/")) or full, 1 if is_directory else 0, int(time.time())),
            )

    def remove_favorite(self, full_path: str) -> None:
        norm = normalize_path(os.path.abspath(os.path.normpath(full_path)))
        with self.connect() as conn:
            conn.execute("DELETE FROM favorites WHERE path_norm=?", (norm,))

    def is_favorite(self, full_path: str) -> bool:
        norm = normalize_path(os.path.abspath(os.path.normpath(full_path)))
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM favorites WHERE path_norm=?", (norm,)).fetchone()
            return row is not None

    def list_favorites(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT id, full_path, path_norm, name, is_directory, created_time
                    FROM favorites
                    ORDER BY created_time DESC, name COLLATE NOCASE
                    """
                ).fetchall()
            )

    def clear_missing_favorites(self) -> int:
        removed = 0
        for row in self.list_favorites():
            if not os.path.exists(row["full_path"]):
                self.remove_favorite(row["full_path"])
                removed += 1
        return removed

    def record_usage(self, full_path: str) -> None:
        full = os.path.abspath(os.path.normpath(full_path))
        norm = normalize_path(full)
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO file_usage(path_norm, full_path, open_count, last_opened_time)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(path_norm) DO UPDATE SET
                    full_path=excluded.full_path,
                    open_count=file_usage.open_count+1,
                    last_opened_time=excluded.last_opened_time
                """,
                (norm, full, now),
            )

    def usage_for_path(self, full_path: str):
        norm = normalize_path(os.path.abspath(os.path.normpath(full_path)))
        with self.connect() as conn:
            return conn.execute(
                "SELECT open_count, last_opened_time FROM file_usage WHERE path_norm=?",
                (norm,),
            ).fetchone()

    def behavior_map(self, full_paths: list[str]) -> dict[str, dict]:
        """Return favorite/usage metadata keyed by normalized path.

        Used by transient direct-directory search so favorites still display
        correctly even though the directory itself is not stored in SQLite.
        """
        norms = []
        seen = set()
        for path in full_paths:
            norm = normalize_path(os.path.abspath(os.path.normpath(path)))
            if norm not in seen:
                seen.add(norm)
                norms.append(norm)
        result: dict[str, dict] = {}
        if not norms:
            return result
        with self.connect() as conn:
            for start in range(0, len(norms), 800):
                chunk = norms[start:start + 800]
                marks = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT p.path_norm,
                           CASE WHEN fav.path_norm IS NULL THEN 0 ELSE 1 END AS is_favorite,
                           COALESCE(us.open_count, 0) AS open_count,
                           COALESCE(us.last_opened_time, 0) AS last_opened_time
                    FROM (
                        SELECT path_norm FROM favorites WHERE path_norm IN ({marks})
                        UNION
                        SELECT path_norm FROM file_usage WHERE path_norm IN ({marks})
                    ) p
                    LEFT JOIN favorites fav ON fav.path_norm=p.path_norm
                    LEFT JOIN file_usage us ON us.path_norm=p.path_norm
                    """,
                    [*chunk, *chunk],
                ).fetchall()
                for row in rows:
                    result[row["path_norm"]] = {
                        "is_favorite": bool(row["is_favorite"]),
                        "open_count": int(row["open_count"] or 0),
                        "last_opened_time": int(row["last_opened_time"] or 0),
                    }
        return result

    # ------------------------------------------------------------------
    # Candidate search
    # ------------------------------------------------------------------
    @staticmethod
    def _base_filters(
        root_ids: list[int] | None,
        item_type: str | None,
        category_extensions: list[str] | None,
        after_time: int | None,
        before_time: int | None,
        extensions: list[str] | None,
        path_filter: str | None,
        size_min: int | None,
        size_max: int | None,
    ) -> tuple[list[str], list]:
        clauses = []
        params: list = []

        if root_ids:
            marks = ",".join("?" for _ in root_ids)
            clauses.append(f"fe.root_id IN ({marks})")
            params.extend(root_ids)
        else:
            clauses.append("fe.root_id IN (SELECT id FROM search_roots WHERE enabled=1)")

        if item_type == "file":
            clauses.append("fe.is_directory=0")
        elif item_type == "folder":
            clauses.append("fe.is_directory=1")

        if category_extensions:
            marks = ",".join("?" for _ in category_extensions)
            clauses.append(f"fe.extension IN ({marks})")
            params.extend(category_extensions)

        if extensions:
            clean = [e.casefold() if e.startswith(".") else "." + e.casefold() for e in extensions]
            marks = ",".join("?" for _ in clean)
            clauses.append(f"fe.extension IN ({marks})")
            params.extend(clean)

        if after_time:
            clauses.append("fe.modified_time>=?")
            params.append(after_time)
        if before_time:
            clauses.append("fe.modified_time<=?")
            params.append(before_time)
        if path_filter:
            clauses.append("fe.path_norm LIKE ?")
            params.append("%" + path_filter.casefold() + "%")
        if size_min is not None:
            clauses.append("fe.size>=?")
            params.append(size_min)
        if size_max is not None:
            clauses.append("fe.size<=?")
            params.append(size_max)

        return clauses, params

    @staticmethod
    def _select_columns() -> str:
        return """
            fe.id, fe.root_id, fe.name, fe.name_norm, fe.stem, fe.stem_norm,
            fe.extension, fe.full_path, fe.path_norm, fe.parent_path,
            fe.is_directory, fe.size, fe.created_time, fe.modified_time, fe.depth,
            CASE WHEN fav.path_norm IS NULL THEN 0 ELSE 1 END AS is_favorite,
            COALESCE(us.open_count, 0) AS open_count,
            COALESCE(us.last_opened_time, 0) AS last_opened_time
        """

    @staticmethod
    def _usage_joins() -> str:
        return """
            LEFT JOIN favorites fav ON fav.path_norm=fe.path_norm
            LEFT JOIN file_usage us ON us.path_norm=fe.path_norm
        """

    def search_candidates(
        self,
        keywords: list[str],
        root_ids: list[int] | None = None,
        item_type: str | None = None,
        category_extensions: list[str] | None = None,
        after_time: int | None = None,
        before_time: int | None = None,
        extensions: list[str] | None = None,
        path_filter: str | None = None,
        size_min: int | None = None,
        size_max: int | None = None,
        limit: int = 3000,
    ) -> list[sqlite3.Row]:
        clauses, params = self._base_filters(
            root_ids, item_type, category_extensions, after_time, before_time,
            extensions, path_filter, size_min, size_max
        )

        seen: set[int] = set()
        result: list[sqlite3.Row] = []

        def add_rows(rows):
            for row in rows:
                if row["id"] not in seen:
                    seen.add(row["id"])
                    result.append(row)
                    if len(result) >= limit:
                        return False
            return True

        with self.connect() as conn:
            # 1) FTS5 前缀筛选：对 modbus / config 这类查询很快。
            if keywords and self.has_fts5():
                tokens = []
                for kw in keywords:
                    token = "".join(ch for ch in kw.casefold() if ch.isalnum() or ch == "_")
                    if token:
                        tokens.append(f'"{token}"*')
                if tokens:
                    where = ["file_search MATCH ?"] + clauses
                    sql = f"""
                        SELECT {self._select_columns()}
                        FROM file_search
                        JOIN file_entries fe ON fe.id=file_search.rowid
                        {self._usage_joins()}
                        WHERE {' AND '.join(where)}
                        LIMIT ?
                    """
                    try:
                        cursor = conn.execute(sql, [" AND ".join(tokens), *params, limit])
                        rows = cursor.fetchall()
                        if not add_rows(rows):
                            return result
                    except sqlite3.OperationalError:
                        pass

            # 2) 精确 / 前缀 / 包含。即使 FTS 命不中 CamelCase 中部也能找到。
            if keywords and len(result) < limit:
                text_clauses = []
                text_params: list = []
                for kw in keywords:
                    like = "%" + kw.casefold() + "%"
                    text_clauses.append(
                        "(fe.name_norm LIKE ? OR fe.stem_norm LIKE ? OR fe.path_norm LIKE ?)"
                    )
                    text_params.extend([like, like, like])
                where = clauses + text_clauses
                sql = f"""
                    SELECT {self._select_columns()}
                    FROM file_entries fe
                    {self._usage_joins()}
                    WHERE {' AND '.join(where)}
                    LIMIT ?
                """
                cursor = conn.execute(sql, [*params, *text_params, limit])
                rows = cursor.fetchall()
                if not add_rows(rows):
                    return result

            # 3) 模糊查询兜底：用首个关键词前 2~3 字符扩大候选，再交给 RapidFuzz。
            if keywords and len(result) < min(500, limit):
                first = keywords[0].casefold()
                prefix_len = 3 if len(first) >= 3 else min(2, len(first))
                if prefix_len:
                    prefix = first[:prefix_len] + "%"
                    where = clauses + ["(fe.name_norm LIKE ? OR fe.stem_norm LIKE ?)"]
                    sql = f"""
                        SELECT {self._select_columns()}
                        FROM file_entries fe
                        {self._usage_joins()}
                        WHERE {' AND '.join(where)}
                        LIMIT ?
                    """
                    cursor = conn.execute(sql, [*params, prefix, prefix, limit])
                    rows = cursor.fetchall()
                    add_rows(rows)

            return result[:limit]

    def recent_entries(
        self,
        root_ids: list[int] | None = None,
        item_type: str | None = None,
        category_extensions: list[str] | None = None,
        after_time: int | None = None,
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        clauses, params = self._base_filters(
            root_ids, item_type, category_extensions, after_time, None,
            None, None, None, None
        )
        sql = f"""
            SELECT {self._select_columns()}
            FROM file_entries fe
            {self._usage_joins()}
            WHERE {' AND '.join(clauses)}
            ORDER BY fe.modified_time DESC
            LIMIT ?
        """
        with self.connect() as conn:
            return list(conn.execute(sql, [*params, limit]).fetchall())
