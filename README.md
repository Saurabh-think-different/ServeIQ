# ServeIQ

Local proof-of-concept foundation for ServeWell IT support: one FastAPI backend,
SQLite for structured data, and Qdrant for the future knowledge index.

## Current scope

- HTTP liveness and database readiness endpoints.
- Repeatable CSV/JSON ingestion through the data CLI, including fresh ticket JSON.
- A frozen uv workspace, a non-root backend image, and persistent local Kubernetes
  storage for SQLite and Qdrant.

HTTP ingestion, incident workflows, Vue, and OpenAI-backed KB indexing are not
implemented yet. A running Qdrant service is not evidence of working vector search.

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

No API keys are needed for this foundation. No calls to OpenAI occur at startup
or during structured ingestion. The backend readiness check initializes/opens
SQLite; it deliberately does not depend on Qdrant or a model provider.

To build/deploy once without leaving a watcher running:

```bash
skaffold run --kube-context rancher-desktop
```

That command does not keep port forwards running. Add a temporary forward in
another terminal when needed:

```bash
kubectl --context rancher-desktop port-forward --address 127.0.0.1 service/servewell-backend 8000:8000
```

## Load the bundled structured dataset

The image contains curated inputs at `/opt/dataset`, excluding evaluation labels.
No macOS `hostPath`, manual file copying, or ingestion sidecar is needed.

```bash
kubectl --context rancher-desktop exec deployment/servewell-backend -- servewell-data ingest
```

Run the same command again to verify unchanged rows are skipped. Import reports
include inserted/updated/skipped/deleted counts, warnings, and the explicit manifest
`tickets/raw` → `tickets/train` mapping. Loading data is an explicit command, not
an API restart side effect.

The importer can update source-owned incident fields; this is a seed/import
foundation, not yet a safe merge API for future human-edited operational tickets.
Do not assume approved workflow edits can be overwritten by re-importing seeds.

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
- **Qdrant has no collections:** expected until KB ingestion is implemented.

## Python development and tests

```bash
uv sync
uv run --project microservices/backend servewell-api
uv run --project microservices/backend pytest microservices/backend/tests -q
```

See the [backend README](microservices/backend/README.md) for the package layout,
configuration, and CLI examples. Keep secrets in local environment/secret stores,
not in source, build contexts, or browser code.
