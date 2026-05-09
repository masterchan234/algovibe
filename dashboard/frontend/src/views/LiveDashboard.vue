<template>
  <div class="live-dashboard" :class="`tab-${mobileTab}`">
    <Header :stats="store.stats" :connected="store.connected" />
    
    <RiskGuardPopup :socket="store.socket" />

    <!-- Bracket Exit Confirm: shown when bot wants to exit at projected loss -->
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

    <!-- Desktop + Mobile: main content area -->
    <main class="ld-main">
      <!-- Positions panel -->
      <section class="ld-panel positions-panel" :class="{ 'mob-hidden': mobileTab !== 'positions' }">
        <div class="panel-header">
          <span class="panel-title">ACTIVE LIVE POSITIONS</span>
          <span class="panel-count">{{ livePositions.length }}</span>
        </div>

        <!-- Desktop: table view -->
        <div class="positions-table-wrap desktop-only" v-if="livePositions.length">
          <table class="positions-table">
            <thead>
              <tr>
                <th>PAIR</th>
                <th>ENTRY</th>
                <th>MARK</th>
                <th>SIZE</th>
                <th>NET PnL</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="pos in livePositions" :key="pos.pair">
                <td class="pair-cell">{{ formatPair(pos.pair) }}</td>
                <td>{{ formatPrice(pos.entry_price) }}</td>
                <td>{{ formatPrice(pos.last_price) }}</td>
                <td>Rp {{ formatNumber(pos.amount_idr) }}</td>
                <td :class="pos.pnl_net_idr >= 0 ? 'profit' : 'loss'">
                  {{ pos.pnl_net_idr >= 0 ? '+' : '' }}{{ formatNumber(pos.pnl_net_idr) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Mobile: card view -->
        <div class="positions-cards mobile-only" v-if="livePositions.length">
          <div class="pos-card" v-for="pos in livePositions" :key="pos.pair">
            <div class="pos-card-top">
              <span class="pos-pair">{{ formatPair(pos.pair) }}</span>
              <span class="pos-pnl" :class="pos.pnl_net_idr >= 0 ? 'profit' : 'loss'">
                {{ pos.pnl_net_idr >= 0 ? '+' : '' }}Rp {{ formatNumber(pos.pnl_net_idr) }}
              </span>
            </div>
            <div class="pos-card-row">
              <div class="pos-stat"><span class="ps-label">Entry</span><span class="ps-val">{{ formatPrice(pos.entry_price) }}</span></div>
              <div class="pos-stat"><span class="ps-label">Mark</span><span class="ps-val">{{ formatPrice(pos.last_price) }}</span></div>
              <div class="pos-stat"><span class="ps-label">Size</span><span class="ps-val">Rp {{ formatNumber(pos.amount_idr) }}</span></div>
            </div>
          </div>
        </div>

        <div v-if="!livePositions.length" class="empty-state">No active live positions</div>
      </section>

      <!-- Right panel: chart + orders -->
      <section class="ld-panel right-panel" :class="{ 'mob-hidden': mobileTab === 'positions' }">
        <div class="chart-wrap" :class="{ 'mob-hidden': mobileTab === 'orders' }">
          <div class="panel-header">
            <span class="panel-title">LIVE EQUITY FLOW</span>
            <span class="data-source-badge" :class="dataSourceClass">{{ dataSourceLabel }}</span>
          </div>
          <ProfitChart />
        </div>

        <div class="orders-wrap" :class="{ 'mob-hidden': mobileTab === 'chart' }">
          <div class="panel-header">
            <span class="panel-title">LIVE ORDER BOOK</span>
            <button class="refresh-btn" @click="fetchOrders">↻</button>
          </div>
          <div class="orders-table-wrap desktop-only" v-if="orders.length">
            <table class="orders-table">
              <thead>
                <tr>
                  <th>PAIR</th>
                  <th>SIDE</th>
                  <th>PRICE</th>
                  <th>STATUS</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="ord in orders" :key="ord.order_id || ord.created_at">
                  <td class="pair-cell">{{ formatPair(ord.pair) }}</td>
                  <td :class="ord.side === 'buy' ? 'profit' : 'loss'">{{ ord.side.toUpperCase() }}</td>
                  <td>{{ formatPrice(ord.price) }}</td>
                  <td><span class="status-chip">{{ ord.status }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
          <!-- Mobile: order cards -->
          <div class="order-cards mobile-only" v-if="orders.length">
            <div class="order-card" v-for="ord in orders" :key="ord.order_id || ord.created_at">
              <span class="oc-pair">{{ formatPair(ord.pair) }}</span>
              <span class="oc-side" :class="ord.side === 'buy' ? 'profit' : 'loss'">{{ ord.side.toUpperCase() }}</span>
              <span class="oc-price">{{ formatPrice(ord.price) }}</span>
              <span class="status-chip">{{ ord.status }}</span>
            </div>
          </div>
          <div v-if="!orders.length" class="empty-state">No recent orders</div>
        </div>
      </section>
    </main>

    <!-- LLM Exit Decision Panel — full-width below main, 4th mobile tab -->
    <section
      class="ld-panel llm-section"
      :class="{ 'mob-hidden': mobileTab !== 'llm' }"
    >
      <LlmDecisionPanel />
    </section>

    <!-- Mobile Bottom Navigation -->
    <nav class="mob-bottom-nav">
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'positions' }" @click="mobileTab = 'positions'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="9" x2="9" y2="21"/></svg>
        <span>Posisi</span>
        <span v-if="livePositions.length" class="mob-badge">{{ livePositions.length }}</span>
      </button>
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'chart' }" @click="mobileTab = 'chart'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        <span>Chart</span>
      </button>
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'orders' }" @click="mobileTab = 'orders'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1" ry="1"/></svg>
        <span>Orders</span>
      </button>
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'llm' }" @click="mobileTab = 'llm'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32 1.41-1.41"/></svg>
        <span>LLM</span>
        <span v-if="store.llmDecisions.length" class="mob-badge">{{ store.llmDecisions.length }}</span>
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
import { ref, computed, onMounted, onUnmounted, watch, onActivated } from 'vue'
import { useRouter } from 'vue-router'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'
import Header from '../components/Header.vue'
import ProfitChart from '../components/ProfitChart.vue'
import RiskGuardPopup from '../components/RiskGuardPopup.vue'
import BracketExitConfirmModal from '../components/BracketExitConfirmModal.vue'
import LlmDecisionPanel from '../components/LlmDecisionPanel.vue'

