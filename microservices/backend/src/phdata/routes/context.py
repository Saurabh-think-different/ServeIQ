"""Exact, agent-ready incident facts without model calls."""

from fastapi import APIRouter, HTTPException

from phdata.models.context import IncidentContext
from phdata.repositories.support import SupportRepository
from phdata.routes.incidents import Database

router = APIRouter(tags=["context"])


@router.get("/incident-context/{ticket_id:path}", response_model=IncidentContext)
def incident_context(ticket_id: str, database: Database) -> IncidentContext:
    context = SupportRepository(database).get_context(ticket_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return context
