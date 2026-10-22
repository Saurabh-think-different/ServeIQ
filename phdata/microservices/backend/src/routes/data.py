"""Reference data and imported reports scoped to the signed-in manager."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import get_current_user
from src.models import Incident, User
from src.models.structured import SLA, Asset, HistoricalTicket, RegionManager, Store, SystemVersion
from src.services.store import get_db

router = APIRouter(prefix="/data", tags=["reference data"])
DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/me")
def manager_profile(user: CurrentUser, db: DB):
    manager = db.get(RegionManager, user.id)
    stores = db.scalars(
        select(Store).where(Store.manager_user_id == user.id).order_by(Store.store_id)
    ).all()
    return {"name": manager.name if manager else None, "email": user.email, "stores": stores}


@router.get("/stores")
def stores(user: CurrentUser, db: DB, limit: Limit = 50, offset: Offset = 0):
    return db.scalars(
        select(Store)
        .where(Store.manager_user_id == user.id)
        .order_by(Store.store_id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/assets")
def assets(
    user: CurrentUser,
    db: DB,
    store_id: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    query = (
        select(Asset)
        .join(Store, Store.store_id == Asset.store_id)
        .where(Store.manager_user_id == user.id)
    )
    if store_id:
        query = query.where(Store.store_id == store_id)
    return db.scalars(query.order_by(Asset.asset_id).offset(offset).limit(limit)).all()


@router.get("/sla")
def sla(user: CurrentUser, db: DB, priority: str | None = None, category: str | None = None):
    query = select(SLA)
    if priority:
        query = query.where(SLA.priority == priority)
    if category:
        query = query.where(SLA.category == category)
    return db.scalars(query.order_by(SLA.priority, SLA.category)).all()


@router.get("/system-versions")
def versions(user: CurrentUser, db: DB, system: str | None = None):
    query = select(SystemVersion)
    if system:
        query = query.where(SystemVersion.system == system)
    return db.scalars(query.order_by(SystemVersion.system, SystemVersion.version)).all()


@router.get("/tickets")
def historical_tickets(user: CurrentUser, db: DB, limit: Limit = 50, offset: Offset = 0):
    return db.scalars(
        select(HistoricalTicket)
        .join(Incident, Incident.id == HistoricalTicket.incident_id)
        .where(Incident.user_id == user.id)
        .order_by(HistoricalTicket.ticket_id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/tickets/{ticket_id}")
def historical_ticket(ticket_id: str, user: CurrentUser, db: DB):
    ticket = db.scalar(
        select(HistoricalTicket)
        .join(Incident, Incident.id == HistoricalTicket.incident_id)
        .where(HistoricalTicket.ticket_id == ticket_id, Incident.user_id == user.id)
    )
    if ticket is None:
        raise HTTPException(404, "Historical ticket not found")
    return ticket
