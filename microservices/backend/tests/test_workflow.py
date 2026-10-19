from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from phdata.config import get_settings
from phdata.db.database import open_database
from phdata.models.policy import ReviewedPolicy
from phdata.models.retrieval import Passage, SearchResponse
from phdata.repositories.runs import insert_run
from phdata.repositories.support import SupportRepository
from phdata.services.retrieval import RetrievalError
from phdata.services.workflow import (
    IdempotencyConflict,
    IncidentNotFound,
    SupportWorkflow,
    build_context_snapshot,
    context_fingerprint,
)

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
SOURCE_PATH = "kb/runbooks/printer-offline.md"
SECTION = "Power indicator"
CONTENT = "Confirm the printer power indicator is illuminated."
CONTENT_HASH = sha256(CONTENT.encode()).hexdigest()


def seed_database(path: Path, *, category="Printers", subcategory="Printer Offline or Not Detected"):
    connection = open_database(path)
    connection.execute(
        "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
        "VALUES ('S1', 'Store 1', 'City', 'Area', 'corporate')",
    )
    connection.execute(
        "INSERT INTO assets (asset_id, store_id, asset_type, make, model, system_version, serial_number) "
        "VALUES ('A1', 'S1', 'Receipt Printer', 'Epson', 'EpsonTM-T88VI', '1.2', 'SERIAL-SECRET')",
    )
    connection.execute(
        "INSERT INTO sla_targets (priority, category, first_response_minutes, resolution_target_minutes, escalation_to_l2_minutes) "
        "VALUES ('P2', 'Printers', 60, 240, 120)",
    )
    connection.execute(
        """INSERT INTO incidents (
            ticket_id, reported_store_id, store_id, reported_asset_id, asset_id,
            opened_at, priority, category, subcategory, status, subject, description, system_version
        ) VALUES ('INC-1', 'S1', 'S1', 'A1', 'A1', ?, 'P2', ?, ?, 'Open',
                  'Printer offline', 'Receipt printer is not detected.', '1.2')""",
        (NOW.isoformat(), category, subcategory),
    )
    connection.commit()
    connection.close()


def make_policy(*, content=CONTENT, applicability="exact"):
    return ReviewedPolicy.model_validate({
        "policy_id": "printer-offline-policy",
        "policy_version": "reviewed-v1",
        "category": "Printers",
        "symptom_scope": "Printer Offline or Not Detected",
        "steps": [{
            "step_id": "printer.power-indicator",
            "policy_id": "printer-offline-policy",
            "policy_version": "reviewed-v1",
            "category": "Printers",
            "symptom_scope": "Printer Offline or Not Detected",
            "operator_role": "store_operator",
            "source_path": SOURCE_PATH,
            "section": SECTION,
            "approved_content_sha256": sha256(content.encode()).hexdigest(),
            "disposition": "guidance",
            "instruction": "Confirm the printer power indicator is illuminated.",
            "applicability": applicability,
            "allowed_models": ["EpsonTM-T88VI"] if applicability == "exact" else [],
        }],
    })


class FakeRetrieval:
    def __init__(self, *, callback=None, error=None, passage=None):
        self.calls = []
        self.callback = callback
        self.error = error
        self.passage = passage or Passage(
            content=CONTENT,
            source_path=SOURCE_PATH,
            section=SECTION,
            similarity_score=0.87,
            category="Printers",
            model="EpsonTM-T88VI",
            system_version="1.2",
            document_type="runbook",
            applicability="context_match",
            applicability_notes=[],
        )

    def search(self, request):
        self.calls.append(request)
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        return SearchResponse(passages=[self.passage], evidence_gap=False, warnings=[])


def workflow(path, *, policies=(), retrieval=None, clock=None):
    settings = replace(get_settings(), database_path=path, ingestion_token="test-token")
    return SupportWorkflow(
        settings,
        policies=policies,
        retrieval=retrieval,
        connection_factory=lambda: open_database(path),
        clock=clock or (lambda: NOW),
    )