const store = useTradeStore()
const router = useRouter()
const { getAuthHeaders } = useAuth()

const now = ref(Date.now())
const startTime = ref(Date.now())

const uptimeStr = computed(() => {
  const sec = store.stats.uptime_sec || 0
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
})

// --- [Manual Overrides] ---
async function toggleHarvestMode() {
  const current = store.stats.session_mode
  const next = current === 'HARVEST_ONLY' ? 'SCALPING' : 'HARVEST_ONLY'
  await store.updateBotState('session_mode', next)
}

async function resetDailyTarget() {
  if (confirm('Reset daily target status? Bot will resume scalping if target was hit.')) {
    await store.updateBotState('daily_target_hit', '0')
    await store.updateBotState('session_mode', 'SCALPING')
  }
}

async function resetLosses() {
  if (confirm('Reset consecutive losses counter?')) {
    await store.updateBotState('consecutive_losses', '0')
  }
}

// Mobile tab: positions | chart | orders
const mobileTab = ref('positions')

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

const liveState = ref({
  bot_offline: false,
  net_zero_survival_active: false,
  net_pnl_realized: 0,
  net_pnl_unrealized: 0,
  peak_net_pnl_session: 0,
})

const orders = ref([])

const livePositions = computed(() =>
  store.positions.filter(p => !p.dry_run)
)

const safetyNetPnl = computed(() => liveState.value.net_pnl_realized + liveState.value.net_pnl_unrealized)
const safetyGaugePct = computed(() => {
  const buffer = liveState.value.net_pnl_realized
  if (buffer <= 0) return 0
  return Math.max(0, Math.min(100, (safetyNetPnl.value / buffer) * 100))
})
const safetyIsDanger = computed(() => safetyGaugePct.value < 20)
const safetyGaugeColor = computed(() => safetyGaugePct.value < 20 ? '#ff3366' : '#00ff88')
const dataSourceLabel = computed(() =>
  store.statsSource === 'api' ? 'API FALLBACK' : 'WS REALTIME'
)
const dataSourceClass = computed(() =>
  store.statsSource === 'api' ? 'is-fallback' : 'is-realtime'
)

async function fetchLiveState() {
  try {
    const res = await fetch(`${backendUrl}/api/live/state`, { headers: getAuthHeaders() })
    if (res.ok) {
      const data = await res.json()
      Object.assign(liveState.value, data)

      // Fallback data path: keep chart alive even when socket stats_update is delayed.
      const realized = Number(data.net_pnl_realized || 0)
      const unrealized = Number(data.net_pnl_unrealized || 0)
      const total = Math.round(realized + unrealized)

      store.stats.daily_pnl = realized
      store.stats.unrealized_pnl = unrealized
      store.markApiStatsUsed(7000)

      const now = new Date()
      const timeStr = now.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      const last = store.pnlHistory[store.pnlHistory.length - 1]
      if (!last || last.pnl !== total) {
        store.pnlHistory.push({ time: timeStr, pnl: total })
        if (store.pnlHistory.length > 300) {
          store.pnlHistory.splice(0, store.pnlHistory.length - 300)
        }
      }
    }
  } catch { liveState.value.bot_offline = true }
}

async function fetchOrders() {
  try {
    const res = await fetch(`${backendUrl}/api/orders`, { headers: getAuthHeaders() })
    if (res.ok) orders.value = await res.json()
  } catch {}
}

async function fetchLlmDecisions() {
  try {
    const res = await fetch(`${backendUrl}/api/llm-decisions`, { headers: getAuthHeaders() })
    if (res.ok) {
      const data = await res.json()
      if (Array.isArray(data)) {
        store.llmDecisions = data
      }
    }
  } catch {}
}

