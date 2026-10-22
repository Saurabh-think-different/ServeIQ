"""Authenticated runtime incident intake and read API; mounted under /api."""

import sqlite3
from typing import Annotated, Iterator
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from phdata.auth import operator_settings
from phdata.config import Settings
from phdata.db.database import open_database
from phdata.models.incidents import IncidentCreate, IncidentPage, IncidentResult
from phdata.repositories import incidents

router = APIRouter(tags=["incidents"])


def incident_database(
    settings: Annotated[Settings, Depends(operator_settings)],
) -> Iterator[sqlite3.Connection]:
    """Own one connection for this request, including rollback-on-close cleanup."""
    connection = None
    try:
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = open_database(settings.database_path)
        yield connection
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="Incident storage is unavailable") from None
    finally:
        if connection is not None:
            connection.close()


Database = Annotated[sqlite3.Connection, Depends(incident_database)]


@router.post("/incidents", status_code=201, response_model=IncidentResult)
def create_incident(intake: IncidentCreate, request: Request, response: Response, database: Database) -> IncidentResult:
    try:
        result = incidents.create_incident(database, intake)
    except incidents.DuplicateIncident:
        raise HTTPException(status_code=409, detail="Incident ticket ID already exists") from None
    location = str(request.url_for("get_incident", ticket_id="__ticket_id__"))
    response.headers["Location"] = location.replace("__ticket_id__", quote(result.incident.ticket_id, safe=""))
    return result


@router.get("/incidents", response_model=IncidentPage)
def list_incidents(
    database: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: str | None = None,
    category: str | None = None,
) -> IncidentPage:
    return incidents.list_incidents(database, limit=limit, offset=offset, status=status, category=category)


@router.get("/incidents/{ticket_id:path}", response_model=IncidentResult, name="get_incident")
def get_incident(ticket_id: str, database: Database) -> IncidentResult:
    result = incidents.get_incident(database, ticket_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return result
