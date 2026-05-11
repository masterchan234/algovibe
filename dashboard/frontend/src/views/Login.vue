<template>
  <div class="blog-portal">
    <header class="portal-header">
      <div class="header-container">
        <div class="portal-branding">
          <p class="brand-kicker">ALGORITHMIC JOURNAL</p>
          <h1>ALGOVIBE <span class="sub">INTELLIGENCE</span></h1>
        </div>
        <nav class="portal-nav" aria-label="Primary Navigation">
          <a href="/about-us">About Us</a>
          <a href="#hero-2">Featured Story</a>
          <a href="#articles">Articles</a>
          <a href="#signals">Live Signals</a>
          <button @click="activeTab = 'auth'" class="login-trigger">login</button>
        </nav>
      </div>
    </header>

    <div v-if="selectedArticle" class="article-overlay" @click.self="closeArticle()">
      <div class="article-modal">
        <button class="close-btn" @click="closeArticle()">✕</button>
        <div class="reader-header">
          <nav class="reader-breadcrumb" aria-label="Breadcrumb">
            <a href="/login" @click.prevent="goLanding">Home</a>
            <span>/</span>
            <a href="/login#articles" @click.prevent="goArticles">Articles</a>
            <span>/</span>
            <span>{{ selectedArticle.title }}</span>
          </nav>
          <div class="art-cat">{{ selectedArticle.category }}</div>
          <h2 class="reader-title">{{ selectedArticle.title }}</h2>
          <div class="art-meta">{{ formatDate(selectedArticle.created_at) }} | BY_ALGOVIBE_ORACLE</div>
        </div>
        <div ref="articleBodyRef" class="reader-body">
          <div class="reader-content">
            <div v-if="renderedArticleHtml" v-html="renderedArticleHtml"></div>
            <div v-else class="reader-content-fallback">{{ selectedArticle.content }}</div>
          </div>
          <div v-if="relatedArticles.length" class="related-section">
            <h3>Related Articles</h3>
            <div class="related-list">
              <a
                v-for="art in relatedArticles"
                :key="art.slug"
                :href="articleHref(art)"
                @click.prevent="openArticle(art)"
              >
                {{ art.title }}
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>

    <main class="portal-main">
      <div class="content-container">
        <section id="hero-1" class="hero hero-primary" aria-labelledby="hero-title">
          <p class="hero-kicker">MARKET INTELLIGENCE BRIEF</p>
          <Transition name="fade-report" mode="out-in">
            <article :key="currentInsightIdx" class="hero-copy">
              <h2 id="hero-title">{{ currentInsightText }}</h2>
              <p>
                Editorial desk Algovibe memantau dinamika pasar kripto IDR secara real-time,
                menggabungkan konteks teknikal, arus orderbook, dan perubahan sentimen untuk keputusan trading yang lebih terukur.
              </p>
            </article>
          </Transition>
          <div class="hero-meta">
            <span>Regime: {{ landingData.market.regime }}</span>
            <span>Sentiment: {{ landingData.market.sentiment }}%</span>
            <span>BTC/IDR: {{ formatPrice(landingData.market.btc_price) }}</span>
            <span>Updated: {{ currentTime }}</span>
          </div>
        </section>

        <section id="hero-2" class="hero hero-secondary" aria-labelledby="featured-story-title">
          <div class="hero-secondary-head">
            <p class="hero-kicker">FEATURED STORY</p>
            <button
              v-if="heroArticle"
              class="read-btn"
              @click="openArticle(heroArticle)"
            >
              READ_FULL_REPORT
            </button>
          </div>
          <article v-if="heroArticle" class="featured-story">
            <p class="art-cat">{{ heroArticle.category }}</p>
            <h2 id="featured-story-title">{{ heroArticle.title }}</h2>
            <p class="feature-excerpt">{{ stripMarkdown(heroArticle.excerpt) }}</p>
            <div class="art-meta">{{ formatDate(heroArticle.created_at) }} | BY_ALGOVIBE_ORACLE</div>
          </article>
          <article v-else class="featured-story empty-story">
            <h2 id="featured-story-title">Featured article is being prepared</h2>
            <p>Mesin editorial sedang menyusun analisis terbaru berbasis data market dan indikator teknis.</p>
          </article>
        </section>

        <section id="articles" class="section-block" aria-labelledby="latest-articles-title">
          <div class="section-headline">
            <h2 id="latest-articles-title">Latest Intelligence Articles</h2>
            <p>Analisis mendalam tentang coin yang hype, coin yang melemah, dan struktur risiko intraday.</p>
          </div>
          <div class="articles-list">
            <article
              v-for="art in remainingArticles"
              :key="art.slug"
              class="portal-article-card"
              @click="openArticle(art)"
            >
              <div class="art-cat">{{ art.category }}</div>
              <h3 class="art-title">{{ art.title }}</h3>
              <p class="art-excerpt">{{ stripMarkdown(art.excerpt) }}</p>
              <div class="art-meta">{{ formatDate(art.created_at) }} | BY_ALGOVIBE_ORACLE</div>
            </article>
          </div>
        </section>

        <section id="signals" class="section-block" aria-labelledby="live-signals-title">
          <div class="section-headline">
            <h2 id="live-signals-title">Live Market Signals</h2>
            <p>Snapshot kekuatan trend dan skor momentum berbasis stream data pasar.</p>
          </div>
          <div class="signals-grid">
            <div v-for="sig in landingData.signals" :key="sig.pair" class="sig-brief">
              <!-- Header: pair + verdict badge -->
              <div class="sig-header">
                <div class="sig-pair-wrap">
                  <span class="pair">{{ sig.pair }}<span class="pair-base">/IDR</span></span>
                  <span class="sig-verdict" :class="sig.verdict?.toLowerCase()">{{ sig.verdict || 'HOLD' }}</span>
                </div>
                <div class="sig-scores">
                  <span class="score-pill buy" title="Buy Score">B {{ sig.score?.toFixed(1) }}</span>
                  <span class="score-pill sell" title="Sell Score">S {{ sig.sell_score?.toFixed(1) }}</span>
                </div>
              </div>
              <!-- Price row -->
              <div class="sig-market">
                <span class="price">{{ formatSignalPrice(sig.price) }}</span>
                <span class="change" :class="getChangeClass(sig.change_display)">{{ formatChange(sig.change_display) }}</span>
              </div>
              <!-- Technical indicators grid -->
              <div class="ind-grid">
                <div class="ind-row" :class="rsiClass(sig.rsi)">
                  <span class="ind-name">RSI-14</span>
                  <span class="ind-val">{{ sig.rsi?.toFixed(1) ?? '—' }}</span>
                  <span class="ind-tag">{{ rsiTag(sig.rsi) }}</span>
                </div>
                <div class="ind-row" :class="macdClass(sig.macd_hist)">
                  <span class="ind-name">MACD</span>
                  <span class="ind-val">{{ formatIndFloat(sig.macd_hist) }}</span>
                  <span class="ind-tag">{{ sig.macd_hist > 0 ? 'POS' : sig.macd_hist < 0 ? 'NEG' : '—' }}</span>
                </div>
                <div class="ind-row" :class="bbClass(sig.bb_pct)">
                  <span class="ind-name">BB %B</span>
                  <span class="ind-val">{{ sig.bb_pct?.toFixed(2) ?? '—' }}</span>
                  <span class="ind-tag">{{ bbTag(sig.bb_pct) }}</span>
                </div>
                <div class="ind-row" :class="stochClass(sig.stoch_k)">
                  <span class="ind-name">Stoch K/D</span>
                  <span class="ind-val">{{ sig.stoch_k?.toFixed(0) }}/{{ sig.stoch_d?.toFixed(0) }}</span>
                  <span class="ind-tag">{{ stochTag(sig.stoch_k) }}</span>
                </div>
                <div class="ind-row" :class="emaCrossClass(sig.ema_cross)">
                  <span class="ind-name">EMA 9/21</span>
                  <span class="ind-val">{{ sig.ema_cross ?? '—' }}</span>
                  <span class="ind-tag">{{ sig.ema_cross === 'BULLISH' ? '↑' : sig.ema_cross === 'BEARISH' ? '↓' : '→' }}</span>
                </div>
                <div class="ind-row" :class="wrClass(sig.williams_r)">
                  <span class="ind-name">Williams %R</span>
                  <span class="ind-val">{{ sig.williams_r?.toFixed(1) ?? '—' }}</span>
                  <span class="ind-tag">{{ wrTag(sig.williams_r) }}</span>
                </div>
                <div class="ind-row" :class="slopeClass(sig.slope)">
                  <span class="ind-name">Slope</span>
                  <span class="ind-val">{{ formatIndFloat(sig.slope) }}%</span>
                  <span class="ind-tag">{{ sig.slope > 0.01 ? 'UP' : sig.slope < -0.01 ? 'DN' : 'FLAT' }}</span>
                </div>
                <div class="ind-row" :class="rocClass(sig.roc)">
                  <span class="ind-name">ROC-10</span>
                  <span class="ind-val">{{ formatIndFloat(sig.roc) }}%</span>
                  <span class="ind-tag">{{ sig.roc > 0 ? 'POS' : sig.roc < 0 ? 'NEG' : '—' }}</span>
                </div>
                <div class="ind-row" :class="volClass(sig.volume_ratio)">
                  <span class="ind-name">Vol Ratio</span>
                  <span class="ind-val">{{ sig.volume_ratio?.toFixed(2) ?? '—' }}x</span>
                  <span class="ind-tag">{{ volTag(sig.volume_ratio) }}</span>
                </div>
                <div class="ind-row" :class="obvClass(sig.obv_slope)">
                  <span class="ind-name">OBV Slope</span>
                  <span class="ind-val">{{ formatIndFloat(sig.obv_slope) }}</span>
                  <span class="ind-tag">{{ sig.obv_slope > 0 ? 'ACC' : sig.obv_slope < 0 ? 'DIST' : '—' }}</span>
                </div>
                <div class="ind-row" :class="bpClass(sig.buy_pressure)">
                  <span class="ind-name">Buy Press</span>
                  <span class="ind-val">{{ sig.buy_pressure?.toFixed(1) ?? '—' }}%</span>
                  <span class="ind-tag">{{ sig.buy_pressure > 55 ? 'BID' : sig.buy_pressure < 45 ? 'ASK' : 'BAL' }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="activity-list">
            <div v-for="(log, i) in publicLogs" :key="i" class="log-item">
              <span class="time">{{ log.time }}</span>
              <span class="msg">{{ log.message }}</span>
            </div>
          </div>
        </section>
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
          <span class="market-item">DB_STATE: <span :class="getDbStateClass(dbState)">{{ dbState }}</span></span>
          <span class="market-item">LATENCY: {{ latency }}ms</span>
        </div>
        <div class="copyright">© 2026 ALGOVIBE QUANTUM SYSTEMS</div>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, onUnmounted, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useMode } from '../composables/useMode'
