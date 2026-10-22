"""Persist a first-wins escalation inside the caller's transaction, never dispatch."""

import json
import math
from datetime import UTC
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.models import Incident, Message, utc_now
from src.models.escalation import L2Escalation, escalation_id_for

REASON_CODES = (
    "unresolved",
    "user_requested",
    "insufficient_evidence",
    "urgent",
    "out_of_scope",
)
SIMULATION_NOTE = "Simulated L2 escalation only. No team has been contacted."
MAX_LIST_ITEMS = 100
MAX_JSON_BYTES = 120000


def _text(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be a nonempty string of at most {maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must contain valid UTF-8 text") from error
    return value


def _text_list(value: list[str], name: str, maximum_items: int, maximum_chars: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError(f"{name} must be a list of at most {maximum_items} strings")
    return [_text(item, name, maximum_chars) for item in value]


def _check_json(value: Any, name: str, depth: int = 0) -> None:
    if depth > 10:
        raise ValueError(f"{name} exceeds the maximum JSON nesting depth")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, str):
        if len(value) > 50000:
            raise ValueError(f"{name} strings must be at most 50000 characters")
        return
    if isinstance(value, list) and len(value) <= MAX_LIST_ITEMS:
        for item in value:
            _check_json(item, name, depth + 1)
        return
    if isinstance(value, dict) and len(value) <= MAX_LIST_ITEMS:
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 200:
                raise ValueError(f"{name} keys must be strings of at most 200 characters")
            _check_json(item, name, depth + 1)
        return
    raise ValueError(f"{name} must contain only bounded, JSON-safe values")


def _json_copy(value: Any, name: str) -> Any:
    _check_json(value, name)
    try:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
        size = len(serialized.encode("utf-8"))
    except (ValueError, UnicodeEncodeError) as error:
        raise ValueError(f"{name} must be JSON-safe UTF-8 data") from error
    if size > MAX_JSON_BYTES:
        raise ValueError(f"{name} exceeds {MAX_JSON_BYTES} bytes")
    return json.loads(serialized)


def _snapshot(value: list[dict]) -> list[dict]:
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError("conversation_snapshot must be a list of at most 12 objects")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError("conversation_snapshot entries must be objects")
    snapshot = _json_copy(value, "conversation_snapshot")
    for item in snapshot:
        if item.get("role") not in ("user", "assistant"):
            raise ValueError("conversation_snapshot roles must be user or assistant")
        content = item.get("content")
        if not isinstance(content, str) or len(content) > 4000:
            raise ValueError(
                "conversation_snapshot content must be text of at most 4000 characters"
            )
    return snapshot


def _support_context(value: dict | None) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_LIST_ITEMS:
        raise ValueError("support_context must be an object of at most 100 keys")
    return _json_copy(value, "support_context")


def record_escalation(
    db: Session,
    *,
    incident_id: str,
    user_id: str,
    message_id: str,
    reason_code: str,
    reason: str,
    summary: str,
    attempted_steps: list[str],
    citations: list[str],
    missing_information: list[str],
    conversation_snapshot: list[dict],
    support_context: dict | None = None,
) -> L2Escalation:
    """Flush, but never commit/rollback or change the incident or assistant message.

    The caller must commit this record together with its final incident/message
    updates. A rollback (including cancellation) leaves no escalation to count.
    Retries for an owned incident return its original record unchanged.
    """
    _text(incident_id, "incident_id", 36)
    _text(user_id, "user_id", 36)
    incident = db.scalar(
        select(Incident)
        .where(Incident.id == incident_id, Incident.user_id == user_id)
        .with_for_update()
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    existing = db.scalar(select(L2Escalation).where(L2Escalation.incident_id == incident_id))
    if existing is not None:
        if existing.user_id != user_id:
            raise HTTPException(status_code=404, detail="Escalation not found")
        return existing

    _text(message_id, "message_id", 36)
    message = db.scalar(
        select(Message).where(Message.id == message_id, Message.incident_id == incident_id)
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    _text(reason_code, "reason_code", 32)
    if reason_code not in REASON_CODES:
        raise ValueError(f"reason_code must be one of: {', '.join(REASON_CODES)}")
    values = {
        "id": escalation_id_for(incident_id),
        "incident_id": incident_id,
        "user_id": user_id,
        "message_id": message_id,
        "reason_code": reason_code,
        "reason": _text(reason, "reason", 4000),
        "summary": _text(summary, "summary", 8000),
        "attempted_steps": _text_list(attempted_steps, "attempted_steps", 20, 1000),
        "citations": _text_list(citations, "citations", 12, 4000),
        "missing_information": _text_list(missing_information, "missing_information", 20, 300),
        "conversation_snapshot": _snapshot(conversation_snapshot),
        "support_context": _support_context(support_context),
        "dispatch_status": "simulated",
        "created_at": utc_now(),
    }
    payload = {**values, "created_at": values["created_at"].isoformat()}
    _json_copy(payload, "escalation")
    dialect = db.get_bind().dialect.name
    # Conflict handling stays in the outer transaction (no committing savepoints).
    if dialect in {"sqlite", "postgresql"}:
        insert = sqlite_insert if dialect == "sqlite" else postgresql_insert
        db.execute(
            insert(L2Escalation)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["incident_id"])
        )
        row = db.scalar(select(L2Escalation).where(L2Escalation.incident_id == incident_id))
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="Escalation not found")
        return row
    row = L2Escalation(**values)
    db.add(row)
    db.flush()
    return row


def public_escalation(row: L2Escalation) -> dict:
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return {
        "id": row.id,
        "incident_id": row.incident_id,
        "user_id": row.user_id,
        "message_id": row.message_id,
        "reason_code": row.reason_code,
        "reason": row.reason,
        "summary": row.summary,
        "attempted_steps": list(row.attempted_steps),
        "citations": list(row.citations),
        "missing_information": list(row.missing_information),
        "conversation_snapshot": json.loads(json.dumps(row.conversation_snapshot, allow_nan=False)),
        "support_context": json.loads(json.dumps(row.support_context, allow_nan=False)),
        "dispatch_status": row.dispatch_status,
        "created_at": created_at.astimezone(UTC).isoformat(),
        "team_contacted": False,
        "note": SIMULATION_NOTE,
    }


__all__ = ["REASON_CODES", "SIMULATION_NOTE", "public_escalation", "record_escalation"]
