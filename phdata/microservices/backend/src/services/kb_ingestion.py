"""Synchronous KB ingestion with SQLite authority and generation-safe Qdrant indexing."""

import hashlib
import math
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from openai import APIError, OpenAI
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ApiException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.knowledge import KnowledgeChunk, KnowledgeDocument
from src.services.chunking import (
    CHUNKER_VERSION,
    ChunkDraft,
    chunk_markdown,
    content_hash,
    count_tokens,
    embedding_text,
)


class KnowledgeIndexError(RuntimeError):
    """A safe operational error; provider responses and secrets are never exposed."""


def _source_path(source: str) -> str:
    path = PurePosixPath(source.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("KB source must be a relative Markdown path without traversal")
    if path.suffix.lower() != ".md":
        raise ValueError("Only Markdown knowledge documents are supported")
    if path.parts[0] != "kb":
        path = PurePosixPath("kb") / path
    if len(str(path)) > 1024:
        raise ValueError("KB source path exceeds 1024 characters")
    return path.as_posix()


def _id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, "servewell:knowledge:" + value))


def _client(settings: Any) -> QdrantClient:
    try:
        return QdrantClient(url=settings.qdrant_url, timeout=30)
    except (ApiException, ValueError, OSError):
        raise KnowledgeIndexError("Unable to initialize the configured Qdrant connection") from None


def _ensure_collection(client: QdrantClient, settings: Any) -> bool:
    """Return whether a collection already existed; never recreate incompatible data."""
    existed = client.collection_exists(settings.qdrant_collection)
    if not existed:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=models.VectorParams(
                size=settings.embedding_dimensions, distance=models.Distance.COSINE
            ),
            timeout=30,
        )
    info = client.get_collection(settings.qdrant_collection)
    vectors = info.config.params.vectors
    if not isinstance(vectors, models.VectorParams) or (
        vectors.size != settings.embedding_dimensions or vectors.distance != models.Distance.COSINE
    ):
        raise KnowledgeIndexError(
            "Qdrant collection must use an unnamed cosine vector with "
            f"{settings.embedding_dimensions} dimensions. Configure a compatible new "
            "collection and reingest; the existing collection was not modified."
        )
    return existed


def _embed(client: OpenAI, texts: list[str], settings: Any) -> list[list[float]]:
    try:
        response = client.embeddings.create(
            input=texts,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(texts))):
            raise ValueError("invalid embedding response indices")
        result = []
        for item in ordered:
            vector = item.embedding
            if (
                len(vector) != settings.embedding_dimensions
                or any(not math.isfinite(value) for value in vector)
                or not any(value != 0 for value in vector)
            ):
                raise ValueError("invalid embedding vector")
            result.append(vector)
        # Qdrant cosine collections normalize vectors on upload.
        return result
    except (APIError, ValueError, TypeError):
        raise KnowledgeIndexError(
            f"Embedding request failed for {len(texts)} input(s); retry ingestion "
            "after checking provider availability and embedding configuration."
        ) from None


def _openai(settings: Any) -> OpenAI:
    if not settings.openai_api_key:
        raise KnowledgeIndexError("OPENAI_API_KEY must be configured for KB embeddings")
    return OpenAI(api_key=settings.openai_api_key, timeout=45.0, max_retries=2)


def _obsolete_filter(document_id: str, generation: str) -> models.Filter:
    return models.Filter(
        must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))],
        must_not=[
            models.FieldCondition(key="index_generation", match=models.MatchValue(value=generation))
        ],
    )


def _cleanup(client: QdrantClient, settings: Any, document_id: str, generation: str) -> bool:
    try:
        client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=models.FilterSelector(filter=_obsolete_filter(document_id, generation)),
            wait=True,
        )
        return True
    except (ApiException, ValueError, OSError):
        # SQLite generation validation hides these points; an unchanged retry cleans them up.
        return False


