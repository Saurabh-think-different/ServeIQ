import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import IncidentView from '../src/views/IncidentView.vue';
import type { ApiClient, IncidentContext, IncidentResult, SearchResponse } from '../src/api/types';

const unsafe = '<img src=x onerror="alert(1)"><script>alert(1)</script>';
function ticket(id = 'INC-1'): IncidentResult {
  return {
    incident: {
      ticket_id: id, subject: `Terminal offline ${id}`, description: unsafe,
      reported_store_id: 'reported-store', store_id: 'S1', reported_asset_id: 'reported-asset', asset_id: 'A1',
      opened_at: '2026-01-01T10:00:00Z', priority: 'P2', category: 'POS', subcategory: 'Offline',
      status: 'Open', system_version: 'OS 4 / App 7 (intake)',
      history: [{ timestamp: 'unknown', actor: 'Manager', note: 'Original source note' }],
    }, warnings: ['Reported identifiers require verification'],
  };
}
function context(id = 'INC-1'): IncidentContext {
  return {
    incident: {
      ticket_id: id, subject: `Terminal offline ${id}`, description: unsafe,
      reported_store_id: 'reported-store', reported_asset_id: 'reported-asset', opened_at: '2026-01-01T10:00:00Z',
      priority: 'P2', category: 'POS', subcategory: 'Offline', status: 'Open',
      system_version: 'OS 4 / App 7 (intake)', escalation_flag: false,
    },
    store: { store_id: 'S1', store_name: 'Harbor Grill', city: 'Pune', neighborhood: 'Center', store_type: 'Restaurant' },
    asset: { asset_id: 'A1', store_id: 'S1', asset_type: 'POS', make: 'Serve', model: 'Countertop 3', system_version: '4.2.1', serial_number: 'serial-1', install_date: null, last_service_date: null, warranty_expiry: null },
    sla: { priority: 'P2', category: 'POS', first_response_minutes: 15, resolution_target_minutes: 240, escalation_to_l2_minutes: 60 },
    history: [
      { event_id: 2, source_event_index: 0, event_at: '2026-01-01T11:00:00Z', actor: 'L1', note: 'First source event' },
      { event_id: 3, source_event_index: 1, event_at: '2026-01-01T09:00:00Z', actor: 'Manager', note: 'Second source event' },
    ],
    related: [
      { reported_related_ticket_id: 'INC-2', related_ticket_id: 'INC-2', persisted_related_ticket_id: 'INC-2', relation_type: 'reported' },
      { reported_related_ticket_id: 'INC-missing', related_ticket_id: null, persisted_related_ticket_id: 'stale-id', relation_type: 'reported' },
    ],
    clock: {
      now: '2026-01-01T12:00:00Z', opened_at: '2026-01-01T10:00:00Z', elapsed_since_open_minutes: 120,
      status_kind: 'open', open_target_deadlines: {
        first_response_at: '2026-01-01T10:15:00Z', resolution_at: '2026-01-01T14:00:00Z', escalation_to_l2_at: '2026-01-01T11:00:00Z',
      }, first_response_fulfillment: 'unknown', fulfillment_note: 'No response milestone is recorded.',
    }, missing: ['Telemetry unavailable'], warnings: ['Clock is a snapshot only'],
  };
}
function results(): SearchResponse {
  return {
    passages: [{ content: unsafe, source_path: 'https://untrusted.example/runbook.md', section: '§ 3 — Power', similarity_score: 0.81,
      category: 'POS', model: null, system_version: null, document_type: 'runbook', applicability: 'unknown', applicability_notes: ['Version compatibility not established'] }],
    evidence_gap: true, warnings: ['Source metadata is incomplete'],
  };
}
function api() {
  return {
    listIncidents: vi.fn<ApiClient['listIncidents']>(), createIncident: vi.fn<ApiClient['createIncident']>(),
    getIncident: vi.fn<ApiClient['getIncident']>().mockResolvedValue(ticket()),
    getContext: vi.fn<ApiClient['getContext']>().mockResolvedValue(context()),
    searchKnowledge: vi.fn<ApiClient['searchKnowledge']>().mockResolvedValue(results()),
    ingest: vi.fn<ApiClient['ingest']>(),
  } satisfies ApiClient;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const wrappers: VueWrapper[] = [];
function render(client = api(), id = 'INC-1') {
  const wrapper = mount(IncidentView, { props: { api: client, ticketId: id } });
  wrappers.push(wrapper);
  return wrapper;
}
async function clickButton(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find(item => item.text() === text);
  expect(button, `Missing button: ${text}`).toBeDefined();
  await button!.trigger('click');
  await flushPromises();
}
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); });

