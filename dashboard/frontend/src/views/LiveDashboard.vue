<template>
  <div class="live-dashboard" :class="`tab-${mobileTab}`">
    <Header :stats="store.stats" :connected="store.connected" />

    <RiskGuardPopup :socket="store.socket" />
    <BracketExitConfirmModal />

    <div v-if="liveState.bot_offline" class="offline-banner">
      ⚠️ Bot engine appears offline or unreachable. Displaying last known state.
    </div>

    <div v-if="liveState.net_zero_survival_active" class="nz-survival-banner">
      🛡️ NET-ZERO SURVIVAL ACTIVATED — Emergency exit executed. System Paused.
    </div>

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
      </div>

      <div class="gauge-bar-row">
        <div class="gauge-track">
          <div
            class="gauge-fill"
            :style="{
              width: safetyGaugePct + '%',
              background: safetyGaugeColor,
            }"
          ></div>
        </div>
        <span class="gauge-pct" :style="{ color: safetyGaugeColor }">{{ safetyGaugePct.toFixed(0) }}%</span>
      </div>
    </div>

    <main class="main-grid" :style="{ '--left-width': leftPanelWidth + 'px' }">
      <section class="left-panel" :class="{ 'mobile-hidden': mobileTab !== 'activity' }">
        <ActivityFeed />
      </section>
      
      <div class="resizer hide-mobile" @mousedown="startResize" :class="{ 'is-active': isResizing }"></div>

      <section class="right-panel" :class="{ 'mobile-hidden': mobileTab !== 'chart' }">
        <ProfitChart />
      </section>
    </main>

    <nav class="mob-bottom-nav">
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'activity' }" @click="mobileTab = 'activity'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 10h16M4 14h10"/></svg>
        <span>Activity</span>
      </button>
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'chart' }" @click="mobileTab = 'chart'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        <span>Chart</span>
      </button>
    </nav>

    <footer class="ld-footer">
      <div class="footer-stats hide-mobile">
        <span class="stat-item">LATENCY: <span class="v">{{ store.latency || 0 }}ms</span></span>
        <span class="stat-item">UPTIME: <span class="v">{{ uptimeStr }}</span></span>
      </div>
      <div class="footer-right">
        <span>Session Peak: <strong class="gold">Rp {{ formatNumber(liveState.peak_net_pnl_session) }}</strong></span>
        <span>Bot Status: <strong :class="store.connected ? 'profit' : 'loss'">{{ store.connected ? 'ONLINE' : 'OFFLINE' }}</strong></span>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'
import Header from '../components/Header.vue'
import ProfitChart from '../components/ProfitChart.vue'
import RiskGuardPopup from '../components/RiskGuardPopup.vue'
import BracketExitConfirmModal from '../components/BracketExitConfirmModal.vue'
import ActivityFeed from '../components/ActivityFeed.vue'

const store = useTradeStore()
const router = useRouter()
const { getAuthHeaders } = useAuth()

const now = ref(Date.now())
const mobileTab = ref('activity')
const wsStaleMs = 7000

// Resizer for panel width control
const leftPanelWidth = ref(440)
const isResizing = ref(false)

function startResize(e) {
  isResizing.value = true
  window.addEventListener('mousemove', handleMouseMove)
  window.addEventListener('mouseup', stopResize)
}

function handleMouseMove(e) {
  if (!isResizing.value) return
  leftPanelWidth.value = Math.max(300, Math.min(800, e.clientX))
}

function stopResize() {
  isResizing.value = false
  window.removeEventListener('mousemove', handleMouseMove)
  window.removeEventListener('mouseup', stopResize)
}

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

const liveState = ref({
  bot_offline: false,
  net_zero_survival_active: false,
  net_pnl_realized: 0,
  net_pnl_unrealized: 0,
  peak_net_pnl_session: 0,
  momentum_state: 'STALLING',
  net_pnl_velocity: 0,
})

const uptimeStr = computed(() => {
  const sec = store.stats.uptime_sec || 0
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
})

