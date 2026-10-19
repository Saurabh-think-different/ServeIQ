"""Strict runtime incident intake and public incident representations."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from phdata.ingestion.schemas import Timestamp


class IncidentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentHistory(IncidentModel):
    timestamp: Timestamp
    actor: str = Field(min_length=1)
    note: str = Field(min_length=1)

    @field_validator("actor", "note")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class IncidentCreate(IncidentModel):
    description: str = Field(min_length=1)
    ticket_id: str | None = Field(default=None, min_length=1)
    subject: str | None = Field(default=None, min_length=1)
    store_id: str | None = Field(default=None, min_length=1)
    asset_id: str | None = Field(default=None, min_length=1)
    priority: Literal["P1", "P2", "P3", "P4"] | None = None
    category: str | None = None
    subcategory: str | None = None
    system_version: str | None = None
    status: str | None = None
    opened_at: Timestamp = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    history: list[IncidentHistory] = Field(default_factory=list)

    @field_validator("description", "ticket_id", "subject", "store_id", "asset_id")
    @classmethod
    def meaningful_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("text must not be blank")
        return value


class Incident(IncidentModel):
    ticket_id: str
    reported_store_id: str | None
    store_id: str | None
    reported_asset_id: str | None
    asset_id: str | None
    opened_at: Timestamp
    priority: Literal["P1", "P2", "P3", "P4"] | None
    category: str | None
    subcategory: str | None
    status: str
    subject: str
    description: str
    system_version: str | None
    history: list[IncidentHistory] = Field(default_factory=list)


class IncidentResult(IncidentModel):
    incident: Incident
    warnings: list[str] = Field(default_factory=list)


class IncidentPage(IncidentModel):
    incidents: list[Incident]
    total: int
    limit: Annotated[int, Field(ge=1, le=100)]
    offset: Annotated[int, Field(ge=0)]
