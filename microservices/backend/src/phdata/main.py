"""FastAPI application entry point."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from phdata.config import get_settings
from phdata.routes.router import api_router

app = FastAPI(title="ServeWell IT Support API", version="0.1.0")
app.include_router(api_router, prefix="/api")


def run() -> None:
    """Run the API with local development defaults."""
    settings = get_settings()
    uvicorn.run("phdata.main:app", host=settings.host, port=settings.port)