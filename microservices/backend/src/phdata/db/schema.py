"""Database schema version and packaged SQL definition."""

from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA_SQL = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")