from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.background_index_service import BackgroundIndexService
from core.index_manager import IndexManager
from database.database import Database
from utils.time_utils import format_datetime


class IndexManagerWindow(QDialog):
    roots_changed = Signal()

    def __init__(
        self,
        database: Database,
        settings: dict,
        index_service: BackgroundIndexService,
        parent=None,
    ):
        super().__init__(parent)
        self.database = database
        self.manager = IndexManager(database)
        self.settings = settings
        self.index_service = index_service

        self.setWindowTitle("长期索引库")
        self.resize(1040, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel("长期索引库")
        title.setObjectName("TitleLabel")
        layout.addWidget(title)
        hint = QLabel(
            "这里保存的是持久化目录索引。FastFinder 退出后仍会保留，下次启动直接搜索。\n"
            "“清空索引数据”只删除该目录已写入 SQLite 的文件/文件夹记录，并保留目录配置；"
            "“删除所选存档”会连同目录配置一起移除。两种操作都不会删除磁盘上的真实文件。"
        )
        hint.setObjectName("SubtleLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        table_card = QFrame()
        table_card.setObjectName("Card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(8, 8, 8, 8)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["启用", "存档目录", "索引项目数", "上次更新", "下次自动更新", "扫描耗时", "状态"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellChanged.connect(self._enabled_changed)
        table_layout.addWidget(self.table)
        layout.addWidget(table_card, 1)

        buttons = QHBoxLayout()
        self.add_btn = QPushButton("+ 添加长期索引")
        self.add_btn.setObjectName("PrimaryButton")
        self.open_btn = QPushButton("打开目录")
        self.rebuild_btn = QPushButton("立即更新")
        self.clear_btn = QPushButton("清空索引数据")
        self.clear_btn.setObjectName("WarningButton")
        self.remove_btn = QPushButton("删除所选存档")
        self.remove_btn.setObjectName("DangerButton")
        self.cancel_btn = QPushButton("取消当前扫描")
        self.close_btn = QPushButton("后台运行并关闭")

        buttons.addWidget(self.add_btn)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.rebuild_btn)
        buttons.addWidget(self.clear_btn)
        buttons.addWidget(self.remove_btn)
        buttons.addStretch()
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self.status = QLabel("等待操作")
        self.status.setObjectName("SubtleLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.add_btn.clicked.connect(self.add_folder)
        self.open_btn.clicked.connect(self.open_selected_folder)
        self.clear_btn.clicked.connect(self.clear_selected_archive)
        self.remove_btn.clicked.connect(self.remove_selected)
        self.rebuild_btn.clicked.connect(self.rebuild_selected)
        self.cancel_btn.clicked.connect(self.cancel_index)
        self.close_btn.clicked.connect(self.close)

        self.index_service.job_started.connect(self._on_job_started)
        self.index_service.progress.connect(self._on_progress)
        self.index_service.job_finished.connect(self._on_done)
        self.index_service.job_failed.connect(self._on_failed)
        self.index_service.queue_changed.connect(self._on_queue_changed)
        self.index_service.idle.connect(self._on_idle)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(1000)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()

        self.refresh()
        self._sync_service_status()

    def refresh(self):
        current_root_id = None
        selected = self._selected_root()
        if selected is not None:
            current_root_id = int(selected["id"])

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        selected_row = -1

        for root in self.manager.list_roots():
            row = self.table.rowCount()
            self.table.insertRow(row)
            root_id = int(root["id"])

            enabled = QTableWidgetItem()
            enabled.setFlags(enabled.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            enabled.setCheckState(
                Qt.CheckState.Checked if root["enabled"] else Qt.CheckState.Unchecked
            )
            enabled.setData(Qt.ItemDataRole.UserRole, root_id)

            if self.index_service.current_root_id == root_id:
                state = "搜索优先，已暂停" if self.index_service.is_paused else "后台更新中"
            elif self.index_service.is_pending(root_id):
                state = "等待更新"
            elif not root["enabled"]:
                state = "已暂停"
            elif root["last_scan_time"]:
                state = "已同步"
            else:
                state = "等待首次扫描"

            self.table.setItem(row, 0, enabled)
            self.table.setItem(row, 1, QTableWidgetItem(root["path"]))
            count_item = QTableWidgetItem(f"{int(root['item_count'] or 0):,}")
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 2, count_item)
            self.table.setItem(row, 3, QTableWidgetItem(format_datetime(root["last_scan_time"]) or "尚未"))
            self.table.setItem(row, 4, QTableWidgetItem(format_datetime(root["next_scan_time"]) or "—"))
            self.table.setItem(row, 5, QTableWidgetItem(f"{float(root['last_scan_duration'] or 0):.1f}s"))
            self.table.setItem(row, 6, QTableWidgetItem(state))

            if current_root_id == root_id:
                selected_row = row

        if selected_row >= 0:
            self.table.selectRow(selected_row)
        self.table.blockSignals(False)
        self._sync_service_status()

    def _selected_root(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        root_id = item.data(Qt.ItemDataRole.UserRole)
        return self.database.get_root(int(root_id)) if root_id else None

    def _enabled_changed(self, row: int, column: int):
        if column != 0:
            return
        item = self.table.item(row, 0)
        root_id = int(item.data(Qt.ItemDataRole.UserRole))
        enabled = item.checkState() == Qt.CheckState.Checked
        self.manager.set_enabled(root_id, enabled)
        if enabled:
            root = self.database.get_root(root_id)
            if root and os.path.isdir(root["path"]):
                self.index_service.enqueue(root_id, root["path"])
        else:
            self.index_service.cancel_root(root_id)
        self.roots_changed.emit()
        self.refresh()

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择需要长期保存索引的目录")
        if not folder:
            return
        try:
            root_id = self.manager.add_root(folder)
        except Exception as exc:
            QMessageBox.warning(self, "添加失败", str(exc))
            return
        self.roots_changed.emit()
        self.index_service.enqueue(root_id, folder)
        self.refresh()

    def open_selected_folder(self):
        root = self._selected_root()
        if root is None:
            return
        path = root["path"]
        if not os.path.isdir(path):
            QMessageBox.warning(self, "目录不存在", path)
            return
        try:
            os.startfile(path)
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))

    def clear_selected_archive(self):
        root = self._selected_root()
        if root is None:
            QMessageBox.information(self, "提示", "请先选中一个长期索引目录。")
            return

        root_id = int(root["id"])
        if self.index_service.current_root_id == root_id:
            QMessageBox.information(
                self,
                "正在扫描",
                "这个目录当前正在后台扫描。请先取消当前扫描，任务结束后再清空索引数据。",
            )
            return

        answer = QMessageBox.warning(
            self,
            "清空所选索引数据",
            f"确定清空这个目录已经写入 FastFinder 的索引数据吗？\n\n{root['path']}\n\n"
            "将删除该目录在 SQLite 中保存的所有文件/文件夹索引记录，但会保留目录配置。\n"
            "为了避免后台马上重新写入，清空后该目录会自动设为“暂停”。\n\n"
            "不会删除、移动或修改磁盘上的任何真实文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.index_service.remove_pending(root_id)
        self.manager.clear_root_archive(root_id, pause_root=True)
        self.refresh()
        self.roots_changed.emit()
        self.status.setText(
            "所选目录的 SQLite 索引数据已清空，并已自动暂停。"
            "需要恢复时重新勾选“启用”并点击“重新扫描”。"
        )

    def remove_selected(self):
        root = self._selected_root()
        if root is None:
            return
        root_id = int(root["id"])

        if self.index_service.current_root_id == root_id:
            QMessageBox.information(
                self,
                "正在扫描",
                "这个目录当前正在后台扫描。请先取消当前扫描，任务结束后再删除存档。",
            )
            return

        answer = QMessageBox.warning(
            self,
            "删除目录索引存档（第 1 次确认）",
            f"确定删除这个目录的 FastFinder 长期索引存档吗？\n\n{root['path']}\n\n"
            "会删除：\n"
            "• 该目录在 SQLite 中保存的全部文件/文件夹索引记录\n"
            "• 该目录本身的长期索引配置\n\n"
            "不会删除、移动或修改磁盘上的真实文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return


        second = QMessageBox.warning(
            self,
            "再次确认删除（第 2 次确认）",
            f"请再次确认：真的要从 FastFinder 索引库中删除这个存档吗？\n\n{root['path']}\n\n"
            "删除后，如果以后还想使用长期索引，需要重新添加该目录并重新扫描。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if second != QMessageBox.StandardButton.Yes:
            return

        self.index_service.remove_pending(root_id)
        self.manager.remove_root(root_id)
        self.refresh()
        self.roots_changed.emit()

    def rebuild_selected(self):
        root = self._selected_root()
        if root is None:
            QMessageBox.information(self, "提示", "请先选中一个索引目录。")
            return
        if not os.path.isdir(root["path"]):
            QMessageBox.warning(self, "目录不存在", root["path"])
            return
        if self.index_service.current_root_id == int(root["id"]):
            QMessageBox.information(self, "提示", "这个目录已经在后台扫描。")
            return
        added = self.index_service.enqueue(int(root["id"]), root["path"])
        if not added:
            QMessageBox.information(self, "提示", "这个目录已经在后台队列中。")
        self.refresh()

    def start_index(self, root_id: int, path: str):
        self.index_service.enqueue(root_id, path)
        self.refresh()

    def cancel_index(self):
        if self.index_service.is_running:
            self.index_service.cancel_current()
            self.status.setText("正在取消当前后台扫描……旧索引仍可继续搜索。")

    def _on_job_started(self, root_id: int, path: str):
        self.progress.setRange(0, 0)
        self.status.setText(f"后台正在扫描并更新：{path}")
        self.refresh()

    def _on_progress(self, root_id: int, data: dict):
        if root_id != self.index_service.current_root_id:
            return
        self.progress.setRange(0, 0)
        self.status.setText(
            f"后台更新中｜文件 {data['files']:,} ｜ 文件夹 {data['folders']:,} ｜ "
            f"新增 {int(data.get('added', 0)):,} ｜ 更新 {int(data.get('updated', 0)):,} ｜ "
            f"速度 {data['speed']:,} 项/秒 ｜ {data['elapsed']:.1f}s\n"
            f"{data['current']}"
        )

    def _on_done(self, root_id: int, data: dict):
        if data.get("cancelled"):
            self.status.setText("后台扫描已取消。旧索引未被清空，已读取的新内容会保留。")
        else:
            self.status.setText(
                f"后台增量更新完成：扫描 {data['files']:,} 个文件 + {data['folders']:,} 个文件夹；"
                f"新增 {int(data.get('added', 0)):,}，更新 {int(data.get('updated', 0)):,}，"
                f"删除 {int(data.get('removed', 0)):,}，未变化 {int(data.get('unchanged', 0)):,}；"
                f"耗时 {data['elapsed']:.2f}s"
            )
        self.refresh()

    def _on_failed(self, root_id: int, message: str):
        self.status.setText(f"后台索引失败：{message}")
        self.refresh()

    def _on_queue_changed(self, count: int):
        self._sync_service_status()

    def _on_idle(self):
        self._sync_service_status()
        self.refresh()

    def _sync_service_status(self):
        running = self.index_service.is_running
        self.cancel_btn.setEnabled(running)
        if running:
            self.progress.setRange(0, 0)
            if self.index_service.is_paused:
                self.status.setText(
                    f"搜索正在进行，后台索引已暂时让路：{self.index_service.current_path}\n"
                    "搜索结束后会自动继续，不会丢失进度。"
                )
                return
            if not self.status.text().startswith("后台更新中"):
                extra = (
                    f"，队列还有 {self.index_service.pending_count} 个目录"
                    if self.index_service.pending_count
                    else ""
                )
                self.status.setText(
                    f"后台正在更新：{self.index_service.current_path}{extra}\n"
                    "可直接关闭本窗口，后台任务不会停止。"
                )
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            if self.index_service.pending_count:
                self.status.setText(
                    f"等待后台处理 {self.index_service.pending_count} 个长期索引目录……"
                )
            elif self.status.text() in ("等待操作", "") or self.status.text().startswith("后台正在更新"):
                self.status.setText("长期索引已保持同步。删除存档只影响数据库，不影响磁盘文件。")
