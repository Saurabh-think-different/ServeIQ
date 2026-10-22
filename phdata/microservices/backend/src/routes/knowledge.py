"""Authenticated retrieval of cited parent context from child-vector matches."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from openai import APIConnectionError, APIStatusError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy.orm import Session

from auth import get_current_user
from config import Settings, get_settings
from src.models import User
from src.services.kb_ingestion import KnowledgeIndexError, search_kb
from src.services.store import get_db

router = APIRouter(prefix="/kb", tags=["knowledge base"])


@router.get("/search")
def search(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    query: Annotated[str, Query(min_length=3, max_length=2000)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
):
    if not query.strip():
        raise HTTPException(422, "Query must contain text")
    try:
        return search_kb(db, query.strip(), limit, settings)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except KnowledgeIndexError as error:
        raise HTTPException(503, str(error)) from error
    except APIStatusError as error:
        raise HTTPException(
            502, {"code": "embedding_provider_error", "provider_status": error.status_code}
        ) from error
    except APIConnectionError as error:
        raise HTTPException(503, "Embedding provider is unavailable") from error
    except (ResponseHandlingException, UnexpectedResponse) as error:
        raise HTTPException(503, "Vector store is unavailable") from error
