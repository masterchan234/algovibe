<template>
  <header class="app-header" :class="headerClass">
    <div class="header-left">
      <div class="brand" @click="$router.push('/mode-select')" role="button" title="Back to Mode Selection">
        <span class="brand-dot"></span>
        <div class="brand-text">
          <span class="main-title">ALGOVIBE</span>
          <span class="sub-title">{{ modeLabel }} EXECUTION</span>
        </div>
      </div>
    </div>
    
    <div class="header-center hide-mobile">
      <div class="stat-bubble" title="Estimated Total Equity">
        <span class="bubble-label">Total Equity</span>
        <span class="bubble-value highlight">Rp {{ formatNumber(stats.total_equity) }}</span>
      </div>
      <div class="stat-bubble" title="Available IDR Balance">
        <span class="bubble-label">Available</span>
        <span class="bubble-value">Rp {{ formatNumber(stats.available_idr) }}</span>
      </div>
      <div class="stat-bubble" v-if="stats.utilization !== undefined">
        <span class="bubble-label">Utilization</span>
        <span class="bubble-value" :class="stats.utilization > 80 ? 'c-bear' : 'c-bull'">{{ stats.utilization }}%</span>
      </div>
      <div class="stat-bubble" v-if="stats.regime">
        <span class="bubble-label">Regime</span>
        <span class="bubble-value" :class="regimeClass">{{ stats.regime }}</span>
      </div>
      <div class="stat-bubble" v-if="isLiveMode && gateBadgeText">
        <span class="bubble-label">Entry Gate</span>
        <span class="bubble-value" :class="gateBadgeClass" :title="gateTooltip">{{ gateBadgeText }}</span>
      </div>
    </div>

    <div class="header-right">
      <div class="mode-toggle" :class="{ busy: isSwitchingMode }" title="Switch execution mode">
        <button
          class="mode-btn"
          :class="{ active: selectedMode === 'dry' }"
          :disabled="isSwitchingMode"
          @click="handleModeSwitch('dry')"
        >
          DRY
        </button>
        <button
          class="mode-btn"
          :class="{ active: selectedMode === 'live' }"
          :disabled="isSwitchingMode"
          @click="handleModeSwitch('live')"
        >
          LIVE
        </button>
      </div>

      <div class="conn-status" :class="{ 'is-live': connected }" title="WebSocket Connection Status">
        <span class="conn-dot"></span>
      </div>
      
      <div class="user-info hide-mobile">
        <span class="user-name">{{ userInfo?.username || 'admin' }}</span>
        <span class="user-role">{{ userInfo?.role || 'OPERATOR' }}</span>
      </div>

      <button class="logout-btn" @click="handleLogout" :disabled="isLoggingOut">
        <span v-if="isLoggingOut">...</span>
        <span v-else>LOGOUT</span>
      </button>
    </div>
  </header>

  <!-- Mobile stats strip — shown only on small screens -->
  <div class="mobile-stats-strip show-mobile" :class="headerClass">
    <div class="ms-item">
      <span class="ms-label">Equity</span>
      <span class="ms-val highlight">Rp {{ formatNumber(stats.total_equity) }}</span>
    </div>
    <div class="ms-divider"></div>
    <div class="ms-item">
      <span class="ms-label">Available</span>
      <span class="ms-val">Rp {{ formatNumber(stats.available_idr) }}</span>
    </div>
    <div class="ms-divider" v-if="stats.utilization !== undefined"></div>
    <div class="ms-item" v-if="stats.utilization !== undefined">
      <span class="ms-label">Utilization</span>
      <span class="ms-val" :class="stats.utilization > 80 ? 'c-bear' : 'c-bull'">{{ stats.utilization }}%</span>
    </div>
    <div class="ms-divider" v-if="stats.regime"></div>
    <div class="ms-item" v-if="stats.regime">
      <span class="ms-label">Regime</span>
      <span class="ms-val" :class="regimeClass">{{ stats.regime }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useMode } from '../composables/useMode'
import { useTradeStore } from '../stores/trade'

const props = defineProps({
  stats: { type: Object, default: () => ({}) },
  connected: { type: Boolean, default: false }
})

const router = useRouter()
const { userInfo, logout, getAuthHeaders } = useAuth()
const store = useTradeStore()
const { selectedMode, isLiveMode, selectMode } = useMode()

const isLoggingOut = ref(false)
const isSwitchingMode = ref(false)

const headerClass = computed(() => ({
  'header-live': isLiveMode.value,
  'header-dry': !isLiveMode.value
}))

