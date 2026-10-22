from pathlib import Path

import pytest

from phdata.config import get_settings
from phdata.main import run


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "HOST", "PORT", "DATA_DIR", "DATABASE_PATH", "QDRANT_URL",
        "GENERATION_MODEL", "GENERATION_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_checkout_defaults():
    repository_root = Path(__file__).resolve().parents[3]
    settings = get_settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.data_dir == repository_root / "client_data"
    assert settings.database_path == (
        repository_root / "microservices" / "backend" / ".local" / "servewell.db"
    )
    assert settings.qdrant_url == "http://127.0.0.1:6333"
    assert settings.generation_model is None
    assert settings.generation_timeout_seconds == 20.0


def test_environment_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "dataset"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "state" / "app.db"))
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("GENERATION_MODEL", " gpt-6-luna ")
    monkeypatch.setenv("GENERATION_TIMEOUT_SECONDS", "15.5")

    settings = get_settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.data_dir == tmp_path / "dataset"
    assert settings.database_path == tmp_path / "state" / "app.db"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.generation_model == "gpt-6-luna"
    assert settings.generation_timeout_seconds == 15.5


@pytest.mark.parametrize("value", ["0", "121", "nan", "inf", "invalid"])
def test_generation_timeout_is_bounded(monkeypatch, value):
    monkeypatch.setenv("GENERATION_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="must be a number from 1 to 120"):
        get_settings()


def test_api_entrypoint_uses_environment(monkeypatch):
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")
    calls = []
    monkeypatch.setattr("phdata.main.uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs)))

    run()

    assert calls == [(("phdata.main:app",), {"host": "0.0.0.0", "port": 9000})]
