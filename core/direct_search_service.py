from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

from rapidfuzz import fuzz, process

from core.query_parser import QueryParser
from core.ranking_engine import RankingEngine
from core.search_service import SearchOptions, SearchService
from models.search_result import SearchResult
from utils.file_utils import DOCUMENT_EXTENSIONS, IMAGE_EXTENSIONS, PROGRAM_EXTENSIONS
from utils.path_utils import normalize_path, path_depth


@dataclass(slots=True)
class DirectEntry:
    """仅存在于当前进程内存中的临时目录条目，不写入 SQLite。"""

    id: int
    root_id: int
    name: str
    name_norm: str
    stem: str
    stem_norm: str
    extension: str
    full_path: str
    path_norm: str
    parent_path: str
    is_directory: bool
    size: int
    created_time: int
    modified_time: int
    depth: int


class DirectDirectoryScanner:
    """快速扫描指定目录并建立一次性的会话内存快照。"""

    def __init__(self, excluded_dirs: list[str] | None = None):
        self.excluded_dirs = {x.casefold() for x in (excluded_dirs or [])}

    def scan(
        self,
        root_path: str,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> tuple[list[DirectEntry], dict]:
        root_path = os.path.abspath(os.path.normpath(root_path))
        if not os.path.isdir(root_path):
            raise ValueError("指定目录不存在")

        started = time.perf_counter()
        entries: list[DirectEntry] = []
        stack = [root_path]
        files = 0
        folders = 0
        last_emit = 0.0
        next_id = -1

        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as iterator:
                    for item in iterator:
                        try:
                            is_dir = item.is_dir(follow_symlinks=False)
                            if is_dir and item.name.casefold() in self.excluded_dirs:
                                continue
                            if not is_dir and not item.is_file(follow_symlinks=False):
                                continue

                            full_path = os.path.abspath(item.path)
                            name = item.name
                            stem = name if is_dir else os.path.splitext(name)[0]
                            extension = "" if is_dir else os.path.splitext(name)[1].casefold()

                            try:
                                stat = item.stat(follow_symlinks=False)
                                created = int(stat.st_ctime)
                                modified = int(stat.st_mtime)
                                size = 0 if is_dir else int(stat.st_size)
                            except OSError:
                                created = 0
                                modified = 0
                                size = 0

                            entries.append(
                                DirectEntry(
                                    id=next_id,
                                    root_id=0,
                                    name=name,
                                    name_norm=name.casefold(),
                                    stem=stem,
                                    stem_norm=stem.casefold(),
                                    extension=extension,
                                    full_path=full_path,
                                    path_norm=normalize_path(full_path),
                                    parent_path=os.path.dirname(full_path),
                                    is_directory=is_dir,
                                    size=size,
                                    created_time=created,
                                    modified_time=modified,
                                    depth=path_depth(full_path),
                                )
                            )
                            next_id -= 1

                            if is_dir:
                                folders += 1
                                stack.append(full_path)
                            else:
                                files += 1

                            now = time.perf_counter()
                            if progress_callback and now - last_emit >= 0.18:
                                elapsed = max(0.001, now - started)
                                progress_callback(
                                    {
                                        "files": files,
                                        "folders": folders,
                                        "items": files + folders,
                                        "elapsed": elapsed,
                                        "speed": int((files + folders) / elapsed),
                                        "current": full_path,
                                    }
                                )
                                last_emit = now
                        except (PermissionError, FileNotFoundError, OSError):
                            continue
            except (PermissionError, FileNotFoundError, OSError):
                continue

        elapsed = time.perf_counter() - started
        stats = {
            "files": files,
            "folders": folders,
            "items": files + folders,
            "elapsed": elapsed,
            "speed": int((files + folders) / max(elapsed, 0.001)),
        }
        return entries, stats


class DirectSearchService:
    """在临时目录的内存快照上执行实时搜索，不访问/修改 SQLite。"""

    def __init__(self, entries: list[DirectEntry], fuzzy_threshold: int = 58):
        self.entries = entries
        self.parser = QueryParser()
        self.ranking = RankingEngine(fuzzy_threshold=fuzzy_threshold)
        self.fuzzy_threshold = fuzzy_threshold

    @staticmethod
    def _category_extensions(category: str) -> set[str] | None:
        if category == "document":
            return DOCUMENT_EXTENSIONS
        if category == "image":
            return IMAGE_EXTENSIONS
        if category == "program":
            return PROGRAM_EXTENSIONS
        return None

    @staticmethod
    def _as_result(entry: DirectEntry, score: float = 0.0) -> SearchResult:
        return SearchResult(
            id=entry.id,
            root_id=0,
            name=entry.name,
            full_path=entry.full_path,
            parent_path=entry.parent_path,
            extension=entry.extension,
            is_directory=entry.is_directory,
            size=entry.size,
            created_time=entry.created_time,
            modified_time=entry.modified_time,
            depth=entry.depth,
            score=score,
            is_favorite=False,
            open_count=0,
            last_opened_time=0,
        )

    def _filter_entries(self, parsed, options: SearchOptions) -> list[DirectEntry]:
        item_type = parsed.type_filter or options.item_type
        category_extensions = self._category_extensions(options.category)
        after_time = parsed.after_time or options.after_time
        extensions = {
            (e if e.startswith(".") else "." + e).casefold()
            for e in parsed.extensions
        }
        path_filter = parsed.path_filter.casefold() if parsed.path_filter else None

        result: list[DirectEntry] = []
        for entry in self.entries:
            if item_type == "file" and entry.is_directory:
                continue
            if item_type == "folder" and not entry.is_directory:
                continue
            if category_extensions is not None and entry.extension not in category_extensions:
                continue
            if extensions and entry.extension not in extensions:
                continue
            if after_time and entry.modified_time < after_time:
                continue
            if parsed.before_time and entry.modified_time > parsed.before_time:
                continue
            if path_filter and path_filter not in entry.path_norm:
                continue
            if parsed.size_min is not None and entry.size < parsed.size_min:
                continue
            if parsed.size_max is not None and entry.size > parsed.size_max:
                continue
            result.append(entry)
        return result

    def _candidate_entries(
        self,
        entries: list[DirectEntry],
        keywords: list[str],
        limit: int,
    ) -> list[DirectEntry]:
        if not keywords:
            return entries[:limit]

        direct: list[DirectEntry] = []
        seen: set[int] = set()

        # 先做极快的包含/前缀筛选。绝大多数正常查询都在这里完成。
        for entry in entries:
            text_name = entry.name_norm
            text_stem = entry.stem_norm
            text_path = entry.path_norm
            if all(k in text_name or k in text_stem or k in text_path for k in keywords):
                direct.append(entry)
                seen.add(entry.id)
                if len(direct) >= limit:
                    return direct

        for entry in entries:
            if entry.id in seen:
                continue
            if any(k in entry.name_norm or k in entry.stem_norm for k in keywords):
                direct.append(entry)
                seen.add(entry.id)
                if len(direct) >= limit:
                    return direct

        # 如果普通匹配太少，再用 RapidFuzz C++ 实现做拼写容错候选。
        # 单字符查询不做模糊，避免无意义的大范围计算。
        joined = " ".join(keywords).strip()
        if len(joined) >= 2 and len(direct) < limit:
            remaining = [e for e in entries if e.id not in seen]
            choices = [e.stem_norm or e.name_norm for e in remaining]
            need = min(limit - len(direct), 1200)
            if choices and need > 0:
                matches = process.extract(
                    joined,
                    choices,
                    scorer=fuzz.WRatio,
                    score_cutoff=self.fuzzy_threshold,
                    limit=need,
                )
                for _, _, idx in matches:
                    entry = remaining[idx]
                    if entry.id not in seen:
                        direct.append(entry)
                        seen.add(entry.id)
                        if len(direct) >= limit:
                            break

        return direct

    def search(self, text: str, options: SearchOptions) -> tuple[list[SearchResult], int]:
        parsed = self.parser.parse(text)
        filtered = self._filter_entries(parsed, options)

        if not parsed.keywords:
            results = [self._as_result(e, 1000.0) for e in filtered]
            results = SearchService._sort(results, options.sort_by)
            return results[: options.limit], len(filtered)

        candidates = self._candidate_entries(filtered, parsed.keywords, options.candidate_limit)
        ranked = self.ranking.rank(candidates, parsed.keywords)
        ranked = SearchService._sort(ranked, options.sort_by)
        return ranked[: options.limit], len(ranked)
