"""Authenticated proposal decisions and bounded audit reads."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from phdata.auth import operator_settings
from phdata.config import Settings
from phdata.db.database import open_database
from phdata.models.actions import (
    ActionDecisionRequest,
    ActionDecisionResult,
    ActionProposalList,
    AuditPage,
)
from phdata.repositories.actions import ActionStateConflict
from phdata.routes.incidents import Database
from phdata.services.actions import ActionConflict, ActionNotFound, SupportActions

router = APIRouter(tags=["actions", "audit"])


def support_actions(
    settings: Annotated[Settings, Depends(operator_settings)],
) -> SupportActions:
    def connection_factory():
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        return open_database(settings.database_path)

    return SupportActions(connection_factory)


Actions = Annotated[SupportActions, Depends(support_actions)]


@router.get("/runs/{run_id}/actions", response_model=ActionProposalList)
def list_run_actions(run_id: str, actions: Actions, _database: Database) -> ActionProposalList:
    try:
        return actions.proposals_for_run(run_id)
    except ActionNotFound:
        raise HTTPException(status_code=404, detail="Support run not found") from None
    except (sqlite3.Error, OSError, ValueError):
        raise HTTPException(status_code=503, detail="Action storage is unavailable") from None


@router.post("/actions/{action_id}/decision", response_model=ActionDecisionResult)
def decide_action(
    action_id: str,
    decision: ActionDecisionRequest,
    actions: Actions,
    _database: Database,
) -> ActionDecisionResult:
    try:
        result, _replayed = actions.decide(action_id, decision)
        return result
    except ActionNotFound:
        raise HTTPException(status_code=404, detail="Action proposal not found") from None
    except (ActionConflict, ActionStateConflict):
        raise HTTPException(status_code=409, detail="Action proposal is stale or no longer pending") from None
    except (sqlite3.Error, OSError, ValueError):
        raise HTTPException(status_code=503, detail="Action storage is unavailable") from None


@router.get("/incidents/{ticket_id:path}/audit", response_model=AuditPage)
def list_incident_audit(
    ticket_id: str,
    actions: Actions,
    _database: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> AuditPage:
    try:
        return actions.audit_for_incident(ticket_id, limit=limit, offset=offset)
    except ActionNotFound:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    except (sqlite3.Error, OSError, ValueError):
        raise HTTPException(status_code=503, detail="Audit storage is unavailable") from None
