# ServeIQ

Local proof-of-concept foundation for ServeWell IT support: one FastAPI backend,
SQLite for structured data, and Qdrant for the future knowledge index.

## Current scope

- HTTP liveness and database readiness endpoints.
- Operator-protected HTTP ingestion: pandas/Pydantic-validated CSV/ticket JSON
  into SQLite, and Markdown knowledge through OpenAI embeddings into Qdrant.
- Independent ingestion stages sharing a small interface and typed reports.
- A frozen uv workspace, a non-root backend image, and persistent local Kubernetes
  storage for SQLite and Qdrant.

Fresh incident create/list/detail, exact structured context, and cited KB search
are available through operator-protected HTTP routes. Incident workflows, action
approvals and Vue are not implemented yet. Embeddings/search are tested offline
with fake vectors and local Qdrant; real provider behavior and semantic relevance
still require manual validation.

## Rancher Desktop + Skaffold

Prerequisites: Rancher Desktop with Kubernetes and the **Moby/dockerd** container
engine, plus Docker CLI, kubectl, and Skaffold. The local Kubernetes context must
be `rancher-desktop`.

Check the contexts and node before deploying:

```bash
docker context show
kubectl --context rancher-desktop get nodes
skaffold version
```

The Docker context should also be `rancher-desktop`. If necessary, select it in
your terminal before starting Skaffold:

```bash
export DOCKER_CONTEXT=rancher-desktop
skaffold dev --kube-context rancher-desktop --port-forward --cleanup=false
```

Skaffold builds locally without pushing an image registry, rewrites the backend
image reference, applies the manifests, and forwards the HTTP services:

- Backend: <http://127.0.0.1:8000/docs>
- Liveness: <http://127.0.0.1:8000/api/health>
- SQLite readiness: <http://127.0.0.1:8000/api/health/ready>

Qdrant is internal by default. To also forward its HTTP endpoint to localhost,
start development with the optional profile:

```bash
skaffold dev --kube-context rancher-desktop --port-forward --cleanup=false -p qdrant-port-forward
```

Qdrant readiness is then available at <http://127.0.0.1:6333/readyz>.

No API keys are needed for health checks or structured ingestion. No calls to
OpenAI occur at startup. Knowledge ingestion requires `OPENAI_API_KEY` and sends
KB passage text to OpenAI; do not enable it for content that is not approved for
that provider. The backend readiness check initializes/opens SQLite; it
deliberately does not depend on Qdrant or a model provider.

To build/deploy once without leaving a watcher running:

```bash
skaffold run --kube-context rancher-desktop
```

That command does not keep port forwards running. Add a temporary forward in
another terminal when needed:

```bash
kubectl --context rancher-desktop port-forward --address 127.0.0.1 service/servewell-backend 8000:8000
```

## Ingest through HTTP

The image contains curated inputs at `/opt/dataset`, excluding evaluation labels.
No macOS `hostPath`, CLI wrapper, ingestion sidecar, or arbitrary-path request is
needed. `POST /api/ingestions` takes `{"target":"structured"}`, `"knowledge"`, or
`"all"` and runs synchronously against server-configured inputs.

For host development, set a local operator token before starting the API:

```bash
export INGESTION_TOKEN="$(openssl rand -hex 32)"
uv run --project microservices/backend servewell-api
```

For Kubernetes, create the optional secret **before deploying**, using a local
untracked environment file outside the build context:

```bash
kubectl --context rancher-desktop create secret generic servewell-ingestion \
  --from-env-file=/absolute/path/to/local-ingestion.env --dry-run=client -o yaml \
  | kubectl --context rancher-desktop apply -f -
```

The file contains `INGESTION_TOKEN=<random-local-token>` and, only for approved
knowledge ingestion, `OPENAI_API_KEY=<provider-key>`. Never commit it. After a
secret change, restart the backend deployment to refresh its environment.
Keep the same operator token in the terminal issuing requests:

```bash
curl --fail-with-body http://127.0.0.1:8000/api/ingestions \
  -H "Authorization: Bearer $INGESTION_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"target":"structured"}'
```

Use `knowledge` to index KB Markdown with OpenAI `text-embedding-3-small`, or
`all` to run both stages. Structured data/tickets and labels are not embedded.
Missing OpenAI credentials block knowledge ingestion without affecting an
explicitly selected structured import. A missing operator token disables the
route (`503`), wrong credentials return `401`, invalid requests `422`, and an
already-running ingestion `409`.

A completed pipeline returns HTTP `200` with `status` (`succeeded`, `partial`,
or `failed`) and independent stage reports. **Check the body, not just the HTTP
status.** Structured data commits atomically in SQLite. Qdrant writes are not
part of that transaction; partial vector writes can be reconciled by rerunning.
No cleanup of stale chunks occurs after a failed replacement. Unchanged chunks
skip embedding calls; changing the embedding identity requires a fresh compatible
collection, not mixing embedding spaces.