it('section links prevent route-hash changes so the incident stays mounted', async () => {
  const wrapper = render();
  await flushPromises();
  for (const link of wrapper.findAll('.section-nav a')) {
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    link.element.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  }
  expect(wrapper.text()).toContain('Terminal offline INC-1');
});

describe('incident workspace', () => {
  it('loads detail and context independently and never automatically retrieves knowledge', async () => {
    const client = api();
    const pending = deferred<IncidentContext>();
    client.getContext.mockReturnValue(pending.promise);
    const wrapper = render(client);
    await flushPromises();
    expect(client.getIncident).toHaveBeenCalledWith('INC-1');
    expect(client.getContext).toHaveBeenCalledWith('INC-1');
    expect(wrapper.get('h1').text()).toBe('Terminal offline INC-1');
    expect(wrapper.text()).toContain('Loading verified context');
    expect(client.searchKnowledge).not.toHaveBeenCalled();
    pending.resolve(context());
    await flushPromises();
    expect(wrapper.text()).toContain('Harbor Grill');
    expect(wrapper.text()).toContain('Telemetry unavailable');
  });

  it('preserves detail when context fails and retries only context', async () => {
    const client = api();
    client.getContext.mockRejectedValueOnce(new Error('Context offline'));
    const wrapper = render(client);
    await flushPromises();
    expect(wrapper.text()).toContain('Context offline');
    expect(wrapper.text()).toContain('Original ticket');
    expect(wrapper.text()).toContain('reported-asset');
    await clickButton(wrapper, 'Retry context');
    expect(client.getIncident).toHaveBeenCalledTimes(1);
    expect(client.getContext).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain('Countertop 3');
    expect(wrapper.text()).not.toContain('Context offline');
  });

  it('shows verified context when detail fails and can independently retry detail', async () => {
    const client = api();
    client.getIncident.mockRejectedValueOnce(new Error('Detail offline'));
    const wrapper = render(client);
    await flushPromises();
    expect(wrapper.text()).toContain('Detail offline');
    expect(wrapper.text()).toContain('Countertop 3');
    expect(wrapper.get('h1').text()).toBe('Terminal offline INC-1');
    await clickButton(wrapper, 'Retry ticket detail');
    expect(client.getContext).toHaveBeenCalledTimes(1);
    expect(client.getIncident).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).not.toContain('Detail offline');
  });

  it('renders original text literally and keeps reported claims apart from records', async () => {
    const wrapper = render();
    await flushPromises();
    expect(wrapper.get('.original-description').text()).toBe(unsafe);
    expect(wrapper.find('img').exists()).toBe(false);
    expect(wrapper.find('script').exists()).toBe(false);
    expect(wrapper.get('#incident-overview').text()).toContain('Reported, not verified');
    expect(wrapper.get('#incident-overview').text()).toContain('reported-store');
    expect(wrapper.get('#incident-context').text()).toContain('S1');
    expect(wrapper.text()).toContain('Next backend stage; no analysis executed');
    expect(wrapper.findAll('button').some(button => /approve|replay|escalate/i.test(button.text()))).toBe(false);
  });

  it('shows SLA durations and deadlines without asserting fulfillment or breach', async () => {
    const wrapper = render();
    await flushPromises();
    const sla = wrapper.get('#sla-heading').element.parentElement!.parentElement!.textContent!;
    expect(sla).toContain('Fulfillment unknown');
    expect(sla).toContain('15 minutes');
    expect(sla).toContain('240 minutes');
    expect(sla).toContain('60 minutes');
    expect(sla).toContain('2026-01-01T10:15:00Z');
    expect(sla).toContain('No response milestone is recorded.');
    expect(sla).not.toMatch(/breached|on track|time left|response met/i);
  });

  it('preserves history source order and links only resolved related references', async () => {
    const wrapper = render();
    await flushPromises();
    const notes = wrapper.findAll('.history-list li p').map(note => note.text());
    expect(notes).toEqual(['First source event', 'Second source event']);
    expect(wrapper.text()).toContain('not timestamp order');
    expect(wrapper.text()).toContain('Clock snapshot');
    expect(wrapper.get('.related-list').text()).toContain('INC-missing — unresolved reference');
    expect(wrapper.findAll('.related-list button')).toHaveLength(1);
    await clickButton(wrapper, 'INC-2');
    expect(wrapper.emitted('open-incident')).toEqual([['INC-2']]);
    await clickButton(wrapper, 'Incident queue');
    expect(wrapper.emitted('back')).toEqual([[]]);
  });

  it('requires an explicit search, uses no automatic version filters, and renders safe citations', async () => {
    const client = api();
    const wrapper = render(client);
    await flushPromises();
    expect((wrapper.get('#incident-query').element as HTMLTextAreaElement).value).toBe('Terminal offline INC-1');
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(client.searchKnowledge).toHaveBeenCalledExactlyOnceWith({ query: 'Terminal offline INC-1', limit: 5 });
    expect(wrapper.get('.passage-content').text()).toBe(unsafe);
    expect(wrapper.find('img').exists()).toBe(false);
    expect(wrapper.find('script').exists()).toBe(false);
    expect(wrapper.get('.citation').text()).toContain('https://untrusted.example/runbook.md');
    expect(wrapper.find('a[href^="https:"]').exists()).toBe(false);
    expect(wrapper.text()).toContain('§ 3 — Power');
    expect(wrapper.text()).toContain('Similarity 0.81');
    expect(wrapper.text()).not.toContain('Confidence');
    expect(wrapper.text()).toContain('Applicability: unknown');
    expect(wrapper.text()).toContain('Version compatibility not established');
    expect(wrapper.text()).toContain('Evidence gap');
    expect(wrapper.text()).toContain('Source metadata is incomplete');
  });

  it('uses only explicitly selected context metadata, never composite intake version', async () => {
    const client = api();
    const wrapper = render(client);
    await flushPromises();
    for (const checkbox of wrapper.findAll('.filter-options input')) await checkbox.setValue(true);
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(client.searchKnowledge).toHaveBeenCalledWith({ query: 'Terminal offline INC-1', limit: 5, category: 'POS', model: 'Countertop 3', system_version: '4.2.1' });
    expect(JSON.stringify(client.searchKnowledge.mock.calls)).not.toContain('OS 4 / App 7');
  });

  it('prefills at most 2000 characters and does not overwrite an edited query on late context', async () => {
    const client = api();
    const value = ticket();
    value.incident.subject = 'a'.repeat(2500);
    client.getIncident.mockResolvedValue(value);
    const pending = deferred<IncidentContext>();
    client.getContext.mockReturnValue(pending.promise);
    const wrapper = render(client);
    await flushPromises();
    expect((wrapper.get('#incident-query').element as HTMLTextAreaElement).value).toHaveLength(2000);
    await wrapper.get('#incident-query').setValue('operator query');
    pending.resolve(context());
    await flushPromises();
    expect((wrapper.get('#incident-query').element as HTMLTextAreaElement).value).toBe('operator query');
    await wrapper.get('#incident-query').setValue('   ');
    await wrapper.get('form').trigger('submit');
    expect(client.searchKnowledge).not.toHaveBeenCalled();
  });

  it.each([503, 409])('handles knowledge %i and permits manual retry', async status => {
    const client = api();
    client.searchKnowledge.mockRejectedValueOnce(Object.assign(new Error('Index failure'), { status }));
    const wrapper = render(client);
    await flushPromises();
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(wrapper.text()).toContain(`(${status})`);
    expect(wrapper.find('.passage').exists()).toBe(false);
    await clickButton(wrapper, 'Retry knowledge search');
    expect(client.searchKnowledge).toHaveBeenCalledTimes(2);
    expect(wrapper.find('.passage').exists()).toBe(true);
  });

  it('shows empty search results and absent records without invented evidence', async () => {
    const client = api();
    const value = context();
    value.asset = null; value.store = null; value.sla = null; value.clock.open_target_deadlines = null;
    value.history = []; value.related = [];
    client.getContext.mockResolvedValue(value);
    client.searchKnowledge.mockResolvedValue({ passages: [], evidence_gap: true, warnings: [] });
    const wrapper = render(client);
    await flushPromises();
    expect(wrapper.text()).toContain('No verified asset record');
    expect(wrapper.text()).toContain('No verified store record');
    expect(wrapper.text()).toContain('No matched SLA policy');
    expect(wrapper.text()).toContain('Open-ticket target deadlines unavailable');
    expect(wrapper.findAll('.filter-options input:disabled')).toHaveLength(2);
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(wrapper.text()).toContain('No source passages returned');
    expect(wrapper.text()).toContain('Evidence gap');
  });

  it('ignores stale detail and context responses after ticket navigation', async () => {
    const client = api();
    const oldDetail = deferred<IncidentResult>();
    const oldContext = deferred<IncidentContext>();
    client.getIncident.mockReturnValueOnce(oldDetail.promise).mockResolvedValue(ticket('INC-2'));
    client.getContext.mockReturnValueOnce(oldContext.promise).mockResolvedValue(context('INC-2'));
    const wrapper = render(client);
    await wrapper.setProps({ ticketId: 'INC-2' });
    await flushPromises();
    oldDetail.resolve(ticket('INC-1'));
    oldContext.reject(new Error('Stale error'));
    await flushPromises();
    expect(wrapper.get('h1').text()).toBe('Terminal offline INC-2');
    expect(wrapper.text()).not.toContain('Stale error');
    expect((wrapper.get('#incident-query').element as HTMLTextAreaElement).value).toBe('Terminal offline INC-2');
  });

  it('clears search and filters on navigation and ignores an old knowledge result', async () => {
    const client = api();
    const oldSearch = deferred<SearchResponse>();
    client.searchKnowledge.mockReturnValueOnce(oldSearch.promise);
    const wrapper = render(client);
    await flushPromises();
    await wrapper.findAll('.filter-options input')[1]!.setValue(true);
    await wrapper.get('form').trigger('submit');
    client.getIncident.mockResolvedValue(ticket('INC-2'));
    client.getContext.mockResolvedValue(context('INC-2'));
    await wrapper.setProps({ ticketId: 'INC-2' });
    await flushPromises();
    oldSearch.resolve(results());
    await flushPromises();
    expect(wrapper.find('.passage').exists()).toBe(false);
    expect(wrapper.text()).toContain('No search executed');
    expect(wrapper.findAll('.filter-options input').every(input => !(input.element as HTMLInputElement).checked)).toBe(true);
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(client.searchKnowledge).toHaveBeenLastCalledWith({ query: 'Terminal offline INC-2', limit: 5 });
  });

  it('discards pending responses after unmount without starting follow-up work', async () => {
    const client = api();
    const pending = deferred<IncidentResult>();
    const pendingContext = deferred<IncidentContext>();
    client.getIncident.mockReturnValue(pending.promise);
    client.getContext.mockReturnValue(pendingContext.promise);
    const wrapper = render(client);
    wrapper.unmount();
    pending.resolve(ticket());
    pendingContext.reject(new Error('Unmounted context'));
    await flushPromises();
    expect(client.searchKnowledge).not.toHaveBeenCalled();
    expect(client.getIncident).toHaveBeenCalledTimes(1);
    expect(client.getContext).toHaveBeenCalledTimes(1);
  });
});
