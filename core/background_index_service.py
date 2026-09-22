from __future__ import annotations

import os
import threading
from collections import deque

from PySide6.QtCore import QObject, QThread, Signal

from core.index_builder import IndexBuilder
from database.database import Database


class _IndexWorker(QThread):
    progress = Signal(dict)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        database: Database,
        root_id: int,
        root_path: str,
        excluded_dirs: list[str],
        pause_event: threading.Event,
        parent=None,
    ):
        super().__init__(parent)
        self.database = database
        self.root_id = root_id
        self.root_path = root_path
        self.excluded_dirs = excluded_dirs
        self.pause_event = pause_event
        self.cancel_event = threading.Event()

    def run(self):
        try:
            builder = IndexBuilder(self.database, self.excluded_dirs)
            result = builder.build(
                self.root_id,
                self.root_path,
                cancel_event=self.cancel_event,
                pause_event=self.pause_event,
                progress_callback=self.progress.emit,
            )
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))

    def cancel(self):
        self.cancel_event.set()


class BackgroundIndexService(QObject):
    """Application-level single-worker index queue.

    One root is scanned at a time.  AutoIndexScheduler may temporarily pause the
    worker while the user is searching, so interactive search keeps priority.
    """

    job_started = Signal(int, str)
    progress = Signal(int, dict)
    job_finished = Signal(int, dict)
    job_failed = Signal(int, str)
    queue_changed = Signal(int)
    paused_changed = Signal(bool)
    idle = Signal()

    def __init__(
        self,
        database: Database,
        excluded_dirs: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.database = database
        self.excluded_dirs = list(excluded_dirs or [])
        self._queue: deque[tuple[int, str]] = deque()
        self._worker: _IndexWorker | None = None
        self._current_root_id: int | None = None
        self._current_path: str = ""
        self._last_progress: dict = {}
        self._shutting_down = False
        self._pause_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return bool(self._worker and self._worker.isRunning())

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    @property
    def current_root_id(self) -> int | None:
        return self._current_root_id

    @property
    def current_path(self) -> str:
        return self._current_path

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    def is_pending(self, root_id: int) -> bool:
        return any(item[0] == root_id for item in self._queue)

    @property
    def last_progress(self) -> dict:
        return dict(self._last_progress)

    def set_excluded_dirs(self, excluded_dirs: list[str] | None) -> None:
        self.excluded_dirs = list(excluded_dirs or [])

    def set_paused(self, paused: bool) -> None:
        old = self._pause_event.is_set()
        if paused:
            self._pause_event.set()
        else:
            self._pause_event.clear()
        if old != paused:
            self.paused_changed.emit(paused)

    def enqueue(self, root_id: int, path: str, force: bool = False) -> bool:
        path = os.path.abspath(os.path.normpath(path))
        if not os.path.isdir(path):
            return False

        if not force:
            if self._current_root_id == root_id:
                return False
            if any(item[0] == root_id for item in self._queue):
                return False

        self._queue.append((int(root_id), path))
        self.queue_changed.emit(len(self._queue))
        self._start_next()
        return True

    def enqueue_enabled_roots(self, force: bool = False) -> int:
        added = 0
        for root in self.database.list_search_roots(enabled_only=True):
            if self.enqueue(int(root["id"]), root["path"], force=force):
                added += 1
        return added

    def remove_pending(self, root_id: int) -> None:
        if not self._queue:
            return
        self._queue = deque(item for item in self._queue if item[0] != root_id)
        self.queue_changed.emit(len(self._queue))

    def cancel_current(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()

    def cancel_root(self, root_id: int) -> None:
        self.remove_pending(root_id)
        if self._current_root_id == root_id:
            self.cancel_current()

    def _start_next(self) -> None:
        if self._shutting_down or self.is_running:
            return

        while self._queue:
            root_id, path = self._queue.popleft()
            self.queue_changed.emit(len(self._queue))
            root = self.database.get_root(root_id)
            if root is None or not root["enabled"] or not os.path.isdir(path):
                continue

            self._current_root_id = root_id
            self._current_path = path
            self._last_progress = {}
            worker = _IndexWorker(
                self.database,
                root_id,
                path,
                self.excluded_dirs,
                self._pause_event,
                self,
            )
            self._worker = worker
            worker.progress.connect(self._on_progress)
            worker.done.connect(self._on_done)
            worker.failed.connect(self._on_failed)
            worker.finished.connect(worker.deleteLater)
            self.job_started.emit(root_id, path)
            # Disk maintenance should never compete aggressively with search/UI.
            worker.start(QThread.Priority.LowPriority)
            return

        self._current_root_id = None
        self._current_path = ""
        self._last_progress = {}
        self._worker = None
        self.idle.emit()

    def _on_progress(self, data: dict) -> None:
        if self._current_root_id is None:
            return
        self._last_progress = dict(data)
        self.progress.emit(self._current_root_id, data)

    def _on_done(self, data: dict) -> None:
        root_id = self._current_root_id
        self._worker = None
        self._current_root_id = None
        self._current_path = ""
        self._last_progress = {}
        if root_id is not None:
            self.job_finished.emit(root_id, data)
        self._start_next()

    def _on_failed(self, message: str) -> None:
        root_id = self._current_root_id
        self._worker = None
        self._current_root_id = None
        self._current_path = ""
        self._last_progress = {}
        if root_id is not None:
            self.job_failed.emit(root_id, message)
        self._start_next()

    def shutdown(self, wait_ms: int = 2500) -> None:
        self._shutting_down = True
        self._queue.clear()
        self._pause_event.clear()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(wait_ms)
        self._worker = None
