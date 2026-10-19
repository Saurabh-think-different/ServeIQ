"""Bounded Markdown ingestion; Qdrant writes are not a cross-store transaction.

Only this module's namespace is reconciled. Replacement IDs are content addressed,
so old evidence survives failed writes. Operators must serialize runs per collection.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI
from qdrant_client import QdrantClient, models

from phdata.ingestion.interfaces import IngestionError, IngestionReport, IngestionStage

NAMESPACE = "servewell.knowledge.v1"
POINT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, NAMESPACE)
CHUNK_CHARS = 1600
BATCH_SIZE = 16
PAGE_SIZE = 128
TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3
MAX_CALLS = 2000
MAX_PAGES = 1000
MAX_DOCUMENTS = 2000
MAX_SOURCE_BYTES = 20_000_000
MAX_DOCUMENT_BYTES = 2_000_000
MAX_CHUNKS = 50_000
METADATA_KEYS = {"document_type", "category", "model", "version"}
EXCLUDED_PARTS = {"labels", "label", "tickets", "contacts", "contact"}


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _metadata_and_body(text: str) -> tuple[dict[str, str], str]:
    """Read only explicit flat scalar metadata, never infer product versions."""
    metadata: dict[str, str] = {}
    lines = text.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError("Unterminated frontmatter")
        for line in lines[1:end]:
            match = re.fullmatch(r"([\w_]+):\s*(.*?)\s*", line.strip())
            if match and match[1] in METADATA_KEYS and match[2]:
                value = match[2]
                if value[0] in "'\"" and value[-1] == value[0]:
                    value = value[1:-1]
                elif value[0] in "[{&*!|>" or value in {"null", "~"}:
                    continue
                metadata[match[1]] = value
        lines = lines[end + 1:]
    body = "".join(lines)
    for key, value in re.findall(
        r"^\*\*(Document Type|Category|Model|Version):\*\*\s*([^\n]+)$", body, re.MULTILINE
    ):
        metadata.setdefault(key.lower().replace(" ", "_"), value.strip())
    return metadata, body


def _sections(body: str):
    headings: list[tuple[int, str]] = []
    section = ""
    content: list[str] = []
    fence: str | None = None
    fence_length = 0
    for line in body.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence, fence_length = marker[1][0], len(marker[1])
            elif marker[1][0] == fence and len(marker[1]) >= fence_length:
                fence = None
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line) if fence is None else None
        if heading:
            if "".join(content).strip():
                yield section, "".join(content).strip()
            level, title = len(heading[1]), heading[2]
            headings = [(n, t) for n, t in headings if n < level]
            headings.append((level, title))
            section = " > ".join(title for _, title in headings)
            content = [line]
        else:
            content.append(line)
    if "".join(content).strip():
        yield section, "".join(content).strip()


def _source_paths(root: Path):
    def fail(error):
        raise error

    # rglob suppresses some traversal errors, which could mistake unreadable
    # documents for deletions. Fail closed instead, and prune disallowed trees.
    for directory, directories, filenames in root.walk(on_error=fail, follow_symlinks=False):
        directories[:] = sorted(
            name for name in directories
            if not name.startswith(".") and name.lower() not in EXCLUDED_PARTS
        )
        for name in sorted(filenames):
            yield directory / name


def _chunks(data_dir: Path) -> dict[str, dict[str, Any]]:
    root = data_dir / "kb"
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Missing knowledge root")
    chunks: dict[str, dict[str, Any]] = {}
    total_bytes = documents = 0
    for path in _source_paths(root):
        relative = path.relative_to(data_dir)
        parts = relative.parts[1:]
        if any(Path(part).stem.lower() in EXCLUDED_PARTS or part.startswith(".") for part in parts):
            continue
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != data_dir):
            continue
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        documents += 1
        if documents > MAX_DOCUMENTS or path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ValueError("Source budget exceeded")
        raw = path.read_bytes()
        total_bytes += len(raw)
        if total_bytes > MAX_SOURCE_BYTES or len(raw) > MAX_DOCUMENT_BYTES:
            raise ValueError("Source budget exceeded")
        metadata, body = _metadata_and_body(raw.decode("utf-8-sig"))
        occurrences: dict[str, int] = {}
        for section, text in _sections(body):
            occurrence = occurrences.get(section, 0)
            occurrences[section] = occurrence + 1
            for index, start in enumerate(range(0, len(text), CHUNK_CHARS)):
                content = text[start:start + CHUNK_CHARS].strip()
                if not content:
                    continue
                payload = {
                    **metadata,
                    "ingestion_namespace": NAMESPACE,
                    "source_path": relative.as_posix(),
                    "section": section,
                    "section_occurrence": occurrence,
                    "chunk_index": index,
                    "chunker_version": 1,
                    "source_hash": _hash(raw),
                    "content_hash": _hash(content.encode()),
                    "content": content,
                }
                identity = json.dumps(payload, sort_keys=True, ensure_ascii=False)
                point_id = str(uuid.uuid5(POINT_NAMESPACE, identity))
                chunks[point_id] = payload
                if len(chunks) > MAX_CHUNKS:
                    raise ValueError("Chunk budget exceeded")
    if not chunks:
        raise ValueError("No knowledge content")
    return chunks


class KnowledgeIngestion(IngestionStage):
    """Use settings' embedding space for ingestion and subsequent query callers.

    Injected clients must honor the same timeout/retry contract as the direct SDKs.
    The OpenAI SDK's implicit retries are disabled; each operation gets at most
    three attempts and the whole run at most MAX_CALLS provider attempts.
    """

    def __init__(self, settings: Any, *, openai_client: Any = None, qdrant_client: Any = None):
        self.settings = settings
        self.openai_client = openai_client
        self.qdrant_client = qdrant_client
        self._calls = 0

    def _call(self, operation: Callable, **kwargs):
        for attempt in range(MAX_ATTEMPTS):
            if self._calls >= MAX_CALLS:
                raise ValueError("Provider call budget exceeded")
            self._calls += 1
            try:
                return operation(**kwargs)
            except Exception:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                time.sleep(0.05 * (2 ** attempt))

    def _existing(self, client, collection, model, dimensions):
        info = self._call(client.get_collection, collection_name=collection)
        vectors = info.config.params.vectors
        if not isinstance(vectors, models.VectorParams) or vectors.size != dimensions or vectors.distance != models.Distance.COSINE:
            raise IngestionError(["Knowledge vector schema mismatch; use a new collection or rebuild."])
        metadata = info.config.metadata or {}
        marked = "embedding_model" in metadata or "embedding_dimensions" in metadata
        if marked and (metadata.get("embedding_model"), metadata.get("embedding_dimensions")) != (model, dimensions):
            raise IngestionError(["Knowledge embedding space mismatch; use a new collection or rebuild."])
        existing, all_ids = {}, set()
        offset = None
        seen_offsets = set()
        for _ in range(MAX_PAGES):
            records, next_offset = self._call(
                client.scroll, collection_name=collection, limit=PAGE_SIZE, offset=offset,
                with_payload=True, with_vectors=False, timeout=TIMEOUT_SECONDS,
            )
            for record in records:
                payload = record.payload or {}
                identity = (payload.get("embedding_model"), payload.get("embedding_dimensions"))
                if (not marked or any(value is not None for value in identity)) and identity != (model, dimensions):
                    raise IngestionError(["Knowledge point embedding space is unverified or mismatched; use a new collection or rebuild."])
                all_ids.add(str(record.id))
                if payload.get("ingestion_namespace") == NAMESPACE:
                    existing[str(record.id)] = payload
            if next_offset is None:
                if not marked and not all_ids:
                    raise IngestionError(["Knowledge collection embedding identity is unverified; use a new collection or rebuild."])
                return existing, all_ids
            if str(next_offset) in seen_offsets:
                raise ValueError("Repeated scroll offset")
            seen_offsets.add(str(next_offset))
            offset = next_offset
        raise ValueError("Scroll budget exceeded")

    def _embed(self, client, payloads, model, dimensions):
        response = self._call(
            client.embeddings.create, input=[p["content"] for p in payloads], model=model,
            dimensions=dimensions, encoding_format="float", timeout=TIMEOUT_SECONDS,
        )
        if response.model != model or len(response.data) != len(payloads):
            raise ValueError("Invalid embedding response")
        vectors = [None] * len(payloads)
        for item in response.data:
            if type(item.index) is not int or not 0 <= item.index < len(payloads) or vectors[item.index] is not None:
                raise ValueError("Invalid embedding index")
            vector = item.embedding
            if len(vector) != dimensions or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in vector
            ):
                raise ValueError("Invalid embedding vector")
            vectors[item.index] = vector
        return vectors

    def run(self) -> IngestionReport:
        report = IngestionReport(stage="knowledge")
        self._calls = 0
        key = getattr(self.settings, "openai_api_key", None)
        if hasattr(key, "get_secret_value"):
            key = key.get_secret_value()
        if not isinstance(key, str) or not key.strip():
            report.status = "blocked"
            report.errors.append("OPENAI_API_KEY is required for knowledge ingestion.")
            return report
        owned_clients = []
        phase = "source validation"
        try:
            chunks = _chunks(Path(self.settings.data_dir))
            model = self.settings.embedding_model
            dimensions = self.settings.embedding_dimensions
            collection = self.settings.qdrant_collection
            if not model or type(dimensions) is not int or dimensions <= 0 or not collection:
                raise ValueError("Invalid embedding settings")
            for payload in chunks.values():
                payload.update(embedding_model=model, embedding_dimensions=dimensions)
            phase = "provider initialization"
            embeddings = self.openai_client
            if embeddings is None:
                embeddings = OpenAI(api_key=key, timeout=TIMEOUT_SECONDS, max_retries=0)
                owned_clients.append(embeddings)
            client = self.qdrant_client
            if client is None:
                client = QdrantClient(url=self.settings.qdrant_url, timeout=TIMEOUT_SECONDS)
                owned_clients.append(client)
            phase = "collection validation"
            if not self._call(client.collection_exists, collection_name=collection):
                created = self._call(
                    client.create_collection, collection_name=collection,
                    vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE),
                    metadata={"embedding_model": model, "embedding_dimensions": dimensions},
                    timeout=TIMEOUT_SECONDS,
                )
                if not created:
                    raise ValueError("Collection creation not confirmed")
            existing, all_ids = self._existing(client, collection, model, dimensions)
            pending = []
            for point_id, payload in chunks.items():
                if point_id in all_ids and point_id not in existing:
                    raise ValueError("Point ID is owned by another namespace")
                if existing.get(point_id) == payload:
                    report.skipped["chunks"] = report.skipped.get("chunks", 0) + 1
                else:
                    pending.append((point_id, payload))
            for start in range(0, len(pending), BATCH_SIZE):
                batch = pending[start:start + BATCH_SIZE]
                phase = "embedding"
                vectors = self._embed(embeddings, [p for _, p in batch], model, dimensions)
                points = [models.PointStruct(id=point_id, vector=vector, payload=payload)
                          for (point_id, payload), vector in zip(batch, vectors, strict=True)]
                phase = "upsert"
                result = self._call(client.upsert, collection_name=collection, points=points,
                                    wait=True, timeout=TIMEOUT_SECONDS)
                if result.status != models.UpdateStatus.COMPLETED:
                    raise ValueError("Upsert not confirmed")
                for point_id, _ in batch:
                    counts = report.updated if point_id in existing else report.inserted
                    counts["chunks"] = counts.get("chunks", 0) + 1
            # No cleanup is attempted until every replacement write is confirmed.
            phase = "stale cleanup"
            stale = sorted(existing.keys() - chunks.keys())
            for start in range(0, len(stale), BATCH_SIZE):
                ids = stale[start:start + BATCH_SIZE]
                result = self._call(client.delete, collection_name=collection,
                                    points_selector=models.PointIdsList(points=ids), wait=True,
                                    timeout=TIMEOUT_SECONDS)
                if result.status != models.UpdateStatus.COMPLETED:
                    raise ValueError("Deletion not confirmed")
                report.deleted["chunks"] = report.deleted.get("chunks", 0) + len(ids)
        except IngestionError as error:
            report.status = "blocked"
            report.errors.extend(error.errors)
        except Exception:
            report.status = "failed"
            report.errors.append(f"Knowledge {phase} failed; verify sources and provider configuration, then retry. No further stale cleanup was attempted.")
        finally:
            for client in reversed(owned_clients):
                try:
                    client.close()
                except Exception:
                    pass
        return report
