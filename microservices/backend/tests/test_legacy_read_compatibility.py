"""Migrated v1 history remains readable without fabricating v2 decisions."""

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from phdata.db.database import initialize_schema, open_database
from phdata.main import app
from phdata.models.actions import AuditPayload
from phdata.models.legacy import LegacyAuditEvent, LegacyRunResult
from phdata.models.runs import RunResult
from phdata.repositories.actions import append_audit_event, list_audit_events
from phdata.repositories.runs import finalize_run, get_run, insert_run, list_runs
from test_migrations import make_v1_database
from test_reliability_regressions import AUTH, database_path


@pytest.fixture
def legacy():
    connection = make_v1_database()
    initialize_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


def test_legacy_reads_preserve_stored_facts_and_leave_database_unchanged(legacy):
    before = tuple(legacy.iterdump())
    result = get_run(legacy, "RUN-V1")
    assert isinstance(result, LegacyRunResult)
    assert result.representation == "legacy_v1"
    assert result.status == "completed" and result.completed_at is None
    assert result.routing_outcome is None
    assert result.decision_summary == "Legacy summary"
    assert result.evidence_json == "[]" and result.response_json is None
    assert not {"steps", "actions", "decision_mechanism", "error_code"} & result.model_dump().keys()
    assert list_runs(legacy, ticket_id="INC-V1").runs == [result]
    audit = list_audit_events(legacy, ticket_id="INC-V1")
    assert audit.total == 1 and isinstance(audit.events[0], LegacyAuditEvent)
    assert audit.events[0].event_type == "legacy_event"
    assert audit.events[0].payload == {}
    assert "action_id" not in audit.events[0].model_dump()
    assert tuple(legacy.iterdump()) == before


@pytest.mark.parametrize("status", ["running", "completed", "failed"])
def test_legacy_states_do_not_require_invented_completion_or_errors(legacy, status):
    legacy.execute("UPDATE agent_runs SET status=? WHERE run_id='RUN-V1'", (status,))
    result = get_run(legacy, "RUN-V1")
    assert isinstance(result, LegacyRunResult) and result.status == status
    assert result.completed_at is None


def test_untyped_legacy_response_is_preserved_not_recast_as_a_reviewed_decision(legacy):
    response = '{"answer":"old free-form output", "unknown":true}'
    legacy.execute("UPDATE agent_runs SET response_json=? WHERE run_id='RUN-V1'", (response,))
    result = get_run(legacy, "RUN-V1")
    assert isinstance(result, LegacyRunResult) and result.response_json == response


def test_legacy_run_cannot_be_finalized_as_a_modern_decision(legacy):
    legacy.execute("UPDATE agent_runs SET completed_at='2026-10-20T00:03:00+00:00' WHERE run_id='RUN-V1'")
    result = get_run(legacy, "RUN-V1")
    with pytest.raises(ValueError, match="typed result"):
        finalize_run(legacy, result, context_fingerprint="a" * 64, context_snapshot_json='{"version":1}')


def test_modern_run_missing_or_invalid_snapshot_is_not_downgraded_to_legacy(legacy):
    insert_run(legacy, run_id="MODERN", ticket_id="INC-V1", started_at="2026-10-20T01:00:00+00:00",
               idempotency_key="modern-key", request_fingerprint="a" * 64)
    assert isinstance(get_run(legacy, "MODERN"), RunResult)
    legacy.execute("UPDATE agent_runs SET response_json=NULL WHERE run_id='MODERN'")
    with pytest.raises(ValueError, match="typed response snapshot"):
        get_run(legacy, "MODERN")
    legacy.execute("UPDATE agent_runs SET response_json='{}' WHERE run_id='MODERN'")
    with pytest.raises(ValidationError):
        get_run(legacy, "MODERN")


def test_mixed_legacy_and_modern_audit_payloads_remain_distinct(legacy):
    append_audit_event(legacy, ticket_id="INC-V1", run_id="RUN-V1", actor_type="system", actor_id=None,
                       event_type="run_started", occurred_at="2026-10-20T00:03:00+00:00",
                       approval_status="not_required", payload=AuditPayload(summary="Modern event"))
    page = list_audit_events(legacy, ticket_id="INC-V1")
    assert page.total == 2
    assert isinstance(page.events[0], LegacyAuditEvent)
    assert page.events[1].payload.summary == "Modern event"
    assert list_audit_events(legacy, ticket_id="INC-V1", limit=1, offset=1).events == page.events[1:]


def test_invalid_versioned_audit_payload_does_not_fall_back_to_legacy(legacy):
    legacy.execute("INSERT INTO audit_events (ticket_id,actor_type,event_type,occurred_at,payload_json) "
                   "VALUES ('INC-V1','system','run_started','2026-10-20T01:00:00+00:00','{\"version\":1}')")
    with pytest.raises(ValidationError):
        list_audit_events(legacy, ticket_id="INC-V1")


def test_migrated_history_is_readable_through_authenticated_http(database_path):
    source = make_v1_database()
    target = open_database(database_path)
    # Backup the v1 fixture so application startup must perform the real migration.
    source.backup(target)
    source.close()
    target.close()
    with TestClient(app) as client:
        detail = client.get("/api/runs/RUN-V1", headers=AUTH)
        assert detail.status_code == 200
        assert detail.json()["representation"] == "legacy_v1"
        assert detail.json()["routing_outcome"] is None
        assert detail.json()["completed_at"] is None
        page = client.get("/api/incidents/INC-V1/runs", headers=AUTH)
        assert page.status_code == 200 and page.json()["runs"] == [detail.json()]
        audit = client.get("/api/incidents/INC-V1/audit", headers=AUTH)
        assert audit.status_code == 200
        assert audit.json()["events"][0]["representation"] == "legacy_v1"
        assert audit.json()["events"][0]["payload"] == {}
        actions = client.get("/api/runs/RUN-V1/actions", headers=AUTH)
        assert actions.status_code == 200 and actions.json()["actions"] == []
