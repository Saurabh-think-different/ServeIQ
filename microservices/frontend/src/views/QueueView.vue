<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import type { ApiClient, IncidentCreate, IncidentPage } from '../api/types';

const props = defineProps<{ api: ApiClient }>();
const emit = defineEmits<{ 'open-incident': [ticketId: string] }>();
const limit = 25;
const offset = ref(0);
const page = ref<IncidentPage | null>(null);
const loading = ref(true);
const error = ref('');
const filters = reactive({ category: '', status: '' });
let appliedFilters: { category?: string; status?: string } = {};
let requestSequence = 0;
let active = true;
const intakeOpen = ref(false);
const submitting = ref(false);
const intakeError = ref('');
const warnings = ref<string[]>([]);
const createdId = ref('');
const blankForm = () => ({ description: '', subject: '', store_id: '', asset_id: '', category: '', priority: '', system_version: '' });
const form = reactive(blankForm());
const range = computed(() => page.value && page.value.incidents.length
  ? `${offset.value + 1}–${offset.value + page.value.incidents.length} of ${page.value.total}`
  : `0 of ${page.value?.total ?? 0}`);

function message(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

async function load() {
  const sequence = ++requestSequence;
  loading.value = true;
  error.value = '';
  page.value = null;
  try {
    const result = await props.api.listIncidents({ limit, offset: offset.value, ...appliedFilters });
    if (active && sequence === requestSequence) page.value = result;
  } catch (reason) {
    if (active && sequence === requestSequence) error.value = message(reason, 'Unable to load incidents. Please try again.');
  } finally {
    if (active && sequence === requestSequence) loading.value = false;
  }
}

function applyFilters() {
  appliedFilters = {};
  if (filters.category.trim()) appliedFilters.category = filters.category.trim();
  if (filters.status.trim()) appliedFilters.status = filters.status.trim();
  offset.value = 0;
  void load();
}

function changePage(direction: number) {
  if (loading.value || !page.value) return;
  const nextOffset = offset.value + direction * limit;
  if (nextOffset < 0 || nextOffset >= page.value.total) return;
  offset.value = nextOffset;
  void load();
}

function openIntake() {
  intakeOpen.value = true;
  intakeError.value = '';
  warnings.value = [];
  createdId.value = '';
}

async function submitIncident() {
  if (submitting.value) return;
  intakeError.value = '';
  warnings.value = [];
  createdId.value = '';
  if (!form.description.trim()) {
    intakeError.value = 'Describe the issue before creating an incident.';
    return;
  }
  const input: IncidentCreate = { description: form.description.trim() };
  const optionalFields = ['subject', 'store_id', 'asset_id', 'category', 'priority', 'system_version'] as const;
  for (const key of optionalFields) {
    if (form[key].trim()) input[key] = form[key];
  }
  submitting.value = true;
  try {
    const result = await props.api.createIncident(input);
    if (!active) return;
    warnings.value = result.warnings;
    createdId.value = result.incident.ticket_id;
    Object.assign(form, blankForm());
    emit('open-incident', result.incident.ticket_id);
    void load();
  } catch (reason) {
    if (active) intakeError.value = message(reason, 'Unable to create the incident. Please try again.');
  } finally {
    if (active) submitting.value = false;
  }
}

function openedAt(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

onMounted(load);
onBeforeUnmount(() => { active = false; requestSequence += 1; });
</script>

<template>
  <section class="stack queue" aria-labelledby="queue-title">
    <header class="page-header queue-header">
      <div>
        <p class="eyebrow">Incident management</p>
        <h1 id="queue-title">Incident queue</h1>
        <p class="muted">Capture reported issues and review incidents. Creating an incident does not run automated triage.</p>
      </div>
      <button class="button primary" type="button" :aria-expanded="intakeOpen" aria-controls="incident-intake" @click="openIntake">New incident</button>
    </header>

    <section v-if="intakeOpen" id="incident-intake" class="card intake" aria-labelledby="intake-title">
      <div class="card-header">
        <div><p class="eyebrow">Manual intake</p><h2 id="intake-title">New incident</h2></div>
        <button class="button secondary small" type="button" :disabled="submitting" @click="intakeOpen = false">Close intake</button>
      </div>
      <p class="muted">Describe what happened. All fields other than description are optional. Store and asset IDs are kept as entered.</p>
      <div v-if="createdId" class="alert success" role="status">Incident {{ createdId }} created.</div>
      <div v-for="(warning, index) in warnings" :key="index" class="alert warning" role="status">{{ warning }}</div>
      <p v-if="intakeError" id="intake-error" class="alert error" role="alert">{{ intakeError }}</p>
      <form class="stack" :aria-busy="submitting" @submit.prevent="submitIncident">
        <label class="field" for="incident-description">Description (required)
          <textarea id="incident-description" v-model="form.description" required rows="4" :disabled="submitting" :aria-describedby="intakeError ? 'intake-error' : undefined" placeholder="What is happening, where, and what has already been tried?"></textarea>
        </label>
        <label class="field" for="incident-subject">Subject (optional)
          <input id="incident-subject" v-model="form.subject" :disabled="submitting" />
        </label>
        <div class="field-row intake-fields">
          <label class="field" for="incident-store">Reported store ID (optional)<input id="incident-store" v-model="form.store_id" :disabled="submitting" /></label>
          <label class="field" for="incident-asset">Reported asset ID (optional)<input id="incident-asset" v-model="form.asset_id" :disabled="submitting" /></label>
          <label class="field" for="incident-category">Category (optional)<input id="incident-category" v-model="form.category" :disabled="submitting" /></label>
          <label class="field" for="incident-priority">Priority (optional)<input id="incident-priority" v-model="form.priority" :disabled="submitting" /></label>
          <label class="field" for="incident-version">System version (optional)<input id="incident-version" v-model="form.system_version" :disabled="submitting" /></label>
        </div>
        <div class="inline"><button class="button primary" type="submit" :disabled="submitting">{{ submitting ? 'Creating…' : 'Create incident' }}</button><span class="muted">Saves the report only.</span></div>
      </form>
    </section>

    <section class="card" aria-label="Incident list">
      <div class="card-header"><h2>Reported incidents</h2><button class="button secondary small" type="button" @click="load">Refresh</button></div>
      <form class="toolbar queue-filters" aria-label="Filter incidents" @submit.prevent="applyFilters">
        <label class="field" for="filter-category">Category<input id="filter-category" v-model="filters.category" placeholder="Exact category" /></label>
        <label class="field" for="filter-status">Status<input id="filter-status" v-model="filters.status" placeholder="Exact status" /></label>
        <button class="button secondary" type="submit">Apply filters</button>
        <p class="muted filter-help">Match the exact category and status recorded on incidents.</p>
      </form>
      <div v-if="loading" class="empty-state" role="status">Loading incidents…</div>
      <div v-else-if="error" class="empty-state">
        <p class="alert error" role="alert">{{ error }}</p>
        <button class="button secondary" type="button" @click="load">Retry</button>
      </div>
      <template v-else-if="page">
        <div v-if="!page.incidents.length" class="empty-state">
          <h3>{{ page.total ? 'No incidents on this page' : 'No incidents found' }}</h3>
          <p class="muted">Create a new incident, adjust the filters, or import existing incidents through the Data screen.</p>
        </div>
        <div v-else class="table-wrap" tabindex="0" role="region" aria-label="Incident queue table">
          <table class="data-table">
            <caption class="table-caption">Reported incidents — select a subject to open its details</caption>
            <thead><tr><th scope="col">Subject / ID</th><th scope="col">Reported store</th><th scope="col">Category</th><th scope="col">Priority</th><th scope="col">Status</th><th scope="col">Opened</th></tr></thead>
            <tbody>
              <tr v-for="incident in page.incidents" :key="incident.ticket_id">
                <td><button class="incident-link" type="button" @click="emit('open-incident', incident.ticket_id)"><span>{{ incident.subject || 'Untitled incident' }}</span><span class="muted incident-id">{{ incident.ticket_id }}</span></button></td>
                <td>{{ incident.reported_store_id ?? 'Not reported' }}</td><td>{{ incident.category || 'Not set' }}</td><td>{{ incident.priority || 'Not set' }}</td><td><span class="badge">{{ incident.status }}</span></td><td><time :datetime="incident.opened_at">{{ openedAt(incident.opened_at) }}</time></td>
              </tr>
            </tbody>
          </table>
        </div>
        <nav class="pagination" aria-label="Incident pages">
          <p class="muted" role="status">{{ range }} incidents</p>
          <div class="inline"><button class="button secondary small" type="button" :disabled="offset === 0" @click="changePage(-1)">Previous</button><button class="button secondary small" type="button" :disabled="offset + limit >= page.total" @click="changePage(1)">Next</button></div>
        </nav>
      </template>
    </section>
  </section>
</template>

<style scoped>
.queue-header { align-items: flex-start; gap: 1.5rem; }
.queue-header .muted { max-width: 46rem; }
.intake { scroll-margin-top: 1rem; }
.intake-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.queue-filters { align-items: end; flex-wrap: wrap; }
.filter-help { flex-basis: 100%; margin: 0; }
.table-wrap { overflow-x: auto; }
.data-table { min-width: 860px; }
.table-caption { text-align: left; padding: 0.75rem 1rem; }
.incident-link { display: grid; gap: 0.35rem; text-align: left; border: 0; background: none; color: var(--teal, #176b64); cursor: pointer; padding: 0.25rem 0; font: inherit; }
.incident-link:hover > span:first-child { text-decoration: underline; }
.incident-link:focus-visible { outline: 2px solid currentColor; outline-offset: 4px; }
.incident-id { font-size: 0.85em; }
@media (max-width: 640px) {
  .queue-header { flex-direction: column; }
  .intake-fields { grid-template-columns: minmax(0, 1fr); }
  .queue-filters .field { width: 100%; }
}
</style>
