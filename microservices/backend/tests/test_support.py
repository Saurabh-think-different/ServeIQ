"""Exact, read-only structured context tests (all data is local and in-memory)."""

from datetime import datetime, timezone
import sqlite3

import pytest

from phdata.db.database import open_database
from phdata.models.context import IncidentContext
from phdata.repositories.support import SupportRepository

NOW = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)


def add_incident(db, ticket_id="INC-1", *, store="S1", asset="A1",
                 opened="2026-01-01T10:00:00+05:30", status="Open",
                 priority="P2", category="Printers"):
    db.execute("""
        INSERT INTO incidents (
            ticket_id, reported_store_id, reported_asset_id, opened_at, priority,
            category, status, subject, description, submitted_by, contact_phone,
            resolution_notes, tags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Offline', 'Printer offline',
                  'Private Name', 'Private Phone', 'Private resolution', '["private_label"]')
    """, (ticket_id, store, asset, opened, priority, category, status))


@pytest.fixture
def db():
    connection = open_database(":memory:")
    connection.executemany("""
        INSERT INTO stores VALUES (?, ?, 'City', 'Area', 'corporate', 'Manager', 'Phone')
    """, [("S1", "Store One"), ("S2", "Store Two")])
    connection.executemany("""
        INSERT INTO assets (asset_id, store_id, asset_type, make, model)
        VALUES (?, ?, 'Printer', 'Make', 'Model')
    """, [("A1", "S1"), ("A2", "S2")])
    connection.execute("INSERT INTO sla_targets VALUES ('P2', 'Printers', 30, 240, 90)")
    add_incident(connection)
    yield connection
    connection.close()


def context(db, **kwargs):
    result = SupportRepository(db).get_context("INC-1", now=kwargs.pop("now", NOW), **kwargs)
    assert result is not None
    return result


def test_exact_typed_lookups_and_safe_projection(db):
    repo = SupportRepository(db)
    assert repo.get_store("S1").store_name == "Store One"
    assert repo.get_asset("A1").store_id == "S1"
    assert repo.get_sla("P2", "Printers").first_response_minutes == 30
    result = context(db)
    assert isinstance(result, IncidentContext)
    assert result.store.store_id == "S1"
    assert result.asset.asset_id == "A1"
    serialized = result.model_dump_json()
    for private in ("submitted_by", "contact_phone", "region_manager", "support_phone",
                    "resolution_notes", "tags", "labels", "Private", "private_label"):
        assert private not in serialized
    assert IncidentContext.model_validate_json(serialized) == result


@pytest.mark.parametrize("value", ["missing", "s1", "S1 ", "S%", "' OR 1=1 --", ""])
def test_missing_exact_ids_cannot_match_by_pattern_or_injection(db, value):
    repo = SupportRepository(db)
    assert repo.get_store(value) is None
    assert repo.get_asset(value) is None
    assert repo.get_context(value, now=NOW) is None
    assert repo.get_history(value) == []
    assert repo.get_related(value) == []


@pytest.mark.parametrize("store, asset, expected_store, expected_asset", [
    ("unknown", "A1", None, None),
    ("S1", "unknown", "S1", None),
    ("S1", "A2", "S1", None),
    ("", "A1", None, None),
    ("   ", "A1", None, None),
    ("S1", None, "S1", None),
    ("S1", "", "S1", None),
])
def test_reference_missing_and_mismatch_fail_closed(db, store, asset, expected_store, expected_asset):
    db.execute("UPDATE incidents SET reported_store_id=?, reported_asset_id=?", (store, asset))
    result = context(db)
    assert (result.store.store_id if result.store else None) == expected_store
    assert (result.asset.asset_id if result.asset else None) == expected_asset
    assert "asset" in result.missing
    assert result.warnings
    if not store.strip():
        assert result.incident.reported_store_id is None


def test_reference_arrivals_resolve_without_changing_persisted_foreign_keys(db):
    db.execute("UPDATE incidents SET reported_store_id='late-store', reported_asset_id='late-asset'")
    assert context(db).store is None
    db.execute("INSERT INTO stores VALUES ('late-store', 'Late', 'City', 'Area', 'franchise', NULL, NULL)")
    db.execute("INSERT INTO assets (asset_id, store_id, asset_type, make, model) VALUES ('late-asset', 'late-store', 'Printer', 'Make', 'Model')")
    result = context(db)
    assert result.store.store_id == "late-store"
    assert result.asset.asset_id == "late-asset"
    assert tuple(db.execute("SELECT store_id, asset_id FROM incidents").fetchone()) == (None, None)


def test_persisted_foreign_keys_never_substitute_for_reported_ids(db):
    db.execute("UPDATE incidents SET store_id='S1', asset_id='A1', reported_store_id='missing', reported_asset_id='missing'")
    result = context(db)
    assert result.store is None and result.asset is None


@pytest.mark.parametrize("priority, category", [("P1", "Printers"), ("P2", "printers"),
                                               ("P2", "Printers "), (None, None),
                                               ("P2", "UnknownCategory"), ("P2", "")])
def test_unknown_sla_never_guessed(db, priority, category):
    db.execute("UPDATE incidents SET priority=?, category=?", (priority, category))
    result = context(db)
    assert result.sla is None
    assert result.incident.category == category
    assert result.clock.open_target_deadlines is None
    assert "sla" in result.missing
    assert SupportRepository(db).get_sla("P2", "' OR 1=1 --") is None


