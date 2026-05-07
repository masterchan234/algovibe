import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { io } from 'socket.io-client'
import { useNotifications } from '../composables/useNotifications'
import { useAuth } from '../composables/useAuth'

// ── Battery Saver helpers ─────────────────────────────────────────────────────

/** True when the viewport is mobile-sized (<768px) */
function _isMobile() {
  return typeof window !== 'undefined' && window.innerWidth < 768
}

/** Max pnlHistory items — fewer on mobile to reduce memory */
function _maxHistory() {
  return _isMobile() ? 75 : 300
}

/**
 * Throttle wrapper: ensures `fn` is called at most once per `intervalMs`.
 * On mobile the default interval is 4000 ms; on desktop it follows whatever
 * the caller specifies (or is called immediately).
 */
function _makeThrottle(intervalMs) {
  let _lastCall = 0
  return function (fn) {
    const now = Date.now()
    if (now - _lastCall >= intervalMs) {
      _lastCall = now
      fn()
    }
  }
}

export const useTradeStore = defineStore('trade', () => {
  const { notify } = useNotifications()
  const { token: authToken, logout: authLogout } = useAuth()

  // Connection state
  const connected = ref(false)
  const socket = ref(null)
  const lastSocketStatsAt = ref(0)
  const statsSource = ref('none')

  // Force-logout flag: set to true when the server invalidates this session
  const forceLogout = ref(false)

  // Visibility API — pause heavy updates when tab/screen is hidden
  const _pageVisible = ref(true)
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      _pageVisible.value = !document.hidden
    })
  }

  // Throttle gate for mobile UI re-renders (4 s on mobile, 0 ms on desktop)
  const _throttleStats = _makeThrottle(_isMobile() ? 4000 : 0)
  const _throttleTrades = _makeThrottle(_isMobile() ? 4000 : 0)

  // Stats
  const stats = reactive({
    daily_pnl: 0,
    all_time_pnl_dry: 0,
    available_idr: 0,
    idr_used: 0,
    total_equity: 0,
    utilization: 0,
    win_rate: 0,
    active_positions: 0,
    unrealized_pnl: 0,
    regime: 'CHOPPY',
    regime_leader: '',
    market_sentiment_index: 50,
    latest_llm_insight: '',
    hero_ai_insight: 'Waiting for AI analysis...',
    hero_ai_insights: [],
    active_reasoning: '',
    equity_reasoning: '',
    equity_action_plan: '',
    first_trade_ts: 0,
    alpha_contributor: '',
    best_trade: 0,
    intensity: 0,
    asset_contributions: [],
    recent_trades: [],
    trades: 0,
    history_count: 0,
    open_count: 0,
    dry_run: true,
    dry_run_loose_gates: false,
    manual_exit_max_net_pct: 0.001,
    // BTC Condition
    btc_price: 0,
    btc_change_24h: 0,
    btc_ch_5m: 0,
    btc_ch_15m: 0,
    btc_ch_1h: 0,
    btc_trend: 'N/A',
    btc_score: 50,
    btc_weakening: false,
    btc_crash: false,
    // Momentum Equity Protection
    momentum_state: 'STALLING',
    net_pnl_velocity: 0,
    consecutive_losses: 0,
    entry_gate_state: '',
    entry_gate_reason: '',
    entry_gate_pair: '',
    entry_gate_at: '',
  })

  // Open positions (trades)
  const positions = ref([])

  // Approval gate: pending action waiting for Supreme Commander decision
  const pendingAction = ref(null)

  // Strategic Break: full-screen blocking popup when daily target is reached
  const strategicBreak = ref(null)

  // Bracket Exit Confirm: popup when bot wants to exit with projected negative PnL
  const bracketExitConfirm = ref(null)

  // Activity feed (logs)
  const activityFeed = ref([])
  const MAX_FEED_ITEMS = 100

  // LiteRT-LM Exit Decisions
  const llmDecisions = ref([])
  const MAX_LLM_ITEMS = 40

  // Profit history for line chart: [{time, pnl}]
  // Capacity is adaptive: 75 pts on mobile, 300 on desktop
  const pnlHistory = ref([])

  // Radar data
  const radarPairs = ref([])

  function _selectedModeDryRun() {
    try {
      const mode = localStorage.getItem('algovibe_mode')
      if (mode === 'live') return false
      if (mode === 'dry') return true
    } catch {}
    return null
  }

  function _normalizeLogItem(item, index = 0) {
    const createdAt = item?.created_at || new Date().toISOString()
    const pair = item?.pair || 'SYSTEM'
    const side = item?.side || item?.type || 'INFO'
    const message = item?.message || item?.price || item?.status || '-'
    const status = item?.status || item?.level || 'INFO'
    const id = item?.id || `${createdAt}|${pair}|${side}|${message}|${index}`

    return {
      ...item,
      id,
      created_at: createdAt,
      pair,
      side,
      status,
      message,
    }
  }

  function _addFeedItems(items) {
    const normalized = items.map((item, idx) => _normalizeLogItem(item, idx))
    for (const item of normalized) {
      // Avoid duplicates by id
      if (!activityFeed.value.find(x => x.id === item.id)) {
        activityFeed.value.unshift(item)
        
        // Trigger Alerts for new items
        const side = (item.side || '').toUpperCase()
        const msg = (item.message || '').toUpperCase()
        
        if (side === 'BUY') {
          // notify({ title: 'NEW BUY ORDER', message: `${item.pair}: ${item.message}`, type: 'success' })
        } else if (side === 'SELL') {
          // notify({ title: 'NEW SELL ORDER', message: `${item.pair}: ${item.message}`, type: 'warning' })
        } else if (msg.includes('WHALE')) {
          // notify({ title: 'WHALE ALERT', message: item.message, type: 'error' })
        } else if (msg.includes('FAKE PUMP')) {
          // notify({ title: 'FAKE PUMP WARNING', message: item.message, type: 'warning' })
        } else if (side.includes('ORACLE') || side.includes('AI')) {
          // notify({ title: 'ORACLE INSIGHT', message: item.message, type: 'ai' })
        }
      }
    }
    if (activityFeed.value.length > MAX_FEED_ITEMS) {
      activityFeed.value = activityFeed.value.slice(0, MAX_FEED_ITEMS)
    }
  }

  function _recordPnl(pnl) {
    // Skip recording entirely when the page is hidden (Visibility API — battery saver)
    if (!_pageVisible.value) return

    const rounded = Math.round(pnl)
    const now = new Date()
    const timeStr = now.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    const maxPts = _maxHistory()

    // Always record two points if empty to ensure ECharts draws a line immediately
    if (pnlHistory.value.length === 0) {
      const prevNow = new Date(now.getTime() - 1000)
      const prevTimeStr = prevNow.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      pnlHistory.value.push({ time: prevTimeStr, pnl: rounded })
      pnlHistory.value.push({ time: timeStr, pnl: rounded })
      return
    }

    // Skip only if value is same and we have enough data to avoid spam
    const last = pnlHistory.value[pnlHistory.value.length - 1]
    if (last && last.pnl === rounded && pnlHistory.value.length > 20) return
    pnlHistory.value.push({ time: timeStr, pnl: rounded })
    if (pnlHistory.value.length > maxPts) {
      pnlHistory.value = pnlHistory.value.slice(-maxPts)
    }
  }

  async function connect() {
    if (socket.value) {
      return socket.value
    }

    const backendUrl = import.meta.env.DEV
      ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
      : window.location.origin

    const s = io(backendUrl, {
      path: '/socket.io',
      // Send JWT in auth so the server can validate the session on connect
      auth: authToken.value ? { token: authToken.value } : {},
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 10000,
    })
    
    socket.value = s

    s.on('connect', () => { connected.value = true })
    s.on('disconnect', () => {
      connected.value = false
      if (statsSource.value === 'socket') {
        statsSource.value = 'none'
      }
      socket.value = null
    })

    // ── Single-Login Enforcement: force-logout this session if another login occurred ──
    s.on('FORCE_LOGOUT', () => {
      notify({
        title: 'Session Terminated',
        message: 'Anda telah login dari perangkat lain. Sesi ini dinonaktifkan.',
        type: 'error',
        duration: 8000,
      })
      authLogout()
      connected.value = false
      forceLogout.value = true
    })

    s.on('stats_update', (data) => {
      // Throttle UI re-renders on mobile; always allow on desktop
      _throttleStats(() => {
        // Skip non-critical updates when page is hidden (Visibility API)
        if (!_pageVisible.value) return
        const selectedDry = _selectedModeDryRun()
        if (selectedDry !== null && typeof data?.dry_run === 'boolean' && data.dry_run !== selectedDry) {
          return
        }
        lastSocketStatsAt.value = Date.now()
        statsSource.value = 'socket'
        Object.assign(stats, {
          daily_pnl: data.daily_pnl ?? 0,
          all_time_pnl_dry: data.all_time_pnl_dry ?? 0,
          available_idr: data.available_idr ?? 0,
          idr_used: data.idr_used ?? 0,
          total_equity: data.total_equity ?? 0,
          utilization: data.utilization ?? 0,
          win_rate: data.win_rate ?? 0,
          active_positions: data.active_positions ?? 0,
          unrealized_pnl: data.unrealized_pnl ?? 0,
          regime: data.regime ?? 'CHOPPY',
          regime_leader: data.regime_leader ?? '',
          market_sentiment_index: data.market_sentiment_index ?? 50,
          latest_llm_insight: data.latest_llm_insight ?? '',
          hero_ai_insight: data.hero_ai_insight ?? 'Waiting for AI analysis...',
          hero_ai_insights: Array.isArray(data.hero_ai_insights) ? data.hero_ai_insights : [],
          active_reasoning: data.active_reasoning ?? '',
          equity_reasoning: data.equity_reasoning ?? '',
          equity_action_plan: data.equity_action_plan ?? '',
          first_trade_ts: data.first_trade_ts ?? 0,
          alpha_contributor: data.alpha_contributor ?? '',
          best_trade: data.best_trade ?? 0,
          intensity: data.intensity ?? 0,
          asset_contributions: data.asset_contributions ?? [],
          recent_trades: data.recent_trades ?? [],
          trades: data.trades ?? 0,
          history_count: data.history_count ?? 0,
          open_count: data.open_count ?? 0,
          dry_run: data.dry_run ?? true,
          dry_run_loose_gates: data.dry_run_loose_gates ?? false,
          manual_exit_max_net_pct: data.manual_exit_max_net_pct ?? 0.001,
          btc_price: data.btc_price ?? 0,
          btc_change_24h: data.btc_change_24h ?? 0,
          btc_ch_5m: data.btc_ch_5m ?? 0,
          btc_ch_15m: data.btc_ch_15m ?? 0,
          btc_ch_1h: data.btc_ch_1h ?? 0,
          btc_trend: data.btc_trend ?? 'N/A',
          btc_score: data.btc_score ?? 50,
          btc_weakening: data.btc_weakening ?? false,
          btc_crash: data.btc_crash ?? false,
          momentum_state: data.momentum_state ?? 'STALLING',
          net_pnl_velocity: data.net_pnl_velocity ?? 0,
          consecutive_losses: data.consecutive_losses ?? 0,
          entry_gate_state: data.entry_gate_state ?? '',
          entry_gate_reason: data.entry_gate_reason ?? '',
          entry_gate_pair: data.entry_gate_pair ?? '',
          entry_gate_at: data.entry_gate_at ?? '',
        })
        _recordPnl((data.daily_pnl ?? 0) + (data.unrealized_pnl ?? 0))
      })
    })

    s.on('trades_update', (data) => {
      _throttleTrades(() => {
        const selectedDry = _selectedModeDryRun()
        const payloadItems = Array.isArray(data) ? data : (Array.isArray(data?.items) ? data.items : null)
        const payloadDry = typeof data?.dry_run === 'boolean' ? data.dry_run : null

        if (selectedDry !== null && payloadDry !== null && payloadDry !== selectedDry) {
          return
        }

        if (Array.isArray(payloadItems)) {
          positions.value = payloadItems
        }
      })
    })

    s.on('logs_update', (data) => {
      if (Array.isArray(data)) {
        _addFeedItems(data)
      }
    })

    s.on('llm_decisions_update', (data) => {
      if (Array.isArray(data)) {
        llmDecisions.value = data.slice(0, MAX_LLM_ITEMS)
      }
    })

    s.on('radar_update', (data) => {
      if (Array.isArray(data)) {
        radarPairs.value = data
      }
    })

    s.on('regime_update', (data) => {
      if (data && data.regime) {
        stats.regime = data.regime
        if (data.market_sentiment_index !== undefined) stats.market_sentiment_index = data.market_sentiment_index
        if (data.regime_leader !== undefined) stats.regime_leader = data.regime_leader
      }
    })

    // ── Supreme Commander Approval Gate events ───────────────────────────
    s.on('pending_action', (data) => {
      if (data && data.action_id) {
        pendingAction.value = data
      }
    })

    s.on('pending_action_cleared', () => {
      pendingAction.value = null
    })

    // ── Strategic Break events ────────────────────────────────────────────
    s.on('strategic_break', (data) => {
      console.log("Strategic Break Data Received:", data)
      // Only update if popup is not already showing (prevent UI flickering from
      // any duplicate emissions that slip through the server-side guard).
      if (!strategicBreak.value && data && data.triggered_at) {
        strategicBreak.value = data
      }
    })

    s.on('strategic_break_cleared', () => {
      strategicBreak.value = null
    })

    // ── Bracket Exit Confirm: negative-PnL exit requiring user approval ──
    s.on('bracket_exit_confirm', (data) => {
      if (data && data.exit_id && (data.projected_pnl ?? 0) < 0) {
        bracketExitConfirm.value = data
      }
    })

    s.on('bracket_exit_confirm_cleared', () => {
      bracketExitConfirm.value = null
    })

    s.on('toast_notification', (data) => {
      notify({
        title: data.title || 'Notification',
        message: data.message || '',
        type: data.type || 'info',
        duration: 6000,
      })
    })

    return s
  }

  function markApiStatsUsed(staleAfterMs = 7000) {
    const now = Date.now()
    if (!lastSocketStatsAt.value || now - lastSocketStatsAt.value > staleAfterMs) {
      statsSource.value = 'api'
    }
  }

  /**
   * Checks the backend for an active HARVEST_ONLY latch on page load/refresh.
   * If the session is already latched and a PENDING strategic_break payload
   * exists, the modal is restored immediately without waiting for a socket event.
   */
  async function checkSessionStatus() {
    const backendUrl = import.meta.env.DEV
      ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
      : window.location.origin
    const { getAuthHeaders, isAuthenticated } = useAuth()
    // Skip if not logged in — avoids console 401 on unauthenticated page loads
    if (!isAuthenticated.value) return
    try {
      const res = await fetch(`${backendUrl}/api/session/status`, {
        method: 'GET',
        credentials: 'include',
        headers: getAuthHeaders(),
      })
      if (!res.ok) return
      const data = await res.json()
      if (
        (data.session_status === 'HARVEST_ONLY' || data.is_latched) &&
        data.strategic_break &&
        data.strategic_break.triggered_at &&
        !strategicBreak.value
      ) {
        strategicBreak.value = data.strategic_break
      }
    } catch {
      // Silently ignore — the socket will deliver the event once connected.
    }
  }

  return {
    connected,
    stats,
    positions,
    pendingAction,
    strategicBreak,
    bracketExitConfirm,
    activityFeed,
    llmDecisions,
    pnlHistory,
    radarPairs,
    connect,
    checkSessionStatus,
    statsSource,
    markApiStatsUsed,
    socket,
    forceLogout,
  }
})
