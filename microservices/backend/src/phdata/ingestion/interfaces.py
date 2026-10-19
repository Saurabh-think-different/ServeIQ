"""Shared contract for independently runnable ingestion stages."""

from abc import ABC, abstractmethod
from typing import Literal

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


class IngestionStage(ABC):
    @abstractmethod
    def run(self) -> IngestionReport:
        """Run the ingestion stage and return its report."""
