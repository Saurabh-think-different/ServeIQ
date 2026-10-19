"""Operator-only HTTP entry point for the configured dataset."""

from secrets import compare_digest
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from phdata.config import Settings, get_settings
from phdata.ingestion.interfaces import IngestionStage, StageName
from phdata.ingestion.knowledge import KnowledgeIngestion
from phdata.ingestion.pipeline import IngestionRequest, PipelineReport, run_pipeline
from phdata.ingestion.structured import StructuredIngestion

router = APIRouter()
_ingestion_lock = Lock()


def operator_settings(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Settings:
    if not settings.ingestion_token:
        raise HTTPException(status_code=503, detail="Ingestion is disabled; configure INGESTION_TOKEN")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not authorization or not authorization.startswith("Bearer ") or not compare_digest(
        supplied.encode(), settings.ingestion_token.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid operator credentials", headers={"WWW-Authenticate": "Bearer"})
    return settings


def ingestion_stages(
    settings: Annotated[Settings, Depends(operator_settings)],
) -> dict[StageName, IngestionStage]:
    return {"structured": StructuredIngestion(settings), "knowledge": KnowledgeIngestion(settings)}


@router.post("/ingestions", tags=["ingestion"], response_model=PipelineReport)
def ingest(
    request: IngestionRequest,
    stages: Annotated[dict[StageName, IngestionStage], Depends(ingestion_stages)],
) -> PipelineReport:
    if not _ingestion_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="An ingestion is already running")
    try:
        return run_pipeline(request.target, stages)
    finally:
        _ingestion_lock.release()
