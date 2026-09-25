from __future__ import annotations

import math
import os
import subprocess
import sys
import time

from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool, QTimer, Signal, Slot, Qt
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.search_result_model import SearchResultModel
from database.database import Database
from utils.path_utils import normalize_path
from utils.resource_path import resource_path
from utils.settings import SettingsStore
from utils.time_utils import days_ago_ts, start_of_today_ts, start_of_year_ts


class SearchWorkerSignals(QObject):
    finished = Signal(int, object, int, float, str, str)


class SearchWorker(QRunnable):
    def __init__(
        self,
        search_id: int,
        database: Database,
        settings: dict,
        text: str,
        options,
    ):
        super().__init__()
        self.search_id = search_id
        self.database = database
        self.settings = settings
        self.text = text
        self.options = options
        self.signals = SearchWorkerSignals()

    @Slot()
    def run(self):
        started = time.perf_counter()
        error = ""
        results = []
        total = 0
        try:
            from core.search_service import SearchService

            service = SearchService(
                self.database,
                fuzzy_threshold=int(self.settings.get("fuzzy_threshold", 58)),
                behavior_ranking=bool(self.settings.get("usage_ranking_enabled", True)),
            )
            results, total = service.search(self.text, self.options)
        except Exception as exc:
            error = str(exc)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.signals.finished.emit(
            self.search_id, results, total, elapsed_ms, error, "index"
        )


class DirectSearchWorker(QRunnable):
    def __init__(
        self,
        search_id: int,
        database: Database,
        entries: list,
        settings: dict,
        text: str,
        options,
    ):
        super().__init__()
        self.search_id = search_id
        self.database = database
        self.entries = entries
        self.settings = settings
        self.text = text
        self.options = options
        self.signals = SearchWorkerSignals()

    @Slot()
    def run(self):
        started = time.perf_counter()
        error = ""
        results = []
        total = 0
        try:
            from core.direct_search_service import DirectSearchService

            service = DirectSearchService(
                self.entries,
                fuzzy_threshold=int(self.settings.get("fuzzy_threshold", 58)),
            )
            results, total = service.search(self.text, self.options)
            behavior = self.database.behavior_map([r.full_path for r in results])
            for result in results:
                data = behavior.get(normalize_path(result.full_path))
                if data:
                    result.is_favorite = bool(data.get("is_favorite", False))
                    result.open_count = int(data.get("open_count", 0))
                    result.last_opened_time = int(data.get("last_opened_time", 0))
                    if self.settings.get("usage_ranking_enabled", True) and self.options.sort_by == "relevance":
                        bonus = 45.0 if result.is_favorite else 0.0
                        if result.open_count:
                            bonus += min(50.0, 12.0 * math.log2(result.open_count + 1.0))
                        if result.last_opened_time:
                            age_days = max(0.0, (time.time() - result.last_opened_time) / 86400.0)
                            bonus += max(0.0, 35.0 - 7.0 * math.log2(age_days + 1.0))
                        result.score += bonus
            if self.options.sort_by == "relevance":
                results.sort(key=lambda x: (x.score, x.modified_time), reverse=True)
        except Exception as exc:
            error = str(exc)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.signals.finished.emit(
            self.search_id, results, total, elapsed_ms, error, "direct"
        )


class DirectScanSignals(QObject):
    progress = Signal(int, object)
    finished = Signal(int, str, object, object, str)


class DirectScanWorker(QRunnable):
    def __init__(self, scan_id: int, path: str, excluded_dirs: list[str]):
        super().__init__()
        self.scan_id = scan_id
        self.path = path
        self.excluded_dirs = excluded_dirs
        self.signals = DirectScanSignals()

    @Slot()
    def run(self):
        error = ""
        entries = []
        stats = {}
        try:
            from core.direct_search_service import DirectDirectoryScanner

            scanner = DirectDirectoryScanner(self.excluded_dirs)
            entries, stats = scanner.scan(
                self.path,
                progress_callback=lambda data: self.signals.progress.emit(
                    self.scan_id, data
                ),
            )
        except Exception as exc:
            error = str(exc)
        self.signals.finished.emit(
            self.scan_id, self.path, entries, stats, error
        )


class DatabaseMaintenanceSignals(QObject):
    finished = Signal(object, str)


class DatabaseMaintenanceWorker(QRunnable):
    """Low-priority DB compatibility work that must never block startup."""

    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        self.signals = DatabaseMaintenanceSignals()

    @Slot()
    def run(self):
        result = {}
        error = ""
        try:
            result = self.database.ensure_fts_ready()
        except Exception as exc:
            error = str(exc)
        self.signals.finished.emit(result, error)


