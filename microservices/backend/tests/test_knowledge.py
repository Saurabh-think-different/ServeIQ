"""Offline provider contract tests: no real OpenAI requests or source transfers."""

import hashlib
import json
import shutil
import socket
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from qdrant_client import QdrantClient, models

from phdata.ingestion import knowledge as kb


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in knowledge tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(kb.time, "sleep", lambda _: None)


@pytest.fixture
def setup():
    # Keep all test filesystem operations inside the project, not system temp dirs.
    parent = Path(__file__).resolve().parents[1] / ".knowledge-test-data"
    root = parent / str(uuid.uuid4())
    (root / "kb").mkdir(parents=True)
    settings = SimpleNamespace(
        data_dir=root, openai_api_key="test-only-not-a-real-key",
        embedding_model="text-embedding-3-small", embedding_dimensions=3,
        qdrant_collection="servewell-kb", qdrant_url="http://unused.invalid",
    )
    client = QdrantClient(location=":memory:")
    provider = Mock()

    def embed(**kwargs):
        return SimpleNamespace(
            model=kwargs["model"],
            data=[SimpleNamespace(index=i, embedding=[1.0, 2.0, 3.0])
                  for i in reversed(range(len(kwargs["input"])))],
        )

    provider.embeddings.create.side_effect = embed
    stage = kb.KnowledgeIngestion(settings, openai_client=provider, qdrant_client=client)
    try:
        yield settings, client, provider, stage
    finally:
        client.close()
        shutil.rmtree(root)
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()


def write(settings, content="# Guide\n\n## Start\nRestart the device.\n", name="guide.md"):
    path = settings.data_dir / "kb" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def points(settings, client):
    return {str(p.id): p.payload for p in client.scroll(
        settings.qdrant_collection, limit=1000, with_payload=True,
    )[0]}


def seed_collection(settings, client, *, model=None, dimensions=None, metadata=True):
    client.create_collection(
        settings.qdrant_collection,
        vectors_config=models.VectorParams(
            size=dimensions or settings.embedding_dimensions, distance=models.Distance.COSINE,
        ),
        metadata={"embedding_model": model or settings.embedding_model,
                  "embedding_dimensions": settings.embedding_dimensions} if metadata else None,
    )


def test_batching_repeat_and_provenance(setup, monkeypatch):
    settings, client, provider, stage = setup
    raw = '---\ndocument_type: runbook\ncategory: "POS"\nmodel: OrbitPOS\nversion: 2.1\n---\n# Guide\n\n## Start\n' + "A" * 7000
    write(settings, raw)
    monkeypatch.setattr(kb, "BATCH_SIZE", 2)
    monkeypatch.setattr(kb, "PAGE_SIZE", 2)
    report = stage.run()
    stored = points(settings, client)
    assert report.status == "succeeded"
    assert report.inserted == {"chunks": len(stored)}
    assert len(stored) > 2
    assert provider.embeddings.create.call_count == (len(stored) + 1) // 2
    for call in provider.embeddings.create.call_args_list:
        assert len(call.kwargs["input"]) <= 2
        assert call.kwargs["model"] == settings.embedding_model
        assert call.kwargs["dimensions"] == 3
        assert call.kwargs["timeout"] == kb.TIMEOUT_SECONDS
        assert call.kwargs["encoding_format"] == "float"
    for point_id, payload in stored.items():
        assert uuid.UUID(point_id).version == 5
        assert payload["ingestion_namespace"] == kb.NAMESPACE
        assert payload["source_path"] == "kb/guide.md"
        assert payload["section"] in {"Guide", "Guide > Start"}
        assert payload["document_type"] == "runbook"
        assert payload["category"] == "POS"
        assert payload["model"] == "OrbitPOS"
        assert payload["version"] == "2.1"
        assert payload["embedding_model"] == settings.embedding_model
        assert payload["embedding_dimensions"] == 3
        assert payload["source_hash"] == hashlib.sha256(raw.encode()).hexdigest()
        assert payload["content_hash"] == hashlib.sha256(payload["content"].encode()).hexdigest()
        assert len(payload["content"]) <= kb.CHUNK_CHARS
    calls = provider.embeddings.create.call_count
    repeat = stage.run()
    assert repeat.status == "succeeded"
    assert repeat.skipped == {"chunks": len(stored)}
    assert repeat.inserted == repeat.deleted == {}
    assert provider.embeddings.create.call_count == calls
    assert points(settings, client) == stored


