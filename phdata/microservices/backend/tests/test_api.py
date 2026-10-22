import os
import tempfile
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp.name) / 'tests.db'}"
os.environ["JWT_SECRET"] = "test-only-secret-with-at-least-32-characters"
os.environ.pop("OPENAI_API_KEY", None)

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as client:
        yield client
    _tmp.cleanup()


def register(client, email):
    response = client.post(
        "/users/register", json={"email": email, "password": "Strong-test-pass123!"}
    )
    assert response.status_code in (200, 201), response.text
    response = client.post(
        "/users/login", json={"email": email, "password": "Strong-test-pass123!"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_auth_and_incident_ownership(client):
    assert client.get("/health").status_code == 200
    assert client.get("/incidents").status_code == 401
    first = register(client, "operator1@example.com")
    second = register(client, "operator2@example.com")
    assert client.get("/users/me", headers=first).json()["email"] == "operator1@example.com"
    assert (
        client.post(
            "/users/login", json={"email": "operator1@example.com", "password": "incorrect"}
        ).status_code
        == 401
    )
    incident = client.post(
        "/incidents",
        headers=first,
        json={"title": "Receipt printer offline", "description": "Store 42 cannot print receipts."},
    )
    assert incident.status_code == 201, incident.text
    incident_id = incident.json()["id"]
    assert client.get(f"/incidents/{incident_id}", headers=second).status_code == 404
    assert client.get(f"/incidents/{incident_id}/messages", headers=second).status_code == 404
    message = client.post(
        f"/incidents/{incident_id}/messages",
        headers=first,
        json={"content": "Printer is powered on"},
    )
    assert message.status_code == 201, message.text
    message_id = message.json()["id"]
    assert client.get(f"/messages/{message_id}/events", headers=second).status_code == 404
    assert client.get(f"/messages/{message_id}/events", headers=first).status_code == 200
    assert (
        client.post("/llm/stream", headers=second, json={"incident_id": incident_id}).status_code
        == 404
    )
    assert (
        client.post("/llm/stream", headers=first, json={"incident_id": incident_id}).status_code
        == 503
    )


def test_list_users_requires_authentication(client):
    response = client.get("/users")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.get("/users", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_list_users_returns_only_ids_and_emails(client):
    first = register(client, "directory-z@example.com")
    second = register(client, "directory-a@example.com")
    expected = [
        {key: client.get("/users/me", headers=headers).json()[key] for key in ("id", "email")}
        for headers in (first, second)
    ]

    response = client.get("/users", headers=first)
    assert response.status_code == 200, response.text
    users = response.json()
    assert all(set(user) == {"id", "email"} for user in users)
    assert all(user in users for user in expected)
    assert [user["email"] for user in users] == sorted(user["email"] for user in users)
    assert client.get("/users", headers=second).json() == users


def test_validation_and_no_password_leaks(client):
    assert client.post("/users/register", json={"email": "bad", "password": "x"}).status_code == 422
    response = client.post(
        "/users/register",
        json={"email": "operator3@example.com", "password": "Strong-test-pass123!"},
    )
    assert response.status_code == 201
    assert "password" not in response.text
    assert (
        client.post(
            "/users/register",
            json={"email": "operator3@example.com", "password": "Strong-test-pass123!"},
        ).status_code
        == 409
    )
