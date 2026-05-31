<template>
  <header class="trader-header">
    <div class="header-left">
      <div class="brand">
        <span class="brand-dot" :class="isLive ? 'live' : 'dry'"></span>
        <div class="brand-text">
          <span class="main-title">ALGOVIBE</span>
          <span class="sub-title">{{ isLive ? 'LIVE' : 'DRY' }} TRADING</span>
        </div>
      </div>
    </div>

    <div class="header-center hide-mobile">
      <div class="stat-bubble" title="Estimated Total Equity">
        <span class="bubble-label">Total Equity</span>
        <span class="bubble-value highlight">{{ currencySymbol }}{{ formatNumber(stats.total_equity) }}</span>
      </div>
      <div class="stat-bubble" title="Available IDR Balance">
        <span class="bubble-label">Available</span>
        <span class="bubble-value">{{ currencySymbol }}{{ formatNumber(stats.available_idr) }}</span>
      </div>
      <div class="stat-bubble hide-tablet" title="Used IDR Balance">
        <span class="bubble-label">In Position</span>
        <span class="bubble-value">{{ currencySymbol }}{{ formatNumber(stats.idr_used) }}</span>
      </div>
      <div class="stat-bubble hide-tablet">
        <span class="bubble-label">Capital Utilized</span>
        <span class="bubble-value">{{ Number(stats.utilization || 0).toFixed(1) }}%</span>
      </div>

      <div class="header-divider hide-tablet"></div>

      <div class="stat-bubble">
        <span class="bubble-label">Daily PnL</span>
        <span class="bubble-value" :class="pnlClass(stats.daily_pnl)">
          {{ pnlSign(stats.daily_pnl) }}{{ currencySymbol }}{{ formatNumber(Math.abs(stats.daily_pnl)) }}
        </span>
      </div>
      <div class="stat-bubble hide-mobile">
        <span class="bubble-label">Win Rate</span>
        <span class="bubble-value">{{ Number(stats.win_rate || 0).toFixed(1) }}%</span>
      </div>
      <div class="stat-bubble hide-mobile" v-if="gateBadgeText">
        <span class="bubble-label" title="Gate Status">Gate</span>
        <span class="bubble-value" :class="gateBadgeClass">{{ gateBadgeText }}</span>
      </div>
    </div>

    <div class="header-right">
      <div class="connection-status hide-mobile">
        <span class="dot" :class="{ 'is-on': connected }"></span>
        <span class="status-text">{{ connected ? 'WS ONLINE' : 'WS OFFLINE' }}</span>
      </div>

      <!-- Mode Selector -->
      <div class="mode-switch-group">
        <button
          class="mode-btn"
          :class="{ active: !isLive }"
          @click="handleModeSwitch('dry')"
          :disabled="isSwitchingMode"
        >DRY</button>
        <button
          class="mode-btn live-btn"
          :class="{ active: isLive }"
          @click="handleModeSwitch('live')"
          :disabled="isSwitchingMode"
        >
          <span class="btn-text">LIVE</span>
        </button>
      </div>

      <button class="logout-btn" @click="handleLogout" title="Logout">
        ⏻
      </button>
      
      <!-- Mobile Nav Toggle -->
      <button class="mobile-nav-btn show-mobile" @click="$emit('nav-mobile', 'radar')">
        ☰
      </button>
    </div>

    <!-- PIN Confirmation Modal -->
    <teleport to="body">
      <transition name="pin-fade">
        <div v-if="showPinModal" class="pin-overlay" role="dialog" aria-modal="true">
          <div class="pin-backdrop" @click="cancelModeSwitch"></div>
          <div class="pin-card">
            <div class="pin-header">
              <span class="pin-icon">⚠️</span>
              <h2 class="pin-title">LIVE MODE ACTIVATION</h2>
            </div>
            <div class="pin-body">
              <p class="pin-warning">You are about to switch to LIVE trading mode. The bot will use real funds.</p>
              <p class="pin-instruction">Please enter your account password to confirm:</p>
              <div class="pin-input-group">
                <input 
                  type="password" 
                  v-model="pinInput" 
                  class="pin-input password-input" 
                  :class="{ 'has-error': pinError }"
                  placeholder="Password" 
                  @keyup.enter="confirmPin"
                  ref="pinInputRef"
                  autofocus
                />
              </div>
              <p v-if="pinError" class="pin-error-text">Invalid password. Please try again.</p>
            </div>
            <div class="pin-actions">
              <button class="pin-btn pin-btn-cancel" @click="cancelModeSwitch">CANCEL</button>
              <button class="pin-btn pin-btn-confirm" @click="confirmPin">ACTIVATE</button>
            </div>
          </div>
        </div>
      </transition>
    </teleport>
  </header>
