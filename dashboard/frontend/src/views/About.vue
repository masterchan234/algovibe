<template>
  <div class="about-portal">
    <!-- Consistent Global Header -->
    <header class="portal-header">
      <div class="header-container">
        <div class="portal-branding" @click="goToHomeSection('')" style="cursor: pointer;">
          <p class="brand-kicker">ALGORITHMIC JOURNAL</p>
          <h1>ALGOVIBE <span class="sub">INTELLIGENCE</span></h1>
        </div>
        <nav class="portal-nav" aria-label="Primary Navigation">
          <router-link to="/about-us" class="nav-link active">About Us</router-link>
          <a href="/login#hero-2" @click.prevent="goToHomeSection('hero-2')">Featured Story</a>
          <a href="/login#articles" @click.prevent="goToHomeSection('articles')">Articles</a>
          <a href="/login#signals" @click.prevent="goToHomeSection('signals')">Live Signals</a>
          <button @click="triggerHomeLogin" class="login-trigger">login</button>
        </nav>
      </div>
    </header>

    <main class="portal-main">
      <div class="content-container">
        
        <!-- Hero Section -->
        <section class="about-hero animate-rise">
          <p class="hero-kicker">SYSTEM SPECIFICATIONS & DESIGN PHILOSOPHY</p>
          <h1>QUANTITATIVE SYSTEM PROFILE</h1>
          <p class="hero-desc">
            Algovibe is a high-speed quantitative microservice architecture designed for real-time crypto asset scalping. 
            It integrates a WebSocket ingress pipeline, an in-memory computation engine, and mathematical confluences, completely eliminating human psychological bias.
          </p>
          <div class="hero-stats">
            <div class="stat-badge">
              <span class="label">OPERATING MODE</span>
              <span class="value cyan">TECHNICAL_ONLY</span>
            </div>
            <div class="stat-badge">
              <span class="label">ACTIVE INDICATORS</span>
              <span class="value green">11 MATHEMATICAL METRICS</span>
            </div>
            <div class="stat-badge">
              <span class="label">ENGINE LATENCY</span>
              <span class="value amber">SUB-MILLISECOND</span>
            </div>
          </div>
        </section>

        <!-- Document Sub-Navigation (Tabs) -->
        <div class="doc-nav">
          <button 
            v-for="tab in tabs" 
            :key="tab.id"
            :class="['doc-tab', { active: activeTab === tab.id }]"
            @click="activeTab = tab.id"
          >
            {{ tab.label }}
          </button>
        </div>

        <!-- Tab Content: Architecture -->
        <div v-if="activeTab === 'architecture'" class="tab-content animate-fade">
          <div class="grid-layout">
            <div class="main-column">
              <h2>Algovibe Microservice Architecture</h2>
              <p>
                Algovibe operates using a network of isolated containers (Docker Compose) communicating in real-time to ensure market data never becomes stale.
              </p>
              
              <!-- Premium Flow Diagram using CSS-styled blocks -->
              <div class="pipeline-flow">
                <div class="flow-step">
                  <div class="step-icon">📡</div>
                  <div class="step-info">
                    <h4>Market Ingress (market-pipe)</h4>
                    <p>Consumes real-time WebSocket streams from Binance/Indodax Spot in a non-blocking manner.</p>
                  </div>
                </div>
                <div class="flow-arrow">↓</div>
                <div class="flow-step">
                  <div class="step-icon">🧠</div>
                  <div class="step-info">
                    <h4>Scoring Engine (brain-inference)</h4>
                    <p>Calculates 11 technical indicators in an in-memory cache to minimize database persistence lag.</p>
                  </div>
                </div>
                <div class="flow-arrow">↓</div>
                <div class="flow-step">
                  <div class="step-icon">🛡️</div>
                  <div class="step-info">
                    <h4>Risk Defense (entry-gates)</h4>
                    <p>Filters incoming signals using ATR volatility parameters and multi-timeframe EMA trend filters.</p>
                  </div>
                </div>
                <div class="flow-arrow">↓</div>
                <div class="flow-step">
                  <div class="step-icon">⚙️</div>
                  <div class="step-info">
                    <h4>Execution Core (executive-core)</h4>
                    <p>Manages order lifecycle, monitors dynamic trailing take profit, and performs state reconciliation.</p>
                  </div>
                </div>
              </div>
            </div>
            
            <div class="side-column">
              <div class="specs-card">
                <h3>System Telemetry Specs</h3>
                <ul class="specs-list">
                  <li><strong>Exchange Compatibility:</strong>
                    <span>Binance Spot & Indodax Spot</span>
                  </li>
                  <li><strong>Core Runtime:</strong>
                    <span>Python 3.10 / Docker Alpine</span>
                  </li>
                  <li><strong>IPC Bridge:</strong>
                    <span>WebSocket / Shared memory buffers</span>
                  </li>
                  <li><strong>Database Layer:</strong>
                    <span>PostgreSQL + TimescaleDB</span>
                  </li>
                  <li><strong>Safety Ingress:</strong>
                    <span>Strict dry-run simulation parity</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        <!-- Tab Content: Technical Indicators -->
        <div v-else-if="activeTab === 'indicators'" class="tab-content animate-fade">
          <div class="section-header">
            <h2>11 Technical Indicators Confluence</h2>
            <p>
              The scoring engine calculates a total confluence score up to a maximum of ~19.5 from 11 active indicators. 
              A BUY/SELL signal is triggered in real-time if the total score exceeds the threshold boundaries (BUY >= 6.0, opposing SELL < 4.0).
            </p>
          </div>

          <div class="indicators-grid">
            <div class="indicator-card">
              <div class="ind-badge text-cyan">01</div>
              <h4>RSI-14 (Wilder Smoothing)</h4>
              <p>Detects overbought/oversold regions using the precise Wilder smoothing method.</p>
              <div class="ind-weight">Weight: 3.0</div>
            </div>
            <div class="indicator-card">
              <div class="ind-badge text-cyan">02</div>
              <h4>Price-Normalized MACD</h4>
              <p>Calculates a price-normalized (x1000) MACD histogram to detect early momentum shifts without nominal price bias.</p>
              <div class="ind-weight">Weight: 2.5</div>
            </div>
            <div class="indicator-card">
              <div class="ind-badge text-cyan">03</div>
              <h4>Bollinger %B</h4>
              <p>Measures the relative position of the price against upper and lower standard deviation bands.</p>
              <div class="ind-weight">Weight: 2.0</div>
            </div>
            <div class="indicator-card">
              <div class="ind-badge text-cyan">04</div>
              <h4>Stochastic RSI</h4>
              <p>Combines Stochastics and RSI to map ultra-fast momentum exhaustion along with K/D crossover tracking.</p>
              <div class="ind-weight">Weight: 2.0</div>
            </div>
            <div class="indicator-card">
              <div class="ind-badge text-cyan">05</div>
              <h4>EMA 9/21 Crossover</h4>
              <p>Determines medium-term trend shifts via dynamic exponential moving average crossovers.</p>
              <div class="ind-weight">Weight: 1.5</div>
            </div>
            <div class="indicator-card">
              <div class="ind-badge text-cyan">06</div>
              <h4>Williams %R</h4>
              <p>An independent momentum indicator to detect the strength of extreme market swings.</p>
              <div class="ind-weight">Weight: 1.5</div>
            </div>
            <div class="indicator-card">
              <div class="ind-badge text-cyan">07</div>
              <h4>Linear Regression Slope</h4>
              <p>Measures the acceleration of trend momentum through the slope of the latest linear regression line.</p>
              <div class="ind-weight">Weight: 1.0</div>
            </div>
            <div class="indicator-card">
              <div class="ind-badge text-cyan">08</div>
              <h4>ROC-10 (Rate of Change)</h4>
              <p>Measures the percentage change in price to identify potential mean-reversion opportunities.</p>
              <div class="ind-weight">Weight: 1.0</div>
            </div>
            <div class="indicator-card">
              <div class="ind-badge text-cyan">09</div>
              <h4>Order Book Pressure</h4>
              <p>Analyzes real-time bid/ask depth pressure (falls back to Volume Ratio if order book depth is unavailable).</p>
              <div class="ind-weight">Weight: 2.0</div>
            </div>
            <div class="indicator-card">
              <div class="ind-badge text-cyan">10</div>
              <h4>Volume Ratio</h4>
              <p>The ratio of current trading volume against its 20-candle moving average. Volume spikes validate trend breakouts.</p>
              <div class="ind-weight">Weight: 1.5</div>
            </div>
            <div class="indicator-card">
              <div class="ind-badge text-cyan">11</div>
              <h4>OBV Slope</h4>
              <p>Tracks discrete volume accumulation and distribution before nominal price movement occurs.</p>
              <div class="ind-weight">Weight: 1.5</div>
            </div>
          </div>
        </div>

        <!-- Tab Content: Entry Gates & Safety -->
        <div v-else-if="activeTab === 'safety'" class="tab-content animate-fade">
          <div class="grid-layout">
            <div class="main-column">
              <h2>Entry Gate Sequence & Risk Mitigation</h2>
              <p>
                Before execution orders are dispatched to the market, trading signals must traverse a sequence of safety barriers (Entry Gates) to minimize risk against falling knives or extreme volatility spikes.
              </p>

              <div class="gates-timeline">
                <div class="gate-block">
                  <div class="gate-num">Gate 1-2</div>
                  <div class="gate-detail">
                    <h4>Blacklist & Cooldown Guard</h4>
                    <p>Ensures the asset is not on the active blacklist and complies with transaction cooldown intervals to prevent rapid consecutive trades on the same pair.</p>
                  </div>
                </div>
                <div class="gate-block">
                  <div class="gate-num">Gate 4b</div>
                  <div class="gate-detail">
                    <h4>ATR Volatility Guard (Volatility Filter)</h4>
                    <p>Rejects execution if the ATR volatility exceeds 3% of the current price (or 4.5% in loose configurations). This protects the bot from artificial price spikes.</p>
                  </div>
                </div>
                <div class="gate-block">
                  <div class="gate-num">Gate 4c</div>
                  <div class="gate-detail">
                    <h4>EMA-50 Trend Guard (Core Trend Filter)</h4>
                    <p>By default, blocks transactions if the price resides below the long-term EMA-50 trend line.</p>
                    <div class="exception-alert">
                      <strong>EXCEPTION (Bottom-Explosion):</strong>
                      The EMA-50 filter is automatically bypassed if extreme oversold reversal conditions are detected: 
                      <code>RSI &lt; 26</code> + <code>Buy Pressure &gt; 66%</code> + <code>Volume Ratio &gt; 2.1</code>.
                    </div>
                  </div>
                </div>
                <div class="gate-block">
                  <div class="gate-num">Gate 10-13</div>
                  <div class="gate-detail">
                    <h4>Consecutive-Loss Halt & Final Confidence Threshold</h4>
                    <p>Automatically halts trading activity if consecutive losses occur, and validates K-NN pattern correlations before final buy order dispatch.</p>
                  </div>
                </div>
              </div>
            </div>

            <div class="side-column">
              <div class="specs-card alert-card">
                <h3>Exit Rules & Capital Preservation</h3>
                <ul class="specs-list">
                  <li><strong>Micro Profit Exit:</strong>
                    <span>Evaluated strictly against the bid price (not last trade price) to guarantee real net profitability after transaction fees.</span>
                  </li>
                  <li><strong>Trailing Take Profit (TTP):</strong>
                    <span>Follows rising price trends and locks in profit when minor trend reversals are detected.</span>
                  </li>
                  <li><strong>Auto Stop-Loss (Auto-SL):</strong>
                    <span>An automated capital protection guard that instantly exits the asset if price action breaches the risk tolerance threshold.</span>
                  </li>
                  <li><strong>Database State Protection:</strong>
                    <span>The cost basis (<code>amount_idr</code>) must never be overwritten during exchange state reconciliation.</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        <!-- Tab Content: Hybrid AI Layer -->
        <div v-else-if="activeTab === 'ai'" class="tab-content animate-fade">
          <div class="grid-layout">
            <div class="main-column">
              <h2>AI Layer (The Oracle) Role & Technical Only Mode</h2>
              <p>
                Algovibe is designed with a strict separation of concerns between artificial intelligence logic and core trade execution algorithms.
              </p>
              
              <div class="ai-concept-grid">
                <div class="concept-card">
                  <h4>TECHNICAL_ONLY_MODE = true</h4>
                  <p>
                    All entry and exit trading decisions are 100% determined by objective mathematical technical indicators. The AI layer is strictly prohibited from executing orders or injecting trading signals into the execution core.
                  </p>
                </div>
                <div class="concept-card">
                  <h4>AI & Oracle Editorial Role</h4>
                  <p>
                    The AI layer acts as an isolated research analyst (The Oracle). It processes market news, identifies trend anomalies, and compiles market intelligence reports and editorial articles published in real-time on this portal.
                  </p>
                </div>
              </div>

              <blockquote class="quant-quote">
                "Execution discipline is achieved by eliminating subjectivity. Pure mathematics dictates execution, while artificial intelligence deciphers the global context in isolation."
              </blockquote>
            </div>

            <div class="side-column">
              <div class="specs-card spec-ai-card">
                <h3>Oracle Architecture</h3>
                <ul class="specs-list">
                  <li><strong>Model:</strong>
                    <span>LiteRT Lightweight Inference Engine</span>
                  </li>
                  <li><strong>Primary Target:</strong>
                    <span>Market Regime Classification & Content Generation</span>
                  </li>
                  <li><strong>Security Isolation:</strong>
                    <span>Zero permission on Binance/Indodax trade key signatures</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </div>

      </div>
    </main>

    <!-- Consistent Telemetry Footer -->
    <footer class="portal-footer">
      <div class="footer-container">
        <div class="market-strip">
          <span class="market-item">ALGOVIBE SECURE PROFILE</span>
          <span class="market-item">SECURITY STATE: <span class="green">OPTIMAL</span></span>
          <span class="market-item">ENCRYPTION: <span class="green">AES-256</span></span>
        </div>
        <div class="copyright">© 2026 ALGOVIBE QUANTUM SYSTEMS</div>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const tabs = [
  { id: 'architecture', label: 'SYSTEM ARCHITECTURE' },
  { id: 'indicators', label: 'INDICATOR MATRIX' },
  { id: 'safety', label: 'SAFETY & ENTRY GATES' },
  { id: 'ai', label: 'AI LAYER (THE ORACLE)' },
]

