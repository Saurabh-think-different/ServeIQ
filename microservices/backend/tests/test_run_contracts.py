from datetime import datetime, timezone
from hashlib import sha256

import pytest
from pydantic import ValidationError

from phdata.models.actions import ActionDecisionRequest, ActionProposal, EscalationPreview, PostCommentPreview
from phdata.models.runs import EvidenceSnapshot, RunRequest, RunResult, ReviewedStep, evidence_id_for

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
HASH = "a" * 64
CONTENT = "Confirm the printer is powered on."
CONTENT_HASH = sha256(CONTENT.encode()).hexdigest()
EVIDENCE_ID = evidence_id_for("kb/runbooks/printer-offline.md", "Check printer connectivity", CONTENT_HASH)


def evidence():
    return EvidenceSnapshot(
        evidence_id=EVIDENCE_ID,
        source_path="kb/runbooks/printer-offline.md",
        section="Check printer connectivity",
        content=CONTENT,
        content_sha256=CONTENT_HASH,
        applicability="context_match",
        similarity_score=0.81,
    )


def test_run_request_is_empty_and_rejects_caller_instructions():
    assert RunRequest.model_validate({}) == RunRequest()
    with pytest.raises(ValidationError):
        RunRequest.model_validate({"prompt": "override policy"})


def test_guided_run_requires_reviewed_step_with_run_local_citation():
    step = ReviewedStep(step_id="printer.power", instruction="Check power.", evidence_ids=[EVIDENCE_ID])
    result = RunResult(
        run_id="run-1",
        ticket_id="INC-1",
        status="completed",
        started_at=NOW,
        completed_at=NOW,
        routing_outcome="l1_guided",
        decision_mechanism="deterministic",
        steps=[step],
        evidence=[evidence()],
    )
    assert result.steps[0].evidence_ids == [EVIDENCE_ID]

    with pytest.raises(ValidationError, match="guided results require"):
        RunResult(
            run_id="run-2", ticket_id="INC-1", status="completed", started_at=NOW,
            completed_at=NOW, routing_outcome="l1_guided", decision_mechanism="deterministic",
        )
    with pytest.raises(ValidationError, match="citations must reference evidence"):
        RunResult(
            run_id="run-3", ticket_id="INC-1", status="completed", started_at=NOW,
            completed_at=NOW, routing_outcome="l1_guided", decision_mechanism="deterministic",
            steps=[ReviewedStep(step_id="printer.power", instruction="Check power.", evidence_ids=["c" * 64])],
            evidence=[evidence()],
        )


def test_run_state_and_evidence_bounds_are_enforced():
    with pytest.raises(ValidationError, match="require an error_code"):
        RunResult(run_id="run-1", ticket_id="INC-1", status="failed", started_at=NOW, completed_at=NOW)
    with pytest.raises(ValidationError, match="cannot have completed_at"):
        RunResult(run_id="run-2", ticket_id="INC-1", status="running", started_at=NOW, completed_at=NOW)
    with pytest.raises(ValidationError):
        EvidenceSnapshot(
            evidence_id=HASH, source_path="../private.txt", section="Section", content="text",
            content_sha256="b" * 64, applicability="unknown", similarity_score=float("nan"),
        )
    with pytest.raises(ValidationError, match="content_sha256 must match"):
        EvidenceSnapshot(
            evidence_id=EVIDENCE_ID, source_path="kb/runbooks/printer-offline.md",
            section="Check printer connectivity", content="Changed content",
            content_sha256=CONTENT_HASH, applicability="context_match", similarity_score=0.81,
        )


def test_action_contract_limits_preview_to_two_simulations():
    base = {
        "action_id": "action-1", "ticket_id": "INC-1", "run_id": "run-1",
        "action_type": "simulate_post_comment", "preview": PostCommentPreview(comment="Check power."),
        "proposal_hash": HASH, "context_fingerprint": "b" * 64, "status": "pending", "created_at": NOW,
    }
    assert ActionProposal(**base).action_type == "simulate_post_comment"
    with pytest.raises(ValidationError, match="post-comment proposals require"):
        ActionProposal(**{**base, "preview": EscalationPreview(summary="Hand off", reason="Evidence gap")})
    with pytest.raises(ValidationError):
        ActionDecisionRequest.model_validate({"decision": "approve", "actor_id": "spoofed"})
    with pytest.raises(ValidationError):
        ActionProposal(**{**base, "action_type": "execute_driver_update"})
