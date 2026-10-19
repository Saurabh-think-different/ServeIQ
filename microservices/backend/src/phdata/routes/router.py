"""Top-level API router."""

from fastapi import APIRouter

from phdata.routes.actions import router as actions_router
from phdata.routes.context import router as context_router
from phdata.routes.health import router as health_router
from phdata.routes.incidents import router as incidents_router
from phdata.routes.ingestion import router as ingestion_router
from phdata.routes.knowledge import router as knowledge_router
from phdata.routes.runs import router as runs_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(ingestion_router)
api_router.include_router(runs_router)
api_router.include_router(actions_router)
api_router.include_router(incidents_router)
api_router.include_router(context_router)
api_router.include_router(knowledge_router)
