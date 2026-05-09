<template>
  <div class="intel-portal">
    <!-- Clean Minimalist Header -->
    <header class="portal-header">
      <div class="header-container">
        <div class="portal-branding">
          <h1>ALGOVIBE <span class="sub">INTELLIGENCE</span></h1>
        </div>
        <nav class="portal-nav">
          <span class="status-indicator">
            <span class="dot"></span> LIVE_ORACLE_ACTIVE
          </span>
          <button @click="activeTab = 'auth'" class="login-trigger">OPERATOR_LOGIN</button>
        </nav>
      </div>
    </header>

    <!-- Main Editorial Content -->
    <main class="portal-main">
      <div class="content-container">
        <!-- Featured Report: AI Reasoning -->
        <section class="featured-intel">
          <div class="intel-label">FEATURED_REPORT // AI_COGNITIVE_STATE</div>
          <Transition name="fade-report" mode="out-in">
            <article :key="currentInsightIdx" class="intel-article">
              <h2 class="article-title">{{ currentInsightText }}</h2>
              <div class="article-meta">
                <span class="meta-item">MODE: {{ landingData.market.regime }}</span>
                <span class="meta-item">SENTIMENT: {{ landingData.market.sentiment }}%</span>
                <span class="meta-item">TELEMETRY: {{ currentTime }}</span>
              </div>
            </article>
          </Transition>
        </section>

        <div class="intel-grid">
          <!-- Featured Articles (New Section) -->
          <section class="intel-section">
            <div class="section-header">LATEST_INTELLIGENCE_REPORTS</div>
            <div class="articles-list">
              <article v-for="art in landingData.articles" :key="art.title" class="portal-article-card">
                <div class="art-cat">{{ art.category }}</div>
                <h3 class="art-title">{{ art.title }}</h3>
                <p class="art-excerpt">{{ art.excerpt }}</p>
                <div class="art-meta">PUBLISHED_BY_ALGOVIBE_ORACLE</div>
              </article>
            </div>
          </section>

          <div class="intel-side-stack">
            <!-- Live Market Feed -->
            <section class="intel-section">
              <div class="section-header">LIVE_MARKET_SIGNALS</div>
              <div class="signals-grid">
                <div v-for="sig in landingData.signals" :key="sig.pair" class="sig-brief">
                  <div class="sig-header">
                    <span class="pair">{{ sig.pair }}</span>
                    <span class="score" :style="{ color: getScoreColor(sig.score) }">{{ sig.score.toFixed(0) }}</span>
                  </div>
                  <div class="sig-footer">
                    <span class="trend" :class="sig.trend.toLowerCase()">{{ sig.trend }}</span>
                  </div>
                </div>
              </div>
            </section>

            <!-- System Activity -->
            <section class="intel-section">
              <div class="section-header">RECENT_ACTIVITY_LOG</div>
              <div class="activity-list">
                <div v-for="(log, i) in publicLogs" :key="i" class="log-item">
                  <span class="time">{{ log.time }}</span>
                  <span class="msg">{{ log.message }}</span>
                </div>
              </div>
            </section>
          </div>
        </div>
      </div>
    </main>

    <!-- Modal Login (Overlay style) -->
    <Transition name="modal-fade">
      <div v-if="activeTab === 'auth'" class="auth-modal" @click.self="activeTab = null">
        <div class="auth-card">
          <h3>SECURE_ACCESS</h3>
          <form @submit.prevent="handleLogin" class="auth-form">
            <div class="input-group">
              <label>OPERATOR_ID</label>
              <input type="text" v-model="username" required />
            </div>
            <div class="input-group">
              <label>ACCESS_KEY</label>
              <input type="password" v-model="password" required />
            </div>
            <button type="submit" :disabled="loading">{{ loading ? 'VERIFYING...' : 'INITIALIZE' }}</button>
          </form>
          <div v-if="error" class="error-msg">{{ error }}</div>
          <button class="close-modal" @click="activeTab = null">×</button>
        </div>
      </div>
    </Transition>

    <!-- Footer Telemetry -->
    <footer class="portal-footer">
      <div class="footer-container">
        <div class="market-strip">
          <span class="market-item">BTC_IDR: {{ formatPrice(landingData.market.btc_price) }}</span>
          <span class="market-item">DB_STATE: <span class="green">OPTIMAL</span></span>
          <span class="market-item">LATENCY: {{ latency }}ms</span>
        </div>
        <div class="copyright">© 2026 ALGOVIBE QUANTUM SYSTEMS</div>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useMode } from '../composables/useMode'
import { io } from 'socket.io-client'