const activeTab = ref('architecture')

function goToHomeSection(id) {
  router.push({ name: 'login', hash: id ? `#${id}` : '' })
}

function triggerHomeLogin() {
  router.push({ name: 'login', query: { showAuth: 'true' } })
}

onMounted(() => {
  document.title = 'System Profile | Algovibe Intelligence'
})
</script>

<style scoped>
.about-portal {
  width: 100vw;
  height: 100vh;
  background:
    radial-gradient(circle at 15% 10%, rgba(35, 77, 102, 0.35), transparent 35%),
    radial-gradient(circle at 82% 20%, rgba(147, 93, 46, 0.28), transparent 30%),
    linear-gradient(180deg, #0c1119 0%, #0b1411 100%);
  color: #d7dde3;
  font-family: 'IBM Plex Sans', 'Space Grotesk', 'Segoe UI', sans-serif;
  overflow-x: hidden;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.content-container, .header-container, .footer-container {
  max-width: 1160px;
  margin: 0 auto;
  width: 100%;
  padding: 0 28px;
}

/* Header style identical to Login.vue */
.portal-header {
  border-bottom: 1px solid rgba(133, 168, 192, 0.2);
  background: rgba(8, 15, 24, 0.9);
  backdrop-filter: blur(8px);
  position: sticky;
  top: 0;
  z-index: 100;
}

.header-container {
  min-height: 84px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.brand-kicker {
  margin: 0 0 4px;
  font-size: 10px;
  letter-spacing: 2px;
  color: #7ea3c1;
  font-weight: 700;
}

.portal-branding h1 {
  font-size: 20px;
  font-weight: 900;
  letter-spacing: 3px;
  color: #f4f8fb;
  margin: 0;
}

.portal-branding h1 .sub {
  color: #5dc6e7;
  font-weight: 500;
  font-size: 13px;
}

.portal-nav {
  display: flex;
  align-items: center;
  gap: 18px;
}

.portal-nav a {
  color: #b6c8d7;
  font-size: 11px;
  text-decoration: none;
  font-weight: 700;
  letter-spacing: 0.6px;
  transition: color 0.2s ease;
}

.portal-nav a:hover,
.portal-nav a.active {
  color: #5dc6e7;
}

.login-trigger {
  background: linear-gradient(120deg, #79d1ee, #5fc4a4);
  color: #051018;
  border: 1px solid rgba(178, 236, 255, 0.35);
  padding: 8px 18px;
  font-size: 11px;
  font-weight: 800;
  border-radius: 999px;
  cursor: pointer;
  transition: transform 0.25s ease, box-shadow 0.25s ease;
}

.login-trigger:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 18px rgba(80, 177, 214, 0.35);
}

.portal-main {
  flex: 1;
  padding: 48px 0 64px;
}

/* Hero Section */
.about-hero {
  border: 1px solid rgba(124, 171, 200, 0.2);
  border-radius: 18px;
  padding: 40px;
  margin-bottom: 32px;
  background: rgba(8, 19, 29, 0.65);
  backdrop-filter: blur(4px);
}

.hero-kicker {
  margin: 0 0 12px;
  color: #8eb8cf;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.5px;
}

.about-hero h1 {
  margin: 0 0 16px;
  font-size: clamp(28px, 4.5vw, 44px);
  line-height: 1.15;
  color: #f4f8fc;
  font-family: 'Source Serif 4', 'Georgia', serif;
}

.hero-desc {
  max-width: 860px;
  line-height: 1.7;
  color: #adbfcd;
  font-size: 16px;
  margin-bottom: 28px;
}

.hero-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}

.stat-badge {
  border: 1px solid rgba(132, 179, 209, 0.2);
  background: rgba(13, 28, 41, 0.7);
  padding: 12px 18px;
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-badge .label {
  font-size: 9px;
  font-weight: 800;
  color: #8fb2c8;
  letter-spacing: 1px;
}

.stat-badge .value {
  font-size: 13px;
  font-weight: 800;
}

.cyan { color: #5dc6e7; }
.green { color: #00ff88; }
.amber { color: #ffcf5a; }

/* Sub-nav tabs */
.doc-nav {
  display: flex;
  border-bottom: 1px solid rgba(133, 168, 192, 0.2);
  margin-bottom: 32px;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.doc-tab {
  background: transparent;
  border: none;
  color: #8fa6b8;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1px;
  padding: 12px 20px;
  cursor: pointer;
  position: relative;
  transition: color 0.2s ease;
  white-space: nowrap;
}

.doc-tab:hover {
  color: #d7edf9;
}

.doc-tab.active {
  color: #5dc6e7;
}

.doc-tab.active::after {
  content: '';
  position: absolute;
  bottom: -5px;
  left: 0;
  right: 0;
  height: 2px;
  background: #5dc6e7;
  box-shadow: 0 0 10px #5dc6e7;
}

/* Tab Layouts */
.grid-layout {
  display: grid;
  grid-template-columns: 1fr 340px;
  gap: 28px;
}

.main-column h2 {
  font-size: 26px;
  color: #f4f8fc;
  font-family: 'Source Serif 4', 'Georgia', serif;
  margin-bottom: 16px;
}

.main-column p {
  line-height: 1.7;
  color: #b5c6d2;
  font-size: 15px;
  margin-bottom: 24px;
}

/* Premium flow diagram */
.pipeline-flow {
  background: rgba(9, 20, 30, 0.45);
  border: 1px solid rgba(125, 171, 196, 0.15);
  border-radius: 14px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.flow-step {
  display: flex;
  gap: 16px;
  background: rgba(13, 26, 41, 0.65);
  border: 1px solid rgba(100, 150, 180, 0.15);
  border-radius: 10px;
  padding: 16px;
  align-items: center;
}

.step-icon {
  font-size: 24px;
  background: rgba(93, 198, 231, 0.1);
  width: 48px;
  height: 48px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.step-info h4 {
  margin: 0 0 4px;
  font-size: 14px;
  color: #eef7ff;
  font-weight: 700;
}

.step-info p {
  margin: 0;
  font-size: 12px;
  color: #9cb1bf;
  line-height: 1.5;
}

.flow-arrow {
  text-align: center;
  font-size: 16px;
  color: #5dc6e7;
  font-weight: bold;
}

/* Side Info Cards */
.specs-card {
  background: rgba(8, 22, 33, 0.62);
  border: 1px solid rgba(125, 169, 193, 0.2);
  border-radius: 14px;
  padding: 24px;
}

.specs-card h3 {
  margin: 0 0 16px;
  font-size: 15px;
  color: #edf7fc;
  letter-spacing: 0.5px;
  border-bottom: 1px solid rgba(133, 168, 192, 0.2);
  padding-bottom: 10px;
}

.specs-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.specs-list li {
  font-size: 12px;
  line-height: 1.5;
  color: #a9c1cf;
  display: flex;
  flex-direction: column;
}

.specs-list li strong {
  color: #f2f8fc;
  font-size: 11px;
  margin-bottom: 2px;
}

.alert-card {
  border-color: rgba(220, 60, 80, 0.25);
  background: rgba(30, 10, 15, 0.4);
}

.alert-card h3 {
  color: #ff6680;
  border-bottom-color: rgba(220, 60, 80, 0.2);
}

.spec-ai-card {
  border-color: rgba(163, 113, 247, 0.25);
  background: rgba(20, 10, 30, 0.45);
}

.spec-ai-card h3 {
  color: #a371f7;
  border-bottom-color: rgba(163, 113, 247, 0.2);
}

/* Indicators grid layout */
.section-header {
  margin-bottom: 28px;
}

.section-header h2 {
  font-size: 26px;
  color: #f4f8fc;
  font-family: 'Source Serif 4', 'Georgia', serif;
  margin-bottom: 10px;
}

.section-header p {
  color: #a5b9c7;
  font-size: 15px;
  line-height: 1.65;
  max-width: 900px;
}

.indicators-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}

.indicator-card {
  background: rgba(13, 22, 34, 0.65);
  border: 1px solid rgba(125, 171, 196, 0.16);
  border-radius: 12px;
  padding: 20px;
  position: relative;
  transition: transform 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease;
}

.indicator-card:hover {
  transform: translateY(-4px);
  border-color: rgba(93, 198, 231, 0.4);
  box-shadow: 0 12px 30px rgba(93, 198, 231, 0.08);
}

.ind-badge {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.8px;
  margin-bottom: 12px;
}

.text-cyan { color: #5dc6e7; }

.indicator-card h4 {
  margin: 0 0 8px;
  font-size: 15px;
  color: #edf5fb;
  font-weight: 700;
}

.indicator-card p {
  margin: 0 0 14px;
  font-size: 12px;
  color: #9eb2c1;
  line-height: 1.6;
}

.ind-weight {
  font-size: 10px;
  font-weight: 800;
  color: #5dc6e7;
  background: rgba(93, 198, 231, 0.1);
  padding: 4px 10px;
  border-radius: 6px;
  width: fit-content;
}

/* Gates timeline style */
.gates-timeline {
  display: flex;
  flex-direction: column;
  gap: 20px;
  position: relative;
  padding-left: 20px;
}

.gates-timeline::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 4px;
  width: 2px;
  background: rgba(125, 171, 196, 0.2);
}

.gate-block {
  position: relative;
}

.gate-block::before {
  content: '';
  position: absolute;
  left: -20px;
  top: 6px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #5dc6e7;
  box-shadow: 0 0 8px #5dc6e7;
}

.gate-num {
  font-size: 10px;
  font-weight: 800;
  color: #5dc6e7;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}

.gate-detail h4 {
  margin: 0 0 6px;
  font-size: 15px;
  color: #eef7ff;
}

.gate-detail p {
  margin: 0 0 12px;
  font-size: 13px;
  color: #a5bdcc;
  line-height: 1.6;
}

.exception-alert {
  background: rgba(13, 28, 41, 0.65);
  border-left: 3px solid #ffcf5a;
  padding: 12px 16px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  color: #c5d4df;
}

.exception-alert code {
  background: rgba(255, 207, 90, 0.1);
  color: #ffcf5a;
  padding: 2px 4px;
  border-radius: 4px;
}

/* AI Layer specific styling */
.ai-concept-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 24px;
}

.concept-card {
  background: rgba(13, 22, 34, 0.55);
  border: 1px solid rgba(125, 171, 196, 0.16);
  border-radius: 10px;
  padding: 20px;
}

.concept-card h4 {
  margin: 0 0 10px;
  color: #5dc6e7;
  font-size: 14px;
}

.concept-card p {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: #adbfcd;
}

.quant-quote {
  border-left: 3px solid rgba(163, 113, 247, 0.6);
  background: rgba(20, 10, 30, 0.25);
  padding: 16px 20px;
  font-style: italic;
  color: #a9c1cf;
  font-size: 14px;
  line-height: 1.6;
  margin: 24px 0 0;
}

/* Footer telemetry */
.portal-footer {
  border-top: 1px solid rgba(118, 163, 188, 0.2);
  padding: 40px 0;
  background: rgba(8, 15, 24, 0.75);
  margin-top: 60px;
}

.footer-container {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.market-strip {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  font-size: 11px;
  color: #8aa6ba;
  font-weight: 700;
}

.copyright {
  font-size: 10px;
  color: #7898ad;
}

/* Keyframe animations */
.animate-rise {
  animation: rise-in 0.6s ease both;
}

.animate-fade {
  animation: fade-in 0.5s ease both;
}

@keyframes rise-in {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fade-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

/* Responsive constraints */
@media (max-width: 1024px) {
  .grid-layout {
    grid-template-columns: 1fr;
  }
  .side-column {
    order: -1; /* Place specs card above key content on small screens */
  }
  .portal-nav a {
    display: none;
  }
}

@media (max-width: 640px) {
  .header-container,
  .content-container,
  .footer-container {
    padding: 0 16px;
  }
  .about-hero {
    padding: 24px;
  }
  .ai-concept-grid {
    grid-template-columns: 1fr;
  }
}
</style>
