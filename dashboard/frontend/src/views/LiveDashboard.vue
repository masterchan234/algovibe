<template>
  <div class="live-dashboard" :class="`tab-${mobileTab}`">
    <main class="main-grid">
      <div class="panels-row" :style="{ '--left-width': leftPanelWidth + 'px' }">
        <section class="left-panel" :class="{ 'mobile-hidden': mobileTab !== 'activity' }">
          <ActivityFeed />
        </section>

        <div class="resizer hide-mobile" @mousedown="startResize" :class="{ 'is-active': isResizing }"></div>

        <section class="right-panel" :class="{ 'mobile-hidden': mobileTab !== 'chart' }">
          <div class="chart-wrap">
            <ProfitChart />
          </div>

          <section class="scorecard-panel entry-intel-panel">
            <div class="scorecard-header entry-intel-header">
              <div>
                <p class="scorecard-kicker">ENTRY INTELLIGENCE</p>
                <h3>Gate Board & Real-Time Coin</h3>
                <p class="scorecard-helper">Gate 1 sampai gate terakhir ditampilkan sebagai box informasi. Setiap box menunjukkan status Pass / Not Pass dan indikator teknikal yang relevan. Coin pair yang sedang dianalisis tampil di bawahnya secara real-time.</p>
              </div>
              <div class="entry-live-card">
                <span class="entry-live-label">Realtime Coin</span>
                <strong class="entry-live-pair">{{ entryGateSnapshot.pair || 'Waiting for active pair' }}</strong>
                <span class="entry-live-meta">{{ entryGateSnapshot.stateText }}</span>
                <span class="entry-live-reason">{{ entryGateSnapshot.reasonText }}</span>
              </div>
            </div>

            <div class="entry-intel-chart">
              <div class="entry-chart-head">
                <div>
                  <p class="entry-chart-kicker">Gate Heatmap</p>
                  <strong class="entry-chart-title">Status intensity for each entry gate</strong>
                </div>
                <span class="entry-chart-score">{{ entryGateChartModel.summary }}</span>
              </div>

              <div class="entry-heatmap-grid">
                <div
                  v-for="cell in entryGateChartModel.cells"
                  :key="`${cell.stage}-${cell.idx}`"
                  class="entry-heatmap-cell"
                  :class="`status-${cell.status}`"
                  :style="{ '--heat-alpha': cell.alpha }"
                  :title="cell.tooltip"
                >
                  <span class="entry-heatmap-stage">G{{ cell.stage }}</span>
                  <span class="entry-heatmap-value">{{ cell.scoreLabel }}</span>
                </div>
              </div>

              <div class="entry-heatmap-legend">
                <span class="legend-item ok">Pass {{ entryGateChartModel.counts.pass }}</span>
                <span class="legend-item warn">Penalty {{ entryGateChartModel.counts.penalty }}</span>
                <span class="legend-item bad">Blocked {{ entryGateChartModel.counts.notPass }}</span>
                <span class="legend-item idle">Idle {{ entryGateChartModel.counts.idle }}</span>
              </div>
            </div>

            <div class="entry-gate-grid">
              <article
                v-for="gate in entryGateCards"
                :key="gate.code"
                class="entry-gate-card"
                :class="[`tone-${gate.tone}`, `status-${gate.status}`]"
              >
                <div class="entry-gate-top">
                  <span class="entry-gate-stage">Gate {{ gate.stage }}</span>
                  <span class="entry-gate-status">{{ gate.statusLabel }}</span>
                </div>
                <h4>{{ gate.title }}</h4>
                <p class="entry-gate-copy">{{ gate.copy }}</p>
                <div class="entry-gate-result">
                  <span class="entry-gate-score" :class="gate.contributionClass">{{ gate.contributionLabel }}</span>
                  <span class="entry-gate-detail">{{ gate.detailText }}</span>
                </div>
                <div class="entry-gate-chip-row">
                  <span v-for="indicator in gate.indicators" :key="indicator" class="entry-gate-chip">{{ indicator }}</span>
                </div>
              </article>
            </div>

            <div class="entry-coin-strip">
              <div class="entry-coin-cell">
                <span>Coin Pair</span>
                <strong>{{ entryGateSnapshot.pair || 'No coin is being analyzed right now' }}</strong>
              </div>
              <div class="entry-coin-cell">
                <span>Action</span>
                <strong :class="entryGateSnapshot.isPass ? 'profit' : (entryGateSnapshot.hasBlocker ? 'loss' : 'gold')">
                  {{ entryGateSnapshot.actionLabel }}
                </strong>
              </div>
              <div class="entry-coin-cell">
                <span>Technical Indicator</span>
                <strong>{{ entryGateIndicatorSummary }}</strong>
              </div>
              <div class="entry-coin-cell">
                <span>Updated</span>
                <strong>{{ entryGateSnapshot.atText }}</strong>
              </div>
            </div>
          </section>
        </section>
      </div>
    </main>

    <nav class="mob-bottom-nav">
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'activity' }" @click="mobileTab = 'activity'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 10h16M4 14h10"/></svg>
        <span>Activity</span>
      </button>
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'chart' }" @click="mobileTab = 'chart'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        <span>Chart</span>
      </button>
    </nav>

    <footer class="ld-footer">
      <div class="footer-stats hide-mobile">
        <span class="stat-item">BROWSER: <span class="v">{{ store.latency || 0 }}ms</span></span>
        <span class="stat-item">EXCHANGE: <span class="v">{{ store.exchange_latency ? store.exchange_latency.toFixed(0) : 0 }}ms</span></span>
        <span class="stat-item">UPTIME: <span class="v">{{ uptimeStr }}</span></span>
      </div>
      <div class="footer-right">
        <span>Session Peak: <strong class="gold">{{ currencySymbol }}{{ formatNumber(liveState.peak_net_pnl_session) }}</strong></span>
        <span>Bot Status: <strong :class="store.connected ? 'profit' : 'loss'">{{ store.connected ? 'ONLINE' : 'OFFLINE' }}</strong></span>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'
import Header from '../components/Header.vue'
import ProfitChart from '../components/ProfitChart.vue'
import RiskGuardPopup from '../components/RiskGuardPopup.vue'
import BracketExitConfirmModal from '../components/BracketExitConfirmModal.vue'
import ActivityFeed from '../components/ActivityFeed.vue'

const store = useTradeStore()
const router = useRouter()
const { getAuthHeaders } = useAuth()

const now = ref(Date.now())
const mobileTab = ref('activity')
const wsStaleMs = 7000

// Resizer for panel width control
const leftPanelWidth = ref(440)
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

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

const liveState = ref({
  bot_offline: false,
  net_zero_survival_active: false,
  net_pnl_realized: 0,
  net_pnl_unrealized: 0,
  peak_net_pnl_session: 0,
  momentum_state: 'STALLING',
  net_pnl_velocity: 0,
})

const scorecardSummary = ref(null)
const scorecardFactors = ref([])
const scorecardRecent = ref([])
const scorecardTrendSeriesData = ref([])
const scorecardPairOptions = ref([])
const scorecardPair = ref('')
const scorecardHours = ref(24)
const scorecardLoading = ref(false)
const scorecardError = ref('')

