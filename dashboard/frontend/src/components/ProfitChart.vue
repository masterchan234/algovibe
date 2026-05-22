<template>
  <div class="chart-panel">
    <!-- KPI Bar -->
    <div class="kpi-bar">
      <div class="kpi-item">
        <div class="kpi-label">Net PnL (All-Time)</div>
        <div class="kpi-value" :class="stats.net_pnl >= 0 ? 'positive' : 'negative'">
          {{ formatIdr(stats.net_pnl) }}
        </div>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Realized</div>
        <div class="kpi-value" :class="stats.daily_pnl >= 0 ? 'positive' : 'negative'">
          {{ formatIdr(stats.daily_pnl) }}
        </div>
      </div>
      <div class="kpi-item momentum-kpi" :class="`momentum-${momentumStateClass}`" :title="`Velocity: ${stats.net_pnl_velocity >= 0 ? '+' : ''}${(stats.net_pnl_velocity || 0).toFixed(1)} ${velocitySuffix}`">
        <div class="kpi-label">Momentum</div>
        <div class="kpi-value momentum-value" :class="`momentum-${momentumStateClass}`">
          <span class="momentum-arrow">{{ momentumIcon }}</span>
          <span class="momentum-label">{{ stats.momentum_state || 'STALLING' }}</span>
        </div>
        <div class="momentum-velocity">{{ stats.net_pnl_velocity >= 0 ? '+' : '' }}{{ (stats.net_pnl_velocity || 0).toFixed(1) }} {{ velocitySuffix }}</div>
        <button
          v-if="stats.consecutive_losses >= 3"
          class="unblock-btn"
          :disabled="isUnblocking"
          @click="handleUnblock"
          :title="`Bot paused due to ${stats.consecutive_losses} consecutive losses. Click to reset.`"
        >{{ isUnblocking ? '...' : `⛔ UNBLOCK (${stats.consecutive_losses}×)` }}</button>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Unrealized</div>
        <div class="kpi-value" :class="stats.unrealized_pnl >= 0 ? 'positive' : 'negative'">
          {{ formatIdr(stats.unrealized_pnl) }}
        </div>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Win Rate</div>
        <div class="kpi-value">{{ (stats.win_rate || 0).toFixed(1) }}%</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Posisi</div>
        <div class="kpi-value">{{ stats.active_positions }}</div>
      </div>
      <div class="kpi-item control-item">
        <div class="kpi-label">Harvest Mode</div>
        <button 
          class="kpi-toggle-btn" 
          :class="{ active: stats.session_mode === 'HARVEST_ONLY' }"
          @click="toggleHarvestMode"
        >
          {{ stats.session_mode === 'HARVEST_ONLY' ? 'ON' : 'OFF' }}
        </button>
      </div>
      <div class="kpi-item control-item">
        <div class="kpi-label">Buy Gates</div>
        <button 
          class="kpi-toggle-btn" 
          :class="{ active: stats.session_buy_enabled }"
          @click="toggleBuyGates"
        >
          {{ stats.session_buy_enabled ? 'ON' : 'OFF' }}
        </button>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Sentiment</div>
        <div class="sentiment-bar">
          <div class="sentiment-fill" :style="{ width: stats.market_sentiment_index + '%' }" :class="sentimentClass"></div>
          <span class="sentiment-num">{{ stats.market_sentiment_index }}</span>
        </div>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Loose Gates</div>
        <button
          class="loose-gates-btn"
          :class="{ active: stats.dry_run_loose_gates }"
          :disabled="isTogglingGates"
          @click="handleToggleLooseGates"
          :title="stats.dry_run_loose_gates ? 'LOOSE GATES active — click to deactivate' : 'Activate LOOSE GATES (bypass strict filters)'"
        >{{ isTogglingGates ? '...' : (stats.dry_run_loose_gates ? '🔓 ON' : '🔒 OFF') }}</button>
      </div>
    </div>

    <!-- UNIFIED EQUITY PERFORMANCE ORACLE -->
    <div class="intelligence-panel">
      <div class="intel-header">
        <div class="intel-title">
          <span class="intel-icon">📊</span>
          <span>EQUITY PERFORMANCE</span>
        </div>
        <div class="intel-meta">
          <div class="tf-selector">
            <button :class="{ active: timeframe === 'live' }" @click="setTimeframe('live')">LIVE</button>
            <button :class="{ active: timeframe === '1h' }" @click="setTimeframe('1h')">1H</button>
            <button :class="{ active: timeframe === '24h' }" @click="setTimeframe('24h')">24H</button>
          </div>
          <div class="intel-chip" :class="stats.net_pnl >= 0 ? 'bullish' : 'bearish'">
            {{ stats.net_pnl >= 0 ? 'NET GAIN' : 'NET RECOVERY' }}
          </div>
        </div>
      </div>

      <div class="intel-body">
        <!-- Visual Equity Curve -->
        <div class="chart-wrap-integrated">
          <div v-show="timeframe === 'live' && store.pnlHistory.length === 0" class="chart-empty-small">
            <div class="loading-dots"><span></span><span></span><span></span></div>
            <span>Waiting for data…</span>
          </div>
          <div ref="chartContainer" class="chart-canvas-small" :style="{ visibility: (timeframe !== 'live' || store.pnlHistory.length > 0) ? 'visible' : 'hidden' }"></div>
        </div>


        <!-- Pulse Metrics Grid -->
        <div class="intel-pulse-grid" v-if="stats.alpha_contributor || stats.best_trade">
          <div class="pulse-card">
            <span class="p-label">THE ALPHA</span>
            <span class="p-value highlight">{{ formatPair(stats.alpha_contributor) || 'SCANNING...' }}</span>
          </div>
          <div class="pulse-card">
            <span class="p-label">BEST TRADE</span>
            <span class="p-value">{{ formatIdr(stats.best_trade) }}</span>
          </div>
          <div class="pulse-card">
            <span class="p-label">INTENSITY</span>
            <span class="p-value">{{ stats.intensity }} <small>TX/HR</small></span>
          </div>
        </div>

        <!-- Equity Attribution Heatmap (Replacing Table) -->
        <div class="asset-heatmap" v-if="stats.asset_contributions && stats.asset_contributions.length > 0">
          <div class="heatmap-header">EQUITY ATTRIBUTION HEATMAP</div>
          <div class="heatmap-grid">
            <div 
              v-for="asset in stats.asset_contributions" 
              :key="asset.pair" 
              class="heatmap-tile"
              :style="tileStyle(asset.pnl)"
              :title="`${asset.trades} Trades | ${formatIdr(asset.pnl)}`"
              @click="onRadarClick(asset)"
            >
              <span class="tile-label">{{ formatPair(asset.pair) }}</span>
              <span class="tile-pnl">{{ formatIdr(asset.pnl) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Top Radar -->
    <div class="radar-section">
      <div class="section-label radar-header-row">
        <span>Signal Radar</span>
        <span class="radar-total">{{ radarDisplayPairs.held.length + radarDisplayPairs.others.length }}/{{ sortedRadarPairs.length }} Coins</span>
      </div>

      <!-- BTC Condition Bar -->
      <div class="btc-condition-bar" :class="btcBarClass">
        <div class="btc-bar-left">
          <span class="btc-icon">₿</span>
          <span class="btc-label">BTC/{{ (stats.btc_price && stats.btc_price < 500000) ? 'USDT' : 'IDR' }}</span>
          <span class="btc-price">{{ formatPrice(stats.btc_price) }}</span>
        </div>
        <div class="btc-bar-mid">
          <span class="btc-delta" :class="stats.btc_ch_5m >= 0 ? 'delta-up' : 'delta-dn'">
            5m {{ stats.btc_ch_5m >= 0 ? '+' : '' }}{{ stats.btc_ch_5m.toFixed(2) }}%
          </span>
          <span class="btc-delta" :class="stats.btc_ch_1h >= 0 ? 'delta-up' : 'delta-dn'">
            1h {{ stats.btc_ch_1h >= 0 ? '+' : '' }}{{ stats.btc_ch_1h.toFixed(2) }}%
          </span>
          <span class="btc-delta" :class="stats.btc_change_24h >= 0 ? 'delta-up' : 'delta-dn'">
            24h {{ stats.btc_change_24h >= 0 ? '+' : '' }}{{ stats.btc_change_24h.toFixed(2) }}%
          </span>
          <span class="btc-trend-chip" :class="btcTrendChipClass">{{ stats.btc_trend }}</span>
        </div>
        <div class="btc-bar-right">
          <span class="btc-status-badge" :class="btcStatusBadgeClass">{{ btcStatusLabel }}</span>
          <span class="btc-score-val">S{{ stats.btc_score.toFixed(0) }}</span>
        </div>
      </div>

      <div class="radar-toolbar">
        <div class="radar-filters">
          <button class="radar-filter-btn" :class="{ active: radarFilter === 'all' }" @click="radarFilter = 'all'">All {{ radarCounts.all }}</button>
          <button class="radar-filter-btn" :class="{ active: radarFilter === 'tradable' }" @click="radarFilter = 'tradable'">Tradable {{ radarCounts.tradable }}</button>
          <button class="radar-filter-btn" :class="{ active: radarFilter === 'blocked' }" @click="radarFilter = 'blocked'">Blocked {{ radarCounts.blocked }}</button>
          <button class="radar-filter-btn" :class="{ active: radarFilter === 'veto' }" @click="radarFilter = 'veto'">AI Veto {{ radarCounts.veto }}</button>
        </div>
        <div class="radar-search-wrap">
          <input
            v-model="radarSearch"
            class="radar-search"
            type="text"
            placeholder="🔍 Search coin…"
            spellcheck="false"
          />
        </div>
        <div class="radar-audit-mini" title="24-hour history, not current live status">
          <span class="audit-label">Audit 24h</span>
          <span>BUY {{ radarAudit.buy_signals_24h || 0 }}</span>
          <span>Blocked {{ radarAudit.buy_blocked_total_24h || 0 }}</span>
          <span>Veto {{ radarAudit.buy_blocked_ai_veto_24h || 0 }}</span>
        </div>
      </div>
      <div class="radar-scroll-wrap">
        <!-- Held coins group -->
        <template v-if="radarDisplayPairs.held.length">
          <div class="radar-group-label">📦 Held ({{ radarDisplayPairs.held.length }})</div>
          <div class="radar-list">
            <div
              v-for="pair in radarDisplayPairs.held"
              :key="pair.pair"
              class="radar-item radar-held"
              :class="{ 'active-radar': chartModalPair === pair.pair }"
              :style="heatmapStyle(pair)"
              :title="formatPair(pair.pair) + ' [HELD] · click = chart, right-click = detail'"
              @click="onRadarClick(pair)"
              @contextmenu="onRadarRightClick($event, pair)"
            >
              <span class="ri-name">
                {{ formatPair(pair.pair) }}
                <span v-if="pair.pair.toUpperCase() === stats.regime_leader.toUpperCase()" class="crown-micro">👑</span>
                <span v-if="pair.ai_veto" class="ri-veto-dot">!</span>
              </span>
            </div>
          </div>
        </template>

        <!-- Other coins group -->
        <template v-if="radarDisplayPairs.others.length">
          <div v-if="radarDisplayPairs.held.length" class="radar-group-label">📡 Radar ({{ radarDisplayPairs.others.length }})</div>
          <div class="radar-list">
            <div
              v-for="pair in radarDisplayPairs.others"
              :key="pair.pair"
              class="radar-item"
              :class="{ 'active-radar': chartModalPair === pair.pair }"
              :style="heatmapStyle(pair)"
              :title="formatPair(pair.pair) + ' · ' + recommendationOf(pair) + ' · S' + (pair.score||0).toFixed(0) + (pair.ai_veto ? ' · AI VETO' : '') + (pair.tradable === false ? ' · BLOCKED' : '') + ' · click = chart, right-click = detail'"
              @click="onRadarClick(pair)"
              @contextmenu="onRadarRightClick($event, pair)"
            >
              <span class="ri-name">
                {{ formatPair(pair.pair) }}
                <span v-if="pair.pair.toUpperCase() === stats.regime_leader.toUpperCase()" class="crown-micro">👑</span>
                <span v-if="pair.ai_veto" class="ri-veto-dot">!</span>
              </span>
            </div>
          </div>
        </template>

        <div v-if="radarSearch && !radarDisplayPairs.held.length && !radarDisplayPairs.others.length" class="radar-empty">
          No coin matches "{{ radarSearch }}"
        </div>
      </div>

      <!-- Chart Modal -->
      <div v-if="showChartModal" class="chart-modal-overlay" @click.self="showChartModal = false">
        <div class="chart-modal">
          <div class="chart-modal-header">
            <span class="chart-modal-title">{{ (chartModalPair || '').replace(/idr$/i, '').replace(/usdt$/i, '').toUpperCase() }} / {{ (stats.btc_price && stats.btc_price < 500000) ? 'USDT' : 'IDR' }}</span>
            <button class="chart-modal-close" @click="showChartModal = false">✕</button>
          </div>
          <div class="chart-modal-body">
            <ChartPanel :forcePair="chartModalPair" />
          </div>
        </div>
      </div>
    </div>

    <RadarDetailModal
      v-if="showRadarDetail"
      :show="showRadarDetail"
      :pair="radarDetailPair"
      :regimeLeader="stats.regime_leader"
      :onClose="closeRadarDetail"
      :onDeepAnalysis="openAiFromDetail"
    />

    <AiAnalysisModal
      v-if="showAiModal"
      :show="showAiModal"
      :pair="selectedPair"
      :onClose="closeAiModal"
      :fetchInsight="fetchAiInsight"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useTradeStore } from '../stores/trade'
import AiAnalysisModal from './AiAnalysisModal.vue'
import RadarDetailModal from './RadarDetailModal.vue'
import ChartPanel from './ChartPanel.vue'

let echarts = null
let chartInitialized = false

// Lazy-load echarts on mount
async function initializeECharts() {
  if (chartInitialized) return
  try {
    const echartsCore = await import('echarts/core')
    const { LineChart } = await import('echarts/charts')
    const { GridComponent, TooltipComponent, TitleComponent, MarkLineComponent } = await import('echarts/components')
    const { CanvasRenderer } = await import('echarts/renderers')
    
    echarts = echartsCore
    echarts.use([LineChart, GridComponent, TooltipComponent, TitleComponent, MarkLineComponent, CanvasRenderer])
    chartInitialized = true
  } catch (err) {
    console.error('Failed to load ECharts:', err)
  }
}

import { useAuth } from '../composables/useAuth'

const store = useTradeStore()
const { getAuthHeaders } = useAuth()
const stats = computed(() => store.stats)

const radarFilter = ref('all')
const radarSearch = ref('')
const showChartModal = ref(false)
const chartModalPair = ref('')
const radarAudit = ref({
  buy_signals_24h: 0,
  buy_blocked_total_24h: 0,
  buy_blocked_ai_veto_24h: 0,
  buy_blocked_gate_24h: 0,
  buy_tradable_24h: 0,
})

let radarAuditIntervalId = null

const tileStyle = (pnl) => {
  if (pnl === 0) return { background: '#161b22', color: '#8b949e' }
  const isPos = pnl > 0
  const absP = Math.abs(pnl)
  // Scale opacity between 0.1 and 0.8 based on PnL (cap at 1M for max intensity)
  const opacity = Math.min(0.8, 0.1 + (absP / 1000000) * 0.7)
  const color = isPos ? `rgba(63, 185, 80, ${opacity})` : `rgba(248, 81, 73, ${opacity})`
  const borderColor = isPos ? `rgba(63, 185, 80, 0.3)` : `rgba(248, 81, 73, 0.3)`
  return {
    background: color,
    border: `1px solid ${borderColor}`,
    color: opacity > 0.4 ? '#fff' : '#e6edf3'
  }
}


const chartContainer = ref(null)
let chart = null
let resizeObserver = null
let rafId = null

// Timeframe state
const timeframe = ref('live')
const loadingHistory = ref(false)
const historicalPnl = ref([])

async function setTimeframe(tf) {
  timeframe.value = tf
  if (tf === 'live') {
    scheduleUpdate()
  } else {
    await fetchPnlHistory()
  }
}

async function fetchPnlHistory() {
  const tf = timeframe.value
  loadingHistory.value = true
  
  const backendUrl = import.meta.env.DEV
    ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
    : window.location.origin

  let modePrefix = '/api/live'
  try {
    const mode = localStorage.getItem('algovibe_mode')
    if (mode === 'dry') modePrefix = '/api/dry'
  } catch {}

  try {
    const res = await fetch(`${backendUrl}${modePrefix}/pnl-history/${tf}`, { headers: getAuthHeaders() })
    if (res.ok) {
      const data = await res.json()
      // Data format: [{time, pnl}, ...]
      historicalPnl.value = data
      scheduleUpdate()
    }
  } catch (e) {
    console.error('Failed to fetch PnL history:', e)
  } finally {
    loadingHistory.value = false
  }
}

// Selection & Modal state
const selectedPair = ref(null)
const selectedPairData = ref(null)
const showAiModal = ref(false)
const showRadarDetail = ref(false)
const radarDetailPair = ref(null)

function onRadarClick(pairObj) {
  // Enrich data if coming from heatmap (which only has {pair: '...'})
  let fullData = pairObj
  if (store.radarPairs && store.radarPairs.length > 0) {
    const match = store.radarPairs.find(p => p.pair === pairObj.pair)
    if (match) fullData = { ...match, ...pairObj }
  }
  // Right-click (or Ctrl+click) -> open AI detail; normal click -> chart
  chartModalPair.value = fullData.pair
  showChartModal.value = true
}

function onRadarRightClick(e, pairObj) {
  e.preventDefault()
  let fullData = pairObj
  if (store.radarPairs && store.radarPairs.length > 0) {
    const match = store.radarPairs.find(p => p.pair === pairObj.pair)
    if (match) fullData = { ...match, ...pairObj }
  }
  radarDetailPair.value = fullData
  showRadarDetail.value = true
  store.socket.emit('watch_pair', { pair: fullData.pair })
}

function closeRadarDetail() {
  showRadarDetail.value = false
  radarDetailPair.value = null
}

function openAiFromDetail() {
  const pair = radarDetailPair.value
  closeRadarDetail()
  selectedPair.value = pair
  selectedPairData.value = pair
  showAiModal.value = true
}

function closeAiModal() {
  showAiModal.value = false
  selectedPair.value = null
}

async function fetchAiInsight(pair) {
  if (!pair || !pair.pair) return 'No pair selected.'
  
  const pairName = pair.pair.toLowerCase()
  const backendUrl = import.meta.env.DEV
    ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
    : window.location.origin

  try {
    // Optional: Trigger fresh analysis
    fetch(`${backendUrl}/api/analyze/${pairName}`, { headers: getAuthHeaders() }).catch(() => {})

    const res = await fetch(`${backendUrl}/api/reasoning/${pairName}`, { headers: getAuthHeaders() })
    if (res.ok) {
      const data = await res.json()
      return data.reasoning || 'No AI intelligence vectors available for this sector currently.'
    }
  } catch (e) {
    console.error('Failed to fetch AI insight:', e)
  }
  return 'Failed to fetch Oracle intelligence data.'
}

const regimeClass = computed(() => {
  const r = stats.regime?.toLowerCase() || ''
  if (r.includes('bull')) return 'regime-bull'
  if (r.includes('bear')) return 'regime-bear'
  return 'regime-choppy'
})

const momentumStateClass = computed(() => {
  switch (stats.momentum_state) {
    case 'IMPROVING': return 'improving'
    case 'DROPPING':  return 'dropping'
    default:          return 'stalling'
  }
})

const momentumIcon = computed(() => {
  switch (stats.momentum_state) {
    case 'IMPROVING': return '▲'
    case 'DROPPING':  return '▼'
    default:          return '—'
  }
})

const velocitySuffix = computed(() => {
  const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
  return isUsdt ? 'USDT/s' : 'IDR/s'
})

const isUnblocking = ref(false)
const isTogglingGates = ref(false)

async function toggleHarvestMode() {
  const current = stats.value?.session_mode
  const isTurningOffHarvest = current === 'HARVEST_ONLY'
  const next = isTurningOffHarvest ? 'SCALPING' : 'HARVEST_ONLY'
  const label = isTurningOffHarvest ? 'NORMAL SCALPING' : 'HARVEST ONLY'
  if (!confirm(`Switch Bot to ${label}?`)) return
  
  try {
    await store.updateBotState('session_mode', next)
    // [Senior Logic] If turning OFF harvest (back to scalping), automatically ENABLE buy gates
    if (isTurningOffHarvest) {
      await store.updateBotState('session_buy_enabled', 'true')
    } else {
      // If turning ON harvest (harvest only), automatically DISABLE buy gates
      await store.updateBotState('session_buy_enabled', 'false')
    }
  } catch (err) {
    console.error('Failed to toggle harvest mode:', err)
  }
}

async function toggleBuyGates() {
  const isCurrentlyOn = stats.value?.session_buy_enabled
  const newValue = isCurrentlyOn ? 'false' : 'true'
  const label = isCurrentlyOn ? 'BLOCK ALL NEW BUYS' : 'ENABLE NEW BUYS'
  if (!confirm(`${label}?`)) return
  
  try {
    await store.updateBotState('session_buy_enabled', newValue)
  } catch (err) {
    console.error('Failed to toggle buy gates:', err)
  }
}

async function handleToggleLooseGates() {
  if (isTogglingGates.value) return
  const current = stats.value?.dry_run_loose_gates ?? false
  const action = current ? 'deactivate' : 'activate'
  if (!confirm(`${action.charAt(0).toUpperCase() + action.slice(1)} LOOSE GATES? ${current ? 'Strict entry filters will be reactivated.' : 'All strict entry filters will be bypassed.'}`)) return
  isTogglingGates.value = true
  try {
    const backendUrl = import.meta.env.DEV
      ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
      : window.location.origin
    const res = await fetch(`${backendUrl}/api/bot/toggle-loose-gates`, {
      method: 'POST',
      headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
      credentials: 'include',
    })
    const data = await res.json()
    if (data.success) {
      store.stats.dry_run_loose_gates = data.loose_gates
    } else {
      alert('Failed to toggle loose gates.')
    }
  } catch (err) {
    console.error('Toggle loose gates error:', err)
    alert('Error while toggling loose gates.')
  } finally {
    isTogglingGates.value = false
  }
}

async function handleUnblock() {
  if (isUnblocking.value) return
  const losses = stats.value?.consecutive_losses ?? 0
  if (!confirm(`Reset ${losses} consecutive losses and lift GLOBAL PAUSE? Bot will resume seeking entries.`)) return
  isUnblocking.value = true
  try {
    const backendUrl = import.meta.env.DEV
      ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
      : window.location.origin
    const res = await fetch(`${backendUrl}/api/bot/unblock`, {
      method: 'POST',
      headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
      credentials: 'include',
    })
    const data = await res.json()
    if (data.success) {
      store.stats.consecutive_losses = 0
    } else {
      alert(data.message || 'Unblock failed.')
    }
  } catch (e) {
    alert('Failed to connect to the server.')
  } finally {
    isUnblocking.value = false
  }
}

function itemClass(pair) {
  const s = pair.score || 0
  if (s >= 80) return 'conviction-high-up'
  if (s <= 20) return 'conviction-high-down'
  return ''
}

function recommendationOf(pair) {
  const backendReco = String(pair?.radar_reco || '').toUpperCase()
  if (backendReco === 'BUY' || backendReco === 'SELL' || backendReco === 'HOLD') {
    return backendReco
  }

  const rawSignal = String(pair?.signal || '').toUpperCase()
  if (rawSignal.includes('BUY')) return 'BUY'
  if (rawSignal.includes('SELL')) return 'SELL'

  const score = Number(pair?.score || 0)
  const prediction = Number(pair?.prediction || 0)
  const trend = String(pair?.trend || '').toUpperCase()

  if (score >= 70 && prediction >= 0.55) return 'BUY'
  if (score <= 30 && prediction <= 0.45) return 'SELL'
  if ((trend.includes('BULL') || trend.includes('UP')) && score >= 60) return 'BUY'
  if ((trend.includes('BEAR') || trend.includes('DOWN')) && score <= 40) return 'SELL'
  return 'HOLD'
}

function shortStatus(pair) {
  if (pair?.ai_veto) {
    const raw = String(pair?.ai_veto_reason || pair?.tradable_reason || 'AI vetoed this entry')
    return raw.length > 68 ? raw.slice(0, 68) + '...' : raw
  }
  if (pair?.tradable === false) {
    const raw = String(pair?.tradable_reason || pair?.gate_status || 'Gate entry conditions not met yet')
    return raw.length > 68 ? raw.slice(0, 68) + '...' : raw
  }
  const radarReason = String(pair?.radar_reason || '')
  if (radarReason) {
    return radarReason.length > 68 ? radarReason.slice(0, 68) + '...' : radarReason
  }
  return 'Ready for execution if other gates pass'
}

function signalPriority(pair) {
  const rec = recommendationOf(pair)
  if (rec === 'BUY') return 0
  if (rec === 'SELL') return 1
  return 2
}

function signalClass(pair) {
  const rec = recommendationOf(pair)
  if (rec === 'BUY') return 'signal-buy'
  if (rec === 'SELL') return 'signal-sell'
  return 'signal-hold'
}

const radarDisplayPairs = computed(() => {
  const filter = radarFilter.value
  const search = radarSearch.value.trim().toLowerCase()
  const heldPairSet = new Set((store.positions || []).map(p => (p.pair || '').toLowerCase()))
  let filtered = sortedRadarPairs.value.filter((pair) => {
    if (filter === 'tradable') return pair?.tradable !== false
    if (filter === 'blocked') return pair?.tradable === false
    if (filter === 'veto') return !!pair?.ai_veto
    return true
  })
  if (search) {
    filtered = filtered.filter(p => (p.pair || '').toLowerCase().includes(search))
  }
  // Pisahkan held vs others
  const held = filtered.filter(p => heldPairSet.has((p.pair || '').toLowerCase()))
  const others = filtered.filter(p => !heldPairSet.has((p.pair || '').toLowerCase()))
  return { held, others }
})

const sortedRadarPairs = computed(() => {
  const data = Array.isArray(store.radarPairs) ? store.radarPairs : []
  return [...data].sort((a, b) => {
    const pri = signalPriority(a) - signalPriority(b)
    if (pri !== 0) return pri
    const scoreDiff = Number(b?.score || 0) - Number(a?.score || 0)
    if (scoreDiff !== 0) return scoreDiff
    return String(a?.pair || '').localeCompare(String(b?.pair || ''))
  })
})

const radarCounts = computed(() => {
  const all = sortedRadarPairs.value
  const tradable = all.filter(p => p?.tradable !== false).length
  const blocked = all.filter(p => p?.tradable === false).length
  const veto = all.filter(p => !!p?.ai_veto).length
  return {
    all: all.length,
    tradable,
    blocked,
    veto,
  }
})

async function fetchRadarAudit() {
  const backendUrl = import.meta.env.DEV
    ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
    : window.location.origin

  try {
    const res = await fetch(`${backendUrl}/api/radar-audit`, { headers: getAuthHeaders() })
    if (!res.ok) return
    const data = await res.json()
    radarAudit.value = {
      buy_signals_24h: Number(data?.buy_signals_24h || 0),
      buy_blocked_total_24h: Number(data?.buy_blocked_total_24h || 0),
      buy_blocked_ai_veto_24h: Number(data?.buy_blocked_ai_veto_24h || 0),
      buy_blocked_gate_24h: Number(data?.buy_blocked_gate_24h || 0),
      buy_tradable_24h: Number(data?.buy_tradable_24h || 0),
    }
  } catch (e) {
    console.error('Failed to fetch radar audit:', e)
  }
}

onMounted(() => {
  fetchRadarAudit()
  radarAuditIntervalId = setInterval(fetchRadarAudit, 30000)
})

function scoreColor(s) {
  if (s >= 80) return '#3fb950'
  if (s <= 20) return '#f85149'
  if (s > 50) return '#58a6ff'
  return '#8b949e'
}

const sentimentClass = computed(() => {
  const s = stats.market_sentiment_index
  if (s >= 60) return 'sentiment-bull'
  if (s <= 40) return 'sentiment-bear'
  return 'sentiment-neutral'
})

function trendClass(trend) {
  if (!trend) return ''
  const t = trend.toUpperCase()
  if (t === 'UP' || t === 'BULLISH') return 'trend-up'
  if (t === 'DOWN' || t === 'BEARISH') return 'trend-down'
  return 'trend-neutral'
}

function chipSignalClass(pair) {
  const rec = recommendationOf(pair)
  if (rec === 'BUY') return 'chip-buy'
  if (rec === 'SELL') return 'chip-sell'
  return 'chip-hold'
}

function heatmapStyle(pair) {
  const score      = Number(pair?.score || 0)
  const prediction = Number(pair?.prediction || 0.5)
  const isVeto     = !!pair?.ai_veto
  const isBlocked  = pair?.tradable === false
  const rec        = recommendationOf(pair)

  // Confidence: blended from score (0-100) + prediction (0-1), clamped 0-1
  const conf = Math.min(1, Math.max(0, (score / 100) * 0.6 + prediction * 0.4))

  // AI Veto → solid deep red with red outline
  if (isVeto) {
    const lit = Math.round(10 + conf * 8)
    return {
      background: `hsla(0, 90%, ${lit}%, 0.95)`,
      outline: '1px solid rgba(248,81,73,0.6)'
    }
  }

  if (rec === 'BUY' && !isBlocked) {
    // Green family: hue 118-130, intensity driven by confidence
    const sat = Math.round(40 + conf * 50)   // 40..90%
    const lit = Math.round(8  + conf * 17)   // 8..25%
    return { background: `hsla(122, ${sat}%, ${lit}%, 0.95)` }
  }

  if (rec === 'SELL' || isBlocked) {
    // Red family: hue 0-5, intensity driven by (1 - confidence for sells) or flat for blocked
    const intensity = rec === 'SELL' ? (1 - conf) : 0.5
    const sat = Math.round(40 + intensity * 50)  // 40..90%
    const lit = Math.round(8  + intensity * 14)  // 8..22%
    // Desaturate blocked-only (non-sell)
    if (isBlocked && rec !== 'SELL') {
      return { background: `hsla(0, ${Math.round(sat * 0.5)}%, ${Math.round(lit * 0.8)}%, 0.75)` }
    }
    return { background: `hsla(2, ${sat}%, ${lit}%, 0.95)` }
  }

  // HOLD → muted amber, scaled by how close conf is to 0.5
  const dist = Math.abs(conf - 0.5)
  const sat  = Math.round(20 + dist * 40)
  const lit  = Math.round(8  + dist * 10)
  return { background: `hsla(40, ${sat}%, ${lit}%, 0.85)` }
}

function formatPair(pair) {
  if (!pair) return ''
  return pair.replace(/idr$/i, '').replace(/usdt$/i, '').toUpperCase()
}

function formatPrice(val) {
  const n = Number(val) || 0
  const isUsdt = (stats.value?.btc_price && stats.value.btc_price < 500000) || n < 500000
  if (isUsdt) {
    if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}k`
    return `$${n.toFixed(2)}`
  } else {
    if (n >= 1_000_000_000) return `Rp ${(n / 1_000_000_000).toFixed(2)}M`
    if (n >= 1_000_000) return `Rp ${(n / 1_000_000).toFixed(2)}jt`
    if (n >= 1_000) return `Rp ${(n / 1_000).toFixed(1)}k`
    return `Rp ${n.toFixed(0)}`
  }
}

// BTC Condition computed helpers
const btcStatusLabel = computed(() => {
  if (store.stats.btc_crash)    return '🔴 CRASH'
  if (store.stats.btc_weakening) return '🟡 WEAKENING'
  return '🟢 STRONG'
})

const btcBarClass = computed(() => {
  if (store.stats.btc_crash)    return 'btc-bar-crash'
  if (store.stats.btc_weakening) return 'btc-bar-weak'
  return 'btc-bar-strong'
})

const btcStatusBadgeClass = computed(() => {
  if (store.stats.btc_crash)    return 'badge-crash'
  if (store.stats.btc_weakening) return 'badge-weak'
  return 'badge-strong'
})

const btcTrendChipClass = computed(() => {
  const t = (store.stats.btc_trend || '').toUpperCase()
  if (t === 'UP' || t === 'BULLISH') return 'chip-up'
  if (t === 'DOWN' || t === 'BEARISH') return 'chip-dn'
  return 'chip-neutral'
})

function formatIdr(val) {
  const n = Number(val) || 0
  const abs = Math.abs(n)
  const prefix = n >= 0 ? '+' : '-'
  const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
  if (isUsdt) {
    if (abs >= 1_000_000) return `${prefix}$${(abs / 1_000_000).toFixed(2)}M`
    if (abs >= 1_000) return `${prefix}$${(abs / 1_000).toFixed(1)}k`
    return `${prefix}$${abs.toFixed(2)}`
  } else {
    if (abs >= 1_000_000) return `${prefix}Rp ${(abs / 1_000_000).toFixed(2)}M`
    if (abs >= 1_000) return `${prefix}Rp ${(abs / 1_000).toFixed(1)}k`
    return `${prefix}Rp ${abs.toFixed(0)}`
  }
}

function buildChartOption() {
  if (timeframe.value === 'live') {
    const history = store.pnlHistory
    const times = history.map(h => h.time)
    const values = history.map(h => h.pnl)
    const lastVal = values[values.length - 1] || 0
    const lineColor = lastVal >= 0 ? '#3fb950' : '#f85149'

    return getCommonOption(times, values, lineColor, 'Equity (Live)')
  } else {
    const times = historicalPnl.value.map(d => d.time)
    const values = historicalPnl.value.map(d => d.pnl)
    const lastVal = values[values.length - 1] || 0
    const lineColor = lastVal >= 0 ? '#3fb950' : '#f85149'
    const label = `Equity (${timeframe.value.toUpperCase()})`
    return getCommonOption(times, values, lineColor, label)
  }
}

function getCommonOption(times, values, lineColor, label) {
  return {
    backgroundColor: 'transparent',
    grid: { top: 40, right: 30, bottom: 30, left: 70 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1c2128',
      borderColor: '#30363d',
      textStyle: { color: '#e6edf3', fontSize: 12 },
      formatter: (params) => {
        const p = params[0]
        const n = Number(p.value)
        const prefix = n >= 0 ? '+' : ''
        const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
        const formattedValue = isUsdt 
          ? `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
          : `Rp ${n.toLocaleString('en-US')}`
        return `<div style="font-size:10px;color:#8b949e">${p.axisValue}</div>
                <div style="font-size:10px;color:#8b949e">${label}</div>
                <div style="font-weight:700;color:${lineColor}">${prefix}${formattedValue}</div>`
      },
    },
    xAxis: {
      type: 'category',
      data: times,
      axisLine: { lineStyle: { color: '#30363d' } },
      axisLabel: {
        color: '#8b949e',
        fontSize: 10,
        interval: Math.max(1, Math.floor(times.length / 8)),
      },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { show: false },
      splitLine: { lineStyle: { color: '#21262d', type: 'dashed' } },
      axisLabel: {
        color: '#8b949e',
        fontSize: 10,
        formatter: (v) => {
          const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
          if (isUsdt) {
            if (Math.abs(v) >= 1_000_000) return '$' + (v / 1_000_000).toFixed(1) + 'M'
            if (Math.abs(v) >= 1_000) return '$' + (v / 1_000).toFixed(0) + 'K'
            return '$' + v
          } else {
            if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M'
            if (Math.abs(v) >= 1_000) return (v / 1_000).toFixed(0) + 'k'
            return v
          }
        },
      },
    },
    series: [
      {
        name: label,
        type: 'line',
        data: values,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        showSymbol: false,
        lineStyle: { color: lineColor, width: 2.5, shadowBlur: 10, shadowColor: lineColor + '66' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: lineColor + '44' },
              { offset: 1, color: lineColor + '00' }
            ]
          }
        },
        markLine: timeframe.value === 'live' ? {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#30363d', type: 'solid', width: 1 },
          data: [{ yAxis: 0 }],
        } : null,
      },
    ],
  }
}