def _make_chunks(
    document_id: str,
    generation: str,
    parents: list[ChunkDraft],
) -> tuple[list[KnowledgeChunk], list[KnowledgeChunk]]:
    parent_rows = []
    child_rows = []
    for ordinal, draft in enumerate(parents):
        parent_id = _id(
            f"{document_id}:{generation}:parent:{ordinal}:{content_hash(draft.content)}"
        )
        parent_rows.append(
            KnowledgeChunk(
                id=parent_id,
                document_id=document_id,
                parent_id=None,
                kind="parent",
                content=draft.content,
                content_hash=content_hash(draft.content),
                heading=draft.heading,
                line_start=draft.line_start,
                line_end=draft.line_end,
                ordinal=ordinal,
                token_count=draft.token_count,
                warnings=draft.warnings,
                index_generation=generation,
            )
        )
        for child in draft.children:
            child_ordinal = len(child_rows)
            child_rows.append(
                KnowledgeChunk(
                    id=_id(
                        f"{document_id}:{generation}:child:{child_ordinal}:"
                        f"{content_hash(child.content)}"
                    ),
                    document_id=document_id,
                    parent_id=parent_id,
                    kind="child",
                    content=child.content,
                    content_hash=content_hash(child.content),
                    heading=child.heading,
                    line_start=child.line_start,
                    line_end=child.line_end,
                    ordinal=child_ordinal,
                    token_count=child.token_count,
                    warnings=child.warnings,
                    index_generation=generation,
                )
            )
    for rows in (parent_rows, child_rows):
        for index, row in enumerate(rows):
            row.previous_id = rows[index - 1].id if index else None
            row.next_id = rows[index + 1].id if index + 1 < len(rows) else None
    return parent_rows, child_rows


def _payload(row: KnowledgeChunk, source: str, settings: Any) -> dict[str, Any]:
    return {
        "parent_id": row.parent_id,
        "document_id": row.document_id,
        "source": source,
        "heading": row.heading,
        "line_start": row.line_start,
        "line_end": row.line_end,
        "content_hash": row.content_hash,
        "index_generation": row.index_generation,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "ordinal": row.ordinal,
    }


