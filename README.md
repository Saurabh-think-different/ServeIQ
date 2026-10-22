# ServeIQ

Local proof of concept for ServeWell IT support: one FastAPI backend,
SQLite for structured data and audit history, Qdrant for cited knowledge retrieval,
and a Vue operator workspace.

## Current scope

- HTTP liveness and database readiness endpoints.
- Operator-protected HTTP ingestion: pandas/Pydantic-validated CSV/ticket JSON
  into SQLite, and Markdown knowledge through OpenAI embeddings into Qdrant.
- Independent ingestion stages sharing a small interface and typed reports.
- A frozen uv workspace, a non-root backend image, and persistent local Kubernetes
  storage for SQLite and Qdrant.

Operator-protected routes and the Vue workspace provide intake/queue,
reported versus verified context, explicit imports, manual cited search,
persisted support runs and summaries, proposal previews, approval/rejection of
local simulations, and audit history. Timed-out writes retain recovery metadata;
the UI never automatically retries them. Credentials remain memory-only.

Guidance defaults to human escalation until an explicitly reviewed, narrowly
scoped policy is configured. Client KB conflicts have not been approved away.
Historical v1 runs/audits are readable as explicitly untyped legacy records,
not reinterpreted as modern guidance or approvable actions.

Unit tests use deterministic boundaries. Separate live synthetic checks have
verified OpenAI embeddings and the production structured-output adapter with
`gpt-6-luna`; this does not establish client-corpus retrieval relevance, model
accuracy on real incidents, measured business savings, or production readiness.

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

- Frontend workspace: <http://127.0.0.1:8080>
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

## Configure OpenAI without sharing credentials

Create a dedicated OpenAI project API key with API billing enabled. Do not paste
it into chat, commit it, place it in frontend configuration, or print Kubernetes
Secret contents. A ChatGPT subscription does not itself provide API credits.

Keep local configuration **outside the checkout**:

```bash
install -d -m 700 "$HOME/.config/serveiq"
(umask 077; touch "$HOME/.config/serveiq/openai.env")
chmod 600 "$HOME/.config/serveiq/openai.env"
code "$HOME/.config/serveiq/openai.env"
```

In that file, enter these values without shell quotes or `export` prefixes:

```dotenv
OPENAI_API_KEY=YOUR_PROJECT_API_KEY
INGESTION_TOKEN=YOUR_EXISTING_LOCAL_OPERATOR_TOKEN
GENERATION_MODEL=gpt-6-luna
```

Keep the existing operator token when updating an existing deployment; changing
it requires reconnecting browser/API clients. For a new deployment, generate a
random token locally with `openssl rand -hex 32`. `gpt-6-luna` is the requested
model identifier. Its availability and structured-output generation were verified
with the configured project during local validation; access may differ for other
projects. Check availability before generation and never silently substitute a
different model. Embeddings use the separately configured
`text-embedding-3-small` model.

Apply the local configuration without printing secret values:

```bash
kubectl --context rancher-desktop create secret generic servewell-ingestion \
  --from-env-file="$HOME/.config/serveiq/openai.env" --dry-run=client -o yaml \
  | kubectl --context rancher-desktop apply -f -
kubectl --context rancher-desktop rollout restart deployment/servewell-backend
kubectl --context rancher-desktop rollout status deployment/servewell-backend
```

The Secret and deployment must use the same namespace. The current backend
manifest reads the optional generation model from this Secret; rebuild/deploy
manifest changes with Skaffold before relying on a new environment entry. Do not
use `kubectl get secret -o yaml` to verify credentials: check only whether the
required keys are present. Kubernetes Secrets are base64 encoded, not inherently
encrypted at rest; Rancher Desktop remains a local POC environment.

A key exported in a VS Code terminal does not automatically propagate to an
already-running backend or a separate assistant tool process. For host runs,
load this **trusted, locally edited** file in the same terminal before launch:

```bash
set -a
. "$HOME/.config/serveiq/openai.env"
set +a
uv run --project microservices/backend servewell-api
```

