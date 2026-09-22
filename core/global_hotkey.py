from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication


WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

DEFAULT_HOTKEY_ID = 0x4F46


_KEY_MAP = {
    "SPACE": 0x20,
    "TAB": 0x09,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "BACKSPACE": 0x08,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "HOME": 0x24,
    "END": 0x23,
    "PGUP": 0x21,
    "PAGEUP": 0x21,
    "PGDOWN": 0x22,
    "PAGEDOWN": 0x22,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
}


class _WindowsHotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: "GlobalHotkeyManager"):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        if sys.platform != "win32":
            return False, 0
        try:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and int(msg.wParam) == self.owner.hotkey_id:
                self.owner.activated.emit()
                return True, 0
        except Exception:
            # Native event filters must never break the Qt event loop.
            pass
        return False, 0


def _portable_sequence(sequence: str) -> str:
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.PortableText).strip()


def validate_hotkey_sequence(sequence: str) -> tuple[bool, str]:
    try:
        _parse_windows_hotkey(sequence)
        return True, ""
    except ValueError as exc:
        return False, str(exc)


def _parse_windows_hotkey(sequence: str) -> tuple[int, int, str]:
    text = _portable_sequence(sequence)
    if not text:
        raise ValueError("请按下一个快捷键组合。")

    parts = [part.strip() for part in text.split("+") if part.strip()]
    if not parts:
        raise ValueError("快捷键格式无效。")

    modifiers = 0
    key_name = None
    for part in parts:
        upper = part.upper()
        if upper in ("CTRL", "CONTROL"):
            modifiers |= MOD_CONTROL
        elif upper == "ALT":
            modifiers |= MOD_ALT
        elif upper == "SHIFT":
            modifiers |= MOD_SHIFT
        elif upper in ("META", "WIN", "WINDOWS"):
            modifiers |= MOD_WIN
        else:
            if key_name is not None:
                raise ValueError("全局快捷键只能包含一个主按键。")
            key_name = part

    if modifiers == 0:
        raise ValueError("全局快捷键至少要包含 Ctrl、Alt、Shift 或 Win 中的一个修饰键。")
    if key_name is None:
        raise ValueError("快捷键缺少主按键。")

    upper_key = key_name.upper()
    if len(upper_key) == 1 and (upper_key.isalpha() or upper_key.isdigit()):
        vk = ord(upper_key)
    elif upper_key.startswith("F") and upper_key[1:].isdigit():
        number = int(upper_key[1:])
        if not 1 <= number <= 24:
            raise ValueError("功能键仅支持 F1 到 F24。")
        vk = 0x70 + number - 1
    elif upper_key in _KEY_MAP:
        vk = _KEY_MAP[upper_key]
    elif len(key_name) == 1 and sys.platform == "win32":
        # Let Windows translate punctuation keys such as / ; [ ] , . - = `.
        translated = ctypes.windll.user32.VkKeyScanW(ord(key_name))
        if translated == -1:
            raise ValueError(f"暂不支持按键：{key_name}")
        vk = translated & 0xFF
    else:
        raise ValueError(
            "主按键建议使用字母、数字、Space、方向键、Home/End、PgUp/PgDown 或 F1-F24。"
        )

    return modifiers, vk, text


class GlobalHotkeyManager(QObject):
    """Windows global hotkey wrapper based on RegisterHotKey.

    It does not require administrator privileges and adds no third-party
    dependency. On non-Windows platforms the feature reports unsupported and
    the rest of FastFinder continues to work normally.
    """

    activated = Signal()

    def __init__(self, parent=None, hotkey_id: int = DEFAULT_HOTKEY_ID):
        super().__init__(parent)
        self.hotkey_id = int(hotkey_id)
        self.current_sequence = ""
        self.last_error = ""
        self._registered = False
        self._filter = _WindowsHotkeyEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    @property
    def registered(self) -> bool:
        return self._registered

    def register(self, sequence: str) -> tuple[bool, str]:
        self.last_error = ""
        if not self.supported:
            self.unregister()
            self.last_error = "当前系统暂不支持 FastFinder 全局快捷键。"
            return False, self.last_error

        try:
            modifiers, vk, normalized = _parse_windows_hotkey(sequence)
        except ValueError as exc:
            self.last_error = str(exc)
            return False, self.last_error

        if self._registered and normalized == self.current_sequence:
            return True, ""

        old_sequence = self.current_sequence if self._registered else ""
        self.unregister()

        ok = bool(
            ctypes.windll.user32.RegisterHotKey(
                None,
                self.hotkey_id,
                modifiers | MOD_NOREPEAT,
                vk,
            )
        )
        if ok:
            self._registered = True
            self.current_sequence = normalized
            return True, ""

        self.last_error = (
            f"快捷键 {normalized} 注册失败，通常是已被 Windows 或其它软件占用。"
        )

        # Best effort: restore the previous shortcut so a failed change does
        # not silently remove the user's working global shortcut.
        if old_sequence:
            try:
                old_modifiers, old_vk, old_normalized = _parse_windows_hotkey(old_sequence)
                restored = bool(
                    ctypes.windll.user32.RegisterHotKey(
                        None,
                        self.hotkey_id,
                        old_modifiers | MOD_NOREPEAT,
                        old_vk,
                    )
                )
                if restored:
                    self._registered = True
                    self.current_sequence = old_normalized
            except Exception:
                pass
        return False, self.last_error

    def unregister(self) -> None:
        if self.supported and self._registered:
            try:
                ctypes.windll.user32.UnregisterHotKey(None, self.hotkey_id)
            except Exception:
                pass
        self._registered = False
        self.current_sequence = ""

    def shutdown(self) -> None:
        self.unregister()
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeNativeEventFilter(self._filter)
            except Exception:
                pass
