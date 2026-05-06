<template>
  <div v-if="show" class="ai-modal-overlay" @click.self="close">
    <div class="ai-modal">
      <div class="ai-modal-header">
        <div class="header-title">
          <span class="ai-icon">🔮</span>
          <span>ORACLE INTELLIGENCE: {{ formatPair(pair?.pair) }}</span>
          <span class="live-price-badge" v-if="livePrice">
            <span class="price-dot"></span>
            Rp {{ livePrice.toLocaleString('id-ID') }}
          </span>
        </div>
        <button class="close-btn" @click="close">×</button>
      </div>
      <div class="ai-modal-body">
        <div v-if="loading" class="loading-state">
          <div class="pulse-ring"></div>
          <span>Processing market vectors…</span>
        </div>
        <div v-else-if="error" class="error-state">
          <span class="error-icon">⚠️</span>
          <span>Data stream interrupted. Please retry.</span>
        </div>
        <div v-else class="content-state">
            <!-- Market Context Bridge (Order Book & Stats) -->
            <div class="market-context-grid" v-if="context">
              <div class="context-section">
                <div class="insight-label">ORDER BOOK DEPTH</div>
                <div class="orderbook-viz">
                  <div class="depth-bar">
                    <div class="depth-bid" :style="{ width: bidRatio + '%' }"></div>
                    <div class="depth-ask" :style="{ width: askRatio + '%' }"></div>
                  </div>
                  <div class="depth-labels">
                    <span class="d-bid">BIDS {{ bidRatio }}%</span>
                    <span class="d-ask">{{ askRatio }}% ASKS</span>
                  </div>
                </div>
              </div>

              <div class="context-section">
                <div class="insight-label">MARKET METRICS</div>
                <div class="metrics-mini-grid">
                  <div class="m-metric">
                    <span class="mm-label">SPREAD</span>
                    <span class="mm-value">{{ context.technicals?.spread?.toFixed(3) }}%</span>
                  </div>
                  <div class="m-metric">
                    <span class="mm-label">VOLATILITY</span>
                    <span class="mm-value">{{ context.technicals?.volatility?.toFixed(2) }}%</span>
                  </div>
                  <div class="m-metric">
                    <span class="mm-label">24H VOL</span>
                    <span class="mm-value">Rp {{ (context.stats?.vol_24h / 1_000_000).toFixed(1) }}M</span>
                  </div>
                </div>
              </div>
            </div>

            <div v-show="insight" class="insight-wrap">
              <div class="insight-label">ANALYSIS SUMMARY</div>
              <div class="insight-text">{{ insight }}</div>

              <div class="insight-label">MARKET VISUALIZATION</div>
              <div ref="chartRef" class="modal-chart"></div>

              <!-- Session Performance Bridge -->
              <div class="insight-label" v-if="pair?.pnl != null">SESSION PERFORMANCE AUDIT</div>
              <div class="session-perf-row" v-if="pair?.pnl != null">
                <div class="perf-metric">
                  <span class="pm-label">CONTRIBUTION</span>
                  <span class="pm-value" :class="pair.pnl >= 0 ? 'up' : 'down'">
                    {{ pair.pnl >= 0 ? '+' : '' }}{{ formatIdr(pair.pnl) }}
                  </span>
                </div>
                <div class="perf-metric">
                  <span class="pm-label">EXECUTION</span>
                  <span class="pm-value">{{ pair.trades || 0 }} <small>TRADES</small></span>
                </div>
              </div>

              <div class="insight-meta" v-if="pair">
                <div class="meta-item">
                  <span class="m-label">PROBABILITY</span>
                  <span class="m-value">{{ ((pair.prediction || 0) * 100).toFixed(1) }}%</span>
                </div>
                <div class="meta-item">
                  <span class="m-label">TREND</span>
                  <span class="m-value" :class="pair.trend?.toLowerCase()">{{ pair.trend || 'NEUTRAL' }}</span>
                </div>
                <div class="meta-item">
                  <span class="m-label">AI VERDICT</span>
                  <span class="m-value highlight-purple">BIJAK</span>
                </div>
              </div>
            </div>
          <div v-if="!insight" class="empty-state">
            <span class="empty-icon">📂</span>
            <span>No localized intelligence available for this sector.</span>
          </div>
        </div>
      </div>
      <div class="ai-modal-footer">
        <button class="action-btn" @click="close">ACKNOWLEDGE</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'

const props = defineProps({
  show: Boolean,
  pair: Object,
  onClose: Function,
  fetchInsight: Function,
})

const store = useTradeStore()
const { getAuthHeaders } = useAuth()

const livePrice = computed(() => {
  if (!props.pair?.pair) return null
  const match = store.radarPairs.find(p => p.pair === props.pair.pair)
  return match ? match.price : (context.value?.stats?.last || null)
})

const insight = ref('')
const loading = ref(false)
const error = ref(false)
const chartRef = ref(null)
const context = ref(null)
let chartInstance = null

const bidRatio = computed(() => {
  if (!context.value?.technicals) return 50
  return context.value.technicals.buy_pressure || 50
})
const askRatio = computed(() => 100 - bidRatio.value)

function close() {
  props.onClose && props.onClose()
}

function formatPair(pair) {
  if (!pair) return '-'
  return pair.replace(/idr$/i, '').replace(/usdt$/i, '').toUpperCase()
}

async function loadInsight() {
  if (!props.pair || !props.show) return
  loading.value = true
  error.value = false
  insight.value = ''
  context.value = null
  
  try {
    const [insightRes, chartDataRes, contextDataRes] = await Promise.allSettled([
      props.fetchInsight(props.pair),
      fetchChartData(props.pair.pair),
      fetchMarketContext(props.pair.pair)
    ])

    const resolvedInsight = insightRes.status === 'fulfilled' ? insightRes.value : ''
    const chartData = chartDataRes.status === 'fulfilled' ? chartDataRes.value : []
    const contextData = contextDataRes.status === 'fulfilled' ? contextDataRes.value : null

    insight.value = resolvedInsight || 'Tidak ada nalar AI yang tersedia saat ini.'
    context.value = contextData
    loading.value = false

    await nextTick()
    if (Array.isArray(chartData) && chartData.length) {
      initChart(chartData)
    }

    // Mark error only when all data channels fail completely.
    if (!insight.value && !context.value && (!Array.isArray(chartData) || chartData.length === 0)) {
      error.value = true
    }
  } catch (err) {
    console.error('Modal Load Error:', err)
    error.value = true
    loading.value = false
  }
}

async function fetchMarketContext(pair) {
  if (!pair) return null
  const backendUrl = import.meta.env.DEV
    ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
    : window.location.origin
  const res = await fetch(`${backendUrl}/api/market-context/${pair.toLowerCase()}`, {
    headers: getAuthHeaders(),
    credentials: 'include',
  })
  if (res.ok) return await res.json()
  return null
}

async function fetchChartData(pair) {
  if (!pair) return []
  const backendUrl = import.meta.env.DEV
    ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
    : window.location.origin
  const res = await fetch(`${backendUrl}/api/ohlcv/${pair.toLowerCase().replace('/', '')}/1h`, {
    headers: getAuthHeaders(),
    credentials: 'include',
  })
  if (res.ok) return await res.json()
  return []
}

function initChart(data) {
  if (!chartRef.value || !data || !data.length) {
    console.warn('No chart data or ref available');
    return
  }
  
  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value)
  }

  // CCXT Format: [timestamp, open, high, low, close, volume]
  const times = data.map(item => {
    const d = new Date(item[0])
    return d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })
  })
  const prices = data.map(item => item[4])

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      show: true,
      trigger: 'axis',
      backgroundColor: '#1c2128',
      borderColor: '#30363d',
      textStyle: { color: '#e6edf3', fontSize: 10 },
      formatter: '{b}: Rp {c}'
    },
    grid: { left: '2%', right: '2%', top: '10%', bottom: '10%', containLabel: false },
    xAxis: {
      show: false,
      type: 'category',
      data: times
    },
    yAxis: {
      show: false,
      type: 'value',
      scale: true
    },
    series: [{
      type: 'line',
      smooth: true,
      showSymbol: false,
      data: prices,
      lineStyle: { width: 2, color: '#a371f7' },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(163, 113, 247, 0.3)' },
            { offset: 1, color: 'rgba(163, 113, 247, 0)' }
          ]
        }
      }
    }]
  }
  chartInstance.setOption(option)
  
  // Resize to container
  setTimeout(() => {
    chartInstance.resize()
  }, 100)
}

watch(() => props.show, (newVal) => {
  if (newVal) loadInsight()
})

function formatIdr(val) {
  const n = Number(val) || 0
  const abs = Math.abs(n)
  const prefix = n >= 0 ? '+' : '-'
  if (abs >= 1_000_000) return `${prefix}Rp ${(abs / 1_000_000).toFixed(2)}jt`
  if (abs >= 1_000) return `${prefix}Rp ${(abs / 1_000).toFixed(1)}k`
  return `${prefix}Rp ${abs.toFixed(0)}`
}

onMounted(() => {
  if (props.show) loadInsight()
})
</script>

<style scoped>
.ai-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(13, 17, 23, 0.85);
  backdrop-filter: blur(8px);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.ai-modal {
  background: #161b22;
  border-radius: 12px;
  width: 100%;
  max-width: 600px;
  box-shadow: 0 0 40px rgba(163, 113, 247, 0.15), 0 20px 50px rgba(0, 0, 0, 0.6);
  border: 1px solid #30363d;
  overflow: hidden;
  animation: modal-in 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}

@keyframes modal-in {
  from { opacity: 0; transform: scale(0.9) translateY(20px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}

.ai-modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #0d1117;
  padding: 16px 20px;
  border-bottom: 1px solid #30363d;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 12px;
  font-weight: 800;
  color: #a371f7;
  font-size: 14px;
  letter-spacing: 1px;
}

.ai-icon { font-size: 18px; }

.live-price-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(63, 185, 80, 0.15);
  padding: 4px 10px;
  border-radius: 6px;
  color: #3fb950;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  font-weight: 800;
  border: 1px solid rgba(63, 185, 80, 0.3);
  margin-left: 10px;
}

.price-dot {
  width: 6px;
  height: 6px;
  background: #3fb950;
  border-radius: 50%;
  box-shadow: 0 0 8px #3fb950;
  animation: price-pulse 1s infinite alternate;
}

@keyframes price-pulse {
  from { opacity: 1; transform: scale(1); }
  to { opacity: 0.5; transform: scale(1.2); }
}

.close-btn {
  background: rgba(255, 255, 255, 0.05);
  border: none;
  color: #8b949e;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 20px;
  transition: all 0.2s;
}

.close-btn:hover {
  background: rgba(248, 81, 73, 0.15);
  color: #f85149;
}

.ai-modal-body {
  padding: 24px;
  min-height: 180px;
  display: flex;
  flex-direction: column;
}

.loading-state, .error-state, .empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  color: #8b949e;
  font-size: 14px;
  text-align: center;
}

.pulse-ring {
  width: 40px;
  height: 40px;
  border: 3px solid #a371f7;
  border-radius: 50%;
  animation: ring-pulse 1.5s infinite;
}

@keyframes ring-pulse {
  0% { transform: scale(0.8); opacity: 0.8; }
  50% { transform: scale(1.2); opacity: 0.3; }
  100% { transform: scale(0.8); opacity: 0.8; }
}

.insight-wrap {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.insight-label {
  font-size: 10px;
  font-weight: 800;
  color: #8b949e;
  letter-spacing: 2px;
}

.insight-text {
  color: #e6edf3;
  font-size: 14px;
  line-height: 1.6;
  font-family: 'Inter', sans-serif;
  max-height: 150px;
  overflow-y: auto;
  padding-right: 8px;
}

.modal-chart {
  width: 100%;
  height: 120px;
  margin-bottom: 8px;
}

.insight-meta {
  display: flex;
  gap: 20px;
  margin-top: 10px;
  padding-top: 16px;
  border-top: 1px solid #21262d;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.m-label {
  font-size: 9px;
  font-weight: 800;
  color: #8b949e;
}

.m-value {
  font-weight: 700;
  font-size: 12px;
  color: #e6edf3;
}

.m-value.up, .m-value.bullish { color: #3fb950; }
.m-value.down, .m-value.bearish { color: #f85149; }

/* Session Performance Style */
.session-perf-row {
  display: flex;
  gap: 30px;
  background: rgba(0, 0, 0, 0.2);
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid rgba(48, 54, 61, 0.5);
  margin-bottom: 16px;
}

/* Market Context Grid */
.market-context-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 24px;
  padding: 16px;
  background: rgba(163, 113, 247, 0.05);
  border-radius: 10px;
  border: 1px solid rgba(163, 113, 247, 0.1);
}

.orderbook-viz {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}

.depth-bar {
  height: 8px;
  display: flex;
  border-radius: 4px;
  overflow: hidden;
  background: #21262d;
}

.depth-bid { background: #3fb950; transition: width 0.5s ease; }
.depth-ask { background: #f85149; transition: width 0.5s ease; }

.depth-labels {
  display: flex;
  justify-content: space-between;
  font-size: 9px;
  font-weight: 800;
}

.d-bid { color: #3fb950; }
.d-ask { color: #f85149; }

.metrics-mini-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-top: 8px;
}

.m-metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.mm-label {
  font-size: 8px;
  color: #8b949e;
  font-weight: 700;
}

.mm-value {
  font-size: 11px;
  font-weight: 800;
  color: #e6edf3;
  font-family: 'JetBrains Mono', monospace;
}

.highlight-purple {
  color: #a371f7 !important;
  text-shadow: 0 0 10px rgba(163, 113, 247, 0.5);
}
.perf-metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.pm-label {
  font-size: 8px;
  font-weight: 800;
  color: #8b949e;
  letter-spacing: 1px;
}
.pm-value {
  font-size: 14px;
  font-weight: 800;
  font-family: 'JetBrains Mono', monospace;
}
.pm-value.up { color: #3fb950; }
.pm-value.down { color: #f85149; }
.pm-value small { font-size: 9px; color: #8b949e; }

.ai-modal-footer {
  padding: 16px 20px;
  background: #0d1117;
  border-top: 1px solid #30363d;
  display: flex;
  justify-content: flex-end;
}

.action-btn {
  background: linear-gradient(135deg, #a371f7, #7928ca);
  color: #fff;
  border: none;
  padding: 8px 20px;
  border-radius: 6px;
  font-weight: 800;
  font-size: 11px;
  letter-spacing: 1px;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(163, 113, 247, 0.3);
  transition: all 0.2s;
}

.action-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(163, 113, 247, 0.4);
}
</style>
