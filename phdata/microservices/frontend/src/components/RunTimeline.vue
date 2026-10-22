<script>
const terminalTools = new Set(['user_input', 'complete_task', 'escalate_to_l2'])
const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {}
const ordered = events => [...(Array.isArray(events) ? events : [])]
  .map((event, index) => ({ ...event, order: index }))
  .sort((a, b) => (Number.isFinite(a.sequence) ? a.sequence : a.order) - (Number.isFinite(b.sequence) ? b.sequence : b.order))
const resultOf = event => object(object(event.data).result ?? event.data)
const citationId = value => typeof value === 'string' ? value : object(value).citation || ''

function savedMatches(events) {
  return ordered(events).flatMap(event => {
    if (event.type !== 'observation') return []
    const result = resultOf(event)
    if ((event.data?.tool || result.tool) !== 'search_knowledge_base') return []
    return Array.isArray(result.data?.matches) ? result.data.matches.filter(match => match && typeof match === 'object') : []
  })
}

function anchor(value) {
  const match = typeof value === 'string' && value.match(/^(.*?)#L(\d+)(?:-L?(\d+))?$/)
  return match ? { source: match[1], start: Number(match[2]), end: Number(match[3] || match[2]) } : null
}

export function citationReferences(events = [], content = '') {
  const ids = []
  for (const event of ordered(events)) {
    const result = resultOf(event)
    if (event.type !== 'done' && !(event.type === 'observation' && terminalTools.has(event.data?.tool || result.tool))) continue
    for (const values of [result.citations, result.data?.citations]) {
      if (Array.isArray(values)) ids.push(...values.map(citationId))
    }
  }
  const sources = String(content || '').match(/Sources\s*:[\s\S]*/i)?.[0] || ''
  ids.push(...(sources.match(/[^\s;,()[\]<>"'`]+#L\d+(?:-L?\d+)?/g) || []))
  const matches = savedMatches(events)
  return [...new Set(ids.filter(id => typeof id === 'string' && id.trim()))].map(id => {
    const requested = anchor(id)
    const match = matches.find(item => item.citation === id) || matches.find(item => {
      const saved = anchor(item.citation)
      return requested && saved && requested.source === saved.source && requested.start >= saved.start && requested.end <= saved.end
    })
    return match ? {
      ...match, citation: id,
      parentCitation: match.citation !== id ? match.citation : match.parent_citation,
    } : { citation: id, source: requested?.source || id.split('#')[0] }
  })
}

export function terminalAnswer(events = []) {
  for (const event of ordered(events).reverse()) {
    const result = resultOf(event)
    if (event.type !== 'done' && !(event.type === 'observation' && terminalTools.has(event.data?.tool || result.tool))) continue
    if (result.error) continue
    if (typeof result.message === 'string' && result.message.trim()) return result.message
    const parts = [result.guidance, result.question].filter(value => typeof value === 'string' && value.trim())
    if (parts.length) return parts.join('\n\n')
  }
  return ''
}
</script>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  events: { type: Array, default: () => [] },
  streaming: { type: Boolean, default: false },
})
const emit = defineEmits(['cite'])
const labels = {
  get_store_context: 'Read store context', search_ticket_history: 'Search ticket history',
  lookup_sla: 'Check service level', lookup_system_version: 'Check system version',
  search_knowledge_base: 'Search knowledge base', user_input: 'Request your input',
  complete_task: 'Complete incident', escalate_to_l2: 'Prepare L2 handoff',
}
const steps = computed(() => {
  const items = []
  const calls = new Map()
  for (const event of ordered(props.events)) {
    const data = object(event.data)
    if (event.type === 'thought') {
      if (typeof data.status === 'string' && data.status.trim()) items.push({ key: `status-${event.id ?? event.order}`, status: data.status })
      continue
    }
    if (event.type === 'error') {
      items.push({ key: `error-${event.id ?? event.order}`, status: typeof data.message === 'string' ? data.message : 'Run failed.', failed: true })
      continue
    }
    if (!['action', 'action_input', 'observation'].includes(event.type)) continue
    const key = data.call_id || `event-${event.id ?? event.order}`
    let step = calls.get(key)
    if (!step) {
      step = { key, tool: data.tool || '', hasArgs: false, hasResult: false, references: [] }
      calls.set(key, step)
      items.push(step)
    }
    step.tool ||= data.tool || ''
    if (event.type === 'action_input') {
      step.args = data.arguments ?? data.action_input ?? {}
      step.hasArgs = true
    }
    if (event.type === 'observation') {
      step.result = resultOf(event)
      step.tool ||= step.result.tool || ''
      step.hasResult = true
      step.failed = Boolean(step.result.error)
      if (step.tool === 'search_knowledge_base' && Array.isArray(step.result.data?.matches)) {
        step.references = step.result.data.matches.filter(match => typeof match?.citation === 'string')
      }
    }
  }
  return items
})
const pretty = value => {
  try { return JSON.stringify(value, null, 2) ?? 'No data saved.' } catch { return 'Data could not be displayed.' }
}
const label = tool => labels[tool] || String(tool || 'Tool step').replaceAll('_', ' ')
</script>

<template>
  <section class="run-timeline" aria-label="Run activity" :aria-busy="streaming">
    <p v-if="!steps.length" class="empty">{{ streaming ? 'Waiting for run activity…' : 'No run activity saved.' }}</p>
    <ol v-else>
      <li v-for="step in steps" :key="step.key" :class="{ failed: step.failed }">
        <span class="step-dot" aria-hidden="true"></span>
        <p v-if="step.status" class="public-status">{{ step.status }}</p>
        <template v-else>
          <div class="step-heading">
            <strong>{{ label(step.tool) }}</strong>
            <span class="tag" :class="{ failure: step.failed }">{{ step.failed ? 'Failed' : step.hasResult ? 'Finished' : streaming ? 'In progress' : 'Incomplete' }}</span>
          </div>
          <div v-if="step.references.length" class="references" aria-label="Retrieved sources">
            <button v-for="(reference, index) in step.references" :key="`${reference.citation}-${index}`" type="button" :aria-label="`View source ${reference.citation}`" @click="emit('cite', { ...reference })">
              {{ reference.title || reference.source || 'Knowledge source' }} · {{ reference.citation }}
            </button>
          </div>
          <details v-if="step.hasArgs">
            <summary>Arguments</summary>
            <pre>{{ pretty(step.args) }}</pre>
          </details>
          <details v-if="step.hasResult">
            <summary>{{ terminalTools.has(step.tool) ? 'Raw terminal result' : 'Result' }}</summary>
            <pre>{{ pretty(step.result) }}</pre>
          </details>
        </template>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.run-timeline { --accent: #087f8c; --ink: #152d35; color: var(--ink); font-size: .875rem; }
ol { list-style: none; margin: 0; padding: 0 0 0 7px; }
li { position: relative; border-left: 1px solid #e2e8ed; padding: 0 0 20px 20px; min-width: 0; }
li:last-child { padding-bottom: 0; border-left-color: transparent; }
.step-dot { position: absolute; top: 6px; left: -4px; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
.failed .step-dot { background: #b42318; }
.public-status, .empty { color: #667a82; margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.step-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.tag { border-radius: 20px; padding: 2px 8px; background: #eef5f6; font-size: .75rem; color: #42636c; }
.failure { color: #a32920; background: #fff0ee; }
details { margin-top: 10px; }
summary { cursor: pointer; color: #667a82; width: fit-content; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 320px; overflow: auto; padding: 12px; border-radius: 8px; background: #f5f7fa; font-size: .75rem; line-height: 1.6; }
.references { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
button { font: inherit; font-size: .75rem; color: var(--accent); border: 1px solid #cbe1e4; background: white; border-radius: 7px; padding: 6px 9px; text-align: left; overflow-wrap: anywhere; cursor: pointer; }
button:hover { background: #eff8f9; }
button:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
</style>
