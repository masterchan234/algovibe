<template>
  <div class="report-portal">

    <PortalHeader />

    <main class="portal-main">
      <div class="content-container">

        <!-- Hero -->
        <section class="report-hero animate-rise">
          <p class="hero-kicker">QUANTITATIVE VALIDATION — BINANCE SPOT — 30-DAY WINDOW</p>
          <h1>BACKTEST PERFORMANCE REPORT</h1>
          <p class="hero-desc">
            Historical simulation of the Algovibe signal engine (v3.0) against 30 days of
            real Binance OHLCV 1-minute candles. Same entry gates, same risk parameters, same exit mechanics.
            <strong>Note:</strong> The live bot's primary edge — real-time order book bid/ask depth — is
            unavailable in historical OHLCV data. Backtest results represent a
            <strong>conservative lower bound</strong>; live performance with active order book signals
            is expected to exceed these figures.
          </p>
          <div class="hero-stats" v-if="summary.profiles.length">
            <div class="stat-badge">
              <span class="label">TOTAL SIMULATED TRADES</span>
              <span class="value cyan">{{ totalTrades }}</span>
            </div>
            <div class="stat-badge">
              <span class="label">BEST WIN RATE</span>
              <span class="value green">{{ bestWinRate }}%</span>
            </div>
            <div class="stat-badge">
              <span class="label">BEST PROFIT FACTOR</span>
              <span class="value amber">{{ bestProfitFactor }}</span>
            </div>
            <div class="stat-badge">
              <span class="label">DATASET</span>
              <span class="value">{{ pairCount }} PAIRS · 1M CANDLES</span>
            </div>
          </div>
          <div v-else-if="loading" class="hero-stats">
            <div class="stat-badge loading-pulse">
              <span class="label">LOADING RESULTS</span>
              <span class="value cyan">—</span>
            </div>
          </div>
        </section>

        <!-- Methodology note -->
        <div class="method-banner">
          <span class="method-icon">⚙</span>
          <span>
            <strong>Methodology:</strong> Signal engine v3.0 — 3 momentum indicators
            (Price Velocity ×3.5 + Volume Ratio ×3.5 + Order Book Pressure ×3.0, max 10.0).
            BUY threshold ≥ 6.5. <strong>OHLCV limitation:</strong> The live bot reads real-time
            order book depth (bid/ask walls) as its 3rd signal — a forward-looking indicator that
            confirms momentum before the candle closes. This edge is inherently absent from historical
            data. The backtest substitutes a candle-structure proxy (close position within high-low range),
            which fires <em>after</em> the move and produces conservative entry timing.
          </span>
        </div>

        <!-- Error state -->
        <div v-if="error" class="error-banner">
          <strong>[ERROR]</strong> {{ error }}
        </div>

        <!-- Profile Performance Cards -->
        <section v-if="summary.profiles.length" class="section animate-fade">
          <div class="section-header">
            <h2>Strategy Profile Results</h2>
            <p>Each profile represents a distinct risk/reward configuration. Results are aggregate across all traded pairs within the 30-day window.</p>
          </div>
          <div class="profiles-grid">
            <div
              v-for="p in summary.profiles"
              :key="p.profile_name"
              class="profile-card"
              :class="{ 'card-positive': p.total_pnl_pct > 0, 'card-negative': p.total_pnl_pct < 0 }"
            >
              <div class="profile-header">
                <span class="profile-name">{{ p.profile_name }}</span>
                <span class="profile-pnl" :class="p.total_pnl_pct >= 0 ? 'positive' : 'negative'">
                  {{ p.total_pnl_pct >= 0 ? '+' : '' }}{{ fmt2(p.total_pnl_pct) }}%
                </span>
              </div>
              <div class="profile-window">
                {{ fmtDate(p.window_start) }} → {{ fmtDate(p.window_end) }}
              </div>
              <div class="metrics-row">
                <div class="metric">
                  <span class="m-label">TRADES</span>
                  <span class="m-val">{{ p.total_trades }}</span>
                </div>
                <div class="metric">
                  <span class="m-label">WIN RATE</span>
                  <span class="m-val green">{{ fmt1(p.win_rate * 100) }}%</span>
                </div>
                <div class="metric">
                  <span class="m-label">PROFIT FACTOR</span>
                  <span class="m-val amber">{{ fmt2(p.profit_factor) }}</span>
                </div>
                <div class="metric">
                  <span class="m-label">MAX DD</span>
                  <span class="m-val red">{{ fmt2(p.max_drawdown_pct) }}%</span>
                </div>
                <div class="metric">
                  <span class="m-label">SHARPE</span>
                  <span class="m-val cyan">{{ fmt2(p.sharpe_ratio) }}</span>
                </div>
                <div class="metric">
                  <span class="m-label">AVG HOLD</span>
                  <span class="m-val">{{ fmtDuration(p.avg_trade_duration_sec) }}</span>
                </div>
              </div>
              <div class="capital-row">
                <span>Capital: <strong>{{ fmtIdr(p.initial_capital_idr) }}</strong></span>
                <span class="arrow">→</span>
                <span :class="p.final_capital_idr >= p.initial_capital_idr ? 'positive' : 'negative'">
                  <strong>{{ fmtIdr(p.final_capital_idr) }}</strong>
                </span>
              </div>
              <div class="streak-row">
                <span class="streak-win">Max win streak: {{ p.max_consecutive_wins }}</span>
                <span class="streak-loss">Max loss streak: {{ p.max_consecutive_losses }}</span>
              </div>
            </div>
          </div>
        </section>

        <!-- Equity Curve -->
        <section v-if="summary.equity_curve.length" class="section animate-fade">
          <div class="section-header">
            <h2>Cumulative PnL Curve</h2>
            <p>Daily sum of all simulated trades. Aggregate across all active profiles and pairs.</p>
          </div>
          <div class="equity-chart-wrap">
            <div class="equity-chart">
              <div class="chart-y-labels">
                <span>{{ equityMax }}%</span>
                <span>0%</span>
                <span>{{ equityMin }}%</span>
              </div>
              <div class="chart-bars">
                <div
                  v-for="(day, i) in summary.equity_curve"
                  :key="i"
                  class="chart-col"
                  :title="`${day.day?.slice(0,10)}: ${fmt2(day.daily_pnl_pct)}% (${day.trades} trades)`"
                >
                  <div
                    class="chart-bar"
                    :class="day.daily_pnl_pct >= 0 ? 'bar-positive' : 'bar-negative'"
                    :style="barStyle(day.daily_pnl_pct)"
                  ></div>
                </div>
              </div>
            </div>
            <div class="chart-legend">
              <span class="legend-pos">▮ Daily gain</span>
              <span class="legend-neg">▮ Daily loss</span>
            </div>
          </div>
        </section>

        <!-- Top Pairs & Exit Reasons side by side -->
        <section v-if="summary.top_pairs.length || summary.exit_reasons.length" class="section animate-fade">
          <div class="two-col">

            <!-- Top Pairs -->
            <div v-if="summary.top_pairs.length" class="panel">
              <h3>Top Pairs by Win Rate</h3>
              <table class="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>PAIR</th>
                    <th>WIN RATE</th>
                    <th>TRADES</th>
                    <th>TOTAL PNL%</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(p, i) in summary.top_pairs" :key="p.pair">
                    <td class="rank">{{ i + 1 }}</td>
                    <td class="pair-cell">{{ p.pair.toUpperCase().replace('USDT','') }}<span class="quote">/USDT</span></td>
                    <td class="green">{{ fmt1(p.avg_win_rate * 100) }}%</td>
                    <td>{{ p.trades }}</td>
                    <td :class="p.total_pnl_pct >= 0 ? 'positive' : 'negative'">
                      {{ p.total_pnl_pct >= 0 ? '+' : '' }}{{ fmt2(p.total_pnl_pct) }}%
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Exit Reasons -->
            <div v-if="summary.exit_reasons.length" class="panel">
              <h3>Exit Reason Breakdown</h3>
              <div class="exit-list">
                <div
                  v-for="r in summary.exit_reasons"
                  :key="r.exit_reason"
                  class="exit-row"
                >
                  <span class="exit-label" :class="exitClass(r.exit_reason)">
                    {{ r.exit_reason || 'UNKNOWN' }}
                  </span>
                  <div class="exit-bar-wrap">
                    <div
                      class="exit-bar"
                      :class="exitClass(r.exit_reason)"
                      :style="{ width: exitPct(r.cnt) + '%' }"
                    ></div>
                  </div>
                  <span class="exit-count">{{ r.cnt }}</span>
                  <span class="exit-pct">{{ exitPct(r.cnt) }}%</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- Disclaimer -->
        <section class="disclaimer animate-fade">
          <h4>IMPORTANT DISCLAIMER</h4>
          <p>
            Past backtest performance does not guarantee future results. Backtesting simulates
            historical conditions using the same signal logic as the live bot, but does not
            replicate real-time order book depth, slippage, network latency, or partial fill
            mechanics. Results shown here represent a <em>conservative lower bound</em> —
            live bot performance with real order book data is expected to be equal or better.
            All values shown in USDT-equivalent unless otherwise stated.
            This report is for informational purposes only and does not constitute financial advice.
          </p>
        </section>

      </div>
    </main>

    <footer class="portal-footer">
      <div class="footer-container">
        <p>© 2026 Algovibe Intelligence — Quantitative Crypto Scalping System</p>
      </div>
    </footer>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import PortalHeader from '../components/PortalHeader.vue'

