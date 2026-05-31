<template>
  <div class="radar-panel">
    <!-- Header -->
    <div class="panel-header glass">
      <div class="header-title">
        <h3>Market Radar</h3>
        <span class="pulse-dot" :class="{ 'is-loading': loading }"></span>
      </div>
      <div class="header-actions">
        <button class="sort-btn" @click="toggleSort" :title="'Sort ' + sortOrder.toUpperCase()">
          <span class="sort-icon">{{ sortOrder === 'desc' ? '⬇️' : '⬆️' }}</span>
        </button>
        <div class="tf-selector">
          <button v-for="t in ['1m','5m','15m','1h','24h']" :key="t" 
                  class="tf-btn" :class="{ active: selectedTf === t }"
                  @click="selectedTf = t">{{ t }}</button>
        </div>
      </div>
    </div>


    <!-- BTC Condition Bar -->
        <!-- BTC Condition Card -->
    <div class="btc-condition-card" :class="btcBarClass" >
      <div class="btc-card-header">
        <div class="btc-title">
          <span class="btc-icon">₿</span>
          <span class="btc-label">BTC/{{ (stats.btc_price && stats.btc_price < 500000) ? 'USDT' : 'IDR' }}</span>
        </div>
        <span class="btc-status-badge" :class="btcStatusBadgeClass">{{ btcStatusLabel }}</span>
      </div>
      
      <div class="btc-card-body">
        <div class="btc-price-wrapper">
          <span class="btc-price">{{ formatPrice(stats.btc_price) }}</span>
          <span class="btc-delta-main" :class="stats.btc_change_24h >= 0 ? 'delta-up' : 'delta-dn'">
            {{ stats.btc_change_24h >= 0 ? '+' : '' }}{{ (stats.btc_change_24h || 0).toFixed(2) }}%
          </span>
        </div>
        
        <div class="btc-metrics">
          <div class="metric-item">
            <span class="metric-label">5m</span>
            <span class="metric-val" :class="stats.btc_ch_5m >= 0 ? 'text-up' : 'text-dn'">
              {{ stats.btc_ch_5m >= 0 ? '+' : '' }}{{ (stats.btc_ch_5m || 0).toFixed(2) }}%
            </span>
          </div>
          <div class="metric-item">
            <span class="metric-label">1h</span>
            <span class="metric-val" :class="stats.btc_ch_1h >= 0 ? 'text-up' : 'text-dn'">
              {{ stats.btc_ch_1h >= 0 ? '+' : '' }}{{ (stats.btc_ch_1h || 0).toFixed(2) }}%
            </span>
          </div>
          <div class="metric-item">
            <span class="metric-label">Trend</span>
            <span class="btc-trend-chip" :class="btcTrendChipClass">{{ stats.btc_trend }}</span>
          </div>
          <div class="metric-item">
            <span class="metric-label">Score</span>
            <span class="btc-score-val">{{ (stats.btc_score || 0).toFixed(1) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Segmented Control -->
    <div class="radar-nav-wrapper">
      <div class="modern-segmented-control">
        <button :class="{ active: radarTab === 'momentum' }" @click="radarTab = 'momentum'">Momentum</button>
        <button :class="{ active: radarTab === 'gainer' }" @click="radarTab = 'gainer'">Gainers</button>
        <button :class="{ active: radarTab === 'whales' }" @click="radarTab = 'whales'">Whales 🐋</button>
        <div class="active-slider" :class="radarTab"></div>
      </div>
    </div>

    <!-- Content -->
    <div class="panel-content custom-scroll">
      <div v-if="loading && pairs.length === 0" class="state-container">
        <div class="loader"></div>
        <p>Scanning markets...</p>
      </div>
      <div v-else-if="pairs.length === 0" class="state-container empty">
        <div class="empty-icon">🏜️</div>
        <p>No active signals</p>
      </div>
      <div v-else class="radar-list">
        <TransitionGroup name="list">
          <div v-for="pair in paginatedPairs" :key="pair.pair" class="radar-card" @click="handleItemClick(pair)" @contextmenu.prevent="handleItemRightClick(pair)">
            
            <!-- Momentum Tab Layout -->
            <template v-if="radarTab === 'momentum'">
              <div class="card-top">
                <div class="pair-info">
                  <span class="pair-name">{{ formatRadarPair(pair.pair) }}</span>
                  <span class="pair-signal" :class="pair.trend?.toLowerCase()">{{ pair.trend || 'WAIT' }}</span>
                </div>
                <div class="price-info text-right">
                  <span class="price mono">{{ formatPrice(pair.price) }}</span>
                </div>
              </div>
              <div class="card-bottom">
                <div class="metrics">
                  <span class="vola-pill" :class="volClass(pair.volatility)">
                    <span class="dot"></span> Score: {{ Number(pair.score || 0).toFixed(1) }}
                  </span>
                  <span class="vol-text" style="margin-left: 8px;">Vola: {{ Number(pair.volatility).toFixed(1) }}%</span>
                </div>
                <div class="change-info text-right">
                  <span class="change-text mono" :class="getTfValue(pair, selectedTf) >= 0 ? 'text-green' : 'text-red'">
                    {{ getTfValue(pair, selectedTf) > 0 ? '+' : ''}}{{ getTfValue(pair, selectedTf).toFixed(2) }}%
                  </span>
                </div>
              </div>
            </template>

            <!-- Gainers Tab Layout -->
            <template v-else-if="radarTab === 'gainer'">
              <div class="card-top">
                <div class="pair-info align-center">
                  <span class="pair-name">{{ formatRadarPair(pair.pair) }}</span>
                </div>
                <div class="price-info text-right">
                  <span class="price mono" style="color: var(--text-primary); font-weight: 700;">{{ formatPrice(pair.price) }}</span>
                </div>
              </div>
              <div class="card-bottom gainer-bottom mt-1">
                <div class="metrics">
                  <span class="vol-text">Vol: {{ formatVolume(pair.volume) }}</span>
                </div>
                <div class="change-info text-right">
                  <span class="change-badge mono" :class="getTfValue(pair, selectedTf) >= 0 ? 'bg-green' : 'bg-red'">
                    {{ getTfValue(pair, selectedTf) > 0 ? '+' : ''}}{{ getTfValue(pair, selectedTf).toFixed(2) }}%
                  </span>
                </div>
              </div>
            </template>

            <!-- Whales Tab Layout -->
            <template v-else-if="radarTab === 'whales'">
              <div class="card-top">
                <div class="pair-info align-center">
                  <span class="pair-name">{{ formatRadarPair(pair.pair) }}</span>
                  <span class="whale-badge">ACTION</span>
                </div>
                <div class="price-info text-right">
                  <span class="price mono" style="color: var(--text-primary); font-weight: 700;">{{ formatPrice(pair.price) }}</span>
                </div>
              </div>
              <div class="card-bottom mt-1">
                <div class="metrics">
                  <span class="vol-text text-blue" style="font-weight: 700; color: #58a6ff;">Vol: {{ formatVolume(pair.volume) }}</span>
                </div>
                <div class="change-info text-right">
                  <span class="vola-pill high" style="background: rgba(14, 203, 129, 0.15); border-color: rgba(14, 203, 129, 0.3);">
                    <span class="dot" style="background: #0ecb81; box-shadow: 0 0 6px #0ecb81;"></span> Vola: {{ Number(pair.volatility).toFixed(1) }}%
                  </span>
                </div>
              </div>
            </template>

          </div>
        </TransitionGroup>
      </div>
      
      <!-- Pagination -->
      <div v-if="pairs.length > pageSize" class="modern-pagination">
        <button class="page-btn" :disabled="currentPage === 1" @click="currentPage--">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"></polyline></svg>
        </button>
        <span class="page-info">{{ currentPage }} / {{ totalPages }}</span>
        <button class="page-btn" :disabled="currentPage === totalPages" @click="currentPage++">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useAuth } from '../composables/useAuth'
import { useTradeStore } from '../stores/trade'

const props = defineProps({
  isLive: { type: Boolean, required: true }
})

const { getAuthHeaders } = useAuth()
const store = useTradeStore()
const backendUrl = import.meta.env.VITE_BACKEND_URL || ''
const pairs = ref([])
const loading = ref(true)
let intervalId = null

const selectedTf = ref('1m')
const radarTab = ref('momentum')
const sortOrder = ref('desc')

function toggleSort() {
  sortOrder.value = sortOrder.value === 'desc' ? 'asc' : 'desc'
}

const currentPage = ref(1)
const pageSize = 15

// Reset page when tab or timeframe changes
watch([radarTab, selectedTf], () => {
  currentPage.value = 1
})


const stats = computed(() => store.stats)

const btcStatusLabel = computed(() => {
  if (store.stats.btc_crash) return '🔴 CRASH'
  if (store.stats.btc_weakening) return '🟡 WEAK'
  return '🟢 STRONG'
})

const btcBarClass = computed(() => {
  if (store.stats.btc_crash) return 'btc-bar-crash'
  if (store.stats.btc_weakening) return 'btc-bar-weak'
  return 'btc-bar-strong'
})

const btcStatusBadgeClass = computed(() => {
  if (store.stats.btc_crash) return 'badge-crash'
  if (store.stats.btc_weakening) return 'badge-weak'
  return 'badge-strong'
})

const btcTrendChipClass = computed(() => {
  const t = (store.stats.btc_trend || '').toUpperCase()
  if (t === 'UP' || t === 'BULLISH') return 'chip-up'
  if (t === 'DOWN' || t === 'BEARISH') return 'chip-dn'
  return 'chip-neutral'
})

function formatRadarPair(pairStr) {
  if (!pairStr) return ''
  return pairStr.replace(/(idr|usdt)$/i, '/$1').toUpperCase()
}

function getTfValue(pair, tf) {
  if (tf === '1m') return Number(pair.change || 0)
  if (tf === '5m') return Number(pair.ch_5m || 0)
  if (tf === '15m') return Number(pair.ch_15m || 0)
  if (tf === '1h') return Number(pair.ch_1h || 0)
  if (tf === '24h') return Number(pair.ch_24h || 0)
  return 0
}

const sortedPairs = computed(() => {
  const arr = radarTab.value === 'momentum' ? pairs.value.filter(p => Number(p.score || 0) > 0) : [...pairs.value]
  const modifier = sortOrder.value === 'desc' ? 1 : -1
  if (radarTab.value === 'momentum') {
    return arr.sort((a, b) => (Number(b.score || 0) - Number(a.score || 0)) * modifier)
  } else if (radarTab.value === 'gainer') {
    return arr.sort((a, b) => (getTfValue(b, selectedTf.value) - getTfValue(a, selectedTf.value)) * modifier)
  } else if (radarTab.value === 'whales') {
    return arr.sort((a, b) => {
      const getWhaleScore = (p) => Number(p.volatility || 0) * Math.log10(Math.max(Number(p.volume || 1), 1))
      return (getWhaleScore(b) - getWhaleScore(a)) * modifier
    })
  }
  return arr
})

const totalPages = computed(() => Math.ceil(sortedPairs.value.length / pageSize) || 1)
const paginatedPairs = computed(() => {
  const start = (currentPage.value - 1) * pageSize
  return sortedPairs.value.slice(start, start + pageSize)
})

async function fetchRadar() {
  loading.value = true
  try {
    const res = await fetch(`${backendUrl}/api/radar`, { headers: getAuthHeaders() })
    if (res.ok) {
      const data = await res.json()
      pairs.value = Array.isArray(data) ? data : []
    }
  } catch (err) {
    console.error('Failed to fetch radar:', err)
  } finally {
    loading.value = false
  }
}

function volClass(vol) {
  const num = Number(vol)
  if (num >= 3.5) return 'high'
  if (num >= 1.5) return 'med'
  return 'low'
}

function formatVolume(vol) {
  if (!vol) return '0'
  const v = Number(vol)
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(2) + 'K'
  return v.toFixed(0)
}

function formatPrice(p) {
  if (!p) return '-'
  const num = Number(p)
  if (num < 0.0001) return num.toFixed(8)
  if (num < 1) return num.toFixed(4)
  if (num < 100) return num.toFixed(2)
  return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function handleItemClick(pair) {
  window.dispatchEvent(new CustomEvent('open-radar-chart', { detail: { ...pair, ui_tf: selectedTf.value } }))
}

function handleItemRightClick(pair) {
  window.dispatchEvent(new CustomEvent('open-ai-modal', { detail: { ...pair, ui_tf: selectedTf.value } }))
}

onMounted(() => {
  fetchRadar()
  intervalId = setInterval(fetchRadar, 10000)
})

onUnmounted(() => {
  if (intervalId) clearInterval(intervalId)
})
</script>

<style scoped>
.radar-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-card);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0,0,0,0.2);
}

