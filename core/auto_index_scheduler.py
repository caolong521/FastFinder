from __future__ import annotations

import os
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from core.background_index_service import BackgroundIndexService
from database.database import Database


class AutoIndexScheduler(QObject):
    """
    Smart background index scheduler.

    Design goals:
    - FastFinder starts immediately; no full scan blocks startup.
    - Long-term roots are checked automatically after a configurable startup delay.
    - Periodic maintenance is based on each root's size and last successful scan.
    - Only one directory is indexed at a time (delegated to BackgroundIndexService).
    - Search has priority: an active index worker pauses before the next batch/folder.
    - watchdog remains optional and is not required for normal automatic maintenance.
    """

    status_changed = Signal(str)
    schedule_changed = Signal()

    SMART_SMALL_LIMIT = 20_000
    SMART_MEDIUM_LIMIT = 200_000

    SMART_SMALL_MINUTES = 30
    SMART_MEDIUM_MINUTES = 60
    SMART_LARGE_MINUTES = 180

    MODE_MINUTES = {
        "30m": 30,
        "60m": 60,
        "180m": 180,
        "daily": 24 * 60,
    }

    def __init__(
        self,
        database: Database,
        index_service: BackgroundIndexService,
        settings: dict,
        is_window_hidden: Callable[[], bool] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.database = database
        self.index_service = index_service
        self.settings = dict(settings)
        self.is_window_hidden = is_window_hidden or (lambda: False)

        self._started = False
        self._startup_cycle_active = False
        self._last_activity = time.monotonic()
        self._active_searches = 0
        self._last_status = ""

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(60_000)
        self._poll_timer.timeout.connect(self._periodic_check)

        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(self._startup_check)

        self._resume_after_search_timer = QTimer(self)
        self._resume_after_search_timer.setSingleShot(True)
        self._resume_after_search_timer.setInterval(1800)
        self._resume_after_search_timer.timeout.connect(self._resume_after_search)

        self.index_service.job_finished.connect(self._on_job_finished)
        self.index_service.job_failed.connect(self._on_job_failed)
        self.index_service.idle.connect(self._on_service_idle)

    # ------------------------------------------------------------------
    # Lifecycle / settings
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.apply_settings(self.settings, reschedule_startup=True)

    def stop(self) -> None:
        self._started = False
        self._startup_cycle_active = False
        self._poll_timer.stop()
        self._startup_timer.stop()
        self._resume_after_search_timer.stop()
        self.index_service.set_paused(False)

    def apply_settings(self, settings: dict, reschedule_startup: bool = False) -> None:
        self.settings = dict(settings)
        self._recalculate_root_schedules()

        if not self._started:
            return

        enabled = self._automatic_enabled()
        mode = self._mode()

        if enabled and mode not in ("startup_only", "off"):
            self._poll_timer.start()
        else:
            self._poll_timer.stop()

        if reschedule_startup:
            self._startup_timer.stop()
            if enabled and self.settings.get("auto_index_startup_check", True):
                delay = max(0, int(self.settings.get("auto_index_startup_delay_sec", 20)))
                self._startup_timer.start(delay * 1000)

        if not enabled:
            self._startup_cycle_active = False
            self.index_service.set_paused(False)
            self._set_status("长期索引自动维护已关闭")
        else:
            self._set_status("长期索引自动维护已启用")

    def roots_changed(self) -> None:
        self._recalculate_root_schedules()
        if self._started:
            QTimer.singleShot(150, self.check_due_roots)

    # ------------------------------------------------------------------
    # Search priority / idle tracking
    # ------------------------------------------------------------------
    def notify_user_activity(self) -> None:
        self._last_activity = time.monotonic()

    def search_started(self) -> None:
        self._active_searches += 1
        self._last_activity = time.monotonic()
        self._resume_after_search_timer.stop()
        if self.settings.get("auto_index_pause_on_search", True):
            self.index_service.set_paused(True)

    def search_finished(self) -> None:
        self._active_searches = max(0, self._active_searches - 1)
        self._last_activity = time.monotonic()
        if self._active_searches == 0:
            self._resume_after_search_timer.start()

    def _resume_after_search(self) -> None:
        if self._active_searches:
            return
        self.index_service.set_paused(False)
        QTimer.singleShot(100, self.check_due_roots)

    def _idle_enough(self) -> bool:
        if not self.settings.get("auto_index_idle_first", True):
            return True
        try:
            if self.is_window_hidden():
                return True
        except Exception:
            pass
        idle_seconds = max(5, int(self.settings.get("auto_index_idle_seconds", 120)))
        return (time.monotonic() - self._last_activity) >= idle_seconds

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------
    def _mode(self) -> str:
        return str(self.settings.get("auto_index_mode", "smart") or "smart")

    def _automatic_enabled(self) -> bool:
        return bool(self.settings.get("auto_index_enabled", True)) and self._mode() != "off"

    def interval_minutes_for_root(self, root) -> int | None:
        mode = self._mode()
        if mode in ("off", "startup_only"):
            return None
        if mode == "smart":
            count = int(root["item_count"] or 0)
            if count < self.SMART_SMALL_LIMIT:
                return self.SMART_SMALL_MINUTES
            if count < self.SMART_MEDIUM_LIMIT:
                return self.SMART_MEDIUM_MINUTES
            return self.SMART_LARGE_MINUTES
        return self.MODE_MINUTES.get(mode, 60)

    def _next_scan_for_root(self, root, now: int | None = None) -> tuple[int | None, int | None]:
        now = int(now or time.time())
        interval = self.interval_minutes_for_root(root)
        if interval is None:
            return None, None
        last_scan = int(root["last_scan_time"] or 0)
        if last_scan <= 0:
            return interval, now
        return interval, last_scan + interval * 60

    def _recalculate_root_schedules(self) -> None:
        if not self._automatic_enabled():
            self.database.clear_next_scan_times()
            self.schedule_changed.emit()
            return

        now = int(time.time())
        for root in self.database.list_search_roots(enabled_only=False):
            root_id = int(root["id"])
            if not root["enabled"]:
                self.database.update_root_schedule(root_id, None, None)
                continue
            interval, next_scan = self._next_scan_for_root(root, now)
            self.database.update_root_schedule(root_id, interval, next_scan)
        self.schedule_changed.emit()

    def _startup_check(self) -> None:
        if not self._automatic_enabled():
            return
        self._startup_cycle_active = True
        self._recalculate_root_schedules()
        self.check_due_roots(startup=True)

    def _periodic_check(self) -> None:
        self.check_due_roots(startup=False)

    def check_due_roots(self, startup: bool = False) -> None:
        if not self._automatic_enabled():
            return
        if self.index_service.is_running or self.index_service.pending_count:
            return
        if self._active_searches:
            return
        if not self._idle_enough():
            self._set_status("长期索引已到检查时间，等待 FastFinder 空闲后更新")
            if self._startup_cycle_active and self._mode() == "startup_only":
                QTimer.singleShot(30_000, lambda: self.check_due_roots(startup=True))
            return

        now = int(time.time())
        due = []
        mode = self._mode()
        for root in self.database.list_search_roots(enabled_only=True):
            path = root["path"]
            if not os.path.isdir(path):
                continue

            # In startup-only mode, a startup compensation checks roots that have
            # never been scanned or whose last scan is older than the default
            # medium smart interval (60 min). There is no periodic timer later.
            if mode == "startup_only":
                if not (startup or self._startup_cycle_active):
                    continue
                last_scan = int(root["last_scan_time"] or 0)
                if last_scan and now - last_scan < 60 * 60:
                    continue
                due_at = last_scan or 0
            else:
                next_scan = root["next_scan_time"]
                if next_scan is None:
                    interval, next_scan = self._next_scan_for_root(root, now)
                    self.database.update_root_schedule(int(root["id"]), interval, next_scan)
                if next_scan is not None and int(next_scan) > now:
                    continue
                due_at = int(next_scan or 0)

            due.append((due_at, int(root["id"]), path))

        if not due:
            self._startup_cycle_active = False
            self._set_status("长期索引已是最新状态")
            self.schedule_changed.emit()
            return

        # Oldest/never-scanned first.  Only enqueue one root; when it finishes,
        # the scheduler decides whether the next due root may run.
        due.sort(key=lambda item: item[0])
        _, root_id, path = due[0]
        if self.index_service.enqueue(root_id, path):
            self._set_status(f"后台自动更新：{path}")

    def _on_job_finished(self, root_id: int, data: dict) -> None:
        root = self.database.get_root(root_id)
        if root is not None and root["enabled"]:
            if self._automatic_enabled():
                interval, next_scan = self._next_scan_for_root(root)
                self.database.update_root_schedule(root_id, interval, next_scan)
            else:
                self.database.update_root_schedule(root_id, None, None)
        self.schedule_changed.emit()
        if self._automatic_enabled():
            QTimer.singleShot(250, self.check_due_roots)

    def _on_job_failed(self, root_id: int, message: str) -> None:
        # Retry later rather than looping immediately on an inaccessible drive.
        root = self.database.get_root(root_id)
        if self._automatic_enabled() and root is not None and root["enabled"]:
            interval = self.interval_minutes_for_root(root) or 60
            retry = int(time.time()) + min(interval, 30) * 60
            self.database.update_root_schedule(root_id, interval, retry)
        self.schedule_changed.emit()
        self._set_status(f"自动更新失败，稍后重试：{message}")

    def _on_service_idle(self) -> None:
        if self._automatic_enabled():
            QTimer.singleShot(250, self.check_due_roots)

    def _set_status(self, text: str) -> None:
        if text == self._last_status:
            return
        self._last_status = text
        self.status_changed.emit(text)
