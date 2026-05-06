<template>
  <div class="app">
    <Header :stats="store.stats" :connected="store.connected" />
    
    <NotificationCenter />
    <RiskGuardPopup :socket="store.socket" />
    <ApprovalGateModal />
    <StrategicBreakModal />

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
          <span class="stat-item">LATENCY: <span class="v">{{ store.latency }}ms</span></span>
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

const newsHeadlines = ref([])
const startTime = ref(Date.now())
const now = ref(Date.now())

async function fetchNews() {
  try {
    const resp = await fetch('/api/news')
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
  setInterval(fetchNews, 5 * 60 * 1000)
  setInterval(() => { now.value = Date.now() }, 1000)
})

// Global FORCE_LOGOUT listener — redirect to login when session is invalidated
watch(() => store.forceLogout, (triggered) => {
  if (triggered) {
    router.push('/login')
  }
})

onUnmounted(() => {
  // cleanup
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
  overflow: hidden;
}

.app {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.main-grid {
  flex: 1;
  display: flex;
  overflow: hidden;
  padding: 16px;
  gap: 0;
}

.left-panel {
  width: var(--left-width);
  min-width: 300px;
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
}

.footer-left { width: 220px; }
.mode-badge {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1px;
  color: var(--accent-purple);
  display: flex;
  align-items: center;
  gap: 8px;
}

.footer-center {
  flex: 1;
  overflow: hidden;
}

.ticker-tape {
  width: 100%;
  white-space: nowrap;
}

.ticker-item {
  display: inline-block;
  margin-right: 40px;
  font-size: 12px;
  font-family: 'JetBrains Mono', monospace;
}

.t-pair { color: #888; margin-right: 8px; }
.t-price { color: #fff; font-weight: 700; }
.t-change.up { color: var(--accent-green); }
.t-change.down { color: var(--accent-red); }

.system-stats {
  display: flex;
  gap: 20px;
}

.stat-item {
  font-size: 10px;
  font-weight: 700;
  color: #444;
  letter-spacing: 1px;
}

.stat-item .v { color: #888; }

@media (max-width: 768px) {
  .hide-mobile { display: none; }
  .main-grid { flex-direction: column; }
  .left-panel { width: 100% !important; height: 50%; }
  .resizer { height: 16px; width: 100%; cursor: row-resize; }
  .resizer::after { width: 40px; height: 2px; }
}
</style>
