"""Strict contracts for reviewed proposals, local decisions, and audit reads."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from phdata.models.legacy import LegacyAuditEvent


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ActionType = Literal["simulate_post_comment", "simulate_escalation"]
ProposalStatus = Literal["pending", "rejected", "completed"]


class ActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PostCommentPreview(ActionModel):
    version: Literal[1] = 1
    comment: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class EscalationPreview(ActionModel):
    version: Literal[1] = 1
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


ProposalPreview = PostCommentPreview | EscalationPreview


class ActionProposal(ActionModel):
    action_id: NonBlank
    ticket_id: NonBlank
    run_id: NonBlank
    action_type: ActionType
    preview: ProposalPreview
    proposal_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    context_fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    status: ProposalStatus
    created_at: AwareDatetime
    decided_at: AwareDatetime | None = None
    decided_by: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    result_summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] | None = None

    @model_validator(mode="after")
    def preview_matches_action(self) -> "ActionProposal":
        if self.action_type == "simulate_post_comment" and not isinstance(self.preview, PostCommentPreview):
            raise ValueError("post-comment proposals require a comment preview")
        if self.action_type == "simulate_escalation" and not isinstance(self.preview, EscalationPreview):
            raise ValueError("escalation proposals require an escalation preview")
        if self.status == "pending" and self.decided_at is not None:
            raise ValueError("pending proposals cannot have a decision timestamp")
        if self.status != "pending" and self.decided_at is None:
            raise ValueError("decided proposals require a decision timestamp")
        return self


class ActionProposalList(ActionModel):
    run_id: NonBlank
    actions: list[ActionProposal] = Field(max_length=50)


class ActionDecisionRequest(ActionModel):
    decision: Literal["approve", "reject"]


class ActionDecisionResult(ActionModel):
    action_id: NonBlank
    decision: Literal["approve", "reject"]
    status: Literal["rejected", "completed"]
    decided_at: AwareDatetime
    decided_by: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    result_summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def decision_matches_status(self) -> "ActionDecisionResult":
        expected = "completed" if self.decision == "approve" else "rejected"
        if self.status != expected:
            raise ValueError("decision and result status do not match")
        return self


class AuditPayload(ActionModel):
    version: Literal[1] = 1
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    action_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    error_code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")] | None = None


class AuditEvent(ActionModel):
    audit_event_id: int = Field(ge=1)
    ticket_id: NonBlank
    run_id: NonBlank | None = None
    action_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    actor_type: Literal["system", "agent", "human"]
    actor_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    event_type: Literal[
        "run_started",
        "run_completed",
        "run_failed",
        "proposal_created",
        "proposal_approved",
        "proposal_rejected",
        "simulation_completed",
    ]
    occurred_at: AwareDatetime
    approval_status: Literal["not_required", "pending", "approved", "rejected", "completed", "failed"] | None = None
    payload: AuditPayload


class AuditPage(ActionModel):
    events: list[AuditEvent | LegacyAuditEvent] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