const ENTRY_GATE_BLUEPRINT = [
  { stage: '1', code: 'POSITION_OR_BLACKLIST', title: 'Open Position / Blacklist Guard', copy: 'Reject pair yang masih open atau masuk blacklist.', indicators: ['Position state', 'Blacklist'] , tone: 'danger', scoreKeys: ['POSITION_OR_BLACKLIST'] },
  { stage: '2', code: 'COOLDOWN_ACTIVE', title: 'Cooldown Guard', copy: 'Menahan pair yang masih berada di window cooldown.', indicators: ['Cooldown clock', 'Pair timing'], tone: 'warn', scoreKeys: ['COOLDOWN_ACTIVE'] },
  { stage: '2b', code: 'REGIME_COOLDOWN', title: 'Regime Cooldown', copy: 'Mencegah re-entry terlalu cepat pada regime yang sama.', indicators: ['Regime memory', 'Cooldown timing'], tone: 'warn', scoreKeys: ['REGIME_COOLDOWN'] },
  { stage: '3', code: 'RADAR_RECOMMENDATION', title: 'Radar Recommendation', copy: 'Radar memberi bias awal sebelum gate teknikal dievaluasi.', indicators: ['Radar bias', 'Tracking log'], tone: 'neutral', scoreKeys: ['RADAR_BUY', 'RADAR_SELL'] },
  { stage: '4', code: 'TECH_GATE', title: 'Multi-Confluence Technical Gate', copy: 'Filter momentum utama yang menggabungkan sinyal teknikal aktif.', indicators: ['Momentum', 'Orderflow', 'Trend'], tone: 'good', scoreKeys: ['BREAKOUT', 'TREND_UP', 'SIDEWAYS', 'TREND_AGAINST'] },
  { stage: '4b', code: 'ATR_HIGH', title: 'ATR Volatility Filter', copy: 'Reject saat volatilitas terlalu liar untuk entry aman.', indicators: ['ATR', 'Volatility'], tone: 'warn', scoreKeys: ['ATR_OK', 'ATR_HIGH'] },
  { stage: '4c', code: 'EMA50_MISS', title: 'EMA-50 Trend Filter', copy: 'Harga harus tetap searah trend EMA-50 dan MTFA.', indicators: ['EMA50', 'MTFA'], tone: 'good', scoreKeys: ['EMA50_ALIGN', 'EMA50_MISS', 'MTFA_ALIGN', 'MTFA_MISS'] },
  { stage: '4d', code: 'VERTICAL_SPIKE', title: 'Vertical Spike / RSI Ceiling', copy: 'Mencegah mengejar candle yang sudah terlalu vertikal.', indicators: ['RSI', 'Velocity', 'EMA9'], tone: 'warn', scoreKeys: ['RSI_HEALTHY', 'RSI_CEILING', 'VERTICAL_SPIKE'] },
  { stage: '4e', code: 'RESISTANCE_NEAR', title: 'Resistance Guard', copy: 'Entry diblok saat harga terlalu dekat resistance.', indicators: ['Resistance', 'Stochastic', 'RSI'], tone: 'warn', scoreKeys: ['RESISTANCE_NEAR'] },
  { stage: '5', code: 'TECH_PROB_IMBALANCE', title: 'Tech Probability & Imbalance', copy: 'Menggabungkan TQ probability dengan orderflow imbalance.', indicators: ['TQ Prob', 'Imbalance'], tone: 'good', scoreKeys: ['TECH_PROB_OK', 'TECH_PROB_NEAR', 'TECH_PROB_LOW', 'STRONG_IMBALANCE', 'IMBALANCE_OK', 'IMBALANCE_WEAK', 'TQ_WARMUP'] },
  { stage: '9', code: 'DYNAMIC_THRESHOLD', title: 'Dynamic Threshold', copy: 'Floor entry menyesuaikan volatilitas dan trend.', indicators: ['Volatility', 'Trend'], tone: 'neutral', scoreKeys: [] },
  { stage: '10', code: 'GLOBAL_PAUSE', title: 'Consecutive-Loss Halt', copy: 'Bot masuk mode defensif ketika loss streak memanjang.', indicators: ['Loss streak', 'Pause state'], tone: 'danger', scoreKeys: ['GLOBAL_PAUSE'] },
  { stage: '11', code: 'KNN_AVOID', title: 'K-NN Pattern Scoring', copy: 'Menolak DNA pola historis yang sering gagal.', indicators: ['KNN DNA', 'Pattern history'], tone: 'neutral', scoreKeys: ['KNN_AVOID'] },
  { stage: '13', code: 'FINAL_CONFIDENCE', title: 'Final Confidence Gate', copy: 'Tahap akhir sebelum entry dieksekusi.', indicators: ['Base confidence', 'Scalper boost'], tone: 'good', scoreKeys: ['BASE_CONF'] },
]

function normalizeGateText(value) {
  return String(value || '').trim().toUpperCase()
}

function detectBlockedGate(stateText, reasonText) {
  const text = `${normalizeGateText(stateText)} ${normalizeGateText(reasonText)}`
  const rules = [
    ['POSITION_OR_BLACKLIST', ['POSITION_OR_BLACKLIST', 'BLACKLIST', 'ALREADY OPEN', 'OPEN POSITION']],
    ['COOLDOWN_ACTIVE', ['COOLDOWN_ACTIVE', 'COOLDOWN ACTIVE']],
    ['REGIME_COOLDOWN', ['REGIME_COOLDOWN', 'REGIME COOLDOWN']],
    ['ATR_HIGH', ['ATR_HIGH', 'ATR TOO HIGH', 'VOLATILITY TOO HIGH']],
    ['EMA50_MISS', ['EMA50_MISS', 'EMA50', 'MTFA']],
    ['VERTICAL_SPIKE', ['VERTICAL_SPIKE', 'SPIKE', 'OVEREXTENDED']],
    ['RESISTANCE_NEAR', ['RESISTANCE_NEAR', 'RESISTANCE']],
    ['TECH_PROB_IMBALANCE', ['TECH_PROB_LOW', 'IMBALANCE_WEAK', 'TQ_WARMUP']],
    ['DYNAMIC_THRESHOLD', ['DYNAMIC_THRESHOLD']],
    ['GLOBAL_PAUSE', ['GLOBAL_PAUSE', 'LOSS STREAK', 'SHADOW PROBE']],
    ['KNN_AVOID', ['KNN_AVOID']],
    ['FINAL_CONFIDENCE', ['BASE_CONF', 'FINAL CONFIDENCE', 'ENTRY SCORE']],
    ['RADAR_RECOMMENDATION', ['RADAR_SELL']],
  ]

  for (const [code, needles] of rules) {
    if (needles.some(needle => text.includes(needle))) {
      return code
    }
  }
  return ''
}

const entryGateSnapshot = computed(() => {
  const stats = store.stats || {}
  const isDryRun = Boolean(stats.dry_run)
  const recentRows = Array.isArray(scorecardRecent.value) ? scorecardRecent.value : []
  const recentPrimary = recentRows[0] || null
  const statePair = String(stats.entry_gate_pair || '').trim().toUpperCase()
  const pair = String(recentPrimary?.pair || statePair || '').trim().toUpperCase()
  const matchedRecent = recentRows.find(d => String(d.pair || '').trim().toUpperCase() === pair) || recentPrimary
  const recentDecision = String(matchedRecent?.decision || '').trim().toUpperCase()
  const recentAt = String(matchedRecent?.created_at || '').trim()
  const stateMatchesPair = Boolean(pair && statePair && statePair === pair)
  const state = stateMatchesPair ? String(stats.entry_gate_state || '').trim() : ''
  const reason = stateMatchesPair ? String(stats.entry_gate_reason || '').trim() : ''
  const at = (stateMatchesPair ? String(stats.entry_gate_at || '').trim() : '') || recentAt
  const executedState = stateMatchesPair && /EXECUTED/i.test(state)
  const passState = stateMatchesPair
    ? /PASS|SUCCESS|DONE/i.test(`${state} ${reason}`)
    : recentDecision === 'PASS'
  const blockerCode = stateMatchesPair ? detectBlockedGate(state, reason) : ''
  const hasBlocker = stateMatchesPair && Boolean(blockerCode) && !passState && !executedState

  let overallLabel = 'Pending'
  if (pair && (executedState || passState || recentDecision === 'PASS')) {
    overallLabel = 'Pass'
  } else if (pair && (hasBlocker || recentDecision === 'REJECT')) {
    overallLabel = 'Not Pass'
  } else if (pair) {
    overallLabel = 'Analyzing'
  }

  let actionLabel = 'IDLE'
  if (executedState) {
    actionLabel = isDryRun ? 'DRY RUN BUY' : 'BUY EXECUTED'
  } else if (recentDecision === 'PASS' || passState) {
    actionLabel = isDryRun ? 'DRY BUY READY' : 'BUY READY'
  } else if (recentDecision === 'REJECT' || hasBlocker) {
    actionLabel = 'REJECT'
  } else if (pair) {
    actionLabel = 'SCANNING'
  }

  return {
    pair,
    state,
    reason,
    at,
    stateText: executedState ? state : (overallLabel === 'Pass' ? 'PASS' : overallLabel === 'Not Pass' ? 'NOT PASS' : (pair ? 'ACTIVE EVALUATION' : 'IDLE')),
    reasonText: reason || (recentDecision === 'PASS'
      ? 'Latest scorecard passed the entry gate.'
      : recentDecision === 'REJECT'
        ? 'Latest scorecard did not pass the entry gate.'
        : 'Waiting for the next gate evaluation.'),
    atText: at ? new Date(at).toLocaleString('id-ID', { dateStyle: 'short', timeStyle: 'medium' }) : '—',
    executedState,
    passState,
    blockerCode,
    hasBlocker,
    isPass: passState,
    overallLabel,
    actionLabel,
    isDryRun,
  }
})

