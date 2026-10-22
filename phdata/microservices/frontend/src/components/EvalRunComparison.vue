<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  runs: { type: Array, default: () => [] },
  currentRunId: { type: String, default: '' },
  api: { type: Object, required: true },
})
const emit = defineEmits(['select-run'])
const selectedA = ref('')
const selectedB = ref('')
const result = ref(null)
const loading = ref(false)
const error = ref('')
let request = 0
let disposed = false

const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {}
const number = value => typeof value === 'number' && Number.isFinite(value) ? value : null
const count = value => Number.isInteger(value) && value >= 0 ? value : null
const text = value => typeof value === 'string' && value.trim() ? value : 'N/A'
const display = value => number(value) === null ? 'N/A' : value.toLocaleString(undefined, { maximumFractionDigits: 3 })
const percentage = value => number(value) === null ? 'N/A' : `${(value * 100).toFixed(1)}%`
const finished = row => ['passed', 'failed', 'error'].includes(row.status)
const average = values => {
  const scores = values.filter(value => number(value) !== null)
  return scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null
}
const nativeScore = row => number(object(object(row.assessment).native_evaluator).score)
const kbRecall = row => {
  const metric = object(object(object(row.assessment).metrics).kb_source_recall)
  return ['passed', 'failed'].includes(metric.status) ? number(metric.value) : null
}
const statusClass = status => ({
  passed: 'passed', failed: 'failed', error: 'failed',
  pending: 'active', running: 'active', queued: 'active', skipped: 'skipped',
}[status] || 'skipped')
const options = computed(() => [...props.runs]
  .filter(run => run && typeof run.id === 'string' && run.id)
  .sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0)))
const canCompare = computed(() => selectedA.value && selectedB.value && selectedA.value !== selectedB.value)
const runLabel = run => `${run.id} · ${text(run.model)} · ${text(run.created_at)}`

watch([options, () => props.currentRunId], ([runs, current], previous) => {
  const ids = new Set(runs.map(run => run.id))
  const currentChanged = !previous || current !== previous[1]
  if (currentChanged || !ids.has(selectedA.value)) {
    selectedA.value = ids.has(current) ? current : runs[0]?.id || ''
  }
  if (currentChanged || !ids.has(selectedB.value) || selectedA.value === selectedB.value) {
    selectedB.value = runs.find(run => run.id !== selectedA.value)?.id || ''
  }
}, { immediate: true })

// Invalidate immediately: an older request must never populate a new selection.
watch([selectedA, selectedB, () => props.api], () => {
  request += 1
  result.value = null
  error.value = ''
  loading.value = false
}, { flush: 'sync' })
onBeforeUnmount(() => { disposed = true; request += 1 })

async function compare() {
  if (!canCompare.value) return
  const version = ++request
  const ids = [selectedA.value, selectedB.value]
  const api = props.api
  loading.value = true
  error.value = ''
  result.value = null
  try {
    if (typeof api?.getRun !== 'function' || typeof api?.getCases !== 'function') {
      throw new Error('Missing read API')
    }
    const snapshots = await Promise.all(ids.map(async id => {
      const [run, cases] = await Promise.all([api.getRun(id), api.getCases(id)])
      if (!run || run.id !== id || !Array.isArray(cases)
        || cases.some(row => !row || typeof row.case_id !== 'string' || !row.case_id)
        || new Set(cases.map(row => row.case_id)).size !== cases.length) {
        throw new Error('Invalid stored result')
      }
      return { run, cases }
    }))
    if (!disposed && version === request) result.value = snapshots
  } catch {
    if (!disposed && version === request) {
      error.value = 'Could not load both stored runs and case summaries. Check admin access and retry Compare.'
    }
  } finally {
    if (!disposed && version === request) loading.value = false
  }
}