Repeat a structured request to verify unchanged rows are skipped. Reports include
counts, warnings, and the explicit `tickets/raw` → `tickets/train` mapping.
Ingestion is never an API restart side effect. The in-process ingestion lock is
for the documented **one process/one replica** setup, not distributed execution.

Imports own the source incident fields/history/relations; removed source children
are reconciled transactionally. Seed imports refuse IDs owned by API-created
incidents (`source_path IS NULL`) rather than overwriting runtime intake. Other
seed updates remain source snapshots, not a merge policy for human-edited tickets.

## Fresh incidents and context

All routes below require the same local operator bearer token as ingestion.

```bash
curl --fail-with-body http://127.0.0.1:8000/api/incidents \
  -H "Authorization: Bearer $INGESTION_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"description":"Receipt printer is offline","category":"Printers","priority":"P2"}'
```

- `POST /api/incidents`: description required; optional reported store/asset IDs,
  subject, category, priority, version and offset-aware history. Generates an ID
  unless supplied; duplicates return `409`. New incidents start `Open`.
- `GET /api/incidents?limit=25&offset=0`: bounded queue with total count; optional
  exact `status` and `category` filters.
- `GET /api/incidents/{ticket_id}`: persisted incident/history and warnings, or `404`.
- `GET /api/incident-context/{ticket_id}`: exact store/asset/SLA/history/related
  lookups, missing conditions and offset-aware clock targets. No model calls,
  guessed IDs, contact columns or resolution-answer fields. Free-text reports
  may still contain personal data; context is not comprehensive PII redaction.

Missing IDs are accepted and preserved as missing; incompatible store/asset
relationships remain unresolved. Context reads can resolve exact reported IDs
against reference data that arrived later; persisted intake detail still reports
its original resolution state. SLA target deadlines do not prove a response or
resolution breach: fulfillment and actual escalation require additional evidence.

## Cited knowledge search

```bash
curl --fail-with-body http://127.0.0.1:8000/api/knowledge/search \
  -H "Authorization: Bearer $INGESTION_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"receipt printer offline","category":"Printers","limit":5}'
```

Search uses the configured ingestion model/dimensions and rejects incompatible
embedding spaces. Missing provider key/index returns an actionable `503`; a
space mismatch returns `409`. Responses include bounded passages, source paths,
sections, raw similarity scores and applicability notes. Scores are **not confidence**.
Explicitly conflicting model/version/category metadata is filtered; generic or
incompletely described guidance is marked applicability unknown. No aliases or
version ranges are inferred. Empty results are an evidence gap, not generated
instructions. Search does not resolve conflicting policy or authorize actions.

Recognizable email/phone-like query fragments are redacted before embedding;
this is not comprehensive PII protection. Provider-backed queries and KB uploads
require approved content and a configured key. This batch was tested **offline
only**; Rancher Desktop and live retrieval checks are intentionally left for
manual review. No new deployment or OpenAI request was performed for this batch.

## State and lifecycle

- SQLite: 1 GiB PVC, whole directory `/var/lib/servewell` (including WAL files).
- Qdrant: existing 2 GiB `qdrant-pvc` at `/qdrant/storage`.
- One replica and `Recreate` strategy per service avoid overlapping writers.
- Inputs are read-only by convention in the image; edit local source inputs and
  rebuild to change them. They are separate from persistent operational state.

Stopping `skaffold dev` with `--cleanup=false` leaves deployments and PVCs in place.
Pod replacement preserves PVC-backed data. **Deleting PVCs, using destructive
Skaffold cleanup, or resetting Rancher Desktop can erase local state.** Do not run
`skaffold delete` as a routine restart command. To pause the application without
removing its state:

```bash
kubectl --context rancher-desktop scale deployment/servewell-backend deployment/qdrant --replicas=0
```

Run Skaffold again to resume. This setup is local-only, not a production identity,
network isolation, backup, or high-availability design.

## Troubleshooting

```bash
kubectl --context rancher-desktop get deployments,pods,pvc
kubectl --context rancher-desktop logs deployment/servewell-backend
kubectl --context rancher-desktop describe deployment servewell-backend
```

- **ImagePullBackOff:** confirm the Docker context, then build through Skaffold;
  do not apply an unbuilt `servewell-backend:latest` deployment and expect a
  registry image to exist.
- **Readiness 503:** inspect backend logs, SQLite path and volume permissions.
  `/api/health` remains independent of database readiness.
- **Pending PVC:** verify Rancher Desktop's default local storage provisioner.
- **Port already occupied:** stop the specific conflicting local forward or use
  a different local port; do not kill processes by name.
- **Qdrant has no collections:** run an authorized knowledge ingestion with an
  approved OpenAI key. Inspect stage errors/blocked status; health readiness does
  not check index contents. Local mocked tests do not require provider keys.

## Python development and tests

```bash
uv sync
uv run --project microservices/backend servewell-api
uv run --project microservices/backend pytest microservices/backend/tests -q
```

See the [backend README](microservices/backend/README.md) for the package layout,
configuration, and HTTP ingestion examples. Keep secrets in local environment/secret stores,
not in source, build contexts, or browser code.
