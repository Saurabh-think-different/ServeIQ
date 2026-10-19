from datetime import datetime, timezone
from fastapi.testclient import TestClient
import pytest

from phdata.config import get_settings
from phdata.db.database import open_database
from phdata.main import app
from phdata.repositories.runs import insert_run

TOKEN = "test-run-api-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch, tmp_path):
    database_path = tmp_path / "state" / "servewell.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("INGESTION_TOKEN", TOKEN)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GENERATION_MODEL", raising=False)
    with TestClient(app) as test_client:
        yield test_client, database_path


def test_run_api_creates_replays_reads_and_lists(client):
    test_client, database_path = client
    created = test_client.post(
        "/api/incidents",
        headers=AUTH,
        json={"ticket_id": "INC-RUN-1", "description": "Printer is offline"},
    )
    assert created.status_code == 201

    start = test_client.post(
        "/api/incidents/INC-RUN-1/runs",
        headers={**AUTH, "Idempotency-Key": "new-run-1"},
        json={},
    )
    assert start.status_code == 201
    assert start.headers["Location"].endswith(f"/api/runs/{start.json()['run_id']}")
    assert start.json()["status"] == "completed"
    assert start.json()["routing_outcome"] == "needs_clarification"
    assert start.json()["decision_mechanism"] == "deterministic"

    replay = test_client.post(
        "/api/incidents/INC-RUN-1/runs",
        headers={**AUTH, "Idempotency-Key": "new-run-1"},
        json={},
    )
    assert replay.status_code == 200
    assert replay.json() == start.json()
    run_id = start.json()["run_id"]
    assert test_client.get(f"/api/runs/{run_id}", headers=AUTH).json() == start.json()
    page = test_client.get("/api/incidents/INC-RUN-1/runs?limit=20&offset=0", headers=AUTH)
    assert page.status_code == 200 and page.json()["total"] == 1
    connection = open_database(database_path)
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 3
    assert connection.execute("SELECT COUNT(*) FROM action_proposals").fetchone()[0] == 1
    connection.close()


def test_run_api_auth_validation_and_unknown_resources(client):
    test_client, database_path = client
    assert test_client.get("/api/runs/not-found").status_code == 401

    missing_header = test_client.post("/api/incidents/UNKNOWN/runs", headers=AUTH, json={})
    assert missing_header.status_code == 422
    unknown = test_client.post(
        "/api/incidents/UNKNOWN/runs",
        headers={**AUTH, "Idempotency-Key": "key"},
        json={},
    )
    assert unknown.status_code == 404
    extra = test_client.post(
        "/api/incidents/UNKNOWN/runs",
        headers={**AUTH, "Idempotency-Key": "key"},
        json={"prompt": "caller supplied instructions"},
    )
    assert extra.status_code == 422
    assert test_client.get("/api/incidents/UNKNOWN/runs", headers=AUTH).status_code == 404
    assert test_client.get("/api/runs/not-found", headers=AUTH).status_code == 404
    connection = open_database(database_path)
    assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    connection.close()


def test_run_routes_are_in_openapi_and_page_bounds_are_enforced(client):
    test_client, _ = client
    paths = test_client.get("/openapi.json").json()["paths"]
    assert "/api/incidents/{ticket_id}/runs" in paths
    assert "/api/runs/{run_id}" in paths
    assert test_client.get("/api/incidents/UNKNOWN/runs?limit=101", headers=AUTH).status_code == 422


def test_startup_recovery_marks_orphaned_runs_once(monkeypatch, tmp_path):
    database_path = tmp_path / "startup" / "servewell.db"
    database_path.parent.mkdir(parents=True)
    connection = open_database(database_path)
    connection.execute(
        "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
        "VALUES ('S1', 'Store', 'City', 'Area', 'corporate')",
    )
    connection.execute(
        """INSERT INTO incidents (
            ticket_id, reported_store_id, store_id, opened_at, status, subject, description
        ) VALUES ('INC-ORPHAN', 'S1', 'S1', ?, 'Open', 'Printer', 'Offline')""",
        (NOW.isoformat(),),
    )
    insert_run(
        connection,
        run_id="RUN-ORPHAN",
        ticket_id="INC-ORPHAN",
        started_at=NOW.isoformat(),
        idempotency_key="orphan-key",
        request_fingerprint="a" * 64,
    )
    connection.commit()
    connection.close()

    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("INGESTION_TOKEN", TOKEN)
    with TestClient(app) as test_client:
        assert test_client.get("/api/health").status_code == 200
    with TestClient(app) as test_client:
        assert test_client.get("/api/health").status_code == 200

    connection = open_database(database_path)
    stored = connection.execute("SELECT status, error_code FROM agent_runs WHERE run_id='RUN-ORPHAN'").fetchone()
    audit_count = connection.execute(
        "SELECT COUNT(*) FROM audit_events WHERE run_id='RUN-ORPHAN' AND event_type='run_failed'",
    ).fetchone()[0]
    assert tuple(stored) == ("failed", "interrupted")
    assert audit_count == 1
    connection.close()
