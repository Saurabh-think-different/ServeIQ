<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { apiRequest } from '../services/api'
import EvalCaseDetail from './EvalCaseDetail.vue'
import EvalRunComparison from './EvalRunComparison.vue'
import CitationPanel from './CitationPanel.vue'

const key = ref('')
const unlocked = ref(false)
const busy = ref(false)
const error = ref('')
const runs = ref([])
const active = ref(null)
const cases = ref([])
const detail = ref(null)
const detailLoading = ref(false)
const catalog = ref([])
const config = ref({ allowed_models: [], max_cases: 25 })
const model = ref('')
const selected = ref([])
const panel = ref('results')
const creating = ref(false)
const acceptedCost = ref(false)
const citation = ref(null)
const hasMore = ref(false)
let timer
let generation = 0
let detailGeneration = 0
const activeJob = computed(() => runs.value.some(run => ['queued', 'running'].includes(run.status)))
const completed = computed(() => active.value?.completed_count || 0)
const progress = computed(() => active.value?.case_count ? Math.round(completed.value / active.value.case_count * 100) : 0)
const passed = computed(() => active.value?.passed_count || 0)
const failed = computed(() => (active.value?.failed_count || 0) + (active.value?.error_count || 0))
const nativeScore = computed(() => {
  const values = cases.value.map(row => row.assessment?.native_evaluator?.score).filter(value => typeof value === 'number')
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
})
const recall = computed(() => {
  const values = cases.value.map(row => row.assessment?.metrics?.kb_source_recall).filter(metric => metric && ['passed', 'failed'].includes(metric.status) && typeof metric.value === 'number')
  return values.length ? values.reduce((sum, metric) => sum + metric.value, 0) / values.length : null
})
function percent(value) { return typeof value === 'number' ? `${Math.round(value * 100)}%` : 'N/A' }
function duration(value) { return typeof value === 'number' ? `${(value / 1000).toFixed(1)}s` : 'N/A' }
function date(value) {
  if (!value) return 'Not started'
  const parsed = new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : value + 'Z')
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
function lock() {
  clearTimeout(timer)
  generation++
  detailGeneration++
  unlocked.value = false
  key.value = ''
  runs.value = []; active.value = null; cases.value = []; detail.value = null; catalog.value = []
  selected.value = []; citation.value = null; panel.value = 'results'
}
async function request(path, options = {}) {
  try {
    return await apiRequest(`/evaluations${path}`, { ...options, headers: { 'X-Ingestion-Key': key.value } })
  } catch (failure) {
    if (failure.status === 401) lock()
    throw failure
  }
}
const comparisonApi = {
  getRun: id => request(`/runs/${encodeURIComponent(id)}`),
  getCases: id => request(`/runs/${encodeURIComponent(id)}/cases?limit=100`),
}
async function unlock() {
  if (!key.value.trim() || busy.value) return
  key.value = key.value.trim(); busy.value = true; error.value = ''
  try {
    const [history, settings, definitions] = await Promise.all([
      request('/runs?limit=50'), request('/config'), request('/cases?limit=100'),
    ])
    runs.value = history; hasMore.value = history.length === 50
    config.value = settings; model.value = settings.default_model
    catalog.value = definitions.cases || []
    selected.value = catalog.value.slice(0, 3).map(item => item.id)
    unlocked.value = true
    if (history.length) await selectRun(history[0].id)
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
async function loadRuns(more = false) {
  const offset = more ? runs.value.length : 0
  const history = await request(`/runs?limit=50&offset=${offset}`)
  runs.value = more ? [...runs.value, ...history] : history
  hasMore.value = history.length === 50
}
async function selectRun(id) {
  const current = ++generation
  detailGeneration++; detail.value = null; error.value = ''
  try {
    const [run, results] = await Promise.all([comparisonApi.getRun(id), comparisonApi.getCases(id)])
    if (current !== generation || !unlocked.value) return
    active.value = run; cases.value = results
    history.replaceState(null, '', `/evals?run=${encodeURIComponent(id)}`)
    schedule()
  } catch (failure) { if (current === generation) error.value = failure.message }
}
function schedule() {
  clearTimeout(timer)
  if (!unlocked.value || !activeJob.value) return
  timer = setTimeout(async () => {
    try {
      const id = active.value?.id
      await loadRuns()
      if (id && unlocked.value) {
        const current = generation
        const [run, results] = await Promise.all([comparisonApi.getRun(id), comparisonApi.getCases(id)])
        if (current === generation) { active.value = run; cases.value = results }
      }
    } catch (failure) { error.value = failure.message }
    schedule()
  }, 2500)
}
async function refresh() {
  busy.value = true; error.value = ''
  try { await loadRuns(); if (active.value) await selectRun(active.value.id); else if (runs.value.length) await selectRun(runs.value[0].id) }
  catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
async function loadMore() {
  busy.value = true
  try { await loadRuns(true) } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
async function openCase(id) {
  const current = ++detailGeneration
  const runId = active.value?.id
  detailLoading.value = true; detail.value = null; error.value = ''
  try {
    const result = await request(`/runs/${encodeURIComponent(runId)}/cases/${encodeURIComponent(id)}`)
    if (current === detailGeneration && runId === active.value?.id) detail.value = result
  } catch (failure) { if (current === detailGeneration) error.value = failure.message }
  finally { if (current === detailGeneration) detailLoading.value = false }
}
async function startRun() {
  if (creating.value || !selected.value.length || !acceptedCost.value || activeJob.value) return
  creating.value = true; error.value = ''
  try {
    const result = await request('/runs', { method: 'POST', body: { suite: 'curated', model: model.value, case_ids: selected.value } })
    await loadRuns(); panel.value = 'results'; await selectRun(result.id)
    acceptedCost.value = false
  } catch (failure) { error.value = failure.message }
  finally { creating.value = false }
}
async function cancel() {
  if (!active.value) return
  busy.value = true
  try { await request(`/runs/${active.value.id}/cancel`, { method: 'POST' }); await selectRun(active.value.id) }
  catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
function toggleAll() {
  selected.value = selected.value.length === catalog.value.length ? [] : catalog.value.slice(0, config.value.max_cases).map(item => item.id)
}
function rerun() {
  selected.value = cases.value.map(item => item.case_id).filter(id => catalog.value.some(item => item.id === id))
  model.value = active.value?.model || config.value.default_model
  panel.value = 'launch'
}
onBeforeUnmount(() => { clearTimeout(timer); generation++; detailGeneration++; key.value = '' })
</script>

<template>
  <main class="eval-page">
    <header class="eval-header">
      <a class="brand" href="/">Serve<span>IQ</span><small>Evaluation lab</small></a>
      <div class="header-actions"><span class="private-label">PRIVATE WORKSPACE</span><a href="/">Back to support</a><button v-if="unlocked" class="quiet" @click="lock">Lock dashboard</button></div>
    </header>
    <section v-if="!unlocked" class="access-card">
      <div class="access-icon" aria-hidden="true">↗</div><p class="eyebrow">SERVEIQ · EVALUATIONS</p>
      <h1>Make agent quality visible.</h1>
      <p>Inspect recorded conversations, compare expected and actual outcomes, and see exactly why a case passed or failed.</p>
      <form @submit.prevent="unlock"><label for="eval-key">Administrator ingestion key</label><input id="eval-key" v-model="key" type="password" autocomplete="off" required spellcheck="false" placeholder="Enter your X-Ingestion-Key" :disabled="busy"><button class="primary" :disabled="busy || !key.trim()">{{ busy ? 'Opening dashboard…' : 'Open evaluation dashboard' }}</button></form>
      <p class="access-note">Uses the existing admin key. It stays in this page’s memory, is never saved in browser storage, and is cleared when you lock or leave the page. The hidden URL is not an access-control mechanism.</p>
      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    </section>
    <template v-else>
      <section class="page-intro"><div><p class="eyebrow">AGENT QUALITY / EVALUATION HISTORY</p><h1>Evidence, not just a pass rate.</h1><p>Real tool trajectories, grounded sources and conversation outcomes. No simulated scores.</p></div><button class="quiet" :disabled="busy" @click="refresh">{{ busy ? 'Refreshing…' : 'Refresh results' }}</button></section>
      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
      <nav class="tabs" aria-label="Evaluation sections"><button :class="{ active: panel === 'results' }" @click="panel = 'results'">Run history <span>{{ runs.length }}</span></button><button :class="{ active: panel === 'compare' }" @click="panel = 'compare'">Compare runs</button><button :class="{ active: panel === 'launch' }" @click="panel = 'launch'">Run evaluations</button></nav>
      <EvalRunComparison v-if="panel === 'compare'" :runs="runs" :current-run-id="active?.id || ''" :api="comparisonApi" @select-run="id => { panel = 'results'; selectRun(id) }" />
      <section v-else-if="panel === 'launch'" class="launch card">
        <div class="section-heading"><div><h2>Launch a curated evaluation</h2><p>Runs in isolated SQLite fixtures. Production incidents and L2 counters are not modified.</p></div><span class="pill">{{ catalog.length }} scenarios</span></div>
        <div class="launch-controls"><label>Model<select v-model="model" :disabled="creating || activeJob"><option v-for="name in config.allowed_models" :key="name" :value="name">{{ name }}</option></select></label><div><strong>{{ selected.length }} cases selected</strong><p>Maximum {{ config.max_cases }} per run. One run at a time.</p></div><button class="quiet" :disabled="creating" @click="toggleAll">{{ selected.length === catalog.length ? 'Clear selection' : 'Select all' }}</button></div>
        <div class="case-picker"><label v-for="item in catalog" :key="item.id" class="pick-case"><input v-model="selected" type="checkbox" :value="item.id" :disabled="creating || activeJob || (!selected.includes(item.id) && selected.length >= config.max_cases)"><span><strong>{{ item.title }}</strong><small>{{ item.turns.length }} turn{{ item.turns.length === 1 ? '' : 's' }} · {{ item.expected_statuses.join(' → ') }}</small><small v-if="item.metadata?.policy_override" class="policy-note">Policy-aware expectation</small></span></label></div>
        <div class="launch-footer"><label class="cost-confirm"><input v-model="acceptedCost" type="checkbox" :disabled="creating || activeJob">I understand this makes live model and embedding calls and may incur API charges.</label><p v-if="activeJob" class="warning">Another evaluation is active. Open its run to follow progress or request cancellation.</p><button class="primary" :disabled="!selected.length || !acceptedCost || creating || activeJob" @click="startRun">{{ creating ? 'Starting…' : `Run ${selected.length} selected cases` }}</button></div>
      </section>
      <div v-else class="results-layout">
        <aside class="run-list card"><div class="list-heading"><h2>Recorded runs</h2><span>{{ runs.length }}</span></div><p v-if="!runs.length" class="empty">No evaluation runs yet. Select Run evaluations to start one.</p><button v-for="run in runs" :key="run.id" class="run-item" :class="{ selected: active?.id === run.id }" @click="selectRun(run.id)"><div><span class="state" :class="run.status">{{ run.status }}</span><small>{{ date(run.created_at) }}</small></div><strong>{{ run.model }}</strong><span>{{ run.case_count }} cases · {{ run.passed_count }} passed · {{ run.failed_count + run.error_count }} failed/error</span><code>{{ run.id.slice(0, 8) }}</code></button><button v-if="hasMore" class="quiet load-more" :disabled="busy" @click="loadMore">Load older runs</button></aside>
        <section class="run-content">
          <template v-if="active">
            <div class="run-overview card"><div class="section-heading"><div><p class="eyebrow">{{ active.suite.toUpperCase() }} SUITE · {{ active.id.slice(0, 8) }}</p><h2>{{ active.model }}</h2><p>{{ active.configuration?.prompt_version || 'Prompt version not recorded' }} · {{ date(active.created_at) }}</p></div><div class="overview-buttons"><button class="quiet" :disabled="activeJob || !cases.length" @click="rerun">Rerun these cases</button><button v-if="['queued', 'running'].includes(active.status)" class="danger-button" :disabled="busy || active.cancel_requested" @click="cancel">{{ active.cancel_requested ? 'Cancellation requested' : 'Cancel run' }}</button><span class="state" :class="active.status">{{ active.status }}</span></div></div>
              <div v-if="['queued', 'running'].includes(active.status)" class="run-progress" role="status"><div><span>{{ completed }} / {{ active.case_count }} cases completed</span><span>{{ progress }}%</span></div><progress :value="completed" :max="active.case_count" /><small>Updates automatically. Cancellation takes effect after the current case.</small></div>
              <p v-if="active.error" class="error-banner">{{ active.error }}</p>
              <div class="kpis"><div><span>Overall pass rate</span><strong>{{ active.completed_count ? percent(passed / active.completed_count) : 'N/A' }}</strong><small>{{ passed }} passed / {{ active.completed_count }} completed</small></div><div><span>Native tool trajectory</span><strong>{{ percent(nativeScore) }}</strong><small>Required tool usage, not full answer quality</small></div><div><span>Expected-source recall</span><strong>{{ percent(recall) }}</strong><small>Only cases with source expectations</small></div><div><span>Recorded case time</span><strong>{{ duration(active.summary?.duration_ms) }}</strong><small>{{ failed }} failed/error · token cost not recorded</small></div></div>
              <p class="explanation">A correct conversation outcome can still fail a stricter retrieval check. Different scenario sets are not evidence of model improvement.</p>
            </div>
            <div class="card case-results"><div class="section-heading"><div><h2>Case outcomes</h2><p>Select a case to inspect its full conversation, tools, citations and checks.</p></div></div><div class="table-scroll"><table><thead><tr><th>Scenario</th><th>Result</th><th>Tool score</th><th>Time</th><th></th></tr></thead><tbody><tr v-for="item in cases" :key="item.case_id"><td><strong>{{ item.title }}</strong><small>{{ item.case_id }}</small><p v-if="item.assessment?.failures?.length" class="failure-hint">{{ item.assessment.failures[0].split(':')[0].replaceAll('_', ' ') }}</p></td><td><span class="state" :class="item.status">{{ item.status }}</span></td><td>{{ percent(item.assessment?.native_evaluator?.score) }}</td><td>{{ duration(item.duration_ms) }}</td><td><button class="inspect-button" @click="openCase(item.case_id)">Inspect <span aria-hidden="true">↗</span></button></td></tr></tbody></table><p v-if="!cases.length" class="empty">Cases are loading or queued.</p></div></div>
            <EvalCaseDetail v-if="detail || detailLoading" :detail="detail" :loading="detailLoading" @close="detail = null; detailGeneration++" @cite="value => citation = value" />
            <details class="card technical"><summary>Run configuration and isolation details</summary><p>Source ticket answers are excluded from the evaluation history fixture. No labels are sent to the agent.</p><pre>{{ JSON.stringify(active.configuration, null, 2) }}</pre><details><summary>Aggregate report</summary><pre>{{ JSON.stringify(active.summary, null, 2) }}</pre></details></details>
          </template>
          <div v-else class="card empty"><h2>Choose an evaluation run</h2><p>Recorded cases and explanations will appear here.</p></div>
        </section>
      </div>
      <CitationPanel :citation="citation" @close="citation = null" />
    </template>
  </main>
</template>

<style scoped>
.eval-page{min-height:100dvh;background:var(--bg);padding-bottom:50px;color:var(--ink)}.eval-header{height:76px;background:#fff;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 4%;gap:20px}.brand{font-size:24px;font-weight:750;letter-spacing:-1px;color:var(--ink);text-decoration:none;display:flex;align-items:center;gap:0}.brand>span{color:var(--accent)}.brand small{font-size:12px;font-weight:500;letter-spacing:0;margin-left:20px;padding-left:20px;border-left:1px solid var(--line);color:var(--muted)}.header-actions{display:flex;align-items:center;gap:20px;font-size:12px}.header-actions a{text-decoration:none}.private-label{font-size:9px;letter-spacing:.13em;color:var(--muted)}.page-intro,.tabs,.results-layout,.launch,.eval-page>.error-banner{max-width:1500px;margin-left:auto;margin-right:auto}.page-intro{padding:34px 28px 24px;display:flex;align-items:center;justify-content:space-between;gap:20px}.page-intro h1{font-size:29px;letter-spacing:-1px;margin:8px 0}.page-intro p,.section-heading p{font-size:12px;color:var(--muted);line-height:1.7;margin:5px 0}.eyebrow{font-size:9px!important;letter-spacing:.13em;font-weight:700;color:var(--muted)}.tabs{display:flex;gap:25px;padding:0 28px;border-bottom:1px solid var(--line);margin-bottom:24px}.tabs button{border:0;background:transparent;padding:13px 0;color:var(--muted);font-weight:600;font-size:13px;border-bottom:2px solid transparent}.tabs button.active{color:var(--accent);border-bottom-color:var(--accent)}.tabs span{background:var(--accent-soft);padding:2px 6px;border-radius:4px;font-size:10px;margin-left:5px}.card{background:#fff;border:1px solid var(--line);border-radius:12px}.results-layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:24px;padding:0 28px}.run-list{align-self:start;overflow:hidden}.list-heading{display:flex;justify-content:space-between;align-items:center;padding:19px;border-bottom:1px solid var(--line)}h2{font-size:15px;margin:0}.list-heading span{font-size:11px;color:var(--muted)}.run-item{display:flex;flex-direction:column;width:100%;padding:17px 18px;gap:9px;background:white;border:0;border-bottom:1px solid var(--line);text-align:left;color:var(--ink)}.run-item.selected{background:#f0f9f8;box-shadow:inset 3px 0 var(--accent)}.run-item>div{display:flex;align-items:center;justify-content:space-between;gap:8px}.run-item strong{font-size:13px}.run-item small{font-size:9px;color:var(--muted)}.run-item>span{font-size:11px;color:var(--muted)}.run-item code{font-size:10px;color:var(--muted)}.run-content{min-width:0;display:flex;flex-direction:column;gap:20px}.run-overview{padding:24px}.section-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:15px}.section-heading h2{font-size:18px}.overview-buttons{display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;border-top:1px solid var(--line);padding-top:22px;margin-top:20px}.kpis>div{display:flex;flex-direction:column;gap:8px}.kpis span{font-size:10px;color:var(--muted)}.kpis strong{font-size:28px;letter-spacing:-1px}.kpis small{font-size:10px;color:var(--muted);line-height:1.6}.explanation{font-size:11px;color:var(--muted);background:var(--bg);padding:10px 12px;border-radius:7px;margin:20px 0 0;line-height:1.6}.state{font-size:10px;font-weight:650;border-radius:5px;padding:4px 7px;background:#eef1f4;color:#667a82;white-space:nowrap;text-transform:capitalize}.state.passed,.state.completed{background:#e7f5ec;color:#227449}.state.failed,.state.error,.state.invalidated{background:#fbecec;color:#b14444}.state.running,.state.queued{background:#fff3d9;color:#91640f}.case-results{overflow:hidden}.case-results>.section-heading{padding:22px}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;text-align:left;font-size:12px}th{font-size:10px;color:var(--muted);background:#f8fafb;font-weight:600;padding:12px 20px;border-top:1px solid var(--line)}td{padding:16px 20px;border-top:1px solid var(--line);vertical-align:top}td:first-child{min-width:230px}td strong{font-size:12px;line-height:1.7;font-weight:550}td small{display:block;margin-top:3px;font-size:10px;color:var(--muted)}.failure-hint{font-size:10px;color:var(--danger);margin:6px 0 0}.inspect-button{border:0;background:var(--accent-soft);color:var(--accent);font-size:11px;padding:7px 10px;border-radius:6px;white-space:nowrap}.quiet,.danger-button{border:1px solid var(--line);background:#fff;color:var(--ink);font-size:11px;padding:9px 12px;border-radius:7px}.danger-button{color:var(--danger);border-color:#eed7d7}.primary{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:12px 18px;font-size:12px;font-weight:600}.empty{padding:28px;color:var(--muted);font-size:12px;line-height:1.8}.error-banner{padding:12px 16px;background:#fff1f1;color:var(--danger);border:1px solid #f0d6d6;border-radius:8px;font-size:12px;line-height:1.6}.access-card{width:min(500px,calc(100% - 36px));margin:80px auto;background:white;border:1px solid var(--line);border-radius:16px;padding:36px}.access-icon{width:46px;height:46px;background:var(--accent-soft);color:var(--accent);border-radius:12px;display:grid;place-items:center;font-size:25px;margin-bottom:24px}.access-card h1{font-size:28px;letter-spacing:-1px}.access-card>p{font-size:13px;line-height:1.8;color:var(--muted)}.access-card form{display:flex;flex-direction:column;gap:10px;margin-top:24px}.access-card label,.launch-controls label{font-size:11px;font-weight:600}.access-card input,select{border:1px solid var(--line);border-radius:7px;padding:11px;background:white;color:var(--ink);min-width:0}.access-card .access-note{font-size:10px;margin-top:18px}.launch{padding:26px;margin-inline:auto;width:calc(100% - 56px);max-width:1444px}.pill{font-size:10px;background:var(--accent-soft);color:var(--accent);padding:6px 9px;border-radius:5px}.launch-controls{display:flex;gap:25px;align-items:center;padding:22px 0;border-bottom:1px solid var(--line)}.launch-controls label{display:flex;flex-direction:column;gap:8px;min-width:220px}.launch-controls p{font-size:11px;color:var(--muted)}.launch-controls strong{font-size:12px}.case-picker{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 24px;max-height:460px;overflow:auto;margin:15px 0}.pick-case{display:flex;gap:10px;align-items:flex-start;padding:14px 4px;border-bottom:1px solid var(--line);cursor:pointer}.pick-case input,.cost-confirm input{accent-color:var(--accent);margin-top:3px}.pick-case strong{display:block;font-size:12px;line-height:1.7;font-weight:550}.pick-case small{display:block;font-size:10px;color:var(--muted);line-height:1.7}.pick-case .policy-note{color:var(--accent)}.launch-footer{border-top:1px solid var(--line);padding-top:20px}.cost-confirm{display:flex;gap:9px;align-items:flex-start;font-size:11px;color:var(--muted);line-height:1.7;margin-bottom:18px}.warning{font-size:12px;color:#91640f}.run-progress{margin-top:20px;background:#f6fafb;padding:14px;border-radius:8px}.run-progress>div{display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}progress{width:100%;height:7px;accent-color:var(--accent);margin:10px 0}.run-progress small{font-size:10px;color:var(--muted)}.technical{padding:18px;font-size:11px;color:var(--muted)}.technical summary{cursor:pointer;font-weight:600}.technical p{line-height:1.8}.technical pre{white-space:pre-wrap;overflow:auto;max-height:360px;font-size:10px;line-height:1.7;background:var(--bg);padding:15px;border-radius:7px}.load-more{margin:12px 18px}@media(max-width:1100px){.kpis{grid-template-columns:repeat(2,1fr)}.results-layout{grid-template-columns:230px minmax(0,1fr)}.section-heading{flex-direction:column}.overview-buttons{justify-content:flex-start}}@media(max-width:720px){.eval-header{padding:0 18px;height:68px}.brand small,.private-label{display:none}.header-actions{gap:10px;font-size:11px}.page-intro{padding:25px 18px 20px;align-items:flex-start}.page-intro h1{font-size:24px}.tabs{padding:0 18px;gap:20px}.tabs button{font-size:11px}.results-layout{display:flex;flex-direction:column;padding:0 16px;gap:18px}.run-list{width:100%;max-height:235px;overflow:auto}.run-item{padding:12px 16px}.run-overview{padding:18px}.kpis strong{font-size:25px}.launch{width:calc(100% - 32px);padding:20px}.launch-controls{flex-wrap:wrap;gap:15px}.case-picker{grid-template-columns:1fr}.access-card{margin-top:42px;padding:26px}.case-results>.section-heading{padding:18px}th,td{padding:12px}.page-intro>.quiet{white-space:nowrap;padding:8px}.technical{padding:15px}} 
</style>
