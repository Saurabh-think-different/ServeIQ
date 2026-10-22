import { vi } from 'vitest'
import type { ApiClient } from '../src/api/types'

export function workflowMock() {
  return {
    startRun: vi.fn<ApiClient['startRun']>(),
    listRuns: vi.fn<ApiClient['listRuns']>().mockResolvedValue({ runs: [], total: 0, limit: 10, offset: 0 }),
    getRun: vi.fn<ApiClient['getRun']>(),
    listActions: vi.fn<ApiClient['listActions']>().mockResolvedValue({ run_id: 'run-1', actions: [] }),
    decideAction: vi.fn<ApiClient['decideAction']>(),
    listAudit: vi.fn<ApiClient['listAudit']>().mockResolvedValue({ events: [], total: 0, limit: 10, offset: 0 }),
  }
}
