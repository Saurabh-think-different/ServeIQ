<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import type { ApiClient, IncidentContext, IncidentResult, SearchRequest, SearchResponse } from '../api/types';

const props = defineProps<{ api: ApiClient; ticketId: string }>();
const emit = defineEmits<{ back: []; 'open-incident': [ticketId: string] }>();
const detail = ref<IncidentResult | null>(null);
const context = ref<IncidentContext | null>(null);
const detailLoading = ref(false);
const contextLoading = ref(false);
const detailError = ref('');
const contextError = ref('');
const query = ref('');
const queryEdited = ref(false);
const useCategory = ref(false);
const useModel = ref(false);
const useVersion = ref(false);
const searchLoading = ref(false);
const searchError = ref('');
const searchResult = ref<SearchResponse | null>(null);
const searchedQuery = ref('');
const incident = computed(() => detail.value?.incident ?? context.value?.incident);
let alive = true;
let navigation = 0;
let detailRequest = 0;
let contextRequest = 0;
let searchRequest = 0;

function scrollToSection(id: string): void {
  document.getElementById(id)?.scrollIntoView({ block: 'start' });
}

function errorText(error: unknown, area: string): string {
  const status = typeof error === 'object' && error !== null && 'status' in error ? error.status : undefined;
  if (status === 503) return `${area} unavailable (503). The service or knowledge index is not ready. Retry when available.`;
  if (status === 409) return `${area} incompatible (409). The knowledge index requires an operator update before retrying.`;
  return `${area} could not be loaded. ${error instanceof Error ? error.message : 'Please retry.'}`;
}

function prefill() {
  if (!queryEdited.value) query.value = (incident.value?.subject || incident.value?.description || '').slice(0, 2000);
}

async function loadDetail() {
  const epoch = navigation;
  const request = ++detailRequest;
  const ticket = props.ticketId;
  detailLoading.value = true;
  detailError.value = '';
  try {
    const result = await props.api.getIncident(ticket);
    if (!alive || epoch !== navigation || request !== detailRequest) return;
    detail.value = result;
    prefill();
  } catch (error) {
    if (alive && epoch === navigation && request === detailRequest) detailError.value = errorText(error, 'Ticket detail');
  } finally {
    if (alive && epoch === navigation && request === detailRequest) detailLoading.value = false;
  }
}

async function loadContext() {
  const epoch = navigation;
  const request = ++contextRequest;
  const ticket = props.ticketId;
  contextLoading.value = true;
  contextError.value = '';
  try {
    const result = await props.api.getContext(ticket);
    if (!alive || epoch !== navigation || request !== contextRequest) return;
    context.value = result;
    prefill();
  } catch (error) {
    if (alive && epoch === navigation && request === contextRequest) contextError.value = errorText(error, 'Verified context');
  } finally {
    if (alive && epoch === navigation && request === contextRequest) contextLoading.value = false;
  }
}

async function searchKnowledge() {
  const text = query.value.trim();
  if (!text || text.length > 2000 || searchLoading.value) return;
  const epoch = navigation;
  const request = ++searchRequest;
  const input: SearchRequest = { query: text, limit: 5 };
  if (useCategory.value && context.value?.incident.category) input.category = context.value.incident.category;
  if (useModel.value && context.value?.asset?.model) input.model = context.value.asset.model;
  if (useVersion.value && context.value?.asset?.system_version) input.system_version = context.value.asset.system_version;
  searchLoading.value = true;
  searchError.value = '';
  searchResult.value = null;
  searchedQuery.value = text;
  try {
    const result = await props.api.searchKnowledge(input);
    if (alive && epoch === navigation && request === searchRequest) searchResult.value = result;
  } catch (error) {
    if (alive && epoch === navigation && request === searchRequest) searchError.value = errorText(error, 'Knowledge retrieval');
  } finally {
    if (alive && epoch === navigation && request === searchRequest) searchLoading.value = false;
  }
}

