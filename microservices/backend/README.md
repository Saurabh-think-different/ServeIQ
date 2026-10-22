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
| `OPENAI_API_KEY` | Unset; provider calls disabled | Optional Kubernetes Secret |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Same |
| `EMBEDDING_DIMENSIONS` | `1536` | Same |
| `QDRANT_COLLECTION` | `servewell-kb` | Same |
| `GENERATION_MODEL` | Unset; no default | Server-side optional setting |
| `GENERATION_TIMEOUT_SECONDS` | `20` (bounded 1–120) | Same |

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
Missing OpenAI credentials block knowledge ingestion/search and generation when
invoked. Tests inject fake embeddings/recommendation clients and never send client
documents to OpenAI.

Structured imports remain source snapshots, not a merge API for human-edited
operational records. Fresh incident HTTP intake uses a separate repository;
the structured module retains a validated single-ticket source-import function. There is no route accepting arbitrary local file paths. Seed imports
refuse collisions with API-created incidents rather than silently overwriting them.

## Incident and retrieval APIs

The shared operator dependency in `auth.py` protects ingestion, incident reads,
intake, context and knowledge search. It remains a local operator token, not a
production identity/authorization system.

| Route | Purpose |
| --- | --- |
| `POST /api/incidents` | Validate fresh report, preserve reported IDs, resolve exact references, persist atomically; `201` + Location, duplicate `409`. |
| `GET /api/incidents` | Bounded list (`limit` 1–100, `offset` >= 0), total and optional exact status/category filters. |
| `GET /api/incidents/{ticket_id}` | Persisted detail/history and relationship warnings, missing `404`. |
| `GET /api/incident-context/{ticket_id}` | Exact typed context with resolved references, SLA targets, history, related IDs and explicit missing conditions. |
| `POST /api/knowledge/search` | Query plus optional category/model/system_version and limit 1–10; cited passages and applicability notes. |

Context excludes contact columns and historical resolution notes; free-text
incident/history remains operator-provided and may contain PII. No generated SQL,
LLM reasoning, inferred device telemetry or automatic actions occur. Clock targets
are timezone-aware but are not proof that an SLA was fulfilled/breached.

Retrieval validates collection/vector/payload identity against indexing. Known
metadata mismatches are excluded; absent metadata/context yields unknown
applicability. No model alias mapping or version-range compatibility is invented.
Recognizable email/phone-like query text is redacted before provider transfer,
without claiming comprehensive PII protection. Missing key/index returns `503`,
embedding-space mismatch `409`. Empty results explicitly report an evidence gap.
Injected fake embeddings/local Qdrant tests prove mechanics, not semantic accuracy.
See the [root guide](../../README.md) for request examples and manual checks.

## Support runs, decisions, and audit

All routes below use the same local operator bearer token. The synchronous run
request accepts only `{}`; use a stable idempotency key for retries:

```bash
curl --fail-with-body -i http://127.0.0.1:8000/api/incidents/INC-123/runs \
  -X POST \
  -H "Authorization: Bearer ${INGESTION_TOKEN}" \
  -H 'Idempotency-Key: run-INC-123-001' \
  -H 'Content-Type: application/json' \
  -d '{}'
```

- `201` means the run resource was stored, not that analysis succeeded; inspect
  its `status` and safe `error_code`. A same-key replay returns the stored run
  with `200` and does not repeat retrieval or generation.
- `GET /api/runs/{run_id}` reloads the persisted result; it does not call a provider.
- `GET /api/incidents/{ticket_id}/runs?limit=20&offset=0` returns a bounded,
  stable list and total count.
- `GET /api/runs/{run_id}/actions` shows immutable local simulation previews.
- `POST /api/actions/{action_id}/decision` accepts only
  `{"decision":"approve"}` or `{"decision":"reject"}`. Decisions are
  atomic and idempotent; approvals only record a local support-comment or L2
  handoff simulation. They do not change incident source fields, contact a
  person, call ITSM, or modify a device.
