<template>
  <div class="live-dashboard">
    <!-- ── Header ─────────────────────────────────────────────────── -->
    <header class="ld-header">
      <div class="ld-brand">
        <span class="ld-logo">⚡</span>
        <div class="ld-brand-text">
          <span class="ld-title">ALGOVIBE</span>
          <span class="ld-subtitle">LIVE MEMBER AREA</span>
        </div>
        <span class="live-badge">
          <span class="live-dot"></span>LIVE
        </span>
      </div>

      <div class="ld-header-stats">
        <div class="ld-stat">
          <span class="ld-stat-label">Balance IDR</span>
          <span class="ld-stat-value gold">Rp {{ formatNumber(store.stats.available_idr) }}</span>
        </div>
        <div class="ld-stat">
          <span class="ld-stat-label">Total Equity</span>
          <span class="ld-stat-value gold">Rp {{ formatNumber(store.stats.total_equity) }}</span>
        </div>
        <div class="ld-stat">
          <span class="ld-stat-label">Net PnL</span>
          <span class="ld-stat-value" :class="store.stats.daily_pnl >= 0 ? 'profit' : 'loss'">
            {{ store.stats.daily_pnl >= 0 ? '+' : '' }}Rp {{ formatNumber(store.stats.daily_pnl) }}
          </span>
        </div>
        <div class="ld-stat momentum-stat" :class="`ms-${momentumClass}`">
          <span class="ld-stat-label">Momentum</span>
          <span class="ld-stat-value momentum-val">
            {{ momentumIcon }} {{ liveState.momentum_state || store.stats.momentum_state || 'STALLING' }}
          </span>
        </div>
      </div>

      <div class="ld-header-right">
        <span class="protection-badge" v-if="liveState.target_protection_active">
          🛡️ FLOOR ACTIVE
        </span>
        <span class="latch-badge" v-if="liveState.is_latched">
          🌾 HARVEST ONLY
        </span>
        <button class="back-btn" @click="goBack">← DRY RUN VIEW</button>
      </div>
    </header>

    <!-- ── Bot Offline Banner ──────────────────────────────────────── -->
    <div v-if="liveState.bot_offline" class="offline-banner">
      ⚠️ Bot engine appears offline or unreachable. Displaying last known state.
    </div>

    <!-- ── Main Grid ──────────────────────────────────────────────── -->
    <main class="ld-main">

      <!-- Left: Active Positions -->
      <section class="ld-panel positions-panel">
        <div class="panel-header">
          <span class="panel-icon">📊</span>
          <span class="panel-title">ACTIVE POSITIONS</span>
          <span class="panel-count">{{ livePositions.length }}</span>
        </div>
        <div class="positions-table-wrap">
          <table class="positions-table" v-if="livePositions.length">
            <thead>
              <tr>
                <th>PAIR</th>
                <th>ENTRY</th>
                <th>MARK</th>
                <th>SIZE (IDR)</th>
                <th>NET PnL</th>
                <th>DURATION</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="pos in livePositions"
                :key="pos.pair"
                :class="pos.pnl_net_idr >= 0 ? 'row-profit' : 'row-loss'"
              >
                <td class="pair-cell">{{ formatPair(pos.pair) }}</td>
                <td>{{ formatPrice(pos.entry_price) }}</td>
                <td>{{ formatPrice(pos.last_price) }}</td>
                <td>{{ formatNumber(pos.amount_idr) }}</td>
                <td :class="pos.pnl_net_idr >= 0 ? 'profit' : 'loss'">
                  {{ pos.pnl_net_idr >= 0 ? '+' : '' }}{{ formatNumber(pos.pnl_net_idr) }}
                  <span class="pct">({{ pos.pnl_pct >= 0 ? '+' : '' }}{{ (pos.pnl_pct || 0).toFixed(2) }}%)</span>
                </td>
                <td>{{ formatDuration(pos.duration_sec) }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-state">
            <span>No active live positions</span>
          </div>
        </div>
      </section>

      <!-- Right: PnL Chart + Orders -->
      <section class="ld-panel right-panel">
        <!-- PnL Chart -->
        <div class="chart-wrap">
          <div class="panel-header">
            <span class="panel-icon">📈</span>
            <span class="panel-title">NET PnL — REALTIME</span>
            <span class="pnl-live-label">
              <span class="pnl-dot"></span>STREAMING
            </span>
          </div>
          <ProfitChart />
        </div>

        <!-- Order Book -->
        <div class="orders-wrap">
          <div class="panel-header">
            <span class="panel-icon">📋</span>
            <span class="panel-title">ORDER BOOK</span>
            <button class="refresh-btn" @click="fetchOrders" :disabled="loadingOrders">↻</button>
          </div>
          <div v-if="orders.length" class="orders-table-wrap">
            <table class="orders-table">
              <thead>
                <tr>
                  <th>PAIR</th>
                  <th>SIDE</th>
                  <th>PRICE</th>
                  <th>AMOUNT</th>
                  <th>STATUS</th>
                  <th>TIME</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="ord in orders" :key="ord.order_id || ord.created_at"
                  :class="`ord-${(ord.status || '').toLowerCase()}`">
                  <td>{{ formatPair(ord.pair) }}</td>
                  <td :class="(ord.side || '').toLowerCase() === 'buy' ? 'side-buy' : 'side-sell'">
                    {{ (ord.side || '').toUpperCase() }}
                  </td>
                  <td>{{ formatPrice(ord.price) }}</td>
                  <td>{{ (ord.amount || 0).toFixed(6) }}</td>
                  <td><span class="status-chip" :class="`chip-${(ord.status||'').toLowerCase()}`">{{ ord.status }}</span></td>
                  <td class="time-cell">{{ formatTime(ord.created_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-state">
            <span>{{ loadingOrders ? 'Loading orders...' : 'No recent orders' }}</span>
          </div>
        </div>
      </section>
    </main>

    <!-- ── Footer ──────────────────────────────────────────────────── -->
    <footer class="ld-footer">
      <span>Peak PnL (Session): <strong class="gold">Rp {{ formatNumber(liveState.peak_net_pnl_session) }}</strong></span>
      <span>Session Mode: <strong class="gold">{{ liveState.session_mode || 'NORMAL' }}</strong></span>
      <span class="footer-conn" :class="store.connected ? 'ok' : 'err'">
        {{ store.connected ? '● CONNECTED' : '○ DISCONNECTED' }}
      </span>
    </footer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'
import ProfitChart from '../components/ProfitChart.vue'

const store = useTradeStore()
const router = useRouter()
const { getAuthHeaders } = useAuth()

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

const ORDER_POLL_INTERVAL_MS = 10_000

// ── Live State (target protection, peak PnL, session) ─────────────────────
const liveState = ref({
  target_protection_active: false,
  peak_net_pnl_session: 0,
  momentum_state: 'STALLING',
  session_mode: 'NORMAL',
  is_latched: false,
  bot_offline: false,
})

// ── Orders ─────────────────────────────────────────────────────────────────
const orders = ref([])
const loadingOrders = ref(false)

// ── Live positions (dry_run === 0 only) ────────────────────────────────────
const livePositions = computed(() =>
  store.positions.filter(p => !p.dry_run && p.dry_run !== 1 && p.dry_run !== true)
)

// ── Momentum ───────────────────────────────────────────────────────────────
const _momentumState = computed(
  () => liveState.value.momentum_state || store.stats.momentum_state || 'STALLING'
)
const momentumClass = computed(() => {
  const s = _momentumState.value
  if (s === 'IMPROVING') return 'improving'
  if (s === 'DROPPING') return 'dropping'
  return 'stalling'
})
const momentumIcon = computed(() => {
  if (_momentumState.value === 'IMPROVING') return '▲'
  if (_momentumState.value === 'DROPPING') return '▼'
  return '●'
})

// ── Fetch helpers ──────────────────────────────────────────────────────────
async function fetchLiveState() {
  try {
    const res = await fetch(`${backendUrl}/api/live/state`, {
      credentials: 'include',
      headers: getAuthHeaders(),
    })
    if (res.ok) {
      const data = await res.json()
      Object.assign(liveState.value, data)
    }
  } catch (e) {
    liveState.value.bot_offline = true
  }
}

async function fetchOrders() {
  loadingOrders.value = true
  try {
    const res = await fetch(`${backendUrl}/api/orders`, {
      credentials: 'include',
      headers: getAuthHeaders(),
    })
    if (res.ok) {
      orders.value = await res.json()
    }
  } catch { /* ignore */ } finally {
    loadingOrders.value = false
  }
}

// ── Navigation ──────────────────────────────────────────────────────────────
function goBack() {
  router.push('/')
}

// ── Formatters ──────────────────────────────────────────────────────────────
function formatNumber(val) {
  return Number(val || 0).toLocaleString('id-ID')
}
function formatPrice(val) {
  if (!val) return '-'
  return Number(val).toLocaleString('id-ID', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
}
function formatPair(pair) {
  if (!pair) return '-'
  return pair.replace(/idr$/i, '').toUpperCase()
}
function formatDuration(sec) {
  if (!sec) return '-'
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}
function formatTime(ts) {
  if (!ts) return '-'
  try {
    return new Date(ts).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch { return ts }
}

// ── Polling interval ────────────────────────────────────────────────────────
let _pollInterval = null

onMounted(async () => {
  if (!store.socket) store.connect()
  // Seed state before first socket push
  await fetchLiveState()
  await fetchOrders()
  // Poll orders every ORDER_POLL_INTERVAL_MS
  _pollInterval = setInterval(fetchOrders, ORDER_POLL_INTERVAL_MS)
})

onUnmounted(() => {
  if (_pollInterval) clearInterval(_pollInterval)
})
</script>

<style scoped>
/* ── Base ─────────────────────────────────────────────────────────────── */
.live-dashboard {
  min-height: 100vh;
  background: #0d0d0d;
  color: #e8e8e8;
  font-family: 'Courier New', monospace;
  display: flex;
  flex-direction: column;
}

/* ── Header ──────────────────────────────────────────────────────────── */
.ld-header {
  background: linear-gradient(135deg, #1a1a1a 0%, #111 100%);
  border-bottom: 2px solid #f5a623;
  padding: 12px 20px;
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}
.ld-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.ld-logo { font-size: 1.6rem; }
.ld-title {
  font-size: 1.2rem;
  font-weight: 700;
  letter-spacing: 3px;
  color: #ffd700;
}
.ld-subtitle {
  font-size: 0.65rem;
  letter-spacing: 2px;
  color: #f5a623;
  display: block;
}

/* Pulsing LIVE badge */
.live-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(255, 30, 30, 0.12);
  border: 1px solid #ff1e1e;
  color: #ff4444;
  font-size: 0.7rem;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 4px;
  letter-spacing: 2px;
}
.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #ff1e1e;
  animation: pulse-red 1.2s ease-in-out infinite;
}
@keyframes pulse-red {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(255,30,30,0.7); }
  50% { opacity: 0.6; box-shadow: 0 0 0 6px rgba(255,30,30,0); }
}

.ld-header-stats {
  display: flex;
  gap: 20px;
  flex: 1;
  flex-wrap: wrap;
}
.ld-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ld-stat-label {
  font-size: 0.6rem;
  letter-spacing: 1px;
  color: #888;
  text-transform: uppercase;
}
.ld-stat-value {
  font-size: 0.95rem;
  font-weight: 700;
}
.ld-stat-value.gold { color: #ffd700; }
.ld-stat-value.profit { color: #00e676; }
.ld-stat-value.loss { color: #ff4444; }

/* Momentum stat */
.momentum-stat.ms-improving .momentum-val { color: #00e676; }
.momentum-stat.ms-dropping .momentum-val  { color: #ff4444; }
.momentum-stat.ms-stalling .momentum-val  { color: #f5a623; }

.ld-header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}
.protection-badge, .latch-badge {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 1px;
  padding: 3px 8px;
  border-radius: 4px;
  background: rgba(245,166,35,0.15);
  border: 1px solid #f5a623;
  color: #f5a623;
}
.back-btn {
  background: transparent;
  border: 1px solid #555;
  color: #aaa;
  padding: 5px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.75rem;
  font-family: inherit;
  transition: all 0.2s;
}
.back-btn:hover { border-color: #f5a623; color: #f5a623; }

/* ── Offline Banner ──────────────────────────────────────────────────── */
.offline-banner {
  background: rgba(255, 100, 0, 0.12);
  border-bottom: 1px solid #ff6400;
  color: #ff8c42;
  padding: 8px 20px;
  font-size: 0.8rem;
  text-align: center;
}

/* ── Main Grid ───────────────────────────────────────────────────────── */
.ld-main {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  padding: 16px;
  overflow: hidden;
}

/* ── Panels ──────────────────────────────────────────────────────────── */
.ld-panel {
  background: #111;
  border: 1px solid #2a2a2a;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.ld-panel:hover { border-color: #f5a623; }

.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid #222;
  background: #161616;
}
.panel-icon { font-size: 1rem; }
.panel-title {
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 2px;
  color: #f5a623;
  flex: 1;
}
.panel-count {
  background: #f5a623;
  color: #0d0d0d;
  border-radius: 10px;
  padding: 1px 7px;
  font-size: 0.65rem;
  font-weight: 700;
}
.pnl-live-label {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.65rem;
  color: #00e676;
}
.pnl-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: #00e676;
  animation: pulse-green 1.5s ease-in-out infinite;
}
@keyframes pulse-green {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* ── Tables ──────────────────────────────────────────────────────────── */
.positions-table-wrap, .orders-table-wrap {
  overflow-y: auto;
  flex: 1;
  padding: 8px;
}
.positions-table, .orders-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.72rem;
}
.positions-table th, .orders-table th {
  text-align: left;
  padding: 6px 8px;
  color: #666;
  letter-spacing: 1px;
  font-size: 0.62rem;
  border-bottom: 1px solid #222;
}
.positions-table td, .orders-table td {
  padding: 6px 8px;
  border-bottom: 1px solid #1a1a1a;
}
.row-profit td { color: #e8e8e8; }
.row-loss td { color: #aaa; }
.profit { color: #00e676 !important; }
.loss   { color: #ff4444 !important; }
.pct    { font-size: 0.65rem; opacity: 0.8; margin-left: 3px; }
.pair-cell { font-weight: 700; color: #f5a623; }
.side-buy  { color: #00e676; font-weight: 700; }
.side-sell { color: #ff4444; font-weight: 700; }
.time-cell { color: #666; font-size: 0.65rem; }

.status-chip {
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 1px;
}
.chip-filled { background: rgba(0,230,118,0.15); color: #00e676; }
.chip-open   { background: rgba(245,166,35,0.15); color: #f5a623; }
.chip-canceled { background: rgba(255,68,68,0.12); color: #ff4444; }
.chip-pending  { background: rgba(100,100,255,0.12); color: #7b7bff; }

/* ── Right Panel ─────────────────────────────────────────────────────── */
.right-panel { gap: 0; }
.chart-wrap, .orders-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-bottom: 1px solid #222;
}
.chart-wrap:last-child, .orders-wrap:last-child { border-bottom: none; }

.refresh-btn {
  background: transparent;
  border: 1px solid #333;
  color: #666;
  padding: 2px 8px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 0.8rem;
}
.refresh-btn:hover { border-color: #f5a623; color: #f5a623; }
.refresh-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* ── Empty State ─────────────────────────────────────────────────────── */
.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 80px;
  color: #444;
  font-size: 0.75rem;
  letter-spacing: 1px;
}

/* ── Footer ──────────────────────────────────────────────────────────── */
.ld-footer {
  background: #111;
  border-top: 1px solid #222;
  padding: 8px 20px;
  display: flex;
  gap: 24px;
  font-size: 0.7rem;
  color: #666;
}
.gold { color: #ffd700; }
.footer-conn.ok  { color: #00e676; margin-left: auto; }
.footer-conn.err { color: #ff4444; margin-left: auto; }

/* ── Responsive ──────────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .ld-main { grid-template-columns: 1fr; }
  .ld-header-stats { gap: 12px; }
}
</style>