.mono {
  font-family: 'JetBrains Mono', 'Courier New', Courier, monospace;
}

/* Glass Header */
.panel-header {
  padding: 16px 20px;
  background: rgba(20, 20, 25, 0.6);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(255,255,255,0.05);
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: relative;
  z-index: 10;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 12px;
}

.panel-header h3 {
  margin: 0;
  font-size: 15px;
  color: #fff;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: rgba(255,255,255,0.2);
}
.pulse-dot.is-loading {
  background: #a371f7;
  box-shadow: 0 0 10px #a371f7;
  animation: pulse 1s infinite;
}

@keyframes pulse {
  0% { opacity: 0.4; transform: scale(0.9); }
  50% { opacity: 1; transform: scale(1.1); }
  100% { opacity: 0.4; transform: scale(0.9); }
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}


/* BTC Condition Card (Premium UI) */
.btc-condition-card {
  margin: 12px 20px;
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.04);
  background: linear-gradient(145deg, rgba(20, 20, 25, 0.8), rgba(10, 10, 15, 0.9));
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3), inset 0 1px 1px rgba(255,255,255,0.03);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

/* Subtle glow at the top edge */
.btc-condition-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.1), transparent);
}

.btc-bar-strong {
  border-color: rgba(63, 185, 80, 0.2);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3), 0 0 15px rgba(63, 185, 80, 0.05);
}
.btc-bar-weak {
  border-color: rgba(210, 153, 34, 0.2);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3), 0 0 15px rgba(210, 153, 34, 0.05);
}
.btc-bar-crash {
  border-color: rgba(248, 81, 73, 0.3);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3), 0 0 15px rgba(248, 81, 73, 0.1);
  animation: btc-crash-pulse 2s ease-in-out infinite alternate;
}
@keyframes btc-crash-pulse {
  from { border-color: rgba(248, 81, 73, 0.2); box-shadow: 0 4px 12px rgba(0,0,0,0.3), 0 0 5px rgba(248,81,73,0.05); }
  to   { border-color: rgba(248, 81, 73, 0.6); box-shadow: 0 4px 12px rgba(0,0,0,0.3), 0 0 20px rgba(248,81,73,0.15); }
}