const entryGateCards = computed(() => {
  const snapshot = entryGateSnapshot.value
  const decision = entryGateDecision.value
  const hasDecision = Boolean(decision)
  const noteEntries = (decision?.score_notes || []).map(parseScoreNote).filter(Boolean)
  const blockerIndex = snapshot.isPass
    ? -1
    : ENTRY_GATE_BLUEPRINT.findIndex(gate => gate.code === snapshot.blockerCode)

  return ENTRY_GATE_BLUEPRINT.map((gate, index) => {
    const matchedNotes = collectGateNotes(gate.scoreKeys, noteEntries)
    const matchedTotal = matchedNotes.reduce((sum, n) => sum + Number(n.points || 0), 0)
    let status = 'pending'
    if (blockerIndex >= 0) {
      if (index < blockerIndex) {
        status = 'pass'
      } else if (index === blockerIndex) {
        status = 'not-pass'
      }
    }

    if (matchedNotes.length) {
      if (status !== 'not-pass') {
        status = matchedTotal < 0 ? 'penalty' : 'pass'
      }
    } else if (decision && gate.code === 'DYNAMIC_THRESHOLD') {
      status = 'pass'
    } else if (decision && gate.code === 'FINAL_CONFIDENCE') {
      status = String(decision.decision || '').toUpperCase() === 'REJECT' ? 'not-pass' : 'pass'
    } else if (status === 'pending' && hasDecision) {
      status = 'not-triggered'
    } else if (status === 'pending' && snapshot.pair && index === 0 && !hasDecision) {
      status = 'active'
    }

    let contributionLabel = 'No numeric output'
    let detailText = gate.copy

    if (gate.code === 'DYNAMIC_THRESHOLD' && decision) {
      contributionLabel = `Floor ${Number(decision.entry_threshold || 0).toFixed(1)}`
      detailText = `Threshold final dari regime ${String(decision.regime || 'UNKNOWN').toUpperCase()}.`
    } else if (gate.code === 'FINAL_CONFIDENCE' && decision) {
      contributionLabel = `${Number(decision.confidence || 0).toFixed(1)}% confidence`
      detailText = 'Confidence model yang dipakai untuk keputusan akhir.'
    } else if (matchedNotes.length) {
      contributionLabel = formatContributionValue(matchedTotal)
      detailText = formatGateDetail(matchedNotes)
    } else if (decision && gate.code === 'TECH_GATE') {
      contributionLabel = formatContributionValue(Number(decision.entry_score || 0) - 50)
      detailText = `Entry score total ${Number(decision.entry_score || 0).toFixed(1)} dari baseline 50.`
    }

    return {
      ...gate,
      status,
      statusLabel: status === 'pass' ? 'Pass' : status === 'penalty' ? 'Penalty' : status === 'not-pass' ? 'Not Pass' : status === 'not-triggered' ? 'Not Triggered' : status === 'active' ? 'Analyzing' : 'Pending',
      hasSignalNotes: matchedNotes.length > 0,
      contributionLabel,
      detailText,
      contributionClass: matchedTotal > 0 || /confidence|floor/i.test(contributionLabel) ? 'up' : matchedTotal < 0 ? 'down' : 'neutral',
    }
  })
})

const entryGateIndicatorSummary = computed(() => {
  const current = entryGateCards.value.find(gate => gate.status === 'not-pass' && gate.hasSignalNotes)
    || entryGateCards.value.find(gate => gate.status === 'active' && gate.hasSignalNotes)
    || entryGateCards.value.find(gate => gate.code === 'TECH_GATE' && (gate.hasSignalNotes || gate.status !== 'pending'))
    || entryGateCards.value.find(gate => gate.status === 'pass' && gate.hasSignalNotes)
    || entryGateCards.value.find(gate => gate.status === 'not-pass')
    || entryGateCards.value.find(gate => gate.status === 'active')
    || entryGateCards.value.find(gate => gate.status === 'pass')
    || entryGateCards.value[entryGateCards.value.length - 1]

  return current?.indicators?.join(' · ') || 'EMA50 · RSI · ATR · Volume'
})

const entryGateChartModel = computed(() => {
  const cards = entryGateCards.value || []
  const counts = {
    pass: 0,
    penalty: 0,
    notPass: 0,
    idle: 0,
  }

  const cells = cards.map((gate, idx) => {
    let statusBase = 0.35
    if (gate.status === 'pass') statusBase = 1
    else if (gate.status === 'penalty') statusBase = 0.7
    else if (gate.status === 'active') statusBase = 0.55
    else if (gate.status === 'not-pass') statusBase = 0.2
    else if (gate.status === 'not-triggered') statusBase = 0.3

    if (gate.status === 'pass') counts.pass += 1
    if (gate.status === 'penalty') counts.penalty += 1
    if (gate.status === 'not-pass') counts.notPass += 1
    if (!['pass', 'penalty', 'not-pass'].includes(gate.status)) counts.idle += 1

    const contributionBoost = Math.max(-2.5, Math.min(2.5, extractContributionNumber(gate.contributionLabel)))
    const normalizedBoost = Math.max(0, Math.min(1, (contributionBoost + 2.5) / 5))
    const alpha = (0.24 + (statusBase * 0.56 + normalizedBoost * 0.2)).toFixed(2)

    return {
      idx,
      stage: gate.stage,
      status: gate.status,
      scoreLabel: gate.contributionLabel,
      alpha,
      tooltip: `Gate ${gate.stage} - ${gate.statusLabel}: ${gate.contributionLabel}`,
    }
  })

  if (!cells.length) {
    return {
      summary: 'No gate data',
      cells: [],
      counts,
    }
  }
  const total = cells.length

  return {
    summary: `${counts.pass} pass · ${counts.notPass} blocked · ${total} gates`,
    cells,
    counts,
  }
})

const entryGateDecision = computed(() => {
  const pair = String(entryGateSnapshot.value.pair || scorecardPair.value || '').trim().toUpperCase()
  const recent = Array.isArray(scorecardRecent.value) ? scorecardRecent.value : []
  if (pair) {
    const matched = recent.find(d => String(d.pair || '').trim().toUpperCase() === pair)
    if (matched) return matched
  }
  return recent[0] || null
})

function formatContributionValue(points) {
  const n = Number(points || 0)
  return `${n > 0 ? '+' : ''}${n.toFixed(1)}`
}

function extractContributionNumber(label) {
  const match = String(label || '').match(/[-+]?\d+(?:\.\d+)?/)
  return match ? Number(match[0]) : 0
}

function collectGateNotes(scoreKeys, noteEntries) {
  if (!Array.isArray(scoreKeys) || !scoreKeys.length) return []
  return noteEntries.filter(n => scoreKeys.includes(n.factor))
}

function formatGateDetail(notes) {
  if (!notes.length) return 'Belum ada angka yang tercatat untuk gate ini.'
  return notes.map(n => `${formatFactorLabel(n.factor)} ${formatContributionValue(n.points)}`).join(' · ')
}

const topPositiveFactors = computed(() => {
  return (scorecardFactors.value || [])
    .filter(f => Number(f.avg_points || 0) > 0)
    .sort((a, b) => Number(b.avg_points || 0) - Number(a.avg_points || 0))
    .slice(0, 5)
})

const topNegativeFactors = computed(() => {
  return (scorecardFactors.value || [])
    .filter(f => Number(f.avg_points || 0) < 0)
    .sort((a, b) => Number(a.avg_points || 0) - Number(b.avg_points || 0))
    .slice(0, 5)
})

const FACTOR_COPY = {
  EMA50_ALIGN: ['Harga di atas EMA50', 'Trend lokal sudah mendukung entry.'],
  EMA50_MISS: ['Harga di bawah EMA50', 'Tunggu reclaim EMA50 sebelum agresif masuk.'],
  MTFA_ALIGN: ['MTFA searah', '5m dan 15m ikut mendukung.'],
  MTFA_MISS: ['MTFA belum sinkron', 'Jangan paksa entry saat timeframe atas belum align.'],
  STRONG_IMBALANCE: ['Buy pressure kuat', 'Orderflow mendukung impuls lanjutan.'],
  IMBALANCE_OK: ['Buy pressure cukup', 'Flow lumayan sehat, tapi belum dominan.'],
  IMBALANCE_WEAK: ['Orderflow lemah', 'Masih minim dorongan bid-side.'],
  BREAKOUT: ['Breakout aktif', 'Momentum breakout memberi edge terbaik.'],
  TREND_UP: ['Trend naik', 'Arah utama masih membantu entry.'],
  SIDEWAYS: ['Market sideways', 'Lebih baik tunggu struktur lebih jelas.'],
  TREND_AGAINST: ['Trend melawan', 'Jangan lawan arah utama tanpa alasan kuat.'],
  TECH_PROB_OK: ['Probabilitas teknikal kuat', 'Setup teknikal sudah cukup matang.'],
  TECH_PROB_NEAR: ['Probabilitas hampir lolos', 'Butuh sedikit konfirmasi tambahan.'],
  TECH_PROB_LOW: ['Probabilitas teknikal rendah', 'Belum ada edge statistik yang cukup.'],
  RSI_HEALTHY: ['RSI sehat', 'Momentum belum overheat.'],
  RSI_CEILING: ['RSI terlalu tinggi', 'Hindari entry saat sudah terlalu panas.'],
  VERTICAL_SPIKE: ['Lonjakan terlalu vertikal', 'Risiko buy at top sedang tinggi.'],
  RESISTANCE_NEAR: ['Dekat resistance', 'Tunggu breakout bersih atau retrace.'],
  ATR_OK: ['Volatilitas aman', 'ATR masih dalam koridor aman.'],
  ATR_HIGH: ['Volatilitas terlalu tinggi', 'Perbesar disiplin, jangan kejar candle liar.'],
  RADAR_BUY: ['Radar mendukung buy', 'Bias tambahan dari radar recommendation.'],
  RADAR_SELL: ['Radar condong sell', 'Radar belum mendukung entry.'],
  BYPASS_SIGNAL: ['Bypass aktif', 'Setup spesial memberi toleransi ekstra.'],
  BASE_CONF: ['Confidence model rendah', 'Belum ada conviction yang cukup untuk dieksekusi.'],
  GLOBAL_PAUSE: ['Global pause aktif', 'Bot sedang defensif setelah loss streak.'],
  COOLDOWN_ACTIVE: ['Cooldown aktif', 'Tunggu siklus cooldown selesai.'],
  REGIME_COOLDOWN: ['Cooldown regime aktif', 'Pair ini baru saja dievaluasi.'],
  POSITION_OR_BLACKLIST: ['Pair diblokir', 'Sudah open position atau masuk blacklist.'],
  KNN_AVOID: ['Pattern historis jelek', 'Setup ini punya jejak performa buruk.'],
  SELL_BLOCK: ['Sell pressure dominan', 'Sinyal teknikal lebih dekat ke sell daripada buy.'],
  TQ_WARMUP: ['Data belum cukup', 'Engine masih warm-up untuk pair ini.'],
}

