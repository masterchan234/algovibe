<template>
  <div class="app">
    <header class="app-header">
      <div class="brand">
        <span class="brand-dot"></span>
        <div class="brand-text">
          <span class="main-title">ALGOVIBE</span>
          <span class="sub-title">QUANT EXECUTION SYSTEM</span>
        </div>
      </div>
      
      <div class="header-center hide-mobile">
        <div class="stat-bubble">
          <span class="bubble-label">Total Equity</span>
          <span class="bubble-value highlight">Rp {{ formatNumber(store.stats.total_equity) }}</span>
        </div>
        <div class="stat-bubble">
          <span class="bubble-label">Liquidity</span>
          <span class="bubble-value">Rp {{ formatNumber(store.stats.available_idr) }}</span>
        </div>
        <div class="stat-bubble" v-if="store.stats.utilization !== undefined">
          <span class="bubble-label">Utilization</span>
          <span class="bubble-value" :class="store.stats.utilization > 80 ? 'c-bear' : 'c-bull'">{{ store.stats.utilization }}%</span>
        </div>
        <div class="stat-bubble" v-if="store.stats.regime">
          <span class="bubble-label">Regime</span>
          <span class="bubble-value" :class="regimeClass">{{ store.stats.regime }}</span>
        </div>
        <div class="stat-bubble">
          <span class="bubble-label">Uptime</span>
          <span class="bubble-value">{{ uptimeStr }}</span>
        </div>
      </div>

      <div class="header-right">
        <div class="conn-status" :class="{ 'is-live': store.connected }">
          <span class="conn-text">{{ store.connected ? 'TERMINAL ONLINE' : 'LINK LOST' }}</span>
          <span class="conn-dot"></span>
        </div>
        
        <div class="header-controls">
          <button 
            class="control-btn" 
            :class="{ 'is-live': !store.stats.dry_run, 'is-loading': isToggling }"
            @click="toggleDryRun"
            :disabled="isToggling"
            :title="store.stats.dry_run ? 'Switch to LIVE MODE' : 'Switch to DRY RUN'"
          >
            {{ isToggling ? '⏳ SYNC...' : (store.stats.dry_run ? '🛡️ DRY' : '🔥 LIVE') }}
          </button>
          
          <button 
            class="control-btn loose-btn" 
            :class="{ 'active': store.stats.dry_run_loose_gates }"
            @click="toggleLooseGates"
            title="Toggle Loose Gates"
          >
            🔓
          </button>
        </div>
      </div>
    </header>
    
    <!-- Login Modal Overlay -->
    <div class="login-modal-overlay" v-if="showLoginModal">
      <div class="login-modal">
        <div class="login-header">
          <h3>AUTHENTICATION REQUIRED</h3>
          <p>Please login to modify bot configuration</p>
        </div>
        <form @submit.prevent="handleLogin" class="login-form">
          <div class="form-group">
            <label>Username</label>
            <input type="text" v-model="loginForm.username" placeholder="admin" required />
          </div>
          <div class="form-group">
            <label>Password</label>
            <input type="password" v-model="loginForm.password" placeholder="••••••••" required />
          </div>
          <div class="form-actions">
            <button type="button" @click="showLoginModal = false" class="btn-cancel">CANCEL</button>
            <button type="submit" class="btn-login" :disabled="isLoggingIn">
              {{ isLoggingIn ? 'AUTHENTICATING...' : 'LOGIN' }}
            </button>
          </div>
        </form>
      </div>
    </div>
    
    <!-- Neural Pull-to-Refresh Indicator -->
    <div 
      class="pull-to-refresh" 
      :style="{ height: pullDistance + 'px', opacity: pullDistance / threshold }"
      :class="{ 'is-refreshing': isRefreshing }"
    >
      <div class="ptr-spinner">
        <div class="spinner-core"></div>
        <div class="spinner-ring"></div>
      </div>
      <span class="ptr-label">{{ isRefreshing ? 'RECALIBRATING...' : 'PULL TO REFRESH ORACLE' }}</span>
    </div>

    <NotificationCenter />

    <main class="main-grid" :style="{ '--left-width': leftPanelWidth + 'px' }">
      <section class="left-panel">
        <ActivityFeed />
      </section>
      
      <div class="resizer" @mousedown="startResize" :class="{ 'is-active': isResizing }"></div>

      <section class="right-panel">
        <ProfitChart />
      </section>
    </main>

    <footer class="app-footer">
      <div class="footer-left">
        <div class="mode-container">
          <span class="mode-badge" :class="{ 'dry-run': isDryRun }">
            {{ isDryRun ? 'SIMULATION MODE (DRY RUN)' : 'PRODUCTION MODE (LIVE)' }}
            <span class="info-icon">ⓘ</span>
          </span>
          
          <!-- Technical Tooltip -->
          <div class="mode-tooltip" v-if="isDryRun">
            <div class="tooltip-header">SIMULATION BLUEPRINT</div>
            <div class="tooltip-body">
              <div class="tooltip-row">
                <span class="t-bullet">🛡️</span>
                <span class="t-text"><strong>Zero Capital Risk:</strong> Eksekusi bersifat virtual, saldo Indodax tidak tersentuh.</span>
              </div>
              <div class="tooltip-row">
                <span class="t-bullet">📡</span>
                <span class="t-text"><strong>Real Market Data:</strong> Menggunakan stream WebSocket real-time untuk akurasi harga.</span>
              </div>
              <div class="tooltip-row">
                <span class="t-bullet">🔓</span>
                <span class="t-text"><strong>Loose Gates:</strong> Ambang batas entry dilonggarkan untuk pengujian dashboard.</span>
              </div>
              <div class="tooltip-row">
                <span class="t-bullet">🧠</span>
                <span class="t-text"><strong>AI Active:</strong> Oracle tetap memberikan nalar dan strategi secara penuh.</span>
              </div>
            </div>
            <div class="tooltip-footer">Set DRY_RUN=False di config.py untuk trading riil.</div>
          </div>
        </div>
      </div>
      <div class="footer-center">
        <div class="ticker-tape" ref="tickerTape">
          <div class="ticker-content" ref="tickerContent">
            <template v-if="newsHeadlines.length">
              <span v-for="(item, i) in newsHeadlines" :key="i" class="ticker-item">
                <span class="t-source">[{{ item.source }}]</span>
                <span class="t-title">{{ item.title }}</span>
                <span class="t-sep">&middot;</span>
              </span>
              <span v-for="(item, i) in newsHeadlines" :key="'b'+i" class="ticker-item" aria-hidden="true">
                <span class="t-source">[{{ item.source }}]</span>
                <span class="t-title">{{ item.title }}</span>
                <span class="t-sep">&middot;</span>
              </span>
            </template>
            <template v-else>
              <span v-for="pair in store.radarPairs" :key="pair.pair" class="ticker-item">
                <span class="t-pair">{{ formatPair(pair.pair) }}</span>
                <span class="t-price">{{ formatNumber(pair.price) }}</span>
                <span class="t-change" :class="pair.change >= 0 ? 'up' : 'down'">
                  {{ pair.change >= 0 ? '\u25b2' : '\u25bc' }}{{ Math.abs(pair.change).toFixed(2) }}%
                </span>
              </span>
              <span v-for="pair in store.radarPairs" :key="'b'+pair.pair" class="ticker-item" aria-hidden="true">
                <span class="t-pair">{{ formatPair(pair.pair) }}</span>
                <span class="t-price">{{ formatNumber(pair.price) }}</span>
                <span class="t-change" :class="pair.change >= 0 ? 'up' : 'down'">
                  {{ pair.change >= 0 ? '\u25b2' : '\u25bc' }}{{ Math.abs(pair.change).toFixed(2) }}%
                </span>
              </span>
            </template>
          </div>
        </div>
      </div>
      <div class="footer-right">
        V1.4.2-STABLE
      </div>
    </footer>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, reactive, computed, watch, nextTick } from 'vue'
