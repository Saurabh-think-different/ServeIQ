<script setup lang="ts">
import { computed, ref } from 'vue';
import type { ApiClient, IngestionReport, IngestionTarget, PipelineReport, SearchRequest, SearchResponse } from '../api/types';

const props = defineProps<{ api: ApiClient }>();
const target = ref<IngestionTarget>('structured');
const consent = ref(false);
const importing = ref(false);
const importError = ref('');
const report = ref<PipelineReport | null>(null);
const needsConsent = computed(() => target.value !== 'structured');
const query = ref('');
const category = ref('');
const model = ref('');
const version = ref('');
const limit = ref<number | string>(5);
const searching = ref(false);
const searchError = ref('');
const results = ref<SearchResponse | null>(null);
const resultQuery = ref('');

function countNames(stage: IngestionReport): string[] {
  return [...new Set([
    ...Object.keys(stage.inserted), ...Object.keys(stage.updated),
    ...Object.keys(stage.skipped), ...Object.keys(stage.deleted),
  ])];
}

async function importData() {
  if (importing.value || (needsConsent.value && !consent.value)) return;
  const selectedTarget = target.value;
  consent.value = false;
  importing.value = true;
  importError.value = '';
  try {
    report.value = await props.api.ingest(selectedTarget);
  } catch {
    importError.value = 'Import outcome unknown: the request could not be confirmed. Some changes may have completed; rollback is not assumed. Retrying the same bundled import is idempotent.';
  } finally {
    importing.value = false;
  }
}

async function search() {
  if (searching.value) return;
  searchError.value = '';
  const requestedLimit = Number(limit.value);
  if (!query.value.trim()) {
    searchError.value = 'Enter a query.';
    return;
  }
  if (!Number.isInteger(requestedLimit) || requestedLimit < 1 || requestedLimit > 10) {
    searchError.value = 'Limit must be a whole number from 1 to 10.';
    return;
  }
  const request: SearchRequest = { query: query.value.trim(), limit: requestedLimit };
  if (category.value.trim()) request.category = category.value.trim();
  if (model.value.trim()) request.model = model.value.trim();
  if (version.value.trim()) request.system_version = version.value.trim();
  searching.value = true;
  try {
    results.value = await props.api.searchKnowledge(request);
    resultQuery.value = request.query;
  } catch {
    searchError.value = 'Knowledge search failed. Try again. Any results below are from the last successful search in this view.';
  } finally {
    searching.value = false;
  }
}
</script>