def test_changed_sections_and_disappeared_documents_reconciled(setup, monkeypatch):
    settings, client, provider, stage = setup
    monkeypatch.setattr(kb, "PAGE_SIZE", 1)
    path = write(settings, "# Guide\n## First\nOld section.\n## Removed\nRetire me.\n")
    removed = write(settings, "# Retired\nOld document.\n", "retired.md")
    assert stage.run().status == "succeeded"
    old = points(settings, client)
    unrelated_id = str(uuid.uuid4())
    client.upsert(settings.qdrant_collection, [models.PointStruct(
        id=unrelated_id, vector=[1, 2, 3], payload={"ingestion_namespace": "someone-else"},
    )])
    path.write_text("# Guide\n## First\nNew section.\n## Added\nNew evidence.\n")
    removed.unlink()
    report = stage.run()
    current = points(settings, client)
    assert report.status == "succeeded"
    assert report.deleted == {"chunks": len(set(old) - set(current))}
    assert unrelated_id in current
    assert not any(p.get("source_path") == "kb/retired.md" for p in current.values())
    assert not any("Removed" in p.get("section", "") for p in current.values())
    assert any("Added" in p.get("section", "") for p in current.values())


@pytest.mark.parametrize("failure_phase", ["embedding", "upsert"])
def test_no_cleanup_after_partial_provider_failure(setup, monkeypatch, failure_phase):
    settings, client, provider, stage = setup
    path = write(settings)
    assert stage.run().status == "succeeded"
    old = points(settings, client)
    path.write_text("# Changed\n## First\n" + "x" * 5000)
    monkeypatch.setattr(kb, "BATCH_SIZE", 1)
    delete = Mock(wraps=client.delete)
    monkeypatch.setattr(client, "delete", delete)
    sensitive_error = RuntimeError("SECRET fake key and private source content")
    if failure_phase == "embedding":
        original = provider.embeddings.create.side_effect
        count = 0

        def fail_later(**kwargs):
            nonlocal count
            count += 1
            if count > 1:
                raise sensitive_error
            return original(**kwargs)

        provider.embeddings.create.side_effect = fail_later
    else:
        original = client.upsert
        count = 0

        def fail_later(**kwargs):
            nonlocal count
            count += 1
            if count > 1:
                raise sensitive_error
            return original(**kwargs)

        monkeypatch.setattr(client, "upsert", fail_later)
    report = stage.run()
    assert report.status == "failed"
    assert report.inserted == {"chunks": 1}
    assert len(points(settings, client)) > len(old)
    assert all(points(settings, client)[key] == value for key, value in old.items())
    assert report.deleted == {}
    delete.assert_not_called()
    assert count == 1 + kb.MAX_ATTEMPTS
    assert "SECRET" not in report.model_dump_json()
    assert failure_phase in report.errors[0]
    if failure_phase == "embedding":
        provider.embeddings.create.side_effect = original
    else:
        monkeypatch.setattr(client, "upsert", original)
    recovered = stage.run()
    assert recovered.status == "succeeded"
    assert recovered.skipped == {"chunks": 1}
    assert not set(old).intersection(points(settings, client))
    assert recovered.deleted == {"chunks": len(old)}


