"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import sqlite3

import uvicorn
from fastapi import FastAPI

from phdata.config import get_settings
from phdata.db.database import open_database
from phdata.routes.router import api_router
from phdata.services.workflow import recover_interrupted_runs

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.database_path.is_file():
        connection = None
        try:
            connection = open_database(settings.database_path)
            connection.execute("BEGIN IMMEDIATE")
            recover_interrupted_runs(connection, now=datetime.now(timezone.utc))
            connection.commit()
        except (sqlite3.Error, OSError, ValueError) as error:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            logger.warning(
                "Startup recovery of interrupted support runs did not complete (%s)",
                type(error).__name__,
            )
        finally:
            if connection is not None:
                connection.close()
    yield


app = FastAPI(title="ServeWell IT Support API", version="0.1.0", lifespan=lifespan)
app.include_router(api_router, prefix="/api")


def run() -> None:
    """Run the API with local development defaults."""
    settings = get_settings()
    uvicorn.run("phdata.main:app", host=settings.host, port=settings.port)