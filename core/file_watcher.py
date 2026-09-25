from __future__ import annotations

import os
import threading
import time

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except Exception:
    FileSystemEventHandler = object
    Observer = None
    WATCHDOG_AVAILABLE = False

from database.database import Database
from utils.path_utils import normalize_path, path_depth


class _Handler(FileSystemEventHandler):
    def __init__(self, database: Database, root_id: int, root_path: str, excluded_dirs: set[str]):
        super().__init__()
        self.database = database
        self.root_id = root_id
        self.root_path = root_path
        self.excluded_dirs = excluded_dirs
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._last_root_touch = 0.0

    def _ignored(self, path: str) -> bool:
        parts = [p.casefold() for p in os.path.normpath(path).split(os.sep)]
        return any(part in self.excluded_dirs for part in parts)

    def _debounced(self, path: str) -> bool:
        now = time.monotonic()
        with self._lock:
            old = self._last.get(path, 0.0)
            self._last[path] = now
            # Periodically prune entries older than the debounce window to prevent unbounded growth
            if len(self._last) > 1000 and (len(self._last) % 100 == 0):
                cutoff = now - 1.0
                self._last = {p: t for p, t in self._last.items() if t >= cutoff}
        return now - old < 0.15

    def _touch_root(self) -> None:
        # 高频文件变化时最多每秒写一次“上次更新”时间。
        now = time.monotonic()
        if now - self._last_root_touch < 1.0:
            return
        self._last_root_touch = now
        try:
            self.database.touch_root_update(self.root_id)
        except Exception:
            pass

    def _upsert(self, path: str, is_directory: bool | None = None):
        if self._ignored(path) or self._debounced(path):
            return
        try:
            if not os.path.exists(path):
                return
            is_dir = os.path.isdir(path) if is_directory is None else is_directory
            name = os.path.basename(path)
            stem = name if is_dir else os.path.splitext(name)[0]
            ext = "" if is_dir else os.path.splitext(name)[1].casefold()
            stat = os.stat(path, follow_symlinks=False)
            row = (
                self.root_id,
                name,
                name.casefold(),
                stem,
                stem.casefold(),
                ext,
                os.path.abspath(path),
                normalize_path(path),
                os.path.dirname(os.path.abspath(path)),
                1 if is_dir else 0,
                0 if is_dir else int(stat.st_size),
                int(stat.st_ctime),
                int(stat.st_mtime),
                path_depth(path),
                time.time_ns(),
            )
            self.database.upsert_single(row)
            self._touch_root()
        except (OSError, PermissionError):
            pass

    def on_created(self, event):
        self._upsert(event.src_path, event.is_directory)

    def on_modified(self, event):
        self._upsert(event.src_path, event.is_directory)

    def on_deleted(self, event):
        if not self._ignored(event.src_path):
            self.database.delete_path(event.src_path, event.is_directory)
            self._touch_root()

    def on_moved(self, event):
        if not self._ignored(event.src_path):
            self.database.delete_path(event.src_path, event.is_directory)
        self._upsert(event.dest_path, event.is_directory)
        self._touch_root()


class FileWatcherManager:
    def __init__(self, database: Database, excluded_dirs: list[str] | None = None):
        self.database = database
        self.excluded_dirs = {x.casefold() for x in (excluded_dirs or [])}
        self.observers = []
        self._operation_lock = threading.RLock()
        self._generation = 0

    @property
    def available(self) -> bool:
        return WATCHDOG_AVAILABLE

    def _stop_locked(self) -> None:
        observers = list(self.observers)
        self.observers.clear()
        for observer in observers:
            try:
                observer.stop()
            except Exception:
                pass
        for observer in observers:
            try:
                observer.join(timeout=1.5)
            except Exception:
                pass

    def _start_generation(self, generation: int) -> None:
        with self._operation_lock:
            if generation != self._generation:
                return
            self._stop_locked()
            if not WATCHDOG_AVAILABLE or generation != self._generation:
                return

            new_observers = []
            for root in self.database.list_search_roots(enabled_only=True):
                if generation != self._generation:
                    break
                path = root["path"]
                if not os.path.isdir(path):
                    continue
                try:
                    handler = _Handler(self.database, int(root["id"]), path, self.excluded_dirs)
                    observer = Observer()
                    observer.schedule(handler, path, recursive=True)
                    observer.daemon = True
                    observer.start()
                    new_observers.append(observer)
                except Exception:
                    # A single unavailable/network root must not prevent the app
                    # from starting or other roots from being watched.
                    continue

            if generation != self._generation:
                for observer in new_observers:
                    try:
                        observer.stop()
                        observer.join(timeout=0.5)
                    except Exception:
                        pass
                return
            self.observers = new_observers

    def start(self) -> None:
        """Synchronous start. Prefer start_async() from UI code."""
        self._generation += 1
        generation = self._generation
        self._start_generation(generation)

    def start_async(self) -> None:
        """Start watchdog outside the Qt GUI thread.

        Recursive watcher registration can be slow on large/network roots.  The
        previous implementation performed it inside MainWindow.__init__, which
        could make FastFinder appear frozen for many seconds.
        """
        self._generation += 1
        generation = self._generation
        threading.Thread(
            target=self._start_generation,
            args=(generation,),
            name="FastFinder-WatcherStart",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self._generation += 1
        with self._operation_lock:
            self._stop_locked()

    def stop_async(self) -> None:
        self._generation += 1
        threading.Thread(
            target=self.stop,
            name="FastFinder-WatcherStop",
            daemon=True,
        ).start()

    def restart(self) -> None:
        self.start()

    def restart_async(self) -> None:
        self.start_async()