@pytest.mark.parametrize("kind", ["model", "dimensions", "point-model", "unverified", "empty-unverified", "named", "distance"])
def test_embedding_space_mismatch_blocks(setup, kind):
    settings, client, provider, stage = setup
    write(settings)
    if kind in {"named", "distance"}:
        vector = models.VectorParams(size=3, distance=models.Distance.DOT if kind == "distance" else models.Distance.COSINE)
        client.create_collection(settings.qdrant_collection, vectors_config={"dense": vector} if kind == "named" else vector)
    else:
        seed_collection(settings, client, model="other-model" if kind == "model" else None,
                        dimensions=4 if kind == "dimensions" else None,
                        metadata=kind not in {"unverified", "empty-unverified"})
        if kind in {"point-model", "unverified"}:
            client.upsert(settings.qdrant_collection, [models.PointStruct(
                id=str(uuid.uuid4()), vector=[1, 2, 3],
                payload={"embedding_model": "other-model", "embedding_dimensions": 3} if kind == "point-model" else {},
            )])
    report = stage.run()
    assert report.status == "blocked"
    assert "new collection or rebuild" in report.errors[0]
    provider.embeddings.create.assert_not_called()


def test_legacy_per_payload_identity_supported(setup):
    settings, client, provider, stage = setup
    write(settings)
    seed_collection(settings, client, metadata=False)
    foreign = str(uuid.uuid4())
    client.upsert(settings.qdrant_collection, [models.PointStruct(
        id=foreign, vector=[1, 2, 3], payload={"embedding_model": settings.embedding_model, "embedding_dimensions": 3},
    )])
    assert stage.run().status == "succeeded"
    assert foreign in points(settings, client)


@pytest.mark.parametrize("bad", ["short", "nan", "inf", "bool", "duplicate", "missing", "negative", "outside", "float-index", "wrong-model"])
def test_invalid_embedding_response_never_writes_or_cleans(setup, monkeypatch, bad):
    settings, client, provider, stage = setup
    path = write(settings)
    assert stage.run().status == "succeeded"
    old = points(settings, client)
    path.write_text("# Changed\nNew text.\n## More\nMore text.\n")
    upsert, delete = Mock(wraps=client.upsert), Mock(wraps=client.delete)
    monkeypatch.setattr(client, "upsert", upsert)
    monkeypatch.setattr(client, "delete", delete)
    original = provider.embeddings.create.side_effect

    def invalid(**kwargs):
        response = original(**kwargs)
        first = response.data[0]
        if bad == "short":
            first.embedding = [1.0]
        elif bad in {"nan", "inf", "bool"}:
            first.embedding[0] = {"nan": float("nan"), "inf": float("inf"), "bool": True}[bad]
        elif bad == "duplicate":
            response.data[-1].index = first.index
        elif bad == "missing":
            response.data.pop()
        elif bad in {"negative", "outside", "float-index"}:
            first.index = {"negative": -1, "outside": 1000, "float-index": 0.0}[bad]
        else:
            response.model = "unexpected-model"
        return response

    provider.embeddings.create.side_effect = invalid
    report = stage.run()
    assert report.status == "failed"
    assert points(settings, client) == old
    upsert.assert_not_called()
    delete.assert_not_called()


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_credentials_before_any_client_or_network(setup, monkeypatch, key):
    settings, client, provider, stage = setup
    settings.openai_api_key = key
    constructors = Mock(side_effect=AssertionError("Must not initialize providers"))
    monkeypatch.setattr(kb, "OpenAI", constructors)
    monkeypatch.setattr(kb, "QdrantClient", constructors)
    report = kb.KnowledgeIngestion(settings).run()
    assert report.status == "blocked"
    constructors.assert_not_called()


def test_only_kb_markdown_not_labels_contacts_tickets_or_symlinks(setup):
    settings, client, provider, stage = setup
    write(settings, "# Allowed\nPublic knowledge.\n", "faq/allowed.md")
    for name in ["labels/leak.md", "contacts/leak.md", "tickets/leak.md", ".hidden.md", "labels.md", "contact.md", "tickets.md"]:
        write(settings, "FORBIDDEN CONTENT", name)
    for name in ["labels.md", "tickets/leak.md", "contacts/leak.md"]:
        path = settings.data_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("FORBIDDEN CONTENT")
    write(settings, "FORBIDDEN CONTENT", "ignore.json")
    (settings.data_dir / "kb" / "external.md").symlink_to(settings.data_dir / "labels.md")
    (settings.data_dir / "kb" / "external-dir").symlink_to(settings.data_dir / "contacts", target_is_directory=True)
    assert stage.run().status == "succeeded"
    assert "FORBIDDEN" not in json.dumps([call.kwargs for call in provider.embeddings.create.call_args_list])
    assert {p["source_path"] for p in points(settings, client).values()} == {"kb/faq/allowed.md"}
    assert all(not kb.METADATA_KEYS.intersection(p) for p in points(settings, client).values())


