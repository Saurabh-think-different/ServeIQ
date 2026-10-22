import asyncio

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.services import store


@pytest.fixture
def databases(tmp_path, monkeypatch):
    operational = create_engine(f"sqlite:///{tmp_path / 'operational.db'}")
    sandbox = create_engine(f"sqlite:///{tmp_path / 'sandbox.db'}")
    for engine, value in ((operational, "operational"), (sandbox, "sandbox")):
        with engine.begin() as db:
            db.execute(text("CREATE TABLE marker (value TEXT NOT NULL)"))
            db.execute(text("INSERT INTO marker VALUES (:value)"), {"value": value})
    monkeypatch.setattr(store, "engine", operational)
    monkeypatch.setattr(store, "_url", operational.url)
    monkeypatch.setattr(store, "_OperationalSession", sessionmaker(bind=operational))
    try:
        yield str(sandbox.url), str(operational.url)
    finally:
        operational.dispose()
        sandbox.dispose()


def read_marker():
    with store.SessionLocal() as db:
        return db.scalar(text("SELECT value FROM marker"))


def test_database_override_is_context_local_and_reaches_threads(databases):
    sandbox, _ = databases
    with store.use_database(sandbox):
        assert read_marker() == "sandbox"
        assert asyncio.run(asyncio.to_thread(read_marker)) == "sandbox"
        with store.operational_session() as db:
            assert db.scalar(text("SELECT value FROM marker")) == "operational"
    assert read_marker() == "operational"


def test_database_override_restored_after_failure(databases):
    sandbox, _ = databases
    with pytest.raises(RuntimeError), store.use_database(sandbox):
        assert read_marker() == "sandbox"
        raise RuntimeError("cancelled evaluation")
    assert read_marker() == "operational"


def test_cannot_evaluate_against_operational_database(databases):
    _, operational = databases
    with pytest.raises(ValueError, match="operational database"), store.use_database(operational):
        pytest.fail("Operational database accepted as an evaluation sandbox")
