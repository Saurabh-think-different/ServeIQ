"""Environment configuration for the API and ingestion stages."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    database_path: Path
    qdrant_url: str
    ingestion_token: str | None = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    qdrant_collection: str = "servewell-kb"


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
        ingestion_token=os.getenv("INGESTION_TOKEN") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "1536")),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "servewell-kb"),
    )
