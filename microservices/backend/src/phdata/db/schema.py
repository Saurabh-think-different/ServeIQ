"""Database schema version and packaged SQL definition."""

from importlib.resources import files

SCHEMA_VERSION = 2
SCHEMA_SQL = files("phdata.db").joinpath("schema.sql").read_text(encoding="utf-8")
MIGRATION_002_SQL = (
    files("phdata.db.migrations")
    .joinpath("002_runs_actions.sql")
    .read_text(encoding="utf-8")
)