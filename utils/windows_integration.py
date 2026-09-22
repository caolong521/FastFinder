from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from utils.app_info import APP_USER_MODEL_ID

# Keep loaded native icon handles alive for the process lifetime.
_NATIVE_ICON_HANDLES: list[int] = []


def configure_windows_app_identity(app_id: str = APP_USER_MODEL_ID) -> bool:
    """
    Give FastFinder its own Windows AppUserModelID.

    This must run before QApplication is created. Without an explicit AppUserModelID,
    Windows can group a source-mode FastFinder window under python.exe/pythonw.exe and
    show the Python taskbar icon even when Qt's window icon is already correct.
    """
    if sys.platform != "win32":
        return False

    try:
        shell32 = ctypes.windll.shell32
        result = shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
        return result == 0
    except Exception:
        return False


def apply_native_window_icon(window, icon_path: str | Path) -> bool:
    """
    Force the Windows HWND large/small icons from the .ico file.

    Qt's setWindowIcon() remains the normal cross-platform icon path. This extra native
    step fixes a Windows edge case where the title bar icon is correct but the taskbar
    button still inherits python.exe/pythonw.exe while running from source.
    """
    if sys.platform != "win32":
        return False

    path = Path(icon_path)
    if not path.exists():
        return False

    try:
        user32 = ctypes.windll.user32
        hwnd = int(window.winId())

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1

        load_image = user32.LoadImageW
        load_image.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        load_image.restype = ctypes.c_void_p

        flags = LR_LOADFROMFILE | LR_DEFAULTSIZE
        big_icon = load_image(None, str(path), IMAGE_ICON, 32, 32, flags)
        small_icon = load_image(None, str(path), IMAGE_ICON, 16, 16, flags)

        if big_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big_icon)
            _NATIVE_ICON_HANDLES.append(int(big_icon))
        if small_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small_icon)
            _NATIVE_ICON_HANDLES.append(int(small_icon))

        return bool(big_icon or small_icon)
    except Exception:
        # Never block application startup only because Windows icon integration failed.
        return False
