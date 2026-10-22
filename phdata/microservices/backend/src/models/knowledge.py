"""Durable source documents and hierarchical retrieval chunks."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base, utc_now


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    title: Mapped[str] = mapped_column(Text)
    source_text: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active_generation: Mapped[str] = mapped_column(String(36), index=True)
    embedding_model: Mapped[str] = mapped_column(String(200))
    embedding_dimensions: Mapped[int] = mapped_column(Integer)
    chunker_version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(10), index=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    heading: Mapped[list[str]] = mapped_column(JSON)
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    previous_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    next_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    index_generation: Mapped[str] = mapped_column(String(36), index=True)


__all__ = ["KnowledgeChunk", "KnowledgeDocument"]
