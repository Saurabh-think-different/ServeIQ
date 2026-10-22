<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import type { ActionDecision, ActionProposal, ApiClient, AuditPage, RunPage, RunReadResult } from '../api/types'
import { ambiguousWrite, operationError } from '../api/errors'

const props = defineProps<{ api: ApiClient; ticketId: string }>()
const emit = defineEmits<{ changed: [] }>()
type Journal = { startKey: string | null; selectedRun: string | null; decisions: Record<string, ActionDecision> }
const journal = ref<Journal>({ startKey: null, selectedRun: null, decisions: {} })
const storageError = ref('')
const runs = ref<RunPage | null>(null)
const runOffset = ref(0)
const runLoading = ref(false)
const runError = ref('')
const selected = ref<RunReadResult | null>(null)
const selectedId = ref('')
const resultLoading = ref(false)
const resultError = ref('')
const actions = ref<ActionProposal[]>([])
const actionsLoading = ref(false)
const actionsError = ref('')
const audit = ref<AuditPage | null>(null)
const auditOffset = ref(0)
const auditLoading = ref(false)
const auditError = ref('')
const consent = ref(false)
const starting = ref(false)
const startError = ref('')
const startRead = ref(false)
const confirming = ref<{ action: ActionProposal; decision: ActionDecision } | null>(null)
const deciding = ref(false)
const decisionError = ref('')
const actionsRead = ref(false)
let alive = true
let epoch = 0
let listRequest = 0
let resultRequest = 0
let proposalRequest = 0
let auditRequest = 0
const valid = (generation: number) => alive && generation === epoch
const storageKey = () => `serveiq:pending:v1:${encodeURIComponent(props.ticketId)}`

function restoreJournal() {
  storageError.value = ''
  try {
    const saved = sessionStorage.getItem(storageKey())
    const parsed = saved ? JSON.parse(saved) : {}
    if (!parsed || typeof parsed !== 'object' || (parsed.startKey != null && (typeof parsed.startKey !== 'string' || !/^[\w-]{1,128}$/.test(parsed.startKey))) || (parsed.selectedRun != null && typeof parsed.selectedRun !== 'string')) throw new Error('Invalid recovery metadata')
    const decisions: Record<string, ActionDecision> = {}
    if (parsed.decisions && typeof parsed.decisions === 'object') {
      for (const [id, value] of Object.entries(parsed.decisions)) {
        if (value === 'approve' || value === 'reject') decisions[id] = value
      }
    }
    journal.value = {
      startKey: typeof parsed.startKey === 'string' && /^[\w-]{1,128}$/.test(parsed.startKey) ? parsed.startKey : null,
      selectedRun: typeof parsed.selectedRun === 'string' ? parsed.selectedRun : null,
      decisions,
    }
  } catch {
    storageError.value = 'Browser session storage is unavailable or invalid. Writes are disabled: recovery keys must survive refresh. Enable session storage and reopen this incident.'
  }
}
function saveJournal(next: Journal): boolean {
  try {
    sessionStorage.setItem(storageKey(), JSON.stringify(next))
    journal.value = next
    return true
  } catch {
    storageError.value = 'Recovery metadata could not be saved. Writes are disabled. Keep this page open and restore browser session storage before continuing.'
    return false
  }
}

