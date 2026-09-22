from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from utils.app_info import (
    APP_DESCRIPTION,
    APP_DESCRIPTION_EN,
    APP_NAME,
    APP_VERSION,
    CONTACT_TEXT,
    COPYRIGHT_HOLDER,
    COPYRIGHT_YEAR,
    DEVELOPER_DISPLAY_NAME,
    LICENSE_NAME,
    ORGANIZATION_NAME,
    PRIVACY_SUMMARY,
    PROJECT_URL,
    software_summary,
)
from utils.resource_path import resource_path


_FALLBACK_MIT_TEXT = """MIT License

Copyright (c) 2026 SolveTrue and FastFinder contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


class AboutWindow(QDialog):
    """Built-in About / developer / license dialog.

    It does not depend on Windows file associations, so it also works after
    FastFinder is packaged as a standalone executable.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"关于 {APP_NAME}")
        self.resize(720, 560)
        self.setMinimumSize(580, 440)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel(f"{APP_NAME}  {APP_VERSION}")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        subtitle = QLabel(f"{APP_DESCRIPTION}\n{APP_DESCRIPTION_EN}")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("SubtleLabel")
        root.addWidget(subtitle)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_software_tab(), "软件信息")
        tabs.addTab(self._build_developer_tab(), "开发者")
        tabs.addTab(self._build_license_tab(), "MIT 许可证")
        root.addWidget(tabs, 1)

        button_row = QHBoxLayout()
        copy_btn = QPushButton("复制软件信息")
        copy_btn.clicked.connect(self._copy_info)
        button_row.addWidget(copy_btn)

        if PROJECT_URL:
            github_btn = QPushButton("打开项目主页")
            github_btn.clicked.connect(self._open_project_url)
            button_row.addWidget(github_btn)

        button_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        root.addLayout(button_row)

    def _build_software_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(
            f"""
            <h3>{APP_NAME}</h3>
            <p><b>版本：</b>{APP_VERSION}</p>
            <p><b>说明：</b>{APP_DESCRIPTION}</p>
            <p><b>组织 / 品牌：</b>{ORGANIZATION_NAME}</p>
            <p><b>许可证：</b>{LICENSE_NAME}</p>
            <p><b>版权：</b>Copyright (c) {COPYRIGHT_YEAR} {COPYRIGHT_HOLDER}</p>
            <hr>
            <p><b>隐私说明</b></p>
            <p>{PRIVACY_SUMMARY}</p>
            <p>长期索引数据库、设置、搜索历史和收藏均由本机 FastFinder 管理。</p>
            """
        )
        layout.addWidget(browser)
        return widget

    def _build_developer_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        lines = [
            f"开发者 / 维护者：{DEVELOPER_DISPLAY_NAME}",
            f"组织 / 品牌：{ORGANIZATION_NAME}",
        ]
        if PROJECT_URL:
            lines.append(f"项目主页：{PROJECT_URL}")
        else:
            lines.append("项目主页：尚未配置（发布 GitHub 后可在 utils/app_info.py 中填写）")
        if CONTACT_TEXT:
            lines.append(f"公开联系方式：{CONTACT_TEXT}")
        else:
            lines.append("公开联系方式：尚未配置")

        info = QLabel("\n\n".join(lines))
        info.setWordWrap(True)
        layout.addWidget(info)

        hint = QLabel(
            "公开发布前，如需展示你的姓名、昵称、GitHub 用户名或公开邮箱，"
            "只修改 utils/app_info.py 即可。建议只填写你明确愿意公开的信息。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("SubtleLabel")
        layout.addWidget(hint)
        layout.addStretch()
        return widget

    def _build_license_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        browser = QTextBrowser()
        browser.setPlainText(self._load_license_text())
        layout.addWidget(browser)
        return widget

    @staticmethod
    def _load_license_text() -> str:
        path = resource_path("LICENSE")
        try:
            if Path(path).exists():
                return Path(path).read_text(encoding="utf-8")
        except Exception:
            pass
        return _FALLBACK_MIT_TEXT

    def _copy_info(self):
        QApplication.clipboard().setText(software_summary())
        QMessageBox.information(self, "已复制", "FastFinder 软件信息已复制到剪贴板。")

    def _open_project_url(self):
        if PROJECT_URL:
            QDesktopServices.openUrl(QUrl(PROJECT_URL))