import { useTradeStore } from './stores/trade'
import ActivityFeed from './components/ActivityFeed.vue'
import ProfitChart from './components/ProfitChart.vue'
import NotificationCenter from './components/NotificationCenter.vue'

const store = useTradeStore()
const newsHeadlines = ref([])
const tickerContent = ref(null)
const tickerTape = ref(null)
let _rafId = null
let _tickerX = 0
const TICKER_PX_PER_FRAME = 0.5

function _runTicker() {
  const el = tickerContent.value
  if (!el) return
  const half = el.scrollWidth / 2
  if (half <= 0) { _rafId = requestAnimationFrame(_runTicker); return }
  _tickerX -= TICKER_PX_PER_FRAME
  if (Math.abs(_tickerX) >= half) _tickerX = 0
  el.style.transform = `translateX(${_tickerX}px)`
  _rafId = requestAnimationFrame(_runTicker)
}

function _startTicker() {
  if (_rafId) return
  _tickerX = 0
  _runTicker()
}

watch([newsHeadlines, () => store.radarPairs], async () => {
  await nextTick()
  _tickerX = 0  // reset position on content change
})

async function fetchNews() {
  try {
    const resp = await fetch('/api/news')
    if (!resp.ok) return
    const items = await resp.json()
    if (Array.isArray(items) && items.length) newsHeadlines.value = items
  } catch (e) { /* fallback ke radarPairs */ }
}
const startTime = ref(Date.now())
const now = ref(Date.now())
const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

