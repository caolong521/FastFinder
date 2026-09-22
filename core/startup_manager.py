from __future__ import annotations

import sys
from pathlib import Path


class StartupManager:
    r"""Manage per-user Windows startup via HKCU\...\Run.

    This is intentionally user-scoped. FastFinder never writes HKLM and never
    requests administrator elevation. Enabling startup is an explicit user
    choice in Settings and defaults to off.
    """

    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def __init__(self, app_name: str = "FastFinder", base_dir: str | Path | None = None):
        self.app_name = app_name
        self.base_dir = Path(base_dir or Path(__file__).resolve().parents[1])

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def _startup_command(self) -> str:
        if getattr(sys, "frozen", False):
            return f'"{Path(sys.executable).resolve()}"'

        python_exe = Path(sys.executable).resolve()
        pythonw = python_exe.with_name("pythonw.exe")
        runner = pythonw if pythonw.exists() else python_exe
        main_py = (self.base_dir / "main.py").resolve()
        return f'"{runner}" "{main_py}"'

    def is_enabled(self) -> bool:
        if not self.supported:
            return False
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, self.app_name)
                return bool(str(value).strip())
        except (FileNotFoundError, OSError):
            return False

    def set_enabled(self, enabled: bool) -> tuple[bool, str]:
        if not self.supported:
            if not enabled:
                return True, ""
            return False, "当前系统不是 Windows，无法配置 Windows 开机自启。"

        try:
            import winreg

            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                self.RUN_KEY,
                0,
                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
            ) as key:
                if enabled:
                    winreg.SetValueEx(
                        key,
                        self.app_name,
                        0,
                        winreg.REG_SZ,
                        self._startup_command(),
                    )
                else:
                    try:
                        winreg.DeleteValue(key, self.app_name)
                    except FileNotFoundError:
                        pass
            return True, ""
        except PermissionError:
            return False, (
                "Windows 拒绝写入当前用户的启动项。请检查安全软件/组策略是否禁止修改开机启动。"
            )
        except OSError as exc:
            return False, f"设置 Windows 开机自启失败：{exc}"
