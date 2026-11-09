<template>
  <div v-if="show" class="ai-modal-overlay" @click.self="close">
    <div class="ai-modal">
      <div class="ai-modal-header">
        <div class="header-title">
          <span class="ai-icon">🔮</span>
          <span>SYSTEM INTELLIGENCE: {{ formatPair(pair?.pair) }}</span>
          <span class="live-price-badge" v-if="livePrice">
            <span class="price-dot"></span>
            {{ isUsdt ? '$' : 'Rp ' }}{{ livePrice.toLocaleString(isUsdt ? 'en-US' : 'id-ID', { minimumFractionDigits: isUsdt ? 2 : 0, maximumFractionDigits: isUsdt ? 4 : 0 }) }}
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
                    <span class="mm-value">{{ formattedVol24h }}</span>
                  </div>
                </div>
              </div>
            </div>

            <div v-show="insight" class="insight-wrap">
              <div class="insight-label">ANALYSIS SUMMARY</div>
              <div class="insight-text">{{ insight }}</div>

              <div class="insight-label" style="display:flex; justify-content:space-between; align-items:center; margin-top:16px;">
                <span>MARKET VISUALIZATION</span>
                <div class="tf-selector">
                  <button v-for="t in ['1m','5m','15m','1h','4h','24h']" :key="t" 
                          class="tf-btn" :class="{ active: selectedTf === t }"
                          @click="changeTf(t)">{{ t }}</button>
                </div>
              </div>
              <div ref="chartRef" class="modal-chart"></div>

              <div class="insight-label" style="margin-top: 16px;">TECHNICAL INDICATORS</div>
              <div class="tech-grid" v-if="filteredTechnicals">
                <div class="tech-item" v-for="(val, key) in filteredTechnicals" :key="key">
                  <span class="tech-key">{{ formatTechKey(key) }}</span>
                  <span class="tech-val" :class="getTechColor(key, val)">{{ formatTechVal(val) }}</span>
                </div>
              </div>

              <!-- Session Performance Bridge -->
              <div class="insight-label" v-if="pair?.pnl != null">SESSION PERFORMANCE AUDIT</div>
              <div class="session-perf-row" v-if="pair?.pnl != null">
                <div class="perf-metric">
                  <span class="pm-label">CONTRIBUTION</span>
                  <span class="pm-value" :class="pair.pnl >= 0 ? 'up' : 'down'">
                    {{ formatIdr(pair.pnl) }}
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
                    <span class="m-value">{{ dynamicProbability }}</span>
                  </div>
                  <div class="meta-item">
                    <span class="m-label">TREND</span>
                    <span class="m-value" :class="dynamicTrend.toLowerCase()">{{ dynamicTrend }}</span>
                  </div>
                  <div class="meta-item">
                    <span class="m-label">AI VERDICT</span>
                    <span class="m-value highlight-purple">{{ dynamicVerdict }}</span>
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
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
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

const isUsdt = computed(() => store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000))

const livePrice = computed(() => {
  if (!props.pair?.pair) return null
  const match = store.radarPairs.find(p => p.pair === props.pair.pair)
  return match ? match.price : (context.value?.stats?.last || null)
})

const insight = ref('')
const loading = ref(false)
const chartLoading = ref(false)
const error = ref(false)
const chartRef = ref(null)
const context = ref(null)
const selectedTf = ref('1h')
let chartInstance = null
let pollInterval = null

  const dynamicTrend = computed(() => {
    if (selectedTf.value === '1m' && props.pair?.trend) return props.pair.trend;
    if (!context.value?.technicals?.ema_cross) return 'NEUTRAL';
    const cross = context.value.technicals.ema_cross;
    if (cross === 'BULLISH') return 'UP';
    if (cross === 'BEARISH') return 'DOWN';
    return 'SIDEWAYS';
  })

  const dynamicVerdict = computed(() => {
    if (selectedTf.value === '1m' && props.pair?.radar_reco) return props.pair.radar_reco.toUpperCase();
    if (!context.value?.technicals?.signal) return 'WAIT';
    return context.value.technicals.signal.toUpperCase();
  })

  const dynamicProbability = computed(() => {
    if (selectedTf.value === '1m' && props.pair?.prediction !== undefined) {
      return ((props.pair.prediction || 0) * 100).toFixed(1) + '%';
    }
    // Simple heuristic probability based on RSI and MACD for non-1m timeframes
    if (!context.value?.technicals) return 'N/A';
    const tech = context.value.technicals;
    let prob = 50;
    if (tech.rsi) {
      prob += (tech.rsi - 50) * 0.5;
    }
    if (tech.macd_hist > 0) prob += 10;
    else if (tech.macd_hist < 0) prob -= 10;
    
    if (prob > 99) prob = 99;
    if (prob < 1) prob = 1;
    return prob.toFixed(1) + '%';
  })

