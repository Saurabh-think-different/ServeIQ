<script setup>
import { computed } from 'vue'

const props = defineProps({
  detail: { type: Object, default: null },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'cite'])
const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {}
const list = value => Array.isArray(value) ? value : value == null ? [] : [value]
const humanize = value => String(value ?? '').replaceAll('_', ' ')
const pretty = value => {
  try { return JSON.stringify(value, null, 2) ?? 'N/A' } catch { return 'Recorded data could not be displayed.' }
}
function display(value) {
  if (value == null) return 'N/A'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'object') return pretty(value)
  return String(value)
}
function readable(value) {
  if (value == null) return 'No details recorded.'
  if (Array.isArray(value)) return value.length ? value.map(readable).join('\n') : 'None'
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${humanize(key)}: ${readable(item)}`).join('\n') || 'None'
  return display(value)
}
const duration = value => typeof value === 'number' && Number.isFinite(value) && value >= 0
  ? value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(2)} s` : 'N/A'
const tone = value => {
  const status = String(value ?? '').toLowerCase()
  if (['passed', 'pass', 'success', 'completed'].includes(status)) return 'pass'
  if (['failed', 'fail', 'error'].includes(status)) return 'fail'
  return 'neutral'
}
const statusLabel = value => value == null || value === '' || value === 'not_applicable' ? 'N/A' : humanize(value)
const spec = computed(() => object(props.detail?.specification))
const actual = computed(() => object(props.detail?.actual))
const assessment = computed(() => object(props.detail?.assessment))
const native = computed(() => object(assessment.value.native_evaluator))
const metrics = computed(() => Object.entries(object(assessment.value.metrics)).map(([name, value]) => ({ name, ...object(value) })))
const invariants = computed(() => Object.entries(object(actual.value.invariants)).map(([name, value]) => ({ name, ...object(value) })))
const outcome = computed(() => assessment.value.passed === true ? 'passed' : assessment.value.passed === false ? 'failed' : null)
const metricDetails = metric => Object.fromEntries(Object.entries(metric).filter(([key]) => !['name', 'value', 'expected', 'status', 'notes'].includes(key)))
const nativeDetails = computed(() => Object.fromEntries(Object.entries(native.value).filter(([key]) => !['criterion', 'score', 'status', 'evaluator'].includes(key))))
const scopeNote = computed(() => {
  const metadata = object(spec.value.metadata)
  return metadata.policy_override ?? metadata.policy_override_note
})
const policyOverrides = computed(() => object(spec.value.metadata).policy_overrides)

function reference(value) {
  if (typeof value === 'string') return value
  const item = object(value)
  return item.citation || item.source || item.document || item.path || ''
}
function normalize(value) {
  return String(reference(value)).trim().replace(/#L\d+(?:-L?\d+)?$/i, '').replaceAll('\\', '/').replace(/^\.\//, '').replace(/^(?:kb\/)+/, '')
}
const callsOf = turn => list(object(turn).tool_calls).map(object)
function matchesOf(turn) {
  return callsOf(turn).filter(call => call.name === 'search_knowledge_base')
    .flatMap(call => list(object(object(call.response).data).matches).filter(item => item && typeof item === 'object'))
}
function referencesOf(turn) {
  const values = [...list(turn.citations), ...list(turn.retrieved_sources), ...list(turn.retrieved_citations), ...matchesOf(turn)]
  const seen = new Set()
  return values.filter(value => {
    const id = reference(value)
    if (!id || seen.has(id)) return false
    seen.add(id)
    return true
  })
}
function lineRange(value) {
  const match = String(value).match(/^(.*?)#L(\d+)(?:-L?(\d+))?$/i)
  return match ? { source: normalize(match[1]), start: Number(match[2]), end: Number(match[3] || match[2]) } : null
}
function cite(value, turn) {
  const id = reference(value)
  const requested = lineRange(id)
  const matches = matchesOf(turn)
  const match = matches.find(item => reference(item) === id) || matches.find(item => {
    const saved = lineRange(item.citation)
    return requested && saved && requested.source === saved.source && requested.start >= saved.start && requested.end <= saved.end
  }) || (!requested ? matches.find(item => normalize(item) === normalize(value)) : null)
  if (match) {
    emit('cite', {
      ...match, citation: id,
      parentCitation: match.citation && match.citation !== id ? match.citation : match.parent_citation,
    })
  } else {
    emit('cite', { citation: id, source: String(id).split('#')[0] })
  }
}
const turns = computed(() => {
  const recorded = list(actual.value.turns)
  const specified = list(spec.value.turns)
  const statuses = list(spec.value.expected_statuses)
  return Array.from({ length: Math.max(recorded.length, specified.length, statuses.length) }, (_, index) => {
    const turn = object(recorded[index])
    const expected = statuses[index]
    const final = turn.final_status
    const query = typeof specified[index] === 'string' ? specified[index] : object(specified[index]).user_input
    return {
      index, turn, recorded: recorded[index] != null, expected, final,
      query: turn.user_input ?? query, calls: callsOf(turn), references: referencesOf(turn),
      comparison: expected == null ? 'No expected status' : final == null ? 'Not recorded' : expected === final ? 'Match' : 'Mismatch',
    }
  })
})
const expectedSources = computed(() => [...new Set(list(spec.value.expected_kb_docs).map(normalize).filter(Boolean))])
const actualSources = computed(() => [...new Set(turns.value.flatMap(row => row.references.map(normalize)).filter(Boolean))])
const sourceComparison = computed(() => ({
  missing: expectedSources.value.filter(source => !actualSources.value.includes(source)),
  matched: expectedSources.value.filter(source => actualSources.value.includes(source)),
  additional: actualSources.value.filter(source => !expectedSources.value.includes(source)),
}))
const errors = computed(() => [...new Set([props.detail?.error, actual.value.error, ...turns.value.map(row => row.turn.error)].filter(value => value != null && value !== '').map(readable))])
const failureReasons = computed(() => {
  const reasons = list(assessment.value.failures).map(value => {
    if (typeof value !== 'string') return readable(value)
    const separator = value.indexOf(':')
    return separator > 0 ? `${humanize(value.slice(0, separator))}: ${value.slice(separator + 1).trim()}` : value
  })
  for (const row of turns.value) {
    if (row.comparison === 'Mismatch') reasons.push(`Turn ${row.index + 1} should end as ${statusLabel(row.expected)}, but ended as ${statusLabel(row.final)}.`)
  }
  if (sourceComparison.value.missing.length) reasons.push(`Expected KB documents were not found in recorded sources: ${sourceComparison.value.missing.join(', ')}.`)
  for (const metric of metrics.value.filter(metric => tone(metric.status) === 'fail')) {
    if (!reasons.some(reason => reason.toLowerCase().startsWith(humanize(metric.name).toLowerCase()))) reasons.push(`${humanize(metric.name)}: ${metric.notes ? readable(metric.notes) : 'The recorded value did not meet the expected criterion.'}`)
  }
  for (const invariant of invariants.value.filter(item => item.passed === false)) reasons.push(`${humanize(invariant.name)}: ${readable(invariant.details)}`)
  if (tone(native.value.status) === 'fail' && !reasons.some(reason => reason.startsWith('native evaluator:'))) reasons.push('Native ADK trajectory evaluation did not meet its configured criterion. See the separate score below.')
  return [...new Set(reasons.filter(Boolean))]
})
</script>

<template>
  <article class="eval-detail" :aria-busy="loading" aria-label="Evaluation case detail">
    <header class="case-header">
      <div class="title-copy">
        <p class="eyebrow">EVALUATION CASE</p>
        <h2>{{ loading ? 'Loading case…' : detail?.title || detail?.case_id || 'Case details' }}</h2>
        <p v-if="detail && !loading" class="muted identifiers">Case {{ detail.case_id ?? detail.id ?? 'N/A' }} · Run {{ detail.run_id ?? 'N/A' }}</p>
      </div>
      <button type="button" class="close-button" aria-label="Close evaluation case details" @click="emit('close')">Close ×</button>
    </header>
    <p v-if="loading" class="empty" role="status">Loading recorded evaluation evidence…</p>
    <p v-else-if="!detail" class="empty">Select a case to inspect its results and recorded evidence.</p>
    <div v-else class="case-content">
      <div class="summary-grid">
        <div><span class="label">Run status</span><strong class="badge" :class="tone(detail.status)">{{ statusLabel(detail.status) }}</strong></div>
        <div><span class="label">Assessment</span><strong class="badge" :class="tone(outcome)">{{ outcome ? statusLabel(outcome) : 'Not assessed' }}</strong></div>
        <div><span class="label">Duration</span><strong>{{ duration(detail.duration_ms ?? actual.duration_ms) }}</strong></div>
        <div><span class="label">Recorded turns</span><strong>{{ turns.filter(row => row.recorded).length }} / {{ turns.length }}</strong></div>
      </div>

      <section class="section" aria-label="Result explanation">
        <h3>What happened</h3>
        <ul v-if="failureReasons.length" class="reason-list"><li v-for="(reason, index) in failureReasons" :key="index">{{ reason }}</li></ul>
        <p v-else class="muted">{{ outcome === 'passed' ? 'All assessed criteria passed. This is not a guarantee of overall answer quality.' : outcome === 'failed' ? 'The assessment failed, but no specific failure reason was recorded.' : 'No assessment result or failure reason has been recorded.' }}</p>
        <div v-for="(error, index) in errors" :key="index" class="error-box"><strong>Execution error</strong><pre>{{ error }}</pre></div>
      </section>

      <section class="section scope-note">
        <h3>Evaluation scope</h3>
        <p>These checks measure recorded routing, tools, evidence, and backend invariants. They do not establish holdout safety, predict model behavior on other cases, or replace a full answer-quality review.</p>
        <template v-if="scopeNote != null"><h4>Fixture policy override</h4><pre class="prose">{{ readable(scopeNote) }}</pre><p class="muted">This note explains the fixture's intended policy expectations; it is not proof of a runtime policy bypass.</p></template>
        <details v-if="policyOverrides != null"><summary>Structured policy overrides</summary><pre>{{ pretty(policyOverrides) }}</pre></details>
      </section>

      <section class="section">
        <h3>Expected versus recorded KB sources</h3>
        <p class="muted">Comparison removes leading kb/ prefixes and #L line anchors only; it does not validate citation lines. Recorded sources include turn references and saved KB matches.</p>
        <div class="source-grid">
          <div><h4>Expected by this case</h4><ul v-if="expectedSources.length" class="source-list"><li v-for="source in expectedSources" :key="source" :class="sourceComparison.missing.includes(source) ? 'missing-source' : 'matched-source'"><span>{{ source }}</span><span class="badge" :class="sourceComparison.missing.includes(source) ? 'fail' : 'pass'">{{ sourceComparison.missing.includes(source) ? 'Missing' : 'Found' }}</span></li></ul><p v-else class="muted">No expected documents specified; recall is N/A.</p></div>
          <div><h4>Recorded in this run</h4><ul v-if="actualSources.length" class="source-list"><li v-for="source in actualSources" :key="source"><span>{{ source }}</span><span class="badge neutral">{{ expectedSources.includes(source) ? 'Expected' : 'Not in expected set' }}</span></li></ul><p v-else class="muted">No sources recorded.</p></div>
        </div>
        <p v-if="sourceComparison.missing.length || sourceComparison.additional.length" class="callout">A different runbook can still contain relevant guidance. Missing an expected document fails a strict source criterion when required; an additional or alternative source alone is not evidence of hallucination. Use the metric result below for the backend's retrieval-only verdict.</p>
      </section>

      <section class="section">
        <h3>Conversation and recorded tool trace</h3>
        <p class="muted">Calls appear in their recorded order. No timing or unrecorded actions are inferred.</p>
        <p v-if="!turns.length" class="empty">No turns saved.</p>
        <section v-for="row in turns" :key="`${detail.id ?? detail.case_id}-${row.index}`" class="turn-card" :aria-label="`Turn ${row.index + 1}`">
          <header class="turn-header"><h4>Turn {{ row.index + 1 }}</h4><span class="muted">{{ duration(row.turn.duration_ms) }}</span></header>
          <div class="routing"><span>Expected: <strong>{{ statusLabel(row.expected) }}</strong></span><span>Actual: <strong>{{ statusLabel(row.final) }}</strong></span><span class="badge" :class="row.comparison === 'Match' ? 'pass' : row.comparison === 'Mismatch' ? 'fail' : 'neutral'">{{ row.comparison }}</span></div>
          <h5>User query{{ row.recorded ? '' : ' (specification only)' }}</h5><pre class="message">{{ row.query == null ? 'No user query recorded.' : display(row.query) }}</pre>
          <h5>Final assistant message</h5><pre class="message">{{ row.turn.final_message == null ? 'No final assistant message recorded.' : display(row.turn.final_message) }}</pre>
          <div v-if="row.turn.error" class="error-box"><strong>Turn error</strong><pre>{{ readable(row.turn.error) }}</pre></div>
          <h5>Tool calls · {{ row.calls.length }}</h5>
          <p v-if="!row.calls.length" class="muted">{{ row.recorded ? 'No tool calls recorded.' : 'This turn was not recorded.' }}</p>
          <ol v-else class="trace">
            <li v-for="(call, index) in row.calls" :key="index">
              <div class="tool-heading"><span class="step-number">{{ index + 1 }}</span><strong>{{ call.name || 'Unnamed tool' }}</strong></div>
              <details><summary>Arguments</summary><pre>{{ pretty(call.args) }}</pre></details>
              <details><summary>Observation / tool response{{ call.response == null ? ' (not recorded)' : '' }}</summary><pre>{{ pretty(call.response) }}</pre></details>
            </li>
          </ol>
          <div v-if="row.references.length" class="references"><h5>Saved source references</h5><button v-for="(item, index) in row.references" :key="index" type="button" @click="cite(item, row.turn)">{{ reference(item) }}</button><p class="muted">Opens saved evidence from this turn's KB tool response, when available. Otherwise only the reference is shown.</p></div>
        </section>
      </section>

      <section class="section">
        <h3>Per-metric assessment</h3>
        <p v-if="!metrics.length" class="muted">No metrics recorded.</p>
        <div v-else class="table-scroll" role="region" aria-label="Evaluation metric results" tabindex="0">
          <table><thead><tr><th scope="col">Metric</th><th scope="col">Value</th><th scope="col">Expected</th><th scope="col">Status</th><th scope="col">Notes and details</th></tr></thead>
            <tbody><tr v-for="metric in metrics" :key="metric.name"><th scope="row">{{ humanize(metric.name) }}</th><td><pre class="cell-value">{{ display(metric.value) }}</pre></td><td><pre class="cell-value">{{ display(metric.expected) }}</pre></td><td><span class="badge" :class="tone(metric.status)">{{ statusLabel(metric.status) }}</span></td><td><p class="metric-notes">{{ metric.notes == null ? 'No notes recorded.' : readable(metric.notes) }}</p><details v-if="Object.keys(metricDetails(metric)).length"><summary>Matched, missing, and other details</summary><pre>{{ readable(metricDetails(metric)) }}</pre></details></td></tr></tbody>
          </table>
        </div>
      </section>

      <section class="section">
        <h3>Native ADK trajectory evaluator</h3>
        <p class="callout">This score measures the configured tool-trajectory criterion, not full answer quality, correctness, grounding, or safety.</p>
        <p v-if="!Object.keys(native).length" class="muted">No native evaluator result recorded.</p>
        <template v-else>
          <div class="native-result"><strong>Score: {{ display(native.score) }}</strong><span class="badge" :class="tone(native.status)">{{ statusLabel(native.status) }}</span></div>
          <dl><dt>Evaluator</dt><dd>{{ display(native.evaluator) }}</dd><dt>Configured criterion</dt><dd><pre class="prose">{{ readable(native.criterion) }}</pre></dd></dl>
          <details v-if="Object.keys(nativeDetails).length"><summary>Additional evaluator details</summary><pre>{{ readable(nativeDetails) }}</pre></details>
        </template>
      </section>

      <section class="section">
        <h3>Backend invariants</h3>
        <p v-if="!invariants.length" class="muted">No backend invariant results recorded.</p>
        <ul v-else class="invariant-list"><li v-for="item in invariants" :key="item.name"><div class="invariant-heading"><strong>{{ humanize(item.name) }}</strong><span class="badge" :class="item.passed === true ? 'pass' : item.passed === false ? 'fail' : 'neutral'">{{ item.passed === true ? 'Passed' : item.passed === false ? 'Failed' : 'N/A' }}</span></div><pre class="prose">{{ readable(item.details) }}</pre></li></ul>
      </section>

      <section class="section technical"><h3>Technical record</h3><details :key="`spec-${detail.id ?? detail.case_id}`"><summary>Raw specification JSON</summary><pre>{{ pretty(detail.specification) }}</pre></details><details :key="`actual-${detail.id ?? detail.case_id}`"><summary>Raw actual JSON</summary><pre>{{ pretty(detail.actual) }}</pre></details></section>
    </div>
  </article>
</template>

<style scoped>
.eval-detail { color: var(--ink, #152d35); background: var(--surface, #fff); border: 1px solid var(--line, #e2e8ed); border-radius: 14px; min-width: 0; max-width: 100%; font-size: .875rem; line-height: 1.6; overflow-wrap: anywhere; }
.case-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; padding: 24px; border-bottom: 1px solid var(--line, #e2e8ed); }
.title-copy { min-width: 0; }
h2, h3, h4, h5, p { margin: 0; }
h2 { font-size: 1.3rem; line-height: 1.4; }
h3 { font-size: 1rem; margin-bottom: 12px; }
h4 { font-size: .875rem; margin-bottom: 10px; }
h5 { font-size: .8rem; margin: 18px 0 8px; }
p + p { margin-top: 10px; }
.eyebrow { color: var(--accent, #087f8c); font-size: .65rem; letter-spacing: .14em; font-weight: 700; margin-bottom: 8px; }
.muted, .label, dt { color: var(--muted, #667a82); }
.identifiers { font-size: .75rem; margin-top: 8px; }
.close-button { flex-shrink: 0; border: 1px solid var(--line, #e2e8ed); border-radius: 8px; background: var(--surface, #fff); color: var(--ink, #152d35); padding: 7px 12px; }
button { cursor: pointer; font: inherit; }
button:hover { color: var(--accent, #087f8c); background: #edf7f7; }
button:focus-visible, summary:focus-visible, .table-scroll:focus-visible { outline: 2px solid var(--accent, #087f8c); outline-offset: 3px; }
.case-content { padding: 24px; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.summary-grid > div { border: 1px solid var(--line, #e2e8ed); border-radius: 9px; padding: 14px; }
.label { display: block; font-size: .73rem; margin-bottom: 8px; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 16px; font-size: .72rem; font-weight: 600; white-space: normal; }
.pass { color: #086654; background: #e6f5ef; }
.fail { color: #9d302e; background: #fff0ef; }
.neutral { color: var(--muted, #667a82); background: #f0f4f6; }
.section { margin-top: 26px; padding-top: 22px; border-top: 1px solid var(--line, #e2e8ed); }
.reason-list { margin: 0; padding-left: 20px; }
.reason-list li + li { margin-top: 8px; }
.scope-note, .callout { background: #f0f8f8; border: 1px solid #cde5e5; padding: 16px; border-radius: 9px; }
.scope-note h4 { margin-top: 16px; }
.callout { margin-top: 14px; font-size: .8rem; }
.error-box { background: #fff5f4; border-left: 3px solid #b4433d; padding: 12px; margin-top: 14px; }
pre { font: .78rem/1.7 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; max-height: 320px; overflow: auto; background: #f5f7fa; border-radius: 7px; padding: 12px; margin: 8px 0 0; }
.prose, .message { font: inherit; white-space: pre-wrap; }
.prose { background: transparent; padding: 0; }
.source-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; margin-top: 18px; }
.source-list, .invariant-list { list-style: none; margin: 0; padding: 0; }
.source-list li { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; border-bottom: 1px solid var(--line, #e2e8ed); padding: 10px; }
.source-list li > span:first-child { flex: 1; min-width: 100px; }
.missing-source { background: #fff6f4; border-left: 3px solid #b4433d; }
.turn-card { border: 1px solid var(--line, #e2e8ed); border-radius: 10px; padding: 18px; margin-top: 18px; min-width: 0; }
.turn-header, .routing, .tool-heading, .native-result, .invariant-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.turn-header { justify-content: space-between; }
.turn-header h4 { margin: 0; }
.routing { font-size: .78rem; margin-top: 12px; }
.message { background: #f6f9fa; border: 1px solid var(--line, #e2e8ed); }
.trace { list-style: none; padding: 0 0 0 10px; margin: 12px 0 0; }
.trace > li { border-left: 2px solid var(--line, #e2e8ed); padding: 0 0 20px 18px; }
.trace > li:last-child { padding-bottom: 0; }
.step-number { background: #e6f5f4; color: var(--accent, #087f8c); border-radius: 50%; min-width: 24px; text-align: center; font-size: .75rem; }
details { margin-top: 10px; min-width: 0; }
summary { cursor: pointer; color: var(--accent, #087f8c); padding: 4px 0; font-size: .8rem; }
.references button { display: block; max-width: 100%; margin: 6px 0; text-align: left; color: var(--accent, #087f8c); border: 1px solid var(--line, #e2e8ed); background: transparent; padding: 6px 10px; border-radius: 6px; overflow-wrap: anywhere; }
.references .muted { font-size: .75rem; margin-top: 10px; }
.table-scroll { overflow-x: auto; max-width: 100%; }
table { width: 100%; border-collapse: collapse; font-size: .78rem; text-align: left; }
th, td { border-bottom: 1px solid var(--line, #e2e8ed); padding: 12px 10px; vertical-align: top; min-width: 100px; }
thead th { color: var(--muted, #667a82); background: #f5f8f9; }
tbody th { min-width: 140px; font-weight: 600; }
td:last-child { min-width: 240px; }
.cell-value { margin: 0; padding: 0; background: transparent; max-height: 180px; min-width: 80px; }
.metric-notes { white-space: pre-wrap; }
.native-result { margin-top: 14px; }
dl { margin: 14px 0 0; }
dt { font-size: .75rem; margin-top: 10px; }
dd { margin: 4px 0 0; }
.invariant-list li { padding: 14px 0; border-bottom: 1px solid var(--line, #e2e8ed); }
.empty { color: var(--muted, #667a82); padding: 24px; }
@media (max-width: 800px) { .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .source-grid { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 480px) { .case-header, .case-content { padding: 16px; } .case-header { gap: 10px; } h2 { font-size: 1.1rem; } .turn-card { padding: 12px; } .summary-grid { gap: 10px; } .routing { align-items: flex-start; flex-direction: column; gap: 6px; } }
</style>
