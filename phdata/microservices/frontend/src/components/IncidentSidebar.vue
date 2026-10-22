<script setup>
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  incidents: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
  loading: Boolean,
  hasMore: Boolean,
  profile: { type: Object, default: () => ({}) },
  isOpen: Boolean,
})
const emit = defineEmits(['select', 'new-incident', 'load-more', 'logout', 'close'])
const id = useId()
const query = ref('')
const sidebar = ref(null)
const closeButton = ref(null)
const mobile = ref(false)
const visibleIncidents = computed(() => props.incidents.filter(incident => String(incident.title || '').toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
const profileName = computed(() => props.profile?.display_name || props.profile?.name || 'Store manager')
const storeCount = computed(() => props.profile?.store_count ?? props.profile?.stores?.length ?? 0)
const initials = computed(() => profileName.value.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toLocaleUpperCase() || 'SM')
let mediaQuery
let returnFocus

function parseDate(value) {
  if (!value) return null
  // Backend timestamps without an offset represent UTC, not browser-local time.
  const source = String(value).trim().replace(' ', 'T')
  const date = new Date(/^\d{4}-\d{2}-\d{2}T/.test(source) && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(source) ? `${source}Z` : source)
  return Number.isNaN(date.getTime()) ? null : date
}
function timestamp(incident) { return parseDate(incident.updated_at || incident.created_at) }
function friendlyTime(incident) {
  const date = timestamp(incident)
  if (!date) return 'Date unavailable'
  const now = new Date()
  if (date.toDateString() === now.toDateString()) return `Today, ${date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return date.toLocaleDateString([], { month: 'short', day: 'numeric', ...(date.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) })
}
function syncMedia() { mobile.value = mediaQuery.matches }
function handleKeydown(event) {
  if (!mobile.value || !props.isOpen) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
  }
  if (event.key !== 'Tab') return
  const focusable = [...sidebar.value.querySelectorAll('button:not(:disabled), input:not(:disabled), [tabindex="0"]')]
    .filter(element => element.getClientRects().length)
  const first = focusable[0]
  const last = focusable.at(-1)
  if (event.shiftKey && (document.activeElement === first || !sidebar.value.contains(document.activeElement))) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && (document.activeElement === last || !sidebar.value.contains(document.activeElement))) {
    event.preventDefault()
    first?.focus()
  }
}
watch(() => mobile.value && props.isOpen, (open, wasOpen) => {
  if (open) {
    returnFocus = document.activeElement
    closeButton.value?.focus()
  } else if (wasOpen && returnFocus?.isConnected) {
    returnFocus.focus()
    returnFocus = null
  }
}, { flush: 'post' })
onMounted(() => {
  mediaQuery = window.matchMedia('(max-width: 767px)')
  syncMedia()
  mediaQuery.addEventListener('change', syncMedia)
  document.addEventListener('keydown', handleKeydown)
})
onBeforeUnmount(() => {
  mediaQuery?.removeEventListener('change', syncMedia)
  document.removeEventListener('keydown', handleKeydown)
  if (mobile.value && props.isOpen && returnFocus?.isConnected) returnFocus.focus()
})
</script>

<template>
  <button v-if="isOpen" type="button" class="backdrop" aria-label="Close incident navigation" tabindex="-1" @click="emit('close')"></button>
  <aside ref="sidebar" class="incident-sidebar" :class="{ 'is-open': isOpen }" :role="mobile && isOpen ? 'dialog' : undefined" :aria-modal="mobile && isOpen ? true : undefined" :aria-hidden="mobile && !isOpen ? true : undefined" :inert="mobile && !isOpen" :aria-labelledby="`${id}-brand`">
    <header class="sidebar-header">
      <div class="brand-row">
        <div :id="`${id}-brand`" class="brand"><svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><rect width="32" height="32" rx="9" fill="currentColor"/><path d="M23 10H13a4 4 0 0 0 0 8h6a2 2 0 0 1 0 4H9" stroke="white" stroke-width="3" stroke-linecap="round"/></svg><span>Serve<span class="brand-accent">IQ</span></span></div>
        <button ref="closeButton" type="button" class="close-button icon-button" aria-label="Close incident navigation" @click="emit('close')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button>
      </div>
      <p class="tagline">Operations support</p>
      <button type="button" class="new-incident" @click="emit('new-incident')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>New incident</button>
      <label class="search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg><span class="sr-only">Filter loaded incidents by title</span><input v-model="query" type="search" placeholder="Search loaded incidents" /></label>
    </header>
    <div class="section-heading"><h2 :id="`${id}-incidents`">YOUR INCIDENTS</h2><span>{{ incidents.length }}</span></div>
    <nav class="incident-list" :aria-labelledby="`${id}-incidents`" :aria-busy="loading">
      <ul v-if="visibleIncidents.length">
        <li v-for="incident in visibleIncidents" :key="incident.id">
          <button type="button" class="incident-item" :class="{ active: incident.id === activeId }" :aria-current="incident.id === activeId ? 'page' : undefined" @click="emit('select', incident.id)">
            <span class="incident-title">{{ incident.title || 'Untitled incident' }}</span>
            <span class="incident-meta"><StatusBadge :status="incident.status" /><time v-if="timestamp(incident)" :datetime="timestamp(incident).toISOString()" :title="timestamp(incident).toLocaleString()">{{ friendlyTime(incident) }}</time><span v-else class="missing-time">Date unavailable</span></span>
          </button>
        </li>
      </ul>
      <div v-else-if="!loading" class="empty"><p>{{ query.trim() ? 'No matching incidents' : 'A fresh start' }}</p><span>{{ query.trim() ? 'Try another title. Search covers loaded incidents only.' : 'Create an incident when you need a hand.' }}</span></div>
      <p class="loading" role="status">{{ loading ? 'Loading incidents…' : '' }}</p>
      <button v-if="hasMore" type="button" class="load-more" :disabled="loading" @click="emit('load-more')">{{ loading ? 'Loading…' : 'Load more incidents' }}</button>
    </nav>
    <footer class="profile">
      <div class="profile-details"><div class="avatar" aria-hidden="true">{{ initials }}</div><div class="profile-text"><p class="profile-name">{{ profileName }}</p><p class="profile-email">{{ profile?.email || 'Manager account' }}</p></div></div>
      <div class="profile-bottom"><span>{{ storeCount }} {{ Number(storeCount) === 1 ? 'store' : 'stores' }}</span><button type="button" class="logout" @click="emit('logout')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M9 4H5v16h4m5-12 4 4-4 4m-5-4h12"/></svg>Sign out</button></div>
    </footer>
  </aside>
</template>

<style scoped>
.incident-sidebar {
  --bg: #f5f7fa; --surface: #fff; --ink: #152d35; --muted: #667a82; --line: #e2e8ed; --accent: #087f8c; --accent-soft: #e6f5f4; --danger: #c44242;
  display: flex; flex-direction: column; width: 300px; flex: 0 0 300px; height: 100vh; height: 100dvh; position: sticky; top: 0; box-sizing: border-box; background: var(--ink); color: #f0f6f7; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.incident-sidebar * { box-sizing: border-box; }
button, input { font: inherit; }
button { cursor: pointer; }
button:focus-visible, input:focus-visible { outline: 3px solid #72d4ca; outline-offset: 3px; }
button:disabled { cursor: not-allowed; opacity: .55; }
.sidebar-header { padding: 28px 20px 20px; }
.brand-row { display: flex; align-items: center; justify-content: space-between; }
.brand { display: flex; align-items: center; gap: 11px; font-size: 26px; font-weight: 750; letter-spacing: -.8px; }
.brand > svg { width: 34px; height: 34px; color: var(--accent); }
.brand-accent { color: #72d4ca; }
.tagline { margin: 8px 0 28px 45px; color: #b2c7cd; font-size: 11px; letter-spacing: .4px; }
.new-incident { display: flex; align-items: center; justify-content: center; gap: 9px; width: 100%; min-height: 46px; border: 1px solid #208f99; border-radius: 10px; background: var(--accent); color: white; font-size: 13px; font-weight: 650; }
.new-incident:hover { background: #09909e; }
.new-incident svg { width: 18px; height: 18px; }
.search { display: flex; align-items: center; gap: 8px; margin-top: 20px; padding-left: 11px; border: 1px solid #39515a; border-radius: 9px; background: #10262e; color: #abc0c6; }
.search svg { width: 16px; height: 16px; flex: 0 0 16px; }
.search input { width: 100%; min-width: 0; min-height: 44px; padding: 10px 8px 10px 0; border: 0; border-radius: 8px; background: transparent; color: white; font-size: 13px; }
.search input::placeholder { color: #abc0c6; }
.section-heading { display: flex; align-items: center; justify-content: space-between; padding: 0 23px 12px; color: #b2c7cd; }
h2 { margin: 0; font-size: 10px; font-weight: 650; letter-spacing: 1.5px; }
.section-heading > span { font-size: 11px; font-variant-numeric: tabular-nums; }
.incident-list { min-height: 0; flex: 1; overflow-y: auto; overscroll-behavior: contain; padding: 3px 12px 16px; scrollbar-width: thin; scrollbar-color: #53717c transparent; }
ul { margin: 0; padding: 0; list-style: none; }
li + li { margin-top: 6px; }
.incident-item { display: block; width: 100%; padding: 14px 11px; border: 1px solid transparent; border-radius: 10px; background: transparent; color: #e5eef0; text-align: left; }
.incident-item:hover { background: #213e47; }
.incident-item.active { border-color: #49757b; background: #23454e; box-shadow: inset 3px 0 #72d4ca; }
.incident-title { display: -webkit-box; overflow: hidden; -webkit-line-clamp: 2; -webkit-box-orient: vertical; font-size: 13px; font-weight: 550; line-height: 1.65; overflow-wrap: anywhere; }
.incident-meta { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.incident-meta > time, .incident-meta > .missing-time { color: #b7cbd0; font-size: 10px; line-height: 1.5; }
.empty { padding: 35px 16px; text-align: center; }
.empty p { margin: 0 0 10px; font-size: 14px; font-weight: 600; }
.empty span { color: #b2c7cd; font-size: 12px; line-height: 1.7; }
.loading { margin: 12px 0; color: #b2c7cd; text-align: center; font-size: 12px; }
.loading:empty { margin: 0; }
.load-more { width: calc(100% - 12px); min-height: 44px; margin: 8px 6px 0; border: 1px solid #39515a; border-radius: 9px; background: transparent; color: #d8ebed; font-size: 12px; }
.load-more:hover:not(:disabled) { background: #213e47; }
.profile { padding: 18px 20px 16px; border-top: 1px solid #344e57; background: #122930; }
.profile-details { display: flex; align-items: center; gap: 11px; }
.avatar { display: grid; place-items: center; width: 36px; height: 36px; flex: 0 0 36px; border-radius: 10px; background: #294b54; color: #b0e5df; font-size: 12px; font-weight: 650; }
.profile-text { min-width: 0; }
.profile-name { margin: 0; font-size: 12px; font-weight: 650; overflow-wrap: anywhere; }
.profile-email { margin: 5px 0 0; color: #b2c7cd; font-size: 11px; overflow-wrap: anywhere; }
.profile-bottom { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 12px; }
.profile-bottom > span { color: #b2c7cd; font-size: 11px; }
.logout { display: inline-flex; align-items: center; gap: 6px; min-height: 44px; padding: 8px 0 8px 8px; border: 0; border-radius: 6px; background: transparent; color: #d6e5e8; font-size: 11px; }
.logout:hover { color: white; }
.logout svg { width: 16px; height: 16px; }
.icon-button { display: grid; place-items: center; width: 44px; height: 44px; border: 1px solid #39515a; border-radius: 9px; background: transparent; color: white; }
.icon-button svg { width: 21px; height: 21px; }
.close-button, .backdrop { display: none; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
@media (max-width: 767px) {
  .incident-sidebar { position: fixed; inset: 0 auto 0 0; z-index: 101; width: min(300px, calc(100vw - 20px)); visibility: hidden; transform: translateX(-100%); transition: transform .2s ease, visibility .2s; }
  .incident-sidebar.is-open { visibility: visible; transform: translateX(0); }
  .close-button { display: grid; }
  .backdrop { display: block; position: fixed; inset: 0; z-index: 100; width: 100%; height: 100%; border: 0; background: #0d222d80; }
  .sidebar-header { padding-top: 18px; }
}
@media (prefers-reduced-motion: reduce) { .incident-sidebar { transition: none; } }
</style>
