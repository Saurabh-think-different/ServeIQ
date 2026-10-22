from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from phdata.config import get_settings
from phdata.ingestion.interfaces import IngestionError, IngestionReport
from phdata.ingestion.pipeline import run_pipeline
from phdata.main import app
from phdata.routes.ingestion import _ingestion_lock, ingestion_stages


class FakeStage:
    def __init__(self, name, *, error=None, status="succeeded"):
        self.name = name
        self.error = error
        self.status = status
        self.calls = 0

    def run(self):
        self.calls += 1
        if self.error:
            raise self.error
        return IngestionReport(stage=self.name, status=self.status, inserted={"rows": 1})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INGESTION_TOKEN", "operator-test-token")
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def stages():
    stages = {name: FakeStage(name) for name in ("structured", "knowledge")}
    app.dependency_overrides[ingestion_stages] = lambda: stages
    yield stages
    app.dependency_overrides.clear()


def test_ingestion_disabled_without_operator_configuration(client, monkeypatch):
    monkeypatch.delenv("INGESTION_TOKEN", raising=False)
    response = client.post("/api/ingestions", json={"target": "structured"})
    assert response.status_code == 503


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic operator-test-token", "Bearer "])
def test_ingestion_requires_operator_token(client, authorization):
    headers = {"Authorization": authorization} if authorization else {}
    response = client.post("/api/ingestions", json={"target": "structured"}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_swagger_declares_operator_bearer_authorization(client):
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["OperatorBearer"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "Enter the operator token only; Swagger adds the Bearer prefix.",
    }
    operation = schema["paths"]["/api/ingestions"]["post"]
    assert {"OperatorBearer": []} in operation["security"]
    assert all(parameter["name"].lower() != "authorization" for parameter in operation.get("parameters", []))


def test_valid_token_returns_blocked_knowledge_without_api_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.post(
        "/api/ingestions", json={"target": "knowledge"},
        headers={"Authorization": "Bearer operator-test-token"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["stages"][0]["status"] == "blocked"


@pytest.mark.parametrize("target", ["structured", "knowledge", "all"])
def test_selected_stages_are_run_and_reported(client, stages, target):
    response = client.post("/api/ingestions", json={"target": target})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert [report["stage"] for report in body["stages"]] == (
        ["structured", "knowledge"] if target == "all" else [target]
    )
    for name, stage in stages.items():
        assert stage.calls == int(target in (name, "all"))


@pytest.mark.parametrize("payload", [{}, {"target": "unknown"}, {"target": "structured", "data_dir": "/etc"}])
def test_invalid_requests_are_rejected(client, stages, payload):
    assert client.post("/api/ingestions", json=payload).status_code == 422
    assert all(stage.calls == 0 for stage in stages.values())


def test_concurrent_ingestion_is_rejected(client, stages):
    assert _ingestion_lock.acquire(blocking=False)
    try:
        response = client.post("/api/ingestions", json={"target": "all"})
        assert response.status_code == 409
    finally:
        _ingestion_lock.release()
    assert client.post("/api/ingestions", json={"target": "all"}).status_code == 200


def test_pipeline_preserves_committed_stage_and_sanitizes_dependency_failure():
    stages = {
        "structured": FakeStage("structured"),
        "knowledge": FakeStage("knowledge", error=RuntimeError("secret-provider-key")),
    }
    report = run_pipeline("all", stages)
    assert report.status == "partial"
    assert report.stages[0].inserted == {"rows": 1}
    assert report.stages[1].status == "failed"
    assert "secret-provider-key" not in report.model_dump_json()


def test_pipeline_retains_safe_validation_errors_and_runs_other_stage():
    stages = {
        "structured": FakeStage("structured", error=IngestionError(["Invalid store row 2"])),
        "knowledge": FakeStage("knowledge"),
    }
    report = run_pipeline("all", stages)
    assert report.status == "partial"
    assert report.stages[0].errors == ["Invalid store row 2"]
    assert stages["knowledge"].calls == 1


def test_real_http_pipeline_imports_structured_data_when_knowledge_is_blocked(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "state" / "http.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    headers = {"Authorization": "Bearer operator-test-token"}
    first = client.post("/api/ingestions", json={"target": "all"}, headers=headers)
    assert first.status_code == 200
    report = first.json()
    assert report["status"] == "partial"
    assert report["stages"][0]["inserted"]["incidents"] == 256
    assert report["stages"][1]["status"] == "blocked"

    repeated = client.post("/api/ingestions", json={"target": "structured"}, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "succeeded"
    stage = repeated.json()["stages"][0]
    assert sum(stage["inserted"].values()) == 0
    assert sum(stage["updated"].values()) == 0
    assert stage["skipped"]["incidents"] == 256


def test_settings_do_not_reveal_credentials():
    settings = replace(get_settings(), ingestion_token="operator-secret", openai_api_key="provider-secret")
    assert "operator-secret" not in repr(settings)
    assert "provider-secret" not in repr(settings)
