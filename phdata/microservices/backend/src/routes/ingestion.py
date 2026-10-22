"""Privileged, bounded dataset ingestion and repeatable demo provisioning."""

import fcntl
import hmac
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import APIKeyHeader
from openai import APIConnectionError, APIStatusError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import distinct, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from config import Settings, get_settings
from src.models import Base, User
from src.models.escalation import L2Escalation
from src.models.structured import HistoricalTicket, RegionManager, Store
from src.services.kb_ingestion import KnowledgeIndexError, ingest_kb_dataset, ingest_kb_document
from src.services.store import get_db
from src.services.structured_ingestion import (
    ingest_csv,
    ingest_structured_dataset,
    ingest_tickets,
)

router = APIRouter(prefix="/ingestion", tags=["ingestion"])
_key_header = APIKeyHeader(name="X-Ingestion-Key", auto_error=False)
DB = Annotated[Session, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]


def require_ingestion_key(
    key: Annotated[str | None, Depends(_key_header)], settings: Config
) -> None:
    if not settings.ingestion_api_key:
        raise HTTPException(503, "Ingestion is disabled until INGESTION_API_KEY is configured")
    if not key or not hmac.compare_digest(key.encode(), settings.ingestion_api_key.encode()):
        raise HTTPException(401, "Invalid ingestion key")


router.dependencies.append(Depends(require_ingestion_key))


@contextmanager
def ingestion_lock(settings: Settings):
    database = make_url(settings.database_url).database
    directory = Path(database).parent if database and database != ":memory:" else Path("data")
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".ingestion.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise HTTPException(
                409, "Another ingestion is running; retry when it finishes"
            ) from error
        try:
            yield
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except KnowledgeIndexError as error:
            raise HTTPException(503, str(error)) from error
        except APIStatusError as error:
            raise HTTPException(
                502, {"code": "embedding_provider_error", "provider_status": error.status_code}
            ) from error
        except APIConnectionError as error:
            raise HTTPException(
                503, "Embedding provider is unavailable; retry ingestion"
            ) from error
        except (ResponseHandlingException, UnexpectedResponse) as error:
            raise HTTPException(503, "Vector store is unavailable; retry ingestion") from error
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def read_upload(file: UploadFile, settings: Settings) -> bytes:
    content = file.file.read(settings.ingestion_max_bytes + 1)
    if not content:
        raise HTTPException(422, "The uploaded file is empty")
    if len(content) > settings.ingestion_max_bytes:
        raise HTTPException(413, "The uploaded file exceeds INGESTION_MAX_BYTES")
    return content


def dataset_root(settings: Settings) -> Path:
    root = settings.dataset_root.resolve()
    if not root.is_dir():
        raise HTTPException(503, "Configured DATASET_ROOT is not available")
    return root


def checked_knowledge_result(result: dict) -> dict:
    if result.get("failed"):
        summary = {key: value for key, value in result.items() if key != "documents"}
        raise HTTPException(502, summary)
    return result


@router.post("/structured")
def structured_dataset(db: DB, settings: Config):
    with ingestion_lock(settings):
        return ingest_structured_dataset(db, dataset_root(settings))


@router.post("/structured/{table}")
def structured_upload(
    table: Literal["stores", "assets", "sla_matrix", "system_versions"],
    db: DB,
    settings: Config,
    file: Annotated[UploadFile, File()],
):
    content = read_upload(file, settings)
    with ingestion_lock(settings):
        return ingest_csv(db, table, content)


@router.post("/tickets")
def ticket_upload(db: DB, settings: Config, file: Annotated[UploadFile, File()]):
    content = read_upload(file, settings)
    with ingestion_lock(settings):
        return ingest_tickets(db, content)


@router.post("/kb")
def knowledge_dataset(db: DB, settings: Config):
    with ingestion_lock(settings):
        return checked_knowledge_result(ingest_kb_dataset(db, dataset_root(settings), settings))


@router.post("/kb/document")
def knowledge_upload(
    db: DB,
    settings: Config,
    file: Annotated[UploadFile, File()],
    source: Annotated[str | None, Form(max_length=300)] = None,
):
    path = PurePosixPath(source or file.filename or "")
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() != ".md"
        or "\\" in str(path)
    ):
        raise HTTPException(422, "Source must be a relative Markdown path without traversal")
    content = read_upload(file, settings)
    with ingestion_lock(settings):
        return ingest_kb_document(db, str(path), content, settings)


@router.post("/all")
def all_datasets(db: DB, settings: Config):
    with ingestion_lock(settings):
        root = dataset_root(settings)
        structured = ingest_structured_dataset(db, root)
        knowledge = checked_knowledge_result(ingest_kb_dataset(db, root, settings))
        return {"structured": structured, "knowledge": knowledge}


@router.get("/status")
def ingestion_status(db: DB, settings: Config):
    return {
        "tables": {
            name: db.scalar(select(func.count()).select_from(table))
            for name, table in Base.metadata.tables.items()
            if name not in {"users", "messages", "events"}
        },
        "embedding_model": settings.embedding_model,
        "qdrant_collection": settings.qdrant_collection,
    }


@router.get("/l2-stats")
def global_l2_stats(db: DB):
    reasons = db.execute(
        select(L2Escalation.reason_code, func.count()).group_by(L2Escalation.reason_code)
    ).all()
    return {
        "total": sum(count for _, count in reasons),
        "by_reason": dict(reasons),
        "dispatch_status": "simulated",
        "counts_unique_incidents": True,
    }


@router.get("/managers")
def managers(db: DB):
    rows = db.execute(
        select(
            RegionManager.name,
            User.email,
            User.id,
            func.count(distinct(Store.store_id)),
            func.count(distinct(HistoricalTicket.ticket_id)),
        )
        .join(User, User.id == RegionManager.user_id)
        .outerjoin(Store, Store.manager_user_id == RegionManager.user_id)
        .outerjoin(HistoricalTicket, HistoricalTicket.store_id == Store.store_id)
        .group_by(RegionManager.name, User.email, User.id)
        .order_by(RegionManager.name)
    ).all()
    return [
        {
            "name": name,
            "email": email,
            "user_id": user_id,
            "stores": stores,
            "historical_incidents": tickets,
        }
        for name, email, user_id, stores, tickets in rows
    ]