<template>
  <div class="stack">
    <header class="page-header">
      <p class="eyebrow">Data &amp; knowledge</p>
      <h1>Bundled imports and retrieval</h1>
      <p class="muted">Import server-configured data or manually explore cited knowledge passages.</p>
    </header>

    <section class="card stack" aria-labelledby="import-heading">
      <header class="card-header">
        <h2 id="import-heading">Server-configured bundled imports</h2>
      </header>
      <p>Sources and paths are configured on the server. This view does not upload files or accept arbitrary host paths. Nothing is imported automatically.</p>
      <form class="stack" data-testid="import-form" @submit.prevent="importData">
        <label class="field">
          Import target
          <select v-model="target" data-testid="import-target" :disabled="importing" @change="consent = false">
            <option value="structured">Structured data</option>
            <option value="knowledge">Knowledge</option>
            <option value="all">Structured data and knowledge</option>
          </select>
        </label>
        <div v-if="needsConsent" class="alert warning">
          <p>Knowledge imports send KB passage content to OpenAI for embedding. Do not proceed unless this transfer is authorized.</p>
          <label class="inline">
            <input v-model="consent" data-testid="knowledge-consent" type="checkbox" :disabled="importing" />
            I confirm the transfer of bundled KB passage content to OpenAI for this submission only.
          </label>
        </div>
        <div class="toolbar">
          <button class="button primary" type="submit" :disabled="importing || (needsConsent && !consent)">
            {{ importing ? 'Importing…' : 'Import bundled data' }}
          </button>
        </div>
      </form>
      <p v-if="importError" class="alert error" role="alert" data-testid="import-error">{{ importError }}</p>
      <div v-if="report" class="stack" data-testid="import-report" aria-live="polite">
        <p class="muted">Latest received import result in this view/session only; not a persisted last-sync inventory.</p>
        <p class="alert" :class="report.status === 'succeeded' ? 'success' : report.status === 'partial' ? 'warning' : 'error'" data-testid="import-status">
          Overall import: {{ report.status }}
        </p>
        <section v-for="(stage, index) in report.stages" :key="`${stage.stage}-${index}`" class="stack" :aria-label="`${stage.stage} stage`">
          <h3>{{ stage.stage }} <span class="badge">{{ stage.status }}</span></h3>
          <div v-if="countNames(stage).length" class="table-wrap">
            <table class="data-table">
              <caption>{{ stage.stage }} stage counts</caption>
              <thead><tr><th scope="col">Collection / table</th><th scope="col">Inserted</th><th scope="col">Updated</th><th scope="col">Skipped</th><th scope="col">Deleted</th></tr></thead>
              <tbody>
                <tr v-for="name in countNames(stage)" :key="name">
                  <th scope="row">{{ name }}</th><td>{{ stage.inserted[name] ?? 0 }}</td><td>{{ stage.updated[name] ?? 0 }}</td><td>{{ stage.skipped[name] ?? 0 }}</td><td>{{ stage.deleted[name] ?? 0 }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted">No collection or table counts reported for this stage.</p>
          <div v-if="Object.keys(stage.path_mappings).length">
            <h4>Reported path mappings</h4>
            <ul><li v-for="(count, path) in stage.path_mappings" :key="path">{{ path }}: {{ count }}</li></ul>
          </div>
          <ul v-if="stage.warnings.length" class="alert warning" aria-label="Stage warnings"><li v-for="(warning, n) in stage.warnings" :key="n">{{ warning }}</li></ul>
          <ul v-if="stage.errors.length" class="alert error" aria-label="Stage errors"><li v-for="(error, n) in stage.errors" :key="n">{{ error }}</li></ul>
        </section>
      </div>
    </section>

    <section class="card stack" aria-labelledby="search-heading">
      <header class="card-header"><h2 id="search-heading">Retrieval playground</h2></header>
      <p class="alert warning">Your query is sent to the configured embedding provider (OpenAI). Redaction is limited, not a guarantee of removing sensitive information. Do not include secrets or personal data.</p>
      <form class="stack" data-testid="search-form" novalidate @submit.prevent="search">
        <label class="field">Query<textarea v-model="query" data-testid="search-query" rows="3" :disabled="searching" /></label>
        <div class="filter-grid">
          <label class="field">Exact category (optional)<input v-model="category" data-testid="search-category" :disabled="searching" /></label>
          <label class="field">Exact model (optional)<input v-model="model" data-testid="search-model" :disabled="searching" /></label>
          <label class="field">Exact system version (optional)<input v-model="version" data-testid="search-version" :disabled="searching" /></label>
          <label class="field">Passage limit (1–10)<input v-model="limit" data-testid="search-limit" type="number" min="1" max="10" step="1" :disabled="searching" /></label>
        </div>
        <div class="toolbar"><button class="button primary" type="submit" :disabled="searching">{{ searching ? 'Searching…' : 'Search knowledge' }}</button></div>
      </form>
      <p v-if="searchError" class="alert error" role="alert" data-testid="search-error">{{ searchError }}</p>
      <div v-if="results" class="stack" data-testid="search-results" aria-live="polite">
        <p class="muted">Last successful search in this view: {{ resultQuery }}</p>
        <p v-if="results.evidence_gap" class="alert warning" data-testid="evidence-gap">Evidence gap: retrieved passages do not establish sufficient applicable evidence. Do not treat them as a verified answer.</p>
        <ul v-if="results.warnings.length" class="alert warning" aria-label="Search warnings"><li v-for="(warning, index) in results.warnings" :key="index">{{ warning }}</li></ul>
        <p v-if="!results.passages.length" class="empty-state">No cited passages returned. No supporting evidence is available from this search.</p>
        <article v-for="(passage, index) in results.passages" :key="index" class="card stack" :aria-label="`Citation ${index + 1}`">
          <header class="card-header"><h3>Citation {{ index + 1 }}</h3><span class="badge">Similarity score: {{ passage.similarity_score }} (not confidence)</span></header>
          <p><strong>Source:</strong> {{ passage.source_path }} <strong>Section:</strong> {{ passage.section || 'Not provided' }}</p>
          <p class="passage-content">{{ passage.content }}</p>
          <p><strong>Applicability:</strong> {{ passage.applicability === 'context_match' ? 'Context match (not proof of applicability)' : 'Unknown' }}</p>
          <p class="muted">Category: {{ passage.category ?? 'Not provided' }} · Model: {{ passage.model ?? 'Not provided' }} · System version: {{ passage.system_version ?? 'Not provided' }} · Document type: {{ passage.document_type ?? 'Not provided' }}</p>
          <ul v-if="passage.applicability_notes.length" aria-label="Applicability notes"><li v-for="(note, n) in passage.applicability_notes" :key="n">{{ note }}</li></ul>
          <p v-else class="muted">No applicability notes provided.</p>
        </article>
      </div>
      <p v-else-if="!searching" class="empty-state">Run a query to retrieve cited passages. No source inventory is inferred.</p>
    </section>
  </div>
</template>

<style scoped>
.filter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); gap: 1rem; }
.passage-content { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
