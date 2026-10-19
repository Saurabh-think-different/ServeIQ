import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createApiClient } from '../src/api/client'

const response = (status: number, body: unknown) => ({ ok: status >= 200 && status < 300, status, json: async () => body })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('operator API client', () => {
  it('keeps the credential only in headers and uses same-origin paths', async () => {
    const fetcher = vi.fn().mockResolvedValue(response(200, { incidents: [] }))
    vi.stubGlobal('fetch', fetcher)
    await createApiClient('local-test-token').listIncidents({ limit: 25, category: 'Printers & POS' })
    const [url, options] = fetcher.mock.calls[0]
    expect(url).toBe('/api/incidents?limit=25&category=Printers+%26+POS')
    expect(url).not.toContain('token')
    expect(options.headers.Authorization).toBe('Bearer local-test-token')
    expect(options.credentials).toBe('omit')
    expect(options.redirect).toBe('error')
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
  })
  it('encodes incident identifiers and sends typed JSON imports', async () => {
    const fetcher = vi.fn().mockResolvedValue(response(200, {}))
    vi.stubGlobal('fetch', fetcher)
    const api = createApiClient('token')
    await api.getContext('INC/A?#')
    await api.ingest('structured')
    expect(fetcher.mock.calls[0][0]).toBe('/api/incident-context/INC%2FA%3F%23')
    expect(fetcher.mock.calls[1][1].body).toBe('{"target":"structured"}')
  })
  it('disconnects on unauthorized responses without exposing response text', async () => {
    const callback = vi.fn()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(401, { detail: 'secret server text' })))
    await expect(createApiClient('token', callback).getIncident('INC')).rejects.toMatchObject({ status: 401 })
    expect(callback).toHaveBeenCalledOnce()
  })
  it('uses field locations rather than sensitive Pydantic input values', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(422, { detail: [{ loc: ['body', 'description'], input: 'secret text', msg: 'secret' }] })))
    await expect(createApiClient('token').createIncident({ description: '' })).rejects.toThrow('Check these fields: description.')
  })
  it('does not confuse a partial pipeline report with HTTP failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(200, { status: 'partial', stages: [] })))
    expect((await createApiClient('token').ingest('all')).status).toBe('partial')
  })
  it('handles HTML proxy failures without exposing arbitrary body content', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => { throw new Error('HTML') } }))
    await expect(createApiClient('token').getIncident('INC')).rejects.toThrow('Service unavailable')
  })
  it('reports unknown write outcome on network interruption', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('credential-bearing transport failure')))
    await expect(createApiClient('token').ingest('all')).rejects.toThrow('operation may have completed')
  })
  it('revokes its own requests when disconnected and cannot be reused', async () => {
    let signal: AbortSignal | undefined
    vi.stubGlobal('fetch', vi.fn((_url, options) => {
      signal = options.signal
      return new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(new Error('abort'))))
    }))
    const api = createApiClient('token')
    const pending = api.listIncidents()
    api.disconnect()
    expect(signal?.aborted).toBe(true)
    await expect(pending).rejects.toBeInstanceOf(ApiError)
    await expect(api.listIncidents()).rejects.toMatchObject({ status: 401 })
  })
})
