import { workflowMock } from './workflowMock';
import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import DataView from '../src/views/DataView.vue';
import type { ApiClient, IngestionReport, PipelineReport, SearchResponse } from '../src/api/types';

function stage(overrides: Partial<IngestionReport> = {}): IngestionReport {
  return { stage: 'structured', status: 'succeeded', inserted: {}, updated: {}, skipped: {}, deleted: {}, warnings: [], errors: [], path_mappings: {}, ...overrides };
}
function setup() {
  const ingest = vi.fn<ApiClient['ingest']>().mockResolvedValue({ status: 'succeeded', stages: [stage()] });
  const searchKnowledge = vi.fn<ApiClient['searchKnowledge']>().mockResolvedValue({ passages: [], evidence_gap: true, warnings: [] });
  const api: ApiClient = {
    ...workflowMock(), ingest, searchKnowledge,
    listIncidents: vi.fn(), createIncident: vi.fn(), getIncident: vi.fn(), getContext: vi.fn(),
  };
  return { wrapper: mount(DataView, { props: { api } }), ingest, searchKnowledge };
}
const searchResult: SearchResponse = {
  evidence_gap: true,
  warnings: ['Limited evidence <script>alert(1)</script>'],
  passages: [{
    content: '<img src=x onerror=alert(1)> Literal passage', source_path: 'javascript:alert(1)',
    section: '<b>Maintenance</b>', similarity_score: 0.74, category: 'Printer', model: 'P1',
    system_version: 'v2', document_type: 'manual', applicability: 'unknown',
    applicability_notes: ['Version not verified <script>bad()</script>'],
  }],
};