async function loadRuns() {
  const generation = epoch, request = ++listRequest
  runLoading.value = true
  runError.value = ''
  startRead.value = false
  try {
    const page = await props.api.listRuns(props.ticketId, { limit: 10, offset: runOffset.value })
    if (!valid(generation) || request !== listRequest) return
    runs.value = page
    startRead.value = true
    if (!selectedId.value) {
      const id = journal.value.selectedRun || page.runs[0]?.run_id
      if (id) void selectRun(id)
    }
  } catch (error) {
    if (valid(generation) && request === listRequest) runError.value = operationError(error, 'Run history')
  } finally {
    if (valid(generation) && request === listRequest) runLoading.value = false
  }
}
async function loadActions(id = selectedId.value) {
  if (!id) return
  const generation = epoch, request = ++proposalRequest
  actionsLoading.value = true
  actionsRead.value = false
  actionsError.value = ''
  confirming.value = null
  try {
    const result = await props.api.listActions(id)
    if (!valid(generation) || request !== proposalRequest || id !== selectedId.value) return
    actions.value = result.actions
    actionsRead.value = true
    const decisions = { ...journal.value.decisions }
    for (const action of result.actions) if (action.status !== 'pending') delete decisions[action.action_id]
    saveJournal({ ...journal.value, decisions })
  } catch (error) {
    if (valid(generation) && request === proposalRequest) actionsError.value = operationError(error, 'Proposals')
  } finally {
    if (valid(generation) && request === proposalRequest) actionsLoading.value = false
  }
}
async function selectRun(id: string) {
  const generation = epoch, request = ++resultRequest
  selectedId.value = id
  selected.value = null
  resultError.value = ''
  actions.value = []
  confirming.value = null
  decisionError.value = ''
  resultLoading.value = true
  saveJournal({ ...journal.value, selectedRun: id })
  proposalRequest++
  actionsRead.value = false
  actionsError.value = ''
  actionsLoading.value = false
  try {
    const result = await props.api.getRun(id)
    if (!valid(generation) || request !== resultRequest) return
    if (result.ticket_id !== props.ticketId) throw new Error('Run belongs to a different ticket')
    selected.value = result
    if (result.representation !== 'legacy_v1') void loadActions(id)
  } catch (error) {
    if (valid(generation) && request === resultRequest) resultError.value = operationError(error, 'Run result')
  } finally {
    if (valid(generation) && request === resultRequest) resultLoading.value = false
  }
}
async function loadAudit() {
  const generation = epoch, request = ++auditRequest
  auditLoading.value = true
  auditError.value = ''
  try {
    const result = await props.api.listAudit(props.ticketId, { limit: 10, offset: auditOffset.value })
    if (valid(generation) && request === auditRequest) audit.value = result
  } catch (error) {
    if (valid(generation) && request === auditRequest) auditError.value = operationError(error, 'Audit history')
  } finally {
    if (valid(generation) && request === auditRequest) auditLoading.value = false
  }
}
function refresh() {
  void loadRuns()
  void loadAudit()
  if (selectedId.value) void selectRun(selectedId.value)
}
function pageRuns(delta: number) { runOffset.value = (runs.value?.offset ?? 0) + delta; void loadRuns() }
function pageAudit(delta: number) { auditOffset.value = (audit.value?.offset ?? 0) + delta; void loadAudit() }

