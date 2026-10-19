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

Incident workflows, a retrieval/query API, fresh incident HTTP intake, and Vue
are not implemented yet. Knowledge ingestion is tested with mocked embeddings;
a running Qdrant service alone is not evidence of semantic-search quality.

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
are reconciled transactionally. This is not a merge policy for future human-edited
operational tickets or an authorization mechanism for support actions.

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
