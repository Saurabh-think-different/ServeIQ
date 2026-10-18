"""SQLite schema and connection helpers for the local ServeWell POC."""

import sqlite3
from pathlib import Path

from phdata.db.schema import SCHEMA_SQL, SCHEMA_VERSION


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_SQL)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def open_database(database_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite database and initialize the current schema."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    return connection