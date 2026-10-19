"""Offline HTTP contracts using isolated file-backed databases and local auth."""

from pathlib import Path
import shutil
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from phdata.db.database import open_database
from phdata.routes.incidents import router

TOKEN = "local-incident-test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def database_path(monkeypatch):
    directory = Path.cwd() / f".test-incidents-{uuid4()}"
    directory.mkdir()
    path = directory / "nested" / "incidents.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("INGESTION_TOKEN", TOKEN)
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield path
    shutil.rmtree(directory)


@pytest.fixture
def client(database_path):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app, headers=HEADERS) as client:
        yield client


def seed_references(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = open_database(path)
    with connection:
        connection.executemany(
            "INSERT INTO stores (store_id,store_name,city,neighborhood,store_type) "
            "VALUES (?, 'Store', 'City', 'Area', 'corporate')", [("S1",), ("S2",)],
        )
        connection.execute(
            "INSERT INTO assets (asset_id,store_id,asset_type,make,model) "
            "VALUES ('A1','S1','POS','Make','Model')",
        )
    connection.close()


def test_create_defaults_and_reopen_persistence(client, database_path):
    response = client.post("/api/incidents", json={"description": "  Register cannot print\nreceipts  "})
    assert response.status_code == 201
    body = response.json()
    incident = body["incident"]
    assert incident["ticket_id"].startswith("INC-")
    assert incident["description"] == "  Register cannot print\nreceipts  "
    assert incident["subject"] == "Register cannot print receipts"
    assert incident["status"] == "Open"
    assert incident["opened_at"].endswith("+00:00")
    assert incident["reported_store_id"] is None
    assert incident["store_id"] is None and incident["asset_id"] is None
    assert any("store_id was not supplied" in warning for warning in body["warnings"])
    assert client.get(response.headers["Location"]).json() == body
    connection = open_database(database_path)
    row = connection.execute("SELECT * FROM incidents").fetchone()
    assert row["reported_store_id"] == ""
    assert row["source_path"] is None
    assert row["resolution_notes"] is None
    assert row["escalation_flag"] == 0
    assert row["tags_json"] == "[]"
    assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    assert "ground_truth" not in row.keys()
    connection.close()
    fresh = FastAPI()
    fresh.include_router(router, prefix="/api")
    with TestClient(fresh, headers=HEADERS) as reopened:
        assert reopened.get(f"/api/incidents/{incident['ticket_id']}").json() == body


def test_exact_relationship_and_original_fields(client, database_path):
    seed_references(database_path)
    history = [{"timestamp": "2026-10-19T17:22:00+05:30", "actor": "Operator", "note": "Reported by store"}]
    payload = {
        "ticket_id": "INC-EXACT", "store_id": "S1", "asset_id": "A1", "priority": "P2",
        "subject": " Original subject ", "description": " Original report ",
        "category": "Unclassified new category", "subcategory": "New detail", "system_version": "v9",
        "opened_at": "2026-10-19T17:20:00+05:30", "history": history, "status": "Resolved",
    }
    response = client.post("/api/incidents", json=payload)
    assert response.status_code == 201
    body = response.json()
    incident = body["incident"]
    for field in ("ticket_id", "store_id", "asset_id", "priority", "subject", "description", "category", "subcategory", "system_version", "opened_at", "history"):
        assert incident[field] == payload[field]
    assert incident["reported_store_id"] == "S1"
    assert incident["reported_asset_id"] == "A1"
    assert incident["status"] == "Open"
    assert any("start Open" in warning for warning in body["warnings"])
    assert client.get(response.headers["Location"]).json()["incident"] == incident


@pytest.mark.parametrize("store,asset,resolved_store,resolved_asset", [
    ("unknown", "A1", None, None), ("S2", "A1", "S2", None),
    (None, "A1", None, None), ("S1", "unknown", "S1", None),
    ("s1", "A1", None, None), (" S1 ", "A1", None, None),
    ("S1", None, "S1", None),
])
def test_unresolved_ids_are_preserved_not_guessed(client, database_path, store, asset, resolved_store, resolved_asset):
    seed_references(database_path)
    response = client.post("/api/incidents", json={"description": "Printer offline", "store_id": store, "asset_id": asset})
    assert response.status_code == 201
    body = response.json()
    assert body["incident"]["reported_store_id"] == store
    assert body["incident"]["reported_asset_id"] == asset
    assert body["incident"]["store_id"] == resolved_store
    assert body["incident"]["asset_id"] == resolved_asset
    assert body["warnings"]


def test_duplicate_never_overwrites_incident_or_history(client, database_path):
    payload = {"ticket_id": "INC-DUP", "description": "Original", "history": [
        {"timestamp": "2026-10-19T12:00:00Z", "actor": "Store", "note": "Initial report"},
    ]}
    assert client.post("/api/incidents", json=payload).status_code == 201
    assert client.post("/api/incidents", json={**payload, "description": "Replacement"}).status_code == 409
    persisted = client.get("/api/incidents/INC-DUP").json()["incident"]
    assert persisted["description"] == "Original"
    assert persisted["history"] == payload["history"]


@pytest.mark.parametrize("payload", [
    {}, {"description": " \n\t"}, {"description": 123}, {"description": "Valid", "priority": "P5"},
    {"description": "Valid", "opened_at": "2026-10-19T12:00:00"},
    {"description": "Valid", "history": [{"timestamp": "2026-10-19T12:00:00", "actor": "Operator", "note": "Note"}]},
    {"description": "Valid", "history": [{"timestamp": "2026-10-19T12:00:00Z", "actor": " ", "note": "Note"}]},
    {"description": "Valid", "history": [{"timestamp": "2026-10-19T12:00:00Z", "actor": "Operator", "note": "Note", "action": "execute"}]},
    *[{"description": "Valid", key: "forbidden"} for key in (
        "resolution_notes", "ground_truth", "evaluator_labels", "routing_outcome", "source_path", "action", "escalation_flag",
    )],
])
def test_invalid_or_server_owned_inputs_are_rejected(client, payload):
    assert client.post("/api/incidents", json=payload).status_code == 422


def test_history_failure_rolls_back_entire_incident(client, database_path):
    assert client.get("/api/incidents").status_code == 200
    connection = open_database(database_path)
    connection.executescript("""
        CREATE TRIGGER fail_history BEFORE INSERT ON incident_events
        WHEN NEW.source_event_index = 1
        BEGIN SELECT RAISE(ABORT, 'sensitive database path and credentials'); END;
    """)
    connection.close()
    response = client.post("/api/incidents", json={
        "ticket_id": "INC-ROLLBACK", "description": "Do not partially commit",
        "history": [{"timestamp": "2026-10-19T12:00:00Z", "actor": "Store", "note": str(index)} for index in range(2)],
    })
    assert response.status_code == 503
    assert response.json() == {"detail": "Incident storage is unavailable"}
    connection = open_database(database_path)
    assert connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0] == 0
    connection.close()


