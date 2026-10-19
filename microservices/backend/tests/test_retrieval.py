from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import socket
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from phdata.config import get_settings
from phdata.ingestion.knowledge import NAMESPACE
from phdata.main import app
from phdata.models.retrieval import SearchRequest
from phdata.routes.knowledge import retrieval_service
from phdata.services.retrieval import KnowledgeRetrieval, RetrievalError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Real network calls are forbidden")
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def search():
    settings = replace(get_settings(), openai_api_key="test-only", embedding_dimensions=3)
    client = QdrantClient(location=":memory:")
    client.create_collection(
        settings.qdrant_collection,
        vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE),
        metadata={"embedding_model": settings.embedding_model, "embedding_dimensions": 3},
    )
    provider = Mock()
    provider.embeddings.create.return_value = SimpleNamespace(
        model=settings.embedding_model, data=[SimpleNamespace(index=0, embedding=[1.0, 0.0, 0.0])],
    )
    service = KnowledgeRetrieval(settings, openai_client=provider, qdrant_client=client)
    yield service, client, provider
    client.close()


def add(service, client, **overrides):
    payload = {
        "ingestion_namespace": NAMESPACE,
        "embedding_model": service.settings.embedding_model, "embedding_dimensions": 3,
        "source_path": "kb/runbooks/printer-offline.md", "content": "Check the printer power indicator.",
        "section": "Printer > Power", "document_type": "runbook",
    }
    payload.update(overrides)
    client.upsert(service.settings.qdrant_collection, points=[
        models.PointStruct(id=str(uuid4()), vector=[1.0, 0.0, 0.0], payload=payload),
    ])


def test_cited_result_and_query_redaction(search):
    service, client, provider = search
    add(service, client)
    result = service.search(SearchRequest(query="Printer offline; contact user@example.com +1 212 555 1234"))
    assert not result.evidence_gap
    passage = result.passages[0]
    assert passage.source_path == "kb/runbooks/printer-offline.md"
    assert passage.section == "Printer > Power"
    assert passage.applicability == "unknown"
    sent = provider.embeddings.create.call_args.kwargs["input"][0]
    assert "user@example.com" not in sent and "212 555 1234" not in sent
    assert any("redacted" in warning for warning in result.warnings)


def test_exact_metadata_filters_incompatible_but_allows_generic(search):
    service, client, _ = search
    add(service, client, model="OtherModel", version="9")
    add(service, client, content="Generic reviewed guidance.")
    result = service.search(SearchRequest(query="printer offline", model="Epson", system_version="1"))
    assert len(result.passages) == 1
    assert result.passages[0].content == "Generic reviewed guidance."
    assert result.passages[0].applicability == "unknown"


def test_complete_explicit_metadata_is_only_a_context_match(search):
    service, client, _ = search
    add(service, client, category="Printers", model="Epson", version="1")
    result = service.search(SearchRequest(query="printer", category="Printers", model="Epson", system_version="1"))
    assert result.passages[0].applicability == "context_match"
    assert any("not confidence" in warning for warning in result.warnings)


@pytest.mark.parametrize("path", ["tickets/train/ticket.json", "kb/../labels/answers.md", "/tmp/guide.md", "kb/labels/answer.md"])
def test_non_knowledge_sources_cannot_be_returned(search, path):
    service, client, _ = search
    add(service, client, source_path=path)
    assert service.search(SearchRequest(query="printer")).evidence_gap


def test_empty_index_returns_evidence_gap(search):
    service, _, _ = search
    assert service.search(SearchRequest(query="printer")).passages == []


@pytest.mark.parametrize("vector", [[1.0], [float("nan"), 0.0, 0.0], [True, 0.0, 0.0]])
def test_invalid_embedding_response_is_sanitized(search, vector):
    service, _, provider = search
    provider.embeddings.create.return_value.data[0].embedding = vector
    with pytest.raises(RetrievalError) as error:
        service.search(SearchRequest(query="printer"))
    assert error.value.status_code == 503


def test_stale_embedding_identity_blocks_before_provider_call(search):
    service, client, provider = search
    add(service, client, embedding_model="other-space")
    with pytest.raises(RetrievalError) as error:
        service.search(SearchRequest(query="printer"))
    assert error.value.status_code == 409
    provider.embeddings.create.assert_not_called()


def test_missing_key_and_missing_collection_are_explicit(search):
    service, client, provider = search
    service.settings = replace(service.settings, openai_api_key=None)
    with pytest.raises(RetrievalError) as error:
        service.search(SearchRequest(query="printer"))
    assert error.value.status_code == 503
    service.settings = replace(service.settings, openai_api_key="test-only")
    client.delete_collection(service.settings.qdrant_collection)
    with pytest.raises(RetrievalError, match="missing"):
        service.search(SearchRequest(query="printer"))
    provider.embeddings.create.assert_not_called()


def test_provider_errors_never_expose_raw_message(search):
    service, _, provider = search
    provider.embeddings.create.side_effect = RuntimeError("sensitive-token-value")
    with pytest.raises(RetrievalError) as error:
        service.search(SearchRequest(query="printer"))
    assert "sensitive-token-value" not in error.value.detail


def test_registered_http_search_validation_and_result(search):
    service, client, _ = search
    add(service, client)
    app.dependency_overrides[retrieval_service] = lambda: service
    try:
        with TestClient(app) as api:
            response = api.post("/api/knowledge/search", json={"query": "printer"})
            assert response.status_code == 200
            assert response.json()["passages"][0]["source_path"].startswith("kb/")
            for payload in ({"query": " "}, {"query": "printer", "limit": 11}, {"query": "printer", "collection": "other"}):
                assert api.post("/api/knowledge/search", json=payload).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_http_search_requires_operator(monkeypatch):
    monkeypatch.setenv("INGESTION_TOKEN", "test-operator")
    with TestClient(app) as api:
        assert api.post("/api/knowledge/search", json={"query": "printer"}).status_code == 401
