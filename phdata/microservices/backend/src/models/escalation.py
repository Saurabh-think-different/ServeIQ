"""Auditable simulated L2 escalations; no dispatch is performed."""

from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base, utc_now


def escalation_id_for(incident_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"phdata:l2-escalation:{incident_id}"))


class L2Escalation(Base):
    __tablename__ = "l2_escalations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    reason_code: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    attempted_steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    citations: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_information: Mapped[list[str]] = mapped_column(JSON, default=list)
    conversation_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    support_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dispatch_status: Mapped[str] = mapped_column(String(20), default="simulated")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


__all__ = ["L2Escalation", "escalation_id_for"]
