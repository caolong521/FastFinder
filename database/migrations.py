from __future__ import annotations


def _columns(connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def run_migrations(connection) -> None:
    """Small, idempotent schema upgrades for existing FastFinder databases."""
    root_columns = _columns(connection, "search_roots")

    if "next_scan_time" not in root_columns:
        connection.execute("ALTER TABLE search_roots ADD COLUMN next_scan_time INTEGER")
    if "update_interval_minutes" not in root_columns:
        connection.execute("ALTER TABLE search_roots ADD COLUMN update_interval_minutes INTEGER")

    connection.execute("PRAGMA user_version=3")
