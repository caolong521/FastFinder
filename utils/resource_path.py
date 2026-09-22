from __future__ import annotations

import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """
    返回资源文件的绝对路径。

    - 源码运行：以项目根目录为基准。
    - PyInstaller 打包：兼容 sys._MEIPASS 临时目录。
    """
    if hasattr(sys, "_MEIPASS"):
        base_dir = Path(getattr(sys, "_MEIPASS"))
    else:
        base_dir = Path(__file__).resolve().parent.parent
    return base_dir.joinpath(*parts)
