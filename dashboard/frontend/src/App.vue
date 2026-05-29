<template>
  <div class="app" :class="{ 'mobile-tab-activity': mobileTab === 'activity', 'mobile-tab-chart': mobileTab === 'chart' }">
    <Header :stats="store.stats" :connected="store.connected" />
    
    <NotificationCenter />
    <RiskGuardPopup :socket="store.socket" />
    <ApprovalGateModal />
    <StrategicBreakModal />

    <section class="dashboard-context-strip">
      <article v-for="card in dryContextCards" :key="card.label" class="context-card" :class="`tone-${card.tone}`">
        <span class="context-label">{{ card.label }}</span>
        <strong class="context-value">{{ card.value }}</strong>
        <span class="context-meta">{{ card.meta }}</span>
      </article>
    </section>

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

    <!-- Mobile Bottom Navigation -->
    <nav class="mobile-bottom-nav show-mobile">
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'activity' }" @click="mobileTab = 'activity'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 10h16M4 14h10"/></svg>
        <span>Feed</span>
      </button>
      <button class="mob-nav-btn" :class="{ active: mobileTab === 'chart' }" @click="mobileTab = 'chart'">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        <span>Chart</span>
      </button>
    </nav>

    <footer class="app-footer hide-mobile">
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
          <span class="stat-item">LATENCY: <span class="v">{{ store.latency || 0 }}ms</span></span>
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

// Mobile tab state
const mobileTab = ref('activity')

const newsHeadlines = ref([])
const startTime = ref(Date.now())
const now = ref(Date.now())
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
  { stage: '1', code: 'POSITION_OR_BLACKLIST', title: 'Open Position / Blacklist Guard', copy: 'Reject pair yang masih open atau masuk blacklist.', indicators: ['Position state', 'Blacklist'], tone: 'danger', scoreKeys: ['POSITION_OR_BLACKLIST'] },
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
let newsIntervalId = null
let clockIntervalId = null
let scorecardIntervalId = null

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

const dryContextCards = computed(() => {
  const summary = scorecardSummary.value || {}
  const trackedPairs = Array.isArray(store.radarPairs) ? store.radarPairs.length : 0
  const feedState = newsHeadlines.value.length > 0 ? `${newsHeadlines.value.length} headlines` : `${trackedPairs} radar pairs`
  // Find latest LLM entry decision from llmDecisions feed
  const llmDecisions = Array.isArray(store.llmDecisions) ? store.llmDecisions : []
  const latestLlmEntry = llmDecisions.find(d => String(d.side || '').includes('LLM_ENTRY'))
  const llmDecisionLabel = latestLlmEntry
    ? `${latestLlmEntry.message || 'HOLD'} ${String(latestLlmEntry.pair || '').toUpperCase()}`
    : 'Waiting…'
  const llmMeta = latestLlmEntry
    ? `Gate decision ${(latestLlmEntry.created_at || '').slice(11, 19) || ''}`
    : 'Menunggu LLM entry gate pertama'
  const llmTone = latestLlmEntry
    ? (String(latestLlmEntry.message || '').toUpperCase() === 'BUY' ? 'good'
        : (String(latestLlmEntry.message || '').toUpperCase() === 'AVOID' ? 'danger' : 'neutral'))
    : 'neutral'
  return [
    {
      label: 'Mode',
      value: 'Dry Run',
      meta: 'Semua eksekusi virtual, market feed tetap real-time.',
      tone: 'good',
    },
    {
      label: 'Observation Window',
      value: `${scorecardHours.value}H`,
      meta: scorecardPair.value ? `Fokus ${formatPair(scorecardPair.value)}` : 'Semua pair dipantau',
      tone: 'neutral',
    },
    {
      label: 'Readiness',
      value: `${Number(summary.pass_rate_pct || 0).toFixed(1)}%`,
      meta: `${Number(summary.sample_size || 0)} evaluasi, ${Number(summary.pass_count || 0)} lolos`,
      tone: Number(summary.pass_rate_pct || 0) >= 40 ? 'good' : 'warn',
    },
    {
      label: 'Market Pulse',
      value: feedState,
      meta: 'Gunakan sebagai konteks, bukan trigger entry.',
      tone: 'neutral',
    },
    {
      label: 'LLM Entry Gate',
      value: llmDecisionLabel,
      meta: llmMeta,
      tone: llmTone,
    },
  ]
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

async function fetchNews() {
  try {
    const resp = await fetch('/api/news', { headers: getAuthHeaders() })
    if (resp.ok) {
      const items = await resp.json()
      if (Array.isArray(items)) newsHeadlines.value = items
    }
  } catch (e) {}
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
    const resp = await fetch(`/api/entry-scorecards?${params.toString()}`, { headers: getAuthHeaders() })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    scorecardSummary.value = data.summary || null
    scorecardFactors.value = Array.isArray(data.factors) ? data.factors : []
    scorecardRecent.value = Array.isArray(data.recent_decisions) ? data.recent_decisions : []
    scorecardTrendSeriesData.value = Array.isArray(data.trend_series) ? data.trend_series : []
    scorecardPairOptions.value = Array.isArray(data.pair_options) ? data.pair_options : []
  } catch {
    scorecardError.value = 'Failed to load scorecard analytics'
  } finally {
    scorecardLoading.value = false
  }
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
  return pair.replace(/idr$/i, '').replace(/usdt$/i, '').toUpperCase()
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
  fetchEntryScorecards()
  newsIntervalId = setInterval(fetchNews, 5 * 60 * 1000)
  scorecardIntervalId = setInterval(fetchEntryScorecards, 15000)
  clockIntervalId = setInterval(() => { now.value = Date.now() }, 1000)
})