- `GET /api/incidents/{ticket_id}/audit?limit=50&offset=0` returns bounded,
  append-only events. The shared token is attributed honestly as
  `local_operator`, not as an individual identity.

```bash
curl --fail-with-body http://127.0.0.1:8000/api/actions/ACT-123/decision \
  -X POST \
  -H "Authorization: Bearer ${INGESTION_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"reject"}'
```

Fresh databases initialize schema version 2; version-1 databases are upgraded
transactionally without deleting existing rows. Startup marks orphaned `running`
records as failed/interrupted once; it never replays model work.

OpenAI generation was approved with the server-selected model identifier
`gpt-6-luna`; model availability and production-adapter structured output have
been live verified with invented synthetic text only. `GENERATION_MODEL` has no
default and must be configured server-side;
client requests cannot override it. The adapter uses a bounded timeout and zero
SDK retries. No production guidance sections are approved or loaded, so current
runs never invoke generation: they ask for missing facts or escalate instead of
inventing troubleshooting steps. Explicit policies can now be loaded from
`REVIEWED_POLICY_FILE` (the optional Rancher ConfigMap mount is described in the
root README). Files are bounded and require review provenance, expiry, exact
approved report wording and observation-only instructions; missing/contradictory
facts or unreviewed text fail closed. These are declared approvals, not verified
reviewer identities. No supplied client KB conflicts have been approved away.
For an applicable policy, provider input is limited to verified device metadata,
reviewed candidate instructions,
and exact cited source snippets; free-text descriptions, history, contacts, and
serial numbers are excluded. Intake, context, deterministic runs, reads,
decisions, and tests require no generation model or provider call. Similarity is
not confidence, and the local simulation is not a real handoff.

## Pilot evaluation status

A development census found 256 training tickets and 220 labels. The printer pilot
is the union of 11 `Not Detected` and four `Printer Offline` cases: 15 tickets and
13 labels, not a literal combined subcategory. These inspected labels are development
data, not a held-out set; the two unlabeled cases receive no invented outcome.

The evaluator now executes the actual workflow/persistence for 13 synthetic
scenarios (12 pilot-eligible; 11 with expected routing, two expected failures),
including contradictions, ambiguous reports, changed evidence and provider failures.
This is a contract/guardrail evaluation with deterministic provider doubles, not
a model-quality score. Separate opt-in live tests verify embeddings and structured
output using invented text. Isolated Rancher tests cover 30 concurrent reads,
persistence and the run/proposal/decision APIs. Real-dataset routing agreement,
semantic relevance, measured business savings and production readiness are not
established by these results.

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
- `ingestion/interfaces.py`: `IngestionStage.run()` abstract contract and shared typed report.
- `ingestion/schemas.py`: Pydantic source-data validation, not handwritten parsers.
- `ingestion/structured.py`: pandas input loading and concrete SQLite import logic.
- `ingestion/knowledge.py`: bounded Markdown chunks, OpenAI embeddings and Qdrant writes.
- `ingestion/pipeline.py`: stage selection and independent result aggregation.
- `tests/`: HTTP, source validation, database and offline provider regression tests.

Add additional layers only with concrete behavior. No plugin registry, abstract
factory hierarchy, background worker, or generic `core/utils` scaffolding is
needed. `services/` owns support workflows and approval-gated local simulations;
`repositories/` owns persistence, and `models/` owns typed contracts. Legacy v1
run/audit payloads remain readable as explicitly untyped historical representations,
without inventing modern outcomes or making old records approvable.

API references were fetched through Context Hub for pandas, Pydantic, FastAPI,
OpenAI and qdrant-client. Indexing/querying must use the same embedding model and
vector size. Changing them requires a new compatible collection/rebuild; never
mix vectors from different embedding spaces. Labels, evaluator notes and ticket
contact details stay out of the knowledge input.