def ingest_kb_document(db: Session, source: str, content: bytes, settings: Any) -> dict[str, Any]:
    """Publish all new vectors before atomically switching SQLite's active generation.

    Failed partial upserts remain invisible. IDs are deterministic, so a retry safely
    overwrites that unpublished generation. Old vectors are deleted only after commit.
    The caller must serialize ingestion/search with the application ingestion lock.
    """
    source = _source_path(source)
    maximum_bytes = getattr(settings, "ingestion_max_bytes", 10 * 1024 * 1024)
    if len(content) > maximum_bytes:
        raise ValueError("KB document exceeds the configured ingestion size limit")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("KB documents must be UTF-8 encoded") from None
    if not text.strip():
        raise ValueError("Knowledge document is empty")
    document_id = _id(source)
    source_hash = hashlib.sha256(content).hexdigest()
    generation = _id(
        f"{document_id}:{source_hash}:{settings.embedding_model}:"
        f"{settings.embedding_dimensions}:{settings.qdrant_collection}:{CHUNKER_VERSION}"
    )
    existing = db.get(KnowledgeDocument, document_id)
    qdrant = _client(settings)
    try:
        try:
            collection_existed = _ensure_collection(qdrant, settings)
        except KnowledgeIndexError:
            raise
        except (ApiException, ValueError, OSError):
            raise KnowledgeIndexError("Qdrant collection is unavailable; retry ingestion") from None
        if existing is not None and existing.active_generation == generation and collection_existed:
            cleaned = _cleanup(qdrant, settings, document_id, generation)
            counts = dict(
                db.execute(
                    select(KnowledgeChunk.kind, func.count())
                    .where(
                        KnowledgeChunk.document_id == document_id,
                        KnowledgeChunk.index_generation == generation,
                    )
                    .group_by(KnowledgeChunk.kind)
                ).all()
            )
            return {
                "status": "unchanged",
                "document_id": document_id,
                "source": source,
                "source_hash": source_hash,
                "index_generation": generation,
                "cleanup_pending": not cleaned,
                "parent_chunks": counts.get("parent", 0),
                "child_chunks": counts.get("child", 0),
                "embedded_chunks": 0,
            }
        parsed = chunk_markdown(source, text, settings.embedding_model)
        parents, children = _make_chunks(document_id, generation, parsed.parents)
        batch_size = min(128, max(1, settings.embedding_batch_size))
        with _openai(settings) as openai:
            for offset in range(0, len(children), batch_size):
                batch = children[offset : offset + batch_size]
                inputs = [embedding_text(row) for row in batch]
                if any(count_tokens(value, settings.embedding_model) > 8191 for value in inputs):
                    raise ValueError("KB embedding input exceeds the model context limit")
                vectors = _embed(openai, inputs, settings)
                try:
                    qdrant.upsert(
                        collection_name=settings.qdrant_collection,
                        points=[
                            models.PointStruct(
                                id=row.id, vector=vector, payload=_payload(row, source, settings)
                            )
                            for row, vector in zip(batch, vectors)
                        ],
                        wait=True,
                    )
                except (ApiException, ValueError, OSError):
                    raise KnowledgeIndexError(
                        f"Qdrant write failed for a batch of {len(batch)} chunks; "
                        "the active document is unchanged and ingestion can be retried."
                    ) from None
        try:
            # Remove children before parents, including a previously published same generation
            # when rebuilding a collection that was deleted outside this service.
            db.execute(
                delete(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == document_id, KnowledgeChunk.kind == "child"
                ),
                execution_options={"synchronize_session": "fetch"},
            )
            db.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id),
                execution_options={"synchronize_session": "fetch"},
            )
            if existing is None:
                existing = KnowledgeDocument(id=document_id, source=source)
                db.add(existing)
            existing.title = parsed.title
            existing.source_text = text
            existing.source_hash = source_hash
            existing.metadata_json = parsed.metadata
            existing.active_generation = generation
            existing.embedding_model = settings.embedding_model
            existing.embedding_dimensions = settings.embedding_dimensions
            existing.chunker_version = CHUNKER_VERSION
            db.flush()
            db.add_all(parents)
            db.flush()
            db.add_all(children)
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            # Never remove the new points here: a connection error can make commit's
            # outcome ambiguous. SQLite authority prevents premature visibility.
            raise KnowledgeIndexError(
                "Knowledge metadata could not be committed; retry ingestion safely."
            ) from None
        cleaned = _cleanup(qdrant, settings, document_id, generation)
        return {
            "status": "indexed",
            "document_id": document_id,
            "source": source,
            "source_hash": source_hash,
            "index_generation": generation,
            "parent_chunks": len(parents),
            "child_chunks": len(children),
            "embedded_chunks": len(children),
            "cleanup_pending": not cleaned,
        }
    finally:
        qdrant.close()


def ingest_kb_dataset(db: Session, root: Path, settings: Any) -> dict[str, Any]:
    """Index only Markdown beneath the dataset's kb directory, never structured CSV."""
    root = Path(root).resolve()
    kb_root = root if root.name == "kb" else root / "kb"
    if not kb_root.is_dir() or not kb_root.resolve().is_relative_to(root):
        raise ValueError("Dataset must contain a local kb directory")
    documents = []
    failures = []
    for path in sorted(kb_root.rglob("*.md")):
        source = "kb/" + path.relative_to(kb_root).as_posix()
        if not path.is_file() or not path.resolve().is_relative_to(kb_root.resolve()):
            failures.append({"source": source, "error": "Source is not a local KB file"})
            continue
        try:
            if path.stat().st_size > getattr(settings, "ingestion_max_bytes", 10 * 1024 * 1024):
                raise ValueError("KB document exceeds the configured ingestion size limit")
            documents.append(ingest_kb_document(db, source, path.read_bytes(), settings))
        except (KnowledgeIndexError, ValueError, OSError) as exc:
            db.rollback()
            failures.append(
                {
                    "source": source,
                    "error": (
                        str(exc)
                        if isinstance(exc, (KnowledgeIndexError, ValueError))
                        else "Unable to read KB source"
                    ),
                }
            )
    return {
        "status": "complete" if not failures else "partial",
        "documents": documents,
        "indexed": sum(item["status"] == "indexed" for item in documents),
        "unchanged": sum(item["status"] == "unchanged" for item in documents),
        "failed": len(failures),
        "errors": failures,
    }


