from __future__ import annotations

import re
import shlex
from datetime import datetime

from models.search_query import SearchQuery


_SIZE_RE = re.compile(r"^([<>]=?)?\s*(\d+(?:\.\d+)?)\s*(b|kb|mb|gb|tb)?$", re.I)


def _parse_date(value: str) -> int | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return int(datetime.strptime(value, fmt).timestamp())
        except ValueError:
            continue
    return None


def _parse_size(value: str) -> tuple[int | None, int | None]:
    match = _SIZE_RE.match(value.strip())
    if not match:
        return None, None
    op, number, unit = match.groups()
    multiplier = {
        None: 1,
        "b": 1,
        "kb": 1024,
        "mb": 1024 ** 2,
        "gb": 1024 ** 3,
        "tb": 1024 ** 4,
    }[(unit or "b").lower()]
    size = int(float(number) * multiplier)
    if op in (">", ">="):
        return size, None
    if op in ("<", "<="):
        return None, size
    return size, size


class QueryParser:
    def parse(self, text: str) -> SearchQuery:
        query = SearchQuery(raw_text=text or "")
        try:
            tokens = shlex.split(text, posix=False)
        except ValueError:
            tokens = (text or "").split()

        for raw in tokens:
            token = raw.strip().strip('"').strip("'")
            if not token:
                continue
            lower = token.casefold()

            if lower.startswith("ext:"):
                values = lower[4:].replace(";", ",").split(",")
                query.extensions.extend(v.lstrip("*.") for v in values if v)
            elif lower.startswith("type:"):
                value = lower[5:]
                if value in ("file", "文件"):
                    query.type_filter = "file"
                elif value in ("folder", "dir", "directory", "文件夹"):
                    query.type_filter = "folder"
            elif lower.startswith("path:"):
                query.path_filter = token[5:].casefold()
            elif lower.startswith("after:"):
                query.after_time = _parse_date(token[6:])
            elif lower.startswith("before:"):
                query.before_time = _parse_date(token[7:])
            elif lower.startswith("size:"):
                query.size_min, query.size_max = _parse_size(token[5:])
            else:
                query.keywords.append(lower)

        return query