function formatNumber(val) { return Number(val || 0).toLocaleString('id-ID') }
function formatPrice(val) { return Number(val || 0).toLocaleString('id-ID') }
function formatPair(pair) { return pair ? pair.replace(/idr$/i, '').toUpperCase() : '-' }

let pollInt = null
let llmPollInt = null
let clockInt = null

onMounted(() => {
  // Live dashboard membutuhkan stream stats_update untuk mengisi pnlHistory/chart.
  // Tanpa koneksi socket ini, chart akan terus "Waiting for data".
  store.connect()
  fetchLiveState()
  fetchOrders()
  fetchLlmDecisions()
  pollInt = setInterval(() => { fetchLiveState(); fetchOrders() }, 5000)
  // Re-poll LLM decisions setiap 30 detik sebagai fallback jika socket putus
  llmPollInt = setInterval(fetchLlmDecisions, 30_000)
  clockInt = setInterval(() => { now.value = Date.now() }, 1000)
})

onUnmounted(() => {
  clearInterval(pollInt)
  clearInterval(llmPollInt)
  if (clockInt) clearInterval(clockInt)
})

// Global FORCE_LOGOUT listener — redirect to login when session is invalidated
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

.ld-main {
  width: 100%;
  flex: 1;
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
  padding: 16px;
  overflow: hidden;
  min-height: 0;
}

.ld-panel {
  background: #0a0c14;
  border: 1px solid #1a1c24;
  border-radius: 16px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.panel-header {
  padding: 16px;
  background: rgba(255, 255, 255, 0.02);
  border-bottom: 1px solid #1a1c24;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.panel-title {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1.5px;
  color: #f5a623;
}

.panel-count {
  background: #f5a623;
  color: #000;
  font-size: 10px;
  font-weight: 800;
  padding: 2px 8px;
  border-radius: 10px;
}

.positions-table-wrap, .orders-table-wrap {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th {
  text-align: left;
  font-size: 10px;
  color: #444;
  padding: 12px 8px;
  border-bottom: 1px solid #1a1c24;
}

td {
  padding: 12px 8px;
  font-size: 13px;
  border-bottom: 1px solid #0d1117;
}

.pair-cell { font-weight: 700; color: #fff; }
.profit { color: #00ff88; }
.loss { color: #ff3366; }

.chart-wrap, .orders-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.orders-wrap { border-top: 1px solid #1a1c24; }

.refresh-btn {
  background: transparent;
  border: 1px solid #333;
  color: #555;
  cursor: pointer;
  border-radius: 4px;
  padding: 2px 8px;
}

.status-chip {
  background: rgba(255, 255, 255, 0.05);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
}

.data-source-badge {
  font-size: 10px;
  letter-spacing: 0.7px;
  font-weight: 800;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
}

.data-source-badge.is-realtime {
  color: #00ff88;
  background: rgba(0, 255, 136, 0.12);
  border-color: rgba(0, 255, 136, 0.35);
}

.data-source-badge.is-fallback {
  color: #f5a623;
  background: rgba(245, 166, 35, 0.12);
  border-color: rgba(245, 166, 35, 0.35);
}

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

/* ─── Mobile card styles (hidden on desktop) ──────── */
.positions-cards { padding: 12px; display: flex; flex-direction: column; gap: 10px; overflow-y: auto; }
.pos-card {
  background: #0d0f1a;
  border: 1px solid #1a1c28;
  border-radius: 14px;
  padding: 14px 16px;
}
.pos-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.pos-pair { font-size: 16px; font-weight: 800; color: #fff; letter-spacing: 1px; }
.pos-pnl { font-size: 16px; font-weight: 800; }
.pos-card-row { display: flex; gap: 12px; }
.pos-stat { display: flex; flex-direction: column; gap: 2px; flex: 1; }
.ps-label { font-size: 9px; font-weight: 700; color: #444; letter-spacing: 1px; text-transform: uppercase; }
.ps-val { font-size: 12px; font-weight: 700; color: #ccc; }

.order-cards { padding: 12px; display: flex; flex-direction: column; gap: 8px; overflow-y: auto; }
.order-card {
  background: #0d0f1a;
  border: 1px solid #1a1c28;
  border-radius: 10px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.oc-pair { font-size: 13px; font-weight: 800; color: #fff; flex: 1; }
.oc-side { font-size: 11px; font-weight: 800; letter-spacing: 0.5px; }
.oc-price { font-size: 12px; color: #888; }

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

  /* On mobile 'orders' tab: show orders full */
  .tab-orders .right-panel { display: flex !important; }
  .tab-orders .chart-wrap { display: none !important; }
  .tab-orders .orders-wrap {
    flex: 1;
    display: flex !important;
    flex-direction: column;
    overflow: hidden;
  }
  .order-cards {
    flex: 1;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

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
}

/* ─── LLM Section (desktop strip + mobile full) ────── */
.llm-section {
  margin: 0 16px 16px;
  max-height: 220px;
  flex-shrink: 0;
}

@media (max-width: 768px) {
  /* On mobile 'llm' tab: show LLM panel fullscreen, hide ld-main */
  .tab-llm .ld-main { display: none !important; }
  .tab-llm .llm-section {
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