class MainWindow(QMainWindow):
    def __init__(self, database: Database, settings_store: SettingsStore, startup_profiler=None):
        super().__init__()
        self.database = database
        self.settings_store = settings_store
        self.settings = self.settings_store.load()
        self._startup_profiler = startup_profiler
        self.pool = QThreadPool.globalInstance()
        self.search_id = 0
        self.direct_scan_id = 0
        self.model = SearchResultModel(self)
        # search_roots.item_count is a tiny O(root-count) estimate; do not COUNT(*)
        # the large file_entries table during startup.
        self.indexed_count = self.database.estimated_entry_count()

        # Heavy/optional windows and services are intentionally None at startup.
        # They are imported + created only when the user actually needs them.
        self.index_window = None
        self.history_window = None
        self.favorites_window = None
        self.quick_window = None
        self.startup_manager = None
        self.index_service = None
        self.watcher = None
        self.auto_scheduler = None
        self.global_hotkey = None
        self.quick_hotkey = None
        self.history_string_model = None
        self.history_completer = None

        self.direct_entries: list = []
        self.direct_loaded_path = ""
        self.direct_scan_running = False

        self._really_quit = False
        self._shutdown_done = False
        self._tray_hint_shown = False
        self._responsive_compact_state = None
        self._background_services_started = False
        self._database_maintenance_started = False
        self._shell_integrations_started = False

        self.setWindowTitle("FastFinder - 本地极速文件查找")
        icon_path = resource_path("resources", "fastfinder.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._fit_initial_geometry()
        self.setAcceptDrops(True)
        self._startup_mark("MainWindow base state")

        # Only build the visible search UI on the critical startup path.
        self._build_ui()
        self._startup_mark("MainWindow visible UI built")
        self._build_shortcuts()
        self.refresh_roots()
        self._startup_mark("MainWindow roots loaded")

        # First search may happen after the event loop starts; no need to block
        # construction on history, quick-window, tray or background services.
        self.schedule_search()

        # 0ms means: run after QApplication enters the event loop and the main
        # window has had a chance to paint.
        QTimer.singleShot(0, self._start_shell_integrations)
        QTimer.singleShot(350, self._start_deferred_services)
        QTimer.singleShot(1500, self._ensure_history_completer)

    def _startup_mark(self, name: str):
        profiler = getattr(self, "_startup_profiler", None)
        if profiler is not None:
            try:
                profiler.mark(name)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Lazy startup helpers
    # ------------------------------------------------------------------
    def _start_shell_integrations(self):
        """Create tray + native global hotkeys after the first paint."""
        if self._shell_integrations_started or self._shutdown_done:
            return
        self._shell_integrations_started = True

        try:
            from core.global_hotkey import GlobalHotkeyManager

            self.global_hotkey = GlobalHotkeyManager(self, hotkey_id=0x4F46)
            self.global_hotkey.activated.connect(self.toggle_window_visibility)
            self.quick_hotkey = GlobalHotkeyManager(self, hotkey_id=0x4F47)
            self.quick_hotkey.activated.connect(self.toggle_quick_search)
        except Exception:
            self.global_hotkey = None
            self.quick_hotkey = None

        self._build_tray()
        hotkey_ok, hotkey_error = self._apply_settings()
        if not hotkey_ok and hotkey_error:
            QTimer.singleShot(900, lambda e=hotkey_error: self._notify_hotkey_problem(e))
        self._startup_mark("tray + global hotkeys deferred")

    def _ensure_background_core(self, start_scheduler: bool = False):
        """Create indexing scheduler/services only when background work is needed."""
        if self.index_service is None:
            from core.background_index_service import BackgroundIndexService

            self.index_service = BackgroundIndexService(
                self.database,
                self.settings.get("excluded_dirs", []),
                self,
            )
            self.index_service.job_finished.connect(self.on_background_index_finished)
            self.index_service.job_failed.connect(self.on_background_index_failed)

        if self.auto_scheduler is None:
            from core.auto_index_scheduler import AutoIndexScheduler

            self.auto_scheduler = AutoIndexScheduler(
                self.database,
                self.index_service,
                self.settings,
                is_window_hidden=lambda: not self.isVisible(),
                parent=self,
            )
            self.auto_scheduler.status_changed.connect(self._on_auto_index_status)

        if start_scheduler:
            self.auto_scheduler.start()
        return self.index_service, self.auto_scheduler

    def _ensure_watcher(self):
        if self.watcher is None:
            from core.file_watcher import FileWatcherManager

            self.watcher = FileWatcherManager(
                self.database,
                self.settings.get("excluded_dirs", []),
            )
        return self.watcher

    def _ensure_quick_window(self):
        if self.quick_window is not None:
            return self.quick_window

        from app.quick_search_window import QuickSearchWindow

        window = QuickSearchWindow(self.database, lambda: self.settings)
        window.open_requested.connect(self._quick_open_result)
        window.reveal_requested.connect(self._quick_reveal_result)
        window.history_requested.connect(self._record_history_text)
        window.search_started.connect(self._priority_search_started)
        window.search_finished.connect(self._priority_search_finished)
        self.quick_window = window
        return window

    def _ensure_history_completer(self):
        if self._shutdown_done or self.history_completer is not None:
            return
        if not self.settings.get("search_history_enabled", True):
            return

        from PySide6.QtCore import QStringListModel
        from PySide6.QtWidgets import QCompleter

        self.refresh_history_completer()

    def _priority_user_activity(self):
        if self.auto_scheduler is not None:
            self.auto_scheduler.notify_user_activity()

    def _priority_search_started(self):
        if self.auto_scheduler is not None:
            self.auto_scheduler.search_started()

    def _priority_search_finished(self):
        if self.auto_scheduler is not None:
            self.auto_scheduler.search_finished()

    # ------------------------------------------------------------------
    # Deferred startup / maintenance
    # ------------------------------------------------------------------
    def _start_deferred_services(self):
        """Start expensive services only after the first window paint."""
        if self._background_services_started or self._shutdown_done:
            return
        self._background_services_started = True

        roots = self.database.list_search_roots()
        if not roots:
            QTimer.singleShot(250, self._first_run_hint)

        # Construct the scheduler/index service only now.  Their modules are not
        # imported on the critical MainWindow construction path.
        self._ensure_background_core(start_scheduler=True)

        # watchdog is an advanced opt-in. Importing watchdog and creating the
        # observer is fully deferred and skipped for the default configuration.
        if roots and self.settings.get("watch_changes", False):
            self._ensure_watcher().start_async()

        self._startup_mark("background scheduler deferred")

        # Old databases that predate FTS may need an expensive one-time rebuild.
        QTimer.singleShot(45000, self._start_database_maintenance)

    def _start_database_maintenance(self):
        if self._database_maintenance_started or self._shutdown_done:
            return
        # Do not compete with an active filesystem re-index for disk bandwidth.
        if self.index_service is not None and self.index_service.is_running:
            QTimer.singleShot(3000, self._start_database_maintenance)
            return
        self._database_maintenance_started = True
        worker = DatabaseMaintenanceWorker(self.database)
        worker.signals.finished.connect(self._on_database_maintenance_finished)
        # Keep a reference until QRunnable completes; this also makes debugging
        # startup maintenance easier.
        self._database_maintenance_worker = worker
        self.pool.start(worker)

    def _on_database_maintenance_finished(self, result, error: str):
        if error:
            # Maintenance failure must never prevent searching.
            return
        if isinstance(result, dict) and result.get("rebuilt"):
            if hasattr(self, "status_label") and not self.search_edit.text().strip():
                self.status_label.setText(
                    f"全文搜索索引已在后台完成兼容升级 · {float(result.get('elapsed', 0)):.1f}s"
                )

    # ------------------------------------------------------------------
    # Window geometry / responsive layout
    # ------------------------------------------------------------------
    def _fit_initial_geometry(self):
        """按当前主显示器的可用工作区设置初始窗口，并保证窗口完整落在屏幕内。"""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.setMinimumSize(680, 500)
            self.resize(1100, 720)
            return

        available = screen.availableGeometry()
        min_w = min(760, max(560, int(available.width() * 0.62)))
        min_h = min(560, max(420, int(available.height() * 0.62)))
        self.setMinimumSize(min_w, min_h)

        width = min(1240, max(min_w, int(available.width() * 0.90)))
        height = min(800, max(min_h, int(available.height() * 0.88)))
        x = available.x() + max(0, (available.width() - width) // 2)
        y = available.y() + max(0, (available.height() - height) // 2)
        self.setGeometry(x, y, width, height)

    def _update_responsive_ui(self):
        if not hasattr(self, "table"):
            return

        width = max(1, self.width())
        compact = width < 1000
        very_compact = width < 820

        # 小窗口减少边距，尽可能把空间留给结果表格。
        if hasattr(self, "main_layout"):
            margin = 10 if compact else 18
            self.main_layout.setContentsMargins(margin, 10 if compact else 16, margin, 10)

        # 说明文本不再决定窗口最小宽度。很窄时只保留最重要信息。
        if hasattr(self, "result_hint"):
            self.result_hint.setVisible(not very_compact)
        if hasattr(self, "index_hint"):
            self.index_hint.setVisible(not very_compact)
        if hasattr(self, "direct_note"):
            self.direct_note.setVisible(not very_compact)

        # 只有真正跨过紧凑模式阈值时才改按钮文案，避免 resizeEvent 中频繁查数据库。
        if self._responsive_compact_state != very_compact:
            self._responsive_compact_state = very_compact
            if hasattr(self, "index_mode_btn"):
                self.index_mode_btn.setText("索引" if very_compact else "索引搜索")
                self.direct_mode_btn.setText("目录" if very_compact else "指定目录")
            if hasattr(self, "direct_scan_btn"):
                self.direct_scan_btn.setText("载入" if very_compact else "载入 / 刷新")
                self.direct_clear_btn.setText("清缓存" if very_compact else "清除临时缓存")
                self._update_direct_save_state()
            if hasattr(self, "favorites_btn"):
                self.favorites_btn.setText("★" if very_compact else "收藏")
                self.history_btn.setText("历史" if very_compact else "历史")
                self.index_btn.setText("索引" if very_compact else "索引库")
                self.settings_btn.setText("设置" if very_compact else "设置")

        if hasattr(self, "search_edit"):
            if very_compact:
                self.search_edit.setPlaceholderText("搜索文件 / 文件夹，支持 ext:dll、type:folder…")
            else:
                self.search_edit.setPlaceholderText(
                    "搜索文件名或文件夹名，例如：modbus config；支持 ext:dll  type:folder  path:project  after:2026-09-01"
                )

        # 结果表格自适应：窄窗口优先保证“名称 + 路径 + 修改时间”。
        viewport_w = max(500, self.table.viewport().width())
        show_size = width >= 860
        show_score = width >= 940
        self.table.setColumnHidden(4, not show_size)
        self.table.setColumnHidden(5, not show_score)

        fixed = 78 + 132
        if show_size:
            fixed += 88
        if show_score:
            fixed += 76
        flexible = max(260, viewport_w - fixed - 12)
        name_w = max(150, int(flexible * (0.40 if compact else 0.36)))
        path_w = max(180, flexible - name_w)

        self.table.setColumnWidth(0, 78)
        self.table.setColumnWidth(1, name_w)
        self.table.setColumnWidth(2, path_w)
        self.table.setColumnWidth(3, 132)
        if show_size:
            self.table.setColumnWidth(4, 88)
        if show_score:
            self.table.setColumnWidth(5, 76)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # resizeEvent 很频繁，但这里只有简单布局计算，不启动搜索。
        self._update_responsive_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        self._startup_mark("UI: begin")
        central = QWidget(self)
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        self.main_layout = main
        main.setContentsMargins(18, 16, 18, 12)
        main.setSpacing(10)

        # 标题栏
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("FastFinder")
        title.setObjectName("TitleLabel")
        subtitle = QLabel("本地文件 / 文件夹极速查找 · 索引搜索 + 指定目录临时搜索")
        subtitle.setObjectName("SubtleLabel")
        subtitle.setWordWrap(True)
        self.subtitle_label = subtitle
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()

        self.favorites_btn = QPushButton("收藏")
        self.history_btn = QPushButton("历史")
        self.index_btn = QPushButton("索引库")
        self.settings_btn = QPushButton("设置")
        self.favorites_btn.clicked.connect(self.open_favorites)
        self.history_btn.clicked.connect(self.open_history)
        self.index_btn.clicked.connect(self.open_index_manager)
        self.settings_btn.clicked.connect(self.open_settings)
        header.addWidget(self.favorites_btn)
        header.addWidget(self.history_btn)
        header.addWidget(self.index_btn)
        header.addWidget(self.settings_btn)
        main.addLayout(header)

        # 大搜索框
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("SearchEdit")
        self.search_edit.setPlaceholderText(
            "搜索文件名或文件夹名，例如：modbus config；支持 ext:dll  type:folder  path:project  after:2026-09-01"
        )
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.on_search_text_changed)
        main.addWidget(self.search_edit)
        self._startup_mark("UI: header + search")

        # 搜索来源卡片
        source_card = QFrame()
        source_card.setObjectName("Card")
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(12, 10, 12, 10)
        source_layout.setSpacing(8)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("搜索方式："))
        self.index_mode_btn = QPushButton("索引搜索")
        self.direct_mode_btn = QPushButton("指定目录")
        for btn in (self.index_mode_btn, self.direct_mode_btn):
            btn.setObjectName("ModeButton")
            btn.setCheckable(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.index_mode_btn)
        self.mode_group.addButton(self.direct_mode_btn)
        self.index_mode_btn.setChecked(True)
        self.index_mode_btn.clicked.connect(lambda: self.set_search_mode("index"))
        self.direct_mode_btn.clicked.connect(lambda: self.set_search_mode("direct"))
        mode_row.addWidget(self.index_mode_btn)
        mode_row.addWidget(self.direct_mode_btn)
        mode_row.addStretch()
        source_layout.addLayout(mode_row)

        # 长说明单独占一行，并允许自动换行。
        # 之前把说明文字和模式按钮塞在同一行，会把窗口的最小宽度撑得很大。
        self.source_hint = QLabel("索引库：适合常用目录，打开即搜，后台自动保持更新")
        self.source_hint.setObjectName("ModeHint")
        self.source_hint.setWordWrap(True)
        self.source_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        source_layout.addWidget(self.source_hint)

        self.source_stack = QStackedWidget()
        self.source_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        # 索引模式页
        index_page = QWidget()
        index_layout = QVBoxLayout(index_page)
        index_layout.setContentsMargins(0, 0, 0, 0)
        index_layout.setSpacing(5)
        index_row = QHBoxLayout()
        index_row.addWidget(QLabel("搜索范围："))
        self.scope_combo = QComboBox()
        self.scope_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.scope_combo.currentIndexChanged.connect(self.schedule_search)
        index_row.addWidget(self.scope_combo, 1)
        index_layout.addLayout(index_row)
        self.index_hint = QLabel("常用目录建议保存到索引库，搜索速度最快")
        self.index_hint.setObjectName("SubtleLabel")
        self.index_hint.setWordWrap(True)
        index_layout.addWidget(self.index_hint)
        self.source_stack.addWidget(index_page)

        # 指定目录页：不写数据库，扫描结果只存在当前进程内存
        direct_page = QWidget()
        direct_layout = QVBoxLayout(direct_page)
        direct_layout.setContentsMargins(0, 0, 0, 0)
        direct_layout.setSpacing(7)

        # 第一行只放“路径 + 选择目录”，让路径输入框优先拿到横向空间。
        direct_row = QHBoxLayout()
        direct_row.addWidget(QLabel("目录："))
        self.direct_path_edit = QLineEdit()
        self.direct_path_edit.setPlaceholderText(
            r"直接粘贴路径，例如 C:\Projects；按 Enter 快速载入，不会写入 SQLite"
        )
        self.direct_path_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.direct_path_edit.returnPressed.connect(self.load_direct_directory)
        direct_row.addWidget(self.direct_path_edit, 1)
        self.direct_browse_btn = QPushButton("选择目录")
        self.direct_browse_btn.clicked.connect(self.browse_direct_directory)
        direct_row.addWidget(self.direct_browse_btn)
        direct_layout.addLayout(direct_row)

        # 操作按钮独占一行。窗口变窄时不会再把路径输入框和整个主界面撑出屏幕。
        direct_actions = QHBoxLayout()
        self.direct_scan_btn = QPushButton("载入 / 刷新")
        self.direct_scan_btn.setObjectName("PrimaryButton")
        self.direct_save_btn = QPushButton("保存到索引库")
        self.direct_clear_btn = QPushButton("清除临时缓存")
        self.direct_scan_btn.clicked.connect(self.load_direct_directory)
        self.direct_save_btn.clicked.connect(self.save_direct_directory)
        self.direct_clear_btn.clicked.connect(self.clear_direct_cache)
        direct_actions.addWidget(self.direct_scan_btn)
        direct_actions.addWidget(self.direct_save_btn)
        direct_actions.addWidget(self.direct_clear_btn)
        direct_actions.addStretch()
        direct_layout.addLayout(direct_actions)

        self.direct_cache_label = QLabel("临时模式 · 未载入目录 · 不写数据库")
        self.direct_cache_label.setObjectName("StatusPill")
        self.direct_cache_label.setWordWrap(True)
        direct_layout.addWidget(self.direct_cache_label)

        self.direct_note = QLabel("关闭 FastFinder 后临时缓存自动释放；需要长期保存时点“保存到索引库”")
        self.direct_note.setObjectName("SubtleLabel")
        self.direct_note.setWordWrap(True)
        direct_layout.addWidget(self.direct_note)
        self.source_stack.addWidget(direct_page)

        source_layout.addWidget(self.source_stack)
        main.addWidget(source_card)
        self._startup_mark("UI: source card")

        # 筛选卡片
        filter_card = QFrame()
        filter_card.setObjectName("Card")
        filter_layout = QVBoxLayout(filter_card)
        filter_layout.setContentsMargins(12, 8, 12, 8)
        filter_layout.setSpacing(5)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("类型："))
        self.type_combo = QComboBox()
        self.type_combo.addItem("全部", (None, "all"))
        self.type_combo.addItem("文件", ("file", "all"))
        self.type_combo.addItem("文件夹", ("folder", "all"))
        self.type_combo.addItem("文档", ("file", "document"))
        self.type_combo.addItem("图片", ("file", "image"))
        self.type_combo.addItem("程序/代码", ("file", "program"))
        self.type_combo.currentIndexChanged.connect(self.schedule_search)
        filters.addWidget(self.type_combo)

        filters.addSpacing(12)
        filters.addWidget(QLabel("时间："))
        self.time_combo = QComboBox()
        for label, value in [
            ("不限", None),
            ("今天", "today"),
            ("3天内", "3d"),
            ("7天内", "7d"),
            ("30天内", "30d"),
            ("今年", "year"),
        ]:
            self.time_combo.addItem(label, value)
        self.time_combo.currentIndexChanged.connect(self.schedule_search)
        filters.addWidget(self.time_combo)

        filters.addSpacing(12)
        filters.addWidget(QLabel("排序："))
        self.sort_combo = QComboBox()
        for label, value in [
            ("相关度", "relevance"),
            ("修改时间 ↓", "modified_desc"),
            ("修改时间 ↑", "modified_asc"),
            ("创建时间 ↓", "created_desc"),
            ("创建时间 ↑", "created_asc"),
            ("大小 ↓", "size_desc"),
            ("大小 ↑", "size_asc"),
            ("名称 A-Z", "name_asc"),
            ("名称 Z-A", "name_desc"),
            ("类型", "type"),
            ("路径", "path"),
        ]:
            self.sort_combo.addItem(label, value)
        self.sort_combo.currentIndexChanged.connect(self.schedule_search)
        filters.addWidget(self.sort_combo)
        filters.addStretch()
        filter_layout.addLayout(filters)
        self.result_hint = QLabel("双击打开 · Ctrl+Enter 定位 · Ctrl+L 搜索")
        self.result_hint.setObjectName("SubtleLabel")
        self.result_hint.setWordWrap(True)
        filter_layout.addWidget(self.result_hint)
        main.addWidget(filter_card)
        self._startup_mark("UI: filters")

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self.open_selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.table.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        header = self.table.horizontalHeader()
        # 不使用 ResizeToContents 作为主要策略。超长文件名会把其它列直接挤出窗口。
        # 由 resizeEvent 按当前可用宽度动态分配列宽。
        for column in range(6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        main.addWidget(self.table, 1)
        self._startup_mark("UI: result table")

        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("SubtleLabel")
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        main.addWidget(self.status_label)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.perform_search)

        self.history_timer = QTimer(self)
        self.history_timer.setSingleShot(True)
        self.history_timer.setInterval(900)
        self.history_timer.timeout.connect(self.record_current_search_history)

        # 首次布局完成后再计算一次列宽。
        QTimer.singleShot(0, self._update_responsive_ui)
        self._startup_mark("UI: status + timers")

    def _build_shortcuts(self):
        for seq in ("Ctrl+L", "Ctrl+F"):
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.activated.connect(self.focus_search)

        esc = QShortcut(QKeySequence("Esc"), self)
        esc.activated.connect(self.clear_search)

        reveal = QShortcut(QKeySequence("Ctrl+Return"), self.table)
        reveal.activated.connect(self.reveal_selected)
        open_item = QShortcut(QKeySequence("Return"), self.table)
        open_item.activated.connect(self.open_selected)
        copy_path = QShortcut(QKeySequence("Ctrl+C"), self.table)
        copy_path.activated.connect(self.copy_selected_path)
        copy_name = QShortcut(QKeySequence("Ctrl+Shift+C"), self.table)
        copy_name.activated.connect(self.copy_selected_name)

        open_settings = QShortcut(QKeySequence("Ctrl+,"), self)
        open_settings.activated.connect(self.open_settings)
        open_indexes = QShortcut(QKeySequence("Ctrl+I"), self)
        open_indexes.activated.connect(self.open_index_manager)
        open_history = QShortcut(QKeySequence("Ctrl+H"), self)
        open_history.activated.connect(self.open_history)
        open_favorites = QShortcut(QKeySequence("Ctrl+B"), self)
        open_favorites.activated.connect(self.open_favorites)
        index_mode = QShortcut(QKeySequence("Alt+1"), self)
        index_mode.activated.connect(lambda: self.set_search_mode("index"))
        direct_mode = QShortcut(QKeySequence("Alt+2"), self)
        direct_mode.activated.connect(lambda: self.set_search_mode("direct"))

    def _build_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip("FastFinder - 本地极速文件查找")

        icon_path = resource_path("resources", "fastfinder.ico")
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            self.tray_icon.setIcon(icon)
            self.setWindowIcon(icon)

        tray_menu = QMenu(self)
        self.tray_toggle_action = QAction("显示 / 隐藏 FastFinder", self)
        self.tray_toggle_action.triggered.connect(self.toggle_window_visibility)
        tray_menu.addAction(self.tray_toggle_action)
        self.tray_quick_action = QAction("快速搜索小窗", self)
        self.tray_quick_action.triggered.connect(self.show_quick_search)
        tray_menu.addAction(self.tray_quick_action)

        self.tray_show_action = QAction("显示并搜索", self)
        self.tray_show_action.triggered.connect(self.restore_from_tray)
        tray_menu.addAction(self.tray_show_action)
        self.tray_hide_action = QAction("隐藏到托盘", self)
        self.tray_hide_action.triggered.connect(lambda: self.hide_to_tray(False))
        tray_menu.addAction(self.tray_hide_action)

        tray_menu.addSeparator()
        tray_favorites_action = QAction("收藏", self)
        tray_favorites_action.triggered.connect(self.open_favorites)
        tray_menu.addAction(tray_favorites_action)
        tray_history_action = QAction("搜索历史", self)
        tray_history_action.triggered.connect(self.open_history)
        tray_menu.addAction(tray_history_action)
        tray_index_action = QAction("索引库", self)
        tray_index_action.triggered.connect(self._open_index_manager_from_tray)
        tray_menu.addAction(tray_index_action)
        tray_settings_action = QAction("设置", self)
        tray_settings_action.triggered.connect(self._open_settings_from_tray)
        tray_menu.addAction(tray_settings_action)

        tray_menu.addSeparator()
        self.tray_exit_action = QAction("退出 FastFinder", self)
        self.tray_exit_action.triggered.connect(self.quit_application)
        tray_menu.addAction(self.tray_exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.show()

    # ------------------------------------------------------------------
    # Search source / direct directory
    # ------------------------------------------------------------------
    def set_search_mode(self, mode: str):
        direct = mode == "direct"
        self.source_stack.setCurrentIndex(1 if direct else 0)
        self.direct_mode_btn.setChecked(direct)
        self.index_mode_btn.setChecked(not direct)
        self.source_hint.setText(
            "指定目录：只扫描当前目录到内存，不写 SQLite；适合临时路径、U盘、共享目录"
            if direct
            else "索引库：适合常用目录，打开即搜，后台自动保持更新"
        )
        if not direct and self.direct_entries:
            # Clear large direct_entries list when switching to index mode
            self.direct_entries = []
            import gc
            gc.collect()
        
        if direct and not self.direct_entries:
            self.model.clear()
            self.status_label.setText("指定目录模式：粘贴目录路径后按 Enter，或点击“选择目录”。")
        else:
            self.schedule_search()

    def _is_direct_mode(self) -> bool:
        return self.source_stack.currentIndex() == 1

    def browse_direct_directory(self):
        start = self.direct_path_edit.text().strip().strip('"')
        if not os.path.isdir(start):
            start = ""
        folder = QFileDialog.getExistingDirectory(self, "选择临时搜索目录", start)
        if folder:
            self.direct_path_edit.setText(folder)
            self.load_direct_directory()

    def load_direct_directory(self):
        path = self.direct_path_edit.text().strip().strip('"').strip()
        path = os.path.expandvars(os.path.expanduser(path))
        path = os.path.abspath(os.path.normpath(path)) if path else ""
        if not path or not os.path.isdir(path):
            QMessageBox.warning(self, "目录不可用", "请输入或选择一个存在的文件夹路径。")
            return

        self.direct_path_edit.setText(path)
        # A direct-directory scan is foreground disk work; give it the same
        # priority as a search so scheduled index maintenance pauses.
        self._priority_search_started()
        self.direct_scan_id += 1
        scan_id = self.direct_scan_id
        self.direct_scan_running = True
        self.direct_entries = []
        self.direct_loaded_path = ""
        self.model.clear()
        self.direct_scan_btn.setEnabled(False)
        self.direct_browse_btn.setEnabled(False)
        self.direct_cache_label.setText("正在读取目录… · 仅内存 · 不写数据库")
        self.status_label.setText(f"正在快速读取指定目录：{path}")

        worker = DirectScanWorker(
            scan_id,
            path,
            list(self.settings.get("excluded_dirs", [])),
        )
        worker.signals.progress.connect(self.on_direct_scan_progress)
        worker.signals.finished.connect(self.on_direct_scan_finished)
        self.pool.start(worker)

    def on_direct_scan_progress(self, scan_id: int, data: dict):
        if scan_id != self.direct_scan_id:
            return
        self.direct_cache_label.setText(
            f"临时读取中 · {data['items']:,} 项 · {data['speed']:,} 项/秒 · 不写数据库"
        )
        self.status_label.setText(
            f"指定目录快速读取：文件 {data['files']:,} ｜ 文件夹 {data['folders']:,} ｜ "
            f"{data['elapsed']:.1f}s\n{data['current']}"
        )

    def on_direct_scan_finished(
        self,
        scan_id: int,
        path: str,
        entries,
        stats: dict,
        error: str,
    ):
        self._priority_search_finished()
        if scan_id != self.direct_scan_id:
            return
        self.direct_scan_running = False
        self.direct_scan_btn.setEnabled(True)
        self.direct_browse_btn.setEnabled(True)

        if error:
            self.direct_entries = []
            self.direct_loaded_path = ""
            self.direct_cache_label.setText("临时读取失败")
            self.status_label.setText(f"指定目录读取失败：{error}")
            return

        self.direct_entries = entries
        self.direct_loaded_path = path
        self.direct_cache_label.setText(
            f"临时缓存 {int(stats.get('items', 0)):,} 项 · "
            f"读取 {float(stats.get('elapsed', 0)):.2f}s · 不写数据库"
        )
        self.status_label.setText(
            f"指定目录已载入：{path} ｜ {int(stats.get('items', 0)):,} 项 ｜ "
            "只保存在本次运行内存中"
        )
        self._update_direct_save_state()
        self.schedule_search()

    def clear_direct_cache(self):
        self.direct_scan_id += 1  # 让仍在返回的旧扫描结果失效
        self.direct_scan_running = False
        self.direct_entries = []
        self.direct_loaded_path = ""
        self.model.clear()
        self.direct_scan_btn.setEnabled(True)
        self.direct_browse_btn.setEnabled(True)
        self.direct_cache_label.setText("临时模式 · 缓存已清除 · 不写数据库")
        self.status_label.setText("临时目录缓存已清除。磁盘文件没有任何变化。")
        self._update_direct_save_state()

    def _saved_root_for_direct_path(self):
        path = self.direct_loaded_path or self.direct_path_edit.text().strip().strip('"')
        if not path:
            return None
        wanted = normalize_path(os.path.abspath(os.path.normpath(path)))
        for root in self.database.list_search_roots():
            if root["path_norm"] == wanted:
                return root
        return None

    def _update_direct_save_state(self):
        root = self._saved_root_for_direct_path()
        compact = self.width() < 820
        if root is not None:
            self.direct_save_btn.setText("已保存" if compact else "已保存到索引库")
            self.direct_save_btn.setEnabled(False)
        else:
            self.direct_save_btn.setText("保存索引" if compact else "保存到索引库")
            self.direct_save_btn.setEnabled(bool(self.direct_loaded_path))

    def save_direct_directory(self):
        path = self.direct_loaded_path
        if not path or not os.path.isdir(path):
            QMessageBox.information(self, "提示", "请先载入一个指定目录。")
            return
        try:
            root_id = self.database.add_search_root(path)
            index_service, _ = self._ensure_background_core(start_scheduler=True)
            index_service.enqueue(root_id, path)
            self.on_roots_changed()
            self._update_direct_save_state()
            self.status_label.setText(
                f"已把 {path} 保存为长期索引。后台正在写入 SQLite；临时搜索仍可继续使用。"
            )
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    # ------------------------------------------------------------------
    # Tray / lifecycle
    # ------------------------------------------------------------------
    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_from_tray()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger and not self.isVisible():
            self.restore_from_tray()

    def hide_to_tray(self, show_notification: bool = True):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.showMinimized()
            return
        if not hasattr(self, "tray_icon"):
            self._start_shell_integrations()
        self.hide()
        if (
            show_notification
            and self.settings.get("tray_notification", True)
            and not self._tray_hint_shown
        ):
            self.tray_icon.showMessage(
                "FastFinder 仍在后台运行",
                "长期索引会按自动更新策略在后台维护。临时目录缓存也会保留到本次程序退出。",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            self._tray_hint_shown = True

    def restore_from_tray(self):
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self.focus_search()

    def toggle_window_visibility(self):
        """Global hotkey action: active window -> hide; otherwise -> show/focus."""
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            self.hide_to_tray(False)
        else:
            self.restore_from_tray()

    def show_quick_search(self):
        self._ensure_quick_window().show_quick()

    def toggle_quick_search(self):
        self._ensure_quick_window().toggle_quick()

    def _quick_open_result(self, result, query: str):
        if query:
            self._record_history_text(query)
        self._open_path(result.full_path)

    def _quick_reveal_result(self, result, query: str):
        if query:
            self._record_history_text(query)
        self._reveal_path(result.full_path)

    def _open_index_manager_from_tray(self):
        self.restore_from_tray()
        self.open_index_manager()

    def _open_settings_from_tray(self):
        self.restore_from_tray()
        self.open_settings()

    def quit_application(self):
        self._really_quit = True
        self._shutdown_services()
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()
        QApplication.quit()

    def _shutdown_services(self):
        if self._shutdown_done:
            return
        self._shutdown_done = True
        if self.auto_scheduler is not None:
            self.auto_scheduler.stop()
        if self.watcher is not None:
            self.watcher.stop()
        if self.index_service is not None:
            self.index_service.shutdown()
        if self.global_hotkey is not None:
            self.global_hotkey.shutdown()
        if self.quick_hotkey is not None:
            self.quick_hotkey.shutdown()

    def _configure_global_hotkey(self, notify_error: bool = False) -> tuple[bool, str]:
        if self.global_hotkey is None:
            return True, ""

        enabled = bool(self.settings.get("global_hotkey_enabled", True))
        sequence = str(self.settings.get("global_hotkey", "Ctrl+Alt+Space")).strip()
        if not enabled:
            self.global_hotkey.unregister()
            self._update_tray_hotkey_text()
            return True, ""

        ok, error = self.global_hotkey.register(sequence)
        self._update_tray_hotkey_text()
        if not ok and notify_error:
            QMessageBox.warning(
                self,
                "全局快捷键注册失败",
                error + "\n\n请换一个组合，例如 Ctrl+Alt+Space 或 Ctrl+Alt+F12。",
            )
        return ok, error

    def _configure_quick_hotkey(self, notify_error: bool = False) -> tuple[bool, str]:
        if self.quick_hotkey is None:
            return True, ""

        enabled = bool(self.settings.get("quick_hotkey_enabled", True))
        sequence = str(self.settings.get("quick_hotkey", "Ctrl+Alt+F")).strip()
        if not enabled:
            self.quick_hotkey.unregister()
            self._update_tray_hotkey_text()
            return True, ""

        ok, error = self.quick_hotkey.register(sequence)
        self._update_tray_hotkey_text()
        if not ok and notify_error:
            QMessageBox.warning(
                self,
                "小搜索窗快捷键注册失败",
                error + "\n\n请换一个组合，例如 Ctrl+Alt+F 或 Ctrl+Shift+Space。",
            )
        return ok, error

    def _update_tray_hotkey_text(self):
        if not hasattr(self, "tray_toggle_action"):
            return

        main_sequence = ""
        quick_sequence = ""
        if self.settings.get("global_hotkey_enabled", True):
            main_sequence = str(self.settings.get("global_hotkey", "Ctrl+Alt+Space"))
            suffix = f"    {main_sequence}" if main_sequence else ""
            self.tray_toggle_action.setText(f"显示 / 隐藏 FastFinder{suffix}")
        else:
            self.tray_toggle_action.setText("显示 / 隐藏 FastFinder")

        if hasattr(self, "tray_quick_action"):
            if self.settings.get("quick_hotkey_enabled", True):
                quick_sequence = str(self.settings.get("quick_hotkey", "Ctrl+Alt+F"))
                suffix = f"    {quick_sequence}" if quick_sequence else ""
                self.tray_quick_action.setText(f"快速搜索小窗{suffix}")
            else:
                self.tray_quick_action.setText("快速搜索小窗")

        tooltip = ["FastFinder - 本地极速文件查找"]
        if main_sequence:
            tooltip.append(f"显示/隐藏：{main_sequence}")
        if quick_sequence:
            tooltip.append(f"快速搜索：{quick_sequence}")
        self.tray_icon.setToolTip("\n".join(tooltip))
        self._update_shortcut_hint()

    def _update_shortcut_hint(self):
        if not hasattr(self, "result_hint"):
            return
        parts = [
            "Enter 打开",
            "Ctrl+Enter 定位",
            "Ctrl+C 复制路径",
            "拖拽可复制到桌面/资源管理器",
        ]
        if self.settings.get("quick_hotkey_enabled", True):
            sequence = str(self.settings.get("quick_hotkey", "Ctrl+Alt+F"))
            if sequence:
                parts.append(f"{sequence} 快速搜索")
        self.result_hint.setText(" · ".join(parts))

    def _notify_hotkey_problem(self, error: str):
        if not error:
            return
        if hasattr(self, "tray_icon") and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.showMessage(
                "FastFinder 快捷键未启用",
                error + " 可在设置中更换快捷键。",
                QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )
        elif hasattr(self, "status_label"):
            self.status_label.setText("全局快捷键未启用：" + error)

    def _apply_settings(self, notify_hotkey_error: bool = False) -> tuple[bool, str]:
        self.search_timer.setInterval(int(self.settings.get("debounce_ms", 120)))
        main_ok, main_error = self._configure_global_hotkey(notify_hotkey_error)
        quick_ok, quick_error = self._configure_quick_hotkey(notify_hotkey_error)
        ok = main_ok and quick_ok
        error = main_error or quick_error
        return ok, error

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def on_search_text_changed(self, *_):
        self._priority_user_activity()
        self.schedule_search()
        if self.settings.get("search_history_enabled", True):
            self.history_timer.start(900)

    def _record_history_text(self, text: str):
        if not self.settings.get("search_history_enabled", True):
            return
        text = " ".join((text or "").strip().split())
        if len(text) < 2:
            return
        self.database.record_search_history(text)
        self.refresh_history_completer()

    def record_current_search_history(self):
        self._record_history_text(self.search_edit.text())

    def refresh_history_completer(self):
        if self.history_string_model is None:
            return
        if not self.settings.get("search_history_enabled", True):
            self.history_string_model.setStringList([])
            return
        queries = [row["query"] for row in self.database.list_search_history(80)]
        self.history_string_model.setStringList(queries)

    def schedule_search(self):
        if hasattr(self, "search_timer"):
            self.search_timer.start(int(self.settings.get("debounce_ms", 120)))

    def _time_from(self):
        value = self.time_combo.currentData()
        if value == "today":
            return start_of_today_ts()
        if value == "3d":
            return days_ago_ts(3)
        if value == "7d":
            return days_ago_ts(7)
        if value == "30d":
            return days_ago_ts(30)
        if value == "year":
            return start_of_year_ts()
        return None

    def _search_options(self, direct: bool = False):
        from core.search_service import SearchOptions

        item_type, category = self.type_combo.currentData()
        root_ids = None
        if not direct:
            root_id = self.scope_combo.currentData()
            root_ids = [int(root_id)] if isinstance(root_id, int) else None
        return SearchOptions(
            root_ids=root_ids,
            item_type=item_type,
            category=category,
            after_time=self._time_from(),
            sort_by=self.sort_combo.currentData(),
            limit=int(self.settings.get("result_limit", 200)),
            candidate_limit=int(self.settings.get("candidate_limit", 3000)),
        )

    def perform_search(self):
        self.search_id += 1
        current_id = self.search_id
        text = self.search_edit.text().strip()
        self._priority_search_started()

        if self._is_direct_mode():
            if self.direct_scan_running:
                self._priority_search_finished()
                return
            if not self.direct_entries or not self.direct_loaded_path:
                self.model.clear()
                self.status_label.setText("指定目录模式：先粘贴/选择一个目录并载入。")
                self._priority_search_finished()
                return
            self.status_label.setText("正在搜索临时目录缓存……")
            worker = DirectSearchWorker(
                current_id,
                self.database,
                self.direct_entries,
                self.settings,
                text,
                self._search_options(direct=True),
            )
        else:
            self.status_label.setText("正在搜索索引库……")
            worker = SearchWorker(
                current_id,
                self.database,
                self.settings,
                text,
                self._search_options(direct=False),
            )

        worker.signals.finished.connect(self.on_search_finished)
        self.pool.start(worker)

    def on_search_finished(
        self,
        search_id: int,
        results,
        total: int,
        elapsed_ms: float,
        error: str,
        source: str,
    ):
        self._priority_search_finished()
        if search_id != self.search_id:
            return
        if source == "direct" and not self._is_direct_mode():
            return
        if source == "index" and self._is_direct_mode():
            return
        if error:
            self.status_label.setText(f"搜索失败：{error}")
            return

        self.model.set_results(results)
        shown = len(results)
        query = self.search_edit.text().strip()
        mode = "最近修改" if not query else "搜索"
        if source == "direct":
            self.status_label.setText(
                f"临时目录 {mode}：显示 {shown:,} 项 ｜ 候选 {total:,} 项 ｜ "
                f"{elapsed_ms:.1f} ms ｜ 会话缓存 {len(self.direct_entries):,} 项 ｜ 不写数据库"
            )
        else:
            self.status_label.setText(
                f"索引库 {mode}：显示 {shown:,} 项 ｜ 候选 {total:,} 项 ｜ "
                f"{elapsed_ms:.1f} ms ｜ 已索引 {self.indexed_count:,} 项"
            )
        if shown:
            self.table.selectRow(0)

    # ------------------------------------------------------------------
    # Roots / settings
    # ------------------------------------------------------------------
    def refresh_roots(self):
        current = self.scope_combo.currentData() if self.scope_combo.count() else None
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem("所有长期索引位置", None)
        selected_index = 0
        for root in self.database.list_search_roots(enabled_only=True):
            self.scope_combo.addItem(root["path"], int(root["id"]))
            if current == int(root["id"]):
                selected_index = self.scope_combo.count() - 1
        self.scope_combo.setCurrentIndex(selected_index)
        self.scope_combo.blockSignals(False)
        self.indexed_count = self.database.estimated_entry_count()
        if hasattr(self, "direct_save_btn"):
            self._update_direct_save_state()

    def open_index_manager(self):
        if self.index_window is not None:
            self.index_window.show()
            self.index_window.raise_()
            self.index_window.activateWindow()
            return

        from app.index_manager_window import IndexManagerWindow

        index_service, _ = self._ensure_background_core(start_scheduler=True)
        window = IndexManagerWindow(
            self.database,
            self.settings,
            index_service,
            self,
        )
        window.roots_changed.connect(self.on_roots_changed)
        window.destroyed.connect(lambda: setattr(self, "index_window", None))
        self.index_window = window
        window.show()

    def open_history(self):
        if self.history_window is not None:
            self.history_window.refresh()
            self.history_window.show()
            self.history_window.raise_()
            self.history_window.activateWindow()
            return
        from app.history_window import HistoryWindow

        window = HistoryWindow(self.database, self)
        window.search_requested.connect(self._use_history_query)
        window.destroyed.connect(lambda: setattr(self, "history_window", None))
        self.history_window = window
        window.show()

    def _use_history_query(self, query: str):
        self.restore_from_tray()
        self.search_edit.setText(query)
        self.focus_search()

    def open_favorites(self):
        if self.favorites_window is not None:
            self.favorites_window.refresh()
            self.favorites_window.show()
            self.favorites_window.raise_()
            self.favorites_window.activateWindow()
            return
        from app.favorites_window import FavoritesWindow

        window = FavoritesWindow(self.database, self)
        window.open_requested.connect(self._open_path)
        window.reveal_requested.connect(self._reveal_path)
        window.favorites_changed.connect(self._on_favorites_changed)
        window.destroyed.connect(lambda: setattr(self, "favorites_window", None))
        self.favorites_window = window
        window.show()

    def _on_favorites_changed(self):
        if self.favorites_window is not None:
            self.favorites_window.refresh()
        self.schedule_search()
        if self.quick_window is not None and self.quick_window.isVisible():
            self.quick_window.perform_search()

    def _on_auto_index_status(self, text: str):
        # Do not overwrite active search feedback.  The status becomes visible
        # when the search box is idle/empty, while the index manager always
        # shows per-root progress separately.
        if (
            hasattr(self, "status_label")
            and not self.search_edit.text().strip()
            and not self.direct_scan_running
        ):
            self.status_label.setText(text)

    def start_background_indexing(self):
        """Manual force-refresh hook kept for compatibility."""
        index_service, _ = self._ensure_background_core(start_scheduler=True)
        index_service.enqueue_enabled_roots()

    def on_background_index_finished(self, root_id: int, data: dict):
        # Root item_count was already updated by IndexBuilder; summing those
        # counters is far cheaper than COUNT(*) over the complete file table.
        self.refresh_roots()
        if not self._is_direct_mode():
            self.schedule_search()

    def on_background_index_failed(self, root_id: int, message: str):
        self.indexed_count = self.database.estimated_entry_count()

    def on_roots_changed(self):
        self.refresh_roots()
        if self.auto_scheduler is not None:
            self.auto_scheduler.roots_changed()
        if self.settings.get("watch_changes", False):
            self._ensure_watcher().restart_async()
        if not self._is_direct_mode():
            self.schedule_search()

    def open_settings(self):
        # Suspend the current global shortcut while QKeySequenceEdit is
        # capturing a new one, otherwise pressing the old combination could
        # hide the settings dialog in the middle of editing.
        if self.global_hotkey is not None:
            self.global_hotkey.unregister()
        if self.quick_hotkey is not None:
            self.quick_hotkey.unregister()
        from app.settings_window import SettingsWindow
        from core.startup_manager import StartupManager

        if self.startup_manager is None:
            self.startup_manager = StartupManager()
        dlg = SettingsWindow(
            self.settings_store,
            self,
            startup_manager=self.startup_manager,
            database=self.database,
        )
        dlg.settings_saved.connect(self.on_settings_saved)
        result = dlg.exec()
        if result != QDialog.DialogCode.Accepted:
            self._configure_global_hotkey(False)
            self._configure_quick_hotkey(False)

    def on_settings_saved(self, settings: dict):
        previous = dict(self.settings)
        self.settings = dict(settings)
        ok, _ = self._apply_settings(notify_hotkey_error=True)
        if not ok:
            # Keep every other setting, but restore the last working hotkeys.
            self.settings["global_hotkey_enabled"] = previous.get("global_hotkey_enabled", True)
            self.settings["global_hotkey"] = previous.get("global_hotkey", "Ctrl+Alt+Space")
            self.settings["quick_hotkey_enabled"] = previous.get("quick_hotkey_enabled", True)
            self.settings["quick_hotkey"] = previous.get("quick_hotkey", "Ctrl+Alt+F")
            self.settings_store.save(self.settings)
            self._configure_global_hotkey(False)
            self._configure_quick_hotkey(False)

        if self.watcher is not None:
            self.watcher.stop_async()
            self.watcher = None

        index_service, auto_scheduler = self._ensure_background_core(start_scheduler=True)
        index_service.set_excluded_dirs(self.settings.get("excluded_dirs", []))
        if self.settings.get("watch_changes", False):
            self._ensure_watcher().start_async()
        auto_scheduler.apply_settings(self.settings, reschedule_startup=True)

        if self.settings.get("search_history_enabled", True):
            self._ensure_history_completer()
        self.refresh_history_completer()
        self.schedule_search()

    def _first_run_hint(self):
        if self.database.list_search_roots():
            return
        answer = QMessageBox.question(
            self,
            "第一次使用 FastFinder",
            "当前没有长期索引。\n\n你可以：\n"
            "• 打开“指定目录”，临时粘贴路径直接搜索（不保存）\n"
            "• 或建立长期索引，之后打开即搜\n\n现在添加一个长期索引目录吗？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.open_index_manager()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def selected_item(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.model.item_at(rows[0].row())

    def open_selected(self, *_):
        item = self.selected_item()
        if item:
            self._record_history_text(self.search_edit.text())
            self._open_path(item.full_path)

    def reveal_selected(self):
        item = self.selected_item()
        if item:
            self._record_history_text(self.search_edit.text())
            self._reveal_path(item.full_path)

    def _open_path(self, path: str):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            if self.settings.get("usage_ranking_enabled", True):
                self.database.record_usage(path)
                if not self._is_direct_mode():
                    QTimer.singleShot(0, self.schedule_search)
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))

    def _reveal_path(self, path: str):
        try:
            if os.path.isdir(path):
                self._open_path(path)
            elif sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception as exc:
            QMessageBox.warning(self, "定位失败", str(exc))

    def show_context_menu(self, pos):
        item = self.selected_item()
        if not item:
            return
        menu = QMenu(self)
        open_action = menu.addAction("打开")
        reveal_action = menu.addAction("打开所在位置")
        menu.addSeparator()
        favorite_action = menu.addAction("取消收藏" if self.database.is_favorite(item.full_path) else "添加到收藏")
        menu.addSeparator()
        copy_path_action = menu.addAction("复制完整路径")
        copy_name_action = menu.addAction("复制名称")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == open_action:
            self._record_history_text(self.search_edit.text())
            self._open_path(item.full_path)
        elif action == reveal_action:
            self._reveal_path(item.full_path)
        elif action == favorite_action:
            if self.database.is_favorite(item.full_path):
                self.database.remove_favorite(item.full_path)
                self.status_label.setText(f"已取消收藏：{item.name}")
            else:
                self.database.add_favorite(item.full_path, item.is_directory)
                self.status_label.setText(f"已收藏：{item.name}")
            self._on_favorites_changed()
        elif action == copy_path_action:
            QApplication.clipboard().setText(item.full_path)
        elif action == copy_name_action:
            QApplication.clipboard().setText(item.name)

    def focus_search(self):
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def copy_selected_path(self):
        item = self.selected_item()
        if item:
            QApplication.clipboard().setText(item.full_path)

    def copy_selected_name(self):
        item = self.selected_item()
        if item:
            QApplication.clipboard().setText(item.name)

    def clear_search(self):
        if self.search_edit.text():
            self.search_edit.clear()
        elif self.settings.get("esc_hides_window", True):
            self.hide_to_tray(False)
        else:
            self.focus_search()

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if os.path.isfile(path):
            self.search_edit.setText(os.path.basename(path))
            self.focus_search()
            return
        if os.path.isdir(path):
            # 文件夹拖入默认走“临时指定目录”，不再强迫建立长期索引。
            self.set_search_mode("direct")
            self.direct_path_edit.setText(path)
            self.load_direct_directory()

    def changeEvent(self, event):
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and self.settings.get("minimize_to_tray", True)
        ):
            QTimer.singleShot(0, self.hide_to_tray)

    def closeEvent(self, event):
        if (
            not self._really_quit
            and self.settings.get("close_to_tray", True)
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            event.ignore()
            self.hide_to_tray()
            return

        self._shutdown_services()
        event.accept()
