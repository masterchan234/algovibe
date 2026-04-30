<template>
  <div class="chart-panel">
    <!-- KPI Bar -->
    <div class="kpi-bar">
      <div class="kpi-item">
        <div class="kpi-label">PnL Hari Ini</div>
        <div class="kpi-value" :class="stats.daily_pnl >= 0 ? 'positive' : 'negative'">
          {{ formatIdr(stats.daily_pnl) }}
        </div>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Unrealized</div>
        <div class="kpi-value" :class="stats.unrealized_pnl >= 0 ? 'positive' : 'negative'">
          {{ formatIdr(stats.unrealized_pnl) }}
        </div>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Win Rate</div>
        <div class="kpi-value">{{ stats.win_rate.toFixed(1) }}%</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Posisi</div>
        <div class="kpi-value">{{ stats.active_positions }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Regime</div>
        <div class="kpi-value regime-badge" :class="regimeClass">{{ stats.regime }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-label">Sentiment</div>
        <div class="sentiment-bar">
          <div class="sentiment-fill" :style="{ width: stats.market_sentiment_index + '%' }" :class="sentimentClass"></div>
          <span class="sentiment-num">{{ stats.market_sentiment_index }}</span>
        </div>
      </div>
    </div>

    <!-- ECharts profit line -->
    <div class="chart-wrap">
      <div class="chart-header-actions">
        <div class="tf-selector">
          <button :class="{ active: timeframe === 'live' }" @click="setTimeframe('live')">LIVE</button>
          <button :class="{ active: timeframe === '1h' }" @click="setTimeframe('1h')">1H</button>
          <button :class="{ active: timeframe === '24h' }" @click="setTimeframe('24h')">24H</button>
        </div>
      </div>
      <div class="chart-canvas" ref="chartContainer"></div>
      <div v-if="timeframe === 'live' && store.pnlHistory.length === 0" class="chart-empty">
        <div class="loading-dots">
          <span></span><span></span><span></span>
        </div>
        <span>Menunggu data trading pertama…</span>
      </div>
      <div v-if="loadingHistory" class="chart-empty">
        <div class="loading-dots">
          <span></span><span></span><span></span>
        </div>
        <span>Fetching equity history…</span>
      </div>
    </div>

    <!-- Intelligence Analysis Panel -->
    <div class="intelligence-panel" v-if="selectedPairData || stats.active_reasoning">
      <div class="intel-header">
        <div class="intel-title">
          <span class="intel-icon">🔮</span>
          <span>ORACLE INTELLIGENCE: {{ selectedPairData?.pair || stats.regime_leader || 'MARKET' }}</span>
        </div>
        <div class="intel-meta" v-if="selectedPairData">
          <div class="intel-chip" :class="trendClass(selectedPairData.trend)">
            {{ selectedPairData.trend }}
          </div>
          <div class="intel-chip">
            PROB: {{ (selectedPairData.prediction * 100).toFixed(1) }}%
          </div>
        </div>
      </div>
      <div class="intel-body">
        <div class="intel-reasoning" v-if="stats.active_reasoning">
          {{ stats.active_reasoning }}
        </div>
        <div class="intel-loading" v-else>
          <div class="intel-dots"><span></span><span></span><span></span></div>
          Decrypting neural vectors...
        </div>
      </div>
    </div>

    <!-- Top Radar -->
    <div class="radar-section">
      <div class="section-label">Signal Radar</div>
      <div class="radar-list">
        <div
          v-for="pair in store.radarPairs.slice(0, 8)"
          :key="pair.pair"
          class="radar-item"
          @click="onRadarClick(pair)"
        >
          <div class="radar-top">
            <span class="radar-pair">{{ pair.pair }}</span>
            <span class="radar-score">{{ (pair.score || 0).toFixed(1) }}</span>
          </div>
          <div class="radar-bottom">
            <span class="radar-trend" :class="trendClass(pair.trend)">{{ pair.trend }}</span>
            <span class="radar-prob" v-if="pair.prediction != null">
              {{ (pair.prediction * 100).toFixed(0) }}%
            </span>
          </div>
        </div>
      </div>
    </div>

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
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, TitleComponent, MarkLineComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import AiAnalysisModal from './AiAnalysisModal.vue'

echarts.use([LineChart, GridComponent, TooltipComponent, TitleComponent, MarkLineComponent, CanvasRenderer])

const store = useTradeStore()
const stats = store.stats
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

  try {
    const res = await fetch(`${backendUrl}/api/pnl-history/${tf}`)
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

function onRadarClick(pair) {
  selectedPair.value = pair.pair
  selectedPairData.value = pair
  
  // Update CURRENT_WATCH_PAIR in backend
  store.socket.emit('watch_pair', { pair: pair.pair })
  
  // Optional: Scroll to intelligence panel
  document.querySelector('.intelligence-panel')?.scrollIntoView({ behavior: 'smooth' })
}

function closeAiModal() {
  showAiModal.value = false
  selectedPair.value = null
}

async function fetchAiInsight(pair) {
  if (pair && pair.insight) return pair.insight
  if (stats.regime_leader && pair && pair.pair && pair.pair.toUpperCase() === stats.regime_leader.toUpperCase()) {
    return stats.latest_llm_insight || 'Tidak ada insight AI.'
  }
  return 'Menganalisa data market terbaru untuk ' + pair.pair + '...'
}

const regimeClass = computed(() => {
  const r = stats.regime
  if (r === 'BULL') return 'regime-bull'
  if (r === 'BEAR') return 'regime-bear'
  return 'regime-choppy'
})

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

function formatIdr(val) {
  const n = Number(val) || 0
  const abs = Math.abs(n)
  const prefix = n >= 0 ? '+' : '-'
  if (abs >= 1_000_000) return `${prefix}Rp ${(abs / 1_000_000).toFixed(2)}jt`
  if (abs >= 1_000) return `${prefix}Rp ${(abs / 1_000).toFixed(1)}k`
  return `${prefix}Rp ${abs.toFixed(0)}`
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
        return `<div style="font-size:10px;color:#8b949e">${p.axisValue}</div>
                <div style="font-size:10px;color:#8b949e">${label}</div>
                <div style="font-weight:700;color:${lineColor}">${prefix}Rp ${n.toLocaleString('id-ID')}</div>`
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
          if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'jt'
          if (Math.abs(v) >= 1_000) return (v / 1_000).toFixed(0) + 'k'
          return v
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

onMounted(() => {
  if (!chartContainer.value) return
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
}

.kpi-item {
  flex: 1;
  padding: 12px 14px;
  border-right: 1px solid #21262d;
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

/* Chart */
.chart-wrap {
  flex: 1;
  min-height: 200px;
  position: relative;
  background: radial-gradient(circle at 50% 50%, rgba(88, 166, 255, 0.03) 0%, transparent 70%);
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

.chart-canvas {
  position: absolute;
  inset: 0;
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

/* Intelligence Panel */
.intelligence-panel {
  padding: 16px 20px;
  background: rgba(163, 113, 247, 0.04);
  border-top: 1px solid rgba(163, 113, 247, 0.15);
  display: flex;
  flex-direction: column;
  gap: 12px;
  flex-shrink: 0;
}

.intel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
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

.intel-reasoning {
  color: #e6edf3;
  font-size: 13.5px;
  line-height: 1.6;
  font-family: 'Inter', sans-serif;
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
  flex-shrink: 0; 
  border-top: 1px solid #30363d;
  background: #0d1117;
  padding-bottom: 8px;
}
.section-label {
  padding: 10px 16px 6px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: #8b949e;
}

.radar-list {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  padding: 4px 14px 12px;
}

.radar-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 8px 10px;
  transition: all 0.2s ease;
  cursor: pointer;
}

.radar-item:hover {
  background: #21262d;
  border-color: #58a6ff;
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(88, 166, 255, 0.1);
}

.radar-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.radar-pair { font-weight: 800; color: #58a6ff; font-size: 12px; }
.radar-score { color: #8b949e; font-size: 10px; font-weight: 600; }

.radar-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.radar-trend { font-size: 9px; font-weight: 700; text-transform: uppercase; }
.trend-up   { color: #3fb950; }
.trend-down { color: #f85149; }
.trend-neutral { color: #8b949e; }
.radar-prob { 
  color: #a371f7; 
  font-size: 10px; 
  font-weight: 800;
  background: rgba(163, 113, 247, 0.1);
  padding: 1px 4px;
  border-radius: 3px;
}

@media (max-width: 1200px) {
  .radar-list { grid-template-columns: repeat(2, 1fr); }
}
</style>