const loading = ref(true)
const error = ref(null)
const summary = ref({ profiles: [], top_pairs: [], total_pairs: 0, exit_reasons: [], equity_curve: [] })

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

onMounted(async () => {
  document.title = 'Backtest Report | Algovibe Intelligence'
  try {
    const res = await fetch(`${backendUrl}/api/backtest/public-summary`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    summary.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

const totalTrades = computed(() =>
  summary.value.profiles.reduce((s, p) => s + (p.total_trades || 0), 0)
)
const pairCount = computed(() => summary.value.total_pairs || summary.value.top_pairs.length)
const bestWinRate = computed(() => {
  const rates = summary.value.profiles.map(p => p.win_rate || 0)
  return fmt1(Math.max(...rates, 0) * 100)
})
const bestProfitFactor = computed(() => {
  const pfs = summary.value.profiles.map(p => p.profit_factor || 0)
  return fmt2(Math.max(...pfs, 0))
})

const equityMax = computed(() => {
  const vals = summary.value.equity_curve.map(d => d.daily_pnl_pct)
  return fmt2(Math.max(...vals, 0))
})
const equityMin = computed(() => {
  const vals = summary.value.equity_curve.map(d => d.daily_pnl_pct)
  return fmt2(Math.min(...vals, 0))
})
const equityRange = computed(() => {
  const vals = summary.value.equity_curve.map(d => Math.abs(d.daily_pnl_pct))
  return Math.max(...vals, 0.01)
})
function barStyle(pnl) {
  const h = Math.min((Math.abs(pnl) / equityRange.value) * 100, 100)
  return { height: `${h}%` }
}

const totalExits = computed(() =>
  summary.value.exit_reasons.reduce((s, r) => s + (r.cnt || 0), 0)
)
function exitPct(cnt) {
  if (!totalExits.value) return 0
  return Math.round((cnt / totalExits.value) * 100)
}
function exitClass(reason) {
  if (!reason) return ''
  const r = reason.toUpperCase()
  if (r === 'TP') return 'exit-tp'
  if (r === 'SL') return 'exit-sl'
  if (r.includes('TRAILING')) return 'exit-trailing'
  if (r.includes('SIGNAL')) return 'exit-signal'
  return 'exit-other'
}

function fmt1(v) { return v != null && !isNaN(v) ? Number(v).toFixed(1) : '—' }
function fmt2(v) { return v != null && !isNaN(v) ? Number(v).toFixed(2) : '—' }
function fmtDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}
function fmtDuration(sec) {
  if (!sec) return '—'
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.round(sec / 60)}m`
  return `${(sec / 3600).toFixed(1)}h`
}
function fmtIdr(v) {
  if (!v) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(v)
}
</script>

<style scoped>
/* ── Layout ─────────────────────────────────────────────────────────────── */
.report-portal {
  width: 100vw;
  min-height: 100vh;
  background:
    radial-gradient(circle at 15% 10%, rgba(35, 77, 102, 0.35), transparent 35%),
    radial-gradient(circle at 85% 25%, rgba(46, 102, 57, 0.25), transparent 30%),
    linear-gradient(180deg, #0c1119 0%, #0b1411 100%);
  color: #d7dde3;
  font-family: 'IBM Plex Sans', 'Space Grotesk', 'Segoe UI', sans-serif;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
}
.content-container, .footer-container {
  max-width: 1160px;
  margin: 0 auto;
  width: 100%;
  padding: 0 28px;
}
.portal-main { flex: 1; padding-bottom: 80px; }

/* ── Hero ─────────────────────────────────────────────────────────────── */
.report-hero {
  padding: 72px 0 40px;
  border-bottom: 1px solid rgba(133, 168, 192, 0.12);
  margin-bottom: 32px;
}
.hero-kicker {
  font-size: 10px;
  letter-spacing: 3px;
  color: #5dc6e7;
  font-weight: 700;
  text-transform: uppercase;
  margin: 0 0 12px;
}
.report-hero h1 {
  font-size: clamp(28px, 4vw, 48px);
  font-weight: 900;
  letter-spacing: 4px;
  color: #f4f8fb;
  margin: 0 0 20px;
  line-height: 1.1;
}
.hero-desc {
  font-size: 15px;
  color: #8fa3b0;
  max-width: 740px;
  line-height: 1.7;
  margin: 0 0 36px;
}
.hero-stats {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.stat-badge {
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(133, 168, 192, 0.2);
  border-radius: 6px;
  padding: 14px 20px;
  min-width: 160px;
}
.stat-badge .label {
  display: block;
  font-size: 9px;
  letter-spacing: 2px;
  color: #6b8394;
  text-transform: uppercase;
  margin-bottom: 6px;
  font-weight: 700;
}
.stat-badge .value {
  font-size: 20px;
  font-weight: 800;
  letter-spacing: 1px;
  color: #d7dde3;
}
.stat-badge .value.cyan  { color: #5dc6e7; }
.stat-badge .value.green { color: #4eca7e; }
.stat-badge .value.amber { color: #e8a44a; }
.loading-pulse { opacity: 0.5; animation: pulse 1.5s infinite; }
@keyframes pulse { 0%, 100% { opacity: 0.5; } 50% { opacity: 1; } }

/* ── Methodology banner ─────────────────────────────────────────────── */
.method-banner {
  background: rgba(93, 198, 231, 0.07);
  border: 1px solid rgba(93, 198, 231, 0.2);
  border-radius: 6px;
  padding: 14px 20px;
  font-size: 13px;
  color: #8fa3b0;
  line-height: 1.6;
  margin-bottom: 48px;
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.method-icon { font-size: 16px; margin-top: 2px; flex-shrink: 0; }
.error-banner {
  background: rgba(200, 60, 60, 0.1);
  border: 1px solid rgba(200, 60, 60, 0.3);
  border-radius: 6px;
  padding: 14px 20px;
  color: #e57373;
  margin-bottom: 24px;
}

/* ── Section ─────────────────────────────────────────────────────────── */
.section { margin-bottom: 64px; }
.section-header { margin-bottom: 28px; }
.section-header h2 {
  font-size: 22px;
  font-weight: 800;
  letter-spacing: 2px;
  color: #f4f8fb;
  margin: 0 0 8px;
}
.section-header p {
  font-size: 13px;
  color: #6b8394;
  max-width: 600px;
  line-height: 1.6;
  margin: 0;
}

/* ── Profile Cards ───────────────────────────────────────────────────── */
.profiles-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 20px;
}
.profile-card {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(133,168,192,0.15);
  border-radius: 8px;
  padding: 24px;
  transition: border-color 0.2s;
}
.profile-card.card-positive { border-color: rgba(78,202,126,0.25); }
.profile-card.card-negative { border-color: rgba(200,80,80,0.2); }
.profile-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 6px;
}
.profile-name {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 1.5px;
  color: #c3d4df;
  text-transform: uppercase;
}
.profile-pnl { font-size: 24px; font-weight: 900; }
.profile-window {
  font-size: 10px;
  color: #5a7280;
  letter-spacing: 1px;
  margin-bottom: 20px;
  text-transform: uppercase;
}
.metrics-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin-bottom: 16px;
}
.metric { display: flex; flex-direction: column; gap: 4px; }
.m-label { font-size: 9px; letter-spacing: 1.5px; color: #4e6472; text-transform: uppercase; font-weight: 700; }
.m-val { font-size: 17px; font-weight: 800; color: #d7dde3; }
.capital-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #6b8394;
  padding: 10px 0;
  border-top: 1px solid rgba(133,168,192,0.1);
  border-bottom: 1px solid rgba(133,168,192,0.1);
  margin-bottom: 10px;
}
.arrow { color: #4e6472; }
.streak-row { display: flex; gap: 16px; font-size: 11px; }
.streak-win { color: #4eca7e; }
.streak-loss { color: #e57373; }

/* ── Equity Chart ────────────────────────────────────────────────────── */
.equity-chart-wrap {
  background: rgba(255,255,255,0.02);
  border: 1px solid rgba(133,168,192,0.12);
  border-radius: 8px;
  padding: 24px 20px 16px;
}
.equity-chart {
  display: flex;
  gap: 12px;
  height: 160px;
  align-items: flex-end;
}
.chart-y-labels {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  height: 100%;
  font-size: 10px;
  color: #4e6472;
  text-align: right;
  min-width: 48px;
}
.chart-bars {
  display: flex;
  align-items: center;
  gap: 3px;
  flex: 1;
  height: 100%;
}
.chart-col {
  flex: 1;
  height: 100%;
  display: flex;
  align-items: center;
  cursor: pointer;
}
.chart-bar {
  width: 100%;
  min-height: 2px;
  border-radius: 2px;
  transition: opacity 0.2s;
}
.chart-bar:hover { opacity: 0.7; }
.bar-positive { background: #4eca7e; }
.bar-negative { background: #e05555; }
.chart-legend {
  display: flex;
  gap: 24px;
  margin-top: 12px;
  font-size: 11px;
}
.legend-pos { color: #4eca7e; }
.legend-neg { color: #e05555; }

/* ── Two-col layout ──────────────────────────────────────────────────── */
.two-col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 24px;
}
.panel {
  background: rgba(255,255,255,0.02);
  border: 1px solid rgba(133,168,192,0.12);
  border-radius: 8px;
  padding: 24px;
}
.panel h3 {
  font-size: 14px;
  font-weight: 800;
  letter-spacing: 2px;
  color: #c3d4df;
  text-transform: uppercase;
  margin: 0 0 20px;
}

/* ── Data table ──────────────────────────────────────────────────────── */
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th {
  font-size: 9px;
  letter-spacing: 1.5px;
  color: #4e6472;
  text-transform: uppercase;
  font-weight: 700;
  padding: 0 8px 10px 0;
  text-align: left;
  border-bottom: 1px solid rgba(133,168,192,0.12);
}
.data-table td {
  padding: 10px 8px 10px 0;
  border-bottom: 1px solid rgba(133,168,192,0.07);
  color: #9bb5c4;
}
.data-table tr:last-child td { border-bottom: none; }
.rank { color: #4e6472; font-weight: 700; font-size: 11px; }
.pair-cell { font-weight: 700; color: #d7dde3; letter-spacing: 0.5px; }
.quote { color: #4e6472; font-size: 11px; }

/* ── Exit reasons ────────────────────────────────────────────────────── */
.exit-list { display: flex; flex-direction: column; gap: 12px; }
.exit-row { display: flex; align-items: center; gap: 10px; }
.exit-label {
  min-width: 110px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
  padding: 3px 8px;
  border-radius: 3px;
  background: rgba(255,255,255,0.05);
}
.exit-tp      { color: #4eca7e; }
.exit-sl      { color: #e57373; }
.exit-trailing{ color: #5dc6e7; }
.exit-signal  { color: #e8a44a; }
.exit-other   { color: #6b8394; }
.exit-bar-wrap { flex: 1; background: rgba(255,255,255,0.05); border-radius: 3px; height: 6px; }
.exit-bar { height: 100%; border-radius: 3px; transition: width 0.6s ease; }
.exit-bar.exit-tp       { background: #4eca7e; }
.exit-bar.exit-sl       { background: #e57373; }
.exit-bar.exit-trailing { background: #5dc6e7; }
.exit-bar.exit-signal   { background: #e8a44a; }
.exit-bar.exit-other    { background: #4e6472; }
.exit-count { font-size: 12px; color: #6b8394; min-width: 32px; text-align: right; }
.exit-pct { font-size: 11px; color: #4e6472; min-width: 36px; text-align: right; }

/* ── Color helpers ───────────────────────────────────────────────────── */
.positive { color: #4eca7e; }
.negative { color: #e57373; }
.green    { color: #4eca7e; }
.amber    { color: #e8a44a; }
.cyan     { color: #5dc6e7; }
.red      { color: #e57373; }

/* ── Disclaimer ──────────────────────────────────────────────────────── */
.disclaimer {
  background: rgba(255,255,255,0.02);
  border: 1px solid rgba(133,168,192,0.1);
  border-radius: 8px;
  padding: 24px 28px;
  margin-top: 20px;
}
.disclaimer h4 {
  font-size: 10px;
  letter-spacing: 2px;
  color: #4e6472;
  text-transform: uppercase;
  margin: 0 0 10px;
  font-weight: 700;
}
.disclaimer p {
  font-size: 12px;
  color: #4e6472;
  line-height: 1.7;
  margin: 0;
}

/* ── Footer ──────────────────────────────────────────────────────────── */
.portal-footer {
  border-top: 1px solid rgba(133,168,192,0.1);
  padding: 24px 0;
}
.footer-container {
  display: flex;
  justify-content: center;
  font-size: 11px;
  color: #3d5260;
  letter-spacing: 1px;
}

/* ── Animations ──────────────────────────────────────────────────────── */
.animate-rise {
  animation: riseIn 0.6s ease both;
}
.animate-fade {
  animation: fadeIn 0.5s ease both;
}
@keyframes riseIn {
  from { opacity: 0; transform: translateY(20px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes fadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}
</style>
