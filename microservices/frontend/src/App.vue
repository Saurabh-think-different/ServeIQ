<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef } from 'vue'
import { ApiError, createApiClient } from './api/client'
import QueueView from './views/QueueView.vue'
import IncidentView from './views/IncidentView.vue'
import DataView from './views/DataView.vue'

type Page = 'queue' | 'incident' | 'data' | 'about'
const api = shallowRef<ReturnType<typeof createApiClient> | null>(null)
const tokenInput = ref('')
const connecting = ref(false)
const error = ref('')
const page = ref<Page>('queue')
const incidentId = ref('')
let connectAttempt = 0
const titles: Record<Page, string> = { queue: 'Incident queue', incident: 'Incident workspace', data: 'Data & knowledge', about: 'About this POC' }
const title = computed(() => titles[page.value])

function disconnect(message = '') {
  connectAttempt++
  api.value?.disconnect()
  api.value = null
  tokenInput.value = ''
  connecting.value = false
  incidentId.value = ''
  page.value = 'queue'
  error.value = message
  history.replaceState(null, '', location.pathname + location.search)
}

async function connect() {
  if (!tokenInput.value.trim() || connecting.value) return
  const attempt = ++connectAttempt
  const candidate = createApiClient(tokenInput.value.trim(), () => {
    if (attempt === connectAttempt) disconnect('Your operator session ended. Reconnect to continue.')
  })
  tokenInput.value = ''
  connecting.value = true
  error.value = ''
  try {
    await candidate.listIncidents({ limit: 1, offset: 0 })
    if (attempt !== connectAttempt) { candidate.disconnect(); return }
    api.value = candidate
    navigateFromHash()
  } catch (cause) {
    candidate.disconnect()
    if (attempt === connectAttempt) error.value = cause instanceof ApiError ? cause.message : 'Could not connect. Check the API and operator token.'
  } finally {
    if (attempt === connectAttempt) connecting.value = false
  }
}

function navigateFromHash() {
  const hash = location.hash.slice(1)
  if (hash.startsWith('/incidents/')) {
    try { incidentId.value = decodeURIComponent(hash.slice('/incidents/'.length)); page.value = 'incident' } catch { page.value = 'queue' }
  } else page.value = hash === '/data' ? 'data' : hash === '/about' ? 'about' : 'queue'
}
function focusMain() { document.getElementById('main-content')?.focus() }
function navigate(destination: Page) {
  page.value = destination
  location.hash = destination === 'data' ? '/data' : destination === 'about' ? '/about' : '/incidents'
}
function openIncident(id: string) {
  incidentId.value = id
  page.value = 'incident'
  location.hash = `/incidents/${encodeURIComponent(id)}`
}
window.addEventListener('hashchange', navigateFromHash)
onBeforeUnmount(() => { window.removeEventListener('hashchange', navigateFromHash); api.value?.disconnect() })
</script>