function statistics({ run, cases }) {
  const summary = object(run.summary)
  const counts = object(summary.counts)
  const completeList = count(run.case_count) !== null && cases.length === run.case_count
  const caseCount = status => completeList ? cases.filter(row => row.status === status).length : null
  const passed = count(counts.passed) ?? count(run.passed_count) ?? caseCount('passed')
  const failed = count(counts.failed) ?? count(run.failed_count) ?? caseCount('failed')
  const errors = count(counts.error) ?? count(run.error_count) ?? caseCount('error')
  const completed = count(summary.completed) ?? count(run.completed_count)
    ?? ([passed, failed, errors].every(value => value !== null) ? passed + failed + errors : null)
  const duration = number(summary.duration_ms) ?? number(run.duration_ms)
  const passRate = number(summary.pass_rate) ?? (completed > 0 && passed !== null ? passed / completed : null)
  const latency = completed > 0 && duration !== null && duration >= 0 ? duration / completed : null
  const native = number(summary.native_trajectory_mean)
    ?? (completeList ? average(cases.filter(finished).map(nativeScore)) : null)
  return {
    total: count(run.case_count), passed, failed, errors, completed, passRate, latency, native,
    model: text(run.model ?? object(run.configuration).model),
    prompt: text(object(run.configuration).prompt_version),
  }
}
const stats = computed(() => result.value?.map(statistics) || [])
const kpis = computed(() => [
  { label: 'Total cases', values: stats.value.map(item => display(item.total)) },
  { label: 'Passed', values: stats.value.map(item => display(item.passed)) },
  { label: 'Failed', values: stats.value.map(item => display(item.failed)) },
  { label: 'Errors', values: stats.value.map(item => display(item.errors)) },
  { label: 'Completed', values: stats.value.map(item => display(item.completed)) },
  { label: 'Pass rate (passed / completed)', values: stats.value.map(item => percentage(item.passRate)) },
  { label: 'Mean case latency (ms)', values: stats.value.map(item => display(item.latency)) },
  { label: 'Native ADK trajectory mean (0–1)', values: stats.value.map(item => display(item.native)) },
  { label: 'Model', values: stats.value.map(item => item.model) },
  { label: 'Prompt version', values: stats.value.map(item => item.prompt) },
])
const matched = computed(() => {
  if (!result.value) return []
  const right = new Map(result.value[1].cases.map(row => [row.case_id, row]))
  return result.value[0].cases.filter(row => right.has(row.case_id))
    .map(a => ({ id: a.case_id, a, b: right.get(a.case_id) }))
})
const sameIds = computed(() => result.value
  && result.value[0].cases.length === result.value[1].cases.length
  && matched.value.length === result.value[0].cases.length)

function canonical(value) {
  if (Array.isArray(value)) return JSON.stringify(value.map(item => canonical(item)))
  if (value && typeof value === 'object') {
    return JSON.stringify(Object.keys(value).sort().map(key => [key, canonical(value[key])]))
  }
  return JSON.stringify(value)
}
const present = value => value !== null && value !== undefined && value !== ''
  && (typeof value !== 'object' || Object.keys(value).length > 0)
