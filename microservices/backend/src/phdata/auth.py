"""Shared local operator boundary; not a production identity system."""

from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from phdata.config import Settings, get_settings


def operator_settings(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Settings:
    if not settings.ingestion_token:
        raise HTTPException(status_code=503, detail="Operator API is disabled; configure INGESTION_TOKEN")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not authorization or not authorization.startswith("Bearer ") or not compare_digest(
        supplied.encode(), settings.ingestion_token.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid operator credentials", headers={"WWW-Authenticate": "Bearer"})
    return settings