const filteredTechnicals = computed(() => {
  if (!context.value?.technicals) return null
  const t = context.value.technicals
  const skip = ['buy_pressure', 'sell_pressure', 'velocity_1m', 'spread', 'volatility', 'velocity']
  let res = {}
  for (let k in t) {
    if (!skip.includes(k) && typeof t[k] !== 'object') {
      res[k] = t[k]
    }
  }
  return res
})

function formatTechKey(k) {
  return k.replace(/_/g, ' ').toUpperCase()
}

function formatTechVal(v) {
  if (typeof v === 'number') {
    if (v === 0) return "0"
    if (Math.abs(v) < 0.001) return v.toExponential(2)
    if (v % 1 !== 0) return v.toFixed(4)
  }
  return v
}

function getTechColor(k, v) {
  if (k.includes('rsi')) {
    if (v > 70) return 'text-red'
    if (v < 30) return 'text-green'
  }
  if (k.includes('macd_hist')) {
    return v > 0 ? 'text-green' : 'text-red'
  }
  return ''
}

const formattedVol24h = computed(() => {
  const vol = context.value?.stats?.vol_24h || 0
  if (isUsdt.value) {
    if (vol >= 1_000_000) return `$${(vol / 1_000_000).toFixed(1)}M`
    if (vol >= 1_000) return `$${(vol / 1_000).toFixed(1)}K`
    return `$${vol.toFixed(2)}`
  } else {
    if (vol >= 1_000_000_000) return `Rp ${(vol / 1_000_000_000).toFixed(1)}B`
    if (vol >= 1_000_000) return `Rp ${(vol / 1_000_000).toFixed(1)}M`
    return `Rp ${vol.toLocaleString('id-ID')}`
  }
})

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
  
  if (props.pair.ui_tf) {
    selectedTf.value = props.pair.ui_tf
  }
  
  loading.value = true
  error.value = false
  insight.value = ''
  context.value = null
  
  try {
    const [insightRes, chartDataRes, contextDataRes] = await Promise.allSettled([
      props.fetchInsight(props.pair, selectedTf.value),
      fetchChartData(props.pair.pair, selectedTf.value),
      fetchMarketContext(props.pair.pair, selectedTf.value)
    ])

    const resolvedInsight = insightRes.status === 'fulfilled' ? insightRes.value : ''
    const chartData = chartDataRes.status === 'fulfilled' ? chartDataRes.value : []
    const contextData = contextDataRes.status === 'fulfilled' ? contextDataRes.value : null

    insight.value = resolvedInsight || 'No AI intelligence vectors available for this sector currently.'
    context.value = contextData
    loading.value = false

    await nextTick()
    if (Array.isArray(chartData) && chartData.length) {
      initChart(chartData)
    }

    if (!insight.value && !context.value && (!Array.isArray(chartData) || chartData.length === 0)) {
      error.value = true
    }
  } catch (err) {
    console.error('Modal Load Error:', err)
    error.value = true
    loading.value = false
  }
}

async function fetchMarketContext(pair, tf = '1m') {
  if (!pair) return null
  const backendUrl = import.meta.env.DEV
    ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
    : window.location.origin
  const res = await fetch(`${backendUrl}/api/market-context/${pair.toLowerCase()}?tf=${tf}`, {
    headers: getAuthHeaders(),
    credentials: 'include',
  })
  if (res.ok) return await res.json()
  return null
}

