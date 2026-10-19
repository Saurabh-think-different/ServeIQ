"""Operator-only bounded knowledge search; the parent mounts this under /api."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from phdata.auth import operator_settings
from phdata.config import Settings
from phdata.models.retrieval import SearchRequest, SearchResponse
from phdata.services.retrieval import KnowledgeRetrieval, RetrievalError

router = APIRouter()


def retrieval_service(
    settings: Annotated[Settings, Depends(operator_settings)],
) -> KnowledgeRetrieval:
    return KnowledgeRetrieval(settings)


@router.post("/knowledge/search", tags=["knowledge"], response_model=SearchResponse)
def search_knowledge(
    request: SearchRequest,
    service: Annotated[KnowledgeRetrieval, Depends(retrieval_service)],
) -> SearchResponse:
    try:
        return service.search(request)
    except RetrievalError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
