import type { ApiClient, IncidentContext, IncidentPage, IncidentResult, PipelineReport, SearchResponse } from './types'

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) { super(message); this.name = 'ApiError' }
}

export function createApiClient(token: string, onUnauthorized: () => void = () => {}): ApiClient & { disconnect(): void } {
  let active = true
  const controllers = new Set<AbortController>()
  async function request<T>(path: string, body?: unknown): Promise<T> {
    if (!active) throw new ApiError(401, 'Session ended. Reconnect to continue.')
    const controller = new AbortController()
    controllers.add(controller)
    const timeout = setTimeout(() => controller.abort(), 120_000)
    try {
      const response = await fetch(`/api${path}`, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: controller.signal, credentials: 'omit', redirect: 'error', cache: 'no-store',
      })
      if (response.status === 401) { onUnauthorized(); throw new ApiError(401, 'Access expired or the operator token was rejected. Reconnect.') }
      if (!response.ok) {
        let message = response.status === 503 ? 'Service unavailable. Check server configuration and try again.' : `Request failed (${response.status}).`
        if (response.status === 409) message = 'Request conflicts with existing data or another operation. Refresh and retry.'
        if (response.status === 404) message = 'The requested incident or resource was not found.'
        if (response.status === 422) {
          message = 'Check the submitted fields. The server rejected this request.'
          try {
            const payload = await response.json()
            if (Array.isArray(payload.detail)) {
              const fields = payload.detail.map((item: { loc?: unknown[] }) => item.loc?.filter(part => part !== 'body').join('.')).filter(Boolean)
              if (fields.length) message = `Check these fields: ${[...new Set(fields)].join(', ')}.`
            }
          } catch { /* Error bodies may be HTML from the proxy. */ }
        }
        throw new ApiError(response.status, message)
      }
      return await response.json() as T
    } catch (error) {
      if (error instanceof ApiError) throw error
      throw new ApiError(0, 'Connection interrupted or request timed out. The operation may have completed; refresh before retrying.')
    } finally {
      clearTimeout(timeout)
      controllers.delete(controller)
    }
  }
  const api: ApiClient & { disconnect(): void } = {
    listIncidents(options = {}) {
      const query = new URLSearchParams()
      for (const [key, value] of Object.entries(options)) if (value !== undefined && value !== '') query.set(key, String(value))
      return request<IncidentPage>(`/incidents?${query}`)
    },
    createIncident: input => request<IncidentResult>('/incidents', input),
    getIncident: id => request<IncidentResult>(`/incidents/${encodeURIComponent(id)}`),
    getContext: id => request<IncidentContext>(`/incident-context/${encodeURIComponent(id)}`),
    searchKnowledge: input => request<SearchResponse>('/knowledge/search', input),
    ingest: target => request<PipelineReport>('/ingestions', { target }),
    disconnect() { active = false; for (const controller of controllers) controller.abort(); controllers.clear(); token = '' },
  }
  return api
}
