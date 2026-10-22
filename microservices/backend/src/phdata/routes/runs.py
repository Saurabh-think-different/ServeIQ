"""Authenticated, synchronous support-run API."""

import sqlite3
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from phdata.auth import operator_settings
from phdata.config import Settings
from phdata.models.runs import RunPage, RunReadResult, RunRequest, RunResult
from phdata.repositories import incidents, runs
from phdata.repositories.runs import RunStateConflict
from phdata.routes.incidents import Database
from phdata.services.recommendations import OpenAIRecommendationAdapter
from phdata.services.workflow import IncidentNotFound, IdempotencyConflict, SupportWorkflow

router = APIRouter(tags=["runs"])


def support_workflow(
    settings: Annotated[Settings, Depends(operator_settings)],
) -> SupportWorkflow:
    recommender = OpenAIRecommendationAdapter(settings) if settings.generation_model else None
    return SupportWorkflow(settings, recommender=recommender)


Workflow = Annotated[SupportWorkflow, Depends(support_workflow)]


@router.post(
    "/incidents/{ticket_id:path}/runs",
    status_code=201,
    response_model=RunResult,
    name="start_incident_run",
)
def start_incident_run(
    ticket_id: str,
    body: RunRequest,
    request: Request,
    response: Response,
    workflow: Workflow,
    _database: Database,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> RunResult:
    try:
        result, replayed = workflow.start_run(ticket_id, idempotency_key=idempotency_key)
    except IncidentNotFound:
        raise HTTPException(status_code=404, detail="Incident not found") from None
    except IdempotencyConflict:
        raise HTTPException(status_code=409, detail="Idempotency key was already used for a different request") from None
    except ValueError as error:
        if "Idempotency-Key" in str(error):
            raise HTTPException(status_code=422, detail="Invalid Idempotency-Key header") from None
        raise HTTPException(status_code=503, detail="Support run storage is unavailable") from None
    except (sqlite3.Error, OSError, RunStateConflict):
        raise HTTPException(status_code=503, detail="Support run storage is unavailable") from None

    if replayed:
        response.status_code = 200
    response.headers["Location"] = str(request.base_url).rstrip("/") + "/api/runs/" + quote(result.run_id, safe="")
    return result


@router.get("/runs/{run_id}", response_model=RunReadResult, name="get_run")
def get_run(run_id: str, workflow: Workflow, _database: Database) -> RunReadResult:
    try:
        result = workflow.get_run(run_id)
    except (sqlite3.Error, OSError, ValueError):
        raise HTTPException(status_code=503, detail="Support run storage is unavailable") from None
    if result is None:
        raise HTTPException(status_code=404, detail="Support run not found")
    return result


@router.get("/incidents/{ticket_id:path}/runs", response_model=RunPage, name="list_incident_runs")
def list_incident_runs(
    ticket_id: str,
    workflow: Workflow,
    database: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> RunPage:
    if incidents.get_incident(database, ticket_id) is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    try:
        return workflow.list_runs(ticket_id, limit=limit, offset=offset)
    except (sqlite3.Error, OSError, ValueError):
        raise HTTPException(status_code=503, detail="Support run storage is unavailable") from None