const router = useRouter()
const { login } = useAuth()
const { syncModeFromBackend } = useMode()

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const activeTab = ref(null)
const latency = ref(0)
const currentTime = ref('')
const publicLogs = ref([])

const landingData = reactive({
  insights: [],
  signals: [],
  articles: [],
  market: { sentiment: 50, btc_price: 0, regime: 'SCANNING' }
})

const currentInsightIdx = ref(0)
const currentInsightText = computed(() => {
  if (!landingData.insights.length) return 'Compiling intelligence reports...'
  return landingData.insights[currentInsightIdx.value].message
})

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

let socket = null
let insightInterval = null
let timeInterval = null

onMounted(() => {
  fetchLandingData()
  setupStream()
  startTime()

  insightInterval = setInterval(() => {
    if (landingData.insights.length > 1) {
      currentInsightIdx.value = (currentInsightIdx.value + 1) % landingData.insights.length
    }
  }, 9000)
})

onUnmounted(() => {
  if (socket) socket.disconnect()
  clearInterval(insightInterval)
  clearInterval(timeInterval)
})

function startTime() {
  timeInterval = setInterval(() => {
    const now = new Date()
    currentTime.value = now.toLocaleTimeString()
  }, 1000)
}

function setupStream() {
  socket = io(backendUrl, { path: '/socket.io', transports: ['websocket'] })

  socket.on('radar_update', (data) => {
    if (Array.isArray(data)) {
      landingData.signals = data.slice(0, 12).map(s => ({
        pair: s.pair.toUpperCase().replace('IDR', ''),
        score: s.score || 50,
        trend: s.trend || 'STABLE'
      }))
    }
  })

  socket.on('llm_decisions_update', (data) => {
    if (Array.isArray(data)) {
      landingData.insights = data
    }
  })

  socket.on('public_activity', (data) => {
    if (Array.isArray(data)) {
      publicLogs.value = data.map(log => ({
        time: log.time.split(' ')[1] || log.time,
        message: log.message
      })).slice(0, 8)
    }
  })
}

async function fetchLandingData() {
  try {
    const res = await fetch(`${backendUrl}/api/public/landing-data`)
    const data = await res.json()
    if (data.success) {
      landingData.insights = data.insights
      landingData.signals = data.signals
      landingData.market = data.market
      landingData.articles = data.articles
    }
  } catch (err) { console.error(err) }
}

async function handleLogin() {
  loading.value = true; error.value = ''
  try {
    const success = await login(username.value, password.value)
    if (success) {
      await syncModeFromBackend()
      router.push('/mode-select')
    } else {
      error.value = 'ACCESS_DENIED'
    }
  } catch (err) { error.value = 'COMM_ERROR' }
  finally { loading.value = false }
}

function getScoreColor(s) {
  if (s > 75) return '#00ff88'
  if (s > 50) return '#00f2ff'
  return '#ff3366'
}

function formatPrice(p) {
  if (!p) return '0'
  return Math.round(p).toLocaleString('id-ID')
}
</script>

<style scoped>
.intel-portal {
  width: 100vw;
  height: 100vh;
  background: #0a0a0b;
  color: #d1d1d1;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  overflow-x: hidden;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.header-container, .content-container, .footer-container {
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
  padding: 0 40px;
}

/* Header */
.portal-header {
  border-bottom: 1px solid #1a1a1c;
  background: #0a0a0b;
  position: sticky;
  top: 0;
  z-index: 100;
}

