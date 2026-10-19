"""Validated source entities; extra source metadata (including labels) is ignored."""

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict,
    Field, StringConstraints, TypeAdapter, field_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Priority = Literal["P1", "P2", "P3", "P4"]
NonNegativeInt = Annotated[int, Field(ge=0)]


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]
_aware_datetime = TypeAdapter(AwareDatetime)


def _timestamp(value: str) -> str:
    _aware_datetime.validate_python(value)
    return value


# Validate an offset-aware ISO timestamp while retaining its original spelling/offset.
Timestamp = Annotated[
    str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}[Tt ]"), AfterValidator(_timestamp),
]


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Store(SourceModel):
    store_id: Text
    store_name: Text
    city: Text
    neighborhood: Text
    store_type: Literal["corporate", "franchise"]
    region_manager: OptionalText = None
    support_phone: OptionalText = None


class Asset(SourceModel):
    asset_id: Text
    store_id: Text
    asset_type: Text
    make: Text
    model: Text
    system_version: OptionalText = None
    serial_number: OptionalText = None
    install_date: OptionalText = None
    last_service_date: OptionalText = None
    warranty_expiry: OptionalText = None


class SLATarget(SourceModel):
    priority: Priority
    category: Text
    first_response_minutes: NonNegativeInt
    resolution_target_minutes: NonNegativeInt
    escalation_to_l2_minutes: NonNegativeInt


class ManifestEntry(SourceModel):
    ticket_id: Text
    filename: Text

    @field_validator("filename")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in value:
            raise ValueError("expected a safe relative ticket path")
        return path.as_posix()


class HistoryEvent(SourceModel):
    timestamp: Timestamp
    actor: Text
    note: Text


class Ticket(SourceModel):
    ticket_id: Text
    store_id: Text
    date_opened: Timestamp
    status: Text
    subject: Text
    description: Text
    asset_id: OptionalText = None
    submitted_by: OptionalText = None
    contact_phone: OptionalText = None
    priority: Annotated[Priority | None, BeforeValidator(_blank_to_none)] = None
    category: OptionalText = None
    subcategory: OptionalText = None
    system_version: OptionalText = None
    resolution_notes: OptionalText = None
    escalation_flag: bool = False
    tags: list[str] = Field(default_factory=list)
    ticket_history: list[HistoryEvent] = Field(default_factory=list)
    related_tickets: list[Text] = Field(default_factory=list)

    @field_validator("tags", "ticket_history", "related_tickets", mode="before")
    @classmethod
    def null_list(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("related_tickets")
    @classmethod
    def unique_relations(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate related ticket ID")
        return value