async function changeTf(tf) {
    if (selectedTf.value === tf) return
    selectedTf.value = tf
    chartLoading.value = true
    try {
      const [data, contextData, newInsight] = await Promise.all([
        fetchChartData(props.pair.pair, tf),
        fetchMarketContext(props.pair.pair, tf),
        props.fetchInsight(props.pair, tf)
      ])
      
      if (Array.isArray(data) && data.length) {
        initChart(data)
      }
      if (contextData) {
        context.value = contextData
      }
      if (newInsight) {
        insight.value = newInsight
      }
    } catch(e) {
      console.error(e)
    }
    chartLoading.value = false
  }

async function fetchChartData(pair, tf = '1h') {
  if (!pair) return []
  const backendUrl = import.meta.env.DEV
    ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
    : window.location.origin
  const res = await fetch(`${backendUrl}/api/ohlcv/${pair.toLowerCase().replace('/', '')}/${tf}`, {
    headers: getAuthHeaders(),
    credentials: 'include',
  })
  if (res.ok) return await res.json()
  return []
}

function calculateMA(dayCount, data) {
  var result = [];
  for (var i = 0, len = data.length; i < len; i++) {
    if (i < dayCount) {
      result.push('-');
      continue;
    }
    var sum = 0;
    for (var j = 0; j < dayCount; j++) {
      sum += data[i - j][1];
    }
    result.push(sum / dayCount);
  }
  return result;
}

function initChart(data) {
  if (!chartRef.value || !data || !data.length) {
    console.warn('No chart data or ref available');
    return
  }
  
  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value)
  }

  const times = data.map(item => {
    const d = new Date(item[0])
    if (['4h', '24h', '1d'].includes(selectedTf.value)) {
      return d.toLocaleDateString('id-ID', { day: 'numeric', month: 'short' }) + ' ' + d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })
    }
    return d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })
  })
  const candles = data.map(item => [item[1], item[4], item[3], item[2]])

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: '#1c2128',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9', fontSize: 10 },
    },
    legend: {
      data: ['Kline', 'MA20', 'MA50'],
      textStyle: { color: '#8b949e' },
      top: 0
    },
    grid: { left: '2%', right: '2%', top: '12%', bottom: '15%', containLabel: true },
    xAxis: {
      type: 'category',
      data: times,
      boundaryGap: false,
      axisLine: { onZero: false, lineStyle: { color: '#30363d' } },
      splitLine: { show: false },
      min: 'dataMin',
      max: 'dataMax',
      axisLabel: { show: true, color: '#8b949e', fontSize: 9 }
    },
    yAxis: {
      scale: true,
      position: 'right',
      splitArea: { show: false },
      splitLine: { lineStyle: { color: '#30363d', type: 'dashed' } },
      axisLabel: { show: true, color: '#8b949e', fontSize: 9, formatter: (val) => val.toLocaleString('en-US') }
    },
    dataZoom: [
      { type: 'inside', start: 30, end: 100 },
      { show: true, type: 'slider', bottom: 0, height: 16 }
    ],
    series: [
      {
        name: 'Kline',
        type: 'candlestick',
        data: candles,
        itemStyle: {
          color: '#3fb950',
          color0: '#f85149',
          borderColor: '#3fb950',
          borderColor0: '#f85149'
        }
      },
      {
        name: 'MA20',
        type: 'line',
        data: calculateMA(20, candles),
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.5, color: '#a371f7' }
      },
      {
        name: 'MA50',
        type: 'line',
        data: calculateMA(50, candles),
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.5, color: '#f1e05a' }
      }
    ]
  }
  chartInstance.setOption(option)
  
  setTimeout(() => {
    chartInstance.resize()
  }, 100)
}

function startPolling() {
  if (pollInterval) clearInterval(pollInterval)
  pollInterval = setInterval(async () => {
    if (!props.show || !props.pair) return
    try {
      const contextData = await fetchMarketContext(props.pair.pair, selectedTf.value)
      if (contextData) {
        context.value = contextData
      }
    } catch(e) {}
  }, 1000)
}

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval)
    pollInterval = null
  }
}