import MarkdownIt from 'markdown-it'

const router = useRouter()
const route = useRoute()
const { login } = useAuth()
const { syncModeFromBackend } = useMode()

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  breaks: true,
})

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const activeTab = ref(null)
const latency = ref(0)
const dbState = ref('SYNCING')
const currentTime = ref('')
const publicLogs = ref([])
const selectedArticle = ref(null)
const articleBodyRef = ref(null)

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

const heroArticle = computed(() => {
  return landingData.articles.length ? landingData.articles[0] : null
})

const remainingArticles = computed(() => {
  if (landingData.articles.length <= 1) return []
  return landingData.articles.slice(1)
})

const relatedArticles = computed(() => {
  if (!selectedArticle.value) return []
  return landingData.articles
    .filter((art) => art.slug !== selectedArticle.value.slug)
    .slice(0, 3)
})

const renderedArticleHtml = computed(() => {
  if (!selectedArticle.value?.content) return ''
  try {
    return md.render(selectedArticle.value.content)
  } catch (err) {
    console.error('[Markdown Render Error]', err)
    return escapeHtml(selectedArticle.value.content)
  }
})

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

const DEFAULT_SEO = {
  title: 'Algovibe Intelligence | Crypto Market Blog & Live Signals',
  description: 'Analisis pasar kripto berbasis data, artikel trading insight, dan live market signals untuk ekosistem IDR.',
}

