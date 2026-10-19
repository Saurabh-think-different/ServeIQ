from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from phdata.db.database import open_database
from phdata.main import app

TOKEN = "test-action-api-token"
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


def create_run_with_proposal(client, ticket_id):
    test_client, _ = client
    incident = test_client.post(
        "/api/incidents",
        headers=AUTH,
        json={
            "ticket_id": ticket_id,
            "description": "Receipt printer is not detected.",
            "category": "Printers",
            "subcategory": "Unreviewed symptom",
            "opened_at": NOW.isoformat(),
        },
    )
    assert incident.status_code == 201
    run = test_client.post(
        f"/api/incidents/{ticket_id}/runs",
        headers={**AUTH, "Idempotency-Key": f"run-{ticket_id}"},
        json={},
    )
    assert run.status_code == 201
    proposals = test_client.get(f"/api/runs/{run.json()['run_id']}/actions", headers=AUTH)
    assert proposals.status_code == 200 and len(proposals.json()["actions"]) == 1
    return run.json(), proposals.json()["actions"][0]


def test_reject_route_is_authenticated_and_auditable(client):
    test_client, _ = client
    run, proposal = create_run_with_proposal(client, "INC-ACTION-1")
    assert test_client.get(f"/api/runs/{run['run_id']}/actions").status_code == 401

    response = test_client.post(
        f"/api/actions/{proposal['action_id']}/decision",
        headers=AUTH,
        json={"decision": "reject"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    replay = test_client.post(
        f"/api/actions/{proposal['action_id']}/decision",
        headers=AUTH,
        json={"decision": "reject"},
    )
    assert replay.status_code == 200 and replay.json() == response.json()
    assert test_client.post(
        f"/api/actions/{proposal['action_id']}/decision",
        headers=AUTH,
        json={"decision": "approve"},
    ).status_code == 409

    audit = test_client.get("/api/incidents/INC-ACTION-1/audit?limit=20&offset=0", headers=AUTH)
    assert audit.status_code == 200
    assert [event["event_type"] for event in audit.json()["events"]] == [
        "run_started", "run_completed", "proposal_created", "proposal_rejected",
    ]
    rejection = audit.json()["events"][-1]
    assert rejection["actor_type"] == "human"
    assert rejection["actor_id"] == "local_operator"
    assert rejection["action_id"] == proposal["action_id"]


def test_approval_simulation_and_replay_do_not_mutate_incident(client):
    test_client, database_path = client
    run, proposal = create_run_with_proposal(client, "INC-ACTION-2")
    decision = test_client.post(
        f"/api/actions/{proposal['action_id']}/decision",
        headers=AUTH,
        json={"decision": "approve"},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "completed"
    assert "no notification" in decision.json()["result_summary"]
    replay = test_client.post(
        f"/api/actions/{proposal['action_id']}/decision",
        headers=AUTH,
        json={"decision": "approve"},
    )
    assert replay.status_code == 200 and replay.json() == decision.json()
    connection = open_database(database_path)
    assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE run_id=?", (run["run_id"],)).fetchone()[0] == 5
    assert connection.execute("SELECT status FROM action_proposals WHERE action_id=?", (proposal["action_id"],)).fetchone()[0] == "completed"
    assert connection.execute("SELECT status FROM incidents WHERE ticket_id='INC-ACTION-2'").fetchone()[0] == "Open"
    connection.close()


def test_stale_proposal_and_invalid_decision_body(client):
    test_client, database_path = client
    _, proposal = create_run_with_proposal(client, "INC-ACTION-3")
    connection = open_database(database_path)
    connection.execute("UPDATE incidents SET description='Changed facts' WHERE ticket_id='INC-ACTION-3'")
    connection.commit()
    connection.close()
    stale = test_client.post(
        f"/api/actions/{proposal['action_id']}/decision",
        headers=AUTH,
        json={"decision": "approve"},
    )
    assert stale.status_code == 409
    assert test_client.post(
        f"/api/actions/{proposal['action_id']}/decision",
        headers=AUTH,
        json={"decision": "approve", "actor_id": "spoofed"},
    ).status_code == 422
    assert test_client.get("/api/incidents/UNKNOWN/audit", headers=AUTH).status_code == 404
    assert test_client.get("/api/runs/MISSING/actions", headers=AUTH).status_code == 404


def test_action_and_audit_routes_are_registered_and_bounded(client):
    test_client, _ = client
    paths = test_client.get("/openapi.json").json()["paths"]
    assert "/api/runs/{run_id}/actions" in paths
    assert "/api/actions/{action_id}/decision" in paths
    assert "/api/incidents/{ticket_id}/audit" in paths
    assert test_client.get("/api/incidents/UNKNOWN/audit?limit=101", headers=AUTH).status_code == 422