def test_two_pass_related_resolution_preserves_unresolved_and_persisted_state(db):
    add_incident(db, "INC-2")
    db.executemany("INSERT INTO incident_relations VALUES ('INC-1', ?, ?, 'related')", [
        ("INC-LATE", None), ("INC-2", "INC-2"), ("INC-MISSING", None),
    ])
    first = context(db)
    assert [(r.reported_related_ticket_id, r.related_ticket_id) for r in first.related] == [
        ("INC-2", "INC-2"), ("INC-LATE", None), ("INC-MISSING", None),
    ]
    add_incident(db, "INC-LATE")
    second = context(db)
    late = second.related[1]
    assert late.related_ticket_id == "INC-LATE"
    assert late.persisted_related_ticket_id is None
    assert second.related[2].related_ticket_id is None
    assert any("read-time" in item for item in second.warnings)
    db.execute("UPDATE incident_relations SET related_ticket_id='INC-LATE' WHERE reported_related_ticket_id='INC-LATE'")
    third = context(db)
    assert third.related[1].persisted_related_ticket_id == "INC-LATE"
    assert not any("read-time" in item for item in third.warnings)


def test_wrong_persisted_related_id_not_exposed_as_resolved(db):
    add_incident(db, "INC-OTHER")
    db.execute("INSERT INTO incident_relations VALUES ('INC-1', 'INC-ABSENT', 'INC-OTHER', 'related')")
    related = context(db).related[0]
    assert related.related_ticket_id is None
    assert related.persisted_related_ticket_id == "INC-OTHER"


def test_history_sorted_by_aware_instant_with_source_ties_and_invalid_last(db):
    events = [
        (4, "2026-01-01T10:00:00+00:00"),
        (2, "2026-01-01T10:00:00+05:30"),
        (1, "2026-01-01T04:30:00Z"),
        (0, "not-a-date"),
        (3, "2026-01-01T04:00:00"),
    ]
    db.executemany("""
        INSERT INTO incident_events (ticket_id, source_event_index, event_at, actor, note)
        VALUES ('INC-1', ?, ?, 'L1', 'Untyped note')
    """, events)
    result = context(db)
    assert [event.source_event_index for event in result.history] == [1, 2, 4, 0, 3]
    assert context(db).history == result.history
    assert len([w for w in result.warnings if "History event" in w]) == 2


def test_offset_clock_deadlines_fixed_now_and_no_inferred_first_response_breach(db):
    result = context(db)
    clock = result.clock
    assert clock.now == NOW
    assert clock.elapsed_since_open_minutes == 90
    assert clock.open_target_deadlines.first_response_at.isoformat() == "2026-01-01T10:30:00+05:30"
    assert clock.open_target_deadlines.resolution_at.isoformat() == "2026-01-01T14:00:00+05:30"
    assert clock.open_target_deadlines.escalation_to_l2_at.isoformat() == "2026-01-01T11:30:00+05:30"
    assert clock.first_response_fulfillment == "unknown"
    assert "do not establish" in clock.fulfillment_note
    assert "overdue" not in clock.model_dump(exclude={"fulfillment_note"})


@pytest.mark.parametrize("status", ["Closed", "Resolved", "Cancelled", "Canceled"])
def test_closed_has_no_invented_open_deadlines_or_breach(db, status):
    db.execute("UPDATE incidents SET status=?", (status,))
    clock = context(db).clock
    assert clock.status_kind == "closed"
    assert clock.open_target_deadlines is None
    assert clock.first_response_fulfillment == "unknown"


@pytest.mark.parametrize("opened", ["invalid", "2026-01-01T10:00:00", "2026-01-01", "0001-01-01T00:00:00+05:30"])
def test_naive_or_malformed_opened_at_fails_safe_explicitly(db, opened):
    db.execute("UPDATE incidents SET opened_at=?", (opened,))
    result = context(db)
    assert result.clock.opened_at is None
    assert result.clock.elapsed_since_open_minutes is None
    assert result.clock.open_target_deadlines is None
    assert "valid_opened_at" in result.missing
    assert any("opened_at" in w for w in result.warnings)


def test_naive_now_not_assumed_utc(db):
    result = context(db, now=datetime(2026, 1, 1, 6))
    assert result.clock.now is None
    assert result.clock.elapsed_since_open_minutes is None
    assert "valid_now" in result.missing
    assert any("now is naive" in warning for warning in result.warnings)


def test_future_opened_at_and_unknown_status_explicit(db):
    db.execute("UPDATE incidents SET status='Mystery', opened_at='2027-01-01T00:00:00Z'")
    result = context(db)
    assert result.clock.status_kind == "unknown"
    assert result.clock.elapsed_since_open_minutes is None
    assert result.clock.open_target_deadlines is None
    assert any("future" in warning for warning in result.warnings)
    assert any("not recognized" in warning for warning in result.warnings)


def test_default_clock_is_aware_and_plain_sqlite_connections_supported(db):
    db.row_factory = None
    repo = SupportRepository(db)
    result = repo.get_context("INC-1")
    assert result.clock.now.utcoffset() is not None
    assert repo.get_store("S1").store_id == "S1"
    assert isinstance(db, sqlite3.Connection)


def test_deadline_overflow_fails_safe(db):
    db.execute("UPDATE sla_targets SET resolution_target_minutes=9223372036854775807")
    result = context(db)
    assert result.clock.open_target_deadlines is None
    assert any("supported datetime range" in warning for warning in result.warnings)
