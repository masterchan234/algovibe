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
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useMode } from '../composables/useMode'

const props = defineProps({
  stats: { type: Object, default: () => ({}) },
  connected: { type: Boolean, default: false }
})

const router = useRouter()
const { userInfo, logout } = useAuth()
const { selectedMode, isLiveMode } = useMode()

const isLoggingOut = ref(false)

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
</script>

<style scoped>
.app-header {
  height: 64px;
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
  gap: 20px;
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
  .logout-btn { padding: 6px 12px; font-size: 10px; }
}
</style>
