# ServeWell Backend

FastAPI service for incident intake, triage, and support-agent workflows.

## Run

From the repository root:

```bash
uv sync
uv run --project microservices/backend servewell-api
```

For development with reload:

```bash
uv run --project microservices/backend uvicorn phdata.main:app --reload
```

The API health check is available at `/api/health`; interactive API docs are at
`/docs`. `/api/health/ready` opens/initializes the configured SQLite database and
returns `503` with a sanitized error if it is unavailable. Liveness does not
access the database, Qdrant, or an external model.

For Rancher Desktop and Skaffold, use the [root startup guide](../../README.md).
The container image installs the backend non-editably from the root frozen uv
lockfile and bundles curated source data without evaluation labels.

## Configuration

The API and ingestion stages share `src/phdata/config.py`:

| Variable | Checkout default | Container default |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | `0.0.0.0` |
| `PORT` | `8000` | `8000` |
| `DATA_DIR` | Repository `client_data/` | `/opt/dataset` |
| `DATABASE_PATH` | `microservices/backend/.local/servewell.db` | `/var/lib/servewell/servewell.db` |
| `QDRANT_URL` | `http://127.0.0.1:6333` | `http://qdrant:6333` |
| `INGESTION_TOKEN` | Unset; import route disabled | Optional Kubernetes Secret |
| `OPENAI_API_KEY` | Unset; knowledge stage blocked | Optional Kubernetes Secret |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Same |
| `EMBEDDING_DIMENSIONS` | `1536` | Same |
| `QDRANT_COLLECTION` | `servewell-kb` | Same |

Explicit container paths avoid assuming a source checkout inside site-packages.
Outside a checkout, defaults are relative to the working directory; configure
`DATA_DIR` and `DATABASE_PATH` explicitly. The optional `servewell-ingestion`
Kubernetes Secret supplies the operator/provider keys; see the root guide.
Never log, commit, or embed either credential in a browser.

## HTTP ingestion

The CLI has been removed. Call the API against server-configured inputs:

```bash
curl --fail-with-body http://127.0.0.1:8000/api/ingestions \
  -H "Authorization: Bearer $INGESTION_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"target":"all"}'
```

Select `structured`, `knowledge`, or `all`; extra fields and arbitrary input
paths are rejected. The endpoint is synchronous and serializes ingestion within
the documented one-process/one-replica deployment. It returns independent stage
reports and overall `succeeded`, `partial`, or `failed` status. HTTP `200` means
the report is available, not that every stage succeeded. A failed stage does not
roll back another store's completed work.

- **Structured:** pandas reads CSVs as strings without implicit null inference;
  Pydantic validates rows and nested ticket JSON. SQLite writes are transactional,
  repeated imports are duplicate-safe, and unknown/mismatched store/asset IDs
  remain unresolved with warnings. The explicit `tickets/raw` → `tickets/train`
  mapping is reported. Source history/relations removed from an imported ticket
  are deleted transactionally; agent runs/audit events are not touched.
- **Knowledge:** section-aware Markdown chunks under `kb/` are embedded through
  OpenAI and indexed in Qdrant with stable IDs, content hashes and provenance.
  Unchanged chunks are skipped; stale chunks are removed only after successful
  replacement. Model/dimension mismatch blocks indexing. Indexing does not decide
  which conflicting runbook instruction is safe for an operator.

Reports contain inserted/updated/skipped/deleted counts, warnings and sanitized
errors. Provider failures never return raw credential-bearing exception text.
Missing OpenAI credentials block only knowledge ingestion. Tests inject fake
embeddings and never send client documents to OpenAI.

Structured imports remain source snapshots, not a merge API for human-edited
operational records. Fresh incident HTTP intake is separate future work; the
structured module retains a validated single-ticket library function for that
adapter. There is no route accepting arbitrary local file paths.

## Test

```bash
uv run --project microservices/backend pytest
```

## Layout

```text
backend/
├── pyproject.toml
├── Dockerfile
├── k8s.yaml
├── src/
│   └── phdata/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── routes/
│       │   ├── __init__.py
│       │   ├── router.py
│       │   ├── health.py
│       │   └── ingestion.py
│       ├── db/
│       │   ├── __init__.py
│       │   ├── database.py
│       │   ├── schema.py
│       │   └── schema.sql
│       └── ingestion/
│           ├── __init__.py
│           ├── interfaces.py
│           ├── schemas.py
│           ├── structured.py
│           ├── knowledge.py
│           └── pipeline.py
└── tests/
```

`src/` is the Python source root, not another application package. `phdata/`
contains the single backend package; there are no sibling application layers
under `src/`. Retaining this package name preserves the API entrypoint and imports.

- `main.py`: FastAPI app, route registration, and server entrypoint.
- `config.py`: shared environment settings and development defaults.
- `routes/`: HTTP routers; liveness `/api/health` and readiness `/api/health/ready`.
- `db/`: SQLite connection and schema initialization.
- `ingestion/interfaces.py`: `IngestionStage.run()` protocol and shared typed report.
- `ingestion/schemas.py`: Pydantic source-data validation, not handwritten parsers.
- `ingestion/structured.py`: pandas input loading and concrete SQLite import logic.
- `ingestion/knowledge.py`: bounded Markdown chunks, OpenAI embeddings and Qdrant writes.
- `ingestion/pipeline.py`: stage selection and independent result aggregation.
- `tests/`: HTTP, source validation, database and offline provider regression tests.

Add additional layers only with concrete behavior. No plugin registry, abstract
factory hierarchy, background worker, or generic `core/utils` scaffolding is
needed. Support workflows and a retrieval/query endpoint remain future work.

API references were fetched through Context Hub for pandas, Pydantic, FastAPI,
OpenAI and qdrant-client. Indexing/querying must use the same embedding model and
vector size. Changing them requires a new compatible collection/rebuild; never
mix vectors from different embedding spaces. Labels, evaluator notes and ticket
contact details stay out of the knowledge input.