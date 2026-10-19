"""Environment configuration shared by the API and data CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    database_path: Path
    qdrant_url: str


def get_settings() -> Settings:
    repository_root = next(
        (
            parent
            for parent in Path(__file__).resolve().parents
            if (parent / "microservices" / "backend" / "pyproject.toml").is_file()
        ),
        None,
    )
    data_default = (repository_root or Path.cwd()) / "client_data"
    database_default = (
        repository_root / "microservices" / "backend" if repository_root else Path.cwd()
    ) / ".local" / "servewell.db"
    return Settings(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        data_dir=Path(os.getenv("DATA_DIR", str(data_default))),
        database_path=Path(os.getenv("DATABASE_PATH", str(database_default))),
        qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
    )
