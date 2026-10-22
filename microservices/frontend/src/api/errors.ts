import { ApiError } from './client'

export function operationError(error: unknown, area: string): string {
  const status = error instanceof ApiError ? error.status : undefined
  if (status === 401) return 'Session ended. Reconnect before continuing.'
  if (status === 409) return `${area} conflicts with current server state. Refresh the relevant records; a stale proposal may require a new run. For knowledge retrieval, ask the operator to rebuild the incompatible index.`
  if (status === 503) return `${area} unavailable (503). Ask the operator to check backend storage, provider configuration and knowledge index readiness before retrying.`
  if (status === 404) return `${area} was not found. Refresh the incident or run list.`
  if (status === 422) return `${area} rejected. Check the submitted fields and query bounds.`
  return `${area} could not be confirmed. Check the connection and refresh before retrying.`
}

export function ambiguousWrite(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status === 0 || error.status >= 500
}
