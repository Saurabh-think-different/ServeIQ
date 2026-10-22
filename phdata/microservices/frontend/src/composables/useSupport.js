import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { apiRequest, friendlyError, getEvents, getMessages, streamIncident } from '../services/api.js'

const SESSION_KEY = 'serveiq.token'
const PAGE_SIZE = 50
const closed = status => status === 'resolved' || status === 'escalated'
const interrupted = reason => reason?.name === 'AbortError'
const ambiguous = reason => !reason?.status || reason.status >= 500

export function useSupport() {
  const token = ref('')
  const authBusy = ref(false)
  const authError = ref('')
  const profile = ref(null)
  const bootstrapLoading = ref(true)
  const incidents = ref([])
  const listLoading = ref(false)
  const hasMore = ref(false)
  const selectedIncident = ref(null)
  const messages = ref([])
  const threadLoading = ref(false)
  const newIncident = ref(false)
  const models = ref({ default_model: '', allowed_models: [] })
  const pendingModel = ref('')
  const creating = ref(false)
  const sending = ref(false)
  const streaming = ref(false)
  const error = ref('')
  const draft = ref('')
  const liveStatus = ref('')
  const pins = ref({})
  const pinnedModel = computed(() => {
    if (!selectedIncident.value) return ''
    return pins.value[selectedIncident.value.id] || messages.value.find(row => row.role === 'assistant' && row.model)?.model || ''
  })
  const modelLocked = computed(() => Boolean(pinnedModel.value))

  let disposed = false
  let epoch = 0
  let view = 0
  let threadRequest = 0
  let offset = 0
  let accountController = new AbortController()
  let viewController = new AbortController()
  let activeStream = null
  let pollTimer = null
  let creationAttempt = null
  const messageWrites = new Map()

  function session(value) {
    try {
      if (value) sessionStorage.setItem(SESSION_KEY, value)
      else sessionStorage.removeItem(SESSION_KEY)
    } catch { /* In-memory sessions still work when storage is unavailable. */ }
  }

  function urlIncident(id) {
    const url = new URL(window.location.href)
    if (id) url.searchParams.set('incident', id)
    else url.searchParams.delete('incident')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  }

  function stopPoll() {
    if (pollTimer !== null) clearTimeout(pollTimer)
    pollTimer = null
  }

  function stopView() {
    view += 1
    threadRequest += 1
    stopPoll()
    viewController.abort()
    viewController = new AbortController()
    activeStream?.controller.abort()
    activeStream = null
    threadLoading.value = false
    creating.value = false
    sending.value = false
    streaming.value = false
    liveStatus.value = ''
  }

  function clearAccount(clearUrl = true) {
    epoch += 1
    accountController.abort()
    accountController = new AbortController()
    stopView()
    token.value = ''
    profile.value = null
    incidents.value = []
    messages.value = []
    selectedIncident.value = null
    newIncident.value = false
    models.value = { default_model: '', allowed_models: [] }
    pendingModel.value = ''
    pins.value = {}
    hasMore.value = false
    listLoading.value = false
    bootstrapLoading.value = false
    authBusy.value = false
    authError.value = ''
    error.value = ''
    draft.value = ''
    offset = 0
    creationAttempt = null
    messageWrites.clear()
    session('')
    if (clearUrl) urlIncident(null)
  }

  function logout() { clearAccount() }

  function expire(account) {
    if (account !== epoch || disposed) return
    clearAccount()
    authError.value = 'Your session has expired. Please sign in again.'
  }

  function options(signal = accountController.signal, bearer = token.value) {
    const account = epoch
    return { token: bearer, signal, onUnauthorized: () => expire(account) }
  }

  const context = () => ({ account: epoch, view, id: selectedIncident.value?.id, signal: viewController.signal })
  const current = ctx => !disposed && ctx.account === epoch && ctx.view === view && ctx.id === selectedIncident.value?.id
  const validAccount = account => !disposed && account === epoch
  const busy = () => creating.value || sending.value || streaming.value || threadLoading.value || selectedIncident.value?.status === 'running'

  function defaultModel() {
    const allowed = models.value.allowed_models
    return allowed.includes(models.value.default_model) ? models.value.default_model : allowed[0] || ''
  }

  watch([pinnedModel, models], () => {
    pendingModel.value = pinnedModel.value || (models.value.allowed_models.includes(pendingModel.value) ? pendingModel.value : defaultModel())
  }, { flush: 'sync' })
  watch(pendingModel, value => {
    if (pinnedModel.value && value !== pinnedModel.value) pendingModel.value = pinnedModel.value
  }, { flush: 'sync' })

  function pinRows(id, rows) {
    const first = rows.find(row => row.role === 'assistant' && row.model)
    if (first && pins.value[id] !== first.model) pins.value = { ...pins.value, [id]: first.model }
  }

  function upsertIncident(row) {
    const index = incidents.value.findIndex(item => item.id === row.id)
    if (index < 0) incidents.value = [row, ...incidents.value]
    else incidents.value = incidents.value.map(item => item.id === row.id ? row : item)
    if (selectedIncident.value?.id === row.id) selectedIncident.value = row
  }

  async function loadMore() {
    if (!token.value || listLoading.value || !hasMore.value) return
    const account = epoch
    listLoading.value = true
    try {
      const page = await apiRequest(`/incidents?limit=${PAGE_SIZE}&offset=${offset}`, options())
      if (!validAccount(account)) return
      offset += page.length
      hasMore.value = page.length === PAGE_SIZE
      const existing = new Set(incidents.value.map(row => row.id))
      incidents.value = [...incidents.value, ...page.filter(row => !existing.has(row.id))]
    } catch (reason) {
      if (validAccount(account) && !interrupted(reason)) error.value = friendlyError(reason)
    } finally {
      if (validAccount(account)) listLoading.value = false
    }
  }

  async function snapshot(id, requestOptions) {
    const [incident, rows] = await Promise.all([
      apiRequest(`/incidents/${encodeURIComponent(id)}`, requestOptions),
      getMessages(id, requestOptions),
    ])
    // A small worker pool avoids opening one connection per message in long threads.
    let next = 0
    const augmented = new Array(rows.length)
    await Promise.all(Array.from({ length: Math.min(4, rows.length) }, async () => {
      while (next < rows.length) {
        const index = next++
        const events = await getEvents(rows[index].id, requestOptions)
        augmented[index] = { ...rows[index], events: events.sort((a, b) => a.sequence - b.sequence) }
      }
    }))
    return { incident, rows: augmented }
  }

  function schedulePoll(ctx = context()) {
    stopPoll()
    if (!current(ctx) || !ctx.id || selectedIncident.value.status !== 'running' || activeStream) return
    pollTimer = setTimeout(async () => {
      pollTimer = null
      if (!current(ctx)) return
      await refreshThread({ quiet: true })
    }, 2000)
  }

  async function refreshThread({ quiet = false } = {}) {
    const ctx = context()
    if (!token.value || !ctx.id) return false
    const request = ++threadRequest
    if (!quiet) threadLoading.value = true
    try {
      const result = await snapshot(ctx.id, options(ctx.signal))
      if (!current(ctx) || request !== threadRequest) return false
      pinRows(ctx.id, result.rows)
      const write = messageWrites.get(ctx.id)
      if (write) {
        const index = result.rows.findIndex(row => row.role === 'user' && row.content === write.content && !write.ids.has(row.id))
        if (index >= 0) {
          write.row = result.rows[index]
          write.uncertain = false
          if (result.rows.slice(index + 1).some(row => row.role === 'assistant')) messageWrites.delete(ctx.id)
        }
      }
      if (activeStream?.incidentId === ctx.id && !activeStream.terminal) {
        // A GET can race a just-persisted SSE event. Never remove live activity.
        const live = new Map(messages.value.map(row => [row.id, row]))
        result.rows = result.rows.map(row => {
          const prior = live.get(row.id)
          const events = new Map(row.events.map(event => [event.id, event]))
          for (const event of prior?.events || []) events.set(event.id, event)
          return { ...row, content: row.content || prior?.content || '', events: [...events.values()].sort((a, b) => a.sequence - b.sequence) }
        })
        for (const row of messages.value) {
          if (!result.rows.some(saved => saved.id === row.id)) result.rows.push(row)
        }
      }
      upsertIncident(result.incident)
      messages.value = result.rows
      streaming.value = result.incident.status === 'running'
      if (streaming.value) liveStatus.value ||= 'Run in progress. Checking for updates…'
      else if (!activeStream) liveStatus.value = ''
      return true
    } catch (reason) {
      if (current(ctx) && !interrupted(reason)) error.value = friendlyError(reason)
      return false
    } finally {
      if (current(ctx) && request === threadRequest) {
        threadLoading.value = false
        schedulePoll(ctx)
      }
    }
  }

  async function selectIncident(id) {
    if (!token.value || !id) return
    stopView()
    selectedIncident.value = incidents.value.find(row => row.id === id) || { id, status: 'open', title: '' }
    messages.value = []
    newIncident.value = false
    error.value = ''
    draft.value = ''
    pendingModel.value = pins.value[id] || defaultModel()
    urlIncident(id)
    await refreshThread()
  }

  function startNew() {
    if (!token.value) return
    stopView()
    selectedIncident.value = null
    messages.value = []
    draft.value = ''
    error.value = ''
    newIncident.value = true
    creationAttempt = null
    pendingModel.value = defaultModel()
    urlIncident(null)
  }

  async function cancelNew() {
    const id = selectedIncident.value?.id || incidents.value[0]?.id
    if (id) return selectIncident(id)
    stopView()
    selectedIncident.value = null
    messages.value = []
    draft.value = ''
    error.value = ''
    newIncident.value = false
    creationAttempt = null
    urlIncident(null)
  }

  async function bootstrap(bearer, account) {
    try {
      // Do not expose a workspace until the server has validated the stored JWT.
      const identity = await apiRequest('/users/me', options(accountController.signal, bearer))
      if (!validAccount(account)) return
      profile.value = { ...identity, name: null, stores: [] }
      token.value = bearer
      session(bearer)
      hasMore.value = true
      const profileTask = apiRequest('/data/me', options()).then(value => {
        if (validAccount(account)) profile.value = { ...identity, ...value }
      }).catch(reason => {
        // An authenticated identity is enough if the optional manager profile is unavailable.
        if (validAccount(account) && reason.status !== 401 && !interrupted(reason)) {
          profile.value = { ...identity, name: null, stores: [] }
        }
      })
      const modelTask = apiRequest('/llm/models', options()).then(value => {
        if (!validAccount(account)) return
        const allowed = [...new Set((value.allowed_models || []).filter(item => typeof item === 'string' && item.trim()))]
        models.value = { default_model: value.default_model || allowed[0] || '', allowed_models: allowed }
      }).catch(reason => {
        if (validAccount(account) && !interrupted(reason)) error.value = friendlyError(reason)
      })
      await Promise.all([profileTask, modelTask, loadMore()])
      if (!validAccount(account)) return
      const requested = new URL(window.location.href).searchParams.get('incident')
      if (requested || incidents.value.length) await selectIncident(requested || incidents.value[0].id)
    } catch (reason) {
      if (validAccount(account)) {
        clearAccount(false)
        authError.value = friendlyError(reason)
      }
    } finally {
      if (validAccount(account)) {
        bootstrapLoading.value = false
        authBusy.value = false
      }
    }
  }

  async function login({ email, password }) {
    if (authBusy.value || disposed) return
    clearAccount(false)
    const account = epoch
    authBusy.value = true
    bootstrapLoading.value = true
    try {
      const result = await apiRequest('/users/login', {
        method: 'POST', body: { email: String(email || '').trim(), password }, signal: accountController.signal,
      })
      if (!validAccount(account)) return
      if (!result.access_token) throw new Error('The server did not return a session token.')
      await bootstrap(result.access_token, account)
    } catch (reason) {
      if (validAccount(account) && !interrupted(reason)) authError.value = friendlyError(reason)
    } finally {
      if (validAccount(account)) {
        authBusy.value = false
        bootstrapLoading.value = false
      }
    }
  }

  function applyEvent(event, ctx, run) {
    if (!current(ctx) || activeStream !== run || (event.incident_id && event.incident_id !== ctx.id)) return
    if (event.message_id) {
      let row = messages.value.find(item => item.id === event.message_id)
      if (!row) {
        row = { id: event.message_id, incident_id: ctx.id, role: 'assistant', content: '', model: pins.value[ctx.id] || run.model, created_at: event.created_at || new Date().toISOString(), events: [] }
        messages.value = [...messages.value, row]
        row = messages.value.find(item => item.id === event.message_id)
      }
      if (row.role !== 'assistant') return
      pinRows(ctx.id, messages.value)
      const duplicate = row.events.some(item => event.id ? item.id === event.id : event.sequence != null && item.sequence === event.sequence)
      if (!duplicate) row.events = [...row.events, event].sort((a, b) => (a.sequence ?? Infinity) - (b.sequence ?? Infinity))
      const result = event.data?.result || event.data
      if (event.type === 'observation' && ['user_input', 'complete_task', 'escalate_to_l2'].includes(event.data?.tool) && typeof result?.message === 'string') {
        row.content = result.message
      }
      if (['done', 'error'].includes(event.type) && typeof event.message === 'string') row.content = event.message
    }
    if (!['done', 'error'].includes(event.type)) {
      streaming.value = true
      if (selectedIncident.value.status !== 'running') upsertIncident({ ...selectedIncident.value, status: 'running' })
    }
    if (event.type === 'thought' && event.data?.status) liveStatus.value = event.data.status
    if (event.type === 'action') liveStatus.value = `Working: ${String(event.data?.tool || 'support tools').replaceAll('_', ' ')}…`
    if (event.type === 'done' || event.type === 'error') {
      run.terminal = true
      streaming.value = false
      liveStatus.value = ''
      upsertIncident({ ...selectedIncident.value, status: event.type === 'error' ? 'failed' : event.status || 'waiting_for_user' })
      if (event.type === 'error') error.value = friendlyError(event.message || 'The run failed. Retry the existing request.')
    }
  }

  async function runIncident(ctx, model) {
    if (!current(ctx) || closed(selectedIncident.value.status)) return
    stopPoll()
    const run = { incidentId: ctx.id, model: pins.value[ctx.id] || model, controller: new AbortController(), terminal: false }
    activeStream = run
    streaming.value = true
    liveStatus.value = 'Starting the assistant…'
    error.value = ''
    try {
      const result = await streamIncident({ ...options(run.controller.signal), incidentId: ctx.id, model: run.model, onEvent: event => applyEvent(event, ctx, run) })
      if (current(ctx) && !result.terminal) {
        error.value = 'The connection ended before completion. Checking the saved run status…'
      }
    } catch (reason) {
      if (current(ctx) && !interrupted(reason)) error.value = friendlyError(reason)
    } finally {
      if (current(ctx) && activeStream === run) {
        activeStream = null
        // A lost connection is not a terminal result. Reconcile, then poll running work using GET only.
        const reconciled = await refreshThread({ quiet: true })
        if (current(ctx) && !reconciled && !run.terminal) {
          upsertIncident({ ...selectedIncident.value, status: 'running' })
          streaming.value = true
          liveStatus.value = 'Connection interrupted. Checking the saved run status…'
          schedulePoll(ctx)
        }
      }
    }
  }

  async function persistUser(ctx, content) {
    let write = messageWrites.get(ctx.id)
    if (write && write.content !== content && write.uncertain) {
      throw new Error('The previous message has not been confirmed. Refresh and retry that message first.')
    }
    if (!write || write.content !== content) {
      write = { content, ids: new Set(messages.value.map(row => row.id)), uncertain: false, row: null }
      messageWrites.set(ctx.id, write)
    }
    const saved = messages.value.find(row => row.role === 'user' && row.content === content && !write.ids.has(row.id))
    if (saved) write.row = saved
    if (write.row) return write.row
    // Without server idempotency keys, an ambiguous POST must be reconciled, not repeated.
    if (write.uncertain) throw new Error('The server has not confirmed the previous message. Refresh before retrying; it will not be posted twice.')
    write.uncertain = true
    try {
      const row = await apiRequest(`/incidents/${encodeURIComponent(ctx.id)}/messages`, {
        ...options(ctx.signal), method: 'POST', body: { content },
      })
      if (!validAccount(ctx.account)) return null
      write.row = { ...row, events: [] }
      write.uncertain = false
      if (current(ctx) && !messages.value.some(item => item.id === row.id)) messages.value = [...messages.value, write.row]
      return write.row
    } catch (reason) {
      if (!ambiguous(reason)) write.uncertain = false
      throw reason
    }
  }

  function chosenModel(requested) {
    const chosen = pinnedModel.value || requested || pendingModel.value || defaultModel()
    if (!chosen || (!pinnedModel.value && !models.value.allowed_models.includes(chosen))) {
      throw new Error('Choose an available assistant model before starting a run.')
    }
    return chosen
  }

  async function recoverCreation(attempt, ctx) {
    for (let cursor = 0; cursor <= 1000000; cursor += 100) {
      const page = await apiRequest(`/incidents?limit=100&offset=${cursor}`, options(ctx.signal))
      if (!current(ctx)) return
      const matches = page.filter(row => !attempt.ids.has(row.id) && row.title === attempt.title && row.description === attempt.description && Date.parse(row.created_at) >= attempt.started - 5000)
      if (matches.length === 1) {
        attempt.incident = matches[0]
        attempt.uncertain = false
        return
      }
      if (matches.length > 1) break
      if (page.length < 100) break
    }
    throw new Error('Incident creation could not be confirmed. Refresh the incident list before retrying; another incident will not be created automatically.')
  }

  async function createIncident({ title, description, model } = {}) {
    if (!token.value || busy()) return
    title = String(title || '').trim()
    description = String(description || '').trim()
    if (!title || title.length > 200 || !description || description.length > 50000) {
      error.value = 'Enter a title (up to 200 characters) and description (up to 50,000 characters).'
      return
    }
    let ctx = context()
    creating.value = true
    error.value = ''
    try {
      const chosen = chosenModel(model)
      if (creationAttempt && (creationAttempt.title !== title || creationAttempt.description !== description)) {
        throw new Error('Finish or cancel the partially created incident before changing its title or description.')
      }
      const attempt = creationAttempt ||= { title, description, model: chosen, ids: new Set(incidents.value.map(row => row.id)), started: Date.now(), incident: null, uncertain: false }
      if (!attempt.incident && attempt.uncertain) await recoverCreation(attempt, ctx)
      if (!current(ctx)) return
      if (!attempt.incident) {
        attempt.uncertain = true
        try {
          const row = await apiRequest('/incidents', { ...options(ctx.signal), method: 'POST', body: { title, description } })
          if (!current(ctx)) return
          attempt.incident = row
          attempt.uncertain = false
        } catch (reason) {
          if (!ambiguous(reason)) attempt.uncertain = false
          throw reason
        }
      }
      if (!current(ctx)) return
      const id = attempt.incident.id
      if (selectedIncident.value?.id !== id) {
        selectedIncident.value = attempt.incident
        messages.value = []
        ctx = context()
      }
      upsertIncident(attempt.incident)
      urlIncident(id)
      if (!await refreshThread({ quiet: true }) || !current(ctx)) return
      if (closed(selectedIncident.value.status) || selectedIncident.value.status === 'running') return
      if (!messages.value.some(row => row.role === 'user')) await persistUser(ctx, attempt.description)
      if (!current(ctx)) return
      newIncident.value = false
      pendingModel.value = pinnedModel.value || attempt.model
      await runIncident(ctx, pendingModel.value)
    } catch (reason) {
      if (current(ctx) && !interrupted(reason)) error.value = friendlyError(reason)
    } finally {
      if (current(ctx)) creating.value = false
    }
  }

  async function sendReply(content = draft.value) {
    if (!token.value || busy() || !selectedIncident.value || newIncident.value || closed(selectedIncident.value.status)) return
    content = String(content || '').trim()
    if (!content || content.length > 50000) {
      error.value = 'Enter a message between 1 and 50,000 characters.'
      return
    }
    const ctx = context()
    sending.value = true
    error.value = ''
    try {
      const model = chosenModel()
      if (!await refreshThread({ quiet: true }) || !current(ctx)) return
      if (closed(selectedIncident.value.status) || selectedIncident.value.status === 'running') return
      const last = messages.value.at(-1)
      if (!(last?.role === 'user' && last.content === content)) await persistUser(ctx, content)
      if (!current(ctx)) return
      draft.value = ''
      await runIncident(ctx, model)
    } catch (reason) {
      if (current(ctx) && !interrupted(reason)) error.value = friendlyError(reason)
    } finally {
      if (current(ctx)) sending.value = false
    }
  }

  async function retryRun() {
    if (!token.value || busy() || !selectedIncident.value || closed(selectedIncident.value.status)) return
    const ctx = context()
    sending.value = true
    error.value = ''
    try {
      if (!await refreshThread({ quiet: true }) || !current(ctx)) return
      if (!['open', 'failed'].includes(selectedIncident.value.status)) return
      if (!messages.value.some(row => row.role === 'user')) throw new Error('The initial message has not been saved. Submit the incident form to finish creating it.')
      await runIncident(ctx, chosenModel())
    } catch (reason) {
      if (current(ctx) && !interrupted(reason)) error.value = friendlyError(reason)
    } finally {
      if (current(ctx)) sending.value = false
    }
  }

  onMounted(() => {
    let stored = ''
    try { stored = sessionStorage.getItem(SESSION_KEY) || '' } catch { /* Storage may be unavailable. */ }
    if (stored) {
      authBusy.value = true
      bootstrap(stored, epoch)
    } else bootstrapLoading.value = false
  })

  onUnmounted(() => {
    disposed = true
    epoch += 1
    accountController.abort()
    stopView()
    messageWrites.clear()
    creationAttempt = null
  })

  return {
    token, authBusy, authError, profile, bootstrapLoading, incidents, listLoading, hasMore,
    selectedIncident, messages, threadLoading, newIncident, models, pendingModel, pinnedModel,
    modelLocked, creating, sending, streaming, error, draft, liveStatus,
    login, logout, loadMore, selectIncident, startNew, cancelNew, createIncident,
    sendReply, retryRun, refreshThread,
  }
}
