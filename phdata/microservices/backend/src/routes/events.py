"""Immutable service-generated event history."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from src.models import Event, EventType, Incident, Message
from src.routes.users import DB, CurrentUser

router = APIRouter(prefix="/messages/{message_id}/events", tags=["events"])


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    message_id: str
    type: EventType
    data: dict[str, Any]
    sequence: int
    created_at: datetime


@router.get("", response_model=list[EventResponse])
def list_events(
    message_id: str,
    db: DB,
    user: CurrentUser,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=1000000),
):
    owned = db.scalar(
        select(Message.id)
        .join(Incident, Message.incident_id == Incident.id)
        .where(Message.id == message_id, Incident.user_id == user.id)
    )
    if owned is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return db.scalars(
        select(Event)
        .where(Event.message_id == message_id)
        .order_by(Event.sequence, Event.created_at, Event.id)
        .offset(offset)
        .limit(limit)
    ).all()
