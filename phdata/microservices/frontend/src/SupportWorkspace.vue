<script setup>
import { computed, ref, watch } from 'vue'
import LoginView from './components/LoginView.vue'
import IncidentSidebar from './components/IncidentSidebar.vue'
import IncidentForm from './components/IncidentForm.vue'
import ModelPicker from './components/ModelPicker.vue'
import StatusBadge from './components/StatusBadge.vue'
import ChatThread from './components/ChatThread.vue'
import CitationPanel from './components/CitationPanel.vue'
import MessageComposer from './components/MessageComposer.vue'
import { useSupport } from './composables/useSupport'

const {
  token, authBusy, authError, profile, bootstrapLoading,
  incidents, listLoading, hasMore, selectedIncident, messages, threadLoading, newIncident,
  models, pendingModel, pinnedModel, modelLocked, creating, sending, streaming, error, draft,
  liveStatus, login, logout, loadMore, selectIncident, startNew, cancelNew,
  createIncident, sendReply, retryRun, refreshThread,
} = useSupport()
const sidebarOpen = ref(false)
const citation = ref(null)
const busy = computed(() => creating.value || sending.value || streaming.value)
const completed = computed(() => ['resolved', 'escalated'].includes(selectedIncident.value?.status))
const displayModel = computed({
  get: () => pinnedModel.value || pendingModel.value,
  set: (value) => { if (!modelLocked.value && !busy.value) pendingModel.value = value },
})
const escalationId = computed(() => {
  for (const message of [...messages.value].reverse()) {
    for (const event of [...(message.events || [])].reverse()) {
      const id = event.data?.result?.escalation_id
      if (id) return id
    }
  }
  return ''
})
const canRetry = computed(() => Boolean(selectedIncident.value && !completed.value && !busy.value &&
  (selectedIncident.value.status === 'failed' || (error.value && messages.value.some((message) => message.role === 'user')))))

function select(id) {
  sidebarOpen.value = false
  citation.value = null
  selectIncident(id)
}
function openNew() {
  sidebarOpen.value = false
  citation.value = null
  startNew()
}
function signOut() {
  sidebarOpen.value = false
  citation.value = null
  logout()
}
function showCitation(value) {
  citation.value = typeof value === 'string' ? { citation: value } : value
}
function date(value) {
  if (!value) return 'Just now'
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : value.replace(' ', 'T') + 'Z'
  const parsed = new Date(normalized)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
watch(() => selectedIncident.value?.id, () => { citation.value = null })
watch(token, () => { citation.value = null })
</script>

<template>
  <LoginView v-if="!token" :busy="authBusy" :error="authError" @login="login" />
  <div v-else class="app-shell">
    <IncidentSidebar
      :incidents="incidents" :active-id="selectedIncident?.id || ''" :loading="listLoading"
      :has-more="hasMore" :profile="profile" :is-open="sidebarOpen"
      @select="select" @new-incident="openNew" @load-more="loadMore" @logout="signOut"
      @close="sidebarOpen = false"
    />
    <main class="workspace">
      <header class="workspace-bar">
        <div class="workspace-heading">
          <button class="icon-button mobile-menu" type="button" aria-label="Open incident navigation" @click="sidebarOpen = true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
          <span class="workspace-caption">WORKSPACE</span>
          <span class="heading-divider">/</span>
          <span class="workspace-name">IT support</span>
        </div>
        <span class="connection-label"><span /> Grounded in your knowledge base</span>
      </header>

      <div v-if="bootstrapLoading" class="workspace-state" role="status">
        <span class="loading-ring" /><h2>Opening your workspace</h2><p>Loading your stores and incident history…</p>
      </div>
      <div v-else-if="newIncident" class="form-region">
        <IncidentForm :busy="creating || sending || streaming" :error="error" :models="models"
          :default-model="pendingModel" @submit="createIncident" @cancel="cancelNew" />
      </div>
      <template v-else-if="selectedIncident">
        <section class="incident-heading">
          <div class="incident-title-row">
            <div class="incident-title-copy">
              <p class="eyebrow">INCIDENT · {{ selectedIncident.id.slice(0, 8).toUpperCase() }}</p>
              <h1>{{ selectedIncident.title }}</h1>
              <p class="incident-meta">Created {{ date(selectedIncident.created_at) }}</p>
            </div>
            <StatusBadge :status="selectedIncident.status" />
          </div>
          <div class="incident-toolbar">
            <ModelPicker v-model="displayModel" :models="models.allowed_models || []" :locked="modelLocked" :busy="busy || threadLoading" />
            <button class="subtle-button" type="button" :disabled="threadLoading || streaming" @click="refreshThread">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 6.1A8 8 0 0 1 19 9M5 15a8 8 0 0 0 12.9 2.9"/></svg>
              Refresh
            </button>
          </div>
          <details class="incident-details">
            <summary>Original incident details</summary>
            <div><span class="field-label">Issue description</span><p>{{ selectedIncident.description }}</p></div>
          </details>
          <div v-if="selectedIncident.status === 'escalated'" class="handoff-notice" role="status">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M12 3l9 16H3L12 3Z"/><path d="M12 9v4m0 3h.01"/></svg>
            <div><strong>{{ escalationId ? 'Simulated L2 handoff recorded' : 'Incident marked escalated' }}</strong>
              <p v-if="escalationId">No real L2 team has been contacted. Reference: <code>{{ escalationId }}</code></p>
              <p v-else>Review the conversation for the available escalation history.</p>
            </div>
          </div>
        </section>
        <div class="chat-region">
          <ChatThread :messages="messages" :incident="selectedIncident" :loading="threadLoading"
            :streaming="streaming" :live-status="liveStatus" @cite="showCitation" @retry="retryRun" />
        </div>
        <MessageComposer v-model:draft="draft" :disabled="completed || threadLoading || busy"
          :busy="busy" :status="selectedIncident.status" :error="error" :can-retry="canRetry"
          @send="sendReply" @retry="retryRun" />
      </template>
      <div v-else class="workspace-state welcome-state">
        <div class="welcome-mark" aria-hidden="true">S<span>IQ</span></div>
        <p class="eyebrow">WELCOME TO SERVEIQ</p>
        <h1>Store support, without the swivel chair.</h1>
        <p>Describe an issue, get grounded guidance, and keep every step in one conversation.</p>
        <button class="primary-button" type="button" @click="openNew">Create an incident <span aria-hidden="true">↗</span></button>
        <p class="welcome-footnote">Or select a previous incident from your history.</p>
        <p v-if="error" class="error-text" role="alert">{{ error }}</p>
      </div>
    </main>
    <CitationPanel :citation="citation" @close="citation = null" />
  </div>
</template>