const provenance = computed(() => {
  if (!result.value) return []
  const configurations = result.value.map(item => object(item.run.configuration))
  return [
    ['Case-set fingerprint', 'case_set_sha256'],
    ['Source fixture hash', 'fixture_sha256'],
    ['KB generation fingerprint', 'kb_generation_sha256'],
    ['Prompt code fingerprint (comparison variable)', 'prompt_sha256'],
    ['Source / metrics fingerprints', 'source_sha256'],
  ].map(([label, key]) => {
    const values = configurations.map(config => config[key])
    const state = values.every(present)
      ? canonical(values[0]) === canonical(values[1]) ? 'Same' : 'Different'
      : 'Unknown'
    return { label, key, state, values: values.map(value => !present(value) ? 'N/A'
      : typeof value === 'string' ? value : canonical(value)) }
  })
})
const fixtureState = computed(() => provenance.value.find(item => item.key === 'fixture_sha256')?.state || 'Unknown')
const variableBadges = computed(() => ['model', 'prompt'].map(key => {
  const values = stats.value.map(item => item[key])
  const label = key === 'model' ? 'Model' : 'Prompt version'
  return `${label}: ${values.includes('N/A') ? 'unknown' : values[0] === values[1] ? 'same' : 'different (comparison variable)'}`
}))
const warnings = computed(() => {
  if (!result.value) return []
  const notices = []
  if (!sameIds.value) notices.push('Different scenarios: the case ID sets differ. Aggregate results use different benchmarks, not a same-case improvement comparison.')
  for (const item of provenance.value) {
    if (item.state === 'Different') notices.push(`${item.label} differs between A and B.${item.key === 'prompt_sha256' ? ' Prompt variation may be intentional; interpret it as a comparison variable.' : ' This can confound the comparison.'}`)
    if (item.state === 'Unknown') notices.push(`${item.label} is missing for one or both runs; equivalence cannot be verified.`)
  }
  result.value.forEach(({ run, cases }, index) => {
    const side = index === 0 ? 'A' : 'B'
    if (count(run.case_count) === null || run.case_count !== cases.length) {
      notices.push(`Run ${side}: case summaries may be incomplete (${cases.length} loaded; ${display(count(run.case_count))} expected). Matching is limited to loaded cases.`)
    }
    if (run.status !== 'completed') notices.push(`Run ${side} is ${text(run.status)}; these are not final completed-run results.`)
    if (object(run.summary).snapshot_valid === false) notices.push(`Run ${side} has an invalid snapshot. Do not use it as a comparable benchmark.`)
  })
  return notices
})
</script>

