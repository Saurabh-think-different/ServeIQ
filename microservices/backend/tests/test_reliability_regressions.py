"""Real HTTP concurrency regressions for request-owned SQLite connections."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sqlite3
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from phdata.db.database import open_database
from phdata.main import app


@pytest.fixture
def database_path(monkeypatch):
    directory = Path(__file__).parent / f".reliability-{uuid4().hex}"
    directory.mkdir()
    path = directory / "state.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("INGESTION_TOKEN", "reliability-token")
    monkeypatch.delenv("GENERATION_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        yield path
    finally:
        shutil.rmtree(directory)


AUTH = {"Authorization": "Bearer reliability-token"}


def test_concurrent_http_reads_and_writes_keep_connections_request_owned(database_path, monkeypatch):
    import phdata.routes.incidents as route

    opened = []

    def tracked_open(path):
        connection = open_database(path)
        opened.append(connection)
        return connection

    monkeypatch.setattr(route, "open_database", tracked_open)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/incidents", headers=AUTH, json={
            "ticket_id": "RANCHER", "description": "Rancher is unavailable",
        })
        assert response.status_code == 201
        paths = ["/api/incidents", "/api/incidents/RANCHER", "/api/incident-context/RANCHER"]
        ready = Barrier(30)

        def read(index):
            ready.wait()
            return client.get(paths[index % 3], headers=AUTH)

        with ThreadPoolExecutor(max_workers=30) as pool:
            reads = list(pool.map(read, range(30)))
            writes = list(pool.map(lambda index: client.post("/api/incidents", headers=AUTH, json={
                "ticket_id": f"CONCURRENT-{index}", "description": f"Concurrent report {index}",
            }), range(20)))
            duplicates = list(pool.map(lambda _: client.post("/api/incidents", headers=AUTH, json={
                "ticket_id": "RANCHER", "description": "Duplicate report",
            }), range(10)))
        assert [response.status_code for response in reads] == [200] * 30
        assert [response.status_code for response in writes] == [201] * 20
        assert [response.status_code for response in duplicates] == [409] * 10
        page = client.get("/api/incidents?limit=100", headers=AUTH)
        assert page.status_code == 200 and page.json()["total"] == 21
    assert len(opened) == 62
    assert len({id(connection) for connection in opened}) == len(opened)
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_concurrent_first_open_initializes_schema_once(database_path):
    barrier = Barrier(12)

    def first_open(_):
        barrier.wait()
        connection = open_database(database_path)
        try:
            return connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert list(pool.map(first_open, range(12))) == [2] * 12


def test_request_connection_can_move_workers_but_does_not_commit_unfinished_work(database_path):
    connection = open_database(database_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(connection.execute, "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
                    "VALUES ('UNCOMMITTED', 'Store', 'City', 'Area', 'corporate')").result()
        pool.submit(connection.close).result()
    check = open_database(database_path)
    try:
        assert check.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 0
    finally:
        check.close()
