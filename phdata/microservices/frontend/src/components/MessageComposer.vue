<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  status: { type: String, default: '' },
  error: { type: String, default: '' },
  canRetry: { type: Boolean, default: false },
  draft: { type: String, default: '' },
})
const emit = defineEmits(['send', 'retry', 'update:draft'])
const composing = ref(false)
const closed = computed(() => ['resolved', 'escalated'].includes(props.status))
const running = computed(() => props.busy || ['running', 'streaming'].includes(props.status))
const locked = computed(() => props.disabled || running.value || closed.value)
const sendable = computed(() => !locked.value && props.draft.trim().length > 0 && props.draft.length <= 50000)
const statusText = computed(() => {
  if (props.status === 'resolved') return 'This incident is resolved. The conversation is read-only.'
  if (props.status === 'escalated') return 'This incident has been escalated. The conversation is read-only.'
  if (running.value) return 'ServeIQ is processing your request. Replies are paused until the run finishes.'
  if (props.status === 'waiting_for_user') return 'Waiting for your reply. Share the requested details or the outcome of the suggested steps.'
  if (props.status === 'failed') return 'The run failed. Retry the existing request without posting it again, or edit and send a new reply if enabled.'
  if (props.disabled) return 'Replies are currently unavailable.'
  return 'Add details to help resolve this incident.'
})
function send() {
  if (sendable.value && !composing.value) emit('send', props.draft.trim())
}
function onKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || composing.value || event.keyCode === 229) return
  event.preventDefault()
  if (!event.repeat) send()
}
function retry() {
  if (props.canRetry && !locked.value) emit('retry')
}
</script>

<template>
  <section class="message-composer" aria-label="Reply to incident">
    <p class="status" role="status" aria-live="polite">{{ statusText }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <form @submit.prevent="send">
      <label class="input-label">
        <span>{{ status === 'waiting_for_user' ? 'Your reply' : 'Message' }}</span>
        <textarea
          :value="draft"
          :disabled="locked"
          :readonly="closed"
          :placeholder="closed ? 'This conversation is read-only' : status === 'waiting_for_user' ? 'Reply with the requested information…' : 'Describe what happened or add more information…'"
          :aria-label="status === 'waiting_for_user' ? 'Your reply. Shift plus Enter for a new line.' : 'Message. Shift plus Enter for a new line.'"
          required
          maxlength="50000"
          rows="3"
          @input="emit('update:draft', $event.target.value)"
          @keydown="onKeydown"
          @compositionstart="composing = true"
          @compositionend="composing = false"
        ></textarea>
      </label>
      <div class="composer-footer">
        <p class="helper">{{ closed ? 'No further replies can be sent.' : 'Enter to send · Shift+Enter for a new line' }}</p>
        <div v-if="!closed" class="actions">
          <button v-if="canRetry" type="button" class="retry-button" :disabled="locked" @click="retry">Retry run</button>
          <button type="submit" class="send-button" :disabled="!sendable" :aria-label="running ? 'Run in progress' : 'Send reply'">
            <span>{{ running ? 'Processing…' : 'Send reply' }}</span>
            <svg v-if="!running" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m3 3 14 7-14 7 2-7-2-7Zm2 7h7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
          </button>
        </div>
      </div>
    </form>
  </section>
</template>

<style scoped>
.message-composer { --accent: #087f8c; --ink: #152d35; background: white; border-top: 1px solid #e2e8ed; padding: 18px 24px; color: var(--ink); }
.status { font-size: .8rem; line-height: 1.6; color: #667a82; margin: 0 0 12px; }
.error { background: #fff4f1; border: 1px solid #f0d1cb; color: #a32920; border-radius: 8px; padding: 10px 12px; font-size: .8rem; white-space: pre-wrap; overflow-wrap: anywhere; }
.input-label { display: block; }
.input-label > span { display: block; font-size: .78rem; font-weight: 600; margin-bottom: 7px; }
textarea { box-sizing: border-box; display: block; width: 100%; min-height: 94px; max-height: 260px; resize: vertical; padding: 12px 14px; border: 1px solid #d6e0e5; border-radius: 10px; background: white; color: var(--ink); font: inherit; font-size: .9rem; line-height: 1.6; }
textarea::placeholder { color: #667a82; }
textarea:disabled { color: #667a82; background: #f5f7fa; cursor: not-allowed; opacity: 1; }
.composer-footer { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-top: 10px; }
.helper { font-size: .72rem; color: #667a82; margin: 0; line-height: 1.5; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
button { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 40px; padding: 9px 15px; border-radius: 8px; font: inherit; font-size: .8rem; font-weight: 600; cursor: pointer; }
.send-button { border: 1px solid var(--accent); color: white; background: var(--accent); }
.send-button:hover:not(:disabled) { background: #066b76; }
.retry-button { border: 1px solid #d6e0e5; color: var(--accent); background: white; }
.retry-button:hover:not(:disabled) { background: #eff8f9; }
button:disabled { opacity: .5; cursor: not-allowed; }
svg { width: 17px; height: 17px; }
button:focus-visible, textarea:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media (max-width: 640px) { .message-composer { padding: 14px 16px; } .composer-footer { align-items: flex-start; } .actions { margin-left: auto; } }
</style>