const socketIsFresh = computed(() => {
  const lastAt = Number(store.lastSocketStatsAt || 0)
  return Boolean(store.connected && lastAt > 0 && (Date.now() - lastAt) < wsStaleMs)
})

const livePositions = computed(() => store.positions)

const liveRealizedPnl = computed(() =>
  socketIsFresh.value ? Number(store.stats.daily_pnl || 0) : Number(liveState.value.net_pnl_realized || 0)
)

const liveUnrealizedPnl = computed(() =>
  socketIsFresh.value ? Number(store.stats.unrealized_pnl || 0) : Number(liveState.value.net_pnl_unrealized || 0)
)

const safetyNetPnl = computed(() => liveRealizedPnl.value + liveUnrealizedPnl.value)
const safetyGaugePct = computed(() => {
  const buffer = liveRealizedPnl.value
  if (buffer <= 0) return 0
  return Math.max(0, Math.min(100, (safetyNetPnl.value / buffer) * 100))
})
const safetyIsDanger = computed(() => safetyGaugePct.value < 20)
const safetyGaugeColor = computed(() => safetyGaugePct.value < 20 ? '#ff3366' : '#00ff88')
const dataSourceLabel = computed(() => socketIsFresh.value ? 'WS REALTIME' : 'API FALLBACK')
const dataSourceClass = computed(() => socketIsFresh.value ? 'is-realtime' : 'is-fallback')

async function fetchLiveState() {
  try {
    const res = await fetch(`${backendUrl}/api/live/state`, { headers: getAuthHeaders() })
    if (!res.ok) return

    const data = await res.json()
    liveState.value.bot_offline = Boolean(data.bot_offline)
    liveState.value.net_zero_survival_active = Boolean(data.net_zero_survival_active)
    liveState.value.peak_net_pnl_session = Number(data.peak_net_pnl_session || 0)

    if (!socketIsFresh.value) {
      liveState.value.net_pnl_realized = Number(data.net_pnl_realized || 0)
      liveState.value.net_pnl_unrealized = Number(data.net_pnl_unrealized || 0)
      liveState.value.momentum_state = data.momentum_state || 'STALLING'
      liveState.value.net_pnl_velocity = Number(data.net_pnl_velocity || 0)

      store.stats.daily_pnl = liveState.value.net_pnl_realized
      store.stats.unrealized_pnl = liveState.value.net_pnl_unrealized
      store.stats.net_pnl = liveState.value.net_pnl_realized + liveState.value.net_pnl_unrealized
      store.stats.momentum_state = liveState.value.momentum_state
      store.stats.net_pnl_velocity = liveState.value.net_pnl_velocity
      store.markApiStatsUsed(wsStaleMs)

      const total = Math.round(store.stats.net_pnl)
      const timeStr = new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      const last = store.pnlHistory[store.pnlHistory.length - 1]
      if (!last || last.pnl !== total) {
        store.pnlHistory.push({ time: timeStr, pnl: total })
        if (store.pnlHistory.length > 300) {
          store.pnlHistory.splice(0, store.pnlHistory.length - 300)
        }
      }
    }
  } catch {
    liveState.value.bot_offline = true
  }
}

