"""Top-level API router."""

from fastapi import APIRouter

from phdata.routes.health import router as health_router
from phdata.routes.ingestion import router as ingestion_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(ingestion_router)