.header-container {
  height: 80px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.portal-branding h1 {
  font-size: 18px;
  font-weight: 900;
  letter-spacing: 3px;
  color: #fff;
  margin: 0;
}

.portal-branding h1 .sub {
  color: #00f2ff;
  font-weight: 300;
  font-size: 14px;
}

.portal-nav {
  display: flex;
  align-items: center;
  gap: 30px;
}

.status-indicator {
  font-size: 11px;
  font-weight: 700;
  color: #444;
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-indicator .dot {
  width: 6px;
  height: 6px;
  background: #00ff88;
  border-radius: 50%;
  box-shadow: 0 0 8px #00ff88;
}

.login-trigger {
  background: #fff;
  color: #000;
  border: none;
  padding: 8px 18px;
  font-size: 11px;
  font-weight: 800;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.3s;
}

.login-trigger:hover { background: #00f2ff; }

/* Main Content */
.portal-main { flex: 1; padding: 60px 0; }

.featured-intel {
  margin-bottom: 80px;
  border-bottom: 1px solid #1a1a1c;
  padding-bottom: 60px;
}

.intel-label {
  font-size: 10px;
  font-weight: 800;
  color: #444;
  margin-bottom: 30px;
  letter-spacing: 2px;
}

.article-title {
  font-size: 48px;
  font-weight: 800;
  color: #fff;
  line-height: 1.1;
  max-width: 900px;
  margin-bottom: 30px;
}

.article-meta {
  display: flex;
  gap: 30px;
  font-size: 12px;
  color: #555;
  font-weight: 600;
}

.intel-grid {
  display: grid;
  grid-template-columns: 1fr 350px;
  gap: 60px;
}

.intel-side-stack {
  display: flex;
  flex-direction: column;
  gap: 60px;
}

.portal-article-card {
  margin-bottom: 40px;
  padding-bottom: 40px;
  border-bottom: 1px solid #1a1a1c;
}

.portal-article-card:last-child { border: none; }

.art-cat {
  font-size: 10px;
  font-weight: 800;
  color: #00f2ff;
  margin-bottom: 12px;
  letter-spacing: 1px;
}

.art-title {
  font-size: 28px;
  font-weight: 800;
  color: #fff;
  margin-bottom: 15px;
  line-height: 1.2;
}

.art-excerpt {
  font-size: 14px;
  color: #888;
  line-height: 1.6;
  margin-bottom: 20px;
}

.art-meta {
  font-size: 10px;
  font-weight: 700;
  color: #333;
}

.section-header {
  font-size: 11px;
  font-weight: 800;
  color: #444;
  margin-bottom: 30px;
  padding-bottom: 10px;
  border-bottom: 1px solid #1a1a1c;
}

.signals-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 20px;
}

.sig-brief {
  background: #111113;
  padding: 20px;
  border-radius: 8px;
  border: 1px solid #1a1a1c;
}

.sig-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 15px;
}

.sig-header .pair { font-weight: 900; font-size: 16px; color: #fff; }
.sig-header .score { font-weight: 800; font-size: 14px; }

.sig-footer { font-size: 10px; font-weight: 700; color: #444; }
.sig-footer .trend.up { color: #00ff88; }
.sig-footer .trend.down { color: #ff3366; }

.activity-list { display: flex; flex-direction: column; gap: 15px; }
.log-item {
  display: flex;
  gap: 15px;
  font-size: 11px;
  line-height: 1.4;
}

.log-item .time { color: #00f2ff; font-weight: 800; }
.log-item .msg { color: #666; }

/* Auth Modal */
.auth-modal {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.95);
  backdrop-filter: blur(10px);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.auth-card {
  background: #111113;
  width: 400px;
  padding: 40px;
  border-radius: 12px;
  border: 1px solid #1a1a1c;
  position: relative;
}

.auth-card h3 { font-size: 14px; letter-spacing: 3px; margin-bottom: 30px; color: #444; text-align: center; }

.auth-form { display: flex; flex-direction: column; gap: 20px; }
.input-group label { display: block; font-size: 10px; color: #444; margin-bottom: 8px; font-weight: 800; }
.input-group input {
  width: 100%;
  background: #0a0a0b;
  border: 1px solid #1a1a1c;
  padding: 12px;
  color: #fff;
  border-radius: 4px;
  outline: none;
}

.auth-form button {
  background: #fff;
  color: #000;
  border: none;
  padding: 14px;
  font-weight: 900;
  border-radius: 4px;
  cursor: pointer;
  margin-top: 10px;
}

.close-modal {
  position: absolute;
  top: 20px;
  right: 20px;
  background: transparent;
  border: none;
  color: #444;
  font-size: 24px;
  cursor: pointer;
}

.error-msg { color: #ff3366; font-size: 11px; text-align: center; margin-top: 15px; }

/* Footer */
.portal-footer {
  border-top: 1px solid #1a1a1c;
  padding: 40px 0;
  background: #0a0a0b;
}

.footer-container {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.market-strip { display: flex; gap: 40px; font-size: 11px; color: #444; font-weight: 700; }
.market-item .green { color: #00ff88; }
.copyright { font-size: 10px; color: #333; }

/* Animations */
.fade-report-enter-active, .fade-report-leave-active { transition: all 0.6s ease; }
.fade-report-enter-from { opacity: 0; transform: translateX(20px); }
.fade-report-leave-to { opacity: 0; transform: translateX(-20px); }

.modal-fade-enter-active, .modal-fade-leave-active { transition: opacity 0.3s; }
.modal-fade-enter-from, .modal-fade-leave-to { opacity: 0; }

@media (max-width: 1024px) {
  .intel-grid { grid-template-columns: 1fr; }
  .article-title { font-size: 32px; }
}
</style>
