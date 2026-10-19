from datetime import datetime, timezone
import json

import pytest

from phdata.db.database import open_database
from phdata.models.actions import (
    ActionDecisionRequest,
    ActionProposal,
    EscalationPreview,
)
from phdata.models.runs import RunResult
from phdata.repositories.actions import create_proposal, proposal_hash_for
from phdata.repositories.runs import finalize_run, insert_run
from phdata.repositories.support import SupportRepository
from phdata.services.actions import ActionConflict, ActionNotFound, SupportActions
from phdata.services.workflow import build_context_snapshot, context_fingerprint

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def seed_action(path, *, run_status="completed", changed=False):
    connection = open_database(path)
    connection.execute(
        "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
        "VALUES ('S1', 'Store', 'City', 'Area', 'corporate')",
    )
    connection.execute(
        """INSERT INTO incidents (
            ticket_id, reported_store_id, store_id, opened_at, status, subject, description
        ) VALUES ('INC-1', 'S1', 'S1', ?, 'Open', 'Printer', ?)""",
        (NOW.isoformat(), "Changed details" if changed else "Reported printer offline"),
    )
    context = SupportRepository(connection).get_context("INC-1", now=NOW)
    assert context is not None
    snapshot = build_context_snapshot(context)
    fingerprint = context_fingerprint(snapshot)
    running = insert_run(
        connection,
        run_id="RUN-1",
        ticket_id="INC-1",
        started_at=NOW.isoformat(),
        idempotency_key="run-key",
        request_fingerprint="a" * 64,
    )
    result = RunResult(
        run_id=running.run_id,
        ticket_id=running.ticket_id,
        status=run_status,
        started_at=NOW,
        completed_at=NOW,
        routing_outcome="l2_escalation" if run_status == "completed" else None,
        decision_mechanism="deterministic" if run_status == "completed" else None,
        error_code="provider_unavailable" if run_status == "failed" else None,
        error_message="Analysis unavailable." if run_status == "failed" else None,
    )
    finalize_run(
        connection,
        result,
        context_fingerprint=fingerprint,
        context_snapshot_json=json.dumps(snapshot),
    )
    preview = EscalationPreview(summary="Human support review is required.", reason="No approved guidance matched.")
    candidate = ActionProposal(
        action_id="ACT-1",
        ticket_id="INC-1",
        run_id="RUN-1",
        action_type="simulate_escalation",
        preview=preview,
        proposal_hash="0" * 64,
        context_fingerprint=fingerprint,
        status="pending",
        created_at=NOW,
    )
    proposal = candidate.model_copy(update={"proposal_hash": proposal_hash_for(candidate)})
    create_proposal(connection, proposal)
    connection.commit()
    connection.close()
    return proposal


def action_service(path):
    return SupportActions(lambda: open_database(path), clock=lambda: NOW)


def test_approval_is_atomic_and_duplicate_replay_has_no_extra_audit(tmp_path):
    path = tmp_path / "actions.db"
    proposal = seed_action(path)
    service = action_service(path)

    result, replayed = service.decide(proposal.action_id, ActionDecisionRequest(decision="approve"))
    assert not replayed and result.status == "completed"
    assert result.decided_by == "local_operator"
    assert "no notification" in result.result_summary

    connection = open_database(path)
    rows = connection.execute(
        "SELECT event_type, actor_type, actor_id FROM audit_events ORDER BY audit_event_id",
    ).fetchall()
    assert [row["event_type"] for row in rows] == ["proposal_approved", "simulation_completed"]
    assert rows[0]["actor_type"] == "human" and rows[0]["actor_id"] == "local_operator"
    assert connection.execute("SELECT status FROM action_proposals WHERE action_id='ACT-1'").fetchone()[0] == "completed"
    assert connection.execute("SELECT description FROM incidents WHERE ticket_id='INC-1'").fetchone()[0] == "Reported printer offline"
    connection.close()

    replay, was_replayed = service.decide(proposal.action_id, ActionDecisionRequest(decision="approve"))
    assert was_replayed and replay == result
    connection = open_database(path)
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 2
    connection.close()


