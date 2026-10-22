"""Authenticated incident agent endpoints (tool/status SSE, not token streaming)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from auth import get_current_user
from config import get_settings
from src.models import User
from src.services.llm import adk_events, configured_models, prepare_run
from src.services.store import get_db, require_incident

router = APIRouter(prefix="/llm", tags=["llm"])


class StreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str = Field(min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=128)


def get_event_source():
    """Override this dependency with an async-generator factory for offline tests."""
    return adk_events


@router.get("/models")
def models(
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Any, Depends(get_settings)],
):
    default, allowed = configured_models(settings)
    return {"default_model": default, "allowed_models": allowed}


@router.post("/stream")
def stream_incident(
    body: StreamRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Any, Depends(get_settings)],
    event_source: Annotated[Any, Depends(get_event_source)],
):
    incident = require_incident(db, body.incident_id, user.id)
    run = prepare_run(db, incident, settings, body.model, event_source)
    return StreamingResponse(
        run.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        background=BackgroundTask(run.close),
    )