</template>

<script setup>
import { computed, ref, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'

const props = defineProps({
  isLive: { type: Boolean, required: true },
  stats: { type: Object, default: () => ({}) },
  connected: { type: Boolean, default: false }
})

const emit = defineEmits(['nav-mobile'])

const router = useRouter()
const store = useTradeStore()
const { getAuthHeaders, logout } = useAuth()
const backendUrl = import.meta.env.VITE_BACKEND_URL || ''

const handleLogout = async () => {
  await logout(backendUrl)
  window.location.href = '/login'
}

const isSwitchingMode = ref(false)

// PIN Modal State
const showPinModal = ref(false)
const pinInput = ref('')
const pinError = ref(false)
const pendingMode = ref(null)
const pinInputRef = ref(null)

const currencySymbol = computed(() => {
  return (props.stats.exchange === 'binance' || (props.stats.btc_price && props.stats.btc_price < 500000)) ? '$' : 'Rp '
})

function formatNumber(val) {
  if (val === undefined || val === null) return '0'
  const isUsdt = props.stats.exchange === 'binance' || (props.stats.btc_price && props.stats.btc_price < 500000)
  if (isUsdt) {
    return new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(val))
  } else {
    return new Intl.NumberFormat('id-ID', { maximumFractionDigits: 0 }).format(Math.round(val))
  }
}

function pnlClass(val) {
  const num = Number(val || 0)
  if (num > 0) return 'text-profit'
  if (num < 0) return 'text-loss'
  return 'text-neutral'
}

function pnlSign(val) {
  const num = Number(val || 0)
  return num > 0 ? '+' : (num < 0 ? '-' : '')
}

const gateBadgeText = computed(() => {
  const state = String(props.stats.entry_gate_state || '')
  if (state === 'GATED') return 'GATED'
  if (state === 'LIVE_ENTRY_EXECUTED') return 'ENTRY EXECUTED'
  return ''
})

const gateBadgeClass = computed(() => {
  const state = String(props.stats.entry_gate_state || '')
  if (state === 'GATED') return 'text-loss'
  if (state === 'LIVE_ENTRY_EXECUTED') return 'text-profit'
  return 'text-neutral'
})

function handleModeSwitch(mode) {
  if (isSwitchingMode.value) return
  if (mode !== 'dry' && mode !== 'live') return
  if ((mode === 'live' && props.isLive) || (mode === 'dry' && !props.isLive)) return

  // Require PIN confirmation when switching to LIVE mode
  if (mode === 'live') {
    pendingMode.value = mode
    pinInput.value = ''
    pinError.value = false
    showPinModal.value = true
    
    // Focus input after modal renders
    setTimeout(() => {
      if (pinInputRef.value) pinInputRef.value.focus()
    }, 100)
    return
  }

  // Switch immediately for DRY mode
  executeModeSwitch(mode)
}

function cancelModeSwitch() {
  showPinModal.value = false
  pendingMode.value = null
  pinInput.value = ''
  pinError.value = false
}

