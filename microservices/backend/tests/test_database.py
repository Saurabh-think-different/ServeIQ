import sqlite3

import pytest

from phdata.db.database import SCHEMA_VERSION, initialize_schema, open_database


@pytest.fixture
def connection():
    connection = open_database(":memory:")
    connection.execute(
        "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
        "VALUES (?, ?, ?, ?, ?)",
        ("SW-0001", "ServeWell - Banjara Hills", "Hyderabad", "Banjara Hills", "corporate"),
    )
    yield connection
    connection.close()


def add_asset(connection, asset_id: str, store_id: str) -> None:
    connection.execute(
        "INSERT INTO assets (asset_id, store_id, asset_type, make, model) "
        "VALUES (?, ?, ?, ?, ?)",
        (asset_id, store_id, "Receipt Printer", "EpsonTM-T88VI", "EpsonTM-T88VI"),
    )


def add_incident(
    connection,
    ticket_id: str,
    *,
    reported_store_id: str = "SW-0001",
    store_id: str | None = "SW-0001",
    reported_asset_id: str | None = None,
    asset_id: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO incidents (
            ticket_id, reported_store_id, store_id, reported_asset_id, asset_id,
            opened_at, status, subject, description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ticket_id,
            reported_store_id,
            store_id,
            reported_asset_id,
            asset_id,
            "2026-10-19T12:00:00+05:30",
            "Open",
            "Printer offline",
            "Receipt printer is not responding.",
        ),
    )


def test_schema_initialization_is_idempotent(connection):
    initialize_schema(connection)
    table_count = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
    ).fetchone()[0]
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert table_count >= 7


def test_unresolved_reported_ids_are_preserved(connection):
    add_incident(
        connection,
        "INC-UNKNOWN-ASSET",
        reported_store_id="SW-9999",
        store_id=None,
        reported_asset_id="POS-9791-T1",
    )
    row = connection.execute(
        "SELECT reported_store_id, store_id, reported_asset_id, asset_id "
        "FROM incidents WHERE ticket_id = ?",
        ("INC-UNKNOWN-ASSET",),
    ).fetchone()
    assert row["reported_store_id"] == "SW-9999"
    assert row["store_id"] is None
    assert row["reported_asset_id"] == "POS-9791-T1"
    assert row["asset_id"] is None


def test_verified_asset_must_belong_to_resolved_store(connection):
    connection.execute(
        "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
        "VALUES (?, ?, ?, ?, ?)",
        ("SW-0002", "ServeWell - Kukatpally", "Hyderabad", "Kukatpally", "franchise"),
    )
    add_asset(connection, "PRN-0001-P1", "SW-0001")
    with pytest.raises(sqlite3.IntegrityError, match="verified asset must belong"):
        add_incident(
            connection,
            "INC-WRONG-STORE-ASSET",
            reported_store_id="SW-0002",
            store_id="SW-0002",
            reported_asset_id="PRN-0001-P1",
            asset_id="PRN-0001-P1",
        )


def test_unknown_resolved_foreign_keys_are_rejected(connection):
    with pytest.raises(sqlite3.IntegrityError):
        add_asset(connection, "PRN-9999-P1", "SW-9999")


def test_routing_outcome_is_constrained(connection):
    add_incident(connection, "INC-VALID")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO agent_runs (
                run_id, ticket_id, started_at, status, routing_outcome
            ) VALUES (?, ?, ?, ?, ?)""",
            ("RUN-1", "INC-VALID", "2026-10-19T12:01:00+05:30", "completed", "guess"),
        )