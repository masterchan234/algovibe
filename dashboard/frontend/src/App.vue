<template>
  <div class="dashboard-layout" :class="[mobileTabClass, modeClass]">
    <TraderHeader :is-live="isLiveMode" :stats="store.stats" :connected="store.connected" @nav-mobile="mobileTab = $event" />

    <main class="dashboard-grid">
      <!-- LEFT PANEL: Market Context & Radar -->
      <section class="left-panel trader-panel" :class="{ 'mobile-hidden': mobileTab !== 'radar' }">
        <SignalRadarPanel :is-live="isLiveMode" />
      </section>

      <!-- CENTER PANEL: Chart & Intelligence -->
      <section class="center-panel" :class="{ 'mobile-hidden': mobileTab !== 'chart' }">
        <div class="main-chart-wrap trader-panel">
          <MainChartPanel :is-live="isLiveMode" />
        </div>
      </section>

      <!-- RIGHT PANEL: Execution & Positions -->
      <section class="right-panel trader-panel" :class="{ 'mobile-hidden': mobileTab !== 'execution' }">
        <ExecutionPanel :is-live="isLiveMode" />
      </section>
    </main>

    <!-- Global App Footer -->
    <AppFooter :is-live="isLiveMode" />

    <!-- Mobile Bottom Navigation (PWA) -->
    <nav class="mobile-bottom-nav">
      <button :class="{ active: mobileTab === 'radar' }" @click="mobileTab = 'radar'">
        <span>🎯 Radar</span>
      </button>
      <button :class="{ active: mobileTab === 'chart' }" @click="mobileTab = 'chart'">
        <span>📈 Chart</span>
      </button>
      <button :class="{ active: mobileTab === 'execution' }" @click="mobileTab = 'execution'">
        <span>⚡ Execution</span>
      </button>
    </nav>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuth } from './composables/useAuth'
import { useTradeStore } from './stores/trade'

import TraderHeader from './components/TraderHeader.vue'
import SignalRadarPanel from './components/SignalRadarPanel.vue'
import MainChartPanel from './components/MainChartPanel.vue'
import ExecutionPanel from './components/ExecutionPanel.vue'
import AppFooter from './components/AppFooter.vue'

const route = useRoute()
const store = useTradeStore()
const { getAuthHeaders } = useAuth()
const backendUrl = import.meta.env.VITE_BACKEND_URL || ''

const isLiveMode = computed(() => route.path === '/dashboard-live')
const modeClass = computed(() => isLiveMode.value ? 'mode-live' : 'mode-dry')

const mobileTab = ref('chart') // radar, chart, execution
const mobileTabClass = computed(() => `tab-${mobileTab.value}`)

let snapshotInterval = null

async function fetchSnapshot() {
  const modePath = isLiveMode.value ? 'live' : 'dry'
  try {
    const [statsRes, positionsRes] = await Promise.all([
      fetch(`${backendUrl}/api/${modePath}/stats`, { headers: getAuthHeaders() }),
      fetch(`${backendUrl}/api/${modePath}/positions`, { headers: getAuthHeaders() }),
    ])

    if (statsRes.ok) {
      const data = await statsRes.json()
      Object.assign(store.stats, data)
    }
    
    if (positionsRes.ok) {
      const data = await positionsRes.json()
      store.positions = data
    }
  } catch (err) {
    console.error('Failed to fetch snapshot:', err)
  }
}

onMounted(() => {
  store.connect()
  fetchSnapshot()
  // Interval removed: WebSocket acts as the single source of truth for stats and positions
})

onUnmounted(() => {
  if (snapshotInterval) clearInterval(snapshotInterval)
})
</script>

<style scoped>
.dashboard-layout {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg-trader-main);
  position: relative;
}

.dashboard-grid {
  flex: 1;
  display: grid;
  grid-template-columns: 380px 1fr 520px;
  gap: 12px;
  padding: 12px;
  
  position: relative;
  z-index: 10;
}

.center-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  min-height: 0;
}

.main-chart-wrap {
  flex: 1; /* 100% of center panel height */
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* Base Panel Styles */
.left-panel, .right-panel {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 70px);
}

/* Mobile Bottom Navigation */
.mobile-bottom-nav {
  display: none;
}

/* --- MOBILE RESPONSIVE (PWA) --- */
@media (max-width: 992px) {
  .dashboard-grid {
    display: flex;
    flex-direction: column;
    padding: 12px;
    padding-bottom: 70px; /* Space for bottom nav */
  }

  .mobile-hidden {
    display: none !important;
  }

  .left-panel, .center-panel, .right-panel {
    flex: 1;
    height: 100%;
  }

  .mobile-bottom-nav {
    display: flex;
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    height: 60px;
    background: var(--bg-surface);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-top: 1px solid var(--border-focus);
    z-index: 1000;
  }

  .mobile-bottom-nav button {
    flex: 1;
    background: transparent;
    border: none;
    color: var(--text-secondary);
    font-size: 12px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
  }

  .mobile-bottom-nav button.active {
    color: var(--accent-purple);
  }
}
</style>