def test_empty_policy_fails_closed_without_retrieval_or_generation(tmp_path):
    path = tmp_path / "runs.db"
    seed_database(path)
    retrieval = FakeRetrieval()
    service = workflow(path, retrieval=retrieval)

    result, replayed = service.start_run("INC-1", idempotency_key="key-1")
    assert not replayed
    assert result.status == "completed"
    assert result.routing_outcome == "l2_escalation"
    assert result.steps == [] and result.evidence == []
    assert any("No reviewed support policy" in warning for warning in result.warnings)
    assert retrieval.calls == []

    replay, was_replayed = service.start_run("INC-1", idempotency_key="key-1")
    assert was_replayed and replay == result
    assert retrieval.calls == []
    connection = open_database(path)
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 3
    assert connection.execute("SELECT COUNT(*) FROM action_proposals").fetchone()[0] == 1
    connection.close()


def test_exact_reviewed_guidance_is_snapshotted_and_cited(tmp_path):
    path = tmp_path / "runs.db"
    seed_database(path)
    retrieval = FakeRetrieval()
    result, replayed = workflow(path, policies=[make_policy()], retrieval=retrieval).start_run(
        "INC-1", idempotency_key="guided-1",
    )
    assert not replayed
    assert result.status == "completed" and result.routing_outcome == "l1_guided"
    assert result.decision_mechanism == "deterministic"
    assert len(retrieval.calls) == 1
    assert retrieval.calls[0].query == "Printers Printer Offline or Not Detected"
    assert result.steps[0].evidence_ids == [result.evidence[0].evidence_id]
    assert result.evidence[0].content == CONTENT
    assert result.model is None
    assert "SERIAL-SECRET" not in str(result.model_dump())


def test_changed_source_hash_or_unknown_applicability_never_guides(tmp_path):
    for index, passage in enumerate((
        Passage(
            content=CONTENT + " Altered.", source_path=SOURCE_PATH, section=SECTION,
            similarity_score=0.9, category="Printers", model="EpsonTM-T88VI", system_version="1.2",
            document_type="runbook", applicability="context_match", applicability_notes=[],
        ),
        Passage(
            content=CONTENT, source_path=SOURCE_PATH, section=SECTION,
            similarity_score=0.9, category=None, model=None, system_version=None,
            document_type="runbook", applicability="unknown", applicability_notes=["model is unspecified"],
        ),
    )):
        path = tmp_path / f"runs-{index}.db"
        seed_database(path)
        result, _ = workflow(
            path, policies=[make_policy()], retrieval=FakeRetrieval(passage=passage),
        ).start_run("INC-1", idempotency_key=f"key-{index}")
        assert result.routing_outcome == "l2_escalation"
        assert result.steps == [] and result.evidence == []


def test_incompatible_model_and_out_of_scope_reports_escalate_without_search(tmp_path):
    path = tmp_path / "runs.db"
    seed_database(path)
    connection = open_database(path)
    connection.execute("UPDATE assets SET model='Other Printer' WHERE asset_id='A1'")
    connection.commit()
    connection.close()
    retrieval = FakeRetrieval()
    incompatible, _ = workflow(path, policies=[make_policy()], retrieval=retrieval).start_run(
        "INC-1", idempotency_key="incompatible",
    )
    assert incompatible.routing_outcome == "l2_escalation"
    assert retrieval.calls == []

    out_of_scope_path = tmp_path / "out-of-scope.db"
    seed_database(out_of_scope_path, category="POS", subcategory="Printer Offline")
    out_of_scope, _ = workflow(
        out_of_scope_path, policies=[make_policy()], retrieval=retrieval,
    ).start_run("INC-1", idempotency_key="out-of-scope")
    assert out_of_scope.routing_outcome == "l2_escalation"
    assert retrieval.calls == []


def test_conflicting_exact_policy_matches_escalate_without_search(tmp_path):
    path = tmp_path / "runs.db"
    seed_database(path)
    second = make_policy().model_dump()
    second["policy_id"] = "second-printer-policy"
    second["steps"][0]["policy_id"] = "second-printer-policy"
    second["steps"][0]["step_id"] = "printer.power-indicator.alternate"
    retrieval = FakeRetrieval()
    result, _ = workflow(
        path,
        policies=[make_policy(), ReviewedPolicy.model_validate(second)],
        retrieval=retrieval,
    ).start_run("INC-1", idempotency_key="conflict")
    assert result.routing_outcome == "l2_escalation"
    assert retrieval.calls == []


def test_retrieval_failure_is_persisted_as_a_safe_failed_run(tmp_path):
    path = tmp_path / "runs.db"
    seed_database(path)
    retrieval = FakeRetrieval(error=RetrievalError(503, "secret provider traceback"))
    service = workflow(path, policies=[make_policy()], retrieval=retrieval)
    result, _ = service.start_run("INC-1", idempotency_key="key")
    assert result.status == "failed"
    assert result.error_code == "knowledge_unavailable"
    assert "traceback" not in result.error_message
    assert service.get_run(result.run_id) == result