def _citation(row: KnowledgeChunk, source: str) -> dict[str, Any]:
    return {
        "id": row.id,
        "content": row.content,
        "heading": row.heading,
        "line_start": row.line_start,
        "line_end": row.line_end,
        "citation": f"{source}#L{row.line_start}-L{row.line_end}",
        "source": source,
        "warnings": row.warnings,
        "ordinal": row.ordinal,
        "previous_id": row.previous_id,
        "next_id": row.next_id,
        "content_hash": row.content_hash,
    }


def search_kb(db: Session, query: str, limit: int, settings: Any) -> list[dict[str, Any]]:
    """Return only committed children and their original, line-cited parent context."""
    query = query.strip()
    if not query:
        raise ValueError("Knowledge search query must not be empty")
    if not 1 <= limit <= 50:
        raise ValueError("Knowledge search limit must be between 1 and 50")
    if count_tokens(query, settings.embedding_model) > 8191:
        raise ValueError("Knowledge search query exceeds the embedding token limit")
    if db.scalar(select(KnowledgeDocument.id).limit(1)) is None:
        return []
    qdrant = _client(settings)
    try:
        try:
            if not qdrant.collection_exists(settings.qdrant_collection):
                raise KnowledgeIndexError(
                    "KB collection is missing; reingest the knowledge dataset"
                )
            _ensure_collection(qdrant, settings)
        except KnowledgeIndexError:
            raise
        except (ApiException, ValueError, OSError):
            raise KnowledgeIndexError("Qdrant is unavailable for knowledge search") from None
        with _openai(settings) as openai:
            vector = _embed(openai, [query], settings)[0]
        results = []
        offset = 0
        page_size = min(100, max(20, limit * 4))
        # Bound recovery work even if failed ingestions left many unpublished points.
        while len(results) < limit and offset < 1000:
            try:
                points = qdrant.query_points(
                    collection_name=settings.qdrant_collection,
                    query=vector,
                    query_filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="embedding_model",
                                match=models.MatchValue(value=settings.embedding_model),
                            ),
                            models.FieldCondition(
                                key="embedding_dimensions",
                                match=models.MatchValue(value=settings.embedding_dimensions),
                            ),
                        ]
                    ),
                    limit=page_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                    timeout=30,
                ).points
            except (ApiException, ValueError, OSError):
                raise KnowledgeIndexError("Qdrant search failed; retry knowledge search") from None
            if not points:
                break
            child_ids = [str(point.id) for point in points]
            children = {
                row.id: row
                for row in db.scalars(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.id.in_(child_ids), KnowledgeChunk.kind == "child"
                    )
                )
            }
            document_ids = {child.document_id for child in children.values()}
            documents = {
                row.id: row
                for row in db.scalars(
                    select(KnowledgeDocument).where(KnowledgeDocument.id.in_(document_ids))
                )
            }
            parent_ids = {child.parent_id for child in children.values()}
            parents = {
                row.id: row
                for row in db.scalars(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.id.in_(parent_ids), KnowledgeChunk.kind == "parent"
                    )
                )
            }
            for point in points:
                child = children.get(str(point.id))
                if child is None:
                    continue
                document = documents.get(child.document_id)
                parent = parents.get(child.parent_id)
                payload = point.payload or {}
                if (
                    document is None
                    or parent is None
                    or (
                        child.index_generation != document.active_generation
                        or parent.index_generation != document.active_generation
                        or payload.get("index_generation") != document.active_generation
                        or payload.get("document_id") != document.id
                        or payload.get("parent_id") != parent.id
                        or payload.get("content_hash") != child.content_hash
                        or document.embedding_model != settings.embedding_model
                        or document.embedding_dimensions != settings.embedding_dimensions
                    )
                ):
                    continue
                results.append(
                    {
                        "score": point.score,
                        "document_id": document.id,
                        "title": document.title,
                        "source": document.source,
                        "source_hash": document.source_hash,
                        "document_metadata": document.metadata_json,
                        "index_generation": document.active_generation,
                        "child_match": _citation(child, document.source),
                        "cited_parent": _citation(parent, document.source),
                    }
                )
                if len(results) == limit:
                    break
            offset += len(points)
            if len(points) < page_size:
                break
        return results
    finally:
        qdrant.close()
