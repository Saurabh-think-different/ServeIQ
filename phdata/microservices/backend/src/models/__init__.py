"""Persistent domain models shared by HTTP routes and agent services."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class IncidentStatus(str, Enum):
    open = "open"
    running = "running"
    waiting_for_user = "waiting_for_user"
    resolved = "resolved"
    escalated = "escalated"
    failed = "failed"


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"


class EventType(str, Enum):
    thought = "thought"
    action = "action"
    action_input = "action_input"
    observation = "observation"


def enum_column(enum: type[Enum]) -> SAEnum:
    return SAEnum(
        enum,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[IncidentStatus] = mapped_column(
        enum_column(IncidentStatus), default=IncidentStatus.open
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    role: Mapped[MessageRole] = mapped_column(enum_column(MessageRole))
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    type: Mapped[EventType] = mapped_column(enum_column(EventType))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    sequence: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


__all__ = [
    "Base",
    "Event",
    "EventType",
    "Incident",
    "IncidentStatus",
    "Message",
    "MessageRole",
    "User",
]