Unit tests stay deterministic and credential-free. Live provider smoke tests are
a separate, explicit integration check, not a replacement for unit tests. The
approved smoke scope is at most **10 small synthetic API requests**, with no bulk
indexing and no client tickets, client KB passages, labels, or other sensitive
content sent to OpenAI. This request limit is not a hard dollar spending cap;
configure provider-side budget controls as well. Real client-data processing
requires a separate approved data-handling process.

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
require approved content and a configured key. Rancher validation covers the
proxy, persistence and concurrent API requests. Live provider checks use invented
synthetic text only; real-corpus retrieval relevance remains unmeasured.

## Support runs and reviewed policy configuration

Start a run explicitly with `POST /api/incidents/{ticket_id}/runs`, an empty JSON
body and an `Idempotency-Key` header. Reuse the same key after an uncertain
response. Read persisted results at `GET /api/runs/{run_id}` and list incident
runs at `GET /api/incidents/{ticket_id}/runs`. Proposals are read from
`GET /api/runs/{run_id}/actions`; only an explicit decision at
`POST /api/actions/{action_id}/decision` with `{"decision":"approve"}` or
`{"decision":"reject"}` can resolve one. Audit history is separate from ticket
source history: `GET /api/incidents/{ticket_id}/audit`.

All require the operator token. Actions are SQLite-only simulations, never live
ITSM writes, device operations or external contacts. The browser shows immutable
previews and a second confirmation before submitting a decision.

For host runs, `REVIEWED_POLICY_FILE` can point to a local JSON document containing
`{"schema_version":1,"policies":[...]}`. Each policy must have reviewer/reference,
validity dates, conflict disposition, exact reviewed report subject/description
pairs, and exact source hashes/model/version applicability. The current pilot
allows only observation-only instructions; privileged changes, unseen wording,
contradictory context, or expired/missing review fail closed. These declarations
are operator attestations, not cryptographic proof of independent review. Do not
label the synthetic fixtures as client approvals.

Once an actual policy has been reviewed, mount it in Rancher Desktop:

```bash
kubectl --context rancher-desktop create configmap servewell-reviewed-policy \
  --from-file=reviewed-policy.json=/absolute/path/to/reviewed-policy.json \
  --from-literal=REVIEWED_POLICY_FILE=/etc/servewell/policies/reviewed-policy.json \
  --dry-run=client -o yaml | kubectl --context rancher-desktop apply -f -
kubectl --context rancher-desktop rollout restart deployment/servewell-backend
```

Deploy the updated manifest first. Without this optional ConfigMap, the backend
remains usable but does not offer unreviewed guidance. Knowledge indexing remains
separate and must not send client content without an approved data-handling process.

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

## Vue development and tests

The frontend depends on backend PR #18 or its merged equivalent. It uses the
existing shared operator token, never a bundled default credential or browser
storage. Refresh requires reconnecting. Run the backend at localhost:8000 (or
forward its Service), then:

```bash
cd microservices/frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. Vite proxies `/api` locally; the containerized UI at
localhost:8080 uses Nginx to proxy the same paths to FastAPI. Knowledge imports
require explicit transfer confirmation; provider keys remain server-side.

```bash
npm test
npm run build
```

See the [frontend guide](microservices/frontend/README.md) for screen scope,
auth limitations and manual Rancher checks. Frontend tests use mocked boundaries;
no live-provider or cluster verification is implied.

## Python development and tests

```bash
uv sync
uv run --project microservices/backend servewell-api
uv run --project microservices/backend pytest microservices/backend/tests -q
```

Live tests are disabled by default. To explicitly authorize **two paid synthetic
requests** (one embedding and one structured recommendation) using the trusted
local configuration file:

```bash
set -a
. "$HOME/.config/serveiq/openai.env"
set +a
SERVEIQ_LIVE_OPENAI=1 uv run --project microservices/backend pytest \
  microservices/backend/tests/test_live_openai.py -q
```

The live tests never open the client dataset. They do not retry provider calls.
The normal backend suite additionally executes 13 synthetic cases through the
actual workflow/persistence with deterministic provider doubles, including
contradictions, source changes and failures; these are not real-ticket metrics.

[CI](.github/workflows/validate.yml) validates backend tests/wheel, frontend
tests/build and both container builds without provider credentials on PRs and
main pushes. It has been validated locally; no remote run or required-check rule
exists until the workflow is published and repository settings are configured.

See the [backend README](microservices/backend/README.md) for the package layout,
configuration, and HTTP ingestion examples. Keep secrets in local environment/secret stores,
not in source, build contexts, or browser code.