describe('DataView bundled imports', () => {
  it('does not import on mount and submits the structured default explicitly', async () => {
    const { wrapper, ingest, searchKnowledge } = setup();
    expect(ingest).not.toHaveBeenCalled();
    expect(searchKnowledge).not.toHaveBeenCalled();
    expect(wrapper.get('[data-testid="import-target"]').element).toHaveProperty('value', 'structured');
    expect(wrapper.find('[data-testid="knowledge-consent"]').exists()).toBe(false);
    expect(wrapper.find('input[type="file"]').exists()).toBe(false);
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    await flushPromises();
    expect(ingest).toHaveBeenCalledExactlyOnceWith('structured');
    expect(wrapper.get('[data-testid="import-status"]').classes()).toContain('success');
    expect(wrapper.text()).toContain('this view/session only');
  });

  it.each(['knowledge', 'all'])('requires one-submission consent for %s', async (target) => {
    const { wrapper, ingest } = setup();
    await wrapper.get('[data-testid="import-target"]').setValue(target);
    expect(wrapper.get('[data-testid="import-form"] button').attributes('disabled')).toBeDefined();
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    expect(ingest).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="knowledge-consent"]').setValue(true);
    expect(ingest).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    await flushPromises();
    expect(ingest).toHaveBeenCalledExactlyOnceWith(target);
    expect(wrapper.get('[data-testid="knowledge-consent"]').element).toHaveProperty('checked', false);
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    expect(ingest).toHaveBeenCalledTimes(1);
  });

  it('revokes consent when changing the target', async () => {
    const { wrapper, ingest } = setup();
    await wrapper.get('[data-testid="import-target"]').setValue('knowledge');
    await wrapper.get('[data-testid="knowledge-consent"]').setValue(true);
    await wrapper.get('[data-testid="import-target"]').setValue('all');
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    expect(ingest).not.toHaveBeenCalled();
  });

  it('renders a partial body as warning with independent stage statuses, counts and literal diagnostics', async () => {
    const { wrapper, ingest } = setup();
    ingest.mockResolvedValueOnce({ status: 'partial', stages: [
      stage({ inserted: { stores: 3 }, updated: { stores: 2 }, skipped: { stores: 1 }, deleted: { stores: 4 }, path_mappings: { '<b>bundled/path</b>': 6 }, warnings: ['<img src=x onerror=bad()>'] }),
      stage({ stage: 'knowledge', status: 'blocked', errors: ['Embedding provider unavailable <script>bad()</script>'] }),
    ] });
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    await flushPromises();
    const status = wrapper.get('[data-testid="import-status"]');
    expect(status.text()).toContain('partial');
    expect(status.classes()).toContain('warning');
    expect(status.classes()).not.toContain('success');
    expect(wrapper.get('section[aria-label="structured stage"]').text()).toContain('succeeded');
    expect(wrapper.get('section[aria-label="knowledge stage"]').text()).toContain('blocked');
    expect(wrapper.findAll('tbody tr')[0]!.findAll('td').map((cell) => cell.text())).toEqual(['3', '2', '1', '4']);
    expect(wrapper.text()).toContain('<b>bundled/path</b>: 6');
    expect(wrapper.text()).toContain('Embedding provider unavailable <script>bad()</script>');
    expect(wrapper.find('script, img').exists()).toBe(false);
  });

  it('shows failed overall and failed stage, including count names present only in deleted', async () => {
    const { wrapper, ingest } = setup();
    ingest.mockResolvedValueOnce({ status: 'failed', stages: [stage({ status: 'failed', deleted: { assets: 2 }, errors: ['Invalid bundled data'] })] });
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    await flushPromises();
    expect(wrapper.get('[data-testid="import-status"]').classes()).toContain('error');
    expect(wrapper.get('section[aria-label="structured stage"]').text()).toContain('failed');
    expect(wrapper.findAll('tbody tr')[0]!.findAll('td').map((cell) => cell.text())).toEqual(['0', '0', '0', '2']);
    expect(wrapper.text()).toContain('Invalid bundled data');
  });

  it('blocks duplicate import submissions and reports unknown transport outcome without claiming rollback', async () => {
    const { wrapper, ingest } = setup();
    let reject!: (reason?: unknown) => void;
    ingest.mockImplementationOnce(() => new Promise<PipelineReport>((_resolve, rejectPromise) => { reject = rejectPromise; }));
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    expect(ingest).toHaveBeenCalledTimes(1);
    expect(wrapper.get('[data-testid="import-form"] button').attributes('disabled')).toBeDefined();
    reject(new Error('private transport details'));
    await flushPromises();
    expect(wrapper.get('[data-testid="import-error"]').text()).toContain('outcome unknown');
    expect(wrapper.get('[data-testid="import-error"]').text()).toContain('rollback is not assumed');
    expect(wrapper.get('[data-testid="import-error"]').text()).toContain('check before explicitly retrying');
    expect(wrapper.text()).not.toContain('private transport details');
    expect(wrapper.get('[data-testid="import-form"] button').attributes('disabled')).toBeUndefined();
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    await flushPromises();
    expect(ingest).toHaveBeenCalledTimes(2);
  });
});

