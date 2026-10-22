<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({ citation: { type: Object, default: null } })
const emit = defineEmits(['close'])
const panel = ref(null)
const closeButton = ref(null)
let returnFocus = null
let mounted = false
const heading = computed(() => {
  const value = props.citation?.heading
  return Array.isArray(value) ? value.join(' › ') : typeof value === 'string' ? value : ''
})
const excerpt = computed(() => {
  const value = props.citation?.excerpt ?? props.citation?.content
  return typeof value === 'string' && value.trim() ? value : ''
})
const source = computed(() => props.citation?.source || String(props.citation?.citation || '').split('#')[0])
const warnings = value => Array.isArray(value) ? value : value ? [value] : []
const display = value => {
  if (typeof value === 'string') return value
  try { return JSON.stringify(value, null, 2) ?? '' } catch { return 'Metadata unavailable.' }
}
function restoreFocus() {
  if (returnFocus?.isConnected && typeof returnFocus.focus === 'function') returnFocus.focus()
  returnFocus = null
}
async function openPanel() {
  if (!mounted) return
  returnFocus ||= document.activeElement
  await nextTick()
  if (props.citation) closeButton.value?.focus()
}
function handleKey(event) {
  if (!props.citation) return
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    emit('close')
    return
  }
  if (event.key !== 'Tab' || !panel.value) return
  const elements = [...panel.value.querySelectorAll('button:not(:disabled), [href], input:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]')]
  const first = elements[0]
  const last = elements.at(-1)
  if (!first) { event.preventDefault(); panel.value.focus(); return }
  if (!panel.value.contains(document.activeElement) || (!event.shiftKey && document.activeElement === last) || (event.shiftKey && document.activeElement === first)) {
    event.preventDefault()
    ;(event.shiftKey ? last : first).focus()
  }
}
watch(() => props.citation, (value, previous) => {
  if (value && !previous) openPanel()
  else if (!value) restoreFocus()
}, { flush: 'post' })
onMounted(() => {
  mounted = true
  document.addEventListener('keydown', handleKey, true)
  if (props.citation) openPanel()
})
onBeforeUnmount(() => {
  mounted = false
  document.removeEventListener('keydown', handleKey, true)
  restoreFocus()
})
</script>

<template>
  <Teleport to="body">
    <div v-if="citation" class="citation-backdrop" @click.self="emit('close')">
      <aside ref="panel" class="citation-panel" role="dialog" aria-modal="true" aria-label="Knowledge source citation" tabindex="-1">
        <header class="panel-header">
          <div><p class="eyebrow">KNOWLEDGE SOURCE</p><h2>{{ citation.title || 'Citation details' }}</h2></div>
          <button ref="closeButton" type="button" class="close-button" aria-label="Close citation details" @click="emit('close')">
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
          </button>
        </header>
        <div class="panel-content">
          <h3 v-if="heading">{{ heading }}</h3>
          <dl class="metadata">
            <template v-if="source"><dt>Source</dt><dd>{{ source }}</dd></template>
            <template v-if="citation.citation"><dt>Line reference</dt><dd>{{ citation.citation }}</dd></template>
            <template v-if="citation.parentCitation || citation.parent_citation"><dt>Parent reference</dt><dd>{{ citation.parentCitation || citation.parent_citation }}</dd></template>
            <template v-if="citation.score != null"><dt>Retrieval score</dt><dd>{{ citation.score }}</dd></template>
          </dl>
          <section class="excerpt-section" aria-label="Saved source excerpt">
            <h3>Saved excerpt</h3>
            <p v-if="citation.parentCitation || citation.parent_citation" class="context-note">The saved excerpt includes the parent section for this line reference.</p>
            <pre v-if="excerpt" class="excerpt">{{ excerpt }}</pre>
            <p v-else class="no-excerpt">No excerpt saved for this citation</p>
          </section>
          <section v-if="warnings(citation.warnings).length" class="warnings" aria-label="Document warnings">
            <h3>Document warnings</h3>
            <ul><li v-for="(warning, index) in warnings(citation.warnings)" :key="index"><pre>{{ display(warning) }}</pre></li></ul>
          </section>
          <section v-if="warnings(citation.section_warnings).length" class="warnings" aria-label="Section warnings">
            <h3>Section warnings</h3>
            <ul><li v-for="(warning, index) in warnings(citation.section_warnings)" :key="index"><pre>{{ display(warning) }}</pre></li></ul>
          </section>
          <p class="footnote">Saved evidence only. No external source is fetched.</p>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.citation-backdrop { --accent: #087f8c; --ink: #152d35; position: fixed; inset: 0; z-index: 1000; display: flex; justify-content: flex-end; background: #10252e66; color: var(--ink); }
.citation-panel { display: flex; flex-direction: column; width: min(540px, 100%); height: 100%; max-height: 100dvh; background: white; box-shadow: -8px 0 32px #152d351a; box-sizing: border-box; }
.panel-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; padding: 25px; border-bottom: 1px solid #e2e8ed; }
.eyebrow { color: var(--accent); font-size: .68rem; font-weight: 700; letter-spacing: .12em; margin: 0 0 8px; }
h2 { font-size: 1.15rem; margin: 0; overflow-wrap: anywhere; }
h3 { font-size: .9rem; margin: 0 0 12px; overflow-wrap: anywhere; }
.close-button { display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px; flex-shrink: 0; border: 1px solid #e2e8ed; border-radius: 8px; background: white; color: #667a82; cursor: pointer; }
.close-button:hover { color: var(--accent); background: #f0f8f9; }
svg { width: 20px; height: 20px; }
.panel-content { padding: 25px; overflow-y: auto; overscroll-behavior: contain; }
.metadata { font-size: .8rem; margin: 0 0 28px; }
dt { color: #667a82; margin-top: 12px; }
dd { margin: 4px 0 0; overflow-wrap: anywhere; white-space: pre-wrap; line-height: 1.6; }
.excerpt { font: inherit; font-size: .875rem; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; background: #f5f7fa; padding: 16px; border: 1px solid #e2e8ed; border-radius: 10px; }
.context-note, .no-excerpt, .footnote { color: #667a82; font-size: .8rem; line-height: 1.6; }
.no-excerpt { padding: 20px; border: 1px dashed #e2e8ed; border-radius: 10px; }
.warnings { margin-top: 20px; border: 1px solid #eedcb2; background: #fffaf0; padding: 14px; border-radius: 10px; }
.warnings h3 { color: #785315; }
.warnings ul { padding-left: 18px; margin: 0; }
.warnings pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: .78rem; line-height: 1.6; margin: 8px 0; }
.footnote { margin-top: 24px; }
button:focus-visible, .citation-panel:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media (max-width: 640px) { .panel-header, .panel-content { padding: 18px; } }
</style>
