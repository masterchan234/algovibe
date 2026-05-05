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

      <div class="ld-header-stats hide-on-mobile">
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
        <!-- BASKET_EXIT — Hold-to-Confirm in Live Mode -->
        <HoldToConfirmBtn
          v-if="livePositions.length > 0"
          label="🚨 BASKET EXIT"
          holding-text="Holding…"
          done-text="✓ Exit Sent"
          variant="danger"
          :hold-ms="2500"
          @confirm="triggerBasketExit"
          class="basket-exit-htc"
        />
        <button class="back-btn" @click="goBack">← DRY VIEW</button>
        <button class="logout-btn-live" @click="handleLogout" title="Logout and clear session">🚪 LOGOUT</button>
      </div>
    </header>

    <!-- ── Bot Offline Banner ──────────────────────────────────────── -->
    <div v-if="liveState.bot_offline" class="offline-banner">
      ⚠️ Bot engine appears offline or unreachable. Displaying last known state.
    </div>

    <!-- ── Net-Zero Survival Activated Banner ─────────────────────── -->
    <div v-if="liveState.net_zero_survival_active" class="nz-survival-banner">
      🛡️ NET-ZERO SURVIVAL ACTIVATED — Emergency exit executed. System Paused.
      Manual review required before resuming.
    </div>

    <!-- ── Safety Gauge (Net-Zero Survival Zone) ──────────────────── -->
    <div
      class="safety-gauge-wrap"
      :class="{ 'gauge-danger': safetyIsDanger || liveState.net_zero_survival_active }"
      v-if="livePositions.length > 0 || liveState.net_zero_survival_active"
    >
      <div class="gauge-header">
        <span class="gauge-icon">🛡️</span>
        <span class="gauge-title">SURVIVAL SAFETY GAUGE</span>
        <span class="gauge-net" :class="safetyNetPnl >= 0 ? 'profit' : 'loss'">
          Net: {{ safetyNetPnl >= 0 ? '+' : '' }}Rp {{ formatNumber(safetyNetPnl) }}
        </span>
        <span class="gauge-vel" :class="_momentumState === 'DROPPING' ? 'loss' : (_momentumState === 'IMPROVING' ? 'profit' : 'neutral')">
          {{ momentumIcon }} {{ liveState.net_pnl_velocity >= 0 ? '+' : '' }}{{ Number(liveState.net_pnl_velocity).toFixed(1) }} IDR/s
        </span>
      </div>

      <div class="gauge-bar-row">
        <span class="gauge-lbl">Realized</span>
        <div class="gauge-track">
          <div
            class="gauge-fill"
            :style="{
              width: safetyGaugePct + '%',
              background: safetyGaugeColor,
              transition: 'width 0.6s ease, background 0.4s ease',
            }"
          ></div>
          <div class="gauge-zero-line" :class="{ 'zero-blink': safetyIsDanger || liveState.net_zero_survival_active }"></div>
        </div>
        <span class="gauge-pct" :style="{ color: safetyGaugeColor }">{{ safetyGaugePct.toFixed(0) }}%</span>
      </div>

      <div class="gauge-legend-row">
        <span class="gauge-legend-item">
          <span class="legend-dot" style="background:#00e676"></span>
          Realized: {{ liveState.net_pnl_realized >= 0 ? '+' : '' }}Rp {{ formatNumber(liveState.net_pnl_realized) }}
        </span>
        <span class="gauge-legend-item">
          <span class="legend-dot" :style="{ background: liveState.net_pnl_unrealized >= 0 ? '#00e676' : '#ff4444' }"></span>
          Unrealized: {{ liveState.net_pnl_unrealized >= 0 ? '+' : '' }}Rp {{ formatNumber(liveState.net_pnl_unrealized) }}
        </span>
        <span class="gauge-legend-item" :class="safetyNetPnl >= 0 ? 'profit' : 'loss'">
          <span class="legend-dot" :style="{ background: safetyGaugeColor }"></span>
          Net PnL: {{ safetyNetPnl >= 0 ? '+' : '' }}Rp {{ formatNumber(safetyNetPnl) }}
        </span>
      </div>
    </div>

    <!-- ── Mobile KPI strip ──────────────────────────────────────────── -->
    <div class="mobile-kpi-strip show-on-mobile">
      <div class="mkpi">
        <span class="mkpi-label">Equity</span>
        <span class="mkpi-value gold">Rp {{ formatNumber(store.stats.total_equity) }}</span>
      </div>
      <div class="mkpi">
        <span class="mkpi-label">PnL</span>
        <span class="mkpi-value" :class="store.stats.daily_pnl >= 0 ? 'profit' : 'loss'">
          {{ store.stats.daily_pnl >= 0 ? '+' : '' }}Rp {{ formatNumber(store.stats.daily_pnl) }}
        </span>
      </div>
      <div class="mkpi momentum-stat" :class="`ms-${momentumClass}`">
        <span class="mkpi-label">Momentum</span>
        <span class="mkpi-value momentum-val">{{ momentumIcon }} {{ _momentumState }}</span>
      </div>
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

        <!-- Desktop Table -->
        <div class="positions-table-wrap hide-on-mobile" v-if="livePositions.length">
          <table class="positions-table">
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
        </div>

        <!-- Mobile Cards -->
        <div class="pos-cards show-on-mobile" v-if="livePositions.length">
          <div
            v-for="pos in livePositions"
            :key="`mob-${pos.pair}`"
            class="pos-card"
            :class="pos.pnl_net_idr >= 0 ? 'card-profit' : 'card-loss'"
          >
            <div class="pos-card-top">
              <span class="pos-card-pair">{{ formatPair(pos.pair) }}</span>
              <span class="pos-card-pnl" :class="pos.pnl_net_idr >= 0 ? 'profit' : 'loss'">
                {{ pos.pnl_net_idr >= 0 ? '+' : '' }}Rp {{ formatNumber(pos.pnl_net_idr) }}
                <small>({{ pos.pnl_pct >= 0 ? '+' : '' }}{{ (pos.pnl_pct || 0).toFixed(2) }}%)</small>
              </span>
            </div>
            <div class="pos-card-row">
              <span class="pos-card-lbl">Entry</span>
              <span>{{ formatPrice(pos.entry_price) }}</span>
              <span class="pos-card-lbl">Mark</span>
              <span>{{ formatPrice(pos.last_price) }}</span>
            </div>
            <div class="pos-card-row">
              <span class="pos-card-lbl">Size</span>
              <span>Rp {{ formatNumber(pos.amount_idr) }}</span>
              <span class="pos-card-lbl">Duration</span>
              <span>{{ formatDuration(pos.duration_sec) }}</span>
            </div>
          </div>
        </div>

        <div v-if="!livePositions.length" class="empty-state">
          <span>No active live positions</span>
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

          <!-- Desktop Table -->
          <div v-if="orders.length" class="orders-table-wrap hide-on-mobile">
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

          <!-- Mobile Order Cards -->
          <div v-if="orders.length" class="order-cards show-on-mobile">
            <div
              v-for="ord in orders"
              :key="`mob-ord-${ord.order_id || ord.created_at}`"
              class="order-card"
            >
              <div class="order-card-top">
                <span class="order-card-pair">{{ formatPair(ord.pair) }}</span>
                <span :class="(ord.side || '').toLowerCase() === 'buy' ? 'side-buy' : 'side-sell'" class="order-card-side">
                  {{ (ord.side || '').toUpperCase() }}
                </span>
                <span class="status-chip" :class="`chip-${(ord.status||'').toLowerCase()}`">{{ ord.status }}</span>
              </div>
              <div class="order-card-row">
                <span class="pos-card-lbl">Price</span>
                <span>{{ formatPrice(ord.price) }}</span>
                <span class="pos-card-lbl">Amount</span>
                <span>{{ (ord.amount || 0).toFixed(6) }}</span>
              </div>
              <div class="order-card-time">{{ formatTime(ord.created_at) }}</div>
            </div>
          </div>

          <div v-if="!orders.length" class="empty-state">
            <span>{{ loadingOrders ? 'Loading orders...' : 'No recent orders' }}</span>
          </div>
        </div>
      </section>
    </main>

    <!-- ── Footer ──────────────────────────────────────────────────── -->
    <footer class="ld-footer">
      <span>Peak PnL (Session): <strong class="gold">Rp {{ formatNumber(liveState.peak_net_pnl_session) }}</strong></span>
      <span class="hide-on-mobile">Session Mode: <strong class="gold">{{ liveState.session_mode || 'NORMAL' }}</strong></span>
      <span class="footer-conn" :class="store.connected ? 'ok' : 'err'">
        {{ store.connected ? '● CONNECTED' : '○ DISCONNECTED' }}
      </span>
    </footer>

    <!-- Mobile Bottom Navigation Bar -->
    <nav class="ld-mobile-nav show-on-mobile" aria-label="Live dashboard navigation">
      <button class="lmn-btn" :class="{ active: liveTab === 'positions' }" @click="liveTab = 'positions'" aria-label="Positions">
        <span>📊</span><span class="lmn-label">Positions</span>
      </button>
      <button class="lmn-btn" :class="{ active: liveTab === 'chart' }" @click="liveTab = 'chart'" aria-label="Chart">
        <span>📈</span><span class="lmn-label">Chart</span>
      </button>
      <button class="lmn-btn" :class="{ active: liveTab === 'orders' }" @click="liveTab = 'orders'" aria-label="Orders">
        <span>📋</span><span class="lmn-label">Orders</span>
      </button>
      <button class="lmn-btn" @click="goBack" aria-label="Back to Dry Run">
        <span>🔄</span><span class="lmn-label">Dry Run</span>
      </button>
      <button class="lmn-btn lmn-logout" @click="handleLogout" aria-label="Logout">
        <span>🚪</span><span class="lmn-label">Logout</span>
      </button>
    </nav>

    <!-- Bracket Exit Confirm: popup for negative-PnL exits requiring user decision -->
    <BracketExitConfirmModal />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'
