"""Shared local operator boundary; not a production identity system."""

from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from phdata.config import Settings, get_settings

operator_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="OperatorBearer",
    description="Enter the operator token only; Swagger adds the Bearer prefix.",
)


def operator_settings(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(operator_bearer)],
) -> Settings:
    if not settings.ingestion_token:
        raise HTTPException(status_code=503, detail="Operator API is disabled; configure INGESTION_TOKEN")
    if not credentials or not compare_digest(
        credentials.credentials.encode(), settings.ingestion_token.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid operator credentials", headers={"WWW-Authenticate": "Bearer"})
    return settings