def test_rejection_has_no_simulation_and_contradictory_decision_conflicts(tmp_path):
    path = tmp_path / "actions.db"
    proposal = seed_action(path)
    service = action_service(path)
    result, replayed = service.decide(proposal.action_id, ActionDecisionRequest(decision="reject"))
    assert not replayed and result.status == "rejected"
    assert "No local simulation" in result.result_summary
    with pytest.raises(ActionConflict):
        service.decide(proposal.action_id, ActionDecisionRequest(decision="approve"))
    replay, was_replayed = service.decide(proposal.action_id, ActionDecisionRequest(decision="reject"))
    assert was_replayed and replay == result

    connection = open_database(path)
    assert [row[0] for row in connection.execute("SELECT event_type FROM audit_events")] == ["proposal_rejected"]
    connection.close()


def test_stale_context_and_failed_run_are_rejected_without_effects(tmp_path):
    path = tmp_path / "actions.db"
    proposal = seed_action(path)
    connection = open_database(path)
    connection.execute("UPDATE incidents SET description='Changed after proposal' WHERE ticket_id='INC-1'")
    connection.commit()
    connection.close()
    with pytest.raises(ActionConflict):
        action_service(path).decide(proposal.action_id, ActionDecisionRequest(decision="approve"))

    failed_path = tmp_path / "failed.db"
    failed = seed_action(failed_path, run_status="failed")
    with pytest.raises(ActionConflict):
        action_service(failed_path).decide(failed.action_id, ActionDecisionRequest(decision="approve"))
    connection = open_database(failed_path)
    assert connection.execute("SELECT status FROM action_proposals").fetchone()[0] == "pending"
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    connection.close()


def test_simulation_failure_rolls_back_approval_and_audit(tmp_path, monkeypatch):
    path = tmp_path / "actions.db"
    proposal = seed_action(path)
    service = action_service(path)

    def fail_simulation(_proposal):
        raise RuntimeError("simulated local failure")

    monkeypatch.setattr(service, "_simulate", fail_simulation)
    with pytest.raises(RuntimeError, match="simulated local failure"):
        service.decide(proposal.action_id, ActionDecisionRequest(decision="approve"))
    connection = open_database(path)
    assert connection.execute("SELECT status FROM action_proposals").fetchone()[0] == "pending"
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    connection.close()


def test_missing_proposals_runs_and_incidents_are_distinguished(tmp_path):
    path = tmp_path / "actions.db"
    service = action_service(path)
    with pytest.raises(ActionNotFound):
        service.decide("MISSING", ActionDecisionRequest(decision="approve"))
    with pytest.raises(ActionNotFound):
        service.proposals_for_run("MISSING")
    with pytest.raises(ActionNotFound):
        service.audit_for_incident("MISSING", limit=50, offset=0)


def test_proposal_created_by_run_is_a_simulation_preview(tmp_path):
    from dataclasses import replace
    from phdata.config import get_settings
    from phdata.services.workflow import SupportWorkflow

    path = tmp_path / "workflow.db"
    connection = open_database(path)
    connection.execute(
        "INSERT INTO incidents (ticket_id, reported_store_id, opened_at, category, subcategory, status, subject, description) "
        "VALUES ('INC-1', '', ?, 'Printers', 'Unreviewed symptom', 'Open', 'Unknown', 'Unclassified')",
        (NOW.isoformat(),),
    )
    connection.commit()
    connection.close()
    settings = replace(get_settings(), database_path=path)
    run, _ = SupportWorkflow(
        settings, connection_factory=lambda: open_database(path), clock=lambda: NOW,
    ).start_run("INC-1", idempotency_key="preview")
    service = action_service(path)
    previews = service.proposals_for_run(run.run_id)
    assert len(previews.actions) == 1
    preview = previews.actions[0]
    assert preview.action_type == "simulate_escalation" and preview.status == "pending"
    assert preview.preview.summary == run.summary_for_ticket
    approved, _ = service.decide(preview.action_id, ActionDecisionRequest(decision="approve"))
    assert approved.status == "completed"