function confirmPin() {
  if (!pinInput.value) {
    pinError.value = true
    if (pinInputRef.value) pinInputRef.value.focus()
    return
  }
  
  const modeToSwitch = pendingMode.value
  executeModeSwitch(modeToSwitch, pinInput.value)
}

async function executeModeSwitch(mode, password = null) {
  isSwitchingMode.value = true
  try {
    const payload = { mode: mode.toUpperCase() }
    if (password) payload.password = password
    
    const res = await fetch(`${backendUrl}/api/bot/select-mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(payload)
    })
    
    if (res.ok) {
      showPinModal.value = false
      pendingMode.value = null
      localStorage.setItem('algovibe_mode', mode)
      window.location.href = mode === 'live' ? '/dashboard-live' : '/dashboard'
    } else {
      if (res.status === 403 || res.status === 401) {
        pinError.value = true
        pinInput.value = ''
        if (pinInputRef.value) pinInputRef.value.focus()
      } else {
        console.error('Failed to switch mode')
        cancelModeSwitch()
      }
    }
  } catch (err) {
    console.error('Failed to switch mode:', err)
    cancelModeSwitch()
  } finally {
    isSwitchingMode.value = false
  }
}

</script>

<style scoped>
.trader-header {
  height: 50px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background: var(--bg-trader-panel);
  border-bottom: 1px solid var(--border-trader);
  position: relative;
  z-index: 10;
}

.header-left, .header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.logout-btn {
  background: transparent;
  border: none;
  color: var(--text-tertiary);
  font-size: 16px;
  cursor: pointer;
  padding: 4px;
  transition: color 0.2s;
}
.logout-btn:hover {
  color: var(--accent-red);
}

.header-center {
  display: flex;
  align-items: center;
  gap: 24px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.brand-dot.live {
  background: var(--color-profit);
  box-shadow: 0 0 10px var(--color-profit);
}
.brand-dot.dry {
  background: var(--accent-purple);
  box-shadow: 0 0 10px var(--accent-purple);
}

.brand-text {
  display: flex;
  flex-direction: column;
}

.main-title {
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 14px;
  color: var(--text-primary);
  letter-spacing: 1px;
}

.sub-title {
  font-size: 10px;
  color: var(--text-secondary);
  letter-spacing: 0.5px;
}

.stat-bubble {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.bubble-label {
  font-size: 11px;
  color: var(--text-tertiary);
  text-transform: uppercase;
}
.bubble-value {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.header-divider {
  width: 1px;
  height: 24px;
  background: var(--border-main);
}

.text-profit { color: var(--color-profit); }
.text-loss { color: var(--color-loss); }
.text-neutral { color: var(--text-secondary); }

.connection-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

.connection-status .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-loss);
}

.connection-status .dot.is-on {
  background: var(--color-profit);
  box-shadow: 0 0 8px var(--color-profit);
}

.mode-switch-group {
  display: flex;
  background: rgba(0, 0, 0, 0.4);
  border-radius: 6px;
  padding: 4px;
  border: 1px solid var(--border-main);
}

.mode-btn {
  background: transparent;
  border: none;
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 700;
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.pin-input {
  width: 120px;
  text-align: center;
  font-size: 24px;
  letter-spacing: 8px;
  padding: 12px;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid var(--border-light);
  border-radius: 8px;
  color: #fff;
  outline: none;
  transition: all 0.2s ease;
  font-family: monospace;
}

.password-input {
  width: 200px;
  font-size: 16px;
  letter-spacing: 2px;
}

.mode-btn.active {
  background: var(--bg-card);
  color: var(--text-primary);
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}

.mode-btn-live.active {
  color: var(--color-profit);
  text-shadow: 0 0 10px rgba(76, 175, 80, 0.5);
}

.show-mobile { display: none; }

@media (max-width: 992px) {
  .hide-mobile { display: none !important; }
  .show-mobile { display: block; }
  .mobile-nav-btn {
    background: transparent;
    border: none;
    color: var(--text-primary);
    font-size: 20px;
  }
}
</style>