import { useMode } from '../composables/useMode'
import ProfitChart from '../components/ProfitChart.vue'
import HoldToConfirmBtn from '../components/HoldToConfirmBtn.vue'
import BracketExitConfirmModal from '../components/BracketExitConfirmModal.vue'

const store = useTradeStore()
const router = useRouter()
const { getAuthHeaders, logout: authLogout } = useAuth()
const { clearMode } = useMode()

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

const ORDER_POLL_INTERVAL_MS = 10_000

// Active tab on mobile bottom nav
const liveTab = ref('positions')

// ── Live State (target protection, peak PnL, session) ─────────────────────
const liveState = ref({
  target_protection_active: false,
  peak_net_pnl_session: 0,
  momentum_state: 'STALLING',
  session_mode: 'NORMAL',
  is_latched: false,
  bot_offline: false,
  net_zero_survival_active: false,
  net_pnl_velocity: 0,
  net_pnl_realized: 0,
  net_pnl_unrealized: 0,
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

// ── Safety Gauge (Net-Zero Survival) ───────────────────────────────────────
// netPnl = realized + unrealized; gauge shows how far from zero we are
const safetyNetPnl = computed(
  () => liveState.value.net_pnl_realized + liveState.value.net_pnl_unrealized
)
// Buffer is the realized profit that cushions unrealised losses
const safetyBuffer = computed(() => liveState.value.net_pnl_realized)
// Gauge fill % — 100 % when net PnL equals realized buffer; 0 % at net zero
const safetyGaugePct = computed(() => {
  const buffer = safetyBuffer.value
  if (buffer <= 0) return 0
  return Math.max(0, Math.min(100, (safetyNetPnl.value / buffer) * 100))
})
// Danger condition: net PnL is low AND momentum is DROPPING
const safetyIsDanger = computed(() =>
  (safetyGaugePct.value < 20 || safetyNetPnl.value <= 0)
  && _momentumState.value === 'DROPPING'
)
const safetyGaugeColor = computed(() => {
  if (liveState.value.net_zero_survival_active || safetyIsDanger.value) return '#ff1e1e'
  if (safetyGaugePct.value < 40) return '#f5a623'
  return '#00e676'
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

// ── BASKET_EXIT — triggered by HoldToConfirmBtn after successful hold ───────
async function triggerBasketExit() {
  try {
    const res = await fetch(`${backendUrl}/api/bot/basket-exit`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders(),
      },
      body: JSON.stringify({ reason: 'MANUAL_LIVE_BASKET_EXIT' }),
    })
    if (res.ok) {
      const data = await res.json()
      console.log('[LiveDash] BASKET_EXIT triggered:', data)
    }
  } catch (e) {
    console.error('[LiveDash] BASKET_EXIT request failed:', e)
  }
}

// ── Navigation ──────────────────────────────────────────────────────────────
function goBack() {
  router.push('/dashboard')
}

async function handleLogout() {
  try { store.socket?.disconnect() } catch { /* ignore */ }
  await authLogout(backendUrl)
  clearMode()
  router.push('/login')
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
const LIVE_STATE_POLL_MS = 3_000   // poll live state (gauge) every 3 s
let _pollInterval = null
let _statePollInterval = null

onMounted(async () => {
  if (!store.socket) store.connect()
  // Seed state before first socket push
  await fetchLiveState()
  await fetchOrders()
  // Poll orders every ORDER_POLL_INTERVAL_MS
  _pollInterval = setInterval(fetchOrders, ORDER_POLL_INTERVAL_MS)
  // Poll live state (gauge + Net-Zero) every LIVE_STATE_POLL_MS
  _statePollInterval = setInterval(fetchLiveState, LIVE_STATE_POLL_MS)
})

onUnmounted(() => {
  if (_pollInterval) clearInterval(_pollInterval)
  if (_statePollInterval) clearInterval(_statePollInterval)
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

.logout-btn-live {
  background: rgba(248, 81, 73, 0.1);
  border: 1px solid rgba(248, 81, 73, 0.4);
  color: #f85149;
  padding: 5px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 700;
  font-family: inherit;
  letter-spacing: 0.5px;
  transition: all 0.2s;
}
.logout-btn-live:hover {
  background: rgba(248, 81, 73, 0.22);
  border-color: #f85149;
  box-shadow: 0 0 10px rgba(248, 81, 73, 0.25);
}
.logout-btn-live:active { transform: scale(0.97); }

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

/* ── Basket Exit HTC button ──────────────────────────────────────────── */
.basket-exit-htc {
  font-family: 'Courier New', monospace;
  font-size: 0.72rem;
  letter-spacing: 1px;
}

/* ── Responsive helpers ──────────────────────────────────────────────── */
.hide-on-mobile { display: initial; }
.show-on-mobile { display: none;    }

/* ── Mobile responsive ───────────────────────────────────────────────── */
@media (max-width: 900px) {
  .ld-main { grid-template-columns: 1fr; }
  .ld-header-stats { gap: 12px; }
}

@media (max-width: 768px) {
  .hide-on-mobile { display: none !important; }
  .show-on-mobile { display: flex !important; }

  /* Add bottom padding for mobile nav */
  .live-dashboard { padding-bottom: 64px; }

  .ld-header {
    padding: 10px 14px;
    gap: 10px;
  }

  .ld-header-right { gap: 6px; margin-left: auto; }

  .back-btn { padding: 8px 10px; min-height: 44px; }

  /* Mobile KPI strip */
  .mobile-kpi-strip {
    display: flex;
    gap: 0;
    background: #161616;
    border-bottom: 1px solid #2a2a2a;
    padding: 8px 12px;
    justify-content: space-around;
    flex-wrap: nowrap;
  }

  .mkpi {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
  }

  .mkpi-label {
    font-size: 0.58rem;
    color: #666;
    letter-spacing: 1px;
    text-transform: uppercase;
  }

  .mkpi-value {
    font-size: 0.85rem;
    font-weight: 700;
  }

  /* Mobile cards for positions */
  .pos-cards {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 10px;
    overflow-y: auto;
  }

  .pos-card {
    background: #161616;
    border: 1px solid #2a2a2a;
    border-radius: 8px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .pos-card.card-profit { border-left: 3px solid #00e676; }
  .pos-card.card-loss   { border-left: 3px solid #ff4444; }

  .pos-card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .pos-card-pair {
    font-size: 0.95rem;
    font-weight: 700;
    color: #f5a623;
  }

  .pos-card-pnl { font-size: 0.85rem; font-weight: 700; }
  .pos-card-pnl small { font-size: 0.7rem; margin-left: 4px; }

  .pos-card-row {
    display: grid;
    grid-template-columns: 50px 1fr 50px 1fr;
    gap: 4px;
    font-size: 0.72rem;
    align-items: center;
  }

  .pos-card-lbl {
    color: #666;
    font-size: 0.62rem;
    letter-spacing: 0.5px;
  }

  /* Mobile order cards */
  .order-cards {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px;
    overflow-y: auto;
  }

  .order-card {
    background: #161616;
    border: 1px solid #2a2a2a;
    border-radius: 8px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .order-card-top {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .order-card-pair {
    font-weight: 700;
    color: #f5a623;
    font-size: 0.85rem;
    flex: 1;
  }

  .order-card-side { font-size: 0.75rem; font-weight: 700; }

  .order-card-row {
    display: grid;
    grid-template-columns: 50px 1fr 50px 1fr;
    gap: 4px;
    font-size: 0.72rem;
    align-items: center;
  }

  .order-card-time { font-size: 0.62rem; color: #555; }

  /* Ensure touch targets ≥ 44×44px */
  .basket-exit-htc { min-height: 44px; min-width: 44px; }
}

/* ── Live Mobile Bottom Nav ──────────────────────────────────────────── */
.ld-mobile-nav {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  height: 64px;
  background: rgba(13, 13, 13, 0.96);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-top: 1px solid #2a2a2a;
  z-index: 200;
  justify-content: space-around;
  align-items: stretch;
  padding-bottom: env(safe-area-inset-bottom, 0);
}

.lmn-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: transparent;
  border: none;
  color: #555;
  cursor: pointer;
  font-size: 10px;
  font-family: inherit;
  letter-spacing: 0.5px;
  min-height: 44px;
  min-width: 44px;
  padding: 8px 4px;
  transition: color 0.2s;
  -webkit-tap-highlight-color: transparent;
}

.lmn-btn.active { color: #f5a623; }
.lmn-btn > span:first-child { font-size: 20px; line-height: 1; }
.lmn-label { font-size: 10px; font-weight: 600; text-transform: uppercase; }
.lmn-btn.lmn-logout { color: #f85149; }
.lmn-btn.lmn-logout:hover { color: #ff6b6b; }

/* ── Net-Zero Survival Activated Banner ─────────────────────────────── */
.nz-survival-banner {
  background: rgba(255, 30, 30, 0.15);
  border-bottom: 2px solid #ff1e1e;
  border-top: 2px solid #ff1e1e;
  color: #ff4444;
  padding: 10px 20px;
  font-size: 0.8rem;
  font-weight: 700;
  letter-spacing: 1px;
  text-align: center;
  animation: nz-pulse 1.5s ease-in-out infinite;
}
@keyframes nz-pulse {
  0%, 100% { background: rgba(255, 30, 30, 0.15); }
  50%       { background: rgba(255, 30, 30, 0.30); }
}

/* ── Safety Gauge ────────────────────────────────────────────────────── */
.safety-gauge-wrap {
  margin: 0 16px 0 16px;
  padding: 10px 14px;
  background: #111;
  border: 1px solid #2a2a2a;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: border-color 0.4s ease;
}
.safety-gauge-wrap.gauge-danger {
  border-color: #ff1e1e;
  box-shadow: 0 0 12px rgba(255, 30, 30, 0.25);
}
.gauge-header {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.gauge-icon { font-size: 1rem; }
.gauge-title {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 2px;
  color: #f5a623;
  flex: 1;
}
.gauge-net, .gauge-vel {
  font-size: 0.75rem;
  font-weight: 700;
}
.gauge-vel.neutral { color: #f5a623; }

.gauge-bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.gauge-lbl {
  font-size: 0.6rem;
  color: #666;
  letter-spacing: 1px;
  text-transform: uppercase;
  width: 52px;
  flex-shrink: 0;
}
.gauge-track {
  flex: 1;
  height: 10px;
  background: #1e1e1e;
  border-radius: 5px;
  overflow: visible;
  position: relative;
  border: 1px solid #333;
}
.gauge-fill {
  height: 100%;
  border-radius: 5px;
  min-width: 2px;
}
.gauge-zero-line {
  position: absolute;
  right: 0;
  top: -3px;
  width: 2px;
  height: 16px;
  background: #555;
  border-radius: 1px;
}
.gauge-zero-line.zero-blink {
  background: #ff1e1e;
  animation: zero-blink 0.8s ease-in-out infinite;
}
@keyframes zero-blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.3; }
}
.gauge-pct {
  font-size: 0.7rem;
  font-weight: 700;
  width: 36px;
  text-align: right;
  flex-shrink: 0;
}

.gauge-legend-row {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.gauge-legend-item {
  font-size: 0.62rem;
  color: #888;
  display: flex;
  align-items: center;
  gap: 5px;
}
.legend-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.neutral { color: #f5a623; }
</style>
