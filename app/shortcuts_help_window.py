from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from utils.resource_path import resource_path


FALLBACK_MARKDOWN = r"""# FastFinder 快捷键

## 全局快捷键

| 快捷键 | 功能 |
| --- | --- |
| `Ctrl + Alt + Space` | 显示 / 隐藏 FastFinder 主窗口 |
| `Ctrl + Alt + F` | 打开快速搜索小窗 |

> 这两个全局快捷键都可以在 **设置** 中自定义。

## 主窗口快捷键

| 快捷键 | 功能 |
| --- | --- |
| `Ctrl + L` / `Ctrl + F` | 聚焦搜索框 |
| `Esc` | 清空搜索；搜索框为空时隐藏到托盘 |
| `Enter` | 打开当前选中的文件或文件夹 |
| `Ctrl + Enter` | 在资源管理器中定位 |
| `Ctrl + C` | 复制完整路径 |
| `Ctrl + Shift + C` | 复制文件名 |
| `Ctrl + I` | 打开索引库 |
| `Ctrl + H` | 打开搜索历史 |
| `Ctrl + B` | 打开收藏 |
| `Ctrl + ,` | 打开设置 |
| `Alt + 1` | 切换到索引搜索 |
| `Alt + 2` | 切换到指定目录搜索 |

## 快速搜索小窗

| 快捷键 | 功能 |
| --- | --- |
| `↑` / `↓` | 选择结果 |
| `Enter` | 打开结果 |
| `Ctrl + Enter` | 定位结果 |
| `Esc` | 关闭小搜索窗 |
"""


class ShortcutsHelpWindow(QDialog):
    """FastFinder 内置快捷键帮助窗口。

    不调用系统默认 Markdown 程序，因此即使 Windows 没有关联 .md 文件也能正常查看。
    PyInstaller 打包时只要 SHORTCUTS.md 被包含在资源中，同样可以读取。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FastFinder 快捷键说明")
        self.setModal(True)
        self.resize(760, 650)
        self.setMinimumSize(560, 420)

        self._markdown_path = resource_path("SHORTCUTS.md")
        self._markdown_text = self._load_markdown(self._markdown_path)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("快捷键说明")
        title.setObjectName("PageTitle")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel("这些快捷键可以让 FastFinder 基本不依赖鼠标完成查找、打开和隐藏。")
        subtitle.setObjectName("SubtleLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        self.browser.setReadOnly(True)
        self.browser.document().setDefaultStyleSheet(
            """
            body { font-family: 'Microsoft YaHei UI'; font-size: 14px; }
            h1 { font-size: 22px; margin-bottom: 12px; }
            h2 { font-size: 17px; margin-top: 18px; margin-bottom: 8px; }
            table { border-collapse: collapse; }
            th, td { padding: 6px 10px; border: 1px solid #d8dee9; }
            code { background: #f3f5f8; padding: 2px 5px; }
            blockquote { color: #667085; border-left: 3px solid #9bbcff; padding-left: 10px; }
            """
        )
        self.browser.setMarkdown(self._markdown_text)
        layout.addWidget(self.browser, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        source_label = QLabel(
            "SHORTCUTS.md" if self._markdown_path.exists() else "内置快捷键说明（SHORTCUTS.md 未找到）"
        )
        source_label.setObjectName("SubtleLabel")
        source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bottom.addWidget(source_label, 1)

        copy_btn = QPushButton("复制说明")
        copy_btn.clicked.connect(self.copy_markdown)
        bottom.addWidget(copy_btn)

        close_btn = QPushButton("关闭")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)

        layout.addLayout(bottom)

    @staticmethod
    def _load_markdown(path: Path) -> str:
        if not path.exists():
            return FALLBACK_MARKDOWN

        # GitHub/Windows 用户最常见是 UTF-8；同时兼容带 BOM 的 UTF-8。
        for encoding in ("utf-8-sig", "utf-8"):
            try:
                text = path.read_text(encoding=encoding)
                if text.strip():
                    return text
            except UnicodeDecodeError:
                continue
            except OSError:
                break

        return FALLBACK_MARKDOWN

    def copy_markdown(self):
        QApplication.clipboard().setText(self._markdown_text)
        QMessageBox.information(self, "已复制", "快捷键说明已复制到剪贴板。")
