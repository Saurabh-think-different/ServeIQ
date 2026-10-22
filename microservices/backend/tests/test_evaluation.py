from datetime import datetime, timezone
from hashlib import sha256

from phdata.models.actions import ActionDecisionResult
from phdata.models.runs import EvidenceSnapshot, RunResult, ReviewedStep, evidence_id_for
from evaluation.pilot import (
    EvaluationExpectation,
    EvaluationObservation,
    evaluate,
)

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
CONTENT = "Synthetic reviewed printer check."
CONTENT_HASH = sha256(CONTENT.encode()).hexdigest()
SOURCE = "kb/runbooks/printer-offline.md"
SECTION = "Synthetic check"
EVIDENCE_ID = evidence_id_for(SOURCE, SECTION, CONTENT_HASH)


def completed(case_id, outcome):
    evidence = [EvidenceSnapshot(
        evidence_id=EVIDENCE_ID,
        source_path=SOURCE,
        section=SECTION,
        content=CONTENT,
        content_sha256=CONTENT_HASH,
        applicability="context_match",
        applicability_notes=[],
        similarity_score=0.9,
    )] if outcome == "l1_guided" else []
    steps = [ReviewedStep(
        step_id="printer.synthetic",
        instruction="Synthetic reviewed check.",
        evidence_ids=[EVIDENCE_ID],
    )] if evidence else []
    return RunResult(
        run_id=f"RUN-{case_id}",
        ticket_id="INC-SYNTHETIC",
        status="completed",
        started_at=NOW,
        completed_at=NOW,
        routing_outcome=outcome,
        decision_mechanism="deterministic",
        summary_for_requester="Synthetic output.",
        summary_for_ticket="Synthetic evaluation only.",
        steps=steps,
        evidence=evidence,
    )


def test_synthetic_pilot_evaluation_reports_denominators_and_control_failures():
    route_expectations = [
        ("guided_printer", True, "l1_guided"),
        ("missing_context", True, "needs_clarification"),
        ("incompatible_version", True, "l2_escalation"),
        ("conflicting_policy", True, "l2_escalation"),
        ("out_of_scope", False, "l2_escalation"),
        ("rejected_action", True, "l2_escalation"),
        ("approval_replay", True, "l2_escalation"),
    ]
    observations = [
        EvaluationObservation(
            expectation=EvaluationExpectation(
                case_id=case_id,
                pilot_eligible=eligible,
                expected_routing=outcome,
                expected_action="reject" if case_id == "rejected_action" else (
                    "approve" if case_id == "approval_replay" else None
                ),
                expected_replay=True if case_id == "approval_replay" else None,
            ),
            run=completed(case_id, outcome),
            action=ActionDecisionResult(
                action_id=f"ACT-{case_id}",
                decision="reject" if case_id == "rejected_action" else "approve",
                status="rejected" if case_id == "rejected_action" else "completed",
                decided_at=NOW,
                decided_by="local_operator",
                result_summary="Synthetic action result.",
            ) if case_id in {"rejected_action", "approval_replay"} else None,
            replayed=case_id == "approval_replay" if case_id == "approval_replay" else None,
        )
        for case_id, eligible, outcome in route_expectations
    ]
    observations.append(EvaluationObservation(
        expectation=EvaluationExpectation(
            case_id="provider_failure",
            pilot_eligible=True,
            expected_routing=None,
        ),
        run=RunResult(
            run_id="RUN-provider-failure",
            ticket_id="INC-SYNTHETIC",
            status="failed",
            started_at=NOW,
            completed_at=NOW,
            error_code="knowledge_unavailable",
            error_message="Synthetic provider outage.",
        ),
    ))

    report = evaluate(observations)
    assert report.case_count == 8
    assert report.eligible_case_count == 7
    assert report.labeled_case_count == 7
    assert report.routing_matches == 7
    assert report.routing_agreement == 1.0
    assert report.escalation_misses == ()
    assert report.uncited_guided_cases == ()
    assert report.action_mismatches == ()
    assert report.replay_mismatches == ()


def test_evaluation_does_not_invent_agreement_without_labeled_cases():
    report = evaluate([])
    assert report.labeled_case_count == 0
    assert report.routing_agreement is None
