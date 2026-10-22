"""SQLite schema and connection helpers for the local ServeWell POC."""

import sqlite3
from pathlib import Path

from phdata.db.schema import MIGRATION_002_SQL, SCHEMA_SQL, SCHEMA_VERSION


def _configure_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")


def _execute_sql_script(connection: sqlite3.Connection, script: str) -> None:
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            sql = statement.strip()
            if sql:
                connection.execute(sql)
            statement = ""
    if statement.strip():
        raise sqlite3.OperationalError("incomplete SQL statement in schema script")


def initialize_schema(connection: sqlite3.Connection) -> None:
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if current_version > SCHEMA_VERSION:
        raise sqlite3.OperationalError(
            f"database schema version {current_version} is newer than supported version {SCHEMA_VERSION}"
        )
    if current_version == SCHEMA_VERSION:
        if not connection.in_transaction:
            _configure_connection(connection)
        return
    if connection.in_transaction:
        raise sqlite3.OperationalError("schema initialization cannot run inside a transaction")
    if current_version < 0:
        raise sqlite3.OperationalError(f"unsupported negative database schema version {current_version}")

    _configure_connection(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        # Another connection may have migrated while this one waited for the lock.
        current_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if current_version > SCHEMA_VERSION:
            raise sqlite3.OperationalError(
                f"database schema version {current_version} is newer than supported version {SCHEMA_VERSION}"
            )
        if current_version == 0:
            _execute_sql_script(connection, SCHEMA_SQL)
            _execute_sql_script(connection, MIGRATION_002_SQL)
        elif current_version == 1:
            _execute_sql_script(connection, MIGRATION_002_SQL)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
        raise


def open_database(database_path: str | Path) -> sqlite3.Connection:
    """Open an independently owned connection and initialize the current schema.

    FastAPI may run dependency setup, a handler, and cleanup on different worker
    threads. Callers must retain exclusive ownership, never use the connection
    concurrently, and close it when their request or unit of work finishes.
    """
    connection = sqlite3.connect(database_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    try:
        initialize_schema(connection)
    except sqlite3.Error:
        connection.close()
        raise
    return connection