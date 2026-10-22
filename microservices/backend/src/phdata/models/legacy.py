"""Read-only v1 history: stored facts, not reconstructed modern decisions.

Legacy runs did not require typed snapshots, completion timestamps, citations,
or decision mechanisms. Raw JSON columns remain strings to preserve even
unstructured historical content without presenting it as reviewed evidence.
Runs without any v2 request/context metadata and audit payloads without a
version are historical representations. Invalid versioned snapshots still fail
validation. These models are not accepted by finalization or audit append APIs.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


class LegacyRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    representation: Literal["legacy_v1"] = "legacy_v1"
    run_id: str
    ticket_id: str
    status: Literal["running", "completed", "failed"]
    started_at: str
    completed_at: str | None
    routing_outcome: str | None
    decision_summary: str | None
    model: str | None
    prompt_version: str | None
    evidence_json: str
    response_json: str | None


class LegacyAuditEvent(BaseModel):
    """Unversioned historical payloads and event names are not modern actions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    representation: Literal["legacy_v1"] = "legacy_v1"
    audit_event_id: int
    ticket_id: str
    run_id: str | None
    actor_type: str
    actor_id: str | None
    event_type: str
    occurred_at: str
    approval_status: str | None
    payload: JsonValue
