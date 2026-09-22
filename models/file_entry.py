from dataclasses import dataclass


@dataclass(slots=True)
class FileEntry:
    id: int | None
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
    indexed_time: int
