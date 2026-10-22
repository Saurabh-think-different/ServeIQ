const BASE = '/api'

export function friendlyError(value) {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(item => {
    const field = Array.isArray(item?.loc) ? item.loc.filter(part => part !== 'body').join('.') : ''
    const message = friendlyError(item?.msg ?? item)
    return field ? `${field}: ${message}` : message
  }).filter(Boolean).join('; ')
  if (value && typeof value === 'object') {
    return friendlyError(value.detail ?? value.message ?? value.error ?? 'The request could not be completed.')
  }
  return 'The request could not be completed. Please try again.'
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(friendlyError(detail))
    this.name = 'ApiError'
    this.status = status
  }
}

async function checkedFetch(path, { token, body, method = 'GET', signal, onUnauthorized, headers = {} } = {}) {
  const response = await fetch(`${BASE}${path}`, {
    method,
    credentials: 'omit',
    signal,
    headers: {
      Accept: 'application/json',
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })
  if (!response.ok) {
    let detail
    try { detail = await response.json() } catch { detail = response.statusText || 'Request failed.' }
    if (response.status === 401) onUnauthorized?.()
    throw new ApiError(response.status, detail)
  }
  return response
}

export async function apiRequest(path, options) {
  const response = await checkedFetch(path, options)
  return response.status === 204 ? null : response.json()
}

async function allPages(path, limit, options) {
  const rows = []
  for (let offset = 0; offset <= 1000000; offset += limit) {
    const page = await apiRequest(`${path}?limit=${limit}&offset=${offset}`, options)
    if (!Array.isArray(page)) throw new Error('The server returned an invalid list.')
    rows.push(...page)
    if (page.length < limit) return rows
  }
  throw new Error('This conversation is too large to load completely.')
}

export const getMessages = (incidentId, options) => allPages(`/incidents/${encodeURIComponent(incidentId)}/messages`, 100, options)
// 200 stays below the event route's maximum (500) and bounds each response.
export const getEvents = (messageId, options) => allPages(`/messages/${encodeURIComponent(messageId)}/events`, 200, options)

export async function streamIncident({ token, incidentId, model, onEvent, signal, onUnauthorized }) {
  const response = await checkedFetch('/llm/stream', {
    token, method: 'POST', signal, onUnauthorized,
    body: { incident_id: incidentId, ...(model ? { model } : {}) },
    headers: { Accept: 'text/event-stream' },
  })
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    await response.body?.cancel()
    throw new Error('The server did not return an event stream. Refresh to check the run status.')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const seen = new Set()
  let buffer = ''
  let type = 'message'
  let data = []
  let eventId
  let terminal = false
  let ended = false

  function dispatch() {
    if (data.length) {
      const payload = JSON.parse(data.join('\n'))
      const id = payload.id || eventId
      const kind = type === 'message' ? payload.type || type : type
      if (!id || !seen.has(id)) {
        if (id) seen.add(id)
        onEvent?.({ ...payload, ...(id ? { id } : {}), type: kind })
      }
      if (kind === 'done' || kind === 'error') terminal = true
    }
    type = 'message'
    data = []
    eventId = undefined
  }

  function line(value) {
    if (value === '') return dispatch()
    if (value.startsWith(':')) return
    const colon = value.indexOf(':')
    const field = colon < 0 ? value : value.slice(0, colon)
    const content = colon < 0 ? '' : value.slice(colon + 1).replace(/^ /, '')
    if (field === 'event') type = content || 'message'
    if (field === 'data') data.push(content)
    if (field === 'id' && !content.includes('\0')) eventId = content
  }

  function consume(final = false) {
    while (buffer.length) {
      const index = buffer.search(/[\r\n]/)
      if (index < 0) break
      if (!final && buffer[index] === '\r' && index === buffer.length - 1) break
      const width = buffer[index] === '\r' && buffer[index + 1] === '\n' ? 2 : 1
      line(buffer.slice(0, index))
      buffer = buffer.slice(index + width)
    }
    // An unterminated frame is deliberately not dispatched at EOF.
  }

  try {
    while (!terminal) {
      const chunk = await reader.read()
      if (chunk.done) {
        ended = true
        buffer += decoder.decode()
        consume(true)
        break
      }
      buffer += decoder.decode(chunk.value, { stream: true })
      consume()
    }
    return { terminal }
  } finally {
    if (!ended) {
      try { await reader.cancel() } catch { /* The connection may already be closed. */ }
    }
    reader.releaseLock()
  }
}
