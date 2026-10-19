"""Agent-ready structured context, without contact fields or training labels."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoreContext(ContextModel):
    store_id: str
    store_name: str
    city: str
    neighborhood: str
    store_type: str


class AssetContext(ContextModel):
    asset_id: str
    store_id: str
    asset_type: str
    make: str
    model: str
    system_version: str | None
    serial_number: str | None
    install_date: str | None
    last_service_date: str | None
    warranty_expiry: str | None


class SLATarget(ContextModel):
    priority: str
    category: str
    first_response_minutes: int = Field(ge=0)
    resolution_target_minutes: int = Field(ge=0)
    escalation_to_l2_minutes: int = Field(ge=0)


class IncidentFacts(ContextModel):
    ticket_id: str
    reported_store_id: str | None
    reported_asset_id: str | None
    opened_at: str
    priority: str | None
    category: str | None
    subcategory: str | None
    status: str
    subject: str
    description: str
    system_version: str | None
    escalation_flag: bool


class HistoryEvent(ContextModel):
    event_id: int
    source_event_index: int
    event_at: str
    actor: str
    note: str


class RelatedReference(ContextModel):
    reported_related_ticket_id: str
    related_ticket_id: str | None
    persisted_related_ticket_id: str | None
    relation_type: str


class TargetDeadlines(ContextModel):
    first_response_at: AwareDatetime
    resolution_at: AwareDatetime
    escalation_to_l2_at: AwareDatetime


class ClockContext(ContextModel):
    now: AwareDatetime | None
    opened_at: AwareDatetime | None
    elapsed_since_open_minutes: float | None
    status_kind: Literal["open", "closed", "unknown"]
    open_target_deadlines: TargetDeadlines | None
    first_response_fulfillment: Literal["unknown"] = "unknown"
    fulfillment_note: str = (
        "History has no typed response or fulfillment events. Elapsed time and target "
        "deadlines do not establish a missed first response or SLA breach; closed "
        "incidents have no inferred overdue state."
    )


class IncidentContext(ContextModel):
    incident: IncidentFacts
    store: StoreContext | None
    asset: AssetContext | None
    sla: SLATarget | None
    history: list[HistoryEvent]
    related: list[RelatedReference]
    clock: ClockContext
    missing: list[str]
    warnings: list[str]