async function fetchLiveSnapshot() {
  try {
    const [statsRes, positionsRes] = await Promise.all([
      fetch(`${backendUrl}/api/live/stats`, { headers: getAuthHeaders() }),
      fetch(`${backendUrl}/api/live/positions`, { headers: getAuthHeaders() }),
    ])

    if (statsRes.ok && !socketIsFresh.value) {
      const data = await statsRes.json()
      Object.assign(store.stats, {
        daily_pnl: Number(data.daily_pnl ?? 0),
        available_idr: Number(data.available_idr ?? 0),
        idr_used: Number(data.idr_used ?? 0),
        total_equity: Number(data.total_equity ?? 0),
        utilization: Number(data.utilization ?? 0),
        win_rate: Number(data.win_rate ?? 0),
        active_positions: Number(data.active_positions ?? 0),
        unrealized_pnl: Number(data.unrealized_pnl ?? 0),
        net_pnl: Number(data.net_pnl ?? 0),
        regime: data.regime ?? 'CHOPPY',
        market_sentiment_index: Number(data.market_sentiment_index ?? 50),
        momentum_state: data.momentum_state ?? 'STALLING',
        net_pnl_velocity: Number(data.net_pnl_velocity ?? 0),
        entry_gate_state: data.entry_gate_state ?? '',
        entry_gate_reason: data.entry_gate_reason ?? '',
        entry_gate_pair: data.entry_gate_pair ?? '',
        entry_gate_at: data.entry_gate_at ?? '',
        session_mode: data.session_mode ?? 'NORMAL',
        daily_target_hit: Boolean(data.daily_target_hit),
        session_buy_enabled: data.session_buy_enabled ?? true,
        btc_price: Number(data.btc_price ?? 0),
        btc_change_24h: Number(data.btc_change_24h ?? 0),
        btc_ch_5m: Number(data.btc_ch_5m ?? 0),
        btc_ch_15m: Number(data.btc_ch_15m ?? 0),
        btc_ch_1h: Number(data.btc_ch_1h ?? 0),
        btc_trend: data.btc_trend ?? 'N/A',
        btc_score: Number(data.btc_score ?? 50),
      })
      store.markApiStatsUsed(wsStaleMs)
    }

    if (positionsRes.ok && !socketIsFresh.value) {
      const data = await positionsRes.json()
      if (Array.isArray(data)) {
        store.positions = data
      }
    }
  } catch {}
}

function formatNumber(val) { return Number(val || 0).toLocaleString('id-ID') }
function formatPrice(val) { return Number(val || 0).toLocaleString('id-ID') }
function formatPair(pair) { return pair ? pair.replace(/idr$/i, '').toUpperCase() : '-' }

let pollInt = null
let clockInt = null

onMounted(() => {
  store.connect()
  fetchLiveSnapshot()
  fetchLiveState()

  pollInt = setInterval(() => {
    if (!socketIsFresh.value) {
      fetchLiveSnapshot()
    }
    fetchLiveState()
  }, 5000)

  clockInt = setInterval(() => { now.value = Date.now() }, 1000)
})

onUnmounted(() => {
  clearInterval(pollInt)
  if (clockInt) clearInterval(clockInt)
  stopResize()
})

watch(() => store.forceLogout, (triggered) => {
  if (triggered) {
    router.push('/login')
  }
})
</script>

<style scoped>
.live-dashboard {
  min-height: 100vh;
  min-height: 100dvh;
  background: #060810;
  display: flex;
  flex-direction: column;
  font-family: 'Inter', sans-serif;
}

.offline-banner {
  background: rgba(245, 166, 35, 0.1);
  color: #f5a623;
  padding: 8px;
  text-align: center;
  font-size: 12px;
  font-weight: 700;
  border-bottom: 1px solid rgba(245, 166, 35, 0.2);
}

.nz-survival-banner {
  background: rgba(255, 51, 102, 0.1);
  color: #ff3366;
  padding: 12px;
  text-align: center;
  font-weight: 800;
  font-size: 11px;
  letter-spacing: 1px;
  border-bottom: 1px solid rgba(255, 51, 102, 0.2);
}

/* Command Center Styles */
.command-center-wrap {
  margin: 16px 24px;
  padding: 16px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 12px;
}

.cc-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.cc-title {
  font-size: 10px;
  font-weight: 900;
  letter-spacing: 2px;
  color: #444;
}

.cc-status {
  font-size: 10px;
  font-weight: 800;
  padding: 4px 10px;
  border-radius: 20px;
  letter-spacing: 1px;
}