let updateQueued = false
function scheduleUpdate() {
  if (updateQueued) return
  updateQueued = true
  rafId = requestAnimationFrame(() => {
    updateQueued = false
    if (!chart || !chartContainer.value) return
    if (typeof chart.isDisposed === 'function' && chart.isDisposed()) return
    chart.setOption(buildChartOption())
  })
}

watch(() => store.pnlHistory.length, scheduleUpdate)
watch(
  () => {
    const last = store.pnlHistory[store.pnlHistory.length - 1]
    return last ? `${last.time}:${last.pnl}` : 'empty'
  },
  scheduleUpdate
)
watch(() => `${stats.value.net_pnl}|${stats.value.daily_pnl}|${stats.value.unrealized_pnl}|${stats.value.momentum_state}|${stats.value.net_pnl_velocity}|${stats.value.active_positions}`, scheduleUpdate)
watch(() => timeframe.value, scheduleUpdate)
watch(() => store.connected, (conn) => {
  if (conn) {
    setTimeout(scheduleUpdate, 500)
    setTimeout(() => { if (chart) chart.resize() }, 1000)
  }
})

onMounted(async () => {
  await initializeECharts()
  if (!chartContainer.value || !echarts) return
  chart = echarts.init(chartContainer.value, null, { renderer: 'canvas' })
  chart.setOption(buildChartOption())

  resizeObserver = new ResizeObserver(() => {
    if (!chart) return
    if (typeof chart.isDisposed === 'function' && chart.isDisposed()) return
    chart.resize()
  })
  resizeObserver.observe(chartContainer.value)
})

onUnmounted(() => {

  if (radarAuditIntervalId) {
    clearInterval(radarAuditIntervalId)
    radarAuditIntervalId = null
  }
  if (rafId != null) {
    cancelAnimationFrame(rafId)
    rafId = null
  }
  updateQueued = false
  resizeObserver?.disconnect()
  resizeObserver = null
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.chart-panel {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: #161b22;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid #30363d;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
}

/* KPI Bar */
.kpi-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid #30363d;
  background: #0d1117;
  flex-shrink: 0;
  flex-wrap: wrap; /* [Senior Fix] Allow wrapping to prevent cutoff on smaller screens */
}

.kpi-item {
  flex: 1 1 auto; /* Allow items to grow and shrink as needed */
  min-width: 100px; /* Ensure items have a minimum width to keep labels readable */
  padding: 10px 12px;
  border-right: 1px solid #21262d;
  border-bottom: 1px solid #21262d; /* Add bottom border for wrapped items */
}
.kpi-item:last-child { border-right: none; }

.kpi-label {
  font-size: 9px;
  color: #8b949e;
  text-transform: uppercase;
  letter-spacing: 1.2px;
  margin-bottom: 4px;
  font-weight: 600;
}

.kpi-value {
  font-size: 15px;
  font-weight: 800;
  color: #e6edf3;
  font-variant-numeric: tabular-nums;
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
}
.kpi-value.positive { color: #3fb950; text-shadow: 0 0 10px rgba(63, 185, 80, 0.3); }
.kpi-value.negative { color: #f85149; text-shadow: 0 0 10px rgba(248, 81, 73, 0.3); }

/* Momentum Indicator */
.momentum-kpi {
  position: relative;
}
.momentum-value {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}
.momentum-arrow {
  font-size: 14px;
  font-weight: 900;
  line-height: 1;
}
.momentum-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.momentum-velocity {
  font-size: 10px;
  color: #8b949e;
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
}
.momentum-value.momentum-improving,
.momentum-improving .momentum-arrow { color: #3fb950; text-shadow: 0 0 10px rgba(63, 185, 80, 0.4); }
.momentum-value.momentum-dropping,
.momentum-dropping .momentum-arrow  { color: #f85149; text-shadow: 0 0 10px rgba(248, 81, 73, 0.4); }
.momentum-value.momentum-stalling,
.momentum-stalling .momentum-arrow  { color: #d29922; text-shadow: 0 0 8px rgba(210, 153, 34, 0.3); }
.momentum-kpi.momentum-improving { background: rgba(63, 185, 80, 0.04); }
.momentum-kpi.momentum-dropping  { background: rgba(248, 81, 73, 0.04); }
.momentum-kpi.momentum-stalling  { background: rgba(210, 153, 34, 0.04); }

.unblock-btn {
  margin-top: 5px;
  padding: 3px 8px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.5px;
  background: rgba(248, 81, 73, 0.15);
  color: #f85149;
  border: 1px solid rgba(248, 81, 73, 0.4);
  border-radius: 4px;
  cursor: pointer;
  width: 100%;
  transition: background 0.15s, opacity 0.15s;
}
.unblock-btn:hover:not(:disabled) {
  background: rgba(248, 81, 73, 0.3);
}
.unblock-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.loose-gates-btn {
  margin-top: 5px;
  padding: 3px 8px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.5px;
  background: rgba(139, 148, 158, 0.12);
  color: #8b949e;
  border: 1px solid rgba(139, 148, 158, 0.3);
  border-radius: 4px;
  cursor: pointer;
  width: 100%;
  transition: background 0.15s, opacity 0.15s, color 0.15s;
}
.loose-gates-btn.active {
  background: rgba(210, 153, 34, 0.18);
  color: #d29922;
  border-color: rgba(210, 153, 34, 0.5);
}
.loose-gates-btn:hover:not(:disabled) {
  background: rgba(139, 148, 158, 0.25);
}
.loose-gates-btn.active:hover:not(:disabled) {
  background: rgba(210, 153, 34, 0.3);
}
.loose-gates-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.kpi-toggle-btn {
  margin-top: 5px;
  padding: 4px 10px;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.5px;
  background: rgba(139, 148, 158, 0.1);
  color: #8b949e;
  border: 1px solid rgba(139, 148, 158, 0.3);
  border-radius: 4px;
  cursor: pointer;
  width: 100%;
  transition: all 0.2s ease;
  text-align: center;
}
.kpi-toggle-btn.active {
  background: rgba(163, 113, 247, 0.15);
  color: #a371f7;
  border-color: rgba(163, 113, 247, 0.5);
  box-shadow: 0 0 12px rgba(163, 113, 247, 0.2);
}
.kpi-toggle-btn:hover:not(:disabled) {
  background: rgba(139, 148, 158, 0.2);
  color: #e6edf3;
  border-color: rgba(139, 148, 158, 0.5);
}
.kpi-toggle-btn.active:hover:not(:disabled) {
  background: rgba(163, 113, 247, 0.25);
  border-color: rgba(163, 113, 247, 0.7);
}

.regime-badge {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 4px;
  display: inline-block;
  font-weight: 700;
}
.regime-bull  { background: rgba(63, 185, 80, 0.15); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.3); }
.regime-bear  { background: rgba(248, 81, 73, 0.15); color: #f85149; border: 1px solid rgba(248, 81, 73, 0.3); }
.regime-choppy { background: rgba(210, 153, 34, 0.15); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.3); }

.sentiment-bar {
  position: relative;
  background: #21262d;
  border-radius: 4px;
  height: 16px;
  overflow: hidden;
  margin-top: 4px;
  border: 1px solid #30363d;
}
.sentiment-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1);
}
.sentiment-bull    { background: linear-gradient(90deg, #3fb950, #2ea043); box-shadow: 0 0 10px rgba(63, 185, 80, 0.4); }
.sentiment-bear    { background: linear-gradient(90deg, #f85149, #da3633); box-shadow: 0 0 10px rgba(248, 81, 73, 0.4); }
.sentiment-neutral { background: linear-gradient(90deg, #d29922, #b08800); }

.sentiment-num {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 9px;
  font-weight: 800;
  color: #fff;
  text-shadow: 0 1px 2px rgba(0,0,0,0.5);
}

/* Intelligence Panel (Unified Oracle) */
.intelligence-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 16px 20px;
  background: linear-gradient(180deg, rgba(163, 113, 247, 0.04) 0%, rgba(13, 17, 23, 0.4) 100%);
  border-top: 1px solid rgba(163, 113, 247, 0.15);
  min-height: 0;
  overflow-y: auto;
}

.chart-wrap-integrated {
  height: 290px;
  min-height: 290px;
  position: relative;
  margin: 12px 0 20px;
  background: #0d1117;
  border-radius: 12px;
  border: 1px solid rgba(48, 54, 61, 0.8);
  box-shadow: inset 0 2px 10px rgba(0,0,0,0.3);
}

.chart-canvas-small {
  position: absolute;
  inset: 0;
}

.chart-empty-small {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #484f58;
  font-size: 11px;
}

.chart-header-actions {
  position: absolute;
  top: 10px;
  right: 15px;
  z-index: 10;
}

.tf-selector {
  display: flex;
  background: #0d1117;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 2px;
  gap: 2px;
}

.tf-selector button {
  background: transparent;
  border: none;
  color: #8b949e;
  font-size: 9px;
  font-weight: 800;
  padding: 3px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
  text-transform: uppercase;
}

.tf-selector button:hover {
  color: #e6edf3;
  background: rgba(255, 255, 255, 0.05);
}

.tf-selector button.active {
  background: #30363d;
  color: #58a6ff;
}

.chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #8b949e;
  font-size: 13px;
  pointer-events: none;
  gap: 12px;
}

.loading-dots {
  display: flex;
  gap: 4px;
}
.loading-dots span {
  width: 6px;
  height: 6px;
  background: #58a6ff;
  border-radius: 50%;
  animation: dot-pulse 1.5s infinite ease-in-out;
}
.loading-dots span:nth-child(2) { animation-delay: 0.2s; }
.loading-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes dot-pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.1); }
}

.intel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.intel-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 800;
  font-size: 12px;
  color: #a371f7;
  letter-spacing: 1px;
  text-transform: uppercase;
}

.intel-icon { font-size: 16px; }

.intel-meta {
  display: flex;
  gap: 8px;
}

.intel-chip {
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 10px;
  font-weight: 800;
  background: #161b22;
  border: 1px solid #30363d;
  color: #8b949e;
}

.intel-chip.bullish, .intel-chip.up { 
  background: rgba(63, 185, 80, 0.1); 
  color: #3fb950; 
  border-color: rgba(63, 185, 80, 0.3);
}
.intel-chip.bearish, .intel-chip.down { 
  background: rgba(248, 81, 73, 0.1); 
  color: #f85149; 
  border-color: rgba(248, 81, 73, 0.3);
}

.intel-body {
  border-left: 2px solid rgba(163, 113, 247, 0.3);
  padding-left: 14px;
}

.intel-reasoning.report-style {
  color: #e6edf3;
  font-size: 12px;
  line-height: 1.4;
  white-space: pre-line;
  margin-bottom: 12px;
  font-family: 'JetBrains Mono', monospace;
  background: rgba(13, 17, 23, 0.4);
  padding: 10px;
  border-radius: 6px;
  border: 1px solid rgba(48, 54, 61, 0.5);
}

.report-meta {
  font-size: 9px;
  color: #484f58;
  font-weight: 800;
  margin-bottom: 12px;
  text-align: right;
  letter-spacing: 0.5px;
}

.intel-action-plan {
  background: rgba(88, 166, 255, 0.05);
  border: 1px solid rgba(88, 166, 255, 0.2);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 16px;
}

.action-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 10px;
  font-weight: 800;
  color: #58a6ff;
  letter-spacing: 1px;
  margin-bottom: 6px;
}

.action-pulse {
  width: 6px;
  height: 6px;
  background: #58a6ff;
  border-radius: 50%;
  box-shadow: 0 0 8px #58a6ff;
  animation: action-blink 1s ease-in-out infinite;
}

@keyframes action-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.action-text {
  color: #e6edf3;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.4;
  font-family: 'JetBrains Mono', monospace;
}

.intel-pulse-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #30363d;
}

.pulse-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.p-label {
  font-size: 9px;
  font-weight: 800;
  color: #8b949e;
  letter-spacing: 1px;
}

.p-value {
  font-size: 14px;
  font-weight: 800;
  color: #e6edf3;
  font-family: 'JetBrains Mono', monospace;
}

.p-value.highlight {
  color: #a371f7;
  text-shadow: 0 0 8px rgba(163, 113, 247, 0.4);
}

.p-value small {
  font-size: 9px;
  opacity: 0.6;
}

/* Equity Attribution Heatmap */
.asset-heatmap {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid rgba(48, 54, 61, 0.5);
}

.heatmap-header {
  font-size: 9px;
  font-weight: 800;
  color: #484f58;
  letter-spacing: 1px;
  margin-bottom: 10px;
  text-transform: uppercase;
}

.heatmap-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  max-height: 200px;
  overflow-y: auto;
  padding-bottom: 8px;
}