function parseScoreNote(note) {
  const raw = String(note || '').trim()
  if (!raw) return null
  const idx = raw.lastIndexOf(':')
  if (idx === -1) return { factor: raw, points: 0 }
  const factor = raw.slice(0, idx).trim()
  const points = Number(raw.slice(idx + 1).trim())
  return { factor, points: Number.isFinite(points) ? points : 0 }
}

function formatFactorLabel(factor) {
  return FACTOR_COPY[factor]?.[0] || String(factor || 'UNKNOWN').replace(/_/g, ' ')
}

function formatFactorAction(factor) {
  return FACTOR_COPY[factor]?.[1] || 'Gunakan faktor ini sebagai konteks keputusan entry.'
}

function formatFactorPoints(value) {
  const n = Number(value || 0)
  return `${n > 0 ? '+' : ''}${n.toFixed(1)}`
}

const regimeStats = computed(() => {
  const bucket = {}
  for (const d of (scorecardRecent.value || [])) {
    const regime = String(d.regime || 'UNKNOWN').toUpperCase()
    if (!bucket[regime]) {
      bucket[regime] = { regime, total: 0, pass: 0 }
    }
    bucket[regime].total += 1
    if (String(d.decision || '').toUpperCase() === 'PASS') {
      bucket[regime].pass += 1
    }
  }

  return Object.values(bucket)
    .map(x => ({
      regime: x.regime,
      total: x.total,
      pass_rate_pct: x.total > 0 ? (x.pass / x.total) * 100 : 0,
    }))
    .sort((a, b) => b.pass_rate_pct - a.pass_rate_pct)
})

const scorecardNearMisses = computed(() => {
  const seen = new Set()
  return (scorecardRecent.value || [])
    .filter(d => String(d.decision || '').toUpperCase() === 'REJECT')
    .map((d) => {
      const notes = (d.score_notes || []).map(parseScoreNote).filter(Boolean)
      const blocker = notes
        .filter(n => n.points < 0)
        .sort((a, b) => a.points - b.points)[0] || notes[0]
      return {
        pair: String(d.pair || ''),
        regime: String(d.regime || 'UNKNOWN'),
        gap: Math.max(0, Number(d.entry_threshold || 0) - Number(d.entry_score || 0)),
        reason: blocker
          ? `${formatFactorLabel(blocker.factor)}. ${formatFactorAction(blocker.factor)}`
          : 'Belum ada faktor dominan yang tercatat.',
      }
    })
    .filter(item => {
      if (!item.pair || seen.has(item.pair)) return false
      seen.add(item.pair)
      return true
    })
    .sort((a, b) => a.gap - b.gap)
    .slice(0, 4)
})

const scorecardOperatorBrief = computed(() => {
  const summary = scorecardSummary.value || {}
  const samples = Number(summary.sample_size || 0)
  const passCount = Number(summary.pass_count || 0)
  const rejectCount = Number(summary.reject_count || 0)
  const passRate = Number(summary.pass_rate_pct || 0)
  const mainBlocker = topNegativeFactors.value[0]
  const bestRegime = regimeStats.value[0]
  const trendSeries = scorecardTrendSeries.value
  const lastRate = trendSeries.length >= 2 ? trendSeries[trendSeries.length - 1].pass_rate : null
  const prevRate = trendSeries.length >= 2 ? trendSeries[trendSeries.length - 2].pass_rate : null
  const trendText = lastRate == null || prevRate == null
    ? 'Trend pass-rate belum cukup panjang.'
    : lastRate > prevRate + 5
      ? 'Pass-rate mulai membaik.'
      : lastRate < prevRate - 5
        ? 'Pass-rate makin ketat.'
        : 'Pass-rate relatif stabil.'

  if (!samples) {
    return {
      title: 'Belum ada evaluasi entry di window ini',
      body: 'Scorecard akan berguna setelah engine menilai candidate pair. Saat ini belum ada data yang bisa dijadikan keputusan operator.',
    }
  }

  if (passCount === 0) {
    return {
      title: 'Belum ada setup layak entry di window ini',
      body: `${rejectCount} evaluasi terakhir semuanya ditolak. Blocker utama saat ini ${mainBlocker ? formatFactorLabel(mainBlocker.factor).toLowerCase() : 'belum terdeteksi'}, jadi user sebaiknya tidak force entry. ${trendText}`,
    }
  }

  return {
    title: `${passRate.toFixed(1)}% kandidat lolos di window ini`,
    body: `${passCount} dari ${samples} evaluasi lolos. ${mainBlocker ? `Friction terbesar tetap ${formatFactorLabel(mainBlocker.factor).toLowerCase()}. ` : ''}${bestRegime ? `Regime terbaik sementara ${bestRegime.regime.toLowerCase()}. ` : ''}${trendText}`,
  }
})

const scorecardActionItems = computed(() => {
  const items = []
  const blocker = topNegativeFactors.value[0]
  const helper = topPositiveFactors.value[0]
  const bestRegime = regimeStats.value[0]
  const nearMiss = scorecardNearMisses.value[0]

  if (blocker) {
    items.push({
      title: 'Blocker utama',
      copy: `${formatFactorLabel(blocker.factor)}. ${formatFactorAction(blocker.factor)}`,
    })
  }
  if (nearMiss) {
    items.push({
      title: 'Setup terdekat',
      copy: `${formatPair(nearMiss.pair)} kurang ${nearMiss.gap.toFixed(1)} poin untuk lolos.`,
    })
  }
  if (bestRegime) {
    items.push({
      title: 'Regime terbaik',
      copy: `${bestRegime.regime} punya pass-rate ${bestRegime.pass_rate_pct.toFixed(1)}% dari ${bestRegime.total} evaluasi.`,
    })
  }
  if (helper) {
    items.push({
      title: 'Tailwind terkuat',
      copy: `${formatFactorLabel(helper.factor)} paling sering membantu setup lolos.`,
    })
  }

  return items.slice(0, 4)
})

