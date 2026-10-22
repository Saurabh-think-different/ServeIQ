"""Synchronous database sessions and ownership checks."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from src.models import Base, Incident

_url = make_url(settings.database_url)
_options = {}
if _url.get_backend_name() == "sqlite":
    _options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    if not _url.database or _url.database == ":memory:":
        _options["poolclass"] = StaticPool
engine = create_engine(settings.database_url, **_options)
if _url.get_backend_name() == "sqlite":

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if _url.database and _url.database != ":memory:":
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


_OperationalSession = sessionmaker(bind=engine, expire_on_commit=False)
_active_session: ContextVar[sessionmaker[Session] | None] = ContextVar(
    "active_database_session", default=None
)


def SessionLocal() -> Session:
    factory = _active_session.get()
    return (factory if factory is not None else _OperationalSession)()


def operational_session() -> Session:
    return _OperationalSession()


@contextmanager
def use_database(database_url: str):
    """Isolate evaluation calls, including asyncio.to_thread lookups, from live data."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise ValueError("Evaluation databases must be file-backed SQLite")
    if _url.database and Path(url.database).resolve() == Path(_url.database).resolve():
        raise ValueError("Evaluation cannot use the operational database")
    scoped_engine = create_engine(
        database_url, connect_args={"check_same_thread": False, "timeout": 30}
    )

    @event.listens_for(scoped_engine, "connect")
    def configure_connection(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    factory = sessionmaker(bind=scoped_engine, expire_on_commit=False)
    token = _active_session.set(factory)
    try:
        yield scoped_engine
    finally:
        _active_session.reset(token)
        scoped_engine.dispose()


def init_db() -> None:
    if _url.get_backend_name() == "sqlite" and _url.database and _url.database != ":memory:":
        Path(_url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as db:
        yield db


def require_incident(db: Session, incident_id: str, user_id: str) -> Incident:
    incident = db.scalar(
        select(Incident).where(Incident.id == incident_id, Incident.user_id == user_id)
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident
