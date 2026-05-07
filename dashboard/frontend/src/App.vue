<template>
  <div class="app" :class="{ 'mobile-tab-activity': mobileTab === 'activity', 'mobile-tab-chart': mobileTab === 'chart' }">
    <Header :stats="store.stats" :connected="store.connected" />
    
    <NotificationCenter />
    <RiskGuardPopup :socket="store.socket" />
    <ApprovalGateModal />
    <StrategicBreakModal />

    <main class="main-grid" :style="{ '--left-width': leftPanelWidth + 'px' }">
      <section class="left-panel" :class="{ 'mobile-hidden': mobileTab !== 'activity' }">
        <ActivityFeed />
      </section>
      
      <div class="resizer hide-mobile" @mousedown="startResize" :class="{ 'is-active': isResizing }"></div>

      <section class="right-panel" :class="{ 'mobile-hidden': mobileTab !== 'chart' }">
        <ProfitChart />
      </section>
    </main>

    <!-- Mobile Bottom Navigation -->
    <nav class="mobile-bottom-nav show-mobile">
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'activity' }" @click="mobileTab = 'activity'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 10h16M4 14h10"/></svg>
        <span>Feed</span>
      </button>
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'chart' }" @click="mobileTab = 'chart'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        <span>Chart</span>
      </button>
    </nav>

    <footer class="app-footer hide-mobile">
      <div class="footer-left">
        <div class="mode-container">
          <span class="mode-badge dry-run">
            SIMULATION MODE (DRY RUN)
            <span class="info-icon">ⓘ</span>
          </span>
          <div class="mode-tooltip">
            <div class="tooltip-header">SIMULATION BLUEPRINT</div>
            <div class="tooltip-body">
              <div class="tooltip-row">
                <span class="t-bullet">🛡️</span>
                <span class="t-text"><strong>Zero Capital Risk:</strong> Eksekusi bersifat virtual.</span>
              </div>
              <div class="tooltip-row">
                <span class="t-bullet">📡</span>
                <span class="t-text"><strong>Real Market Data:</strong> WebSocket stream real-time.</span>
              </div>
            </div>
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
            </template>
            <template v-else>
              <span v-for="pair in store.radarPairs" :key="pair.pair" class="ticker-item">
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
      <div class="footer-right hide-mobile">
        <div class="system-stats">
          <span class="stat-item">UPTIME: <span class="v">{{ uptimeStr }}</span></span>
          <span class="stat-item">LATENCY: <span class="v">{{ store.latency || 0 }}ms</span></span>
        </div>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useTradeStore } from './stores/trade'
import { useAuth } from './composables/useAuth'

import Header from './components/Header.vue'
import ActivityFeed from './components/ActivityFeed.vue'
import ProfitChart from './components/ProfitChart.vue'
import NotificationCenter from './components/NotificationCenter.vue'
import RiskGuardPopup from './components/RiskGuardPopup.vue'
import ApprovalGateModal from './components/ApprovalGateModal.vue'
import StrategicBreakModal from './components/StrategicBreakModal.vue'

const store = useTradeStore()
const router = useRouter()
const { userInfo, isAuthenticated, getAuthHeaders, logout: authLogout } = useAuth()

// Mobile tab state
const mobileTab = ref('activity')

const newsHeadlines = ref([])
const startTime = ref(Date.now())
const now = ref(Date.now())
let newsIntervalId = null
let clockIntervalId = null

async function fetchNews() {
  try {
    const resp = await fetch('/api/news', { headers: getAuthHeaders() })
    if (resp.ok) {
      const items = await resp.json()
      if (Array.isArray(items)) newsHeadlines.value = items
    }
  } catch (e) {}
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
  return days > 0 ? `${days}d ${h}:${m}:${s}` : `${h}:${m}:${s}`
})

function formatPair(pair) {
  if (!pair) return '-'
  return pair.replace(/idr$/i, '').toUpperCase()
}

function formatNumber(val) {
  return Number(val || 0).toLocaleString('id-ID')
}

// Resizer Logic
const leftPanelWidth = ref(400)
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

onMounted(() => {
  store.connect()
  fetchNews()
  newsIntervalId = setInterval(fetchNews, 5 * 60 * 1000)
  clockIntervalId = setInterval(() => { now.value = Date.now() }, 1000)
})

// Global FORCE_LOGOUT listener — redirect to login when session is invalidated
watch(() => store.forceLogout, (triggered) => {
  if (triggered) {
    router.push('/login')
  }
})

onUnmounted(() => {
  if (newsIntervalId) {
    clearInterval(newsIntervalId)
    newsIntervalId = null
  }
  if (clockIntervalId) {
    clearInterval(clockIntervalId)
    clockIntervalId = null
  }
  // Ensure no dangling listeners when user leaves during drag-resize.
  stopResize()
})
</script>

<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono:wght@500;700&display=swap');

:root {
  --bg-main: #060810;
  --bg-panel: #0a0c14;
  --bg-hover: #12141c;
  --border-dim: #1a1c24;
  --text-main: #ffffff;
  --text-dim: #555555;
  --accent-blue: #6366f1;
  --accent-green: #00ff88;
  --accent-red: #ff3366;
  --accent-purple: #a371f7;
  --accent-yellow: #f5a623;
}

body {
  margin: 0;
  background: var(--bg-main);
  color: var(--text-main);
  font-family: 'Inter', sans-serif;
  overflow: hidden; /* Desktop: panels are fixed-height, no body scroll needed */
}

.app {
  height: 100%;
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
}

.main-grid {
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
  background: var(--bg-panel);
  border-radius: 16px;
  border: 1px solid var(--border-dim);
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
  background: var(--border-dim);
  border-radius: 1px;
}

.resizer:hover::after, .resizer.is-active::after {
  background: var(--accent-blue);
  box-shadow: 0 0 10px var(--accent-blue);
}

.right-panel {
  flex: 1;
  min-height: 0;
  background: var(--bg-panel);
  border-radius: 16px;
  border: 1px solid var(--border-dim);
  overflow: hidden;
}

.app-footer {
  height: 48px;
  background: var(--bg-panel);
  border-top: 1px solid var(--border-dim);
  display: flex;
  align-items: center;
  padding: 0 24px;
  gap: 24px;
  flex-shrink: 0;
}

.footer-left {
  flex: 0 0 240px;
  min-width: 200px;
}
.footer-right {
  flex: 0 0 240px;
  min-width: 180px;
  display: flex;
  justify-content: flex-end;
}

.mode-container {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.mode-badge {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1px;
  color: var(--accent-purple);
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.info-icon {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 1px solid rgba(163, 113, 247, 0.4);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 9px;
  line-height: 1;
}

.mode-tooltip {
  position: absolute;
  left: 0;
  bottom: calc(100% + 10px);
  width: 320px;
  background: #11141d;
  border: 1px solid #252938;
  border-radius: 12px;
  padding: 10px 12px;
  opacity: 0;
  transform: translateY(8px);
  pointer-events: none;
  transition: opacity 0.2s ease, transform 0.2s ease;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
  z-index: 20;
}

.mode-container:hover .mode-tooltip {
  opacity: 1;
  transform: translateY(0);
}

.tooltip-header {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1px;
  color: #f5a623;
  margin-bottom: 8px;
}

.tooltip-body {
  display: grid;
  gap: 6px;
}

.tooltip-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: #c9d1d9;
  font-size: 11px;
  line-height: 1.35;
}

.t-bullet {
  font-size: 11px;
  line-height: 1.35;
}

.t-text strong {
  color: #fff;
}

.footer-center {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.ticker-tape {
  width: 100%;
  white-space: nowrap;
  overflow: hidden;
}

.ticker-content {
  display: inline-block;
  white-space: nowrap;
  min-width: 100%;
}

.ticker-item {
  display: inline-block;
  margin-right: 40px;
  font-size: 12px;
  font-family: 'JetBrains Mono', monospace;
}

.t-source { color: #8b949e; margin-right: 6px; }
.t-title { color: #d2d7de; }
.t-sep { color: #6e7681; margin-left: 10px; }


.t-pair { color: #888; margin-right: 8px; }
.t-price { color: #fff; font-weight: 700; }
.t-change.up { color: var(--accent-green); }
.t-change.down { color: var(--accent-red); }

.system-stats {
  display: flex;
  gap: 20px;
  white-space: nowrap;
}

.stat-item {
  font-size: 10px;
  font-weight: 700;
  color: #444;
  letter-spacing: 1px;
}

.stat-item .v { color: #888; }

@media (max-width: 1300px) {
  .app-footer {
    gap: 14px;
    padding: 0 14px;
  }

  .footer-left {
    flex-basis: 190px;
    min-width: 160px;
  }

  .footer-right {
    flex-basis: 180px;
    min-width: 150px;
  }

  .mode-badge {
    font-size: 9px;
    letter-spacing: 0.7px;
  }

  .system-stats {
    gap: 12px;
  }

  .stat-item {
    font-size: 9px;
    letter-spacing: 0.7px;
  }
}

@media (max-width: 1080px) {
  .footer-left {
    display: none;
  }

  .footer-center {
    flex: 1;
  }

  .footer-right {
    flex-basis: auto;
    min-width: 150px;
  }
}

@media (max-width: 768px) {
  body { overflow: hidden; }

  .hide-mobile { display: none !important; }
  .show-mobile { display: flex !important; }

  .app {
    height: 100%;
    height: 100dvh;
  }

  .main-grid {
    flex-direction: column;
    padding: 8px;
    overflow: hidden;
    gap: 8px;
    min-height: 0;
  }

  /* Only one panel visible at a time via bottom nav */
  .left-panel {
    width: 100% !important;
    min-width: 0;
    height: 100%;
    border-radius: 0;
    border-left: none;
    border-right: none;
    border-top: 1px solid var(--border-dim);
    border-radius: 12px;
  }

  .right-panel {
    width: 100% !important;
    height: 100%;
    border-radius: 0;
    border-left: none;
    border-right: none;
    border-top: 1px solid var(--border-dim);
    border-radius: 12px;
  }

  .mobile-hidden {
    display: none !important;
  }

  /* Mobile Bottom Navigation */
  .mobile-bottom-nav {
    height: 56px;
    height: calc(56px + env(safe-area-inset-bottom));
    padding-bottom: env(safe-area-inset-bottom);
    background: rgba(10, 12, 20, 0.95);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-top: 1px solid var(--border-dim);
    display: flex;
    align-items: center;
    justify-content: space-around;
    z-index: 200;
    flex-shrink: 0;
  }

  .mob-nav-btn {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    background: transparent;
    border: none;
    color: #555;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    cursor: pointer;
    padding: 8px 0;
    transition: color 0.2s;
    -webkit-tap-highlight-color: transparent;
  }

  .mob-nav-btn svg {
    width: 20px;
    height: 20px;
    stroke: currentColor;
  }

  .mob-nav-btn.active {
    color: var(--accent-purple);
  }

  .mob-nav-btn.active svg {
    filter: drop-shadow(0 0 6px var(--accent-purple));
  }

  .mode-tooltip {
    display: none;
  }
}

/* Desktop: hide mobile-only elements */
@media (min-width: 769px) {
  .show-mobile { display: none !important; }
  .mobile-hidden { display: flex !important; }
}

</style>
