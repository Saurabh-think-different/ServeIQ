import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import RunWorkspace from '../src/views/RunWorkspace.vue'
import type { ActionProposal, ApiClient, RunResult } from '../src/api/types'
import { workflowMock } from './workflowMock'

const run: RunResult = {
  run_id: 'RUN-1', ticket_id: 'INC-1', status: 'completed', started_at: '2026-10-22T06:00:00Z',
  completed_at: '2026-10-22T06:00:01Z', routing_outcome: 'l2_escalation', decision_mechanism: 'deterministic',
  summary_for_requester: 'Contact human support.', summary_for_ticket: '<img src=x onerror=alert(1)>',
  questions: [], steps: [], evidence: [], warnings: ['No reviewed policy.'],
  error_code: null, error_message: null, model: null, prompt_version: null,
}
const proposal: ActionProposal = {
  action_id: 'ACT-1', ticket_id: 'INC-1', run_id: 'RUN-1', action_type: 'simulate_escalation',
  preview: { version: 1, summary: 'Human review needed.', reason: 'No reviewed policy.' },
  proposal_hash: 'hash', context_fingerprint: 'fingerprint', status: 'pending',
  created_at: run.started_at, decided_at: null, decided_by: null, result_summary: null,
}
function client() {
  return {
    ...workflowMock(), listIncidents: vi.fn(), createIncident: vi.fn(), getIncident: vi.fn(),
    getContext: vi.fn(), searchKnowledge: vi.fn(), ingest: vi.fn(),
  } satisfies ApiClient
}
function withRun() {
  const api = client()
  api.listRuns.mockResolvedValue({ runs: [run], total: 1, limit: 10, offset: 0 })
  api.getRun.mockResolvedValue(run)
  api.listActions.mockResolvedValue({ run_id: run.run_id, actions: [proposal] })
  return api
}
const wrappers: VueWrapper[] = []
function render(api = client(), ticketId = 'INC-1') {
  const wrapper = mount(RunWorkspace, { props: { api, ticketId } })
  wrappers.push(wrapper)
  return wrapper
}
async function button(wrapper: VueWrapper, text: string) {
  const target = wrapper.findAll('button').find(item => item.text() === text)
  expect(target, `Missing button ${text}`).toBeDefined()
  await target!.trigger('click')
  await flushPromises()
}
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  sessionStorage.clear()
  vi.restoreAllMocks()
})

it('loads persisted results without starting a run or a decision and renders summaries literally', async () => {
  const api = withRun(), wrapper = render(api)
  await flushPromises()
  expect(api.startRun).not.toHaveBeenCalled()
  expect(api.decideAction).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('deterministic policy routing')
  expect(wrapper.text()).toContain('Contact human support.')
  expect(wrapper.text()).toContain(run.summary_for_ticket)
  expect(wrapper.find('img').exists()).toBe(false)
  expect(wrapper.text()).toContain('No reviewed policy.')
})

it('requires transfer consent and starts only on an explicit click with a recovery key', async () => {
  const api = client(), wrapper = render(api)
  api.startRun.mockResolvedValue(run)
  api.getRun.mockResolvedValue(run)
  await flushPromises()
  expect(wrapper.get('[data-testid="start-run"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-testid="run-consent"]').setValue(true)
  await wrapper.get('[data-testid="start-run"]').trigger('click')
  await flushPromises()
  expect(api.startRun).toHaveBeenCalledTimes(1)
  expect(api.startRun).toHaveBeenCalledWith('INC-1', expect.stringMatching(/^[\w-]+$/))
  expect(wrapper.get('[data-testid="run-consent"]').element).toHaveProperty('checked', false)
})

it('retains the same key after an unknown start outcome and requires a read before explicit replay', async () => {
  const api = client(), wrapper = render(api)
  api.startRun.mockRejectedValueOnce(new TypeError('Network error')).mockResolvedValueOnce(run)
  api.getRun.mockResolvedValue(run)
  await flushPromises()
  await wrapper.get('[data-testid="run-consent"]').setValue(true)
  await wrapper.get('[data-testid="start-run"]').trigger('click')
  await flushPromises()
  const key = api.startRun.mock.calls[0]![1]
  expect(api.startRun).toHaveBeenCalledTimes(1)
  expect(wrapper.text()).toContain('Outcome unknown')
  expect(wrapper.get('[data-testid="start-run"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-testid="refresh-workflow"]').trigger('click')
  await flushPromises()
  await wrapper.get('[data-testid="run-consent"]').setValue(true)
  await wrapper.get('[data-testid="start-run"]').trigger('click')
  await flushPromises()
  expect(api.startRun.mock.calls[1]).toEqual(['INC-1', key])
})

it('restores an unconfirmed start after remount without automatically replaying it', async () => {
  sessionStorage.setItem('serveiq:pending:v1:INC-1', JSON.stringify({ startKey: 'saved-key', selectedRun: null, decisions: {} }))
  const api = client(), wrapper = render(api)
  await flushPromises()
  expect(wrapper.text()).toContain('Unconfirmed run start')
  expect(wrapper.get('[data-testid="start-run"]').text()).toContain('Replay start with same key')
  expect(api.startRun).not.toHaveBeenCalled()
})

it('requires an explicit preview confirmation before approving a simulation', async () => {
  const api = withRun(), wrapper = render(api)
  api.decideAction.mockResolvedValue({ action_id: 'ACT-1', decision: 'approve', status: 'completed', decided_at: run.started_at, decided_by: 'local_operator', result_summary: 'Simulation only.' })
  await flushPromises()
  await button(wrapper, 'Approve simulation')
  expect(api.decideAction).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('Only a local simulation will be recorded.')
  await wrapper.get('[data-testid="confirm-decision"]').trigger('click')
  await flushPromises()
  expect(api.decideAction).toHaveBeenCalledExactlyOnceWith('ACT-1', 'approve')
})

it('blocks an opposite decision until an unknown decision outcome has been reconciled', async () => {
  const api = withRun(), wrapper = render(api)
  api.decideAction.mockRejectedValue(new TypeError('Network error'))
  await flushPromises()
  await button(wrapper, 'Reject proposal')
  await wrapper.get('[data-testid="confirm-decision"]').trigger('click')
  await flushPromises()
  expect(api.decideAction).toHaveBeenCalledExactlyOnceWith('ACT-1', 'reject')
  expect(wrapper.text()).toContain('Outcome not reconciled')
  const approve = wrapper.findAll('button').find(item => item.text() === 'Approve simulation')!
  expect(approve.attributes('disabled')).toBeDefined()
  await wrapper.get('[data-testid="refresh-proposals"]').trigger('click')
  await flushPromises()
  expect(approve.attributes('disabled')).toBeDefined()
  expect(api.decideAction).toHaveBeenCalledTimes(1)
})

it('does not display an old run response after the incident changes', async () => {
  const api = withRun()
  let resolve!: (value: RunResult) => void
  api.getRun.mockReturnValueOnce(new Promise<RunResult>(done => { resolve = done }))
  const wrapper = render(api)
  await flushPromises()
  api.listRuns.mockResolvedValue({ runs: [], total: 0, limit: 10, offset: 0 })
  await wrapper.setProps({ ticketId: 'INC-2' })
  resolve(run)
  await flushPromises()
  expect(wrapper.find('[data-testid="run-result"]').exists()).toBe(false)
  expect(api.startRun).not.toHaveBeenCalled()
})

it('disables writes if durable browser-tab recovery storage is unavailable', async () => {
  vi.spyOn(sessionStorage, 'getItem').mockImplementation(() => { throw new Error('Storage disabled') })
  const api = client(), wrapper = render(api)
  await flushPromises()
  await wrapper.get('[data-testid="run-consent"]').setValue(true)
  expect(wrapper.text()).toContain('Writes are disabled')
  expect(wrapper.get('[data-testid="start-run"]').attributes('disabled')).toBeDefined()
  expect(api.startRun).not.toHaveBeenCalled()
})