function getCookie(name) {
  const value = `; ${document.cookie}`
  const parts = value.split(`; ${name}=`)
  if (parts.length === 2) return parts.pop().split(';').shift()
}

const showLoginModal = ref(false)
const isLoggingIn = ref(false)
const loginForm = reactive({ username: '', password: '' })
const isToggling = ref(false)
async function handleLogin() {
  isLoggingIn.value = true
  try {
    const res = await fetch(`${backendUrl}/api/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrf_token')
      },
      body: JSON.stringify(loginForm)
    })
    if (res.ok) {
      showLoginModal.value = false
      alert('Login successful! You can now toggle the mode.')
    } else {
      const err = await res.json()
      alert(`Login failed: ${err.error || 'Invalid credentials'}`)
    }
  } catch (e) {
    alert('Login error')
  } finally {
    isLoggingIn.value = false
  }
}

const isDryRun = computed(() => {
  return store.stats.dry_run
})

async function toggleDryRun() {
  console.log("🚀 [UI] Toggle Mode Requested")
  isToggling.value = true
  try {
    const res = await fetch(`${backendUrl}/api/bot/toggle-dry-run`, { 
      method: 'POST',
      credentials: 'include',
      headers: {
        'X-CSRFToken': getCookie('csrf_token')
      }
    })
    console.log("📡 [UI] Server Response Status:", res.status)
    if (res.ok) {
      const data = await res.json()
      console.log("✅ [UI] Mode Changed Successfully:", data.dry_run)
      store.stats.dry_run = data.dry_run
      
      // Force reload to ensure all data & sockets are reset for the new mode
      setTimeout(() => {
        window.location.reload()
      }, 500)
    } else {
      console.error("❌ [UI] Server Rejected Request")
      if (res.status === 401) {
        showLoginModal.value = true
      } else {
        alert("Server rejected the request. Please login first if needed.")
      }
    }
  } catch (e) {
    console.error("❌ [UI] Fetch Error:", e)
    alert("Network error. Is the dashboard server running?")
  } finally {
    isToggling.value = false
  }
}

async function toggleLooseGates() {
  try {
    const res = await fetch(`${backendUrl}/api/bot/toggle-loose-gates`, { 
      method: 'POST',
      credentials: 'include',
      headers: {
        'X-CSRFToken': getCookie('csrf_token')
      }
    })
    if (res.ok) {
      const data = await res.json()
      store.stats.dry_run_loose_gates = data.loose_gates
      alert(`Loose Gates: ${data.loose_gates ? 'ON' : 'OFF'}`)
    }
  } catch (e) {
    console.error('Failed to toggle loose gates', e)
  }
}

const uptimeStr = computed(() => {
  const start = store.stats.first_trade_ts || startTime.value
  const diff = Math.floor((now.value - start) / 1000)
  if (diff < 0) return '00:00:00'
  
  const days = Math.floor(diff / 86400)
  const hours = Math.floor((diff % 86400) / 3600)
  const minutes = Math.floor((diff % 3600) / 60)
  const seconds = diff % 60
  
  const h = hours.toString().padStart(2, '0')
  const m = minutes.toString().padStart(2, '0')
  const s = seconds.toString().padStart(2, '0')
  
  if (days > 0) return `${days}d ${h}:${m}:${s}`
  return `${h}:${m}:${s}`
})

const regimeClass = computed(() => {
  const r = store.stats.regime
  if (r === 'BULL') return 'c-bull'
  if (r === 'BEAR') return 'c-bear'
  return 'c-choppy'
})

function formatPair(pair) {
  if (!pair) return '-'
  return pair.replace(/idr$/i, '').replace(/usdt$/i, '').toUpperCase()
}

function formatNumber(val) {
  return Number(val || 0).toLocaleString('id-ID')
}

// Pull-to-Refresh Logic
const pullDistance = ref(0)
const isRefreshing = ref(false)
const threshold = 70
let startY = 0

function handleTouchStart(e) {
  if (document.documentElement.scrollTop <= 0) {
    startY = e.touches[0].pageY
  }
}

function handleTouchMove(e) {
  if (isRefreshing.value || document.documentElement.scrollTop > 0) return
  const currentY = e.touches[0].pageY
  const dist = currentY - startY
  if (dist > 0) {
    pullDistance.value = Math.min(dist * 0.5, threshold + 20)
    // Prevent default scroll when pulling down
    if (dist > 10) e.stopPropagation()
  }
}

function handleTouchEnd() {
  if (isRefreshing.value) return
  if (pullDistance.value >= threshold) {
    triggerRefresh()
  } else {
    pullDistance.value = 0
  }
}

async function triggerRefresh() {
  isRefreshing.value = true
  pullDistance.value = threshold
  
  try {
    // Force re-fetch stats and AI report
    store.socket.emit('get_stats')
    // Simulate short network delay for UX satisfaction
    await new Promise(r => setTimeout(r, 1200))
  } finally {
    isRefreshing.value = false
    pullDistance.value = 0
  }
}

// Resizer Logic
const leftPanelWidth = ref(400)
const isResizing = ref(false)

function startResize(e) {
  isResizing.value = true
  window.addEventListener('mousemove', handleMouseMove)
  window.addEventListener('mouseup', stopResize)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

function handleMouseMove(e) {
  if (!isResizing.value) return
  // Limit min 300px, max 800px or 50% of window
  const newWidth = Math.max(300, Math.min(800, e.clientX))
  leftPanelWidth.value = newWidth
}

function stopResize() {
  isResizing.value = false
  window.removeEventListener('mousemove', handleMouseMove)
  window.removeEventListener('mouseup', stopResize)
  document.body.style.cursor = 'default'
  document.body.style.userSelect = 'auto'
}

onMounted(() => {
  store.connect()
  fetchNews()
  setInterval(fetchNews, 5 * 60 * 1000)
  setInterval(() => { now.value = Date.now() }, 1000)
  _startTicker()

  // Mobile Touch listeners
  window.addEventListener('touchstart', handleTouchStart, { passive: true })
  window.addEventListener('touchmove', handleTouchMove, { passive: false })
  window.addEventListener('touchend', handleTouchEnd, { passive: true })
})

onUnmounted(() => {
  if (_rafId) cancelAnimationFrame(_rafId)
})
</script>

<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono:wght@500;700&display=swap');

:root {
  --bg-main: #0d1117;
  --bg-panel: #161b22;
  --bg-hover: #21262d;
  --border-dim: #30363d;
  --text-main: #e6edf3;
  --text-dim: #8b949e;
  --accent-blue: #58a6ff;
  --accent-green: #3fb950;
  --accent-red: #f85149;
  --accent-purple: #a371f7;
  --accent-yellow: #d29922;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: 'Inter', -apple-system, sans-serif;
  background: var(--bg-main);
  color: var(--text-main);
  -webkit-font-smoothing: antialiased;
}

/* Custom Scrollbar */
::-webkit-scrollbar {
  width: 6px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: var(--border-dim);
  border-radius: 10px;
}
::-webkit-scrollbar-thumb:hover {
  background: var(--text-dim);
}
</style>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: radial-gradient(circle at top right, rgba(88, 166, 255, 0.05) 0%, transparent 40%),
              radial-gradient(circle at bottom left, rgba(163, 113, 247, 0.03) 0%, transparent 30%);
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 56px;
  background: rgba(13, 17, 23, 0.8);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-dim);
  flex-shrink: 0;
  z-index: 100;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-dot {
  width: 10px;
  height: 10px;
  background: var(--accent-blue);
  border-radius: 50%;
  box-shadow: 0 0 12px var(--accent-blue);
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.8); }
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1;
}

.main-title {
  font-weight: 900;
  font-size: 18px;
  letter-spacing: 2px;
  background: linear-gradient(90deg, var(--accent-blue), #fff);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.sub-title {
  font-size: 9px;
  font-weight: 700;
  color: var(--text-dim);
  letter-spacing: 1px;
}

.header-center {
  display: flex;
  gap: 20px;
}

.stat-bubble {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 80px;
}

.bubble-label {
  font-size: 9px;
  font-weight: 800;
  text-transform: uppercase;
  color: var(--text-dim);
  letter-spacing: 0.5px;
}

.bubble-value {
  font-size: 13px;
  font-weight: 700;
  font-family: 'JetBrains Mono', monospace;
}

.highlight { color: var(--accent-blue); }
.c-bull { color: var(--accent-green); }
.c-bear { color: var(--accent-red); }
.c-choppy { color: var(--accent-yellow); }

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}


.conn-status {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 14px;
  background: rgba(248, 81, 73, 0.1);
  border: 1px solid rgba(248, 81, 73, 0.2);
  border-radius: 8px;
  transition: all 0.5s ease;
}

.conn-status.is-live {
  background: rgba(63, 185, 80, 0.1);
  border-color: rgba(63, 185, 80, 0.2);
}

.conn-text {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1px;
  color: var(--accent-red);
}

.is-live .conn-text { color: var(--accent-green); }

.conn-dot {
  width: 6px;
  height: 6px;
  background: var(--accent-red);
  border-radius: 50%;
}

.is-live .conn-dot { 
  background: var(--accent-green); 
  box-shadow: 0 0 8px var(--accent-green);
  animation: blink 1s ease-in-out infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.header-controls {
  display: flex;
  gap: 8px;
}

.control-btn {
  background: var(--bg-hover);
  border: 1px solid var(--border-dim);
  color: var(--text-main);
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 800;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.control-btn:hover {
  background: var(--border-dim);
  border-color: var(--text-dim);
}

.control-btn.is-live {
  background: rgba(248, 81, 73, 0.1);
  color: var(--accent-red);
  border-color: rgba(248, 81, 73, 0.3);
  box-shadow: 0 0 10px rgba(248, 81, 73, 0.1);
}

.control-btn.is-live:hover {
  background: rgba(248, 81, 73, 0.2);
}

.loose-btn {
  width: 32px;
  padding: 0;
  opacity: 0.5;
}

.loose-btn.active {
  opacity: 1;
  background: rgba(88, 166, 255, 0.1);
  color: var(--accent-blue);
  border-color: var(--accent-blue);
}

/* Pull to Refresh UI */
.pull-to-refresh {
  overflow: hidden;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(163, 113, 247, 0.05);
  transition: height 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  position: relative;
}

.pull-to-refresh.is-refreshing {
  height: 70px !important;
}

.ptr-spinner {
  position: relative;
  width: 24px;
  height: 24px;
}

.spinner-core {
  position: absolute;
  inset: 6px;
  background: var(--accent-purple);
  border-radius: 50%;
  box-shadow: 0 0 10px var(--accent-purple);
}

.spinner-ring {
  position: absolute;
  inset: 0;
  border: 2px solid rgba(163, 113, 247, 0.2);
  border-top-color: var(--accent-purple);
  border-radius: 50%;
}

.is-refreshing .spinner-ring {
  animation: ptr-rotate 0.8s linear infinite;
}

.ptr-label {
  font-size: 9px;
  font-weight: 800;
  color: var(--accent-purple);
  letter-spacing: 1px;
}

/* Login Modal */
.login-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.8);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
}

.login-modal {
  width: 360px;
  background: var(--bg-panel);
  border: 1px solid var(--border-dim);
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
}

.login-header h3 {
  font-size: 16px;
  font-weight: 900;
  color: var(--accent-blue);
  margin-bottom: 4px;
}

.login-header p {
  font-size: 11px;
  color: var(--text-dim);
  margin-bottom: 24px;
}

.form-group {
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-group label {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-dim);
  text-transform: uppercase;
}

.form-group input {
  background: var(--bg-main);
  border: 1px solid var(--border-dim);
  border-radius: 6px;
  padding: 10px 12px;
  color: var(--text-main);
  font-family: inherit;
  font-size: 14px;
}

.form-group input:focus {
  outline: none;
  border-color: var(--accent-blue);
}

.form-actions {
  display: flex;
  gap: 12px;
  margin-top: 24px;
}

.btn-cancel, .btn-login {
  flex: 1;
  padding: 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-cancel {
  background: transparent;
  border: 1px solid var(--border-dim);
  color: var(--text-dim);
}

.btn-cancel:hover {
  background: var(--bg-hover);
  color: var(--text-main);
}

.btn-login {
  background: var(--accent-blue);
  border: none;
  color: #000;
}

.btn-login:hover {
  filter: brightness(1.2);
}

.btn-login:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@keyframes ptr-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.main-grid {
  display: grid;
  grid-template-columns: var(--left-width, 400px) 4px 1fr;
  gap: 8px;
  padding: 12px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.resizer {
  width: 4px;
  cursor: col-resize;
  background: transparent;
  transition: background 0.2s;
  border-radius: 2px;
  margin: 0 -4px;
  z-index: 10;
}

.resizer:hover, .resizer.is-active {
  background: var(--accent-blue);
  box-shadow: 0 0 8px var(--accent-blue);
}

.resizer:hover, .resizer.is-active {
  background: var(--accent-blue);
  box-shadow: 0 0 8px var(--accent-blue);
}

.left-panel,
.right-panel {
  min-height: 0;
  overflow: hidden;
}

.app-footer {
  height: 32px;
  background: var(--bg-panel);
  border-top: 1px solid var(--border-dim);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-dim);
}

.mode-badge {
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 800;
  font-size: 10px;
  background: rgba(163, 113, 247, 0.15);
  color: var(--accent-purple);
  border: 1px solid rgba(163, 113, 247, 0.3);
}

.mode-badge.dry-run {
  background: rgba(210, 153, 34, 0.15);
  color: var(--accent-yellow);
  border-color: rgba(210, 153, 34, 0.3);
}

.info-icon {
  margin-left: 6px;
  font-size: 11px;
  opacity: 0.7;
}

/* Advanced Tooltip Styling */
.mode-container {
  position: relative;
  display: inline-block;
}

.mode-tooltip {
  position: absolute;
  bottom: 150%;
  left: 0;
  width: 280px;
  background: #1c2128;
  border: 1px solid var(--border-dim);
  border-radius: 8px;
  padding: 16px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.6);
  z-index: 1000;
  opacity: 0;
  visibility: hidden;
  transform: translateY(10px);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  pointer-events: none;
}

.mode-container:hover .mode-tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateY(0);
}

.tooltip-header {
  font-size: 10px;
  font-weight: 900;
  color: var(--accent-blue);
  letter-spacing: 1.5px;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-dim);
}

.tooltip-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tooltip-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.t-bullet { font-size: 14px; }
.t-text {
  font-size: 11px;
  line-height: 1.4;
  color: var(--text-main);
}
.t-text strong { color: var(--accent-purple); }

.tooltip-footer {
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-dim);
  font-size: 9px;
  font-style: italic;
  color: var(--text-dim);
}

.footer-center {
  flex: 1;
  overflow: hidden;
  margin: 0 40px;
}

.ticker-tape {
  width: 100%;
  overflow: hidden;
  white-space: nowrap;
}

.ticker-content {
  display: inline-flex;
  will-change: transform;
  white-space: nowrap;
}

.ticker-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-right: 32px;
  flex-shrink: 0;
}

.t-pair { font-weight: 800; color: var(--accent-blue); }
.t-price { font-family: 'JetBrains Mono', monospace; font-size: 10px; }
.t-change.up { color: var(--accent-green); }
.t-change.down { color: var(--accent-red); }
.t-source { font-weight: 700; color: var(--accent-blue); margin-right: 4px; }
.t-title { color: var(--text-secondary, #c9cdd8); }
.t-sep { color: rgba(255,255,255,0.2); margin: 0 12px; }

@keyframes ticker {
  0%   { transform: translateX(0); }
  100% { transform: translateX(-50%); }
}
/* ticker now driven by requestAnimationFrame — keyframe kept as fallback */

@media (max-width: 1000px) {
  .resizer { display: none; }
  .app {
    height: auto;
    overflow-y: auto;
  }
  
  .app-header {
    padding: 0 16px;
    height: 50px;
  }

  .main-title { font-size: 16px; }
  .sub-title { display: none; }
  
  .hide-mobile { display: none; }
  
  .main-grid {
    grid-template-columns: 1fr;
    padding: 8px;
    gap: 8px;
    height: auto;
    overflow: visible;
  }
  
  .left-panel {
    order: 2;
    height: 500px;
  }
  
  .right-panel {
    order: 1;
    min-height: 400px;
  }

  .app-footer {
    height: auto;
    flex-direction: column;
    padding: 8px;
    gap: 10px;
  }
  
  .footer-center {
    width: 100%;
    margin: 0;
    order: 3;
  }
  
  .footer-left { order: 1; }
  .footer-right { order: 2; font-size: 8px; opacity: 0.5; }
  
  .ticker-item { margin-right: 16px; }
}
</style>