let insightInterval = null
let timeInterval = null
let landingPollInterval = null

onMounted(() => {
  fetchLandingData()
  startTime()

  insightInterval = setInterval(() => {
    if (landingData.insights.length > 1) {
      currentInsightIdx.value = (currentInsightIdx.value + 1) % landingData.insights.length
    }
  }, 9000)

  landingPollInterval = setInterval(() => {
    fetchLandingData()
  }, 12000)
})

onUnmounted(() => {
  clearInterval(insightInterval)
  clearInterval(timeInterval)
  clearInterval(landingPollInterval)
})

watch(() => route.params.slug, () => {
  syncArticleFromRoute()
}, { immediate: true })

watch(() => heroArticle.value, () => {
  applySeoMeta(selectedArticle.value || heroArticle.value || null)
  renderStructuredData()
})

watch(() => landingData.articles, () => {
  syncArticleFromRoute()
  renderStructuredData()
})

function startTime() {
  timeInterval = setInterval(() => {
    const now = new Date()
    currentTime.value = now.toLocaleTimeString()
  }, 1000)
}

async function fetchLandingData() {
  const started = performance.now()
  try {
    const res = await fetch(`${backendUrl}/api/public/landing-data?ts=${Date.now()}`, {
      cache: 'no-store',
      headers: {
        'cache-control': 'no-cache',
        'pragma': 'no-cache',
      },
    })
    latency.value = Math.max(0, Math.round(performance.now() - started))
    if (!res.ok) {
      dbState.value = 'DEGRADED'
      return
    }
    const data = await res.json()
    if (data.success) {
      dbState.value = latency.value <= 1200 ? 'OPTIMAL' : 'DEGRADED'
      landingData.insights = data.insights
      landingData.signals = (data.signals || []).map((s) => ({
        pair: String(s.pair || '').toUpperCase().replace('IDR', ''),
        score: Number(s.score ?? 0),
        sell_score: Number(s.sell_score ?? 0),
        confidence: Number(s.confidence ?? 50),
        verdict: s.verdict || 'HOLD',
        trend: s.trend || 'STABLE',
        price: Number(s.price || 0),
        change_display: pickBestChange(s),
        // Indikator teknikal
        rsi: Number(s.rsi ?? 50),
        macd_hist: Number(s.macd_hist ?? 0),
        bb_pct: Number(s.bb_pct ?? 0.5),
        stoch_k: Number(s.stoch_k ?? 50),
        stoch_d: Number(s.stoch_d ?? 50),
        ema_cross: s.ema_cross || 'NEUTRAL',
        williams_r: Number(s.williams_r ?? -50),
        slope: Number(s.slope ?? 0),
        roc: Number(s.roc ?? 0),
        volume_ratio: Number(s.volume_ratio ?? 1),
        obv_slope: Number(s.obv_slope ?? 0),
        buy_pressure: Number(s.buy_pressure ?? 50),
        signal: s.signal || 'NEUTRAL',
      }))
      landingData.market = data.market
      publicLogs.value = (data.public_logs || []).map((log) => ({
        time: formatLogTime(log.time),
        message: String(log.message || '')
      })).slice(0, 8)
      const slugCounter = new Map()
      landingData.articles = (data.articles || []).map((art, idx) => {
        const base = slugify(art.title || 'market-report')
        const stamp = normalizeDateSlug(art.created_at)
        const seeded = stamp ? `${base}-${stamp}` : base
        const count = (slugCounter.get(seeded) || 0) + 1
        slugCounter.set(seeded, count)
        const slug = count > 1 ? `${seeded}-${count}` : seeded

        return {
          ...art,
          content: art.content || art.excerpt || '',
          slug: slug || `market-report-${idx + 1}`,
        }
      })
      syncArticleFromRoute()
      applySeoMeta(selectedArticle.value || heroArticle.value || null)
      renderStructuredData()
    } else {
      dbState.value = 'DEGRADED'
    }
  } catch (err) {
    latency.value = Math.max(0, Math.round(performance.now() - started))
    dbState.value = 'DEGRADED'
    console.error(err)
  }
}

function slugify(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-') || 'market-report'
}

function normalizeDateSlug(value) {
  if (!value) return ''
  const dt = new Date(value)
  if (Number.isNaN(dt.getTime())) return ''
  const y = String(dt.getFullYear())
  const m = String(dt.getMonth() + 1).padStart(2, '0')
  const d = String(dt.getDate()).padStart(2, '0')
  return `${y}${m}${d}`
}

function articleHref(article) {
  return `/articles/${article.slug}`
}

