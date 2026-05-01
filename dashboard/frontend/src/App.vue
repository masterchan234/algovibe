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
        <div class="stat-bubble" v-if="store.stats.regime">
          <span class="bubble-label">Regime</span>
          <span class="bubble-value" :class="regimeClass">{{ store.stats.regime }}</span>
        </div>
        <div class="stat-bubble hide-mobile">
          <span class="bubble-label">AI Intelligence</span>
          <span class="bubble-value highlight" style="font-size: 10px; font-weight: 600; color: var(--accent-purple);">
            MEMANTAU PEMILIHAN KOIN BIJAK...
          </span>
        </div>
        <div class="stat-bubble">
          <span class="bubble-label">Uptime</span>
          <span class="bubble-value">{{ uptimeStr }}</span>
        </div>
      </div>

      <div class="header-right">
        <button class="audio-toggle" @click="toggleMute" :title="isMuted ? 'Unmute' : 'Mute'">
          {{ isMuted ? '🔇' : '🔊' }}
        </button>
        <div class="conn-status" :class="{ 'is-live': store.connected }">
          <span class="conn-text">{{ store.connected ? 'TERMINAL ONLINE' : 'LINK LOST' }}</span>
          <span class="conn-dot"></span>
        </div>
      </div>
    </header>
    
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

    <main class="main-grid">
      <section class="left-panel">
        <ActivityFeed />
      </section>
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
        <div class="ticker-tape">
          <div class="ticker-content">
            <span v-for="pair in store.radarPairs" :key="pair.pair" class="ticker-item">
              <span class="t-pair">{{ formatPair(pair.pair) }}</span>
              <span class="t-price">{{ formatNumber(pair.price) }}</span>
              <span class="t-change" :class="pair.change >= 0 ? 'up' : 'down'">
                {{ pair.change >= 0 ? '▲' : '▼' }}{{ Math.abs(pair.change).toFixed(2) }}%
              </span>
            </span>
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
import { onMounted, ref, computed } from 'vue'
import { useTradeStore } from './stores/trade'
import { useAudio } from './composables/useAudio'
import ActivityFeed from './components/ActivityFeed.vue'
import ProfitChart from './components/ProfitChart.vue'
import NotificationCenter from './components/NotificationCenter.vue'

const store = useTradeStore()
const { isMuted, toggleMute } = useAudio()
const startTime = ref(Date.now())
const now = ref(Date.now())

const isDryRun = computed(() => {
  // Usually passed from backend via stats
  return true // Default to dry run for safety visual
})

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

onMounted(() => {
  store.connect()
  setInterval(() => {
    now.value = Date.now()
  }, 1000)
  
  // Mobile Touch listeners
  window.addEventListener('touchstart', handleTouchStart, { passive: true })
  window.addEventListener('touchmove', handleTouchMove, { passive: false })
  window.addEventListener('touchend', handleTouchEnd, { passive: true })
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

.audio-toggle {
  background: var(--bg-hover);
  border: 1px solid var(--border-dim);
  color: var(--text-main);
  width: 32px;
  height: 32px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  transition: all 0.2s;
}

.audio-toggle:hover {
  background: var(--border-dim);
  transform: scale(1.05);
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

@keyframes ptr-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.main-grid {
  display: grid;
  grid-template-columns: 400px 1fr;
  gap: 12px;
  padding: 12px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
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
  white-space: nowrap;
}

.ticker-content {
  display: inline-block;
  animation: ticker 80s linear infinite;
}

.ticker-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-right: 24px;
}

.t-pair { font-weight: 800; color: var(--accent-blue); }
.t-price { font-family: 'JetBrains Mono', monospace; font-size: 10px; }
.t-change.up { color: var(--accent-green); }
.t-change.down { color: var(--accent-red); }

@keyframes ticker {
  0% { transform: translateX(100%); }
  100% { transform: translateX(-100%); }
}

@media (max-width: 1000px) {
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
