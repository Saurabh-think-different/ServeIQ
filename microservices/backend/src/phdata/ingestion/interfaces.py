"""Shared contract for independently runnable ingestion stages."""

from typing import Literal, Protocol

from pydantic import BaseModel, Field

StageName = Literal["structured", "knowledge"]


class IngestionReport(BaseModel):
    stage: StageName
    status: Literal["succeeded", "failed", "blocked"] = "succeeded"
    inserted: dict[str, int] = Field(default_factory=dict)
    updated: dict[str, int] = Field(default_factory=dict)
    skipped: dict[str, int] = Field(default_factory=dict)
    deleted: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    path_mappings: dict[str, int] = Field(default_factory=dict)


class IngestionError(ValueError):
    """A safe, actionable failure suitable for an ingestion report."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class IngestionStage(Protocol):
    def run(self) -> IngestionReport: ...