function openArticle(article) {
  if (!article) return
  selectedArticle.value = article
  applySeoMeta(article)
  const slug = article.slug || slugify(article.title)
  nextTick(() => {
    if (articleBodyRef.value) {
      articleBodyRef.value.scrollTop = 0
    }
  })
  if (route.name !== 'article' || route.params.slug !== slug) {
    router.push({ name: 'article', params: { slug } })
  }
}

function closeArticle() {
  selectedArticle.value = null
  applySeoMeta(heroArticle.value || null)
  if (route.name === 'article') {
    router.push({ name: 'login' })
  }
}

function goLanding() {
  closeArticle()
  router.push({ name: 'login' })
}

function goArticles() {
  closeArticle()
  router.push({ name: 'login' })
  requestAnimationFrame(() => {
    const section = document.getElementById('articles')
    if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

function syncArticleFromRoute() {
  if (route.name !== 'article') {
    if (selectedArticle.value) {
      selectedArticle.value = null
    }
    applySeoMeta(heroArticle.value || null)
    return
  }

  const slug = String(route.params.slug || '')
  if (!slug || !landingData.articles.length) return
  const matched = landingData.articles.find(a => a.slug === slug)
  if (matched) {
    selectedArticle.value = matched
    applySeoMeta(matched)
    nextTick(() => {
      if (articleBodyRef.value) {
        articleBodyRef.value.scrollTop = 0
      }
    })
  }
}

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function setMetaTag(attr, key, value) {
  let el = document.head.querySelector(`meta[${attr}="${key}"]`)
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute(attr, key)
    document.head.appendChild(el)
  }
  el.setAttribute('content', value)
}

function setCanonical(url) {
  let el = document.head.querySelector('link[rel="canonical"]')
  if (!el) {
    el = document.createElement('link')
    el.setAttribute('rel', 'canonical')
    document.head.appendChild(el)
  }
  el.setAttribute('href', url)
}

function applySeoMeta(article) {
  const title = article?.title
    ? `${article.title} | Algovibe Intelligence`
    : DEFAULT_SEO.title
  const description = article?.excerpt
    ? String(article.excerpt).slice(0, 180)
    : DEFAULT_SEO.description
  const canonicalUrl = article?.slug
    ? `${window.location.origin}/articles/${article.slug}`
    : `${window.location.origin}/login`

  document.title = title
  setMetaTag('name', 'description', description)
  setMetaTag('property', 'og:title', title)
  setMetaTag('property', 'og:description', description)
  setMetaTag('property', 'og:url', canonicalUrl)
  setMetaTag('name', 'twitter:title', title)
  setMetaTag('name', 'twitter:description', description)
  setCanonical(canonicalUrl)
}

function renderStructuredData() {
  const baseUrl = window.location.origin
  const currentArticle = selectedArticle.value || heroArticle.value || null
  const graph = [
    {
      '@context': 'https://schema.org',
      '@type': 'WebSite',
      name: 'Algovibe Intelligence',
      url: `${baseUrl}/login`,
      inLanguage: 'id-ID',
      description: DEFAULT_SEO.description,
    },
    {
      '@type': 'CollectionPage',
      name: 'Algovibe Intelligence Market Reports',
      url: `${baseUrl}/login`,
      inLanguage: 'id-ID',
      hasPart: landingData.articles.slice(0, 8).map((article) => ({
        '@type': 'Article',
        headline: article.title,
        description: article.excerpt,
        articleSection: article.category,
        datePublished: article.created_at,
        url: `${baseUrl}/articles/${article.slug}`,
        author: {
          '@type': 'Organization',
          name: 'Algovibe Oracle',
        },
      })),
    },
  ]

  if (currentArticle?.slug) {
    graph.push({
      '@type': 'BreadcrumbList',
      itemListElement: [
        {
          '@type': 'ListItem',
          position: 1,
          name: 'Home',
          item: `${baseUrl}/login`,
        },
        {
          '@type': 'ListItem',
          position: 2,
          name: 'Articles',
          item: `${baseUrl}/login#articles`,
        },
        {
          '@type': 'ListItem',
          position: 3,
          name: currentArticle.title,
          item: `${baseUrl}/articles/${currentArticle.slug}`,
        },
      ],
    })
  }

  const payload = { '@context': 'https://schema.org', '@graph': graph }
  let script = document.getElementById('algovibe-landing-jsonld')
  if (!script) {
    script = document.createElement('script')
    script.type = 'application/ld+json'
    script.id = 'algovibe-landing-jsonld'
    document.head.appendChild(script)
  }
  script.textContent = JSON.stringify(payload)
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

// ── Indicator status helpers ──────────────────────────────────────────────────
function rsiClass(v) {
  const n = Number(v ?? 50)
  if (n > 70) return 'ind-ob'
  if (n < 30) return 'ind-os'
  if (n > 55) return 'ind-up'
  if (n < 45) return 'ind-dn'
  return 'ind-nt'
}
function rsiTag(v) {
  const n = Number(v ?? 50)
  if (n > 80) return 'EXOB'
  if (n > 70) return 'OB'
  if (n < 20) return 'EXOS'
  if (n < 30) return 'OS'
  if (n > 55) return 'HIGH'
  if (n < 45) return 'LOW'
  return 'NEUT'
}
function macdClass(v) { return Number(v ?? 0) > 0 ? 'ind-up' : Number(v ?? 0) < 0 ? 'ind-dn' : 'ind-nt' }
function bbClass(v) {
  const n = Number(v ?? 0.5)
  if (n > 0.85) return 'ind-ob'
  if (n < 0.15) return 'ind-os'
  if (n > 0.65) return 'ind-up'
  if (n < 0.35) return 'ind-dn'
  return 'ind-nt'
}
function bbTag(v) {
  const n = Number(v ?? 0.5)
  if (n > 0.85) return 'UPPER'
  if (n < 0.15) return 'LOWER'
  if (n > 0.65) return 'HIGH'
  if (n < 0.35) return 'LOW'
  return 'MID'
}
function stochClass(v) {
  const n = Number(v ?? 50)
  if (n > 80) return 'ind-ob'
  if (n < 20) return 'ind-os'
  if (n > 60) return 'ind-up'
  if (n < 40) return 'ind-dn'
  return 'ind-nt'
}
function stochTag(v) {
  const n = Number(v ?? 50)
  if (n > 80) return 'OB'
  if (n < 20) return 'OS'
  if (n > 60) return 'HIGH'
  if (n < 40) return 'LOW'
  return 'NEUT'
}
function emaCrossClass(v) {
  if (v === 'BULLISH') return 'ind-up'
  if (v === 'BEARISH') return 'ind-dn'
  return 'ind-nt'
}
function wrClass(v) {
  // Williams %R: 0 to -100. Above -20 = overbought, below -80 = oversold
  const n = Number(v ?? -50)
  if (n > -20) return 'ind-ob'
  if (n < -80) return 'ind-os'
  if (n > -50) return 'ind-up'
  return 'ind-dn'
}
function wrTag(v) {
  const n = Number(v ?? -50)
  if (n > -20) return 'OB'
  if (n < -80) return 'OS'
  if (n > -50) return 'HIGH'
  return 'LOW'
}
function slopeClass(v) { return Number(v ?? 0) > 0.01 ? 'ind-up' : Number(v ?? 0) < -0.01 ? 'ind-dn' : 'ind-nt' }
function rocClass(v) { return Number(v ?? 0) > 0 ? 'ind-up' : Number(v ?? 0) < 0 ? 'ind-dn' : 'ind-nt' }
function volClass(v) {
  const n = Number(v ?? 1)
  if (n >= 2.0) return 'ind-up'
  if (n <= 0.5) return 'ind-dn'
  return 'ind-nt'
}
function volTag(v) {
  const n = Number(v ?? 1)
  if (n >= 2.0) return 'SPIKE'
  if (n >= 1.5) return 'HIGH'
  if (n <= 0.5) return 'DRY'
  if (n <= 0.8) return 'LOW'
  return 'NORM'
}
function obvClass(v) { return Number(v ?? 0) > 0 ? 'ind-up' : Number(v ?? 0) < 0 ? 'ind-dn' : 'ind-nt' }
function bpClass(v) {
  const n = Number(v ?? 50)
  if (n > 60) return 'ind-up'
  if (n < 40) return 'ind-dn'
  return 'ind-nt'
}
function formatIndFloat(v) {
  const n = Number(v ?? 0)
  if (Math.abs(n) >= 100) return n.toFixed(1)
  if (Math.abs(n) >= 1) return n.toFixed(2)
  if (Math.abs(n) >= 0.001) return n.toFixed(4)
  return n.toFixed(6)
}
// ─────────────────────────────────────────────────────────────────────────────

function formatPrice(p) {
  if (!p) return '0'
  return Math.round(p).toLocaleString('id-ID')
}

function formatSignalPrice(price) {
  if (!price || price <= 0) return '-'
  return `${Math.round(price).toLocaleString('id-ID')} IDR`
}

function pickBestChange(source) {
  const candidates = [
    Number(source?.change_24h ?? 0),
    Number(source?.change ?? 0),
    Number(source?.ch_1h ?? 0),
    Number(source?.ch_15m ?? 0),
    Number(source?.ch_5m ?? 0),
  ]

  for (const val of candidates) {
    if (Number.isFinite(val) && Math.abs(val) > 0.0001) return val
  }
  return Number.isFinite(candidates[0]) ? candidates[0] : 0
}

function formatChange(value) {
  const n = Number(value || 0)
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

function getChangeClass(value) {
  const n = Number(value || 0)
  if (n > 0) return 'up'
  if (n < 0) return 'down'
  return 'flat'
}

function getDbStateClass(value) {
  const s = String(value || '').toUpperCase()
  if (s === 'OPTIMAL') return 'green'
  if (s === 'DEGRADED') return 'red'
  return 'amber'
}

function formatLogTime(value) {
  if (!value) return '--:--:--'
  const raw = String(value)

  // Handle ISO and common datetime formats directly.
  const parsed = new Date(raw)
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toLocaleTimeString('id-ID', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  }

  // Fallback for raw strings that include hh:mm:ss with microseconds.
  const m = raw.match(/(\d{2}:\d{2}:\d{2})/)
  if (m && m[1]) return m[1]

  return raw.slice(0, 8)
}

function formatDate(v) {
  if (!v) return '-'
  try {
    return new Date(v).toLocaleString('id-ID')
  } catch {
    return v
  }
}

function stripMarkdown(text) {
  return String(text || '')
    .replace(/^#{1,6}\s+/gm, '')       // headings
    .replace(/\*{1,3}([^*]+)\*{1,3}/g, '$1')  // bold/italic
    .replace(/_{1,2}([^_]+)_{1,2}/g, '$1')    // italic/underline
    .replace(/~~([^~]+)~~/g, '$1')             // strikethrough
    .replace(/`[^`]+`/g, '')                   // inline code
    .replace(/^[-*+]\s+/gm, '')               // list bullets
    .replace(/^\d+\.\s+/gm, '')              // ordered list
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // links
    .replace(/!\[[^\]]*\]\([^)]+\)/g, '')    // images
    .replace(/^[>|]\s*/gm, '')                // blockquote / table pipes
    .replace(/\|/g, ' ')                      // remaining table pipes
    .replace(/\n{2,}/g, ' ')                  // collapse blank lines
    .replace(/\s{2,}/g, ' ')                  // collapse spaces
    .trim()
}
</script>

<style scoped>
.blog-portal {
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

.header-container, .content-container, .footer-container {
  max-width: 1160px;
  margin: 0 auto;
  width: 100%;
  padding: 0 28px;
}

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

.hero {
  border: 1px solid rgba(124, 171, 200, 0.2);
  border-radius: 18px;
  padding: 30px;
  margin-bottom: 28px;
  background: rgba(8, 19, 29, 0.65);
}

.hero-primary {
  animation: rise-in 0.6s ease both;
}

.hero-secondary {
  animation: rise-in 0.8s ease both;
}

.hero-kicker {
  margin: 0 0 12px;
  color: #8eb8cf;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.5px;
}

.hero-copy h2 {
  margin: 0;
  font-size: clamp(28px, 5vw, 52px);
  line-height: 1.08;
  color: #f4f8fc;
  font-family: 'Source Serif 4', 'Georgia', serif;
}

.hero-copy p {
  margin: 16px 0 0;
  max-width: 820px;
  line-height: 1.7;
  color: #adbfcd;
  font-size: 16px;
}

.hero-meta {
  margin-top: 24px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.hero-meta span {
  font-size: 11px;
  font-weight: 700;
  color: #8fb2c8;
  border: 1px solid rgba(132, 179, 209, 0.22);
  background: rgba(13, 28, 41, 0.7);
  padding: 7px 10px;
  border-radius: 999px;
}

.hero-secondary-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  margin-bottom: 8px;
}

.featured-story h2 {
  margin: 0 0 10px;
  font-size: clamp(24px, 4vw, 36px);
  line-height: 1.16;
  color: #f6fafc;
  font-family: 'Source Serif 4', 'Georgia', serif;
}

.feature-excerpt {
  margin: 0 0 16px;
  color: #b1c2ce;
  line-height: 1.75;
  font-size: 15px;
}

.empty-story p {
  color: #9cb1bf;
  margin: 0;
}

.read-btn {
  border: 1px solid rgba(143, 193, 219, 0.3);
  background: rgba(14, 33, 47, 0.8);
  color: #b5dbef;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 800;
  padding: 8px 14px;
  cursor: pointer;
}

.section-block {
  margin-top: 38px;
}

.section-headline h2 {
  margin: 0;
  color: #f3f7fb;
  font-size: clamp(24px, 3vw, 34px);
  font-family: 'Source Serif 4', 'Georgia', serif;
}

.section-headline p {
  margin: 8px 0 20px;
  color: #9eb2c1;
  line-height: 1.65;
}

.articles-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.portal-article-card {
  padding: 20px;
  border-radius: 14px;
  border: 1px solid rgba(126, 168, 194, 0.2);
  background: rgba(7, 20, 29, 0.62);
  cursor: pointer;
  transition: transform 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease;
}

.portal-article-card:hover {
  transform: translateY(-3px);
  border-color: rgba(145, 193, 220, 0.45);
  box-shadow: 0 10px 24px rgba(4, 12, 19, 0.36);
}

.art-cat {
  font-size: 10px;
  font-weight: 800;
  color: #77c4e5;
  margin-bottom: 10px;
  letter-spacing: 1px;
}

.art-title {
  font-size: 24px;
  font-weight: 800;
  color: #f4f8fb;
  margin: 0 0 12px;
  line-height: 1.2;
  font-family: 'Source Serif 4', 'Georgia', serif;
}

.art-excerpt {
  font-size: 14px;
  color: #a5b9c7;
  line-height: 1.6;
  margin-bottom: 14px;
}

.art-meta {
  font-size: 10px;
  font-weight: 700;
  color: #7391a4;
}

.signals-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(270px, 1fr));
  gap: 16px;
  margin-top: 6px;
}

.sig-brief {
  background: rgba(8, 23, 33, 0.68);
  padding: 16px;
  border-radius: 14px;
  border: 1px solid rgba(125, 173, 198, 0.22);
}

/* Header: pair + verdict */
.sig-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 10px;
}

.sig-pair-wrap {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sig-pair-wrap .pair {
  font-weight: 900;
  font-size: 18px;
  color: #edf5fb;
  line-height: 1;
}

.pair-base {
  font-size: 10px;
  font-weight: 600;
  color: #6e8fa2;
  margin-left: 2px;
}

.sig-verdict {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.8px;
  padding: 3px 7px;
  border-radius: 4px;
  width: fit-content;
  background: rgba(100, 140, 160, 0.18);
  color: #8aabbc;
}
.sig-verdict.buy { background: rgba(0, 200, 100, 0.18); color: #00d87a; }
.sig-verdict.sell { background: rgba(220, 60, 80, 0.18); color: #ff4466; }
.sig-verdict.hold { background: rgba(100, 150, 190, 0.15); color: #7aabcc; }

.sig-scores {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-end;
}

.score-pill {
  font-size: 10px;
  font-weight: 800;
  padding: 2px 7px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}
.score-pill.buy { background: rgba(0, 180, 90, 0.15); color: #00d87a; border: 1px solid rgba(0, 200, 100, 0.25); }
.score-pill.sell { background: rgba(220, 60, 80, 0.12); color: #ff6680; border: 1px solid rgba(220, 60, 80, 0.25); }

/* Price row */
.sig-market {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  gap: 8px;
}

.sig-market .price {
  color: #9fc4d9;
  font-size: 11px;
  font-weight: 700;
}

.sig-market .change { font-size: 11px; font-weight: 800; }
.sig-market .change.up { color: #00ff88; }
.sig-market .change.down { color: #ff5577; }
.sig-market .change.flat { color: #91aabd; }

/* Technical indicator grid */
.ind-grid {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.ind-row {
  display: grid;
  grid-template-columns: 90px 1fr auto;
  align-items: center;
  gap: 6px;
  padding: 4px 7px;
  border-radius: 6px;
  background: rgba(10, 28, 40, 0.45);
  border-left: 2px solid rgba(100, 140, 170, 0.25);
  font-size: 11px;
}

/* Indicator state colors */
.ind-row.ind-up  { border-left-color: #00cc66; }
.ind-row.ind-dn  { border-left-color: #ff3355; }
.ind-row.ind-ob  { border-left-color: #ffaa00; }
.ind-row.ind-os  { border-left-color: #00d4ff; }
.ind-row.ind-nt  { border-left-color: rgba(100, 140, 170, 0.25); }

.ind-name {
  color: #7a9db2;
  font-weight: 600;
  white-space: nowrap;
  font-size: 10px;
  letter-spacing: 0.2px;
}

.ind-val {
  color: #c8dce8;
  font-weight: 700;
  font-size: 11px;
  text-align: right;
}

.ind-tag {
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.4px;
  text-align: right;
  min-width: 32px;
  color: #6e8fa2;
}
.ind-row.ind-up  .ind-tag { color: #00cc66; }
.ind-row.ind-dn  .ind-tag { color: #ff3355; }
.ind-row.ind-ob  .ind-tag { color: #ffaa00; }
.ind-row.ind-os  .ind-tag { color: #00d4ff; }

.activity-list {
  margin-top: 20px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}

.log-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 11px;
  line-height: 1.45;
  padding: 10px;
  border-radius: 10px;
  border: 1px solid rgba(111, 153, 177, 0.2);
  background: rgba(8, 19, 27, 0.56);
}

.log-item .time {
  color: #7bcbe8;
  font-weight: 800;
  min-width: 72px;
  flex: 0 0 72px;
  white-space: nowrap;
}

.log-item .msg {
  color: #96adbc;
  flex: 1;
}

.auth-modal {
  position: fixed;
  inset: 0;
  background: rgba(3, 9, 14, 0.82);
  backdrop-filter: blur(10px);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.auth-card {
  background: #111a24;
  width: 400px;
  padding: 40px;
  border-radius: 12px;
  border: 1px solid rgba(127, 176, 201, 0.25);
  position: relative;
}

.auth-card h3 { font-size: 14px; letter-spacing: 3px; margin-bottom: 30px; color: #8ca8bd; text-align: center; }

.auth-form { display: flex; flex-direction: column; gap: 20px; }
.input-group label { display: block; font-size: 10px; color: #8ca8bd; margin-bottom: 8px; font-weight: 800; }
.input-group input {
  width: 100%;
  background: #0c1520;
  border: 1px solid rgba(126, 170, 194, 0.3);
  padding: 12px;
  color: #fff;
  border-radius: 4px;
  outline: none;
}

.auth-form button {
  background: linear-gradient(120deg, #7ad4ef, #5fc5a4);
  color: #08202a;
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
  color: #87a5bb;
  font-size: 24px;
  cursor: pointer;
}

.error-msg { color: #ff3366; font-size: 11px; text-align: center; margin-top: 15px; }

.article-overlay {
  position: fixed;
  inset: 0;
  z-index: 1300;
  background: rgba(0, 8, 15, 0.75);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.article-modal {
  display: flex;
  width: min(900px, 100%);
  max-height: 85vh;
  overflow: hidden;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  border-radius: 12px;
  border: 1px solid rgba(122, 171, 196, 0.28);
  background: #0d1822;
  position: relative;
  padding: 32px;
}

.close-btn {
  position: absolute;
  top: 14px;
  right: 14px;
  border: none;
  background: transparent;
  color: #9ab3c2;
  font-size: 20px;
  cursor: pointer;
}

.reader-title {
  margin: 8px 0 12px;
  font-size: 28px;
  line-height: 1.25;
  color: #f3f8fc;
  font-family: 'Source Serif 4', 'Georgia', serif;
}

.reader-breadcrumb {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 11px;
  color: #85a6bb;
}

.reader-breadcrumb a {
  color: #85c8e8;
  text-decoration: none;
}

.reader-body {
  flex: 1;
  min-height: 0;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid rgba(119, 161, 184, 0.22);
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

.reader-content {
  color: #b5c6d2;
  line-height: 1.7;
  font-size: 15px;
}

/* ── Markdown-rendered elements (MarkdownIt → v-html) ── */
.reader-content :deep(h2) {
  color: #eef7ff;
  font-size: 19px;
  font-family: 'Source Serif 4', 'Georgia', serif;
  font-weight: 700;
  margin: 26px 0 10px;
  padding-bottom: 7px;
  border-bottom: 1px solid rgba(119, 161, 184, 0.2);
  line-height: 1.3;
}

.reader-content :deep(h3) {
  color: #c8dde9;
  font-size: 15px;
  font-weight: 700;
  margin: 20px 0 7px;
  line-height: 1.35;
}

.reader-content :deep(h4) {
  color: #a8c2d4;
  font-size: 13px;
  font-weight: 700;
  margin: 16px 0 6px;
  letter-spacing: 0.03em;
}

.reader-content :deep(p) {
  color: #b5c6d2;
  margin: 0 0 12px;
  line-height: 1.75;
  font-size: 15px;
}

.reader-content :deep(ul),
.reader-content :deep(ol) {
  color: #d0e2ec;
  padding-left: 22px;
  margin: 0 0 14px;
}

.reader-content :deep(li) {
  line-height: 1.65;
  font-size: 14px;
  margin-bottom: 5px;
}

.reader-content :deep(li > p) {
  margin: 0;
}

.reader-content :deep(strong) {
  color: #daeaf5;
  font-weight: 700;
}

.reader-content :deep(em) {
  color: #a8c4d8;
  font-style: italic;
}

.reader-content :deep(hr) {
  border: none;
  border-top: 1px solid rgba(119, 161, 184, 0.2);
  margin: 22px 0;
}

.reader-content :deep(a) {
  color: #5fc5e8;
  text-decoration: none;
}

.reader-content :deep(a:hover) {
  text-decoration: underline;
}

.reader-content :deep(code) {
  background: rgba(16, 31, 43, 0.8);
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 13px;
  color: #9fd3e8;
  font-family: 'Fira Code', 'Consolas', monospace;
}

.reader-content :deep(pre) {
  background: rgba(8, 18, 27, 0.85);
  border: 1px solid rgba(119, 161, 184, 0.18);
  border-radius: 8px;
  padding: 14px 16px;
  overflow-x: auto;
  margin-bottom: 14px;
}

.reader-content :deep(pre code) {
  background: none;
  padding: 0;
  font-size: 13px;
  color: #9fd3e8;
}

.reader-content :deep(blockquote) {
  border-left: 3px solid rgba(119, 161, 184, 0.4);
  margin: 14px 0;
  padding: 8px 16px;
  color: #8fafc0;
  font-style: italic;
}

.reader-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 16px;
  font-size: 13px;
  overflow-x: auto;
  display: block;
}

.reader-content :deep(th) {
  color: #eef7ff;
  background: rgba(16, 31, 43, 0.92);
  padding: 10px 12px;
  text-align: left;
  font-size: 12px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  border-bottom: 2px solid rgba(119, 161, 184, 0.28);
  white-space: nowrap;
}

.reader-content :deep(td) {
  color: #b5c6d2;
  padding: 9px 12px;
  border-bottom: 1px solid rgba(119, 161, 184, 0.1);
  font-size: 13px;
  line-height: 1.5;
  vertical-align: top;
}

.reader-content :deep(tr:last-child td) {
  border-bottom: none;
}

.reader-content :deep(tr:nth-child(even) td) {
  background: rgba(16, 31, 43, 0.35);
}
/* ── end markdown elements ── */

.reader-content-fallback {
  color: #b5c6d2;
  white-space: pre-wrap;
  word-wrap: break-word;
  line-height: 1.7;
  font-size: 15px;
}

.reader-section-title {
  margin: 8px 0 0;
  color: #eef7ff;
  font-size: 17px;
  line-height: 1.35;
  font-family: 'Source Serif 4', 'Georgia', serif;
}

.reader-paragraph {
  margin: 0;
  color: #b5c6d2;
}

.reader-list {
  margin: 0;
  padding-left: 20px;
  color: #d6e5ef;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.reader-list-ordered {
  padding-left: 24px;
}

.reader-table-wrap {
  overflow-x: auto;
  border: 1px solid rgba(119, 161, 184, 0.18);
  border-radius: 10px;
  background: rgba(7, 16, 25, 0.65);
}

.reader-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 520px;
}

.reader-table th,
.reader-table td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid rgba(119, 161, 184, 0.14);
  font-size: 13px;
  line-height: 1.5;
}

.reader-table th {
  color: #eef7ff;
  font-size: 12px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  background: rgba(16, 31, 43, 0.92);
}

.reader-table tr:last-child td {
  border-bottom: none;
}

.related-section {
  margin-top: 22px;
  border-top: 1px solid rgba(119, 161, 184, 0.22);
  padding-top: 14px;
}

.related-section h3 {
  margin: 0 0 12px;
  color: #9bc0d6;
  font-size: 14px;
  letter-spacing: 0.4px;
}

.related-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.related-list a {
  color: #c8deeb;
  text-decoration: none;
  font-size: 13px;
  line-height: 1.45;
}

.related-list a:hover {
  color: #87ccea;
}

.portal-footer {
  border-top: 1px solid rgba(118, 163, 188, 0.2);
  padding: 40px 0;
  background: rgba(8, 15, 24, 0.75);
}

.footer-container {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.market-strip { display: flex; gap: 16px; flex-wrap: wrap; font-size: 11px; color: #8aa6ba; font-weight: 700; }
.market-item .green { color: #00ff88; }
.market-item .red { color: #ff5f7a; }
.market-item .amber { color: #ffcf5a; }
.copyright { font-size: 10px; color: #7898ad; }

.fade-report-enter-active, .fade-report-leave-active { transition: all 0.6s ease; }
.fade-report-enter-from { opacity: 0; transform: translateX(20px); }
.fade-report-leave-to { opacity: 0; transform: translateX(-20px); }

.modal-fade-enter-active, .modal-fade-leave-active { transition: opacity 0.3s; }
.modal-fade-enter-from, .modal-fade-leave-to { opacity: 0; }

@keyframes rise-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 1024px) {
  .portal-nav a {
    display: none;
  }
  .articles-list {
    grid-template-columns: 1fr;
  }
  .reader-title { font-size: 22px; }
}

@media (max-width: 640px) {
  .header-container,
  .content-container,
  .footer-container {
    padding: 0 16px;
  }

  .hero,
  .article-modal {
    padding: 18px;
  }

  .article-overlay {
    padding: 12px;
    align-items: stretch;
  }

  .article-modal {
    max-height: calc(100vh - 24px);
  }

  .hero-copy p,
  .feature-excerpt {
    font-size: 14px;
  }
}
</style>