const scorecardTrendSeries = computed(() => {
  if (Array.isArray(scorecardTrendSeriesData.value) && scorecardTrendSeriesData.value.length) {
    return scorecardTrendSeriesData.value
      .map((item, index) => ({
        t: Number.isFinite(Number(item.t)) ? Number(item.t) : index,
        label: String(item.label || item.t || ''),
        pass_rate: Number(item.pass_rate || 0),
      }))
      .sort((a, b) => a.t - b.t)
      .slice(-24)
  }

  const bucket = {}
  for (const d of (scorecardRecent.value || [])) {
    const ts = Date.parse(String(d.created_at || ''))
    if (!Number.isFinite(ts)) continue
    const dt = new Date(ts)
    dt.setMinutes(0, 0, 0)
    const key = dt.getTime()
    if (!bucket[key]) {
      bucket[key] = { total: 0, pass: 0, label: dt.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' }) }
    }
    bucket[key].total += 1
    if (String(d.decision || '').toUpperCase() === 'PASS') {
      bucket[key].pass += 1
    }
  }

  return Object.entries(bucket)
    .map(([k, v]) => ({
      t: Number(k),
      label: v.label,
      pass_rate: v.total > 0 ? (v.pass / v.total) * 100 : 0,
    }))
    .sort((a, b) => a.t - b.t)
    .slice(-24)
})

const scorecardTrendLinePath = computed(() => {
  const pts = scorecardTrendSeries.value
  if (pts.length < 2) return ''
  const w = 320
  const h = 80
  return pts.map((p, i) => {
    const x = (i / (pts.length - 1)) * w
    const y = h - (Math.max(0, Math.min(100, p.pass_rate)) / 100) * h
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
  }).join(' ')
})

const scorecardTrendAreaPath = computed(() => {
  const line = scorecardTrendLinePath.value
  const pts = scorecardTrendSeries.value
  if (!line || pts.length < 2) return ''
  return `${line} L 320 80 L 0 80 Z`
})

const uptimeBaseSec = ref(0)
const uptimeBaseAt = ref(0)
const uptimeStartedAt = ref('')

function restoreUptimeSnapshot() {
  try {
    const saved = JSON.parse(sessionStorage.getItem('algovibe_uptime_snapshot') || '{}')
    uptimeBaseSec.value = Math.max(0, Number(saved.uptimeSec || 0))
    uptimeBaseAt.value = Math.max(0, Number(saved.baseAt || 0))
    uptimeStartedAt.value = String(saved.startedAt || '')
  } catch {}
}

function persistUptimeSnapshot() {
  try {
    sessionStorage.setItem('algovibe_uptime_snapshot', JSON.stringify({
      uptimeSec: uptimeBaseSec.value,
      baseAt: uptimeBaseAt.value,
      startedAt: uptimeStartedAt.value,
    }))
  } catch {}
}

function applyUptimeSnapshot(nextSec, startedAtRaw) {
  const nextUptimeSec = Math.max(0, Number(nextSec || 0))
  const nextStartedAt = String(startedAtRaw || '')
  const currentElapsed = uptimeBaseAt.value > 0
    ? uptimeBaseSec.value + Math.max(0, Math.floor((Date.now() - uptimeBaseAt.value) / 1000))
    : Math.max(0, uptimeBaseSec.value)

  if (nextStartedAt && uptimeStartedAt.value && nextStartedAt !== uptimeStartedAt.value) {
    uptimeStartedAt.value = nextStartedAt
    uptimeBaseSec.value = nextUptimeSec
    uptimeBaseAt.value = Date.now()
    store.stats.uptime_sec = nextUptimeSec
    persistUptimeSnapshot()
    return
  }

  if (nextStartedAt && !uptimeStartedAt.value) {
    uptimeStartedAt.value = nextStartedAt
  }

  const acceptedUptime = Math.max(currentElapsed, nextUptimeSec)
  uptimeBaseSec.value = acceptedUptime
  uptimeBaseAt.value = Date.now()
  store.stats.uptime_sec = acceptedUptime
  persistUptimeSnapshot()
}

const uptimeStr = computed(() => {
  const baseSec = Number(uptimeBaseSec.value || 0)
  const baseAt = Number(uptimeBaseAt.value || 0)
  const localDrift = baseAt > 0 ? Math.max(0, Math.floor((now.value - baseAt) / 1000)) : 0
  const sec = baseSec + localDrift
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
})

async function fetchBotStatus() {
  try {
    const res = await fetch(`${backendUrl}/api/bot-status`, { headers: getAuthHeaders() })
    if (!res.ok) return

    const data = await res.json()
    applyUptimeSnapshot(
      data.uptime_sec,
      data.bot_state?.executive_core_started_at,
    )
  } catch {}
}

const socketIsFresh = computed(() => {
  const lastAt = Number(store.lastSocketStatsAt || 0)
  return Boolean(store.connected && lastAt > 0 && (Date.now() - lastAt) < wsStaleMs)
})

const livePositions = computed(() => store.positions)

const liveRealizedPnl = computed(() =>
  socketIsFresh.value ? Number(store.stats.daily_pnl || 0) : Number(liveState.value.net_pnl_realized || 0)
)

const liveUnrealizedPnl = computed(() =>
  socketIsFresh.value ? Number(store.stats.unrealized_pnl || 0) : Number(liveState.value.net_pnl_unrealized || 0)
)

const safetyNetPnl = computed(() => liveRealizedPnl.value + liveUnrealizedPnl.value)
const safetyGaugePct = computed(() => {
  const buffer = liveRealizedPnl.value
  if (buffer <= 0) return 0
  return Math.max(0, Math.min(100, (safetyNetPnl.value / buffer) * 100))
})
const safetyIsDanger = computed(() => safetyGaugePct.value < 20)
const safetyGaugeColor = computed(() => safetyGaugePct.value < 20 ? '#ff3366' : '#00ff88')
const dataSourceLabel = computed(() => socketIsFresh.value ? 'WS REALTIME' : 'API FALLBACK')
const dataSourceClass = computed(() => socketIsFresh.value ? 'is-realtime' : 'is-fallback')
const currencySymbol = computed(() => {
  return (store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)) ? '$' : 'Rp '
})
const liveContextCards = computed(() => {
  const summary = scorecardSummary.value || {}
  const sessionMode = String(store.stats.session_mode || 'NORMAL').replace(/_/g, ' ')
  const regime = String(store.stats.regime || 'UNKNOWN').replace(/_/g, ' ')
  return [
    {
      label: 'Engine State',
      value: liveState.value.net_zero_survival_active ? 'Protected' : (store.connected ? 'Connected' : 'Fallback'),
      meta: liveState.value.net_zero_survival_active ? 'Emergency protection active' : `Source ${dataSourceLabel.value.toLowerCase()}`,
      tone: liveState.value.net_zero_survival_active ? 'danger' : (store.connected ? 'good' : 'neutral'),
    },
    {
      label: 'Session Bias',
      value: sessionMode,
      meta: `Regime ${regime}`,
      tone: sessionMode.includes('RECOVERY') || sessionMode.includes('PROTECT') ? 'warn' : 'neutral',
    },
    {
      label: 'Open Exposure',
      value: `${livePositions.value.length} posisi`,
      meta: `Net ${safetyNetPnl.value >= 0 ? '+' : ''}${currencySymbol.value}${formatNumber(safetyNetPnl.value)}`,
      tone: safetyNetPnl.value >= 0 ? 'good' : 'danger',
    },
    {
      label: 'Entry Window',
      value: `${Number(summary.pass_rate_pct || 0).toFixed(1)}% ready`,
      meta: `${Number(summary.sample_size || 0)} evaluasi dalam ${scorecardHours.value}H`,
      tone: Number(summary.pass_rate_pct || 0) >= 40 ? 'good' : 'warn',
    },
  ]
})

async function fetchLiveState() {
  try {
    const res = await fetch(`${backendUrl}/api/live/state`, { headers: getAuthHeaders() })
    if (!res.ok) return

    const data = await res.json()
    liveState.value.bot_offline = Boolean(data.bot_offline)
    liveState.value.net_zero_survival_active = Boolean(data.net_zero_survival_active)
    liveState.value.peak_net_pnl_session = Number(data.peak_net_pnl_session || 0)

    store.stats.entry_gate_state = data.entry_gate_state ?? store.stats.entry_gate_state ?? ''
    store.stats.entry_gate_reason = data.entry_gate_reason ?? store.stats.entry_gate_reason ?? ''
    store.stats.entry_gate_pair = data.entry_gate_pair ?? store.stats.entry_gate_pair ?? ''
    store.stats.entry_gate_at = data.entry_gate_at ?? store.stats.entry_gate_at ?? ''

    if (!socketIsFresh.value) {
      liveState.value.net_pnl_realized = Number(data.net_pnl_realized || 0)
      liveState.value.net_pnl_unrealized = Number(data.net_pnl_unrealized || 0)
      liveState.value.momentum_state = data.momentum_state || 'STALLING'
      liveState.value.net_pnl_velocity = Number(data.net_pnl_velocity || 0)

      store.stats.daily_pnl = liveState.value.net_pnl_realized
      store.stats.unrealized_pnl = liveState.value.net_pnl_unrealized
      store.stats.net_pnl = liveState.value.net_pnl_realized + liveState.value.net_pnl_unrealized
      store.stats.momentum_state = liveState.value.momentum_state
      store.stats.net_pnl_velocity = liveState.value.net_pnl_velocity
      store.markApiStatsUsed(wsStaleMs)

      const total = Math.round(store.stats.net_pnl)
      const timeStr = new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      const last = store.pnlHistory[store.pnlHistory.length - 1]
      if (!last || last.pnl !== total) {
        store.pnlHistory.push({ time: timeStr, pnl: total })
        if (store.pnlHistory.length > 300) {
          store.pnlHistory.splice(0, store.pnlHistory.length - 300)
        }
      }
    }
  } catch {
    liveState.value.bot_offline = true
  }
}

async function fetchEntryScorecards() {
  scorecardLoading.value = true
  scorecardError.value = ''
  try {
    const params = new URLSearchParams()
    params.set('limit', '400')
    params.set('hours', String(scorecardHours.value || 24))
    if (scorecardPair.value) {
      params.set('pair', scorecardPair.value)
    }
    const res = await fetch(`${backendUrl}/api/entry-scorecards?${params.toString()}`, { headers: getAuthHeaders() })
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }
    const data = await res.json()
    scorecardSummary.value = data.summary || null
    scorecardFactors.value = Array.isArray(data.factors) ? data.factors : []
    scorecardRecent.value = Array.isArray(data.recent_decisions) ? data.recent_decisions : []
    scorecardTrendSeriesData.value = Array.isArray(data.trend_series) ? data.trend_series : []
    scorecardPairOptions.value = Array.isArray(data.pair_options) ? data.pair_options : []
  } catch (e) {
    scorecardError.value = 'Failed to load scorecard analytics'
  } finally {
    scorecardLoading.value = false
  }
}