.btc-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.btc-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.btc-icon {
  font-size: 16px;
  color: #f0a500;
  text-shadow: 0 0 8px rgba(240, 165, 0, 0.4);
}
.btc-label {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.btc-status-badge {
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.8px;
  text-transform: uppercase;
  box-shadow: inset 0 1px 1px rgba(255,255,255,0.1);
}
.badge-strong { background: rgba(63, 185, 80, 0.1);  color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.25); }
.badge-weak   { background: rgba(210, 153, 34, 0.1); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.25); }
.badge-crash  { background: rgba(248, 81, 73, 0.1);  color: #f85149; border: 1px solid rgba(248, 81, 73, 0.25); }

.btc-card-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.btc-price-wrapper {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.btc-price {
  color: var(--text-primary);
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -0.5px;
  font-family: 'Inter', -apple-system, sans-serif;
}
.btc-delta-main {
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
}
.delta-up { color: #3fb950; background: rgba(63, 185, 80, 0.1); }
.delta-dn { color: #f85149; background: rgba(248, 81, 73, 0.1); }
.text-up { color: #3fb950; }
.text-dn { color: #f85149; }

.btc-metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  background: rgba(0, 0, 0, 0.2);
  padding: 10px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.02);
}
.metric-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.metric-label {
  font-size: 9px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-weight: 600;
}
.metric-val {
  font-size: 11px;
  font-weight: 700;
}
.btc-trend-chip {
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 9px;
  font-weight: 800;
  text-transform: uppercase;
}
.chip-up      { color: #3fb950; background: rgba(63, 185, 80, 0.1); }
.chip-dn      { color: #f85149; background: rgba(248, 81, 73, 0.1); }
.chip-neutral { color: var(--text-secondary); background: rgba(139, 148, 158, 0.1); }
.btc-score-val {
  font-size: 11px;
  color: #58a6ff;
  font-weight: 800;
}

.radar-nav-wrapper {
  padding: 12px 20px;
  background: rgba(20, 20, 25, 0.6);
  border-bottom: 1px solid rgba(255,255,255,0.03);
}

/* Modern Segmented Control with Sliding Background */
.modern-segmented-control {
  display: flex;
  position: relative;
  background: rgba(0, 0, 0, 0.3);
  border-radius: 8px;
  padding: 4px;
  border: 1px solid rgba(255,255,255,0.05);
}

.modern-segmented-control button {
  flex: 1;
  background: transparent;
  border: none;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  padding: 8px 0;
  border-radius: 6px;
  cursor: pointer;
  z-index: 2;
  transition: color 0.3s ease;
}

.modern-segmented-control button.active {
  color: #fff;
}

.active-slider {
  position: absolute;
  top: 4px;
  bottom: 4px;
  width: calc(33.333% - 2.6px);
  background: rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  z-index: 1;
  transition: transform 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}

.active-slider.momentum { transform: translateX(0%); }
.active-slider.gainer { transform: translateX(100%); }
.active-slider.whales { transform: translateX(200%); }


/* Content Area */
.panel-content {
  flex: 1;
  padding: 12px 16px;
  position: relative;
}

.state-container {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  height: 100%;
  gap: 16px;
  color: var(--text-tertiary);
}

.loader {
  width: 24px;
  height: 24px;
  border: 3px solid rgba(163, 113, 247, 0.2);
  border-top-color: #a371f7;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}
@keyframes spin { 100% { transform: rotate(360deg); } }

.empty-icon { font-size: 32px; opacity: 0.5; }


/* Radar List Cards */
.radar-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.radar-card {
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.03);
  border-radius: 10px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

.radar-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  background: linear-gradient(180deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0) 100%);
  opacity: 0;
  transition: opacity 0.25s ease;
}

.radar-card:hover {
  transform: translateY(-2px);
  background: rgba(255, 255, 255, 0.04);
  border-color: rgba(255, 255, 255, 0.1);
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
}
.radar-card:hover::before { opacity: 1; }

.card-top, .card-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: relative;
  z-index: 1;
}

.pair-info {
  display: flex;
  align-items: center;
  gap: 10px;
}
.pair-name {
  font-family: 'Inter', sans-serif;
  font-weight: 700;
  font-size: 14px;
  color: #fff;
  letter-spacing: 0.2px;
}

.whale-badge {
  background: rgba(88, 166, 255, 0.15);
  color: #58a6ff;
  border: 1px solid rgba(88, 166, 255, 0.3);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 9px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.pair-signal {
  font-size: 10px;
  font-weight: 800;
  padding: 3px 6px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.pair-signal.buy { background: rgba(14, 203, 129, 0.15); color: #0ecb81; border: 1px solid rgba(14, 203, 129, 0.2); }
.pair-signal.sell { background: rgba(246, 70, 93, 0.15); color: #f6465d; border: 1px solid rgba(246, 70, 93, 0.2); }
.pair-signal.wait { background: rgba(255, 255, 255, 0.05); color: var(--text-secondary); }

.price-info .price {
  font-size: 14px;
  color: var(--text-secondary);
}

.vola-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 20px;
  background: rgba(0,0,0,0.3);
  border: 1px solid rgba(255,255,255,0.05);
}
.vola-pill .dot {
  width: 6px; height: 6px;
  border-radius: 50%;
}
.vola-pill.high .dot { background: #0ecb81; box-shadow: 0 0 6px #0ecb81; }
.vola-pill.med .dot { background: #f5a623; }
.vola-pill.low .dot { background: var(--text-tertiary); }

.vola-pill.high { color: #0ecb81; }
.vola-pill.med { color: #f5a623; }
.vola-pill.low { color: var(--text-secondary); }

.change-text {
  font-size: 13px;
  font-weight: 600;
}

.text-green { color: #0ecb81; }
.text-red { color: #f6465d; }

.vol-text {
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 500;
}

.change-badge {
  padding: 4px 8px;
  border-radius: 6px;
  font-weight: 700;
  font-size: 12px;
  color: #fff;
  display: inline-block;
  min-width: 60px;
  text-align: center;
}
.bg-green { background: #0ecb81; }
.bg-red { background: #f6465d; }


/* Pagination */
.modern-pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 20px;
  padding: 16px 0 8px 0;
  margin-top: 8px;
}
.page-btn {
  background: rgba(255, 255, 255, 0.05);
  color: #fff;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 50%;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
}
.page-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.1);
  transform: scale(1.05);
}
.page-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.page-info {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 1px;
}

/* Transitions */
.list-enter-active,
.list-leave-active {
  transition: all 0.4s ease;
}
.list-enter-from {
  opacity: 0;
  transform: translateX(-20px);
}
.list-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
</style>
