"""Operator-only HTTP entry point for the configured dataset."""

from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from phdata.auth import operator_settings
from phdata.config import Settings
from phdata.ingestion.interfaces import IngestionStage, StageName
from phdata.ingestion.knowledge import KnowledgeIngestion
from phdata.ingestion.pipeline import IngestionRequest, PipelineReport, run_pipeline
from phdata.ingestion.structured import StructuredIngestion

router = APIRouter()
_ingestion_lock = Lock()


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