def test_context_change_during_retrieval_fails_without_proposal(tmp_path):
    path = tmp_path / "runs.db"
    seed_database(path)

    def change_report():
        connection = open_database(path)
        connection.execute("UPDATE incidents SET description='New device facts' WHERE ticket_id='INC-1'")
        connection.commit()
        connection.close()

    retrieval = FakeRetrieval(callback=change_report)
    result, _ = workflow(path, policies=[make_policy()], retrieval=retrieval).start_run(
        "INC-1", idempotency_key="stale",
    )
    assert result.status == "failed" and result.error_code == "context_changed"
    connection = open_database(path)
    assert connection.execute("SELECT COUNT(*) FROM action_proposals").fetchone()[0] == 0
    connection.close()


def test_unknown_incident_has_no_run_or_audit_side_effects(tmp_path):
    path = tmp_path / "runs.db"
    service = workflow(path)
    with pytest.raises(IncidentNotFound):
        service.start_run("UNKNOWN", idempotency_key="key")
    connection = open_database(path)
    assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    connection.close()


def test_request_fingerprint_conflict_and_per_ticket_keys(tmp_path):
    path = tmp_path / "runs.db"
    seed_database(path)
    service = workflow(path)
    first, _ = service.start_run("INC-1", idempotency_key="same")
    connection = open_database(path)
    connection.execute(
        "UPDATE agent_runs SET request_fingerprint=? WHERE run_id=?",
        ("c" * 64, first.run_id),
    )
    connection.commit()
    connection.close()
    with pytest.raises(IdempotencyConflict):
        service.start_run("INC-1", idempotency_key="same")

    connection = open_database(path)
    connection.execute(
        "INSERT INTO incidents (ticket_id, reported_store_id, opened_at, status, subject, description) "
        "VALUES ('INC-2', '', ?, 'Open', 'POS issue', 'Register is slow')",
        (NOW.isoformat(),),
    )
    connection.commit()
    connection.close()
    other, replayed = service.start_run("INC-2", idempotency_key="same")
    assert not replayed and other.ticket_id == "INC-2"


def test_context_fingerprint_ignores_clock_time_and_serial_number(tmp_path):
    path = tmp_path / "runs.db"
    seed_database(path)
    connection = open_database(path)
    repository = SupportRepository(connection)
    early = repository.get_context("INC-1", now=NOW)
    later = repository.get_context("INC-1", now=NOW + timedelta(minutes=15))
    assert early is not None and later is not None
    assert context_fingerprint(build_context_snapshot(early)) == context_fingerprint(build_context_snapshot(later))
    assert "serial_number" not in build_context_snapshot(early)["asset"]
    connection.close()


def test_missing_category_and_symptom_produce_specific_questions(tmp_path):
    path = tmp_path / "runs.db"
    seed_database(path, category=None, subcategory=None)
    result, _ = workflow(path).start_run("INC-1", idempotency_key="missing")
    assert result.routing_outcome == "needs_clarification"
    assert {question.question_id for question in result.questions} == {"confirm_category", "confirm_symptom"}
    assert result.steps == []


def test_startup_recovery_is_idempotent(tmp_path):
    path = tmp_path / "runs.db"
    seed_database(path)
    connection = open_database(path)
    insert_run(
        connection,
        run_id="RUN-ORPHAN",
        ticket_id="INC-1",
        started_at=NOW.isoformat(),
        idempotency_key="orphan",
        request_fingerprint="a" * 64,
    )
    connection.commit()
    from phdata.services.workflow import recover_interrupted_runs

    assert recover_interrupted_runs(connection, now=NOW + timedelta(minutes=1)) == 1
    connection.commit()
    assert recover_interrupted_runs(connection, now=NOW + timedelta(minutes=2)) == 0
    connection.commit()
    recovered = SupportWorkflow(
        replace(get_settings(), database_path=path),
        connection_factory=lambda: open_database(path),
        clock=lambda: NOW,
    ).get_run("RUN-ORPHAN")
    assert recovered is not None and recovered.status == "failed" and recovered.error_code == "interrupted"
    assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='run_failed'").fetchone()[0] == 1
    connection.close()