.mode-scalping { background: rgba(0, 255, 136, 0.1); color: #00ff88; box-shadow: 0 0 10px rgba(0, 255, 136, 0.1); }
.mode-harvest { background: rgba(245, 166, 35, 0.1); color: #f5a623; box-shadow: 0 0 10px rgba(245, 166, 35, 0.1); }

.cc-controls {
  display: flex;
  align-items: center;
  gap: 24px;
}

.cc-control-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cc-label {
  font-size: 9px;
  font-weight: 700;
  color: #666;
  letter-spacing: 1px;
}

.cc-toggle-btn {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #555;
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 10px;
  font-weight: 900;
  cursor: pointer;
  transition: all 0.3s ease;
}

.cc-toggle-btn.active {
  background: #f5a623;
  color: #000;
  box-shadow: 0 0 15px rgba(245, 166, 35, 0.4);
  border-color: transparent;
}

.cc-action-btn {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #888;
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 10px;
  font-weight: 900;
  cursor: pointer;
}

.cc-action-btn.active {
  color: #fff;
  border-color: rgba(0, 255, 136, 0.5);
  background: rgba(0, 255, 136, 0.1);
}

.cc-divider {
  width: 1px;
  height: 30px;
  background: rgba(255, 255, 255, 0.05);
}

.cc-value-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.cc-val {
  font-size: 14px;
  font-weight: 800;
  color: #fff;
}

.cc-mini-btn {
  background: transparent;
  border: 1px solid rgba(255, 51, 102, 0.3);
  color: #ff3366;
  font-size: 8px;
  font-weight: 900;
  padding: 2px 6px;
  border-radius: 4px;
  cursor: pointer;
}

.cc-mini-btn:hover {
  background: #ff3366;
  color: #fff;
}

@media (max-width: 768px) {
  .command-center-wrap { margin: 12px; padding: 12px; }
  .cc-controls { gap: 12px; overflow-x: auto; padding-bottom: 4px; }
  .cc-control-item { flex-shrink: 0; }
}

.main-grid {
  width: 100%;
  flex: 1;
  display: flex;
  overflow: hidden;
  padding: 16px;
  gap: 0;
  min-height: 0;
}

.left-panel {
  width: var(--left-width);
  min-width: 300px;
  min-height: 0;
  background: #0a0c14;
  border-radius: 16px;
  border: 1px solid #1a1c24;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.resizer {
  width: 16px;
  cursor: col-resize;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: opacity 0.3s;
}

.resizer::after {
  content: '';
  width: 2px;
  height: 40px;
  background: #1a1c24;
  border-radius: 1px;
}

.resizer:hover::after, .resizer.is-active::after {
  background: #6366f1;
  box-shadow: 0 0 10px #6366f1;
}

.right-panel {
  flex: 1;
  min-height: 0;
  background: #0a0c14;
  border-radius: 16px;
  border: 1px solid #1a1c24;
  overflow: hidden;
}

.profit { color: #00ff88; }
.loss { color: #ff3366; }

.safety-gauge-wrap {
  padding: 16px 24px;
  background: rgba(255, 255, 255, 0.01);
  border-bottom: 1px solid #1a1c24;
}

.gauge-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.gauge-title { font-size: 10px; font-weight: 800; color: #444; }
.gauge-track {
  height: 4px;
  background: #1a1c24;
  border-radius: 2px;
  flex: 1;
  margin: 0 16px;
  overflow: hidden;
}

.gauge-fill { height: 100%; transition: width 0.5s ease; }
.gauge-bar-row { display: flex; align-items: center; }
.gauge-pct { font-size: 12px; font-weight: 800; min-width: 40px; text-align: right; }

.ld-footer {
  height: 48px;
  background: #0a0c14;
  border-top: 1px solid #1a1c24;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  font-size: 11px;
  font-weight: 700;
  color: #555;
  letter-spacing: 0.5px;
}

.footer-stats {
  display: flex;
  gap: 20px;
}

.stat-item {
  font-size: 10px;
  font-weight: 800;
  color: #444;
  letter-spacing: 1px;
}

.stat-item .v {
  color: #888;
}

.footer-right {
  display: flex;
  gap: 24px;
}

.gold { color: #f5a623; }

/* ─── Responsive ──────────────────────────────────── */
@media (max-width: 1024px) {
  .ld-main { grid-template-columns: 1fr; overflow-y: auto; }
}



/* ─── Mobile Bottom Nav ───────────────────────────── */
.mob-bottom-nav {
  display: none; /* shown via media query */
  height: 58px;
  background: rgba(10, 12, 20, 0.97);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-top: 1px solid #1a1c24;
  flex-shrink: 0;
  padding-bottom: env(safe-area-inset-bottom);
  height: calc(58px + env(safe-area-inset-bottom));
}
.mob-nav-btn {
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: transparent;
  border: none;
  color: #555;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  cursor: pointer;
  padding: 8px 0;
  transition: color 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.mob-nav-btn svg { width: 20px; height: 20px; }
.mob-nav-btn.active { color: #f5a623; }
.mob-nav-btn.active svg { filter: drop-shadow(0 0 5px rgba(245, 166, 35, 0.7)); }
.mob-badge {
  position: absolute;
  top: 6px;
  right: calc(50% - 18px);
  background: #ff3366;
  color: #fff;
  font-size: 9px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
}

@media (max-width: 768px) {
  .live-dashboard {
    height: 100%;
    height: 100dvh;
    overflow: hidden;
  }

  .ld-main {
    grid-template-columns: 1fr;
    padding: 0;
    gap: 0;
    overflow: hidden;
    flex: 1;
    min-height: 0;
  }

  .ld-panel {
    border-radius: 0;
    border-left: none;
    border-right: none;
    border-top: none;
  }

  /* Tab-based panel visibility */
  .mob-hidden { display: none !important; }
  .desktop-only { display: none !important; }
  .mobile-only { display: flex !important; flex-direction: column; }

  /* Full-height panel on mobile */
  .positions-panel {
    height: 100%;
    overflow: hidden;
  }
  .positions-cards {
    flex: 1;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  .right-panel {
    height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .chart-wrap {
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  /* On mobile 'chart' tab: show chart fullscreen */
  .tab-chart .right-panel { display: flex !important; }
  .tab-chart .orders-wrap { display: none !important; }
  .tab-chart .chart-wrap { flex: 1; }

  /* Footer hidden on mobile (replaced by bottom nav) */
  .ld-footer { display: none; }

  /* Show mobile bottom nav */
  .mob-bottom-nav {
    display: flex !important;
    align-items: center;
    justify-content: space-around;
  }

  /* Safety gauge compact on mobile */
  .safety-gauge-wrap { padding: 10px 16px; }
  .gauge-title { font-size: 9px; }
  .gauge-net { font-size: 11px; }
}

/* Desktop: hide mobile-only elements */
@media (min-width: 769px) {
  .desktop-only { display: block !important; }
  .mobile-only { display: none !important; }
  .mob-bottom-nav { display: none !important; }
  .mob-hidden { display: flex !important; }

  .positions-panel {
    max-height: clamp(360px, 56vh, 620px);
    align-self: start;
  }

  .right-panel {
    min-height: clamp(460px, 62vh, 760px);
  }

  /* Desktop strict layout: below safety gauge only left-right 2 columns */
  .intel-section {
    display: none !important;
  }
}

/* ─── Intel Section (desktop strip + mobile full) ────── */
.intel-section {
  margin: 0 16px 16px;
  max-height: 190px;
  flex-shrink: 0;
}

@media (max-width: 768px) {
  /* On mobile 'intel' tab: show intel panel fullscreen, hide ld-main */
  .tab-intel .ld-main { display: none !important; }
  .tab-intel .intel-section {
    display: flex !important;
    flex-direction: column;
    flex: 1;
    margin: 0;
    max-height: none;
    border-radius: 0;
    border-left: none;
    border-right: none;
    border-top: none;
  }
}
</style>
