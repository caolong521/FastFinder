from __future__ import annotations

import time

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.search_service import SearchOptions, SearchService
from database.database import Database


class QuickSearchSignals(QObject):
    finished = Signal(int, object, float, str)


class QuickSearchWorker(QRunnable):
    def __init__(self, search_id: int, database: Database, query: str, fuzzy_threshold: int, behavior_ranking: bool):
        super().__init__()
        self.search_id = search_id
        self.database = database
        self.query = query
        self.fuzzy_threshold = fuzzy_threshold
        self.behavior_ranking = behavior_ranking
        self.signals = QuickSearchSignals()

    @Slot()
    def run(self):
        started = time.perf_counter()
        results = []
        error = ""
        try:
            service = SearchService(self.database, fuzzy_threshold=self.fuzzy_threshold, behavior_ranking=self.behavior_ranking)
            results, _ = service.search(
                self.query,
                SearchOptions(
                    sort_by="relevance",
                    limit=12,
                    candidate_limit=1600,
                ),
            )
        except Exception as exc:
            error = str(exc)
        elapsed = (time.perf_counter() - started) * 1000.0
        self.signals.finished.emit(self.search_id, results, elapsed, error)


class QuickSearchWindow(QWidget):
    """Small always-on-top search launcher used by a dedicated global hotkey."""

    open_requested = Signal(object, str)
    reveal_requested = Signal(object, str)
    history_requested = Signal(str)
    search_started = Signal()
    search_finished = Signal()

    def __init__(self, database: Database, settings_getter, parent=None):
        super().__init__(parent)
        self.database = database
        self.settings_getter = settings_getter
        self.pool = QThreadPool.globalInstance()
        self.search_id = 0
        self._results = []

        self.setObjectName("QuickSearchWindow")
        self.setWindowTitle("FastFinder 快速搜索")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(760, 430)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        card = QFrame()
        card.setObjectName("QuickCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("QuickSearchEdit")
        self.search_edit.setPlaceholderText("快速搜索文件 / 文件夹…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._schedule_search)
        self.search_edit.returnPressed.connect(self.open_current)
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("QuickResultList")
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.itemActivated.connect(lambda *_: self.open_current())
        layout.addWidget(self.list_widget, 1)

        self.status_label = QLabel("输入关键词即可搜索长期索引 · Enter 打开 · Ctrl+Enter 定位 · Esc 隐藏")
        self.status_label.setObjectName("SubtleLabel")
        layout.addWidget(self.status_label)
        outer.addWidget(card)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.perform_search)

        self.history_timer = QTimer(self)
        self.history_timer.setSingleShot(True)
        self.history_timer.setInterval(900)
        self.history_timer.timeout.connect(self._commit_history)

        esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        esc_shortcut.activated.connect(self.hide)
        reveal_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        reveal_shortcut.activated.connect(self.reveal_current)
        open_shortcut = QShortcut(QKeySequence("Return"), self.list_widget)
        open_shortcut.activated.connect(self.open_current)
        down_shortcut = QShortcut(QKeySequence("Down"), self)
        down_shortcut.activated.connect(lambda: self.move_selection(1))
        up_shortcut = QShortcut(QKeySequence("Up"), self)
        up_shortcut.activated.connect(lambda: self.move_selection(-1))
        focus_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        focus_shortcut.activated.connect(self.focus_search)

    def settings(self) -> dict:
        try:
            return dict(self.settings_getter())
        except Exception:
            return {}

    def _schedule_search(self):
        self.timer.start(90)
        self.history_timer.start()

    def _commit_history(self):
        text = self.search_edit.text().strip()
        if len(text) >= 2:
            self.history_requested.emit(text)

    def perform_search(self):
        self.search_started.emit()
        self.search_id += 1
        search_id = self.search_id
        query = self.search_edit.text().strip()
        settings = self.settings()
        worker = QuickSearchWorker(
            search_id,
            self.database,
            query,
            int(settings.get("fuzzy_threshold", 58)),
            bool(settings.get("usage_ranking_enabled", True)),
        )
        worker.signals.finished.connect(self._on_search_finished)
        self.pool.start(worker)

    def _on_search_finished(self, search_id: int, results, elapsed_ms: float, error: str):
        self.search_finished.emit()
        if search_id != self.search_id:
            return
        if error:
            self.status_label.setText("搜索失败：" + error)
            return

        self._results = list(results)
        self.list_widget.clear()
        for result in self._results:
            prefix = "★ " if result.is_favorite else ""
            kind = "📁" if result.is_directory else "📄"
            text = f"{kind}  {prefix}{result.name}\n    {result.parent_path}"
            item = QListWidgetItem(text)
            item.setToolTip(result.full_path)
            self.list_widget.addItem(item)
        if self._results:
            self.list_widget.setCurrentRow(0)
        self.status_label.setText(
            f"{len(self._results)} 项 · {elapsed_ms:.1f} ms · Enter 打开 · Ctrl+Enter 定位 · Esc 隐藏"
        )

    def focus_search(self):
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def move_selection(self, delta: int):
        count = self.list_widget.count()
        if count <= 0:
            return
        row = self.list_widget.currentRow()
        if row < 0:
            row = 0
        else:
            row = max(0, min(count - 1, row + delta))
        self.list_widget.setCurrentRow(row)
        self.list_widget.setFocus()

    def current_result(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._results):
            return self._results[row]
        return None

    def open_current(self):
        result = self.current_result()
        if result is None:
            return
        query = self.search_edit.text().strip()
        self.open_requested.emit(result, query)
        self.hide()

    def reveal_current(self):
        result = self.current_result()
        if result is None:
            return
        query = self.search_edit.text().strip()
        self.reveal_requested.emit(result, query)
        self.hide()

    def show_quick(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(780, max(560, int(available.width() * 0.52)))
            height = min(460, max(340, int(available.height() * 0.48)))
            x = available.x() + (available.width() - width) // 2
            y = available.y() + max(36, int(available.height() * 0.16))
            self.setGeometry(x, y, width, height)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_edit.setFocus()
        self.search_edit.selectAll()
        self.perform_search()

    def toggle_quick(self):
        if self.isVisible() and self.isActiveWindow():
            self.hide()
        else:
            self.show_quick()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.reveal_current()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Down and self.search_edit.hasFocus():
            if self.list_widget.count():
                self.list_widget.setFocus()
                self.list_widget.setCurrentRow(max(0, self.list_widget.currentRow()))
                event.accept()
                return
        super().keyPressEvent(event)
