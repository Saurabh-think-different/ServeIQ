import sqlite3
from importlib.resources import files

import pytest

from phdata.db.database import initialize_schema, open_database
from phdata.db.schema import MIGRATION_002_SQL, SCHEMA_SQL, SCHEMA_VERSION
from phdata.models.actions import AuditPayload
from phdata.repositories.actions import append_audit_event, list_audit_events


def make_v1_database():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA_SQL)
    connection.execute("PRAGMA user_version = 1")
    connection.execute(
        "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
        "VALUES ('S1', 'Store', 'City', 'Area', 'corporate')",
    )
    connection.execute(
        """INSERT INTO incidents (
            ticket_id, reported_store_id, store_id, opened_at, status, subject, description
        ) VALUES ('INC-V1', 'S1', 'S1', '2026-10-20T00:00:00+00:00', 'Open', 'Printer', 'Offline')""",
    )
    connection.execute(
        "INSERT INTO incident_events (ticket_id, source_event_index, event_at, actor, note) "
        "VALUES ('INC-V1', 0, '2026-10-20T00:01:00+00:00', 'Operator', 'Reported offline')",
    )
    connection.execute(
        """INSERT INTO agent_runs (
            run_id, ticket_id, started_at, status, decision_summary
        ) VALUES ('RUN-V1', 'INC-V1', '2026-10-20T00:02:00+00:00', 'completed', 'Legacy summary')""",
    )
    connection.execute(
        """INSERT INTO audit_events (
            ticket_id, run_id, actor_type, actor_id, event_type, occurred_at, payload_json
        ) VALUES ('INC-V1', 'RUN-V1', 'system', 'legacy', 'legacy_event',
                 '2026-10-20T00:02:00+00:00', '{}')""",
    )
    connection.commit()
    return connection


def test_fresh_database_applies_full_version_two_schema():
    connection = open_database(":memory:")
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_runs)")}
        assert {
            "idempotency_key", "request_fingerprint", "context_fingerprint",
            "context_snapshot_json", "error_code",
        } <= columns
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_proposals'",
        ).fetchone()
        assert SCHEMA_VERSION == 2
        initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        connection.close()


def test_version_one_migration_preserves_existing_records():
    connection = make_v1_database()
    try:
        initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT description FROM incidents WHERE ticket_id='INC-V1'").fetchone()[0] == "Offline"
        assert connection.execute("SELECT note FROM incident_events WHERE ticket_id='INC-V1'").fetchone()[0] == "Reported offline"
        assert connection.execute("SELECT decision_summary FROM agent_runs WHERE run_id='RUN-V1'").fetchone()[0] == "Legacy summary"
        assert connection.execute("SELECT event_type FROM audit_events WHERE ticket_id='INC-V1'").fetchone()[0] == "legacy_event"
    finally:
        connection.close()


def test_fresh_schema_failure_rolls_back_base_schema(monkeypatch):
    import phdata.db.database as database

    connection = sqlite3.connect(":memory:")
    monkeypatch.setattr(
        database,
        "MIGRATION_002_SQL",
        "CREATE TABLE temporary (id INTEGER);" + chr(10) + "INVALID SQL;",
    )
    with pytest.raises(sqlite3.OperationalError):
        initialize_schema(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
    connection.close()


def test_failed_migration_rolls_back_columns_and_version():
    connection = make_v1_database()
    connection.execute("CREATE TABLE action_proposals (action_id TEXT PRIMARY KEY)")
    connection.commit()
    try:
        with pytest.raises(sqlite3.OperationalError, match="already exists"):
            initialize_schema(connection)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_runs)")}
        assert "idempotency_key" not in columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        connection.close()


def test_future_version_is_refused_without_lowering_it():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA user_version = 3")
    with pytest.raises(sqlite3.OperationalError, match="newer than supported"):
        initialize_schema(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    connection.close()


def test_initialization_preserves_caller_owned_transactions():
    connection = open_database(":memory:")
    connection.execute("BEGIN")
    connection.execute(
        "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
        "VALUES ('TEMP', 'Store', 'City', 'Area', 'corporate')",
    )
    initialize_schema(connection)
    assert connection.in_transaction
    connection.rollback()
    assert connection.execute("SELECT COUNT(*) FROM stores WHERE store_id='TEMP'").fetchone()[0] == 0
    connection.close()

    legacy = make_v1_database()
    legacy.execute("BEGIN")
    with pytest.raises(sqlite3.OperationalError, match="cannot run inside a transaction"):
        initialize_schema(legacy)
    assert legacy.in_transaction
    legacy.rollback()
    legacy.close()


def test_packaged_schema_and_migration_resources_are_readable():
    assert files("phdata.db").joinpath("schema.sql").read_text(encoding="utf-8") == SCHEMA_SQL
    assert files("phdata.db.migrations").joinpath("002_runs_actions.sql").read_text(encoding="utf-8") == MIGRATION_002_SQL


def test_audit_events_allow_append_but_reject_update_and_delete():
    connection = open_database(":memory:")
    connection.execute(
        "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
        "VALUES ('S1', 'Store', 'City', 'Area', 'corporate')",
    )
    connection.execute(
        """INSERT INTO incidents (
            ticket_id, reported_store_id, store_id, opened_at, status, subject, description
        ) VALUES ('INC-AUDIT', 'S1', 'S1', '2026-10-20T00:00:00+00:00', 'Open', 'Printer', 'Offline')""",
    )
    event = append_audit_event(
        connection,
        ticket_id="INC-AUDIT",
        run_id=None,
        actor_type="system",
        actor_id=None,
        event_type="run_started",
        occurred_at="2026-10-20T00:01:00+00:00",
        approval_status="not_required",
        payload=AuditPayload(summary="Run accepted"),
    )
    assert event.audit_event_id == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("UPDATE audit_events SET actor_id='changed' WHERE audit_event_id=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM audit_events WHERE audit_event_id=1")
    page = list_audit_events(connection, ticket_id="INC-AUDIT")
    assert page.total == 1 and page.events[0].payload.summary == "Run accepted"
    connection.close()
