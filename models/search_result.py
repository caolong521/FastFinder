from dataclasses import dataclass


@dataclass(slots=True)
class SearchResult:
    id: int
    root_id: int
    name: str
    full_path: str
    parent_path: str
    extension: str
    is_directory: bool
    size: int
    created_time: int
    modified_time: int
    depth: int
    score: float = 0.0
    is_favorite: bool = False
    open_count: int = 0
    last_opened_time: int = 0