async function fetchLiveSnapshot() {
  try {
    const [statsRes, positionsRes] = await Promise.all([
      fetch(`${backendUrl}/api/live/stats`, { headers: getAuthHeaders() }),
      fetch(`${backendUrl}/api/live/positions`, { headers: getAuthHeaders() }),
    ])

    if (statsRes.ok && !socketIsFresh.value) {
      const data = await statsRes.json()
      Object.assign(store.stats, {
        daily_pnl: Number(data.daily_pnl ?? 0),
        available_idr: Number(data.available_idr ?? 0),
        idr_used: Number(data.idr_used ?? 0),
        total_equity: Number(data.total_equity ?? 0),
        utilization: Number(data.utilization ?? 0),
        win_rate: Number(data.win_rate ?? 0),
        active_positions: Number(data.active_positions ?? 0),
        unrealized_pnl: Number(data.unrealized_pnl ?? 0),
        net_pnl: Number(data.net_pnl ?? 0),
        regime: data.regime ?? 'CHOPPY',
        market_sentiment_index: Number(data.market_sentiment_index ?? 50),
        momentum_state: data.momentum_state ?? 'STALLING',
        net_pnl_velocity: Number(data.net_pnl_velocity ?? 0),
        entry_gate_state: data.entry_gate_state ?? '',
        entry_gate_reason: data.entry_gate_reason ?? '',
        entry_gate_pair: data.entry_gate_pair ?? '',
        entry_gate_at: data.entry_gate_at ?? '',
        session_mode: data.session_mode ?? 'NORMAL',
        daily_target_hit: Boolean(data.daily_target_hit),
        session_buy_enabled: data.session_buy_enabled ?? true,
        btc_price: Number(data.btc_price ?? 0),
        btc_change_24h: Number(data.btc_change_24h ?? 0),
        btc_ch_5m: Number(data.btc_ch_5m ?? 0),
        btc_ch_15m: Number(data.btc_ch_15m ?? 0),
        btc_ch_1h: Number(data.btc_ch_1h ?? 0),
        btc_trend: data.btc_trend ?? 'N/A',
        btc_score: Number(data.btc_score ?? 50),
      })
      store.markApiStatsUsed(wsStaleMs)
    }

    if (positionsRes.ok && !socketIsFresh.value) {
      const data = await positionsRes.json()
      if (Array.isArray(data)) {
        store.positions = data
      }
    }
  } catch {}
}

