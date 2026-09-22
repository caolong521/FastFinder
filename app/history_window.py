from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from database.database import Database
from utils.time_utils import format_datetime


class HistoryWindow(QDialog):
    search_requested = Signal(str)

    def __init__(self, database: Database, parent=None):
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("FastFinder 搜索历史")
        self.resize(760, 500)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["搜索内容", "使用次数", "最后使用"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.use_selected)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        use_btn = QPushButton("搜索所选")
        delete_btn = QPushButton("删除所选")
        clear_btn = QPushButton("清空历史")
        close_btn = QPushButton("关闭")
        use_btn.setObjectName("PrimaryButton")
        clear_btn.setObjectName("WarningButton")
        use_btn.clicked.connect(self.use_selected)
        delete_btn.clicked.connect(self.delete_selected)
        clear_btn.clicked.connect(self.clear_history)
        close_btn.clicked.connect(self.close)
        buttons.addWidget(use_btn)
        buttons.addWidget(delete_btn)
        buttons.addWidget(clear_btn)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        rows = self.database.list_search_history(300)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            query_item = QTableWidgetItem(row["query"])
            query_item.setData(1000, int(row["id"]))
            self.table.setItem(r, 0, query_item)
            self.table.setItem(r, 1, QTableWidgetItem(str(int(row["use_count"]))))
            self.table.setItem(r, 2, QTableWidgetItem(format_datetime(int(row["last_used_time"]))))
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, max(280, self.table.columnWidth(0)))

    def _selected(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return int(item.data(1000)), item.text()

    def use_selected(self, *_):
        selected = self._selected()
        if not selected:
            return
        _, query = selected
        self.search_requested.emit(query)
        self.accept()

    def delete_selected(self):
        selected = self._selected()
        if not selected:
            return
        history_id, _ = selected
        self.database.delete_search_history(history_id)
        self.refresh()

    def clear_history(self):
        if self.table.rowCount() == 0:
            return
        answer = QMessageBox.question(
            self,
            "清空搜索历史",
            "确定清空全部搜索历史吗？\n\n这不会删除任何文件或索引。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.database.clear_search_history()
        self.refresh()
