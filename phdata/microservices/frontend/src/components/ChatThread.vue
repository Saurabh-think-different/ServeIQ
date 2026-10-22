<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import RunTimeline, { citationReferences, terminalAnswer } from './RunTimeline.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  messages: { type: Array, default: () => [] },
  incident: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  streaming: { type: Boolean, default: false },
  liveStatus: { type: String, default: '' },
})
const emit = defineEmits(['cite', 'retry'])
const scroller = ref(null)
const nearBottom = ref(true)
const rows = computed(() => props.messages.map((message, index) => {
  const events = Array.isArray(message.events) ? message.events : []
  const content = typeof message.content === 'string' && message.content.trim() ? message.content : terminalAnswer(events)
  return {
    ...message, events, answer: content,
    key: message.id ?? `message-${index}`,
    historical: message.role === 'assistant' && !message.model && !events.length,
    citations: message.role === 'assistant' ? citationReferences(events, content) : [],
    active: props.streaming && message.role === 'assistant' && index === props.messages.length - 1,
  }
}))
const hasActiveAssistant = computed(() => rows.value.at(-1)?.active)
const processingStatus = computed(() => {
  if (props.liveStatus.trim()) return props.liveStatus
  const events = rows.value.at(-1)?.role === 'assistant' ? rows.value.at(-1).events : []
  const status = [...events].reverse().find(event => event.type === 'thought' && typeof event.data?.status === 'string')?.data.status
  return status || 'Processing your incident…'
})
const formatTime = value => {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
function trackScroll() {
  const element = scroller.value
  if (element) nearBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 96
}
async function followUpdates() {
  const follow = nearBottom.value
  await nextTick()
  if (follow && nearBottom.value && scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}
watch(() => [props.messages, props.streaming, props.loading, props.liveStatus], followUpdates, { deep: true })
watch(() => props.incident?.id, () => { nearBottom.value = true; followUpdates() })
onMounted(followUpdates)
</script>

<template>
  <section ref="scroller" class="chat-thread" aria-label="Incident conversation" tabindex="0" :aria-busy="loading" @scroll.passive="trackScroll">
    <header class="thread-heading">
      <div>
        <p class="eyebrow">SERVEIQ ASSISTANT</p>
        <h2>{{ incident?.title || 'Support conversation' }}</h2>
      </div>
      <StatusBadge v-if="incident?.status" :status="incident.status" />
    </header>

    <div v-if="loading && !messages.length" class="loading-card" role="status">
      <span>Loading conversation…</span>
      <div class="skeleton" aria-hidden="true"></div>
      <div class="skeleton short" aria-hidden="true"></div>
    </div>
    <p v-else-if="!messages.length && !streaming" class="empty">Your incident conversation will appear here.</p>

    <ol class="messages">
      <li v-for="message in rows" :key="message.key" :class="['message', message.role === 'user' ? 'user-message' : 'assistant-message']">
        <article :aria-label="message.role === 'user' ? 'Your message' : 'Assistant response'">
          <header class="message-meta">
            <strong>{{ message.role === 'user' ? 'You' : 'ServeIQ' }}</strong>
            <span v-if="message.role === 'assistant' && message.model" class="chip">{{ message.model }}</span>
            <span v-if="message.historical" class="chip historical">Historical</span>
            <time v-if="formatTime(message.created_at)" :datetime="message.created_at">{{ formatTime(message.created_at) }}</time>
          </header>
          <p v-if="message.role === 'user'" class="message-text">{{ message.content }}</p>
          <template v-else>
            <div v-if="message.answer" class="answer">{{ message.answer }}</div>
            <p v-else-if="!message.active" class="no-answer">No final answer saved for this run.</p>
            <div v-if="message.citations.length" class="citations" aria-label="Answer sources">
              <span class="sources-label">Sources</span>
              <button v-for="reference in message.citations" :key="reference.citation" type="button" :aria-label="`View citation ${reference.citation}`" @click="emit('cite', reference)">
                <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M6 2.5h6l3 3v12H5v-15h1Zm6 0v4h3M8 10h4M8 13h4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
                {{ reference.citation }}
              </button>
            </div>
            <div v-if="message.active" class="processing" role="status" aria-live="polite">
              <span class="processing-dot" aria-hidden="true"></span>{{ processingStatus }}
              <div v-if="!message.answer" class="skeleton" aria-hidden="true"></div>
              <div v-if="!message.answer" class="skeleton short" aria-hidden="true"></div>
            </div>
            <details v-if="message.events.length" class="run-details" @toggle="followUpdates">
              <summary>Run activity <span>{{ message.events.length }} events</span></summary>
              <RunTimeline :events="message.events" :streaming="message.active" @cite="emit('cite', $event)" />
            </details>
          </template>
        </article>
      </li>
    </ol>

    <div v-if="streaming && !hasActiveAssistant" class="loading-card processing" role="status" aria-live="polite">
      <span class="processing-dot" aria-hidden="true"></span>{{ processingStatus }}
      <div class="skeleton" aria-hidden="true"></div>
      <div class="skeleton short" aria-hidden="true"></div>
    </div>
    <div v-if="incident?.status === 'failed' && !streaming" class="run-error" role="status">
      <span>The run could not finish. Retry the existing request or add more information below.</span>
      <button type="button" :disabled="loading" @click="emit('retry')">Retry run</button>
    </div>
  </section>
</template>

<style scoped>
.chat-thread { --accent: #087f8c; --ink: #152d35; box-sizing: border-box; min-height: 0; height: 100%; overflow-y: auto; overflow-anchor: none; padding: 28px; color: var(--ink); background: #f5f7fa; }
.thread-heading { display: flex; gap: 16px; align-items: center; justify-content: space-between; margin-bottom: 30px; }
.eyebrow { color: var(--accent); font-size: .68rem; font-weight: 700; letter-spacing: .12em; margin: 0 0 7px; }
h2 { font-size: 1.2rem; margin: 0; overflow-wrap: anywhere; }
.messages { display: flex; flex-direction: column; gap: 24px; padding: 0; margin: 0; list-style: none; }
.message { min-width: 0; }
.message article { border: 1px solid #e2e8ed; border-radius: 14px; padding: 20px; }
.user-message { align-self: flex-end; max-width: 85%; }
.user-message article { background: #eaf4f5; border-color: #d3e7e9; border-bottom-right-radius: 4px; }
.assistant-message { width: 100%; }
.assistant-message article { background: white; box-shadow: 0 2px 7px #152d3504; }
.message-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; font-size: .8rem; }
.message-meta strong { font-size: .85rem; }
time { color: #667a82; font-size: .72rem; margin-left: auto; }
.chip { font-size: .68rem; background: #edf6f6; color: var(--accent); padding: 3px 8px; border-radius: 20px; overflow-wrap: anywhere; }
.historical { color: #667a82; background: #f1f4f6; }
.message-text, .answer { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; line-height: 1.75; }
.answer { font-size: .96rem; }
.message-text { font-size: .9rem; }
.empty, .no-answer { color: #667a82; font-size: .875rem; }
.citations { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin-top: 20px; }
.sources-label { width: 100%; color: #667a82; font-size: .72rem; font-weight: 600; }
button { display: inline-flex; gap: 5px; align-items: center; background: white; color: var(--accent); border: 1px solid #cbe1e4; border-radius: 7px; padding: 7px 10px; font: inherit; font-size: .75rem; cursor: pointer; text-align: left; overflow-wrap: anywhere; }
button:hover { background: #eff8f9; }
button:disabled { opacity: .5; cursor: not-allowed; }
svg { width: 16px; height: 16px; flex-shrink: 0; }
.run-details { border-top: 1px solid #e2e8ed; padding-top: 14px; margin-top: 20px; }
summary { cursor: pointer; color: #667a82; font-size: .78rem; padding: 4px 0; }
summary span { font-size: .7rem; margin-left: 8px; }
.run-details[open] summary { margin-bottom: 16px; }
.processing { color: #667a82; font-size: .85rem; margin-top: 16px; overflow-wrap: anywhere; white-space: pre-wrap; }
.processing-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); margin-right: 7px; }
.loading-card { background: white; border: 1px solid #e2e8ed; padding: 20px; border-radius: 14px; margin-bottom: 20px; color: #667a82; }
.skeleton { height: 10px; width: 85%; background: #edf1f4; border-radius: 5px; margin-top: 14px; }
.short { width: 57%; }
.run-error { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 22px; border: 1px solid #f0d1cb; background: #fff7f5; border-radius: 10px; padding: 14px; color: #923e30; font-size: .85rem; }
button:focus-visible, summary:focus-visible, .chat-thread:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media (max-width: 640px) { .chat-thread { padding: 16px; } .thread-heading { align-items: flex-start; } .message article { padding: 15px; } .user-message { max-width: 94%; } time { width: 100%; margin-left: 0; } }
</style>