.heatmap-grid::-webkit-scrollbar { width: 3px; }
.heatmap-grid::-webkit-scrollbar-thumb { background: #30363d; border-radius: 10px; }

.heatmap-tile {
  flex: 0 0 calc(20% - 3px); /* 5 tiles per row for premium spacing */
  min-width: 48px;
  height: 38px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  padding: 3px;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
  border: 1px solid rgba(255,255,255,0.05);
}

.heatmap-tile:hover {
  transform: translateY(-2px) scale(1.08);
  filter: brightness(1.2);
  z-index: 5;
  box-shadow: 0 4px 15px rgba(163, 113, 247, 0.25);
  border-color: rgba(163, 113, 247, 0.5) !important;
}

.tile-label {
  font-size: 8px;
  font-weight: 800;
  font-family: 'JetBrains Mono', monospace;
  line-height: 1.1;
  text-transform: uppercase;
}

.tile-pnl {
  display: block;
  font-size: 7px;
  font-weight: 700;
  margin-top: 2px;
  line-height: 1;
  opacity: 0.85;
}

.intel-loading {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #8b949e;
  font-size: 12px;
  font-style: italic;
}

.intel-dots {
  display: flex;
  gap: 4px;
}

.intel-dots span {
  width: 4px;
  height: 4px;
  background: #a371f7;
  border-radius: 50%;
  animation: intel-pulse 1.4s infinite ease-in-out;
}

.intel-dots span:nth-child(2) { animation-delay: 0.2s; }
.intel-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes intel-pulse {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

/* Radar */
.radar-section { 
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  max-height: 240px;
  border-top: 1px solid #30363d;
  background: #0d1117;
  padding-bottom: 4px;
}

/* BTC Condition Bar */
.btc-condition-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 0 14px 8px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid #30363d;
  background: #161b22;
  font-size: 10px;
  font-weight: 700;
  transition: border-color 0.3s ease, background 0.3s ease;
}
.btc-bar-strong {
  border-color: rgba(63, 185, 80, 0.4);
  background: linear-gradient(90deg, rgba(63, 185, 80, 0.06) 0%, #161b22 100%);
}
.btc-bar-weak {
  border-color: rgba(210, 153, 34, 0.4);
  background: linear-gradient(90deg, rgba(210, 153, 34, 0.06) 0%, #161b22 100%);
}
.btc-bar-crash {
  border-color: rgba(248, 81, 73, 0.5);
  background: linear-gradient(90deg, rgba(248, 81, 73, 0.08) 0%, #161b22 100%);
  animation: btc-crash-pulse 1.5s ease-in-out infinite alternate;
}
@keyframes btc-crash-pulse {
  from { border-color: rgba(248, 81, 73, 0.5); }
  to   { border-color: rgba(248, 81, 73, 0.9); }
}
.btc-bar-left {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.btc-icon {
  font-size: 12px;
  color: #f0a500;
}
.btc-label {
  color: #8b949e;
  letter-spacing: 0.5px;
}
.btc-price {
  color: #e6edf3;
  font-size: 11px;
}
.btc-bar-mid {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.btc-delta {
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 9px;
  font-weight: 800;
}
.delta-up { color: #3fb950; background: rgba(63, 185, 80, 0.1); }
.delta-dn { color: #f85149; background: rgba(248, 81, 73, 0.1); }
.btc-trend-chip {
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.3px;
}
.chip-up      { color: #3fb950; background: rgba(63, 185, 80, 0.12); }
.chip-dn      { color: #f85149; background: rgba(248, 81, 73, 0.12); }
.chip-neutral { color: #8b949e; background: rgba(139, 148, 158, 0.1); }
.btc-bar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.btc-status-badge {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 9px;
  font-weight: 900;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.badge-strong { background: rgba(63, 185, 80, 0.15);  color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.3);  }
.badge-weak   { background: rgba(210, 153, 34, 0.15); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.3); }
.badge-crash  { background: rgba(248, 81, 73, 0.15);  color: #f85149; border: 1px solid rgba(248, 81, 73, 0.5);  }
.btc-score-val {
  font-size: 9px;
  color: #8b949e;
  font-weight: 800;
}
.section-label {
  padding: 10px 16px 6px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: #8b949e;
}

.radar-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.radar-total {
  color: #58a6ff;
  font-size: 9px;
  font-weight: 800;
}

.radar-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 14px 6px;
}

.radar-filters {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.radar-filter-btn {
  border: 1px solid #30363d;
  background: #161b22;
  color: #8b949e;
  font-size: 9px;
  font-weight: 800;
  padding: 3px 8px;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.radar-filter-btn:hover {
  color: #e6edf3;
  border-color: #58a6ff;
}

.radar-filter-btn.active {
  background: rgba(88, 166, 255, 0.12);
  color: #58a6ff;
  border-color: rgba(88, 166, 255, 0.45);
}

.radar-audit-mini {
  display: flex;
  gap: 8px;
  font-size: 9px;
  font-weight: 800;
  color: #8b949e;
  white-space: nowrap;
}

.audit-label {
  color: #58a6ff;
  letter-spacing: 0.3px;
}

.radar-scroll-wrap {
  flex: 1;
  min-height: 0;
  max-height: none;
  overflow-y: auto;
  padding-right: 2px;
}

/* Custom scrollbar for radar */
.radar-scroll-wrap::-webkit-scrollbar { width: 4px; }
.radar-scroll-wrap::-webkit-scrollbar-track { background: transparent; }
.radar-scroll-wrap::-webkit-scrollbar-thumb { background: #30363d; border-radius: 10px; }
.radar-scroll-wrap::-webkit-scrollbar-thumb:hover { background: #a371f7; }

/* === Radar heatmap grid (20 col × N row) === */
.radar-list {
  display: grid;
  grid-template-columns: repeat(20, 1fr);
  gap: 2px;
  padding: 4px 10px 6px;
}

.radar-item {
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 3px;
  cursor: pointer;
  overflow: hidden;
  min-width: 0;
  transition: filter 0.15s, transform 0.1s;
  position: relative;
}

.radar-item:hover {
  filter: brightness(1.45);
  transform: scale(1.08);
  z-index: 2;
}

.active-radar {
  outline: 1.5px solid #a371f7;
  filter: brightness(1.3);
  z-index: 2;
}

/* Chip content */
.ri-name {
  font-size: 6.5px;
  font-weight: 700;
  color: rgba(255,255,255,0.88);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: center;
  padding: 0 2px;
  display: flex;
  align-items: center;
  gap: 1px;
  line-height: 1;
  text-shadow: 0 1px 2px rgba(0,0,0,0.7);
}

.crown-micro { font-size: 6px; filter: drop-shadow(0 0 3px gold); }

.ri-veto-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: rgba(248,81,73,0.95);
  color: #fff;
  font-size: 5.5px;
  font-weight: 900;
  flex-shrink: 0;
  line-height: 1;
}

/* Search box */
.radar-search-wrap { padding: 4px 10px 2px; }
.radar-search {
  width: 100%;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 5px;
  color: #e6edf3;
  font-size: 11px;
  padding: 4px 8px;
  outline: none;
  box-sizing: border-box;
}
.radar-search::placeholder { color: #6e7681; }
.radar-search:focus { border-color: #58a6ff; }

/* Held / Radar group labels */
.radar-group-label {
  padding: 4px 10px 2px;
  font-size: 9px;
  font-weight: 800;
  color: #8b949e;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.radar-held { outline: 1px solid rgba(88,166,255,0.4); }

.radar-empty { padding: 12px 10px; font-size: 11px; color: #6e7681; text-align: center; }

/* Chart Modal */
.chart-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.65);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.chart-modal {
  background: #0d1117;
  border: 1px solid #30363d;
  border-radius: 10px;
  width: min(92vw, 860px);
  height: min(88vh, 620px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 24px 64px rgba(0,0,0,0.6);
}
.chart-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid #21262d;
  flex-shrink: 0;
}
.chart-modal-title { font-size: 14px; font-weight: 800; color: #e6edf3; }
.chart-modal-close {
  background: transparent;
  border: none;
  color: #8b949e;
  font-size: 16px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
}
.chart-modal-close:hover { background: #21262d; color: #e6edf3; }
.chart-modal-body { flex: 1; min-height: 0; overflow: hidden; }

.radar-trend { font-size: 7.5px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.3px; }
.trend-up   { color: #3fb950; }
.trend-down { color: #f85149; }
.trend-neutral { color: #8b949e; }
.radar-prob { 
  color: #fff; 
  font-size: 9px; 
  font-weight: 900;
  background: #a371f7;
  padding: 1px 5px;
  border-radius: 4px;
  box-shadow: 0 2px 6px rgba(163, 113, 247, 0.3);
}

@media (max-width: 1000px) {
  .chart-panel {
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  .kpi-bar {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
  }
  .kpi-item {
    padding: 10px;
    border-right: 1px solid #21262d;
    border-bottom: 1px solid #21262d;
  }
  .kpi-item:nth-child(2n) { border-right: none; }
  
  .intelligence-panel {
    padding: 12px 16px;
  }
  
  .chart-wrap-integrated {
    height: 230px;
    min-height: 230px;
  }

  .radar-section {
    max-height: 200px;
  }

  .heatmap-tile {
    flex: 0 0 calc(20% - 3px); /* 5 columns on mobile */
    min-width: 50px;
    height: 36px;
  }
  
  .tile-label { font-size: 8px; }

  .radar-list {
    grid-template-columns: repeat(10, 1fr);
    gap: 2px;
    padding: 4px 10px 8px;
  }

  .radar-toolbar {
    flex-direction: column;
    align-items: flex-start;
    padding: 0 10px 6px;
  }
}

@media (max-width: 480px) {
  .chart-wrap-integrated {
    height: 190px;
    min-height: 190px;
  }

  .radar-section {
    max-height: 180px;
  }

  .radar-list { grid-template-columns: repeat(6, 1fr); }
  .heatmap-tile { flex: 0 0 calc(25% - 3px); } /* 4 columns for very small */
}
</style>
