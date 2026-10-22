"""Atomic operator decisions for two local-only support simulations."""

from datetime import datetime, timezone
import sqlite3
from typing import Callable
from uuid import uuid4

from phdata.models.actions import (
    ActionDecisionRequest,
    ActionDecisionResult,
    ActionProposal,
    ActionProposalList,
    AuditPage,
    AuditPayload,
    EscalationPreview,
    PostCommentPreview,
)
from phdata.repositories.actions import (
    ActionStateConflict,
    append_audit_event,
    create_proposal,
    get_decision_result,
    get_proposal,
    list_audit_events,
    list_proposals_for_run,
    proposal_hash_for,
    store_action_decision,
)
from phdata.repositories.support import SupportRepository
from phdata.services.workflow import build_context_snapshot, context_fingerprint


class ActionNotFound(Exception):
    """The requested proposal, run, or incident does not exist."""


class ActionConflict(Exception):
    """The proposal is stale, already decided, or not eligible for a decision."""


class SupportActions:
    """Owns action transactions and never performs network or device operations."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.connection_factory = connection_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def proposals_for_run(self, run_id: str) -> ActionProposalList:
        connection = self.connection_factory()
        try:
            if connection.execute("SELECT 1 FROM agent_runs WHERE run_id=?", (run_id,)).fetchone() is None:
                raise ActionNotFound
            return ActionProposalList(run_id=run_id, actions=list_proposals_for_run(connection, run_id))
        finally:
            connection.close()

    def audit_for_incident(self, ticket_id: str, *, limit: int, offset: int) -> AuditPage:
        connection = self.connection_factory()
        try:
            if connection.execute("SELECT 1 FROM incidents WHERE ticket_id=?", (ticket_id,)).fetchone() is None:
                raise ActionNotFound
            return list_audit_events(connection, ticket_id=ticket_id, limit=limit, offset=offset)
        finally:
            connection.close()

    def decide(
        self,
        action_id: str,
        request: ActionDecisionRequest,
    ) -> tuple[ActionDecisionResult, bool]:
        connection = self.connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            proposal = get_proposal(connection, action_id)
            if proposal is None:
                raise ActionNotFound

            if proposal.status != "pending":
                prior = get_decision_result(connection, action_id)
                if prior is None:
                    raise ActionConflict
                if prior.decision != request.decision:
                    raise ActionConflict
                connection.commit()
                return prior, True

            run = connection.execute(
                "SELECT status FROM agent_runs WHERE run_id=? AND ticket_id=?",
                (proposal.run_id, proposal.ticket_id),
            ).fetchone()
            if run is None:
                raise ActionNotFound
            if run["status"] != "completed":
                raise ActionConflict

            context = SupportRepository(connection).get_context(proposal.ticket_id, now=self._now())
            if context is None:
                raise ActionConflict
            current_fingerprint = context_fingerprint(build_context_snapshot(context))
            if current_fingerprint != proposal.context_fingerprint:
                raise ActionConflict

            if request.decision == "approve":
                decided_at = self._now()
                append_audit_event(
                    connection,
                    ticket_id=proposal.ticket_id,
                    run_id=proposal.run_id,
                    actor_type="human",
                    actor_id="local_operator",
                    event_type="proposal_approved",
                    occurred_at=decided_at.isoformat(),
                    approval_status="approved",
                    payload=AuditPayload(
                        summary="Operator approved the local simulation",
                        action_id=proposal.action_id,
                    ),
                )
                summary = self._simulate(proposal)
                result = ActionDecisionResult(
                    action_id=proposal.action_id,
                    decision="approve",
                    status="completed",
                    decided_at=decided_at,
                    decided_by="local_operator",
                    result_summary=summary,
                )
                store_action_decision(connection, result)
                append_audit_event(
                    connection,
                    ticket_id=proposal.ticket_id,
                    run_id=proposal.run_id,
                    actor_type="system",
                    actor_id="local_simulation",
                    event_type="simulation_completed",
                    occurred_at=result.decided_at.isoformat(),
                    approval_status="completed",
                    payload=AuditPayload(
                        summary=summary,
                        action_id=proposal.action_id,
                    ),
                )
            else:
                result = ActionDecisionResult(
                    action_id=proposal.action_id,
                    decision="reject",
                    status="rejected",
                    decided_at=self._now(),
                    decided_by="local_operator",
                    result_summary="Proposal rejected. No local simulation was performed.",
                )
                store_action_decision(connection, result)
                append_audit_event(
                    connection,
                    ticket_id=proposal.ticket_id,
                    run_id=proposal.run_id,
                    actor_type="human",
                    actor_id="local_operator",
                    event_type="proposal_rejected",
                    occurred_at=result.decided_at.isoformat(),
                    approval_status="rejected",
                    payload=AuditPayload(
                        summary="Operator rejected the local simulation",
                        action_id=proposal.action_id,
                    ),
                )
            connection.commit()
            return result, False
        except (ActionNotFound, ActionConflict, ActionStateConflict):
            if connection.in_transaction:
                connection.rollback()
            raise
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("action clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _simulate(proposal: ActionProposal) -> str:
        if proposal.action_type == "simulate_post_comment":
            if not isinstance(proposal.preview, PostCommentPreview):
                raise ActionConflict
            return "Support comment simulation recorded locally; incident source fields and external systems were unchanged."
        if proposal.action_type == "simulate_escalation":
            if not isinstance(proposal.preview, EscalationPreview):
                raise ActionConflict
            return "Local L2 handoff simulation recorded; no notification or ITSM request was sent."
        raise ActionConflict


def make_post_comment_proposal(
    *,
    ticket_id: str,
    run_id: str,
    comment: str,
    context_fingerprint_value: str,
    created_at: datetime,
) -> ActionProposal:
    return _make_proposal(
        ticket_id=ticket_id,
        run_id=run_id,
        action_type="simulate_post_comment",
        preview=PostCommentPreview(comment=comment),
        context_fingerprint_value=context_fingerprint_value,
        created_at=created_at,
    )


def make_escalation_proposal(
    *,
    ticket_id: str,
    run_id: str,
    summary: str,
    reason: str,
    context_fingerprint_value: str,
    created_at: datetime,
) -> ActionProposal:
    return _make_proposal(
        ticket_id=ticket_id,
        run_id=run_id,
        action_type="simulate_escalation",
        preview=EscalationPreview(summary=summary, reason=reason),
        context_fingerprint_value=context_fingerprint_value,
        created_at=created_at,
    )


def _make_proposal(
    *,
    ticket_id: str,
    run_id: str,
    action_type: str,
    preview: PostCommentPreview | EscalationPreview,
    context_fingerprint_value: str,
    created_at: datetime,
) -> ActionProposal:
    proposal = ActionProposal(
        action_id=f"ACT-{uuid4()}",
        ticket_id=ticket_id,
        run_id=run_id,
        action_type=action_type,
        preview=preview,
        proposal_hash="0" * 64,
        context_fingerprint=context_fingerprint_value,
        status="pending",
        created_at=created_at,
    )
    return proposal.model_copy(update={"proposal_hash": proposal_hash_for(proposal)})


def create_outcome_proposal(
    connection: sqlite3.Connection,
    result,
    *,
    context_fingerprint_value: str,
    created_at: datetime,
) -> ActionProposal | None:
    """Construct the only allowed local preview from a completed run result."""
    if result.status != "completed" or result.routing_outcome is None:
        return None
    if result.routing_outcome == "l2_escalation":
        summary = result.summary_for_ticket or "Human support review is required."
        if len(summary) > 2000:
            summary = "Human support review is required for this run."
        proposal = make_escalation_proposal(
            ticket_id=result.ticket_id,
            run_id=result.run_id,
            summary=summary,
            reason="The completed run requires a human support handoff.",
            context_fingerprint_value=context_fingerprint_value,
            created_at=created_at,
        )
    elif result.routing_outcome in {"l1_guided", "needs_clarification"}:
        if result.routing_outcome == "l1_guided":
            parts = [result.summary_for_requester or ""]
            parts.extend(f"- {step.instruction}" for step in result.steps)
        else:
            parts = [question.text for question in result.questions]
        comment = chr(10).join(part for part in parts if part)
        if not comment or len(comment) > 2000:
            return None
        proposal = make_post_comment_proposal(
            ticket_id=result.ticket_id,
            run_id=result.run_id,
            comment=comment,
            context_fingerprint_value=context_fingerprint_value,
            created_at=created_at,
        )
    else:
        return None
    create_proposal(connection, proposal)
    append_audit_event(
        connection,
        ticket_id=result.ticket_id,
        run_id=result.run_id,
        actor_type="agent",
        actor_id="support_workflow",
        event_type="proposal_created",
        occurred_at=created_at.isoformat(),
        approval_status="pending",
        payload=AuditPayload(summary="Created a local simulation preview", action_id=proposal.action_id),
    )
    return proposal
