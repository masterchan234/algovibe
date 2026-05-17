<template>
  <div class="chart-panel">
    <!-- Pair selector -->
    <div class="chart-toolbar">
      <div class="pair-tabs">
        <button
          v-for="pos in heldPositions"
          :key="pos.pair"
          :class="['pair-btn', { active: selectedPair === pos.pair }]"
          @click="selectPair(pos.pair)"
        >
          {{ pairLabel(pos.pair) }}
          <span class="pair-status" :class="(pos.status || '').toLowerCase()">{{ pos.status }}</span>
        </button>
      </div>
      <div class="tf-tabs">
        <button v-for="t in timeframes" :key="t.value"
          :class="['tf-btn', { active: selectedTf === t.value }]"
          @click="selectTf(t.value)">{{ t.label }}</button>
      </div>
    </div>

    <!-- Loading / Error -->
    <div v-if="loading" class="chart-state">⏳ Loading chart data…</div>
    <div v-else-if="error" class="chart-state error">⚠️ {{ error }}</div>

    <!-- ECharts candlestick -->
    <div v-else-if="candles.length" class="chart-wrap">
      <v-chart
        class="echart"
        :option="chartOption"
        :update-options="{ notMerge: false, lazyUpdate: true }"
        autoresize
      />
    </div>

    <!-- Indicators panel -->
    <div v-if="indicators && Object.keys(indicators).length && !loading" class="indicators-grid">
      <div v-for="(val, key) in displayIndicators" :key="key" class="ind-cell">
        <span class="ind-key">{{ key }}</span>
        <span class="ind-val" :class="signalClass(key, val)">{{ formatVal(key, val) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { CandlestickChart, LineChart, BarChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, DataZoomComponent,
  LegendComponent, MarkLineComponent
} from 'echarts/components'
import VChart from 'vue-echarts'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'

use([CanvasRenderer, CandlestickChart, LineChart, BarChart,
     GridComponent, TooltipComponent, DataZoomComponent,
     LegendComponent, MarkLineComponent])

const store = useTradeStore()
const { getAuthHeaders } = useAuth()

const props = defineProps({
  forcePair: { type: String, default: '' }
})

const backendUrl = window.BACKEND_URL || ''

const timeframes = [
  { label: '1m',  value: '1' },
  { label: '15m', value: '15' },
  { label: '30m', value: '30' },
  { label: '1h',  value: '60' },
  { label: '4h',  value: '240' },
]

const selectedPair = ref('')
const selectedTf   = ref('15')
const candles      = ref([])
const indicators   = ref({})
const loading      = ref(false)
const error        = ref('')

const heldPositions = computed(() =>
  (store.positions || []).filter(p => p.status === 'OPEN' || p.status === 'DUST')
)

function pairLabel(pair) {
  return (pair || '').replace('idr', '').toUpperCase()
}

function selectPair(pair) {
  selectedPair.value = pair
}
function selectTf(tf) {
  selectedTf.value = tf
}

async function fetchChart() {
  if (!selectedPair.value) return
  // Hanya tampilkan loading spinner saat pertama kali (belum ada data)
  const firstLoad = candles.value.length === 0
  if (firstLoad) loading.value = true
  error.value = ''
  try {
    const res = await fetch(
      `${backendUrl}/api/chart/${selectedPair.value}?tf=${selectedTf.value}&limit=100`,
      { headers: getAuthHeaders() }
    )
    const data = await res.json()
    if (!data.success) throw new Error(data.message || 'Failed to load chart')
    candles.value    = data.candles || []
    indicators.value = data.indicators || {}
  } catch (e) {
    error.value = e.message
    if (candles.value.length === 0) candles.value = []
  } finally {
    loading.value = false
  }
}

// Auto-select first held pair on mount / when positions change
watch(heldPositions, (positions) => {
  if (!selectedPair.value && positions.length) {
    selectedPair.value = positions[0].pair
  }
}, { immediate: true })

// forcePair from parent overrides selection
watch(() => props.forcePair, (val) => {
  if (val) selectedPair.value = val
}, { immediate: true })

watch([selectedPair, selectedTf], fetchChart)

onMounted(() => {
  if (selectedPair.value) fetchChart()
  // Auto-refresh every 15s
  setInterval(fetchChart, 15000)
})

// ── Chart option ──────────────────────────────────────────────────────────────
const chartOption = computed(() => {
  if (!candles.value.length) return {}

  const times  = candles.value.map(c => {
    const d = new Date(c.time * 1000)
    return `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
  })
  const ohlc   = candles.value.map(c => [c.open, c.close, c.low, c.high])
  const vols   = candles.value.map(c => c.volume)

  // EMA 9 & 21
  function ema(data, period) {
    const k = 2 / (period + 1)
    const result = []
    let prev = null
    for (const v of data) {
      if (prev === null) { result.push(+v.toFixed(4)); prev = v }
      else { const e = v * k + prev * (1 - k); result.push(+e.toFixed(4)); prev = e }
    }
    return result
  }
  const closes = candles.value.map(c => c.close)
  const ema9  = ema(closes, 9)
  const ema21 = ema(closes, 21)

  return {
    backgroundColor: '#0d1117',
    animation: false,
    animationDuration: 0,
    animationDurationUpdate: 0,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: '#161b22',
      borderColor: '#30363d',
      textStyle: { color: '#e6edf3', fontSize: 11 },
    },
    legend: {
      data: ['OHLC', 'EMA 9', 'EMA 21'],
      textStyle: { color: '#8b949e', fontSize: 10 },
      top: 4,
    },
    grid: [
      { left: 60, right: 12, top: 36, bottom: 90 },
      { left: 60, right: 12, height: 40, bottom: 44 },
    ],
    xAxis: [
      { type: 'category', data: times, axisLabel: { color: '#8b949e', fontSize: 9 }, axisLine: { lineStyle: { color: '#30363d' } }, gridIndex: 0 },
      { type: 'category', data: times, axisLabel: { show: false }, gridIndex: 1 },
    ],
    yAxis: [
      { type: 'value', scale: true, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e', fontSize: 9 }, gridIndex: 0 },
      { type: 'value', splitLine: { show: false }, axisLabel: { show: false }, gridIndex: 1 },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 40, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], bottom: 4, height: 20, borderColor: '#30363d', fillerColor: 'rgba(88,166,255,0.1)', textStyle: { color: '#8b949e', fontSize: 9 } },
    ],
    series: [
      {
        name: 'OHLC',
        type: 'candlestick',
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: '#3fb950',
          color0: '#f85149',
          borderColor: '#3fb950',
          borderColor0: '#f85149',
        },
      },
      {
        name: 'EMA 9',
        type: 'line',
        data: ema9,
        xAxisIndex: 0,
        yAxisIndex: 0,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#58a6ff', width: 1 },
      },
      {
        name: 'EMA 21',
        type: 'line',
        data: ema21,
        xAxisIndex: 0,
        yAxisIndex: 0,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#a371f7', width: 1 },
      },
      {
        name: 'Volume',
        type: 'bar',
        data: vols,
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: {
          color: (params) => {
            const c = candles.value[params.dataIndex]
            return c.close >= c.open ? 'rgba(63,185,80,0.5)' : 'rgba(248,81,73,0.5)'
          },
        },
      },
    ],
  }
})

// ── Indicator display ─────────────────────────────────────────────────────────
const INDICATOR_LABELS = {
  rsi: 'RSI-14',
  macd_hist: 'MACD Hist',
  bb_pct_b: 'BB %B',
  stoch_rsi_k: 'StochRSI K',
  stoch_rsi_d: 'StochRSI D',
  ema_signal: 'EMA Cross',
  williams_r: 'Williams %R',
  slope: 'Slope',
  roc: 'ROC-10',
  volume_ratio: 'Vol Ratio',
  obv_slope: 'OBV Slope',
  signal: 'Signal',
  buy_score: 'Buy Score',
  sell_score: 'Sell Score',
}

const displayIndicators = computed(() => {
  const out = {}
  for (const [k, v] of Object.entries(indicators.value || {})) {
    const label = INDICATOR_LABELS[k] || k
    out[label] = v
  }
  return out
})

function formatVal(key, val) {
  if (val === null || val === undefined) return '–'
  if (typeof val === 'boolean') return val ? 'YES' : 'NO'
  if (typeof val === 'string') return val
  return Number(val).toFixed(2)
}

function signalClass(key, val) {
  if (key === 'Signal') {
    if (String(val).toUpperCase() === 'BUY') return 'green'
    if (String(val).toUpperCase() === 'SELL') return 'red'
    return 'neutral'
  }
  if (key === 'RSI-14') {
    if (val < 30) return 'green'
    if (val > 70) return 'red'
    return 'neutral'
  }
  if (key === 'Buy Score') return val >= 6 ? 'green' : 'neutral'
  if (key === 'Sell Score') return val >= 6 ? 'red' : 'neutral'
  return 'neutral'
}
</script>

<style scoped>
.chart-panel { display: flex; flex-direction: column; height: 100%; background: #0d1117; overflow: hidden; }

.chart-toolbar { display: flex; flex-direction: column; gap: 0; border-bottom: 1px solid #21262d; }

.pair-tabs { display: flex; gap: 2px; padding: 6px 8px 4px; flex-wrap: wrap; }
.pair-btn {
  background: #161b22; border: 1px solid #30363d; color: #8b949e;
  padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: 700;
  cursor: pointer; display: flex; align-items: center; gap: 4px;
}
.pair-btn.active { background: #1f2937; border-color: #58a6ff; color: #e6edf3; }
.pair-status { font-size: 8px; padding: 1px 4px; border-radius: 3px; font-weight: 800; }
.pair-status.open  { background: rgba(63,185,80,0.15); color: #3fb950; }
.pair-status.dust  { background: rgba(139,148,158,0.15); color: #8b949e; }

.tf-tabs { display: flex; gap: 2px; padding: 4px 8px 6px; }
.tf-btn {
  background: transparent; border: 1px solid transparent; color: #8b949e;
  padding: 2px 8px; border-radius: 3px; font-size: 10px; font-weight: 700; cursor: pointer;
}
.tf-btn.active { border-color: #30363d; color: #58a6ff; background: #161b22; }

.chart-state { flex: 1; display: flex; align-items: center; justify-content: center; color: #8b949e; font-size: 12px; }
.chart-state.error { color: #f85149; }

.chart-wrap { flex: 1; min-height: 0; }
.echart { width: 100%; height: 100%; min-height: 280px; }

.indicators-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(90px, 1fr));
  gap: 1px;
  background: #21262d;
  border-top: 1px solid #21262d;
  flex-shrink: 0;
}
.ind-cell {
  background: #0d1117;
  padding: 5px 8px;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.ind-key { font-size: 9px; color: #6e7681; font-weight: 600; text-transform: uppercase; }
.ind-val { font-size: 11px; font-weight: 800; color: #e6edf3; }
.ind-val.green  { color: #3fb950; }
.ind-val.red    { color: #f85149; }
.ind-val.neutral { color: #e6edf3; }
</style>