function formatNumber(val) { 
  const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
  if (isUsdt) {
    return Number(val || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  } else {
    return Number(val || 0).toLocaleString('id-ID', { maximumFractionDigits: 0 })
  }
}
function formatPrice(val) { 
  const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
  if (isUsdt) {
    return Number(val || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
  } else {
    return Number(val || 0).toLocaleString('id-ID', { maximumFractionDigits: 0 })
  }
}
function formatPair(pair) { return pair ? pair.replace(/idr$/i, '').replace(/usdt$/i, '').toUpperCase() : '-' }

let pollInt = null
let clockInt = null
let scorecardTick = 0

onMounted(() => {
  restoreUptimeSnapshot()
  store.connect()
  fetchBotStatus()
  fetchLiveSnapshot()
  fetchLiveState()
  fetchEntryScorecards()

  pollInt = setInterval(() => {
    fetchBotStatus()
    if (!socketIsFresh.value) {
      fetchLiveSnapshot()
    }
    fetchLiveState()
    scorecardTick += 1
    if (scorecardTick % 3 === 0) {
      fetchEntryScorecards()
    }
  }, 5000)

  clockInt = setInterval(() => { now.value = Date.now() }, 1000)
})

onUnmounted(() => {
  clearInterval(pollInt)
  if (clockInt) clearInterval(clockInt)
  stopResize()
})

watch(() => store.forceLogout, (triggered) => {
  if (triggered) {
    router.push('/login')
  }
})

watch([scorecardPair, scorecardHours], () => {
  fetchEntryScorecards()
})
</script>

<style scoped>
.live-dashboard {
  min-height: 100vh;
  min-height: 100dvh;
  background: #060810;
  display: flex;
  flex-direction: column;
  font-family: 'Inter', sans-serif;
  overflow-x: hidden;
  overflow-y: auto;
}

.offline-banner {
  background: rgba(245, 166, 35, 0.1);
  color: #f5a623;
  padding: 8px;
  text-align: center;
  font-size: 12px;
  font-weight: 700;
  border-bottom: 1px solid rgba(245, 166, 35, 0.2);
}

.nz-survival-banner {
  background: rgba(255, 51, 102, 0.1);
  color: #ff3366;
  padding: 12px;
  text-align: center;
  font-weight: 800;
  font-size: 11px;
  letter-spacing: 1px;
  border-bottom: 1px solid rgba(255, 51, 102, 0.2);
}

/* Command Center Styles */
.command-center-wrap {
  margin: 16px 24px;
  padding: 16px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 12px;
}

.cc-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.cc-title {
  font-size: 10px;
  font-weight: 900;
  letter-spacing: 2px;
  color: #444;
}

.cc-status {
  font-size: 10px;
  font-weight: 800;
  padding: 4px 10px;
  border-radius: 20px;
  letter-spacing: 1px;
}

.mode-scalping { background: rgba(0, 255, 136, 0.1); color: #00ff88; box-shadow: 0 0 10px rgba(0, 255, 136, 0.1); }
.mode-harvest { background: rgba(245, 166, 35, 0.1); color: #f5a623; box-shadow: 0 0 10px rgba(245, 166, 35, 0.1); }

.cc-controls {
  display: flex;
  align-items: center;
  gap: 24px;
}

.cc-control-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cc-label {
  font-size: 9px;
  font-weight: 700;
  color: #666;
  letter-spacing: 1px;
}

.cc-toggle-btn {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #555;
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 10px;
  font-weight: 900;
  cursor: pointer;
  transition: all 0.3s ease;
}

.cc-toggle-btn.active {
  background: #f5a623;
  color: #000;
  box-shadow: 0 0 15px rgba(245, 166, 35, 0.4);
  border-color: transparent;
}

.cc-action-btn {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #888;
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 10px;
  font-weight: 900;
  cursor: pointer;
}

.cc-action-btn.active {
  color: #fff;
  border-color: rgba(0, 255, 136, 0.5);
  background: rgba(0, 255, 136, 0.1);
}

.cc-divider {
  width: 1px;
  height: 30px;
  background: rgba(255, 255, 255, 0.05);
}

.cc-value-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.cc-val {
  font-size: 14px;
  font-weight: 800;
  color: #fff;
}

.cc-mini-btn {
  background: transparent;
  border: 1px solid rgba(255, 51, 102, 0.3);
  color: #ff3366;
  font-size: 8px;
  font-weight: 900;
  padding: 2px 6px;
  border-radius: 4px;
  cursor: pointer;
}

.cc-mini-btn:hover {
  background: #ff3366;
  color: #fff;
}

@media (max-width: 768px) {
  .command-center-wrap { margin: 12px; padding: 12px; }
  .cc-controls { gap: 12px; overflow-x: auto; padding-bottom: 4px; }
  .cc-control-item { flex-shrink: 0; }
}

.main-grid {
  width: 100%;
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  padding: 16px;
  gap: 0;
}

.panels-row {
  display: flex;
  min-height: clamp(520px, 68vh, 860px);
  overflow: hidden;
  gap: 0;
}

.left-panel {
  width: var(--left-width);
  min-width: 300px;
  min-height: 0;
  background: #0a0c14;
  border-radius: 16px;
  border: 1px solid #1a1c24;
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
  background: #1a1c24;
  border-radius: 1px;
}

.resizer:hover::after, .resizer.is-active::after {
  background: #6366f1;
  box-shadow: 0 0 10px #6366f1;
}

.right-panel {
  flex: 1;
  min-height: 0;
  background: #0a0c14;
  border-radius: 16px;
  border: 1px solid #1a1c24;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.chart-wrap {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.profit { color: #00ff88; }
.loss { color: #ff3366; }

.safety-gauge-wrap {
  padding: 16px 24px;
  background: rgba(255, 255, 255, 0.01);
  border-bottom: 1px solid #1a1c24;
}

.scorecard-panel {
  margin: 14px 16px 0;
  padding: 16px;
  border: 1px solid #1f2431;
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(14, 18, 30, 0.96), rgba(20, 26, 43, 0.92));
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.18);
}

.entry-intel-panel {
  display: grid;
  gap: 16px;
  margin: 16px 0 0;
}

.entry-intel-chart {
  display: grid;
  gap: 10px;
  padding: 12px;
  border-radius: 14px;
  border: 1px solid rgba(206, 219, 250, 0.16);
  background:
    linear-gradient(180deg, rgba(241, 247, 255, 0.08), rgba(241, 247, 255, 0.03)),
    linear-gradient(140deg, rgba(22, 28, 43, 0.9), rgba(10, 14, 24, 0.92));
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.entry-chart-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.entry-chart-kicker {
  margin: 0;
  font-size: 10px;
  letter-spacing: 0.8px;
  text-transform: uppercase;
  color: #b2c1de;
  font-weight: 800;
}

.entry-chart-title {
  display: block;
  margin-top: 1px;
  font-size: 13px;
  line-height: 1.3;
  color: #f2f6ff;
  font-weight: 600;
}

.entry-chart-score {
  font-size: 10px;
  color: #d5e0f6;
  border: 1px solid rgba(203, 219, 251, 0.22);
  border-radius: 999px;
  padding: 4px 9px;
  background: rgba(216, 230, 255, 0.1);
  white-space: nowrap;
}

.entry-heatmap-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 8px;
}

.entry-heatmap-cell {
  min-height: 56px;
  border-radius: 10px;
  border: 1px solid rgba(225, 234, 255, 0.12);
  background: rgba(210, 223, 252, calc(var(--heat-alpha, 0.35) * 0.22));
  display: grid;
  place-content: center;
  gap: 3px;
  text-align: center;
}

.entry-heatmap-cell.status-pass {
  background: rgba(123, 234, 177, calc(var(--heat-alpha, 0.35) * 0.38));
}

.entry-heatmap-cell.status-penalty,
.entry-heatmap-cell.status-active {
  background: rgba(247, 202, 128, calc(var(--heat-alpha, 0.35) * 0.36));
}

.entry-heatmap-cell.status-not-pass {
  background: rgba(238, 156, 181, calc(var(--heat-alpha, 0.35) * 0.38));
}

.entry-heatmap-stage {
  font-size: 10px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  color: #e7efff;
  font-weight: 700;
}

.entry-heatmap-value {
  font-size: 11px;
  color: #c9d6f2;
  font-weight: 700;
}

.entry-heatmap-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.entry-heatmap-legend .legend-item {
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 10px;
  letter-spacing: 0.35px;
  text-transform: uppercase;
  border: 1px solid rgba(225, 234, 255, 0.18);
  color: #d7e2f9;
}

.entry-heatmap-legend .legend-item.ok {
  background: rgba(123, 234, 177, 0.18);
}

.entry-heatmap-legend .legend-item.warn {
  background: rgba(247, 202, 128, 0.18);
}

.entry-heatmap-legend .legend-item.bad {
  background: rgba(238, 156, 181, 0.2);
}

.entry-heatmap-legend .legend-item.idle {
  background: rgba(196, 209, 238, 0.16);
}

.entry-intel-header {
  grid-template-columns: minmax(0, 1.6fr) minmax(260px, 0.9fr);
  align-items: start;
}

.entry-live-card {
  display: grid;
  gap: 6px;
  padding: 14px 16px;
  border-radius: 16px;
  border: 1px solid rgba(127, 161, 255, 0.16);
  background: linear-gradient(180deg, rgba(13, 17, 30, 0.98), rgba(8, 10, 16, 0.96));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
}

.entry-live-label {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: #7fa1ff;
}

.entry-live-pair {
  font-size: 20px;
  line-height: 1.1;
  color: #eef2ff;
}

.entry-live-meta,
.entry-live-reason {
  font-size: 11px;
  line-height: 1.45;
  color: #a0abc7;
}

.entry-live-reason {
  color: #c6d0eb;
}

.entry-gate-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.entry-gate-card {
  display: grid;
  gap: 10px;
  padding: 14px;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: linear-gradient(180deg, rgba(10, 13, 21, 0.96), rgba(6, 8, 15, 0.94));
  min-height: 168px;
}

.entry-gate-top {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
}

.entry-gate-stage {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: #8f95a3;
}

.entry-gate-status {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.8px;
  text-transform: uppercase;
  padding: 5px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.04);
  color: #a0abc7;
}

.entry-gate-card.status-pass .entry-gate-status {
  color: #00ff88;
  background: rgba(0, 255, 136, 0.12);
  border: 1px solid rgba(0, 255, 136, 0.25);
}

.entry-gate-card.status-not-pass .entry-gate-status {
  color: #ff5b89;
  background: rgba(255, 91, 137, 0.12);
  border: 1px solid rgba(255, 91, 137, 0.25);
}

.entry-gate-card.status-penalty .entry-gate-status {
  color: #f5a623;
  background: rgba(245, 166, 35, 0.12);
  border: 1px solid rgba(245, 166, 35, 0.25);
}

.entry-gate-card.status-active .entry-gate-status {
  color: #f5a623;
  background: rgba(245, 166, 35, 0.12);
  border: 1px solid rgba(245, 166, 35, 0.25);
}

.entry-gate-card.status-not-triggered .entry-gate-status {
  color: #93a0c9;
  background: rgba(147, 160, 201, 0.12);
  border: 1px solid rgba(147, 160, 201, 0.25);
}

.entry-gate-card h4 {
  margin: 0;
  font-size: 14px;
  line-height: 1.3;
  color: #eef2ff;
}

.entry-gate-copy {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: #9aa5c5;
}

.entry-gate-result {
  display: grid;
  gap: 4px;
}

.entry-gate-score {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.3px;
}

.entry-gate-score.up {
  color: #00ff88;
}

.entry-gate-score.down {
  color: #ff5b89;
}

.entry-gate-score.neutral {
  color: #bfcaff;
}

.entry-gate-detail {
  font-size: 11px;
  line-height: 1.45;
  color: #a0abc7;
}

.entry-gate-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-content: flex-start;
}

.entry-gate-chip {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.4px;
  padding: 5px 8px;
  border-radius: 999px;
  background: rgba(127, 161, 255, 0.1);
  border: 1px solid rgba(127, 161, 255, 0.18);
  color: #bfcaff;
}

.entry-coin-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.entry-coin-cell {
  display: grid;
  gap: 6px;
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(8, 10, 16, 0.82);
}

.entry-coin-cell span {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: #7f88a3;
}

.entry-coin-cell strong {
  font-size: 12px;
  line-height: 1.45;
  color: #eef2ff;
}

.dashboard-context-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin: 14px 16px 0;
}

.context-card {
  display: grid;
  gap: 6px;
  min-height: 104px;
  padding: 14px 15px;
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: linear-gradient(180deg, rgba(15, 19, 31, 0.96), rgba(9, 12, 21, 0.92));
}

.context-label {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1px;
  color: #7f88a3;
  text-transform: uppercase;
}

.context-value {
  font-size: 18px;
  line-height: 1.1;
  color: #eef2ff;
}

.context-meta {
  font-size: 11px;
  line-height: 1.35;
  color: #a0abc7;
}

.tone-good {
  border-color: rgba(0, 255, 136, 0.16);
  box-shadow: inset 0 1px 0 rgba(0, 255, 136, 0.05);
}

.tone-warn {
  border-color: rgba(245, 166, 35, 0.16);
  box-shadow: inset 0 1px 0 rgba(245, 166, 35, 0.05);
}

.tone-danger {
  border-color: rgba(255, 91, 137, 0.18);
  box-shadow: inset 0 1px 0 rgba(255, 91, 137, 0.05);
}

.tone-neutral {
  border-color: rgba(127, 161, 255, 0.14);
}

.scorecard-header {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(260px, 0.9fr);
  align-items: start;
  gap: 16px;
  margin-bottom: 16px;
}

.scorecard-sidecar {
  display: grid;
  justify-items: end;
  gap: 10px;
}

.scorecard-controls {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.scorecard-controls label {
  display: grid;
  gap: 4px;
}

.scorecard-controls label span {
  font-size: 10px;
  color: #8790a7;
  font-weight: 700;
}

.scorecard-controls select {
  background: #0f1320;
  border: 1px solid #222a3f;
  color: #d9e2ff;
  font-size: 11px;
  border-radius: 8px;
  padding: 5px 8px;
  min-width: 88px;
}

.scorecard-kicker {
  margin: 0;
  font-size: 10px;
  letter-spacing: 1.4px;
  color: #8f95a3;
  font-weight: 800;
}

.scorecard-header h3 {
  margin: 4px 0 0;
  font-size: 15px;
  font-weight: 800;
  color: #eef2ff;
}

.scorecard-helper {
  margin: 8px 0 0;
  max-width: 640px;
  font-size: 12px;
  line-height: 1.45;
  color: #94a0be;
}

.scorecard-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.chip {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.4px;
  border-radius: 999px;
  padding: 6px 10px;
}

.chip.pass {
  color: #00ff88;
  background: rgba(0, 255, 136, 0.12);
  border: 1px solid rgba(0, 255, 136, 0.35);
}

.chip.reject {
  color: #ff5b89;
  background: rgba(255, 91, 137, 0.12);
  border: 1px solid rgba(255, 91, 137, 0.35);
}

.chip.neutral {
  color: #9fb2ff;
  background: rgba(159, 178, 255, 0.12);
  border: 1px solid rgba(159, 178, 255, 0.35);
}

.scorecard-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.scorecard-span-2 {
  grid-column: span 2;
}

.scorecard-card {
  background: rgba(8, 10, 16, 0.8);
  border: 1px solid #1a1f2b;
  border-radius: 14px;
  padding: 12px;
}

.scorecard-brief-card {
  background: linear-gradient(135deg, rgba(18, 24, 40, 0.96), rgba(13, 17, 30, 0.92));
}

.scorecard-mini-label {
  margin: 0 0 8px;
  font-size: 10px;
  letter-spacing: 1px;
  font-weight: 800;
  color: #7fa1ff;
}

.scorecard-brief-title {
  font-size: 14px;
  color: #eef2ff;
}

.scorecard-brief-copy {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.5;
  color: #cfd8f4;
}

.scorecard-brief-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.scorecard-brief-item {
  display: grid;
  gap: 3px;
  padding: 8px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(127, 161, 255, 0.08);
}

.scorecard-brief-item-title {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.6px;
  color: #7fa1ff;
}

.scorecard-brief-item-copy,
.scorecard-subtext {
  font-size: 11px;
  line-height: 1.4;
  color: #8f9ab5;
}

.scorecard-card h4 {
  margin: 0 0 10px;
  font-size: 11px;
  color: #8f95a3;
  font-weight: 800;
  letter-spacing: 0.8px;
  text-transform: uppercase;
}

.scorecard-card ul {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 8px;
}

.scorecard-card li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.scorecard-factor-copy,
.scorecard-nearmiss-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.scorecard-nearmiss-item {
  align-items: flex-start;
}

.factor-name {
  font-size: 11px;
  color: #c9d3ef;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.factor-metric {
  font-size: 11px;
  font-weight: 800;
  min-width: 52px;
  text-align: right;
}

.factor-metric.up {
  color: #00ff88;
}

.factor-metric.down {
  color: #ff5b89;
}

.muted {
  color: #72798a;
  font-size: 11px;
}

.scorecard-state {
  font-size: 12px;
  color: #9aa5c5;
  padding: 6px 0;
}

.scorecard-state.error {
  color: #ff5b89;
}

.scorecard-trend-card {
  grid-column: span 1;
}

@media (max-width: 1200px) {
  .scorecard-header {
    grid-template-columns: 1fr;
  }

  .scorecard-sidecar {
    justify-items: start;
  }

  .scorecard-controls,
  .scorecard-summary {
    justify-content: flex-start;
  }
}

.trend-wrap {
  display: grid;
  gap: 8px;
}

.trend-svg {
  width: 100%;
  height: 80px;
  background: rgba(7, 11, 22, 0.8);
  border-radius: 8px;
}

.trend-line {
  fill: none;
  stroke: #7fa1ff;
  stroke-width: 2;
}

.trend-area {
  fill: rgba(127, 161, 255, 0.18);
  stroke: none;
}

.trend-meta {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: #7f88a3;
}

.gauge-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.gauge-title { font-size: 10px; font-weight: 800; color: #444; }
.gauge-track {
  height: 4px;
  background: #1a1c24;
  border-radius: 2px;
  flex: 1;
  margin: 0 16px;
  overflow: hidden;
}

.gauge-fill { height: 100%; transition: width 0.5s ease; }
.gauge-bar-row { display: flex; align-items: center; }
.gauge-pct { font-size: 12px; font-weight: 800; min-width: 40px; text-align: right; }

.ld-footer {
  height: 48px;
  background: #0a0c14;
  border-top: 1px solid #1a1c24;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  font-size: 11px;
  font-weight: 700;
  color: #555;
  letter-spacing: 0.5px;
}

.footer-stats {
  display: flex;
  gap: 20px;
}

.stat-item {
  font-size: 10px;
  font-weight: 800;
  color: #444;
  letter-spacing: 1px;
}

.stat-item .v {
  color: #888;
}

.footer-right {
  display: flex;
  gap: 24px;
}

.gold { color: #f5a623; }

/* ─── Responsive ──────────────────────────────────── */
@media (max-width: 1024px) {
  .ld-main { grid-template-columns: 1fr; overflow-y: auto; }
}



/* ─── Mobile Bottom Nav ───────────────────────────── */
.mob-bottom-nav {
  display: none; /* shown via media query */
  height: 58px;
  background: rgba(10, 12, 20, 0.97);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-top: 1px solid #1a1c24;
  flex-shrink: 0;
  padding-bottom: env(safe-area-inset-bottom);
  height: calc(58px + env(safe-area-inset-bottom));
}
.mob-nav-btn {
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: transparent;
  border: none;
  color: #555;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  cursor: pointer;
  padding: 8px 0;
  transition: color 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.mob-nav-btn svg { width: 20px; height: 20px; }
.mob-nav-btn.active { color: #f5a623; }
.mob-nav-btn.active svg { filter: drop-shadow(0 0 5px rgba(245, 166, 35, 0.7)); }
.mob-badge {
  position: absolute;
  top: 6px;
  right: calc(50% - 18px);
  background: #ff3366;
  color: #fff;
  font-size: 9px;
  font-weight: 800;
  min-width: 16px;
  height: 16px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
}

@media (max-width: 768px) {
  .live-dashboard {
    height: 100%;
    height: 100dvh;
    overflow-x: hidden;
    overflow-y: auto;
  }

  .ld-main {
    grid-template-columns: 1fr;
    padding: 0;
    gap: 0;
    overflow: hidden;
    flex: 1;
    min-height: 0;
  }

  .ld-panel {
    border-radius: 0;
    border-left: none;
    border-right: none;
    border-top: none;
  }

  /* Tab-based panel visibility */
  .mob-hidden { display: none !important; }
  .desktop-only { display: none !important; }
  .mobile-only { display: flex !important; flex-direction: column; }

  /* Full-height panel on mobile */
  .positions-panel {
    height: 100%;
    overflow: hidden;
  }
  .positions-cards {
    flex: 1;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  .panels-row {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    display: flex;
  }

  .right-panel {
    height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .chart-wrap {
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  /* On mobile 'chart' tab: show chart fullscreen */
  .tab-chart .right-panel { display: flex !important; }
  .tab-chart .orders-wrap { display: none !important; }
  .tab-chart .chart-wrap { flex: 1; }

  /* Footer hidden on mobile (replaced by bottom nav) */
  .ld-footer { display: none; }

  /* Show mobile bottom nav */
  .mob-bottom-nav {
    display: flex !important;
    align-items: center;
    justify-content: space-around;
  }

  /* Safety gauge compact on mobile */
  .safety-gauge-wrap { padding: 10px 16px; }
  .gauge-title { font-size: 9px; }
  .gauge-net { font-size: 11px; }

  .scorecard-panel {
    margin: 10px 10px 0;
    padding: 10px;
  }

  .entry-intel-header {
    grid-template-columns: 1fr;
  }

  .entry-gate-grid,
  .entry-coin-strip {
    grid-template-columns: 1fr;
  }

  .entry-heatmap-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .dashboard-context-strip {
    grid-template-columns: 1fr;
    margin: 10px 10px 0;
    gap: 10px;
  }

  .scorecard-grid {
    grid-template-columns: 1fr;
  }

  .scorecard-span-2 {
    grid-column: span 1;
  }

  .scorecard-brief-list {
    grid-template-columns: 1fr;
  }

  .scorecard-summary {
    justify-content: flex-start;
  }

  .scorecard-controls {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .scorecard-sidecar {
    justify-items: start;
  }

  .context-value {
    font-size: 16px;
  }
}

/* Desktop: hide mobile-only elements */
@media (min-width: 769px) {
  .desktop-only { display: block !important; }
  .mobile-only { display: none !important; }
  .mob-bottom-nav { display: none !important; }
  .mob-hidden { display: flex !important; }

  .positions-panel {
    max-height: clamp(360px, 56vh, 620px);
    align-self: start;
  }

  .right-panel {
    min-height: clamp(460px, 62vh, 760px);
  }

  /* Desktop strict layout: below safety gauge only left-right 2 columns */
  .intel-section {
    display: none !important;
  }
}

/* ─── Intel Section (desktop strip + mobile full) ────── */
.intel-section {
  margin: 0 16px 16px;
  max-height: 190px;
  flex-shrink: 0;
}

@media (max-width: 768px) {
  /* On mobile 'intel' tab: show intel panel fullscreen, hide ld-main */
  .tab-intel .ld-main { display: none !important; }
  .tab-intel .intel-section {
    display: flex !important;
    flex-direction: column;
    flex: 1;
    margin: 0;
    max-height: none;
    border-radius: 0;
    border-left: none;
    border-right: none;
    border-top: none;
  }
}
</style>