async function startRun() {
  if (starting.value || deciding.value || storageError.value || !consent.value || !startRead.value || (!journal.value.startKey && runs.value?.runs.some(run => run.status === 'running'))) return
  const generation = epoch
  let key = journal.value.startKey
  if (!key) {
    try { key = crypto.randomUUID() } catch { startError.value = 'Secure recovery-key generation is unavailable. Use localhost or HTTPS.'; return }
    if (!saveJournal({ ...journal.value, startKey: key })) return
  }
  consent.value = false
  starting.value = true
  startError.value = ''
  startRead.value = false
  try {
    const result = await props.api.startRun(props.ticketId, key)
    if (!valid(generation)) return
    if (result.ticket_id !== props.ticketId || !result.run_id) throw new Error('Invalid run response')
    saveJournal({ ...journal.value, startKey: null, selectedRun: result.run_id })
    runOffset.value = 0
    void loadRuns()
    void loadAudit()
    await selectRun(result.run_id)
  } catch (error) {
    if (!valid(generation)) return
    if (!ambiguousWrite(error)) saveJournal({ ...journal.value, startKey: null })
    startError.value = `${operationError(error, 'Run start')} ${ambiguousWrite(error) ? 'Outcome unknown. The same recovery key is retained. Read the history before explicitly replaying this start; do not create a new key.' : 'No automatic retry was made.'}`
  } finally {
    if (valid(generation)) starting.value = false
  }
}
function choose(action: ActionProposal, decision: ActionDecision) {
  if (deciding.value || !actionsRead.value || starting.value || storageError.value) return
  const pending = journal.value.decisions[action.action_id]
  if (pending && pending !== decision) return
  confirming.value = { action, decision }
}
async function decide() {
  const choice = confirming.value
  if (!choice || deciding.value || !actionsRead.value || storageError.value || starting.value) return
  const generation = epoch
  const { action, decision } = choice
  if (!saveJournal({ ...journal.value, decisions: { ...journal.value.decisions, [action.action_id]: decision } })) return
  confirming.value = null
  deciding.value = true
  actionsRead.value = false
  decisionError.value = ''
  try {
    const result = await props.api.decideAction(action.action_id, decision)
    if (!valid(generation)) return
    const decisions = { ...journal.value.decisions }
    delete decisions[action.action_id]
    saveJournal({ ...journal.value, decisions })
    actions.value = actions.value.map(item => item.action_id === result.action_id ? { ...item, ...result } : item)
    emit('changed')
    auditOffset.value = 0
    void loadAudit()
    await loadActions()
  } catch (error) {
    if (valid(generation)) decisionError.value = `${operationError(error, 'Decision')} Outcome not reconciled. Refresh proposals before another explicit decision; only the original choice can be retried while its outcome is unknown.`
  } finally {
    if (valid(generation)) deciding.value = false
  }
}
watch(() => [props.api, props.ticketId] as const, () => {
  epoch++
  journal.value = { startKey: null, selectedRun: null, decisions: {} }
  runs.value = null; selected.value = null; selectedId.value = ''; actions.value = []; audit.value = null
  runOffset.value = 0; auditOffset.value = 0
  runError.value = ''; resultError.value = ''; actionsError.value = ''; auditError.value = ''; startError.value = ''; decisionError.value = ''
  starting.value = false; deciding.value = false; consent.value = false; confirming.value = null; startRead.value = false; actionsRead.value = false
  restoreJournal()
  void loadRuns(); void loadAudit()
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { alive = false; epoch++ })
const routing: Record<string, string> = { l1_self_service: 'L1 self-service', l1_guided: 'L1 guided support', l2_escalation: 'L2 escalation recommended', non_it: 'Non-IT request', needs_clarification: 'Needs clarification' }
</script>

<template>
  <section id="incident-runs" class="card stack" aria-labelledby="runs-heading">
    <header class="card-header"><h2 id="runs-heading">Support runs &amp; reviewed actions</h2><span class="badge">Persisted results</span></header>
    <p class="muted">Runs do not start automatically. Routing is a recommendation, not a repair. Questions are persisted prompts for the operator; answering them is not yet supported here.</p>
    <div class="alert warning">
      <p>A run may send a policy-scoped query to OpenAI for embedding and, if generation is configured, limited context, candidate steps and KB evidence for model selection. Redaction is limited. Authorize this transfer before starting or replaying a run.</p>
      <label class="inline"><input v-model="consent" data-testid="run-consent" type="checkbox" :disabled="starting" /> I authorize the provider transfer for this request.</label>
    </div>
    <p v-if="storageError" role="alert" class="alert error">{{ storageError }}</p>
    <div v-if="journal.startKey" class="alert warning" data-testid="pending-start">
      <p>Unconfirmed run start. Its recovery key is retained across refresh in this browser tab. Read history first, then replay only with the same key. Replay returns an accepted run or starts it if the server never accepted the request.</p>
      <p v-if="!startRead">Refresh runs before replaying.</p>
    </div>
    <div class="inline">
      <button class="button primary" data-testid="start-run" :disabled="!consent || starting || deciding || !!storageError || !startRead || (!journal.startKey && !!runs?.runs.some(run => run.status === 'running'))" @click="startRun">{{ starting ? 'Starting…' : journal.startKey ? 'Replay start with same key' : 'Start support run' }}</button>
      <button class="button secondary" data-testid="refresh-workflow" :disabled="starting || deciding" @click="refresh">Refresh runs, proposals &amp; audit</button>
    </div>
    <p v-if="startError" role="alert" class="alert error">{{ startError }}</p>
    <p v-if="runLoading" role="status">Loading run history…</p>
    <p v-if="runError" role="alert" class="alert error">{{ runError }} Previously received history may be stale.</p>
    <template v-if="runs">
      <p v-if="!runs.runs.length" class="muted">No runs on this page. Start one explicitly when authorized.</p>
      <ul v-else class="run-list" aria-label="Run history"><li v-for="run in runs.runs" :key="run.run_id"><button class="button secondary small" :aria-pressed="selectedId === run.run_id" :disabled="starting || deciding" @click="selectRun(run.run_id)">{{ run.started_at }} · {{ run.status }} · {{ run.run_id }}</button></li></ul>
      <nav class="inline" aria-label="Run pagination"><button class="button secondary small" :disabled="runLoading || starting || deciding || runs.offset === 0" @click="pageRuns(-10)">Previous runs</button><span>{{ runs.total }} runs · offset {{ runs.offset }}</span><button class="button secondary small" :disabled="runLoading || starting || deciding || runs.offset + runs.limit >= runs.total || runs.offset + runs.limit > 100000" @click="pageRuns(10)">Next runs</button></nav>
    </template>
    <p v-if="resultLoading" role="status">Loading persisted run…</p>
    <div v-if="resultError" role="alert" class="alert error"><p>{{ resultError }}</p><button class="button secondary small" :disabled="resultLoading" @click="selectRun(selectedId)">Retry run read</button></div>
    <article v-if="selected" class="stack run-result" data-testid="run-result">
      <h3>Run {{ selected.run_id }} <span class="badge">{{ selected.status }}</span></h3>
      <p class="muted">Started {{ selected.started_at }} · Completed {{ selected.completed_at || 'not yet recorded' }}</p>
      <template v-if="selected.representation === 'legacy_v1'"><p class="alert warning">Historical v1 result — untyped legacy snapshot. No modern reviewed guidance, decision mechanism or approvable actions are inferred.</p><p>{{ selected.decision_summary || 'No historical summary.' }}</p><p>Historical routing: {{ selected.routing_outcome || 'not recorded' }}</p><details><summary>Raw historical evidence (not reviewed guidance)</summary><pre>{{ selected.evidence_json }}</pre></details><details><summary>Raw historical response</summary><pre>{{ selected.response_json || 'Not recorded' }}</pre></details></template>
      <template v-else>
      <p v-if="selected.status === 'running'" class="alert warning">Still running or interrupted. Refresh reads to reconcile; do not start a replacement or assume failure from a timeout.</p>
      <p v-if="selected.status === 'failed'" role="alert" class="alert error">Run failed: {{ selected.error_code }} — {{ selected.error_message || 'No additional details recorded.' }} No successful recommendation is implied.</p>
      <template v-if="selected.routing_outcome"><h3>{{ routing[selected.routing_outcome] || selected.routing_outcome }}</h3></template>
      <p v-if="selected.decision_mechanism === 'deterministic'">Mechanism: deterministic policy routing — not a model-generated diagnosis.</p>
      <p v-else-if="selected.decision_mechanism === 'generation'">Mechanism: model selection from reviewed candidates · {{ selected.model || 'model not recorded' }} · prompt {{ selected.prompt_version || 'not recorded' }}. This is not a confidence score.</p>
      <p v-else class="muted">Decision mechanism not recorded yet.</p>
      <ul v-if="selected.warnings.length" class="alert warning"><li v-for="(warning, i) in selected.warnings" :key="i">{{ warning }}</li></ul>
      <div class="context-grid"><section><h3>Requester summary</h3><p class="literal">{{ selected.summary_for_requester || 'Not recorded.' }}</p></section><section><h3>Ticket summary</h3><p class="literal">{{ selected.summary_for_ticket || 'Not recorded.' }}</p></section></div>
      <section><h3>Clarification questions</h3><ul v-if="selected.questions.length"><li v-for="question in selected.questions" :key="question.question_id">{{ question.text }}</li></ul><p v-else class="muted">No questions recorded.</p></section>
      <section><h3>Reviewed guidance &amp; citations</h3><p v-if="!selected.steps.length" class="muted">No reviewed guidance steps recorded. Do not infer instructions from a routing label.</p><ol v-else><li v-for="step in selected.steps" :key="step.step_id" class="guidance-step"><p class="literal">{{ step.instruction }}</p><p class="muted">Role: {{ step.operator_role }}</p><ul><li v-for="(note, i) in step.applicability_notes" :key="i">{{ note }}</li></ul><details v-for="id in step.evidence_ids" :key="id"><summary>Citation {{ id }}</summary><template v-for="source in selected.evidence.filter(item => item.evidence_id === id)" :key="source.evidence_id"><p><strong>{{ source.source_path }} · {{ source.section }}</strong></p><p class="literal">{{ source.content }}</p><p class="muted">Applicability: {{ source.applicability }} · Similarity {{ source.similarity_score }} (not confidence)</p><ul><li v-for="(note, i) in source.applicability_notes" :key="i">{{ note }}</li></ul><p class="muted">Snapshot SHA-256: {{ source.content_sha256 }}</p></template></details></li></ol></section>
      <details v-if="selected.evidence.length"><summary>All persisted evidence snapshots ({{ selected.evidence.length }})</summary><article v-for="source in selected.evidence" :key="source.evidence_id"><h4>{{ source.source_path }} · {{ source.section }}</h4><p class="literal">{{ source.content }}</p><p class="muted">{{ source.evidence_id }} · {{ source.applicability }}</p></article></details>
      </template>
    </article>
    <section v-if="selected && selected.representation !== 'legacy_v1'" class="stack" aria-labelledby="proposals-heading">
      <div class="card-header"><h3 id="proposals-heading">Proposal previews</h3><button class="button secondary small" data-testid="refresh-proposals" :disabled="actionsLoading || deciding || starting" @click="loadActions()">Refresh proposals</button></div>
      <p class="alert warning">Simulated actions only. Approval records a local SQLite simulation/history entry; it does not send a live ticket comment, contact L2, repair a device or call an external ticket system. Rejecting records a local decision.</p>
      <p v-if="actionsLoading" role="status">Loading proposals…</p><p v-if="actionsError" role="alert" class="alert error">{{ actionsError }} Decisions are disabled until a successful read.</p>
      <p v-if="decisionError" role="alert" class="alert error">{{ decisionError }}</p>
      <p v-if="actionsRead && !actions.length" class="muted">No proposals recorded for this run.</p>
      <article v-for="action in actions" :key="action.action_id" class="proposal stack" :data-testid="`proposal-${action.action_id}`">
        <h4>{{ action.action_type === 'simulate_post_comment' ? 'Simulated ticket comment' : 'Simulated L2 escalation' }} <span class="badge">{{ action.status }}</span></h4>
        <p class="muted">{{ action.action_id }} · Created {{ action.created_at }}</p>
        <p v-if="'comment' in action.preview" class="literal">{{ action.preview.comment }}</p><template v-else><p class="literal">{{ action.preview.summary }}</p><p>Reason: {{ action.preview.reason }}</p></template>
        <details><summary>Proposal identity</summary><p class="muted">Proposal hash: {{ action.proposal_hash }}</p><p class="muted">Context fingerprint: {{ action.context_fingerprint }}</p></details>
        <p v-if="action.result_summary" class="alert success">{{ action.result_summary }}</p><p v-if="action.decided_at" class="muted">{{ action.decided_at }} · {{ action.decided_by }}</p>
        <p v-if="journal.decisions[action.action_id]" class="alert warning">Unconfirmed {{ journal.decisions[action.action_id] }} decision retained across refresh. Read proposals before retrying that same decision.</p>
        <div v-if="action.status === 'pending'" class="inline"><button class="button primary small" :disabled="!actionsRead || starting || deciding || !!storageError || (!!journal.decisions[action.action_id] && journal.decisions[action.action_id] !== 'approve')" @click="choose(action, 'approve')">Approve simulation</button><button class="button secondary small" :disabled="!actionsRead || starting || deciding || !!storageError || (!!journal.decisions[action.action_id] && journal.decisions[action.action_id] !== 'reject')" @click="choose(action, 'reject')">Reject proposal</button></div>
        <div v-if="confirming?.action.action_id === action.action_id" role="group" aria-label="Confirm simulated action decision" class="alert warning"><p>Confirm {{ confirming.decision }} for the exact preview above? {{ confirming.decision === 'approve' ? 'Only a local simulation will be recorded.' : 'The proposal will be rejected locally.' }}</p><div class="inline"><button class="button primary small" data-testid="confirm-decision" :disabled="deciding" @click="decide">Confirm {{ confirming.decision }}</button><button class="button secondary small" @click="confirming = null">Cancel decision</button></div></div>
      </article>
      <p v-if="deciding" role="status">Submitting explicit decision…</p>
    </section>
    <section class="stack" aria-labelledby="audit-heading"><div class="card-header"><h3 id="audit-heading">Persisted audit history</h3><button class="button secondary small" :disabled="auditLoading" @click="loadAudit">Refresh audit</button></div><p class="muted">Shared operator/system actors, not verified individual identity. Audit events are separate from source ticket history.</p><p v-if="auditLoading" role="status">Loading audit…</p><p v-if="auditError" role="alert" class="alert error">{{ auditError }} Previously received events may be stale.</p><template v-if="audit"><p v-if="!audit.events.length" class="muted">No audit events on this page.</p><ol v-else class="audit-list"><li v-for="event in audit.events" :key="event.audit_event_id"><strong>{{ event.event_type }}</strong> · {{ event.occurred_at }}<template v-if="event.representation === 'legacy_v1'"><p class="badge">Historical v1 event — untyped payload</p><pre>{{ JSON.stringify(event.payload, null, 2) }}</pre><p class="muted">{{ event.actor_type }} · {{ event.actor_id || 'not recorded' }} · {{ event.approval_status || 'no approval status' }}</p></template><template v-else><p>{{ event.payload.summary }}</p><p class="muted">{{ event.actor_type }} · {{ event.actor_id || 'not recorded' }} · {{ event.approval_status || 'no approval status' }} · run {{ event.run_id || '—' }} · action {{ event.action_id || '—' }}</p><p v-if="event.payload.error_code">{{ event.payload.error_code }}</p></template></li></ol><nav class="inline" aria-label="Audit pagination"><button class="button secondary small" :disabled="auditLoading || audit.offset === 0" @click="pageAudit(-10)">Previous audit events</button><span>{{ audit.total }} events · offset {{ audit.offset }}</span><button class="button secondary small" :disabled="auditLoading || audit.offset + audit.limit >= audit.total || audit.offset + audit.limit > 100000" @click="pageAudit(10)">Next audit events</button></nav></template></section>
  </section>
</template>

<style scoped>
.run-list, .audit-list { padding-left: 20px; }
.run-list li, .audit-list li, .guidance-step { margin-bottom: 16px; }
.run-result, .proposal { border-top: 1px solid var(--border); padding-top: 20px; }
.run-result, .proposal, details { overflow-wrap: anywhere; }
.proposal h4 { margin: 0; }
summary { cursor: pointer; color: var(--teal); margin-bottom: 10px; }
.literal { white-space: pre-wrap; overflow-wrap: anywhere; }
.context-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
@media (max-width: 620px) { .context-grid { grid-template-columns: 1fr; } }
</style>
