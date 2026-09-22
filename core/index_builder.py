from __future__ import annotations

import os
import threading
import time
from typing import Callable

from database.database import Database
from utils.path_utils import normalize_path, path_depth


class IndexBuilder:
    def __init__(
        self,
        database: Database,
        excluded_dirs: list[str] | None = None,
        batch_size: int = 1200,
    ):
        self.database = database
        self.excluded_dirs = {x.casefold() for x in (excluded_dirs or [])}
        self.batch_size = max(200, batch_size)

    @staticmethod
    def _wait_if_paused(
        pause_event: threading.Event | None,
        cancel_event: threading.Event,
    ) -> bool:
        """Return False when cancellation was requested while paused."""
        if pause_event is None:
            return not cancel_event.is_set()
        while pause_event.is_set() and not cancel_event.is_set():
            cancel_event.wait(0.08)
        return not cancel_event.is_set()

    def build(
        self,
        root_id: int,
        root_path: str,
        cancel_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict:
        """Incrementally synchronize one long-term root.

        The disk tree is still walked so additions/deletions can be detected,
        but unchanged rows are no longer rewritten into the main SQLite table.
        This sharply reduces WAL/FTS churn for large, mostly unchanged indexes.

        Search can temporarily pause this worker before the next folder/batch.
        Existing SQLite data remains available while synchronization is running.
        """
        cancel_event = cancel_event or threading.Event()
        root_path = os.path.abspath(os.path.normpath(root_path))
        start = time.perf_counter()
        scan_generation = time.time_ns()

        files = 0
        folders = 0
        added = 0
        updated = 0
        unchanged = 0
        removed = 0
        stack = [root_path]
        batch: list[tuple] = []
        last_emit = 0.0
        scanned_since_pause_check = 0

        conn = self.database.connect()
        self.database.begin_incremental_scan(conn)

        def flush_batch() -> None:
            nonlocal added, updated, unchanged
            if not batch:
                return
            stats = self.database.sync_scan_batch(root_id, batch, conn)
            added += int(stats.get("added", 0))
            updated += int(stats.get("updated", 0))
            unchanged += int(stats.get("unchanged", 0))
            conn.commit()
            batch.clear()

        try:
            while stack and not cancel_event.is_set():
                if not self._wait_if_paused(pause_event, cancel_event):
                    break

                current = stack.pop()
                try:
                    with os.scandir(current) as entries:
                        for entry in entries:
                            if cancel_event.is_set():
                                break

                            scanned_since_pause_check += 1
                            if scanned_since_pause_check >= 128:
                                scanned_since_pause_check = 0
                                if not self._wait_if_paused(pause_event, cancel_event):
                                    break

                            try:
                                is_dir = entry.is_dir(follow_symlinks=False)
                                if is_dir and entry.name.casefold() in self.excluded_dirs:
                                    continue
                                if not is_dir and not entry.is_file(follow_symlinks=False):
                                    continue

                                full_path = os.path.abspath(entry.path)
                                parent = os.path.dirname(full_path)
                                name = entry.name
                                stem = name if is_dir else os.path.splitext(name)[0]
                                ext = "" if is_dir else os.path.splitext(name)[1].casefold()

                                try:
                                    stat = entry.stat(follow_symlinks=False)
                                    created = int(stat.st_ctime)
                                    modified = int(stat.st_mtime)
                                    size = 0 if is_dir else int(stat.st_size)
                                except OSError:
                                    created = modified = 0
                                    size = 0

                                batch.append((
                                    root_id,
                                    name,
                                    name.casefold(),
                                    stem,
                                    stem.casefold(),
                                    ext,
                                    full_path,
                                    normalize_path(full_path),
                                    parent,
                                    1 if is_dir else 0,
                                    size,
                                    created,
                                    modified,
                                    path_depth(full_path),
                                    scan_generation,
                                ))

                                if is_dir:
                                    folders += 1
                                    stack.append(full_path)
                                else:
                                    files += 1

                                if len(batch) >= self.batch_size:
                                    if not self._wait_if_paused(pause_event, cancel_event):
                                        break
                                    flush_batch()

                                now = time.perf_counter()
                                if progress_callback and now - last_emit >= 0.25:
                                    elapsed = max(0.001, now - start)
                                    progress_callback({
                                        "files": files,
                                        "folders": folders,
                                        "written": added + updated,
                                        "added": added,
                                        "updated": updated,
                                        "unchanged": unchanged,
                                        "removed": removed,
                                        "elapsed": elapsed,
                                        "speed": int((files + folders) / elapsed),
                                        "current": full_path,
                                    })
                                    last_emit = now

                            except (PermissionError, FileNotFoundError, OSError):
                                continue

                except (PermissionError, FileNotFoundError, OSError):
                    continue

            # Persist the already-scanned tail.  If cancelled we deliberately do
            # NOT remove stale rows because the tree was not completely visited.
            if batch:
                if self._wait_if_paused(pause_event, cancel_event):
                    flush_batch()
                elif cancel_event.is_set():
                    # Search pause can turn into shutdown cancellation.  The
                    # unflushed tail is simply discarded; prior index remains.
                    batch.clear()

            elapsed = time.perf_counter() - start
            total = files + folders

            if cancel_event.is_set():
                self.database.abort_incremental_scan(conn)
                conn.commit()
                return {
                    "cancelled": True,
                    "files": files,
                    "folders": folders,
                    "written": added + updated,
                    "added": added,
                    "updated": updated,
                    "unchanged": unchanged,
                    "removed": 0,
                    "elapsed": elapsed,
                    "speed": int(total / max(elapsed, 0.001)),
                }

            removed = self.database.finish_incremental_scan(root_id, conn)
            self.database.update_root_scan_stats(
                root_id,
                total,
                elapsed,
                conn=conn,
            )
            conn.commit()

            return {
                "cancelled": False,
                "files": files,
                "folders": folders,
                "written": added + updated,
                "added": added,
                "updated": updated,
                "unchanged": unchanged,
                "removed": removed,
                "elapsed": elapsed,
                "speed": int(total / max(elapsed, 0.001)),
            }
        finally:
            try:
                self.database.abort_incremental_scan(conn)
            except Exception:
                pass
            conn.close()