const modeLabel = computed(() => isLiveMode.value ? 'LIVE' : 'SIMULATION')

const regimeClass = computed(() => {
  const r = props.stats.regime || ''
  if (r.includes('BULL')) return 'c-bull'
  if (r.includes('BEAR')) return 'c-bear'
  return 'c-neutral'
})

const gateBadgeText = computed(() => {
  const state = String(props.stats.entry_gate_state || '')
  if (!state) return ''
  if (state === 'GATED') {
    const pair = String(props.stats.entry_gate_pair || '').toUpperCase()
    return pair ? `GATED ${pair}` : 'GATED'
  }
  if (state === 'LIVE_ENTRY_EXECUTED') return 'ENTRY EXECUTED'
  return ''
})

const gateBadgeClass = computed(() => {
  const state = String(props.stats.entry_gate_state || '')
  if (state === 'GATED') return 'c-gated'
  if (state === 'LIVE_ENTRY_EXECUTED') return 'c-bull'
  return 'c-neutral'
})

const gateTooltip = computed(() => {
  const reason = String(props.stats.entry_gate_reason || '').trim()
  const at = String(props.stats.entry_gate_at || '').trim()
  if (reason && at) return `${reason} (${at})`
  if (reason) return reason
  return 'Entry gate status'
})

function formatNumber(val) {
  if (val === undefined || val === null) return '0'
  return new Intl.NumberFormat('id-ID').format(Math.round(val))
}

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

async function handleLogout() {
  if (!confirm('Are you sure you want to logout and terminate the current session?')) return
  
  isLoggingOut.value = true
  try {
    await logout(backendUrl)
    router.push('/login')
  } catch (err) {
    console.error('Logout failed:', err)
  } finally {
    isLoggingOut.value = false
  }
}

async function refreshDashboardState() {
  const headers = getAuthHeaders()

  const [statsRes, tradesRes] = await Promise.all([
    fetch(`${backendUrl}/api/stats`, { headers, credentials: 'include' }),
    fetch(`${backendUrl}/api/trades`, { headers, credentials: 'include' }),
  ])

  if (statsRes.ok) {
    const data = await statsRes.json()
    Object.assign(store.stats, {
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
      latest_llm_insight: data.latest_llm_insight ?? '',
      hero_ai_insight: data.hero_ai_insight ?? 'Waiting for AI analysis...',
      hero_ai_insights: Array.isArray(data.hero_ai_insights) ? data.hero_ai_insights : [],
      active_reasoning: data.active_reasoning ?? '',
      alpha_contributor: data.alpha_contributor ?? '',
      best_trade: data.best_trade ?? 0,
      intensity: data.intensity ?? 0,
      dry_run: data.dry_run ?? true,
      dry_run_loose_gates: data.dry_run_loose_gates ?? false,
    })
    store.markApiStatsUsed(15000)
  }

  if (tradesRes.ok) {
    const trades = await tradesRes.json()
    if (Array.isArray(trades)) {
      store.positions = trades
    }
  }
}

async function handleModeSwitch(mode) {
  if (isSwitchingMode.value) return
  if (mode !== 'dry' && mode !== 'live') return
  if (selectedMode.value === mode) return

  isSwitchingMode.value = true
  try {
    await selectMode(mode, backendUrl)
    await refreshDashboardState()
    router.push(mode === 'live' ? '/dashboard-live' : '/dashboard-dry')
  } catch (err) {
    console.error('Mode switch failed:', err)
    alert(err?.message || 'Failed to switch mode')
  } finally {
    isSwitchingMode.value = false
  }
}
</script>

<style scoped>
.app-header {
  height: 64px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: rgba(10, 12, 20, 0.8);
  backdrop-filter: blur(20px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  position: sticky;
  top: 0;
  z-index: 100;
  transition: all 0.5s ease;
}

.header-live {
  border-bottom: 1px solid rgba(245, 166, 35, 0.2);
  box-shadow: 0 4px 20px rgba(245, 166, 35, 0.05);
}

.header-dry {
  border-bottom: 1px solid rgba(163, 113, 247, 0.2);
  box-shadow: 0 4px 20px rgba(163, 113, 247, 0.05);
}

.header-left {
  display: flex;
  align-items: center;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 8px;
  transition: background 0.3s;
}

.brand:hover {
  background: rgba(255, 255, 255, 0.03);
}

.brand-dot {
  width: 10px;
  height: 10px;
  background: #6366f1;
  border-radius: 50%;
  box-shadow: 0 0 10px #6366f1;
  animation: pulse 2s infinite;
}

.header-live .brand-dot {
  background: #f5a623;
  box-shadow: 0 0 10px #f5a623;
}

@keyframes pulse {
  0% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.2); opacity: 0.7; }
  100% { transform: scale(1); opacity: 1; }
}

