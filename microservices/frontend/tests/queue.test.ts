// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import QueueView from '../src/views/QueueView.vue';
import type { ApiClient, Incident, IncidentPage, IncidentResult } from '../src/api/types';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function incident(ticketId = 'INC-001'): Incident {
  return {
    ticket_id: ticketId, subject: 'Checkout unavailable', description: 'Checkout shows an error.',
    reported_store_id: '0007', store_id: null, reported_asset_id: '0002', asset_id: null,
    opened_at: '2026-10-19T08:00:00Z', priority: 'High', category: 'POS', subcategory: null,
    status: 'Open', system_version: null, history: [],
  };
}

function page(incidents: Incident[] = [incident()], total = incidents.length, offset = 0): IncidentPage {
  return { incidents, total, offset, limit: 25 };
}

function apiMock() {
  return {
    listIncidents: vi.fn<ApiClient['listIncidents']>().mockResolvedValue(page()),
    createIncident: vi.fn<ApiClient['createIncident']>().mockResolvedValue({ incident: incident(), warnings: [] }),
    getIncident: vi.fn<ApiClient['getIncident']>(), getContext: vi.fn<ApiClient['getContext']>(),
    searchKnowledge: vi.fn<ApiClient['searchKnowledge']>(), ingest: vi.fn<ApiClient['ingest']>(),
  } satisfies ApiClient;
}

const wrappers: VueWrapper[] = [];
function render(api = apiMock()) {
  const wrapper = mount(QueueView, { props: { api } });
  wrappers.push(wrapper);
  return wrapper;
}
function button(wrapper: VueWrapper, label: string) {
  const result = wrapper.findAll('button').find(item => item.text() === label);
  if (!result) throw new Error(`Missing button: ${label}`);
  return result;
}
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); });

describe('Incident queue', () => {
  it('loads a page, preserves reported IDs, and opens a row with an accessible button', async () => {
    const pending = deferred<IncidentPage>();
    const api = apiMock();
    api.listIncidents.mockReturnValueOnce(pending.promise);
    const wrapper = render(api);
    expect(wrapper.get('[role="status"]').text()).toContain('Loading incidents');
    expect(api.listIncidents).toHaveBeenCalledWith({ limit: 25, offset: 0 });
    pending.resolve(page());
    await flushPromises();
    expect(wrapper.text()).not.toContain('Loading incidents');
    expect(wrapper.find('table').text()).toContain('0007');
    expect(wrapper.text()).toContain('1–1 of 1 incidents');
    await wrapper.get('.incident-link').trigger('click');
    expect(wrapper.emitted('open-incident')).toEqual([['INC-001']]);
  });

  it('explains the empty queue without adding sample incidents', async () => {
    const api = apiMock();
    api.listIncidents.mockResolvedValue(page([]));
    const wrapper = render(api);
    await flushPromises();
    expect(wrapper.text()).toContain('No incidents found');
    expect(wrapper.text()).toContain('Data screen');
    expect(wrapper.find('table').exists()).toBe(false);
    expect(button(wrapper, 'Next').attributes('disabled')).toBeDefined();
  });

  it('shows safe API errors and retries', async () => {
    const api = apiMock();
    api.listIncidents.mockRejectedValueOnce(new Error('The queue is temporarily unavailable.'));
    const wrapper = render(api);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain('The queue is temporarily unavailable.');
    await button(wrapper, 'Retry').trigger('click');
    await flushPromises();
    expect(api.listIncidents).toHaveBeenCalledTimes(2);
    expect(wrapper.find('table').exists()).toBe(true);
  });

  it('paginates and applies exact category/status filters from the first page', async () => {
    const api = apiMock();
    api.listIncidents.mockResolvedValueOnce(page([incident()], 26))
      .mockResolvedValueOnce(page([incident('INC-026')], 26, 25))
      .mockResolvedValueOnce(page([incident()], 26))
      .mockResolvedValueOnce(page([]));
    const wrapper = render(api);
    await flushPromises();
    expect(button(wrapper, 'Previous').attributes('disabled')).toBeDefined();
    await button(wrapper, 'Next').trigger('click');
    await flushPromises();
    expect(api.listIncidents).toHaveBeenLastCalledWith({ limit: 25, offset: 25 });
    expect(wrapper.text()).toContain('26–26 of 26 incidents');
    expect(button(wrapper, 'Next').attributes('disabled')).toBeDefined();
    await button(wrapper, 'Previous').trigger('click');
    await flushPromises();
    expect(api.listIncidents).toHaveBeenLastCalledWith({ limit: 25, offset: 0 });
    await wrapper.get('#filter-category').setValue('POS');
    await wrapper.get('#filter-status').setValue('Open');
    await wrapper.get('form[aria-label="Filter incidents"]').trigger('submit');
    await flushPromises();
    expect(api.listIncidents).toHaveBeenLastCalledWith({ limit: 25, offset: 0, category: 'POS', status: 'Open' });
    await wrapper.get('#filter-category').setValue('');
    await wrapper.get('#filter-status').setValue('');
    await wrapper.get('form[aria-label="Filter incidents"]').trigger('submit');
    await flushPromises();
    expect(api.listIncidents).toHaveBeenLastCalledWith({ limit: 25, offset: 0 });
  });

  it('ignores stale list results when refreshed and after unmount', async () => {
    const first = deferred<IncidentPage>();
    const second = deferred<IncidentPage>();
    const third = deferred<IncidentPage>();
    const api = apiMock();
    api.listIncidents.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise).mockReturnValueOnce(third.promise);
    const wrapper = render(api);
    await button(wrapper, 'Refresh').trigger('click');
    second.resolve(page([incident('INC-NEW')]));
    await flushPromises();
    first.reject(new Error('Stale failure'));
    await flushPromises();
    expect(wrapper.text()).toContain('INC-NEW');
    expect(wrapper.text()).not.toContain('Stale failure');
    await button(wrapper, 'Refresh').trigger('click');
    wrapper.unmount();
    third.resolve(page());
    await flushPromises();
    expect(wrapper.emitted('open-incident')).toBeUndefined();
  });
});

