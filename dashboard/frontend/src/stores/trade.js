import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { io } from 'socket.io-client'
import { useNotifications } from '../composables/useNotifications'
import { useAuth } from '../composables/useAuth'

export const useTradeStore = defineStore('trade', () => {
  const { notify } = useNotifications()
  
  // Connection state
  const connected = ref(false)
  const socket = ref(null)

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
  })

  // Open positions (trades)
  const positions = ref([])

  // Approval gate: pending action waiting for Supreme Commander decision
  const pendingAction = ref(null)

  // Strategic Break: full-screen blocking popup when daily target is reached
  const strategicBreak = ref(null)

  // Activity feed (logs)
  const activityFeed = ref([])
  const MAX_FEED_ITEMS = 100

  // Profit history for line chart: [{time, pnl}]
  const pnlHistory = ref([])
  const MAX_HISTORY = 300

  // Radar data
  const radarPairs = ref([])

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
    const rounded = Math.round(pnl)
    const now = new Date()
    const timeStr = now.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    
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
    if (pnlHistory.value.length > MAX_HISTORY) {
      pnlHistory.value = pnlHistory.value.slice(-MAX_HISTORY)
    }
  }

  async function connect() {
    const backendUrl = import.meta.env.DEV
      ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
      : window.location.origin

    let viewToken = null
    try {
      const res = await fetch(`${backendUrl}/api/view-token`, {
        method: 'GET',
        credentials: 'include',
      })
      if (res.ok) {
        const data = await res.json()
        viewToken = data?.token || null
      }
    } catch {
      viewToken = null
    }

    const s = io(backendUrl, {
      path: '/socket.io',
      auth: viewToken ? { token: viewToken } : {},
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 10000,
    })
    
    socket.value = s

    s.on('connect', () => { connected.value = true })
    s.on('disconnect', () => { connected.value = false })

    s.on('stats_update', (data) => {
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
      })
      _recordPnl((data.daily_pnl ?? 0) + (data.unrealized_pnl ?? 0))
    })

    s.on('trades_update', (data) => {
      if (Array.isArray(data)) {
        positions.value = data
      }
    })

    s.on('logs_update', (data) => {
      if (Array.isArray(data)) {
        _addFeedItems(data)
      }
    })

    s.on('market_trades_update', (payload) => {
      const pair = (payload?.pair || 'MARKET').toUpperCase()
      const trades = Array.isArray(payload?.trades) ? payload.trades : []
      if (!trades.length) return

      // Ambil subset terbaru supaya feed tetap ringan.
      const latest = trades.slice(0, 20)
      const mapped = latest.map((t, idx) => {
        const sideRaw = String(t?.side || '').toUpperCase()
        const side = sideRaw === 'BUY' ? 'BUY' : (sideRaw === 'SELL' ? 'SELL' : 'MARKET')
        const price = Number(t?.price || 0)
        const amount = Number(t?.amount || 0)
        const ts = Number(t?.time || Date.now())
        const createdAt = new Date(ts).toISOString()

        return {
          id: `mkt_${pair}_${t?.id || idx}_${ts}`,
          created_at: createdAt,
          pair,
          side,
          message: `Market ${side} | ${amount.toFixed(6)} @ Rp ${price.toLocaleString('id-ID')}`,
          level: 'INFO',
          status: 'LIVE',
        }
      })

      _addFeedItems(mapped)
    })

    s.on('radar_update', (data) => {
      if (Array.isArray(data)) {
        radarPairs.value = data
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

  /**
   * Checks the backend for an active HARVEST_ONLY latch on page load/refresh.
   * If the session is already latched and a PENDING strategic_break payload
   * exists, the modal is restored immediately without waiting for a socket event.
   */
  async function checkSessionStatus() {
    const backendUrl = import.meta.env.DEV
      ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
      : window.location.origin
    const { getAuthHeaders } = useAuth()
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
    activityFeed,
    pnlHistory,
    radarPairs,
    connect,
    checkSessionStatus,
    socket,
  }
})
