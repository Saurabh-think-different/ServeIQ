"""Bounded search contracts; similarity is not an applicability probability."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=10, strict=True)
    category: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    system_version: str | None = Field(default=None, min_length=1, max_length=200)


class Passage(BaseModel):
    content: str
    source_path: str
    section: str
    similarity_score: float = Field(allow_inf_nan=False)
    category: str | None = None
    model: str | None = None
    system_version: str | None = None
    document_type: str | None = None
    applicability: Literal["context_match", "unknown"]
    applicability_notes: list[str]


class SearchResponse(BaseModel):
    passages: list[Passage]
    evidence_gap: bool
    warnings: list[str]
