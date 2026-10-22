"""Only user messages may be created through the public API."""

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.models import Message, MessageRole
from src.routes.users import DB, CurrentUser
from src.services.store import require_incident

router = APIRouter(prefix="/incidents/{incident_id}/messages", tags=["messages"])


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=50000)


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    incident_id: str
    role: MessageRole
    content: str
    model: str | None
    created_at: datetime


@router.post("", response_model=MessageResponse, status_code=201)
def create_message(incident_id: str, body: MessageCreate, db: DB, user: CurrentUser):
    require_incident(db, incident_id, user.id)
    message = Message(incident_id=incident_id, role=MessageRole.user, content=body.content)
    db.add(message)
    db.commit()
    return message


@router.get("", response_model=list[MessageResponse])
def list_messages(
    incident_id: str,
    db: DB,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
):
    require_incident(db, incident_id, user.id)
    return db.scalars(
        select(Message)
        .where(Message.incident_id == incident_id)
        .order_by(Message.created_at, Message.id)
        .offset(offset)
        .limit(limit)
    ).all()
