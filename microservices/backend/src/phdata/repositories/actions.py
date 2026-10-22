"""SQLite storage for immutable action previews and append-only audit events."""

import json
import sqlite3
from hashlib import sha256
from typing import Any

from phdata.models.legacy import LegacyAuditEvent
from phdata.models.actions import (
    ActionDecisionResult,
    ActionProposal,
    AuditEvent,
    AuditPage,
    AuditPayload,
    EscalationPreview,
    PostCommentPreview,
)


class ActionStateConflict(Exception):
    """A proposal is missing or no longer in the expected pending state."""


def proposal_hash_for(proposal: ActionProposal) -> str:
    normalized = json.dumps(
        {
            "action_type": proposal.action_type,
            "preview": proposal.preview.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(normalized.encode("utf-8")).hexdigest()


def create_proposal(connection: sqlite3.Connection, proposal: ActionProposal) -> None:
    """Store a pending, immutable proposal; the caller owns the transaction."""
    if proposal.status != "pending" or proposal.decided_at is not None:
        raise ValueError("new proposals must be pending and undecided")
    if proposal_hash_for(proposal) != proposal.proposal_hash:
        raise ValueError("proposal_hash does not match the normalized proposal content")
    connection.execute(
        """INSERT INTO action_proposals (
            action_id, ticket_id, run_id, action_type, payload_json,
            proposal_hash, context_fingerprint, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (
            proposal.action_id,
            proposal.ticket_id,
            proposal.run_id,
            proposal.action_type,
            json.dumps(proposal.preview.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
            proposal.proposal_hash,
            proposal.context_fingerprint,
            proposal.created_at.isoformat(),
        ),
    )


def _proposal_from_row(row: sqlite3.Row) -> ActionProposal:
    preview_data: dict[str, Any] = json.loads(row["payload_json"])
    preview_model = PostCommentPreview if row["action_type"] == "simulate_post_comment" else EscalationPreview
    preview = preview_model.model_validate(preview_data)
    result = (
        ActionDecisionResult.model_validate_json(row["result_json"])
        if row["result_json"] is not None else None
    )
    proposal = ActionProposal(
        action_id=row["action_id"],
        ticket_id=row["ticket_id"],
        run_id=row["run_id"],
        action_type=row["action_type"],
        preview=preview,
        proposal_hash=row["proposal_hash"],
        context_fingerprint=row["context_fingerprint"],
        status=row["status"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        result_summary=result.result_summary if result is not None else None,
    )
    if proposal_hash_for(proposal) != proposal.proposal_hash:
        raise ValueError("stored proposal content does not match its immutable hash")
    if result is not None:
        expected_decision = "reject" if proposal.status == "rejected" else "approve"
        if result.action_id != proposal.action_id or result.decision != expected_decision:
            raise ValueError("stored decision result does not match its proposal state")
    return proposal


def get_proposal(connection: sqlite3.Connection, action_id: str) -> ActionProposal | None:
    row = connection.execute(
        "SELECT * FROM action_proposals WHERE action_id = ?", (action_id,),
    ).fetchone()
    return _proposal_from_row(row) if row is not None else None


def get_decision_result(
    connection: sqlite3.Connection,
    action_id: str,
) -> ActionDecisionResult | None:
    row = connection.execute(
        "SELECT result_json FROM action_proposals WHERE action_id = ?", (action_id,),
    ).fetchone()
    if row is None or row["result_json"] is None:
        return None
    return ActionDecisionResult.model_validate_json(row["result_json"])


def store_action_decision(
    connection: sqlite3.Connection,
    result: ActionDecisionResult,
) -> None:
    """Apply a pending-to-terminal transition without committing the caller's transaction."""
    status = result.status
    cursor = connection.execute(
        """UPDATE action_proposals SET status=?, decided_at=?, decided_by=?, result_json=?
        WHERE action_id=? AND status='pending'""",
        (
            status,
            result.decided_at.isoformat(),
            result.decided_by,
            result.model_dump_json(),
            result.action_id,
        ),
    )
    if cursor.rowcount != 1:
        raise ActionStateConflict("proposal is missing or no longer pending")


def list_proposals_for_run(connection: sqlite3.Connection, run_id: str) -> list[ActionProposal]:
    rows = connection.execute(
        "SELECT * FROM action_proposals WHERE run_id = ? "
        "ORDER BY created_at, action_id LIMIT 50",
        (run_id,),
    ).fetchall()
    return [_proposal_from_row(row) for row in rows]


def append_audit_event(
    connection: sqlite3.Connection,
    *,
    ticket_id: str,
    run_id: str | None,
    actor_type: str,
    actor_id: str | None,
    event_type: str,
    occurred_at: str,
    approval_status: str | None,
    payload: AuditPayload,
) -> AuditEvent:
    """Append a validated event without committing the caller's transaction."""
    event = AuditEvent(
        audit_event_id=1,
        ticket_id=ticket_id,
        run_id=run_id,
        action_id=payload.action_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type=event_type,
        occurred_at=occurred_at,
        approval_status=approval_status,
        payload=payload,
    )
    cursor = connection.execute(
        """INSERT INTO audit_events (
            ticket_id, run_id, actor_type, actor_id, event_type,
            occurred_at, payload_json, approval_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.ticket_id,
            event.run_id,
            event.actor_type,
            event.actor_id,
            event.event_type,
            event.occurred_at.isoformat(),
            event.payload.model_dump_json(),
            event.approval_status,
        ),
    )
    return event.model_copy(update={"audit_event_id": cursor.lastrowid})


def list_audit_events(
    connection: sqlite3.Connection,
    *,
    ticket_id: str,
    limit: int = 50,
    offset: int = 0,
) -> AuditPage:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1 to 100 and offset must not be negative")
    total = connection.execute(
        "SELECT COUNT(*) FROM audit_events WHERE ticket_id = ?", (ticket_id,),
    ).fetchone()[0]
    rows = connection.execute(
        "SELECT * FROM audit_events WHERE ticket_id = ? "
        "ORDER BY occurred_at ASC, audit_event_id ASC LIMIT ? OFFSET ?",
        (ticket_id, limit, offset),
    ).fetchall()
    events: list[AuditEvent | LegacyAuditEvent] = []
    for row in rows:
        stored_payload = json.loads(row["payload_json"])
        # v1 payloads had no versioned contract; never reinterpret them as actions.
        if not isinstance(stored_payload, dict) or "version" not in stored_payload:
            events.append(LegacyAuditEvent(
                **{field: row[field] for field in LegacyAuditEvent.model_fields
                   if field not in {"representation", "payload"}},
                payload=stored_payload,
            ))
            continue
        payload = AuditPayload.model_validate(stored_payload)
        events.append(AuditEvent(
            audit_event_id=row["audit_event_id"],
            ticket_id=row["ticket_id"],
            run_id=row["run_id"],
            action_id=payload.action_id,
            actor_type=row["actor_type"],
            actor_id=row["actor_id"],
            event_type=row["event_type"],
            occurred_at=row["occurred_at"],
            approval_status=row["approval_status"],
            payload=payload,
        ))
    return AuditPage(events=events, total=total, limit=limit, offset=offset)
