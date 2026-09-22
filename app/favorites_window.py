from __future__ import annotations

import os

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


class FavoritesWindow(QDialog):
    open_requested = Signal(str)
    reveal_requested = Signal(str)
    favorites_changed = Signal()

    def __init__(self, database: Database, parent=None):
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("FastFinder 收藏")
        self.resize(900, 520)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["类型", "名称", "路径", "收藏时间"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.open_selected)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        open_btn = QPushButton("打开")
        reveal_btn = QPushButton("定位")
        remove_btn = QPushButton("取消收藏")
        clean_btn = QPushButton("清理不存在项")
        close_btn = QPushButton("关闭")
        open_btn.setObjectName("PrimaryButton")
        remove_btn.setObjectName("WarningButton")
        open_btn.clicked.connect(self.open_selected)
        reveal_btn.clicked.connect(self.reveal_selected)
        remove_btn.clicked.connect(self.remove_selected)
        clean_btn.clicked.connect(self.clean_missing)
        close_btn.clicked.connect(self.close)
        buttons.addWidget(open_btn)
        buttons.addWidget(reveal_btn)
        buttons.addWidget(remove_btn)
        buttons.addWidget(clean_btn)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        rows = self.database.list_favorites()
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            full_path = row["full_path"]
            type_text = "文件夹" if row["is_directory"] else "文件"
            if not os.path.exists(full_path):
                type_text += "（不存在）"
            self.table.setItem(r, 0, QTableWidgetItem(type_text))
            self.table.setItem(r, 1, QTableWidgetItem(row["name"]))
            path_item = QTableWidgetItem(full_path)
            path_item.setData(1000, full_path)
            self.table.setItem(r, 2, path_item)
            self.table.setItem(r, 3, QTableWidgetItem(format_datetime(int(row["created_time"]))))
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(2, max(360, self.table.columnWidth(2)))

    def _selected_path(self):
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 2)
        return item.data(1000) if item else ""

    def open_selected(self, *_):
        path = self._selected_path()
        if path:
            self.open_requested.emit(path)

    def reveal_selected(self):
        path = self._selected_path()
        if path:
            self.reveal_requested.emit(path)

    def remove_selected(self):
        path = self._selected_path()
        if not path:
            return
        self.database.remove_favorite(path)
        self.favorites_changed.emit()
        self.refresh()

    def clean_missing(self):
        removed = self.database.clear_missing_favorites()
        self.favorites_changed.emit()
        self.refresh()
        QMessageBox.information(self, "清理完成", f"已清理 {removed} 个不存在的收藏项。")