describe('DataView retrieval playground', () => {
  it('searches manually with exact optional filters and literal cited evidence', async () => {
    const { wrapper, searchKnowledge } = setup();
    searchKnowledge.mockResolvedValueOnce(searchResult);
    await wrapper.get('[data-testid="search-query"]').setValue(' paper jam ');
    await wrapper.get('[data-testid="search-category"]').setValue('Printer');
    await wrapper.get('[data-testid="search-model"]').setValue('P1');
    await wrapper.get('[data-testid="search-version"]').setValue('v2');
    await wrapper.get('[data-testid="search-limit"]').setValue('10');
    expect(searchKnowledge).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="search-form"]').trigger('submit');
    await flushPromises();
    expect(searchKnowledge).toHaveBeenCalledExactlyOnceWith({ query: 'paper jam', category: 'Printer', model: 'P1', system_version: 'v2', limit: 10 });
    const citation = wrapper.get('article[aria-label="Citation 1"]');
    expect(citation.text()).toContain('javascript:alert(1)');
    expect(citation.text()).toContain('<b>Maintenance</b>');
    expect(citation.text()).toContain('<img src=x onerror=alert(1)> Literal passage');
    expect(citation.text()).toContain('Similarity score: 0.74 (not confidence)');
    expect(citation.text()).toContain('Applicability: Unknown');
    expect(citation.text()).toContain('Version not verified <script>bad()</script>');
    expect(wrapper.get('[data-testid="evidence-gap"]').text()).toContain('Evidence gap');
    expect(wrapper.find('script, img, a').exists()).toBe(false);
    expect(wrapper.text()).toContain('Redaction is limited');
  });

  it('omits blank filters and supports minimum limit and an empty result', async () => {
    const { wrapper, searchKnowledge } = setup();
    await wrapper.get('[data-testid="search-query"]').setValue('test');
    await wrapper.get('[data-testid="search-limit"]').setValue('1');
    await wrapper.get('[data-testid="search-form"]').trigger('submit');
    await flushPromises();
    expect(searchKnowledge).toHaveBeenCalledExactlyOnceWith({ query: 'test', limit: 1 });
    expect(wrapper.text()).toContain('No cited passages returned');
  });

  it.each(['0', '11', '1.5', ''])('rejects invalid limit %j without calling the API', async (limit) => {
    const { wrapper, searchKnowledge } = setup();
    await wrapper.get('[data-testid="search-query"]').setValue('test');
    await wrapper.get('[data-testid="search-limit"]').setValue(limit);
    await wrapper.get('[data-testid="search-form"]').trigger('submit');
    expect(searchKnowledge).not.toHaveBeenCalled();
    expect(wrapper.get('[data-testid="search-error"]').text()).toContain('whole number from 1 to 10');
  });

  it('rejects blank queries', async () => {
    const { wrapper, searchKnowledge } = setup();
    await wrapper.get('[data-testid="search-query"]').setValue('   ');
    await wrapper.get('[data-testid="search-form"]').trigger('submit');
    expect(searchKnowledge).not.toHaveBeenCalled();
    expect(wrapper.get('[data-testid="search-error"]').text()).toContain('Enter a query');
  });

  it('keeps import results and prior citations when search fails, with separate loading and errors', async () => {
    const { wrapper, ingest, searchKnowledge } = setup();
    await wrapper.get('[data-testid="import-form"]').trigger('submit');
    await flushPromises();
    searchKnowledge.mockResolvedValueOnce(searchResult);
    await wrapper.get('[data-testid="search-query"]').setValue('first query');
    await wrapper.get('[data-testid="search-form"]').trigger('submit');
    await flushPromises();
    let reject!: (reason?: unknown) => void;
    searchKnowledge.mockImplementationOnce(() => new Promise<SearchResponse>((_resolve, rejectPromise) => { reject = rejectPromise; }));
    await wrapper.get('[data-testid="search-query"]').setValue('second query');
    await wrapper.get('[data-testid="search-form"]').trigger('submit');
    await wrapper.get('[data-testid="search-form"]').trigger('submit');
    expect(searchKnowledge).toHaveBeenCalledTimes(2);
    expect(wrapper.get('[data-testid="search-form"] button').attributes('disabled')).toBeDefined();
    expect(wrapper.get('[data-testid="import-form"] button').attributes('disabled')).toBeUndefined();
    reject(new Error('private provider details'));
    await flushPromises();
    expect(wrapper.get('[data-testid="search-error"]').text()).toContain('Knowledge search could not be confirmed');
    expect(wrapper.get('[data-testid="search-results"]').text()).toContain('first query');
    expect(wrapper.find('article[aria-label="Citation 1"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="import-status"]').text()).toContain('succeeded');
    expect(wrapper.find('[data-testid="import-error"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain('private provider details');
    expect(ingest).toHaveBeenCalledTimes(1);
  });
});
