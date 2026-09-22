from __future__ import annotations

from dataclasses import dataclass

from core.query_parser import QueryParser
from core.ranking_engine import RankingEngine
from database.database import Database
from models.search_result import SearchResult
from utils.file_utils import DOCUMENT_EXTENSIONS, IMAGE_EXTENSIONS, PROGRAM_EXTENSIONS


@dataclass(slots=True)
class SearchOptions:
    root_ids: list[int] | None = None
    item_type: str | None = None
    category: str = "all"
    after_time: int | None = None
    sort_by: str = "relevance"
    limit: int = 200
    candidate_limit: int = 3000


class SearchService:
    def __init__(self, database: Database, fuzzy_threshold: int = 58, behavior_ranking: bool = True):
        self.database = database
        self.parser = QueryParser()
        self.ranking = RankingEngine(fuzzy_threshold=fuzzy_threshold, behavior_ranking=behavior_ranking)

    @staticmethod
    def _category_extensions(category: str) -> list[str] | None:
        if category == "document":
            return sorted(DOCUMENT_EXTENSIONS)
        if category == "image":
            return sorted(IMAGE_EXTENSIONS)
        if category == "program":
            return sorted(PROGRAM_EXTENSIONS)
        return None

    @staticmethod
    def _to_result(row, score: float = 0.0) -> SearchResult:
        return SearchResult(
            id=row["id"],
            root_id=row["root_id"],
            name=row["name"],
            full_path=row["full_path"],
            parent_path=row["parent_path"],
            extension=row["extension"],
            is_directory=bool(row["is_directory"]),
            size=row["size"] or 0,
            created_time=row["created_time"] or 0,
            modified_time=row["modified_time"] or 0,
            depth=row["depth"] or 0,
            score=score,
            is_favorite=bool(row["is_favorite"]) if "is_favorite" in row.keys() else False,
            open_count=int(row["open_count"] or 0) if "open_count" in row.keys() else 0,
            last_opened_time=int(row["last_opened_time"] or 0) if "last_opened_time" in row.keys() else 0,
        )

    def search(self, text: str, options: SearchOptions) -> tuple[list[SearchResult], int]:
        parsed = self.parser.parse(text)
        item_type = parsed.type_filter or options.item_type
        category_extensions = self._category_extensions(options.category)
        after_time = parsed.after_time or options.after_time

        if not parsed.keywords:
            rows = self.database.recent_entries(
                root_ids=options.root_ids,
                item_type=item_type,
                category_extensions=category_extensions,
                after_time=after_time,
                limit=options.limit,
            )
            results = [self._to_result(r, 1000.0) for r in rows]
            return self._sort(results, options.sort_by)[: options.limit], len(results)

        rows = self.database.search_candidates(
            keywords=parsed.keywords,
            root_ids=options.root_ids,
            item_type=item_type,
            category_extensions=category_extensions,
            after_time=after_time,
            before_time=parsed.before_time,
            extensions=parsed.extensions,
            path_filter=parsed.path_filter,
            size_min=parsed.size_min,
            size_max=parsed.size_max,
            limit=options.candidate_limit,
        )
        ranked = self.ranking.rank(rows, parsed.keywords)
        ranked = self._sort(ranked, options.sort_by)
        return ranked[: options.limit], len(ranked)

    @staticmethod
    def _sort(results: list[SearchResult], sort_by: str) -> list[SearchResult]:
        if sort_by == "modified_desc":
            return sorted(results, key=lambda x: x.modified_time, reverse=True)
        if sort_by == "modified_asc":
            return sorted(results, key=lambda x: x.modified_time)
        if sort_by == "created_desc":
            return sorted(results, key=lambda x: x.created_time, reverse=True)
        if sort_by == "created_asc":
            return sorted(results, key=lambda x: x.created_time)
        if sort_by == "size_desc":
            return sorted(results, key=lambda x: x.size, reverse=True)
        if sort_by == "size_asc":
            return sorted(results, key=lambda x: x.size)
        if sort_by == "name_asc":
            return sorted(results, key=lambda x: x.name.casefold())
        if sort_by == "name_desc":
            return sorted(results, key=lambda x: x.name.casefold(), reverse=True)
        if sort_by == "type":
            return sorted(results, key=lambda x: (not x.is_directory, x.extension, x.name.casefold()))
        if sort_by == "path":
            return sorted(results, key=lambda x: x.full_path.casefold())
        return sorted(results, key=lambda x: (x.score, x.modified_time), reverse=True)
