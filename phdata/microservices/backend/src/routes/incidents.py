"""User-owned incident creation and retrieval."""

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.models import Incident, IncidentStatus
from src.routes.users import DB, CurrentUser
from src.services.store import require_incident

router = APIRouter(prefix="/incidents", tags=["incidents"])


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=50000)


class IncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    title: str
    description: str
    status: IncidentStatus
    created_at: datetime


@router.post("", response_model=IncidentResponse, status_code=201)
def create_incident(body: IncidentCreate, db: DB, user: CurrentUser):
    incident = Incident(user_id=user.id, **body.model_dump())
    db.add(incident)
    db.commit()
    return incident


@router.get("", response_model=list[IncidentResponse])
def list_incidents(
    db: DB,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
):
    return db.scalars(
        select(Incident)
        .where(Incident.user_id == user.id)
        .order_by(Incident.created_at.desc(), Incident.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/{incident_id}", response_model=IncidentResponse)
def get_incident(incident_id: str, db: DB, user: CurrentUser):
    return require_incident(db, incident_id, user.id)
