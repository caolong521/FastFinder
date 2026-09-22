from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QMimeData, QUrl, Qt

from models.search_result import SearchResult
from utils.file_utils import format_size
from utils.time_utils import format_datetime


class SearchResultModel(QAbstractTableModel):
    HEADERS = ["类型", "名称", "路径", "修改时间", "大小", "相关度"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[SearchResult] = []

    def set_results(self, items: list[SearchResult]) -> None:
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def clear(self) -> None:
        self.set_results([])

    def item_at(self, row: int) -> SearchResult | None:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                if item.is_directory:
                    return "📁 文件夹"
                return (item.extension.lstrip(".") or "文件").upper()
            if col == 1:
                return ("★ " if item.is_favorite else "") + item.name
            if col == 2:
                return item.parent_path
            if col == 3:
                return format_datetime(item.modified_time)
            if col == 4:
                return "" if item.is_directory else format_size(item.size)
            if col == 5:
                return str(int(item.score)) if item.score else ""

        if role == Qt.ItemDataRole.ToolTipRole:
            usage = f"\n已打开 {item.open_count} 次" if item.open_count else ""
            favorite = "\n★ 已收藏" if item.is_favorite else ""
            return item.full_path + favorite + usage

        if role == Qt.ItemDataRole.UserRole:
            return item.full_path

        if role == Qt.ItemDataRole.TextAlignmentRole and col in (4, 5):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        return None

    # ------------------------------------------------------------------
    # Windows/desktop drag-out support
    # ------------------------------------------------------------------
    def flags(self, index: QModelIndex):
        flags = super().flags(index)
        if index.isValid():
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        return flags

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        mime = QMimeData()
        rows = sorted({index.row() for index in indexes if index.isValid()})
        urls = []
        for row in rows:
            item = self.item_at(row)
            if item and item.full_path:
                urls.append(QUrl.fromLocalFile(item.full_path))
        mime.setUrls(urls)
        return mime

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction
