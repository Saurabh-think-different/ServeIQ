import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App.vue'

const queue = { incidents: [], total: 0, limit: 25, offset: 0 }
const json = (body: unknown, status = 200) => ({ ok: status === 200, status, json: async () => body })
let wrapper: ReturnType<typeof mount> | undefined

afterEach(() => { wrapper?.unmount(); wrapper = undefined; history.replaceState(null, '', '/'); window.localStorage.clear(); window.sessionStorage.clear(); vi.unstubAllGlobals() })

async function connect() {
  wrapper = mount(App)
  await wrapper.get('input[type="password"]').setValue('operator-test-token')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

describe('memory-only operator session', () => {
  it('validates protected access and opens the real empty queue', async () => {
    const fetcher = vi.fn().mockResolvedValue(json(queue))
    vi.stubGlobal('fetch', fetcher)
    await connect()
    expect(fetcher.mock.calls[0][0]).toBe('/api/incidents?limit=1&offset=0')
    expect(wrapper!.text()).toContain('Incident queue')
    expect(wrapper!.find('input[type="password"]').exists()).toBe(false)
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
  })
  it('disconnects and clears incident details from the rendered application', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(queue)))
    await connect()
    const disconnect = wrapper!.findAll('button').find(button => button.text() === 'Disconnect')!
    await disconnect.trigger('click')
    expect(wrapper!.text()).toContain('Open your workspace')
    expect((wrapper!.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper!.find('nav').exists()).toBe(false)
  })
  it('rejects bad credentials without presenting a successful connection', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'no' }, 401)))
    await connect()
    expect(wrapper!.text()).toContain('Open your workspace')
    expect(wrapper!.find('nav').exists()).toBe(false)
    expect(wrapper!.get('[role="alert"]').text()).not.toContain('operator-test-token')
  })
  it('does not fake connection when the backend is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    await connect()
    expect(wrapper!.get('[role="alert"]').text()).toContain('Connection interrupted')
    expect(wrapper!.find('nav').exists()).toBe(false)
  })
  it('shows honest architecture scope rather than fabricated telemetry metrics', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(queue)))
    await connect()
    const about = wrapper!.findAll('nav a').find(link => link.text().includes('About'))!
    await about.trigger('click')
    expect(wrapper!.text()).toContain('Intentionally not claimed')
    expect(wrapper!.text()).toContain('No live device telemetry')
    expect(wrapper!.text()).not.toContain('91%')
  })
})
