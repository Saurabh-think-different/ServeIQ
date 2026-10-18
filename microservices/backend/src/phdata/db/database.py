"""SQLite schema and connection helpers for the local ServeWell POC."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA_SQL = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create the current schema on an open connection; safe to call repeatedly."""
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_SQL)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def open_database(database_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite database and initialize the current schema."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    return connection