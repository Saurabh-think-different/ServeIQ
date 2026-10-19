# ServeIQ operator workspace

Vue 3 + TypeScript + Vite. A small L1 support workspace using the real FastAPI
contracts from backend PR #18 (or its merged equivalent). No fake metrics,
provider results, device telemetry, agent progress, or approved actions are
included in the production application.

## Screens

- **Connect:** shared local operator bearer token, validated against the protected
  incident API. It is held only in memory and cleared on disconnect; refresh
  requires reconnecting. This is not individual login, SSO, RBAC or tenant isolation.
- **Incident queue:** exact category/status filters, pagination, refresh, meaningful
  empty/error/loading states, and fresh incident intake. Creating an incident does
  not automatically run agent analysis.
- **Incident workspace:** original report, reported vs. verified IDs, structured
  context, history, related references, SLA targets/fulfillment uncertainty, and
  manually requested cited knowledge passages. Context can fail independently
  without hiding an otherwise available incident.
- **Data & knowledge:** explicit server-configured imports, consent for KB text
  transfer to OpenAI, independent stage reports, and a manual retrieval playground.
  Import results belong to the current mounted view; there is no fictional durable
  last-sync inventory.
- **About:** the actual POC architecture and limitations, not a live monitoring page.

Analysis/run/action UI is intentionally deferred until those backend APIs exist.
A source search is not a diagnosis or permission to perform an action. Similarity
is not confidence, and target deadlines do not establish an SLA breach.

## Local development

Use Node.js 22.12+ or a supported newer LTS (the image uses Node 24). From this folder:

```bash
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. Vite proxies `/api` to
`http://127.0.0.1:8000`; run/forward the backend there. Do not enable broad Vite host
access or expose this local token-based setup on an untrusted network.

Set `INGESTION_TOKEN` **on the backend**, then enter that same token in the connect
screen. Never place operator or OpenAI keys in `VITE_*` variables, source, browser
storage, URLs, a public fixture, or a Docker build argument. There is no default
credential. Provider keys remain server-side.

The connection is checked using `GET /api/incidents?limit=1&offset=0` rather than an
unprotected health endpoint. A `401` ends the operator session. Disconnect aborts
outstanding browser requests but cannot undo a server operation already accepted.
Transport timeouts likewise have an unknown write outcome: refresh/check before
retrying. Failed/blocked/partial ingestion is shown from the response body even
when the HTTP status is 200.

Hash URLs retain the selected screen or incident across refresh, but **not access**:
you must reconnect. Tokens and submitted report text are never placed in the URL.
Do not rely on disconnect to revoke the shared server token; rotate it server-side
when necessary. Use HTTPS and proper identity/session design before deployment
beyond localhost.

## Tests and build

```bash
npm test
npm run build
```

Tests mock API/fetch boundaries and do not contact FastAPI, Qdrant or OpenAI.
TypeScript checking is part of the production build. The npm lockfile is authoritative.
The test setup uses isolated memory storage to avoid Node's experimental web-storage
implementation leaking into the jsdom browser environment.

No external fonts, icon CDN, UI framework, global store, or router dependency is
required. Content is rendered as text, never untrusted HTML. Client errors suppress
raw exception bodies and Pydantic input values that might contain sensitive data.

## Rancher Desktop / Skaffold — manual verification

The root Skaffold configuration builds this folder separately and forwards the UI
at <http://127.0.0.1:8080>. The two-stage Dockerfile builds locked assets with Node
and serves them using unprivileged Nginx on port 8080. Nginx proxies `/api/` to the
backend Kubernetes Service, preserving the path and Authorization header. Browser
code does not depend on Kubernetes DNS and never receives provider credentials.

The frontend `/healthz` checks the static server only; backend health is separate.
The image/cluster/proxy integration has not been run as part of this frontend batch;
these are the user's manual checks:

1. Start the existing root Skaffold flow; open localhost:8080.
2. Connect with the configured operator token. Verify wrong credentials fail.
3. Import bundled structured data explicitly and inspect stage reports.
4. Filter/page the queue; create a new report and inspect its returned workspace.
5. Check reported vs. verified facts and missing/SLA warnings.
6. With an approved provider key and indexed KB, request cited guidance. Without
   credentials/index, confirm actionable errors rather than fake results.
7. Confirm knowledge/all imports require explicit transfer confirmation.
8. Disconnect and refresh; confirm access and report details do not remain visible.
9. Check narrow/mobile layout and keyboard navigation.

Model transfers happen only after explicit user requests to the corresponding
backend APIs; the UI includes disclosures, not a substitute for a data policy.
