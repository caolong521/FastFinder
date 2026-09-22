from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.about_window import AboutWindow
from app.shortcuts_help_window import ShortcutsHelpWindow
from core.global_hotkey import validate_hotkey_sequence
from database.database import Database
from utils.settings import SettingsStore
from utils.time_utils import format_datetime


class SettingsWindow(QDialog):
    settings_saved = Signal(dict)

    def __init__(
        self,
        store: SettingsStore,
        parent=None,
        startup_manager=None,
        database: Database | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self.startup_manager = startup_manager
        self.database = database
        self.setWindowTitle("FastFinder 设置")
        self.resize(760, 720)
        self.setMinimumSize(620, 500)
        settings = store.load()

        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(4, 4, 4, 4)
        body_layout.setSpacing(12)

        # --------------------------------------------------------------
        # Search behavior
        # --------------------------------------------------------------
        search_group = QGroupBox("搜索")
        search_form = QFormLayout(search_group)

        self.excluded_edit = QLineEdit(", ".join(settings.get("excluded_dirs", [])))
        self.excluded_edit.setPlaceholderText("node_modules, .git, .vs, .idea, __pycache__")
        search_form.addRow("忽略目录：", self.excluded_edit)

        self.debounce_spin = QSpinBox()
        self.debounce_spin.setRange(50, 1000)
        self.debounce_spin.setSuffix(" ms")
        self.debounce_spin.setValue(int(settings.get("debounce_ms", 120)))
        search_form.addRow("实时搜索延迟：", self.debounce_spin)

        self.result_limit_spin = QSpinBox()
        self.result_limit_spin.setRange(50, 2000)
        self.result_limit_spin.setValue(int(settings.get("result_limit", 200)))
        search_form.addRow("最大显示结果：", self.result_limit_spin)

        self.candidate_spin = QSpinBox()
        self.candidate_spin.setRange(500, 20000)
        self.candidate_spin.setSingleStep(500)
        self.candidate_spin.setValue(int(settings.get("candidate_limit", 3000)))
        search_form.addRow("候选数量：", self.candidate_spin)

        self.fuzzy_spin = QSpinBox()
        self.fuzzy_spin.setRange(30, 95)
        self.fuzzy_spin.setSuffix("%")
        self.fuzzy_spin.setValue(int(settings.get("fuzzy_threshold", 58)))
        search_form.addRow("模糊匹配阈值：", self.fuzzy_spin)
        body_layout.addWidget(search_group)

        # --------------------------------------------------------------
        # Smart long-term index maintenance
        # --------------------------------------------------------------
        auto_group = QGroupBox("长期索引自动更新")
        auto_form = QFormLayout(auto_group)

        self.auto_index_check = QCheckBox("自动维护长期索引（推荐）")
        self.auto_index_check.setChecked(bool(settings.get("auto_index_enabled", True)))
        auto_form.addRow("自动维护：", self.auto_index_check)

        self.auto_mode_combo = QComboBox()
        modes = [
            ("智能更新（推荐）", "smart"),
            ("每 30 分钟", "30m"),
            ("每 1 小时", "60m"),
            ("每 3 小时", "180m"),
            ("每天", "daily"),
            ("仅启动时检查", "startup_only"),
            ("不自动更新", "off"),
        ]
        selected_mode = str(settings.get("auto_index_mode", "smart"))
        for text, data in modes:
            self.auto_mode_combo.addItem(text, data)
            if data == selected_mode:
                self.auto_mode_combo.setCurrentIndex(self.auto_mode_combo.count() - 1)
        auto_form.addRow("更新模式：", self.auto_mode_combo)

        self.startup_check = QCheckBox("FastFinder 启动后检查已经过期的索引")
        self.startup_check.setChecked(bool(settings.get("auto_index_startup_check", True)))
        auto_form.addRow("启动补偿：", self.startup_check)

        self.startup_delay_spin = QSpinBox()
        self.startup_delay_spin.setRange(0, 300)
        self.startup_delay_spin.setSuffix(" 秒")
        self.startup_delay_spin.setValue(int(settings.get("auto_index_startup_delay_sec", 20)))
        auto_form.addRow("启动延迟：", self.startup_delay_spin)

        self.idle_first_check = QCheckBox("优先在托盘/空闲状态更新，避免影响正常使用")
        self.idle_first_check.setChecked(bool(settings.get("auto_index_idle_first", True)))
        auto_form.addRow("空闲优先：", self.idle_first_check)

        self.idle_seconds_spin = QSpinBox()
        self.idle_seconds_spin.setRange(10, 900)
        self.idle_seconds_spin.setSingleStep(10)
        self.idle_seconds_spin.setSuffix(" 秒")
        self.idle_seconds_spin.setValue(int(settings.get("auto_index_idle_seconds", 120)))
        auto_form.addRow("空闲判定：", self.idle_seconds_spin)

        self.pause_search_check = QCheckBox("搜索时暂停后台索引写入，搜索结束后自动继续")
        self.pause_search_check.setChecked(bool(settings.get("auto_index_pause_on_search", True)))
        auto_form.addRow("搜索优先：", self.pause_search_check)

        self.watch_check = QCheckBox("高级：实时监听文件变化（watchdog，默认关闭）")
        self.watch_check.setChecked(bool(settings.get("watch_changes", False)))
        auto_form.addRow("实时监听：", self.watch_check)

        summary = self.database.get_index_update_summary() if self.database is not None else {}
        self.last_update_label = QLabel(format_datetime(summary.get("last_scan_time")))
        self.next_update_label = QLabel(format_datetime(summary.get("next_scan_time")))
        auto_form.addRow("最近一次更新：", self.last_update_label)
        auto_form.addRow("下一次预计：", self.next_update_label)

        auto_hint = QLabel(
            "智能模式：小目录（<2万项）约 30 分钟检查一次；中目录（2万~20万项）约 1 小时；"
            "大目录（>20万项）约 3 小时。一次只更新一个目录。实时 watchdog 不是必需功能，"
            "普通用户保持关闭即可。"
        )
        auto_hint.setWordWrap(True)
        auto_hint.setObjectName("SubtleLabel")
        auto_form.addRow("说明：", auto_hint)
        body_layout.addWidget(auto_group)

        self.auto_index_check.toggled.connect(self._sync_auto_controls)
        self.auto_mode_combo.currentIndexChanged.connect(self._sync_auto_controls)
        self.startup_check.toggled.connect(self._sync_auto_controls)
        self.idle_first_check.toggled.connect(self._sync_auto_controls)

        # --------------------------------------------------------------
        # Window / shortcuts / startup
        # --------------------------------------------------------------
        app_group = QGroupBox("窗口、快捷键与启动")
        form = QFormLayout(app_group)

        self.minimize_tray_check = QCheckBox("最小化主窗口时隐藏到系统托盘")
        self.minimize_tray_check.setChecked(bool(settings.get("minimize_to_tray", True)))
        form.addRow("最小化到托盘：", self.minimize_tray_check)

        self.close_tray_check = QCheckBox("点击右上角 X 时隐藏到托盘，不退出 FastFinder")
        self.close_tray_check.setChecked(bool(settings.get("close_to_tray", True)))
        form.addRow("关闭到托盘：", self.close_tray_check)

        self.tray_notification_check = QCheckBox("第一次隐藏到托盘时显示提示")
        self.tray_notification_check.setChecked(bool(settings.get("tray_notification", True)))
        form.addRow("托盘提示：", self.tray_notification_check)

        self.global_hotkey_check = QCheckBox("启用系统全局快捷键，按一次显示，再按一次隐藏")
        self.global_hotkey_check.setChecked(bool(settings.get("global_hotkey_enabled", True)))
        form.addRow("主窗口快捷键：", self.global_hotkey_check)

        self.hotkey_edit = QKeySequenceEdit()
        self.hotkey_edit.setMaximumSequenceLength(1)
        self.hotkey_edit.setKeySequence(QKeySequence(str(settings.get("global_hotkey", "Ctrl+Alt+Space"))))
        self.hotkey_edit.setEnabled(self.global_hotkey_check.isChecked())
        self.global_hotkey_check.toggled.connect(self.hotkey_edit.setEnabled)
        form.addRow("显示 / 隐藏：", self.hotkey_edit)

        self.quick_hotkey_check = QCheckBox("启用独立的小搜索窗快捷键")
        self.quick_hotkey_check.setChecked(bool(settings.get("quick_hotkey_enabled", True)))
        form.addRow("小搜索窗：", self.quick_hotkey_check)

        self.quick_hotkey_edit = QKeySequenceEdit()
        self.quick_hotkey_edit.setMaximumSequenceLength(1)
        self.quick_hotkey_edit.setKeySequence(QKeySequence(str(settings.get("quick_hotkey", "Ctrl+Alt+F"))))
        self.quick_hotkey_edit.setEnabled(self.quick_hotkey_check.isChecked())
        self.quick_hotkey_check.toggled.connect(self.quick_hotkey_edit.setEnabled)
        form.addRow("快速搜索快捷键：", self.quick_hotkey_edit)

        self.start_hidden_check = QCheckBox("启动程序后直接进入系统托盘，后台运行")
        self.start_hidden_check.setChecked(bool(settings.get("start_hidden_to_tray", False)))
        form.addRow("启动到托盘：", self.start_hidden_check)

        self.esc_hide_check = QCheckBox("搜索框为空时按 Esc 隐藏到托盘")
        self.esc_hide_check.setChecked(bool(settings.get("esc_hides_window", True)))
        form.addRow("Esc 快速隐藏：", self.esc_hide_check)

        self.windows_startup_check = QCheckBox("允许 FastFinder 随当前 Windows 用户登录后自动启动")
        startup_value = bool(settings.get("start_with_windows", False))
        if self.startup_manager is not None:
            startup_value = bool(self.startup_manager.is_enabled())
        self.windows_startup_check.setChecked(startup_value)
        form.addRow("Windows 开机自启：", self.windows_startup_check)

        self.history_check = QCheckBox("记录搜索历史，便于再次查找（可随时清空）")
        self.history_check.setChecked(bool(settings.get("search_history_enabled", True)))
        form.addRow("搜索历史：", self.history_check)

        self.usage_ranking_check = QCheckBox("根据实际打开次数轻微提高常用文件排名")
        self.usage_ranking_check.setChecked(bool(settings.get("usage_ranking_enabled", True)))
        form.addRow("常用文件加权：", self.usage_ranking_check)
        body_layout.addWidget(app_group)

        startup_hint = QLabel(
            "Windows 开机自启默认关闭，只有你在这里主动勾选并保存后才会写入当前用户启动项。"
            "FastFinder 使用 HKCU 用户级启动项，不修改系统级 HKLM；通常不需要管理员权限。"
        )
        startup_hint.setWordWrap(True)
        startup_hint.setObjectName("SubtleLabel")
        body_layout.addWidget(startup_hint)

        hotkey_hint = QLabel(
            "主窗口建议 Ctrl+Alt+Space；小搜索窗建议 Ctrl+Alt+F。两个全局快捷键不能设置成同一个组合。"
        )
        hotkey_hint.setWordWrap(True)
        hotkey_hint.setObjectName("SubtleLabel")
        body_layout.addWidget(hotkey_hint)

        self.hotkey_help_btn = QPushButton("查看快捷键说明（SHORTCUTS.md）")
        self.hotkey_help_btn.clicked.connect(self.open_shortcuts_help)
        body_layout.addWidget(self.hotkey_help_btn)

        self.about_btn = QPushButton("关于 FastFinder / 软件信息 / MIT License")
        self.about_btn.clicked.connect(self.open_about)
        body_layout.addWidget(self.about_btn)

        hint = QLabel("提示：bin / obj / venv 默认不忽略，方便开发时搜索 DLL、EXE 和环境文件。")
        hint.setWordWrap(True)
        body_layout.addWidget(hint)
        body_layout.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_auto_controls()

    def _sync_auto_controls(self):
        master = self.auto_index_check.isChecked()
        mode_enabled = master and self.auto_mode_combo.currentData() != "off"
        self.auto_mode_combo.setEnabled(master)
        self.startup_check.setEnabled(mode_enabled)
        self.startup_delay_spin.setEnabled(mode_enabled and self.startup_check.isChecked())
        self.idle_first_check.setEnabled(mode_enabled)
        self.idle_seconds_spin.setEnabled(mode_enabled and self.idle_first_check.isChecked())
        self.pause_search_check.setEnabled(mode_enabled)

    def open_shortcuts_help(self):
        dialog = ShortcutsHelpWindow(self)
        dialog.exec()

    def open_about(self):
        dialog = AboutWindow(self)
        dialog.exec()

    def save(self):
        hotkey = self.hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        quick_hotkey = self.quick_hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)

        if self.global_hotkey_check.isChecked():
            ok, error = validate_hotkey_sequence(hotkey)
            if not ok:
                QMessageBox.warning(self, "主窗口快捷键不可用", error)
                self.hotkey_edit.setFocus()
                return

        if self.quick_hotkey_check.isChecked():
            ok, error = validate_hotkey_sequence(quick_hotkey)
            if not ok:
                QMessageBox.warning(self, "小搜索窗快捷键不可用", error)
                self.quick_hotkey_edit.setFocus()
                return

        if (
            self.global_hotkey_check.isChecked()
            and self.quick_hotkey_check.isChecked()
            and hotkey.casefold() == quick_hotkey.casefold()
        ):
            QMessageBox.warning(self, "快捷键冲突", "主窗口快捷键和小搜索窗快捷键不能相同。")
            return

        if self.startup_manager is not None:
            ok, error = self.startup_manager.set_enabled(self.windows_startup_check.isChecked())
            if not ok:
                QMessageBox.warning(self, "开机自启设置失败", error)
                return

        excluded = [x.strip() for x in self.excluded_edit.text().replace(";", ",").split(",") if x.strip()]
        settings = {
            "excluded_dirs": excluded,
            "debounce_ms": self.debounce_spin.value(),
            "result_limit": self.result_limit_spin.value(),
            "candidate_limit": self.candidate_spin.value(),
            "fuzzy_threshold": self.fuzzy_spin.value(),
            "auto_index_enabled": self.auto_index_check.isChecked(),
            "auto_index_mode": str(self.auto_mode_combo.currentData()),
            "auto_index_startup_check": self.startup_check.isChecked(),
            "auto_index_startup_delay_sec": self.startup_delay_spin.value(),
            "auto_index_idle_first": self.idle_first_check.isChecked(),
            "auto_index_idle_seconds": self.idle_seconds_spin.value(),
            "auto_index_pause_on_search": self.pause_search_check.isChecked(),
            "watch_changes": self.watch_check.isChecked(),
            "background_update_settings_version": 1,
            "minimize_to_tray": self.minimize_tray_check.isChecked(),
            "close_to_tray": self.close_tray_check.isChecked(),
            "tray_notification": self.tray_notification_check.isChecked(),
            "global_hotkey_enabled": self.global_hotkey_check.isChecked(),
            "global_hotkey": hotkey or "Ctrl+Alt+Space",
            "quick_hotkey_enabled": self.quick_hotkey_check.isChecked(),
            "quick_hotkey": quick_hotkey or "Ctrl+Alt+F",
            "start_hidden_to_tray": self.start_hidden_check.isChecked(),
            "esc_hides_window": self.esc_hide_check.isChecked(),
            "start_with_windows": self.windows_startup_check.isChecked(),
            "search_history_enabled": self.history_check.isChecked(),
            "usage_ranking_enabled": self.usage_ranking_check.isChecked(),
        }
        self.store.save(settings)
        self.settings_saved.emit(settings)
        self.accept()
