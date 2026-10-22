"""Environment configuration; startup must reject an unset JWT secret."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/phdata.db"
    jwt_secret: str = ""
    jwt_algorithm: Literal["HS256"] = "HS256"
    token_expire_minutes: int = Field(default=60, ge=1, le=10080)
    openai_api_key: str | None = None
    llm_model: str = "gpt-6.1-sol"
    allowed_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["gpt-6.1-sol", "gpt-6-luna", "gpt-5.4-mini", "gpt-4.1"]
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    max_agent_steps: int = Field(default=12, ge=1, le=100)
    agent_timeout_seconds: int = Field(default=180, ge=30, le=600)
    kb_min_score: float = Field(default=0.35, ge=-1, le=1)
    dataset_root: Path = Path("client_data")
    ingestion_api_key: str | None = None
    ingestion_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "servewell_kb"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=256, le=3072)
    embedding_batch_size: int = Field(default=32, ge=1, le=128)
    evaluation_max_cases: int = Field(default=25, ge=1, le=100)
    evaluation_case_timeout_seconds: int = Field(default=240, ge=30, le=900)
    evaluation_run_timeout_seconds: int = Field(default=1800, ge=60, le=7200)

    @field_validator("allowed_models", "cors_origins", mode="before")
    @classmethod
    def parse_lists(cls, value: object) -> object:
        if isinstance(value, str):
            return (
                json.loads(value)
                if value.lstrip().startswith("[")
                else [item.strip() for item in value.split(",") if item.strip()]
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
