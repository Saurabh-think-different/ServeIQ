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

The API and data CLI share `src/phdata/config.py`:

| Variable | Checkout default | Container default |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | `0.0.0.0` |
| `PORT` | `8000` | `8000` |
| `DATA_DIR` | Repository `client_data/` | `/opt/dataset` |
| `DATABASE_PATH` | `microservices/backend/.local/servewell.db` | `/var/lib/servewell/servewell.db` |
| `QDRANT_URL` | `http://127.0.0.1:6333` | `http://qdrant:6333` |

The explicit container paths avoid relying on a source checkout being present
in site-packages. Outside a checkout, CLI defaults are relative to the working
directory; set `DATA_DIR` and `DATABASE_PATH` explicitly for installed deployments.
Qdrant configuration is reserved for upcoming retrieval; no collection is
created or queried by the current API.

## Load Data

Import the checked-in dataset into the local SQLite database:

```bash
uv run --project microservices/backend servewell-data ingest
```

The command uses `client_data/` and writes to
`microservices/backend/.local/servewell.db` by default. Set `DATA_DIR` and
`DATABASE_PATH` to override these defaults; CLI flags `--data-dir` and
`--database-path` take precedence over environment settings.

The checked-in manifest names ticket files under `tickets/raw/`, while this
checkout stores them under `tickets/train/`. The importer applies the explicit
`raw` to `train` path mapping and reports how many files used it. Any other
missing path fails with its manifest row and ticket ID. Imported ticket history
and relations are source-owned snapshots: entries removed from a re-imported
source ticket are removed transactionally from those tables and reported as
`deleted`. This does not delete agent runs or audit events, and it is not a merge
policy for future human-edited operational history.

Import a fresh ticket JSON after loading the reference data:

```bash
uv run --project microservices/backend servewell-data ticket /path/to/INC-NEW.json
```

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
│       │   └── health.py
│       ├── db/
│       │   ├── __init__.py
│       │   ├── database.py
│       │   ├── schema.py
│       │   └── schema.sql
│       └── ingestion/
│           ├── __init__.py
│           ├── cli.py
│           └── importer.py
└── tests/
```

`src/` is the Python source root, not another application package. `phdata/`
contains the single backend package; there are no sibling application layers
under `src/`. Retaining this package name preserves the existing CLI entrypoints
and imports.

- `main.py`: FastAPI app, route registration, and server entrypoint.
- `config.py`: shared environment settings and development defaults.
- `routes/`: HTTP routers; liveness `/api/health` and readiness `/api/health/ready`.
- `db/`: SQLite connection and schema initialization.
- `ingestion/`: reusable CSV/JSON importer and its CLI adapter.
- `tests/`: HTTP API, database, and importer regression tests.

Add models, repositories, and services when their first concrete feature needs
them. Keep runtime settings in `config.py`; do not restore empty `core/`,
`utils/`, or parallel `src/api/` scaffolding. HTTP ingestion and support workflows are planned, not implemented
by this layout cleanup.

## Planned knowledge embeddings

Use OpenAI embeddings for KB indexing and retrieval, with
`text-embedding-3-small` as the proposed initial model. The model and vector
size must match between indexing, querying, and the Qdrant collection. Keep
`OPENAI_API_KEY` server-side and exclude labels, evaluator notes, and contact
details from embedding inputs.

The OpenAI embeddings API documentation was checked through Context Hub
(`chub get openai/chat --lang py`). No embedding requests or Qdrant indexing
are performed by this change; the implementation belongs to issue
[#5](https://github.com/Saurabh-think-different/ServeIQ/issues/5).