watch(() => [props.ticketId, props.api] as const, () => {
  navigation++;
  detail.value = null;
  context.value = null;
  detailError.value = '';
  contextError.value = '';
  query.value = '';
  queryEdited.value = false;
  useCategory.value = false;
  useModel.value = false;
  useVersion.value = false;
  searchResult.value = null;
  searchError.value = '';
  searchLoading.value = false;
  searchedQuery.value = '';
  void loadDetail();
  void loadContext();
}, { immediate: true, flush: 'sync' });

onBeforeUnmount(() => { alive = false; navigation++; });
</script>

<template>
  <div class="incident-workspace stack">
    <nav class="breadcrumb inline" aria-label="Breadcrumb">
      <button class="button secondary small" type="button" @click="emit('back')">Incident queue</button>
      <span aria-hidden="true">/</span><span>{{ ticketId }}</span>
    </nav>

    <header class="page-header">
      <div>
        <p class="eyebrow">Incident workspace · {{ ticketId }}</p>
        <h1>{{ incident?.subject || 'Incident detail' }}</h1>
        <div v-if="incident" class="inline">
          <span class="badge">{{ incident.status }}</span>
          <span class="badge">Priority {{ incident.priority || 'not provided' }}</span>
          <span class="badge">{{ incident.category || 'Uncategorized' }}</span>
          <span v-if="context?.store" class="muted">{{ context.store.store_name }} · {{ context.store.store_id }}</span>
        </div>
      </div>
    </header>

    <nav class="toolbar inline section-nav" aria-label="Workspace sections">
      <a href="#incident-overview" @click.prevent="scrollToSection('incident-overview')">Overview</a>
      <a href="#incident-context" @click.prevent="scrollToSection('incident-context')">Context</a>
      <a href="#incident-knowledge" @click.prevent="scrollToSection('incident-knowledge')">Knowledge</a>
    </nav>

    <div v-if="detailLoading" role="status" class="muted">Loading ticket detail…</div>
    <div v-if="detailError" class="alert error" role="alert">
      <p>{{ detailError }}</p>
      <button type="button" class="button secondary small" :disabled="detailLoading" @click="loadDetail">Retry ticket detail</button>
    </div>
    <div v-for="(warning, index) in detail?.warnings || []" :key="`detail-${index}`" class="alert warning">{{ warning }}</div>

    <div class="workspace-grid">
      <div id="incident-overview" class="stack overview-column">
        <section class="card" aria-labelledby="original-heading">
          <div class="card-header"><h2 id="original-heading">Original ticket</h2><span class="badge">Reported, not verified</span></div>
          <p class="literal original-description">{{ incident?.description || 'No description available.' }}</p>
          <dl class="facts">
            <dt>Reported store</dt><dd>{{ incident?.reported_store_id || 'Not provided' }}</dd>
            <dt>Reported asset</dt><dd>{{ incident?.reported_asset_id || 'Not provided' }}</dd>
            <dt>Reported system version</dt><dd>{{ incident?.system_version || 'Not provided' }}</dd>
            <dt>Opened</dt><dd>{{ incident?.opened_at || 'Unknown' }}</dd>
            <dt>Subcategory</dt><dd>{{ incident?.subcategory || 'Not provided' }}</dd>
          </dl>
          <p class="muted">Intake identifiers and version text are claims, not proof of a matching asset or compatible runbook.</p>
        </section>

        <section class="card" aria-labelledby="sla-heading">
          <div class="card-header"><h2 id="sla-heading">SLA policy</h2><span class="badge">Fulfillment unknown</span></div>
          <template v-if="context?.sla">
            <p class="muted">Policy: {{ context.sla.priority }} · {{ context.sla.category }}</p>
            <dl class="facts">
              <dt>First response target</dt><dd>{{ context.sla.first_response_minutes }} minutes</dd>
              <dt>Resolution target</dt><dd>{{ context.sla.resolution_target_minutes }} minutes</dd>
              <dt>Escalation to L2 target</dt><dd>{{ context.sla.escalation_to_l2_minutes }} minutes</dd>
            </dl>
          </template>
          <p v-else class="muted">No matched SLA policy available.</p>
          <template v-if="context?.clock.open_target_deadlines">
            <h3>Open-ticket target deadlines</h3>
            <dl class="facts">
              <dt>First response</dt><dd>{{ context.clock.open_target_deadlines.first_response_at }}</dd>
              <dt>Resolution</dt><dd>{{ context.clock.open_target_deadlines.resolution_at }}</dd>
              <dt>Escalation to L2</dt><dd>{{ context.clock.open_target_deadlines.escalation_to_l2_at }}</dd>
            </dl>
          </template>
          <p v-else class="muted">Open-ticket target deadlines unavailable.</p>
          <p class="muted">{{ context?.clock.fulfillment_note || 'Response and resolution fulfillment cannot be verified from available records.' }}</p>
          <p class="muted">These are policy targets, not a breach assessment or countdown.</p>
        </section>
      </div>

      <div class="stack evidence-column">
        <section id="incident-context" class="card" aria-labelledby="context-heading">
          <div class="card-header"><h2 id="context-heading">Verified context</h2><span class="badge">Structured records</span></div>
          <p v-if="contextLoading" class="muted" role="status">Loading verified context…</p>
          <div v-if="contextError" class="alert error" role="alert">
            <p>{{ contextError }}</p>
            <button type="button" class="button secondary small" :disabled="contextLoading" @click="loadContext">Retry context</button>
          </div>
          <template v-if="context">
            <div v-for="(warning, index) in context.warnings" :key="`context-${index}`" class="alert warning">{{ warning }}</div>
            <div v-if="context.missing.length" class="alert warning"><strong>Missing context</strong><ul><li v-for="(item, index) in context.missing" :key="index">{{ item }}</li></ul></div>
            <div class="context-grid">
              <div>
                <h3>Store record</h3>
                <dl v-if="context.store" class="facts">
                  <dt>Store</dt><dd>{{ context.store.store_id }} · {{ context.store.store_name }}</dd>
                  <dt>Location</dt><dd>{{ context.store.city }} · {{ context.store.neighborhood }}</dd>
                  <dt>Type</dt><dd>{{ context.store.store_type }}</dd>
                </dl>
                <p v-else class="muted">No verified store record.</p>
              </div>
              <div>
                <h3>Asset record</h3>
                <dl v-if="context.asset" class="facts">
                  <dt>Asset</dt><dd>{{ context.asset.asset_id }}</dd>
                  <dt>Store</dt><dd>{{ context.asset.store_id }}</dd>
                  <dt>Type / make</dt><dd>{{ context.asset.asset_type }} · {{ context.asset.make }}</dd>
                  <dt>Model</dt><dd>{{ context.asset.model }}</dd>
                  <dt>Recorded version</dt><dd>{{ context.asset.system_version || 'Unknown' }}</dd>
                  <dt>Serial number</dt><dd>{{ context.asset.serial_number || 'Unknown' }}</dd>
                  <dt>Installed</dt><dd>{{ context.asset.install_date || 'Unknown' }}</dd>
                  <dt>Last serviced</dt><dd>{{ context.asset.last_service_date || 'Unknown' }}</dd>
                  <dt>Warranty expiry</dt><dd>{{ context.asset.warranty_expiry || 'Unknown' }}</dd>
                </dl>
                <p v-else class="muted">No verified asset record.</p>
              </div>
            </div>
            <p class="muted">Escalation flag: {{ context.incident.escalation_flag ? 'set' : 'not set' }}. This is a stored flag, not an executed action.</p>
            <h3>Related tickets</h3>
            <ul v-if="context.related.length" class="related-list">
              <li v-for="(related, index) in context.related" :key="index">
                <button v-if="related.related_ticket_id" type="button" class="button secondary small" @click="emit('open-incident', related.related_ticket_id)">{{ related.related_ticket_id }}</button>
                <span v-else>{{ related.reported_related_ticket_id }} — unresolved reference</span>
                <span class="muted"> · {{ related.relation_type }}</span>
              </li>
            </ul>
            <p v-else class="muted">No related ticket references.</p>
          </template>
        </section>

        <section id="incident-knowledge" class="card stack" aria-labelledby="knowledge-heading">
          <div class="card-header"><h2 id="knowledge-heading">Cited knowledge</h2><span class="badge">Manual retrieval</span></div>
          <p class="muted">Search source passages. Retrieval is not analysis, a resolution plan, or confirmation of applicability.</p>
          <form class="stack" @submit.prevent="searchKnowledge">
            <label class="field" for="incident-query">Knowledge query
              <textarea id="incident-query" v-model="query" rows="3" maxlength="2000" @input="queryEdited = true" />
            </label>
            <div class="filter-options">
              <label><input v-model="useCategory" type="checkbox" :disabled="!context?.incident.category" /> Use context category<span v-if="context?.incident.category">: {{ context.incident.category }}</span></label>
              <label><input v-model="useModel" type="checkbox" :disabled="!context?.asset?.model" /> Use asset model<span v-if="context?.asset?.model">: {{ context.asset.model }}</span></label>
              <label><input v-model="useVersion" type="checkbox" :disabled="!context?.asset?.system_version" /> I confirm the recorded asset version is applicable<span v-if="context?.asset?.system_version">: {{ context.asset.system_version }}</span></label>
            </div>
            <p class="muted">Filters are optional and off by default. Reported intake version text is never applied. Confirm asset version applicability before selecting its filter.</p>
            <div class="inline">
              <button type="submit" class="button primary" :disabled="!query.trim() || query.trim().length > 2000 || searchLoading">{{ searchLoading ? 'Searching…' : 'Search knowledge' }}</button>
              <span class="muted">{{ query.length }} / 2000 characters</span>
            </div>
          </form>
          <div v-if="searchLoading" role="status" class="muted">Retrieving source passages…</div>
          <div v-if="searchError" class="alert error" role="alert">
            <p>{{ searchError }}</p>
            <button type="button" class="button secondary small" :disabled="searchLoading || !query.trim()" @click="searchKnowledge">Retry knowledge search</button>
          </div>
          <template v-if="searchResult">
            <p class="muted">Results for: {{ searchedQuery }}</p>
            <div v-for="(warning, index) in searchResult.warnings" :key="`search-${index}`" class="alert warning">{{ warning }}</div>
            <p v-if="searchResult.evidence_gap" class="alert warning" role="status">Evidence gap: retrieved sources do not establish sufficient applicable evidence. Verify missing context; do not treat these results as an approved fix.</p>
            <p v-if="!searchResult.passages.length" class="empty-state">No source passages returned. Refine the query or check knowledge ingestion.</p>
            <article v-for="(passage, index) in searchResult.passages" :key="index" class="passage">
              <header class="passage-header"><h3>Source {{ index + 1 }}</h3><span class="badge">Similarity {{ passage.similarity_score }}</span></header>
              <p class="citation"><strong>{{ passage.source_path }}</strong> · {{ passage.section }}</p>
              <p class="literal passage-content">{{ passage.content }}</p>
              <p class="muted">Metadata: {{ passage.category || 'category unknown' }} · {{ passage.model || 'model unknown' }} · {{ passage.system_version || 'version unknown' }} · {{ passage.document_type || 'document type unknown' }}</p>
              <p><strong>Applicability: {{ passage.applicability === 'unknown' ? 'unknown' : 'context match (not an approval)' }}</strong></p>
              <ul v-if="passage.applicability_notes.length"><li v-for="(note, noteIndex) in passage.applicability_notes" :key="noteIndex">{{ note }}</li></ul>
              <p v-else class="muted">Applicability still requires operator verification.</p>
            </article>
          </template>
          <p v-else-if="!searchLoading && !searchError" class="empty-state">No search executed. Review the query and explicitly search when ready.</p>
        </section>
      </div>
    </div>

    <section class="card" aria-labelledby="history-heading">
      <div class="card-header"><h2 id="history-heading">Ticket history</h2><span class="badge">Source order</span></div>
      <p class="muted">Events remain in source order, not timestamp order. Source timestamps may be incomplete or inconsistent; they do not prove SLA fulfillment.</p>
      <p v-if="context" class="muted">Clock snapshot: {{ context.clock.now || 'unavailable' }} · status kind: {{ context.clock.status_kind }} · elapsed since open: {{ context.clock.elapsed_since_open_minutes == null ? 'unknown' : `${context.clock.elapsed_since_open_minutes} minutes (snapshot only)` }}.</p>
      <ol v-if="context?.history.length" class="history-list">
        <li v-for="event in context.history" :key="event.event_id"><span class="muted">Source index {{ event.source_event_index }} · {{ event.event_at }} · {{ event.actor }}</span><p class="literal">{{ event.note }}</p></li>
      </ol>
      <ol v-else-if="!context && detail?.incident.history.length" class="history-list">
        <li v-for="(event, index) in detail.incident.history" :key="index"><span class="muted">{{ event.timestamp }} · {{ event.actor }}</span><p class="literal">{{ event.note }}</p></li>
      </ol>
      <p v-else class="empty-state">No history available.</p>
    </section>
    <section class="card" aria-labelledby="analysis-heading">
      <h2 id="analysis-heading">Analysis &amp; approvals</h2>
      <p class="muted">Next backend stage; no analysis executed. No actions or approvals are available in this workspace.</p>
    </section>
  </div>
