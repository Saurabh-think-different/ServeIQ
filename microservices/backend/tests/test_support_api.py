from fastapi.testclient import TestClient

from phdata.main import app


def test_fresh_incident_context_and_seed_import_are_registered(monkeypatch, tmp_path):
    monkeypatch.setenv("INGESTION_TOKEN", "local-support-test")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(app, headers={"Authorization": "Bearer local-support-test"}) as client:
        created = client.post("/api/incidents", json={"ticket_id": "INC-FRESH", "description": "Receipt printer will not print"})
        assert created.status_code == 201
        context = client.get("/api/incident-context/INC-FRESH")
        assert context.status_code == 200
        body = context.json()
        assert body["incident"]["ticket_id"] == "INC-FRESH"
        assert body["store"] is None and body["asset"] is None
        assert "sla" in body["missing"]
        assert client.get("/api/incident-context/unknown").status_code == 404
        imported = client.post("/api/ingestions", json={"target": "structured"})
        assert imported.json()["status"] == "succeeded"
        assert client.get("/api/incidents/INC-FRESH").json()["incident"]["description"] == "Receipt printer will not print"
        assert client.get("/api/incidents?limit=1").json()["total"] == 257
    with TestClient(app) as unauthorized:
        assert unauthorized.get("/api/incident-context/INC-FRESH").status_code == 401
