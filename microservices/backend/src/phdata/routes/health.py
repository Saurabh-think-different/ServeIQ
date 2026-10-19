"""Health endpoint for orchestration and local smoke checks."""

import sqlite3

from fastapi import APIRouter, HTTPException

from phdata.config import get_settings
from phdata.db.database import open_database

router = APIRouter()


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", tags=["health"])
def readiness() -> dict[str, str]:
    database_path = get_settings().database_path
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = open_database(database_path)
        try:
            connection.execute("SELECT 1").fetchone()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ready", "database": "ok"}