describe('Manual incident intake', () => {
  it('requires a meaningful description and omits blank optional fields', async () => {
    const api = apiMock();
    const wrapper = render(api);
    await flushPromises();
    await button(wrapper, 'New incident').trigger('click');
    const form = wrapper.get('#incident-intake form');
    await wrapper.get('#incident-description').setValue('   ');
    await form.trigger('submit');
    expect(api.createIncident).not.toHaveBeenCalled();
    expect(wrapper.get('[role="alert"]').text()).toContain('Describe the issue');
    await wrapper.get('#incident-description').setValue('  Checkout fails when paying.  ');
    await wrapper.get('#incident-subject').setValue('  ');
    await wrapper.get('#incident-store').setValue('0007');
    await wrapper.get('#incident-asset').setValue('0002');
    await form.trigger('submit');
    await flushPromises();
    expect(api.createIncident).toHaveBeenCalledExactlyOnceWith({ description: 'Checkout fails when paying.', store_id: '0007', asset_id: '0002' });
    expect(wrapper.emitted('open-incident')).toEqual([['INC-001']]);
    expect(api.getIncident).not.toHaveBeenCalled();
    expect(api.ingest).not.toHaveBeenCalled();
  });

  it('blocks duplicate submissions and exposes creation warnings when still mounted', async () => {
    const pending = deferred<IncidentResult>();
    const api = apiMock();
    api.createIncident.mockReturnValueOnce(pending.promise);
    const wrapper = render(api);
    await flushPromises();
    await button(wrapper, 'New incident').trigger('click');
    await wrapper.get('#incident-description').setValue('Checkout fails.');
    await wrapper.get('#incident-subject').setValue('Payment failure');
    await wrapper.get('#incident-category').setValue('POS');
    await wrapper.get('#incident-priority').setValue('High');
    await wrapper.get('#incident-version').setValue('01.02');
    const form = wrapper.get('#incident-intake form');
    await form.trigger('submit');
    await form.trigger('submit');
    expect(api.createIncident).toHaveBeenCalledTimes(1);
    expect(api.createIncident).toHaveBeenCalledWith({ description: 'Checkout fails.', subject: 'Payment failure', category: 'POS', priority: 'High', system_version: '01.02' });
    expect(button(wrapper, 'Creating…').attributes('disabled')).toBeDefined();
    pending.resolve({ incident: incident(), warnings: ['Store could not be matched.'] });
    await flushPromises();
    expect(wrapper.text()).toContain('Store could not be matched.');
    expect(wrapper.text()).toContain('Incident INC-001 created.');
    expect((wrapper.get('#incident-description').element as HTMLTextAreaElement).value).toBe('');
  });

  it('preserves intake values on backend validation failure and allows retry', async () => {
    const api = apiMock();
    api.createIncident.mockRejectedValueOnce(new Error('Priority must be one of the supported values.'));
    const wrapper = render(api);
    await flushPromises();
    await button(wrapper, 'New incident').trigger('click');
    await wrapper.get('#incident-description').setValue('Checkout fails.');
    await wrapper.get('#incident-intake form').trigger('submit');
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain('Priority must be one of the supported values.');
    expect((wrapper.get('#incident-description').element as HTMLTextAreaElement).value).toBe('Checkout fails.');
    await wrapper.get('#incident-intake form').trigger('submit');
    await flushPromises();
    expect(api.createIncident).toHaveBeenCalledTimes(2);
  });

  it('does not emit a late creation result after unmount', async () => {
    const pending = deferred<IncidentResult>();
    const api = apiMock();
    api.createIncident.mockReturnValueOnce(pending.promise);
    const wrapper = render(api);
    await flushPromises();
    await button(wrapper, 'New incident').trigger('click');
    await wrapper.get('#incident-description').setValue('Checkout fails.');
    await wrapper.get('#incident-intake form').trigger('submit');
    wrapper.unmount();
    pending.resolve({ incident: incident(), warnings: [] });
    await flushPromises();
    expect(wrapper.emitted('open-incident')).toBeUndefined();
    expect(api.listIncidents).toHaveBeenCalledTimes(1);
  });
});
