from __future__ import annotations

import os
from database.database import Database


class IndexManager:
    def __init__(self, database: Database):
        self.database = database

    def add_root(self, path: str) -> int:
        if not os.path.isdir(path):
            raise ValueError("目录不存在")
        return self.database.add_search_root(path)

    def remove_root(self, root_id: int) -> None:
        self.database.remove_search_root(root_id)

    def clear_root_archive(self, root_id: int, pause_root: bool = True) -> None:
        self.database.clear_root_archive(root_id, pause_root=pause_root)

    def list_roots(self):
        return self.database.list_search_roots()

    def set_enabled(self, root_id: int, enabled: bool) -> None:
        self.database.set_root_enabled(root_id, enabled)
