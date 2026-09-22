import os
from pathlib import Path


def normalize_search_text(text: str) -> str:
    return (text or "").strip().casefold()


def normalize_path(path: str) -> str:
    return os.path.normpath(path).casefold()


def path_depth(path: str) -> int:
    try:
        return len(Path(path).parts)
    except Exception:
        return path.count(os.sep)


def is_same_or_child(path: str, root: str) -> bool:
    try:
        p = os.path.abspath(path)
        r = os.path.abspath(root)
        return os.path.commonpath([p, r]) == r
    except Exception:
        return False