<template>
  <section class="eval-comparison" aria-label="Stored evaluation run comparison" :aria-busy="loading">
    <header>
      <h2>Compare stored evaluation runs</h2>
      <p>Read-only comparison. Compare retrieves saved results only; it never starts a model run.</p>
    </header>
    <form class="selectors" @submit.prevent="compare">
      <label>
        <span>Run A (defaults to current run)</span>
        <select v-model="selectedA" required>
          <option disabled value="">Select run A</option>
          <option v-for="run in options" :key="run.id" :value="run.id">{{ runLabel(run) }}</option>
        </select>
      </label>
      <label>
        <span>Run B (defaults to latest other run)</span>
        <select v-model="selectedB" required>
          <option disabled value="">Select run B</option>
          <option v-for="run in options" :key="run.id" :value="run.id">{{ runLabel(run) }}</option>
        </select>
      </label>
      <button class="compare-button" type="submit" :disabled="!canCompare || loading">
        {{ loading ? 'Loading…' : 'Compare' }}
      </button>
    </form>
    <p v-if="options.length < 2" class="note">At least two stored runs are needed for comparison.</p>
    <p v-else-if="selectedA && selectedA === selectedB" class="note">Choose two different runs.</p>
    <p v-if="loading" role="status">Loading both runs and case summaries…</p>
    <p v-if="error" class="load-error" role="alert">{{ error }}</p>
    <p v-if="!result && !loading && !error && canCompare" class="note">Select Compare to load this pair.</p>

    <template v-if="result">
      <div class="badges" aria-label="Comparison context">
        <span class="context-badge">Matched cases: {{ matched.length }} (A: {{ result[0].cases.length }}, B: {{ result[1].cases.length }})</span>
        <span class="context-badge">Same fixture: {{ fixtureState === 'Same' ? 'yes' : fixtureState === 'Different' ? 'no' : 'unverified' }}</span>
        <span v-for="badge in variableBadges" :key="badge" class="context-badge">{{ badge }}</span>
        <span v-for="(snapshot, index) in result" :key="snapshot.run.id" class="context-badge">
          {{ index === 0 ? 'A' : 'B' }}{{ snapshot.run.id === currentRunId ? ': current run' : ': stored run' }} · {{ text(snapshot.run.model) }}
        </span>
      </div>

      <aside v-if="warnings.length" class="comparison-warning" role="alert">
        <h3>Warning: comparison may not be like-for-like</h3>
        <ul><li v-for="notice in warnings" :key="notice">{{ notice }}</li></ul>
        <p>Model and prompt changes are comparison variables, not proof of improvement. Do not attribute aggregate differences to them when scenarios, fixtures, KB or evaluator code differ.</p>
      </aside>
      <aside v-if="!matched.length" class="comparison-warning">
        <h3>Different scenarios — no common cases</h3>
        <p>These runs are not a comparable benchmark: A has {{ result[0].cases.length }} cases and B has {{ result[1].cases.length }}, with disjoint case IDs. Summaries below are descriptive only; there is no same-case improvement claim.</p>
      </aside>

      <h3>Whole-run summaries (descriptive, not matched-case scores)</h3>
      <p class="note">Pass rate uses completed cases, including errors. Mean latency is stored duration_ms / completed. Missing values are N/A, never assumed zero. Native mean falls back to scored completed case assessments only when all case summaries are loaded.</p>
      <div class="table-scroll" role="region" aria-label="Whole-run summary table" tabindex="0">
        <table>
          <caption>Saved aggregates for each run; no improvement ranking</caption>
          <thead><tr>
            <th scope="col">Measure</th>
            <th v-for="(snapshot, index) in result" :key="snapshot.run.id" scope="col">
              Run {{ index === 0 ? 'A' : 'B' }} · {{ text(snapshot.run.status) }}
              <button type="button" class="open-button" @click="emit('select-run', snapshot.run.id)">Open run {{ index === 0 ? 'A' : 'B' }}</button>
            </th>
          </tr></thead>
          <tbody><tr v-for="kpi in kpis" :key="kpi.label">
            <th scope="row">{{ kpi.label }}</th>
            <td v-for="(value, index) in kpi.values" :key="index">{{ value }}</td>
          </tr></tbody>
        </table>
      </div>

      <details class="provenance">
        <summary>Fixture, KB, prompt and source / metrics fingerprints</summary>
        <div class="table-scroll" role="region" aria-label="Provenance fingerprints" tabindex="0">
          <table>
            <caption>Missing fingerprints do not establish equivalence</caption>
            <thead><tr><th scope="col">Fingerprint</th><th scope="col">Check</th><th scope="col">A</th><th scope="col">B</th></tr></thead>
            <tbody><tr v-for="item in provenance" :key="item.key">
              <th scope="row">{{ item.label }}</th><td>{{ item.state }}</td>
              <td v-for="(value, index) in item.values" :key="index"><code>{{ value }}</code></td>
            </tr></tbody>
          </table>
        </div>
      </details>

      <h3>Common-case comparison · {{ matched.length }} matched</h3>
      <p class="note">Only identical case IDs are paired. Matching IDs alone do not establish identical specifications or fixtures; check the warnings above. Native ADK and KB source recall scores range from 0 to 1; unavailable or inapplicable scores are N/A.</p>
      <div v-if="matched.length" class="table-scroll" role="region" aria-label="Common-case comparison table" tabindex="0">
        <table>
          <caption>Same-case results, with run A and B shown separately</caption>
          <thead><tr>
            <th scope="col">Case ID / title</th><th scope="col">Status A</th><th scope="col">Status B</th>
            <th scope="col">Native A</th><th scope="col">Native B</th><th scope="col">KB recall A</th><th scope="col">KB recall B</th>
          </tr></thead>
          <tbody><tr v-for="pair in matched" :key="pair.id">
            <th scope="row"><code>{{ pair.id }}</code><span class="case-title">{{ text(pair.a.title) }}</span>
              <span v-if="pair.a.title !== pair.b.title" class="case-title">B title: {{ text(pair.b.title) }} (title differs)</span>
            </th>
            <td><span class="case-badge" :class="statusClass(pair.a.status)">{{ text(pair.a.status) }}</span></td>
            <td><span class="case-badge" :class="statusClass(pair.b.status)">{{ text(pair.b.status) }}</span></td>
            <td>{{ display(nativeScore(pair.a)) }}</td><td>{{ display(nativeScore(pair.b)) }}</td>
            <td>{{ display(kbRecall(pair.a)) }}</td><td>{{ display(kbRecall(pair.b)) }}</td>
          </tr></tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.eval-comparison { color: var(--ink, #152d35); background: var(--surface, #fff); border: 1px solid var(--line, #e2e8ed); border-radius: 12px; padding: 24px; min-width: 0; }
h2 { font-size: 20px; margin: 0 0 8px; }
h3 { font-size: 15px; margin: 22px 0 10px; }
p, li { font-size: 13px; line-height: 1.65; }
header p, .note { color: var(--muted, #667a82); }
.selectors { display: flex; align-items: end; flex-wrap: wrap; gap: 14px; margin: 20px 0; }
label { flex: 1 1 230px; min-width: 0; font-size: 12px; font-weight: 600; }
label span { display: block; margin-bottom: 7px; }
select { display: block; width: 100%; min-height: 42px; padding: 9px; border: 1px solid var(--line, #e2e8ed); border-radius: 8px; color: var(--ink, #152d35); background: var(--surface, #fff); }
button { min-height: 42px; border-radius: 8px; padding: 9px 15px; font: inherit; cursor: pointer; }
.compare-button { color: #fff; background: var(--accent, #087f8c); border: 1px solid var(--accent, #087f8c); }
.compare-button:hover:not(:disabled) { background: #066b76; }
button:disabled { opacity: .55; cursor: not-allowed; }
:is(select, button, summary, .table-scroll):focus-visible { outline: 2px solid var(--accent, #087f8c); outline-offset: 3px; }
.badges { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0; }
.context-badge { padding: 6px 10px; border-radius: 7px; font-size: 12px; background: var(--accent-soft, #e6f5f4); color: var(--ink, #152d35); overflow-wrap: anywhere; }
.comparison-warning { padding: 18px; margin: 18px 0; border: 2px solid #b56b14; border-left-width: 6px; border-radius: 8px; background: #fff7e8; color: #70430c; }
.comparison-warning h3 { margin: 0 0 10px; font-size: 17px; }
.comparison-warning ul { margin: 0; padding-left: 20px; }
.comparison-warning p { margin-bottom: 0; }
.load-error { color: var(--danger, #c44242); font-weight: 600; }
.table-scroll { overflow-x: auto; max-width: 100%; margin: 12px 0; border: 1px solid var(--line, #e2e8ed); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 12px; text-align: left; }
caption { text-align: left; padding: 12px; color: var(--muted, #667a82); }
th, td { padding: 12px; border-top: 1px solid var(--line, #e2e8ed); vertical-align: top; overflow-wrap: anywhere; }
thead th { background: var(--bg, #f5f7fa); }
tbody th { font-weight: 600; min-width: 180px; }
td { min-width: 105px; font-variant-numeric: tabular-nums; }
.open-button { display: block; margin-top: 8px; background: transparent; color: var(--accent, #087f8c); border: 1px solid var(--accent, #087f8c); font-size: 12px; }
.provenance { margin-top: 20px; }
summary { cursor: pointer; font-size: 13px; color: var(--accent, #087f8c); padding: 8px 0; }
code { white-space: pre-wrap; overflow-wrap: anywhere; }
.provenance td { max-width: 280px; }
.case-title { display: block; margin-top: 5px; font-weight: 400; }
.case-badge { display: inline-block; padding: 4px 8px; border-radius: 6px; font-weight: 600; }
.passed { color: #176342; background: #e7f5ed; }
.failed { color: #a02727; background: #fdeaea; }
.active { color: #79500e; background: #fff2d4; }
.skipped { color: #52616a; background: #edf0f2; }
@media (max-width: 640px) { .eval-comparison { padding: 16px; } .selectors { align-items: stretch; } .selectors label { flex-basis: 100%; } }
</style>
