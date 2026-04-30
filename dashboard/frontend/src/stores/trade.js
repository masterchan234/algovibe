import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { io } from 'socket.io-client'

export const useTradeStore = defineStore('trade', () => {
  // Connection state
  const connected = ref(false)

  // Stats
  const stats = reactive({
    daily_pnl: 0,
    all_time_pnl_dry: 0,
    win_rate: 0,
    active_positions: 0,
    unrealized_pnl: 0,
    regime: 'CHOPPY',
    regime_leader: '',
    market_sentiment_index: 50,
    latest_llm_insight: '',
  })

  // Open positions (trades)
  const positions = ref([])

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
      }
    }
    if (activityFeed.value.length > MAX_FEED_ITEMS) {
      activityFeed.value = activityFeed.value.slice(0, MAX_FEED_ITEMS)
    }
  }

  function _recordPnl(pnl) {
    const rounded = Math.round(pnl)
    // Skip jika masih nol dan belum ada data bermakna — hindari grafik datar
    const hasData = pnlHistory.value.some(h => h.pnl !== 0)
    if (rounded === 0 && !hasData) return
    // Skip jika nilai sama persis dengan entri terakhir
    const last = pnlHistory.value[pnlHistory.value.length - 1]
    if (last && last.pnl === rounded) return
    const now = new Date()
    const timeStr = now.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
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

    const socket = io(backendUrl, {
      path: '/socket.io',
      auth: viewToken ? { token: viewToken } : {},
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 10000,
    })

    socket.on('connect', () => { connected.value = true })
    socket.on('disconnect', () => { connected.value = false })

    socket.on('stats_update', (data) => {
      Object.assign(stats, {
        daily_pnl: data.daily_pnl ?? 0,
        all_time_pnl_dry: data.all_time_pnl_dry ?? 0,
        win_rate: data.win_rate ?? 0,
        active_positions: data.active_positions ?? 0,
        unrealized_pnl: data.unrealized_pnl ?? 0,
        regime: data.regime ?? 'CHOPPY',
        regime_leader: data.regime_leader ?? '',
        market_sentiment_index: data.market_sentiment_index ?? 50,
        latest_llm_insight: data.latest_llm_insight ?? '',
      })
      _recordPnl((data.daily_pnl ?? 0) + (data.unrealized_pnl ?? 0))
    })

    socket.on('trades_update', (data) => {
      if (Array.isArray(data)) {
        positions.value = data
      }
    })

    socket.on('logs_update', (data) => {
      if (Array.isArray(data)) {
        _addFeedItems(data)
      }
    })

    socket.on('market_trades_update', (payload) => {
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

    socket.on('radar_update', (data) => {
      if (Array.isArray(data)) {
        radarPairs.value = data.slice(0, 20)
      }
    })

    return socket
  }

  return {
    connected,
    stats,
    positions,
    activityFeed,
    pnlHistory,
    radarPairs,
    connect,
  }
})