<template>
  <div v-if="!api" class="access-layout">
    <section class="access-story" aria-label="About ServeIQ">
      <a class="brand access-brand" href="#"><span class="brand-mark">S<span>·</span></span>ServeIQ</a>
      <div><p class="eyebrow">SERVEWELL · IT SUPPORT</p><h1>Less searching.<br>More supporting.</h1><p class="story-copy">One workspace for incident context, source-backed knowledge, and clear next steps.</p>
        <div class="story-list"><span>01 <strong>Start with the facts</strong></span><span>02 <strong>Keep the evidence visible</strong></span><span>03 <strong>Make uncertainty explicit</strong></span></div>
      </div>
      <p class="story-footer">Local proof of concept · Synthetic source dataset</p>
    </section>
    <main class="access-main">
      <section class="access-card">
        <span class="badge">OPERATOR ACCESS</span><h2>Open your workspace</h2><p class="muted">Connect with your local operator token. This is a shared POC operator boundary, not an individual user account.</p>
        <form class="stack" @submit.prevent="connect">
          <label class="field">Operator token<input v-model="tokenInput" type="password" autocomplete="off" placeholder="Enter your operator token" required :disabled="connecting" spellcheck="false" /></label>
          <p v-if="error" class="alert error" role="alert">{{ error }}</p>
          <button class="button primary" :disabled="connecting || !tokenInput.trim()">{{ connecting ? 'Connecting…' : 'Connect to ServeIQ →' }}</button>
        </form>
        <div class="access-note"><strong>Memory-only access</strong><p>Your token is never saved in browser storage. Refreshing the page requires reconnecting. Model credentials stay on the server.</p></div>
        <p class="muted small-text">Requires the incident APIs from backend PR #18 or its merged equivalent. No credentials are bundled with this application.</p>
      </section>
    </main>
  </div>
  <div v-else class="app-layout">
    <a class="skip-link" href="#main-content" @click.prevent="focusMain">Skip to content</a>
    <aside class="sidebar">
      <a href="#/incidents" class="brand" @click="navigate('queue')"><span class="brand-mark">S<span>·</span></span>ServeIQ</a>
      <p class="sidebar-label">SUPPORT WORKSPACE</p>
      <nav aria-label="Main navigation">
        <a href="#/incidents" :class="{ active: page === 'queue' || page === 'incident' }" :aria-current="page === 'queue' || page === 'incident' ? 'page' : undefined" @click="navigate('queue')"><span aria-hidden="true">▤</span> Incident queue</a>
        <a href="#/data" :class="{ active: page === 'data' }" :aria-current="page === 'data' ? 'page' : undefined" @click="navigate('data')"><span aria-hidden="true">▧</span> Data & knowledge</a>
        <a href="#/about" :class="{ active: page === 'about' }" :aria-current="page === 'about' ? 'page' : undefined" @click="navigate('about')"><span aria-hidden="true">ⓘ</span> About this POC</a>
      </nav>
      <div class="sidebar-note"><span class="status-dot"></span><strong>Local POC</strong><p>Verified facts first.<br>No autonomous device actions.</p></div>
      <div class="operator"><span class="avatar">OP</span><div><strong>Local operator</strong><small>Shared access · not SSO</small></div></div>
      <button class="button secondary small" @click="disconnect()">Disconnect</button>
    </aside>
    <div class="main-layout">
      <header class="topbar"><span>Workspace <span class="breadcrumb-separator">/</span> <strong>{{ title }}</strong></span><span class="badge">SYNTHETIC DATA · POC</span></header>
      <main id="main-content" tabindex="-1" class="page-content">
        <QueueView v-if="page === 'queue'" :api="api" @open-incident="openIncident" />
        <IncidentView v-else-if="page === 'incident'" :key="incidentId" :api="api" :ticket-id="incidentId" @back="navigate('queue')" @open-incident="openIncident" />
        <DataView v-else-if="page === 'data'" :api="api" />
        <div v-else class="stack">
          <header class="page-header"><div><p class="eyebrow">SCOPE & BOUNDARIES</p><h1>Built to show its work.</h1><p class="muted">A small, honest foundation for the ServeWell support pilot.</p></div></header>
          <section class="card"><h2>How the pieces fit</h2><div class="architecture-flow"><span>Vue workspace</span><b>→</b><span>FastAPI</span><b>→</b><span>SQLite facts + Qdrant knowledge</span></div><p class="muted">Structured facts use exact lookups. Knowledge indexing and queries use the same OpenAI embedding space. A search result is evidence, not a generated diagnosis or an authorization to act.</p></section>
          <div class="about-grid"><section class="card"><h2>Available now</h2><ul class="feature-list"><li>Fresh incident intake and a persisted queue</li><li>Reported and verified store/asset context</li><li>History and explicit SLA target limitations</li><li>HTTP imports with independent stage reports</li><li>Cited knowledge search and applicability warnings</li></ul></section><section class="card"><h2>Intentionally not claimed</h2><ul class="feature-list"><li>No live device telemetry or real repairs</li><li>No fabricated confidence or ROI metrics</li><li>No agent analysis, approvals or audit UI yet</li><li>No semantic ticket-history search or hybrid ranking</li><li>No production identity, RBAC or tenant isolation</li></ul></section></div>
          <p class="alert warning">Model-backed requests require server-side credentials and approved content. Missing evidence is a reason to ask or hand off—not to invent instructions.</p>
        </div>
      </main>
    </div>
  </div>
</template>
