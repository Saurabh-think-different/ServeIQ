"""Single-query retrieval from the ingestion embedding space, without generation.

Only explicit document metadata is used for context; aliases are not inferred.
Injected clients remain caller-owned. Direct SDK clients are closed per search.
"""

from __future__ import annotations

import math
import re
from pathlib import PurePosixPath
from typing import Any

from phdata.config import Settings
from phdata.models.retrieval import Passage, SearchRequest, SearchResponse

TIMEOUT_SECONDS = 20
EXCLUDED_PARTS = {"labels", "label", "tickets", "ticket", "contacts", "contact"}
EMAIL = re.compile(r"[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\w)\+?\d(?:[\d ()-]{5,}\d)(?!\w)")


class RetrievalError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def redact_query(query: str) -> str:
    """Remove recognizable email/phone-like strings, not all possible personal data."""
    return PHONE.sub("[phone redacted]", EMAIL.sub("[email redacted]", query))


class KnowledgeRetrieval:
    def __init__(self, settings: Settings, *, openai_client: Any = None, qdrant_client: Any = None):
        self.settings = settings
        self.openai_client = openai_client
        self.qdrant_client = qdrant_client

    def search(self, request: SearchRequest) -> SearchResponse:
        key = self.settings.openai_api_key
        if hasattr(key, "get_secret_value"):
            key = key.get_secret_value()
        if not isinstance(key, str) or not key.strip():
            raise RetrievalError(503, "Configure OPENAI_API_KEY before knowledge search.")
        model, dimensions = self.settings.embedding_model, self.settings.embedding_dimensions
        collection = self.settings.qdrant_collection
        if not model or type(dimensions) is not int or dimensions <= 0 or not collection:
            raise RetrievalError(503, "Configure the knowledge embedding model, dimensions and collection.")
        query = redact_query(request.query)
        owned = []
        phase = "initialization"
        try:
            from qdrant_client import QdrantClient, models

            from phdata.ingestion.knowledge import NAMESPACE

            client = self.qdrant_client
            if client is None:
                client = QdrantClient(url=self.settings.qdrant_url, timeout=TIMEOUT_SECONDS)
                owned.append(client)
            phase = "collection validation"
            if not client.collection_exists(collection_name=collection):
                raise RetrievalError(503, "Knowledge collection is missing; run knowledge ingestion first.")
            info = client.get_collection(collection_name=collection)
            vectors = info.config.params.vectors
            if (not isinstance(vectors, models.VectorParams) or vectors.size != dimensions
                    or vectors.distance != models.Distance.COSINE):
                raise RetrievalError(409, "Knowledge vector schema mismatch; rebuild with the configured embedding space.")
            metadata = info.config.metadata or {}
            if ("embedding_model" in metadata or "embedding_dimensions" in metadata) and (
                metadata.get("embedding_model"), metadata.get("embedding_dimensions")
            ) != (model, dimensions):
                raise RetrievalError(409, "Knowledge embedding identity mismatch; rebuild the collection.")
            namespace = models.FieldCondition(key="ingestion_namespace", match=models.MatchValue(value=NAMESPACE))
            identity = [
                models.FieldCondition(key="embedding_model", match=models.MatchValue(value=model)),
                models.FieldCondition(key="embedding_dimensions", match=models.MatchValue(value=dimensions)),
            ]
            # Fail closed on mixed/stale namespace points before comparing scores.
            stale, _ = client.scroll(
                collection_name=collection,
                scroll_filter=models.Filter(must=[namespace], must_not=[models.Filter(must=identity)]),
                limit=1, with_payload=False, with_vectors=False, timeout=TIMEOUT_SECONDS,
            )
            if stale:
                raise RetrievalError(409, "Knowledge contains unverified or mixed embedding identities; rerun ingestion or rebuild.")
            conditions = [namespace, *identity]
            for field, expected in (("category", request.category), ("model", request.model),
                                    ("version", request.system_version)):
                if expected is not None:
                    conditions.append(models.Filter(should=[
                        models.FieldCondition(key=field, match=models.MatchValue(value=expected)),
                        models.IsEmptyCondition(is_empty=models.PayloadField(key=field)),
                    ]))
            phase = "embedding"
            embeddings = self.openai_client
            if embeddings is None:
                from openai import OpenAI

                embeddings = OpenAI(api_key=key, timeout=TIMEOUT_SECONDS, max_retries=0)
                owned.append(embeddings)
            response = embeddings.embeddings.create(
                input=[query], model=model, dimensions=dimensions,
                encoding_format="float", timeout=TIMEOUT_SECONDS,
            )
            if response.model != model or len(response.data) != 1:
                raise ValueError("Invalid embedding response")
            item = response.data[0]
            if type(item.index) is not int or item.index != 0:
                raise ValueError("Invalid embedding index")
            vector = item.embedding
            if len(vector) != dimensions or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in vector
            ):
                raise ValueError("Invalid embedding vector")
            phase = "query"
            hits = client.query_points(
                collection_name=collection, query=vector,
                query_filter=models.Filter(must=conditions), limit=min(50, request.limit * 5),
                with_payload=True, with_vectors=False, timeout=TIMEOUT_SECONDS,
            ).points
            passages = []
            for hit in hits:
                payload = hit.payload or {}
                if payload.get("ingestion_namespace") != NAMESPACE:
                    continue
                if (payload.get("embedding_model"), payload.get("embedding_dimensions")) != (model, dimensions):
                    raise RetrievalError(409, "Knowledge hit embedding identity mismatch; rebuild the collection.")
                passage = self._passage(payload, hit.score, request)
                if passage is not None:
                    passages.append(passage)
                if len(passages) == request.limit:
                    break
            warnings = ["Similarity scores are not confidence or proof of applicability."]
            if query != request.query:
                warnings.append("Recognizable email/phone-like query text was redacted; this is not comprehensive PII detection.")
            if not passages:
                warnings.append("Evidence gap: no eligible passages found in the bounded search; no guidance generated.")
            return SearchResponse(passages=passages, evidence_gap=not passages, warnings=warnings)
        except RetrievalError:
            raise
        except ImportError:
            raise RetrievalError(503, "Knowledge search dependencies are unavailable; restore backend dependencies.") from None
        except Exception:
            raise RetrievalError(503, f"Knowledge {phase} failed; verify provider availability and configuration, then retry.") from None
        finally:
            for client in reversed(owned):
                try:
                    client.close()
                except Exception:
                    pass

    @staticmethod
    def _passage(payload: dict, score: float, request: SearchRequest) -> Passage | None:
        path = payload.get("source_path")
        content, section = payload.get("content"), payload.get("section")
        if not isinstance(path, str) or "\\" in path:
            return None
        source = PurePosixPath(path)
        if (source.is_absolute() or not source.parts or source.parts[0] != "kb"
                or ".." in source.parts or source.suffix.lower() != ".md"
                or any(part.startswith(".") or PurePosixPath(part).stem.lower() in EXCLUDED_PARTS
                       for part in source.parts[1:])):
            return None
        if not isinstance(content, str) or not content.strip() or not isinstance(section, str):
            return None
        if len(content) > 1600 or isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            return None
        provenance = {}
        notes = []
        for field, expected in (("category", request.category), ("model", request.model),
                                ("version", request.system_version)):
            actual = payload.get(field)
            if actual is not None and (not isinstance(actual, str) or not actual.strip()):
                return None
            if expected is not None and actual is not None and actual != expected:
                return None
            provenance[field] = actual
            if actual is None or expected is None:
                notes.append(f"{field} applicability unknown: document metadata or verified query context missing.")
        document_type = payload.get("document_type")
        if document_type is not None and not isinstance(document_type, str):
            return None
        return Passage(
            content=content, source_path=path, section=section, similarity_score=score,
            category=provenance["category"], model=provenance["model"],
            system_version=provenance["version"], document_type=document_type,
            applicability="unknown" if notes else "context_match", applicability_notes=notes,
        )