@pytest.mark.parametrize("mode", ["missing", "empty", "blank", "symlink"])
def test_empty_root_fails_without_destructive_cleanup(setup, mode):
    settings, client, provider, stage = setup
    path = write(settings)
    assert stage.run().status == "succeeded"
    old = points(settings, client)
    path.unlink()
    root = settings.data_dir / "kb"
    if mode == "missing":
        root.rmdir()
    elif mode == "blank":
        write(settings, "  \n")
    elif mode == "symlink":
        root.rmdir()
        alternate = settings.data_dir / "other"
        alternate.mkdir()
        (alternate / "document.md").write_text("# Outside\nNot approved")
        root.symlink_to(alternate, target_is_directory=True)
    report = stage.run()
    assert report.status == "failed"
    assert points(settings, client) == old


def test_fenced_headers_metadata_and_duplicate_sections(setup):
    settings, client, provider, stage = setup
    write(settings, "# Manual\n**Document Type:** specification\n**Model:** POS\n**Version:** 5.1\n"
          "## Start\n```text\n# not a heading\n```\n## Start\nSecond section.\n")
    assert stage.run().status == "succeeded"
    stored = list(points(settings, client).values())
    assert all(p["document_type"] == "specification" and p["model"] == "POS" and p["version"] == "5.1" for p in stored)
    assert any("# not a heading" in p["content"] and p["section"] == "Manual > Start" for p in stored)
    assert {p["section_occurrence"] for p in stored if p["section"] == "Manual > Start"} == {0, 1}


def test_unconfirmed_upsert_prevents_cleanup(setup, monkeypatch):
    settings, client, provider, stage = setup
    path = write(settings)
    assert stage.run().status == "succeeded"
    old = points(settings, client)
    path.write_text("# Changed\nReplacement\n")
    monkeypatch.setattr(client, "upsert", Mock(return_value=SimpleNamespace(status=models.UpdateStatus.ACKNOWLEDGED)))
    assert stage.run().status == "failed"
    assert points(settings, client) == old


def test_source_traversal_errors_fail_closed(setup, monkeypatch):
    settings, client, provider, stage = setup
    write(settings)
    assert stage.run().status == "succeeded"
    old = points(settings, client)

    def unreadable(self, *, on_error, follow_symlinks):
        on_error(PermissionError("PRIVATE source path"))
        yield  # Preserve the Path.walk iterator contract.

    monkeypatch.setattr(Path, "walk", unreadable)
    report = stage.run()
    assert report.status == "failed"
    assert "PRIVATE" not in report.model_dump_json()
    assert points(settings, client) == old


def test_direct_clients_have_explicit_timeout_and_retry_budgets(setup, monkeypatch):
    settings, client, provider, stage = setup
    write(settings)
    openai_constructor = Mock(return_value=provider)
    qdrant_constructor = Mock(return_value=client)
    close = Mock(wraps=client.close)
    monkeypatch.setattr(kb, "OpenAI", openai_constructor)
    monkeypatch.setattr(kb, "QdrantClient", qdrant_constructor)
    monkeypatch.setattr(client, "close", close)
    assert kb.KnowledgeIngestion(settings).run().status == "succeeded"
    openai_constructor.assert_called_once_with(
        api_key=settings.openai_api_key, timeout=kb.TIMEOUT_SECONDS, max_retries=0,
    )
    qdrant_constructor.assert_called_once_with(url=settings.qdrant_url, timeout=kb.TIMEOUT_SECONDS)
    provider.close.assert_called_once()
    close.assert_called_once()


def test_provider_budget_is_bounded(setup, monkeypatch):
    settings, client, provider, stage = setup
    write(settings)
    monkeypatch.setattr(kb, "MAX_CALLS", 1)
    report = stage.run()
    assert report.status == "failed"
    provider.embeddings.create.assert_not_called()
    assert stage._calls == 1