def test_pagination_stable_timestamp_ties_and_filters(client):
    for ticket, opened, category in [
        ("INC-B", "2026-10-19T10:00:00Z", "POS"),
        ("INC-A", "2026-10-19T15:30:00+05:30", "POS"),
        ("INC-C", "2026-10-18T10:00:00Z", "Network"),
    ]:
        assert client.post("/api/incidents", json={"ticket_id": ticket, "description": "Offline", "opened_at": opened, "category": category}).status_code == 201
    first = client.get("/api/incidents?limit=1&offset=0").json()
    second = client.get("/api/incidents?limit=1&offset=1").json()
    assert first["total"] == 3 and first["limit"] == 1 and first["offset"] == 0
    assert first["incidents"][0]["ticket_id"] == "INC-A"
    assert second["incidents"][0]["ticket_id"] == "INC-B"
    assert client.get("/api/incidents?category=POS&status=Open").json()["total"] == 2
    assert client.get("/api/incidents?status=Resolved").json()["incidents"] == []
    assert client.get("/api/incidents?offset=999").json()["incidents"] == []
    assert client.get("/api/incidents", params={"category": "POS' OR 1=1 --"}).json()["total"] == 0


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=wrong", "offset=-1", "offset=wrong"])
def test_pagination_bounds(client, query):
    assert client.get(f"/api/incidents?{query}").status_code == 422


@pytest.mark.parametrize("method,path", [("post", "/api/incidents"), ("get", "/api/incidents"), ("get", "/api/incidents/INC-X")])
@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic token"])
def test_every_endpoint_requires_local_bearer(client, method, path, authorization):
    client.headers.pop("Authorization")
    headers = {"Authorization": authorization} if authorization else {}
    response = client.request(method, path, headers=headers, json={"description": "Offline"} if method == "post" else None)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in response.text


def test_disabled_auth_missing_detail_and_sanitized_database_error(client, database_path, monkeypatch):
    monkeypatch.delenv("INGESTION_TOKEN")
    assert client.get("/api/incidents").status_code == 503
    monkeypatch.setenv("INGESTION_TOKEN", TOKEN)
    assert client.get("/api/incidents/no-such-ticket").status_code == 404
    database_path.write_bytes(b"not a SQLite database")
    response = client.get("/api/incidents")
    assert response.status_code == 503
    assert str(database_path) not in response.text
    assert TOKEN not in response.text


def test_sql_parameters_and_encoded_location(client):
    ticket_id = "INC-' ; SELECT 1 --/part"
    response = client.post("/api/incidents", json={"ticket_id": ticket_id, "description": "Text ' ; DROP TABLE incidents --"})
    assert response.status_code == 201
    assert "%27" in response.headers["Location"]
    assert client.get(response.headers["Location"]).json()["incident"]["ticket_id"] == ticket_id
    assert client.get("/api/incidents").json()["total"] == 1
