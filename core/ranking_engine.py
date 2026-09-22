from __future__ import annotations

import math
import re
import time

from rapidfuzz import fuzz

from models.search_result import SearchResult


def _get(row, key: str, default=0):
    """同时兼容 sqlite.Row/dict 和 slots dataclass，并允许缺省字段。"""
    try:
        value = row[key]
    except (TypeError, KeyError, IndexError):
        value = getattr(row, key, default)
    return default if value is None else value


class RankingEngine:
    """相关度分层 + 轻量行为加权。

    名称命中等级始终是主因素；收藏与使用频率只做小幅加分，避免
    “常用但不相关”的文件压过真正匹配的结果。
    """

    def __init__(self, fuzzy_threshold: int = 58, behavior_ranking: bool = True):
        self.fuzzy_threshold = fuzzy_threshold
        self.behavior_ranking = bool(behavior_ranking)

    @staticmethod
    def _word_boundary_hit(name: str, keyword: str) -> bool:
        try:
            return re.search(rf"(^|[^0-9a-zA-Z]){re.escape(keyword)}", name, re.I) is not None
        except re.error:
            return False

    def score(self, row, keywords: list[str]) -> float:
        name = _get(row, "name_norm", "")
        stem = _get(row, "stem_norm", "")
        path = _get(row, "path_norm", "")
        joined = " ".join(keywords).strip()

        if not keywords:
            base = 1000.0
        elif name == joined:
            base = 10000.0
        elif stem == joined:
            base = 9800.0
        elif name.startswith(joined) or stem.startswith(joined):
            base = 9200.0
        elif len(keywords) == 1 and self._word_boundary_hit(name, keywords[0]):
            base = 8800.0
        elif all(k in name or k in stem for k in keywords):
            base = 8400.0 if len(keywords) == 1 else 8600.0
        elif any(k in name or k in stem for k in keywords):
            base = 8000.0
        else:
            target = stem or name
            fuzzy_values = [fuzz.WRatio(k, target) for k in keywords]
            fuzzy = sum(fuzzy_values) / max(1, len(fuzzy_values))
            if fuzzy >= self.fuzzy_threshold:
                base = 6200.0 + min(1400.0, (fuzzy - self.fuzzy_threshold) * 33.0)
            elif all(k in path for k in keywords):
                base = 4500.0
            elif any(k in path for k in keywords):
                base = 4200.0
            else:
                return 0.0

        now = time.time()
        modified = int(_get(row, "modified_time", 0) or 0)
        age_days = max(0.0, (now - modified) / 86400.0) if modified else 3650.0
        recent_bonus = max(0.0, 80.0 - 10.0 * math.log2(age_days + 1.0))
        depth_bonus = max(0.0, 20.0 - min(20.0, float(_get(row, "depth", 0) or 0)))
        short_bonus = max(0.0, 10.0 - min(10.0, len(_get(row, "name", "")) / 8.0))
        folder_bonus = 4.0 if _get(row, "is_directory", False) else 0.0

        # Behavior bonus is deliberately capped below a name-match tier gap.
        if self.behavior_ranking:
            favorite_bonus = 45.0 if bool(_get(row, "is_favorite", 0)) else 0.0
            open_count = int(_get(row, "open_count", 0) or 0)
            usage_bonus = min(50.0, 12.0 * math.log2(open_count + 1.0)) if open_count else 0.0
            last_opened = int(_get(row, "last_opened_time", 0) or 0)
            if last_opened:
                opened_age_days = max(0.0, (now - last_opened) / 86400.0)
                last_opened_bonus = max(0.0, 35.0 - 7.0 * math.log2(opened_age_days + 1.0))
            else:
                last_opened_bonus = 0.0
        else:
            favorite_bonus = usage_bonus = last_opened_bonus = 0.0

        return (
            base
            + recent_bonus
            + depth_bonus
            + short_bonus
            + folder_bonus
            + favorite_bonus
            + usage_bonus
            + last_opened_bonus
        )

    def rank(self, rows, keywords: list[str]) -> list[SearchResult]:
        results: list[SearchResult] = []
        for row in rows:
            score = self.score(row, keywords)
            if keywords and score <= 0:
                continue
            results.append(
                SearchResult(
                    id=int(_get(row, "id", 0)),
                    root_id=int(_get(row, "root_id", 0)),
                    name=_get(row, "name", ""),
                    full_path=_get(row, "full_path", ""),
                    parent_path=_get(row, "parent_path", ""),
                    extension=_get(row, "extension", ""),
                    is_directory=bool(_get(row, "is_directory", False)),
                    size=int(_get(row, "size", 0) or 0),
                    created_time=int(_get(row, "created_time", 0) or 0),
                    modified_time=int(_get(row, "modified_time", 0) or 0),
                    depth=int(_get(row, "depth", 0) or 0),
                    score=score,
                    is_favorite=bool(_get(row, "is_favorite", 0)),
                    open_count=int(_get(row, "open_count", 0) or 0),
                    last_opened_time=int(_get(row, "last_opened_time", 0) or 0),
                )
            )
        results.sort(key=lambda x: (x.score, x.modified_time), reverse=True)
        return results