.brand-text {
  display: flex;
  flex-direction: column;
}

.main-title {
  font-size: 16px;
  font-weight: 900;
  letter-spacing: 3px;
  color: #fff;
  line-height: 1;
}

.sub-title {
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #555;
  margin-top: 4px;
}

.header-center {
  display: flex;
  align-items: center;
  gap: 20px;
}

.stat-bubble {
  display: flex;
  flex-direction: column;
  padding: 0 16px;
  border-left: 1px solid rgba(255, 255, 255, 0.05);
}

.bubble-label {
  font-size: 9px;
  font-weight: 700;
  color: #444;
  letter-spacing: 1px;
  text-transform: uppercase;
}

.bubble-value {
  font-size: 13px;
  font-weight: 700;
  color: #ccc;
  margin-top: 2px;
}

.bubble-value.highlight {
  color: #fff;
}

.c-bull { color: #00ff88; }
.c-bear { color: #ff3366; }
.c-gated { color: #f5a623; }
.c-neutral { color: #888; }

.header-right {
  display: flex;
  align-items: center;
  gap: 14px;
}

.mode-toggle {
  display: inline-flex;
  align-items: center;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 3px;
}

.mode-toggle.busy {
  opacity: 0.7;
}

.mode-btn {
  border: none;
  background: transparent;
  color: #7d8590;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.8px;
  padding: 6px 10px;
  border-radius: 7px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.mode-btn.active {
  color: #fff;
  background: rgba(163, 113, 247, 0.22);
  box-shadow: inset 0 0 0 1px rgba(163, 113, 247, 0.45);
}

.header-live .mode-btn.active {
  background: rgba(245, 166, 35, 0.22);
  box-shadow: inset 0 0 0 1px rgba(245, 166, 35, 0.45);
}

.mode-btn:disabled {
  cursor: wait;
}

.conn-status {
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.conn-dot {
  width: 6px;
  height: 6px;
  background: #ff3366;
  border-radius: 50%;
  box-shadow: 0 0 5px #ff3366;
}

.conn-status.is-live .conn-dot {
  background: #00ff88;
  box-shadow: 0 0 5px #00ff88;
}

.user-info {
  display: flex;
  flex-direction: column;
  text-align: right;
}

.user-name {
  font-size: 12px;
  font-weight: 700;
  color: #fff;
}

.user-role {
  font-size: 8px;
  font-weight: 800;
  color: #555;
  letter-spacing: 1px;
}

.logout-btn {
  background: rgba(255, 51, 102, 0.1);
  color: #ff3366;
  border: 1px solid rgba(255, 51, 102, 0.3);
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1px;
  cursor: pointer;
  transition: all 0.3s ease;
}

.logout-btn:hover:not(:disabled) {
  background: #ff3366;
  color: #fff;
  box-shadow: 0 0 15px rgba(255, 51, 102, 0.4);
}

.logout-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@media (max-width: 768px) {
  .hide-mobile { display: none; }
  .app-header { padding: 0 16px; }
  .mode-btn { font-size: 9px; padding: 5px 8px; }
  .logout-btn { padding: 6px 12px; font-size: 10px; }
}

/* Mobile stats strip */
.mobile-stats-strip {
  display: none;
  align-items: center;
  padding: 0 16px;
  height: 40px;
  background: rgba(8, 10, 18, 0.95);
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  gap: 0;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
  flex-shrink: 0;
}
.mobile-stats-strip::-webkit-scrollbar { display: none; }

.ms-item {
  display: flex;
  flex-direction: column;
  padding: 0 12px;
  flex-shrink: 0;
}
.ms-label {
  font-size: 8px;
  font-weight: 700;
  color: #444;
  letter-spacing: 1px;
  text-transform: uppercase;
}
.ms-val {
  font-size: 11px;
  font-weight: 700;
  color: #ccc;
  white-space: nowrap;
}
.ms-val.highlight { color: #fff; }
.ms-divider {
  width: 1px;
  height: 24px;
  background: rgba(255, 255, 255, 0.06);
  flex-shrink: 0;
}

@media (max-width: 768px) {
  .show-mobile { display: flex !important; }
}

@media (min-width: 769px) {
  .show-mobile { display: none !important; }
}
</style>