</template>

<style scoped>
.incident-workspace { min-width: 0; }
.breadcrumb { font-size: .85rem; }
.section-nav { gap: 1.5rem; }
.section-nav a { color: var(--accent, #0b6f64); text-underline-offset: .2em; }
.workspace-grid { display: grid; grid-template-columns: minmax(260px, .8fr) minmax(0, 1.8fr); gap: 1.25rem; align-items: start; }
.context-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1.25rem; }
h1 { overflow-wrap: anywhere; }
h2 { margin: 0; font-size: 1rem; }
h3 { font-size: .9rem; }
.facts { display: grid; grid-template-columns: minmax(90px, 1fr) minmax(0, 1.4fr); gap: .65rem 1rem; font-size: .88rem; }
.facts dt { color: var(--text-muted, #596271); }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.literal { white-space: pre-wrap; overflow-wrap: anywhere; }
.original-description { padding: 1rem; background: var(--surface-subtle, #f5f4f1); border-radius: .5rem; }
.filter-options { display: grid; gap: .6rem; font-size: .85rem; }
.filter-options label { overflow-wrap: anywhere; }
.filter-options input { margin-right: .4rem; }
.passage { border-top: 1px solid var(--border, #dedbd4); padding-top: 1rem; }
.passage-header { display: flex; align-items: center; justify-content: space-between; gap: .5rem; }
.passage-header h3 { margin: 0; }
.citation { font-size: .85rem; overflow-wrap: anywhere; }
.history-list { padding-left: 1.25rem; }
.history-list li { padding: .5rem 0; }
.history-list p { margin: .5rem 0; }
.related-list { list-style: none; padding-left: 0; }
.related-list li { margin: .6rem 0; }
@media (max-width: 1050px) { .workspace-grid { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .context-grid { grid-template-columns: 1fr; } .facts { grid-template-columns: 1fr; gap: .3rem; } .facts dd { margin-bottom: .5rem; } }
</style>
