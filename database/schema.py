SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS search_roots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    path_norm TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    item_count INTEGER NOT NULL DEFAULT 0,
    last_scan_time INTEGER,
    last_scan_duration REAL DEFAULT 0,
    next_scan_time INTEGER,
    update_interval_minutes INTEGER
);

CREATE TABLE IF NOT EXISTS file_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    stem TEXT NOT NULL DEFAULT '',
    stem_norm TEXT NOT NULL DEFAULT '',
    extension TEXT NOT NULL DEFAULT '',
    full_path TEXT NOT NULL UNIQUE,
    path_norm TEXT NOT NULL,
    parent_path TEXT NOT NULL,
    is_directory INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    created_time INTEGER,
    modified_time INTEGER,
    depth INTEGER NOT NULL DEFAULT 0,
    indexed_time INTEGER NOT NULL,
    FOREIGN KEY(root_id) REFERENCES search_roots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL UNIQUE,
    use_count INTEGER NOT NULL DEFAULT 1,
    last_used_time INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_path TEXT NOT NULL,
    path_norm TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    is_directory INTEGER NOT NULL DEFAULT 0,
    created_time INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS file_usage (
    path_norm TEXT PRIMARY KEY,
    full_path TEXT NOT NULL,
    open_count INTEGER NOT NULL DEFAULT 0,
    last_opened_time INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_entries_name ON file_entries(name_norm);
CREATE INDEX IF NOT EXISTS idx_entries_stem ON file_entries(stem_norm);
CREATE INDEX IF NOT EXISTS idx_entries_extension ON file_entries(extension);
CREATE INDEX IF NOT EXISTS idx_entries_type ON file_entries(is_directory);
CREATE INDEX IF NOT EXISTS idx_entries_modified ON file_entries(modified_time DESC);
CREATE INDEX IF NOT EXISTS idx_entries_created ON file_entries(created_time DESC);
CREATE INDEX IF NOT EXISTS idx_entries_size ON file_entries(size DESC);
CREATE INDEX IF NOT EXISTS idx_entries_root ON file_entries(root_id);
CREATE INDEX IF NOT EXISTS idx_entries_path_norm ON file_entries(path_norm);
CREATE INDEX IF NOT EXISTS idx_history_last_used ON search_history(last_used_time DESC);
CREATE INDEX IF NOT EXISTS idx_favorites_created ON favorites(created_time DESC);
CREATE INDEX IF NOT EXISTS idx_usage_last_opened ON file_usage(last_opened_time DESC);
CREATE INDEX IF NOT EXISTS idx_usage_open_count ON file_usage(open_count DESC);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS file_search USING fts5(
    name_norm,
    stem_norm,
    path_norm,
    content='file_entries',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS file_entries_ai AFTER INSERT ON file_entries BEGIN
    INSERT INTO file_search(rowid, name_norm, stem_norm, path_norm)
    VALUES (new.id, new.name_norm, new.stem_norm, new.path_norm);
END;

CREATE TRIGGER IF NOT EXISTS file_entries_ad AFTER DELETE ON file_entries BEGIN
    INSERT INTO file_search(file_search, rowid, name_norm, stem_norm, path_norm)
    VALUES('delete', old.id, old.name_norm, old.stem_norm, old.path_norm);
END;

CREATE TRIGGER IF NOT EXISTS file_entries_au AFTER UPDATE ON file_entries BEGIN
    INSERT INTO file_search(file_search, rowid, name_norm, stem_norm, path_norm)
    VALUES('delete', old.id, old.name_norm, old.stem_norm, old.path_norm);
    INSERT INTO file_search(rowid, name_norm, stem_norm, path_norm)
    VALUES (new.id, new.name_norm, new.stem_norm, new.path_norm);
END;
"""