watch(() => props.show, (newVal) => {
  if (newVal) {
    loadInsight()
    startPolling()
  } else {
    stopPolling()
  }
})

function formatIdr(val) {
  const n = Number(val) || 0
  const abs = Math.abs(n)
  const prefix = n >= 0 ? '+' : '-'
  if (isUsdt.value) {
    if (abs >= 1_000_000) return `${prefix}$${(abs / 1_000_000).toFixed(2)}M`
    if (abs >= 1_000) return `${prefix}$${(abs / 1_000).toFixed(1)}k`
    return `${prefix}$${abs.toFixed(2)}`
  } else {
    if (abs >= 1_000_000) return `${prefix}Rp ${(abs / 1_000_000).toFixed(2)}M`
    if (abs >= 1_000) return `${prefix}Rp ${(abs / 1_000).toFixed(1)}k`
    return `${prefix}Rp ${abs.toFixed(0)}`
  }
}

onMounted(() => {
  if (props.show) {
    loadInsight()
    startPolling()
  }
})

onUnmounted(() => {
  stopPolling()
  if (chartInstance) {
    chartInstance.dispose()
    chartInstance = null
  }
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
  background: var(--bg-surface);
  border-radius: 12px;
  width: 100%;
  max-width: 900px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 0 40px rgba(163, 113, 247, 0.15), 0 20px 50px rgba(0, 0, 0, 0.6);
  border: 1px solid var(--border-trader);
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
  background: var(--bg-deep);
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-trader);
  flex-shrink: 0;
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
  color: var(--text-secondary);
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
  flex: 1;
  overflow-y: auto;
}

.loading-state, .error-state, .empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  color: var(--text-secondary);
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
  color: var(--text-secondary);
  letter-spacing: 2px;
}

.insight-text {
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.6;
  font-family: 'Inter', sans-serif;
  max-height: 150px;
  overflow-y: auto;
  padding-right: 8px;
}

.modal-chart {
  width: 100%;
  height: 350px;
  margin-bottom: 8px;
}

.insight-meta {
  display: flex;
  gap: 20px;
  margin-top: 10px;
  padding-top: 16px;
  border-top: 1px solid var(--border-light);
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.m-label {
  font-size: 9px;
  font-weight: 800;
  color: var(--text-secondary);
}

.m-value {
  font-weight: 700;
  font-size: 12px;
  color: var(--text-primary);
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
  padding: 8px 12px;
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
  background: var(--border-light);
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
  color: var(--text-secondary);
  font-weight: 700;
}

.mm-value {
  font-size: 11px;
  font-weight: 800;
  color: var(--text-primary);
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
  color: var(--text-secondary);
  letter-spacing: 1px;
}
.pm-value {
  font-size: 14px;
  font-weight: 800;
  font-family: 'JetBrains Mono', monospace;
}
.pm-value.up { color: #3fb950; }
.pm-value.down { color: #f85149; }
.pm-value small { font-size: 9px; color: var(--text-secondary); }

.ai-modal-footer {
  padding: 16px 20px;
  background: var(--bg-deep);
  border-top: 1px solid var(--border-trader);
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

.text-red { color: #f85149; }
.text-green { color: #3fb950; }

.tech-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 8px;
  margin-top: 8px;
  margin-bottom: 16px;
}
.tech-item {
  background: var(--bg-panel);
  border: 1px solid var(--border-light);
  border-radius: 6px;
  padding: 8px;
  display: flex;
  flex-direction: column;
}
.tech-key {
  font-size: 8px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.tech-val {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-primary);
}

.tf-selector {
  display: flex;
  gap: 4px;
}
.tf-btn {
  background: transparent;
  border: 1px solid var(--border-light);
  color: var(--text-secondary);
  font-size: 9px;
  padding: 2px 6px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.tf-btn:hover {
  background: rgba(163, 113, 247, 0.1);
  color: var(--text-primary);
}
.tf-btn.active {
  background: rgba(163, 113, 247, 0.2);
  border-color: #a371f7;
  color: #fff;
  font-weight: bold;
}
</style>
