from fastapi.testclient import TestClient

from phdata.main import app


def test_health_route_is_registered() -> None:
    assert "/api/health" in app.openapi()["paths"]


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/health" in response.json()["paths"]


def test_unknown_route_returns_not_found() -> None:
    with TestClient(app) as client:
        response = client.get("/api/unknown")

    assert response.status_code == 404


def test_readiness_initializes_configured_database(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "state" / "servewell.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("QDRANT_URL", "http://unavailable.invalid:6333")

    with TestClient(app) as client:
        response = client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}
    assert database_path.is_file()


def test_database_failure_does_not_break_liveness(monkeypatch, tmp_path) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("blocked", encoding="utf-8")
    monkeypatch.setenv("DATABASE_PATH", str(parent_file / "servewell.db"))

    with TestClient(app) as client:
        readiness = client.get("/api/health/ready")
        liveness = client.get("/api/health")

    assert readiness.status_code == 503
    assert readiness.json() == {"detail": "Database unavailable"}
    assert str(tmp_path) not in readiness.text
    assert liveness.status_code == 200


def test_readiness_reports_invalid_sqlite_file(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "invalid.db"
    database_path.write_text("not a SQLite database", encoding="utf-8")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))

    with TestClient(app) as client:
        response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}