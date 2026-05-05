<template>
  <div class="live-dashboard">
    <Header :stats="store.stats" :connected="store.connected" />
    
    <RiskGuardPopup :socket="store.socket" />

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

    <main class="ld-main">
      <section class="ld-panel positions-panel">
        <div class="panel-header">
          <span class="panel-title">ACTIVE LIVE POSITIONS</span>
          <span class="panel-count">{{ livePositions.length }}</span>
        </div>

        <div class="positions-table-wrap" v-if="livePositions.length">
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
        <div v-else class="empty-state">No active live positions</div>
      </section>

      <section class="ld-panel right-panel">
        <div class="chart-wrap">
          <div class="panel-header">
            <span class="panel-title">LIVE EQUITY FLOW</span>
          </div>
          <ProfitChart />
        </div>

        <div class="orders-wrap">
          <div class="panel-header">
            <span class="panel-title">LIVE ORDER BOOK</span>
            <button class="refresh-btn" @click="fetchOrders">↻</button>
          </div>
          <div class="orders-table-wrap" v-if="orders.length">
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
          <div v-else class="empty-state">No recent orders</div>
        </div>
      </section>
    </main>

    <footer class="ld-footer">
      <span>Session Peak: <strong class="gold">Rp {{ formatNumber(liveState.peak_net_pnl_session) }}</strong></span>
      <span>Bot Status: <strong :class="store.connected ? 'profit' : 'loss'">{{ store.connected ? 'ONLINE' : 'OFFLINE' }}</strong></span>
    </footer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'
import Header from '../components/Header.vue'
import ProfitChart from '../components/ProfitChart.vue'
import RiskGuardPopup from '../components/RiskGuardPopup.vue'

const store = useTradeStore()
const router = useRouter()
const { getAuthHeaders } = useAuth()

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

async function fetchLiveState() {
  try {
    const res = await fetch(`${backendUrl}/api/live/state`, { headers: getAuthHeaders() })
    if (res.ok) Object.assign(liveState.value, await res.json())
  } catch { liveState.value.bot_offline = true }
}

async function fetchOrders() {
  try {
    const res = await fetch(`${backendUrl}/api/orders`, { headers: getAuthHeaders() })
    if (res.ok) orders.value = await res.json()
  } catch {}
}

function formatNumber(val) { return Number(val || 0).toLocaleString('id-ID') }
function formatPrice(val) { return Number(val || 0).toLocaleString('id-ID') }
function formatPair(pair) { return pair ? pair.replace(/idr$/i, '').toUpperCase() : '-' }

let pollInt = null
onMounted(() => {
  fetchLiveState()
  fetchOrders()
  pollInt = setInterval(() => { fetchLiveState(); fetchOrders() }, 5000)
})
onUnmounted(() => clearInterval(pollInt))
</script>

<style scoped>
.live-dashboard {
  min-height: 100vh;
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
  font-size: 13px;
  font-weight: 800;
  border-bottom: 1px solid rgba(255, 51, 102, 0.2);
}

.ld-main {
  flex: 1;
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
  padding: 16px;
  overflow: hidden;
}

.ld-panel {
  background: #0a0c14;
  border: 1px solid #1a1c24;
  border-radius: 16px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
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
  height: 40px;
  background: #0a0c14;
  border-top: 1px solid #1a1c24;
  display: flex;
  align-items: center;
  padding: 0 24px;
  gap: 24px;
  font-size: 10px;
  font-weight: 700;
  color: #333;
}

.gold { color: #f5a623; }

@media (max-width: 1024px) {
  .ld-main { grid-template-columns: 1fr; }
}
</style>
