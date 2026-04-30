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
      <div class="chart-canvas" ref="chartContainer"></div>
      <div v-if="store.pnlHistory.length === 0" class="chart-empty">
        <span>Menunggu data trading pertama…</span>
      </div>
    </div>

    <!-- LLM Insight -->
    <div class="llm-insight" v-if="stats.latest_llm_insight">
      <span class="ai-badge">AI</span>
      {{ stats.latest_llm_insight }}
    </div>

    <!-- Top Radar -->
    <div class="radar-section">
      <div class="section-label">Top Radar</div>
      <div class="radar-list">
        <div
          v-for="pair in store.radarPairs.slice(0, 8)"
          :key="pair.pair"
          class="radar-item"
        >
          <span class="radar-pair">{{ pair.pair }}</span>
          <span class="radar-score">{{ (pair.score || 0).toFixed(2) }}</span>
          <span class="radar-trend" :class="trendClass(pair.trend)">{{ pair.trend }}</span>
          <span class="radar-prob" v-if="pair.prediction_prob != null">
            AI:{{ (pair.prediction_prob * 100).toFixed(0) }}%
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useTradeStore } from '../stores/trade'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  TitleComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([LineChart, GridComponent, TooltipComponent, TitleComponent, CanvasRenderer])

const store = useTradeStore()
const stats = store.stats
const chartContainer = ref(null)
let chart = null
let resizeObserver = null
let rafId = null

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
  const history = store.pnlHistory
  const times = history.map(h => h.time)
  const values = history.map(h => h.pnl)
  const lastVal = values[values.length - 1] || 0
  const lineColor = lastVal >= 0 ? '#3fb950' : '#f85149'

  return {
    backgroundColor: 'transparent',
    grid: { top: 20, right: 20, bottom: 30, left: 70 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1c2128',
      borderColor: '#30363d',
      textStyle: { color: '#e6edf3', fontSize: 12 },
      formatter: (params) => {
        const p = params[0]
        const n = Number(p.value)
        const prefix = n >= 0 ? '+' : ''
        return `${p.axisValue}<br/>Equity: <b>${prefix}Rp ${n.toLocaleString('id-ID')}</b>`
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
      axisLine: { lineStyle: { color: '#30363d' } },
      splitLine: { lineStyle: { color: '#21262d' } },
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
        type: 'line',
        data: values,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: lineColor, width: 2 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: lineColor + '33' },
            { offset: 1, color: lineColor + '00' },
          ]),
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#8b949e', type: 'dashed', width: 1 },
          data: [{ yAxis: 0 }],
        },
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
  border-radius: 8px;
  overflow: hidden;
}

/* KPI Bar */
.kpi-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid #21262d;
  background: #0d1117;
  flex-shrink: 0;
}

.kpi-item {
  flex: 1;
  padding: 10px 12px;
  border-right: 1px solid #21262d;
}
.kpi-item:last-child { border-right: none; }

.kpi-label {
  font-size: 10px;
  color: #8b949e;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 3px;
}

.kpi-value {
  font-size: 14px;
  font-weight: 700;
  color: #e6edf3;
  font-variant-numeric: tabular-nums;
}
.kpi-value.positive { color: #3fb950; }
.kpi-value.negative { color: #f85149; }

.regime-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  display: inline-block;
}
.regime-bull  { background: rgba(63, 185, 80, 0.15); color: #3fb950; }
.regime-bear  { background: rgba(248, 81, 73, 0.15); color: #f85149; }
.regime-choppy { background: rgba(210, 153, 34, 0.15); color: #d29922; }

.sentiment-bar {
  position: relative;
  background: #21262d;
  border-radius: 3px;
  height: 18px;
  overflow: hidden;
  margin-top: 2px;
}
.sentiment-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.5s ease;
}
.sentiment-bull    { background: rgba(63, 185, 80, 0.5); }
.sentiment-bear    { background: rgba(248, 81, 73, 0.5); }
.sentiment-neutral { background: rgba(210, 153, 34, 0.4); }

.sentiment-num {
  position: absolute;
  right: 5px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 10px;
  font-weight: 700;
  color: #e6edf3;
}

/* Chart */
.chart-wrap {
  flex: 1;
  min-height: 0;
  position: relative;
}
.chart-canvas {
  position: absolute;
  inset: 0;
}
.chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #8b949e;
  font-size: 12px;
  pointer-events: none;
}

/* LLM Insight */
.llm-insight {
  padding: 8px 14px;
  background: rgba(163, 113, 247, 0.06);
  border-top: 1px solid rgba(163, 113, 247, 0.2);
  font-size: 11.5px;
  color: #c9d1d9;
  line-height: 1.5;
  display: flex;
  gap: 8px;
  align-items: flex-start;
  flex-shrink: 0;
}
.ai-badge {
  font-size: 10px;
  font-weight: 700;
  background: rgba(163, 113, 247, 0.2);
  color: #a371f7;
  padding: 1px 5px;
  border-radius: 3px;
  flex-shrink: 0;
  line-height: 1.8;
}

/* Radar */
.radar-section { flex-shrink: 0; border-top: 1px solid #21262d; }
.section-label {
  padding: 5px 14px 3px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: #8b949e;
}

.radar-list {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  padding: 4px 10px 8px;
}

.radar-item {
  display: flex;
  gap: 5px;
  align-items: center;
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 11px;
}

.radar-pair { font-weight: 600; color: #58a6ff; }
.radar-score { color: #8b949e; }
.trend-up   { color: #3fb950; }
.trend-down { color: #f85149; }
.trend-neutral { color: #8b949e; }
.radar-prob { color: #a371f7; font-size: 10px; }
</style>