// Global FORCE_LOGOUT listener — redirect to login when session is invalidated
watch(() => store.forceLogout, (triggered) => {
  if (triggered) {
    router.push('/login')
  }
})

onUnmounted(() => {
  if (newsIntervalId) {
    clearInterval(newsIntervalId)
    newsIntervalId = null
  }
  if (clockIntervalId) {
    clearInterval(clockIntervalId)
    clockIntervalId = null
  }
  if (scorecardIntervalId) {
    clearInterval(scorecardIntervalId)
    scorecardIntervalId = null
  }
  // Ensure no dangling listeners when user leaves during drag-resize.
  stopResize()
})

watch([scorecardPair, scorecardHours], () => {
  fetchEntryScorecards()
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
  overflow-x: hidden;
  overflow-y: auto;
}

.app {
  height: 100%;
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow-x: hidden;
  overflow-y: auto;
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

.scorecard-panel {
  margin: 14px 16px;
  flex-shrink: 0;
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

.factor-metric.up { color: #00ff88; }
.factor-metric.down { color: #ff5b89; }

.muted {
  color: #72798a;
  font-size: 11px;
}

.scorecard-state {
  font-size: 12px;
  color: #9aa5c5;
  padding: 6px 0;
}

.scorecard-state.error { color: #ff5b89; }

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

.left-panel {
  width: var(--left-width);
  min-width: 300px;
  min-height: 0;
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
  min-height: 0;
  background: var(--bg-panel);
  border-radius: 16px;
  border: 1px solid var(--border-dim);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.chart-wrap {
  flex: 1;
  min-height: 0;
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
  flex-shrink: 0;
}

.footer-left {
  flex: 0 0 240px;
  min-width: 200px;
}
.footer-right {
  flex: 0 0 240px;
  min-width: 180px;
  display: flex;
  justify-content: flex-end;
}

.mode-container {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.mode-badge {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1px;
  color: var(--accent-purple);
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.info-icon {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 1px solid rgba(163, 113, 247, 0.4);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 9px;
  line-height: 1;
}

.mode-tooltip {
  position: absolute;
  left: 0;
  bottom: calc(100% + 10px);
  width: 320px;
  background: #11141d;
  border: 1px solid #252938;
  border-radius: 12px;
  padding: 10px 12px;
  opacity: 0;
  transform: translateY(8px);
  pointer-events: none;
  transition: opacity 0.2s ease, transform 0.2s ease;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
  z-index: 20;
}

.mode-container:hover .mode-tooltip {
  opacity: 1;
  transform: translateY(0);
}

.tooltip-header {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1px;
  color: #f5a623;
  margin-bottom: 8px;
}

.tooltip-body {
  display: grid;
  gap: 6px;
}

.tooltip-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: #c9d1d9;
  font-size: 11px;
  line-height: 1.35;
}

.t-bullet {
  font-size: 11px;
  line-height: 1.35;
}

.t-text strong {
  color: #fff;
}

.footer-center {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.ticker-tape {
  width: 100%;
  white-space: nowrap;
  overflow: hidden;
}

.ticker-content {
  display: inline-block;
  white-space: nowrap;
  min-width: 100%;
}

.ticker-item {
  display: inline-block;
  margin-right: 40px;
  font-size: 12px;
  font-family: 'JetBrains Mono', monospace;
}

.t-source { color: #8b949e; margin-right: 6px; }
.t-title { color: #d2d7de; }
.t-sep { color: #6e7681; margin-left: 10px; }


.t-pair { color: #888; margin-right: 8px; }
.t-price { color: #fff; font-weight: 700; }
.t-change.up { color: var(--accent-green); }
.t-change.down { color: var(--accent-red); }

.system-stats {
  display: flex;
  gap: 20px;
  white-space: nowrap;
}

.stat-item {
  font-size: 10px;
  font-weight: 700;
  color: #444;
  letter-spacing: 1px;
}

.stat-item .v { color: #888; }

@media (max-width: 1300px) {
  .app-footer {
    gap: 14px;
    padding: 0 14px;
  }

  .footer-left {
    flex-basis: 190px;
    min-width: 160px;
  }

  .footer-right {
    flex-basis: 180px;
    min-width: 150px;
  }

  .mode-badge {
    font-size: 9px;
    letter-spacing: 0.7px;
  }

  .system-stats {
    gap: 12px;
  }

  .stat-item {
    font-size: 9px;
    letter-spacing: 0.7px;
  }
}

@media (max-width: 1080px) {
  .footer-left {
    display: none;
  }

  .footer-center {
    flex: 1;
  }

  .footer-right {
    flex-basis: auto;
    min-width: 150px;
  }
}

@media (max-width: 768px) {
  body { overflow-x: hidden; overflow-y: auto; }

  .hide-mobile { display: none !important; }
  .show-mobile { display: flex !important; }

  .app {
    height: 100%;
    height: 100dvh;
  }

  .main-grid {
    flex-direction: column;
    padding: 8px;
    overflow-y: auto;
    gap: 8px;
    min-height: 0;
  }

  .panels-row {
    flex: 0 0 auto;
    min-height: clamp(400px, 60vh, 700px);
    overflow: hidden;
    display: flex;
  }

  /* Only one panel visible at a time via bottom nav */
  .left-panel {
    width: 100% !important;
    min-width: 0;
    height: 100%;
    border-radius: 0;
    border-left: none;
    border-right: none;
    border-top: 1px solid var(--border-dim);
    border-radius: 12px;
  }

  .right-panel {
    width: 100% !important;
    height: 100%;
    border-radius: 0;
    border-left: none;
    border-right: none;
    border-top: 1px solid var(--border-dim);
    border-radius: 12px;
    display: flex;
    flex-direction: column;
  }

  .chart-wrap {
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  .mobile-hidden {
    display: none !important;
  }

  /* Mobile Bottom Navigation */
  .mobile-bottom-nav {
    height: 56px;
    height: calc(56px + env(safe-area-inset-bottom));
    padding-bottom: env(safe-area-inset-bottom);
    background: rgba(10, 12, 20, 0.95);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-top: 1px solid var(--border-dim);
    display: flex;
    align-items: center;
    justify-content: space-around;
    z-index: 200;
    flex-shrink: 0;
  }

  .mob-nav-btn {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    background: transparent;
    border: none;
    color: #555;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    cursor: pointer;
    padding: 8px 0;
    transition: color 0.2s;
    -webkit-tap-highlight-color: transparent;
  }

  .mob-nav-btn svg {
    width: 20px;
    height: 20px;
    stroke: currentColor;
  }

  .mob-nav-btn.active {
    color: var(--accent-purple);
  }

  .mob-nav-btn.active svg {
    filter: drop-shadow(0 0 6px var(--accent-purple));
  }

  .mode-tooltip {
    display: none;
  }

  .scorecard-panel {
    margin: 0 8px 8px;
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

  .context-value {
    font-size: 16px;
  }

  .scorecard-controls {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .scorecard-sidecar {
    justify-items: start;
  }
}

/* Desktop: hide mobile-only elements */
@media (min-width: 769px) {
  .show-mobile { display: none !important; }
  .mobile-hidden { display: flex !important; }
}

</style>
