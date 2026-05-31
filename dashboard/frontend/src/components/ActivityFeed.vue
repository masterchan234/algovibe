<template>
  <div class="activity-feed">
    <!-- Main Header -->
    <header class="feed-main-header">
      <div class="brand-zone">
        <span class="title">Execution Engine</span>
        <span class="badge" :class="store.connected ? 'live' : 'offline'">
          {{ store.connected ? '● LIVE' : '○ OFFLINE' }}
        </span>
      </div>
    </header>

    <!-- Navigation Bar (Segmented Control) -->
    <nav class="feed-nav">
      <div class="segmented-control">
        <button 
          :class="{ active: filterMode === 'overview' }" 
          @click="filterMode = 'overview'"
        >
          Overview
        </button>
        <button 
          :class="{ active: filterMode === 'history' }" 
          @click="filterMode = 'history'; historyPage = 1"
        >
          History
          <span v-if="store.stats.history_count > 0" class="tab-count history">{{ store.stats.history_count }}</span>
        </button>
        <button
          :class="{ active: filterMode === 'analysis' }"
          @click="filterMode = 'analysis'"
        >
          <span class="tab-icon">🧠</span> Analysis
        </button>
      </div>
    </nav>

    <!-- Contextual Panel (Positions / History / Analysis) -->
    <div class="context-panel expanded">

      <!-- Active Positions Mode (Overview) -->
      <div class="positions-section" v-if="filterMode === 'overview' || (filterMode === 'all' && store.positions.length > 0)">
        <div class="section-header">
          <div class="col-pair">PAIR</div>
          <div class="col-size">SIZE</div>
          <div class="col-price-progress text-center">PRICE & TARGETS</div>
          <div class="col-pnl">PnL (NET)</div>
          <div class="col-exit">ACT</div>
        </div>
        <div class="positions-list">
          <div
            v-for="pos in visiblePositions"
            :key="pos.pair"
            class="position-item"
            :class="[isDustPosition(pos) ? 'dust' : (pos.pnl_net_idr >= 0 ? 'green' : 'red'), { 'exit-pending': isExitRequested(pos.pair) }]"
          >
            <!-- Order Book Depth Background -->
            <div class="depth-bg" v-if="getCoinDepth(pos.pair)">
              <div class="depth-bg-bid" :style="{ width: getCoinDepth(pos.pair).bidPct + '%' }"></div>
              <div class="depth-bg-ask" :style="{ width: getCoinDepth(pos.pair).askPct + '%' }"></div>
            </div>

            <div class="col-pair" style="position: relative; z-index: 1;">
              <div class="pair-name">{{ formatPair(pos.pair) }}</div>
              <div style="display: flex; gap: 6px; margin-top: 4px;">
                <div class="status-pill" :class="pos.status.toLowerCase()">{{ displayStatus(pos) }}</div>
                <div v-if="pos.is_fast_opportunity" class="status-pill fast-opportunity" title="High-opportunity fast trade using reserved capacity">⚡ FAST</div>
              </div>
            </div>
            
            <div class="col-size" style="position: relative; z-index: 1;">
              <div class="val-idr">{{ formatIdr(pos.amount_quote, false) }}</div>
              <div class="val-coin">{{ formatCoin(pos.amount_crypto ?? pos.amount_coin) }} units</div>
            </div>

            <div class="col-price-progress" style="position: relative; z-index: 1;">
              <div class="price-labels">
                <span class="entry-label">ENT: {{ formatNumber(pos.entry_price) }}</span>
                <span class="current-label">MARK: {{ formatNumber(pos.last_price || 0) }}</span>
              </div>
              <div v-if="!isDustPosition(pos)" class="progress-container">
                <div class="progress-bg"></div>
                <div 
                  class="progress-fill" 
                  :style="{ 
                    left: calculatePosition(pos) + '%',
                    backgroundColor: (pos.pnl_net_pct ?? pos.pnl_pct) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'
                  }"
                ></div>
                <div class="target-marker sl" title="Stop Loss"></div>
                <div class="target-marker tp" title="Take Profit"></div>
              </div>
              <div v-else class="dust-note">
                DUST tracked - waiting for BUY signal to top-up and promote back to OPEN.
              </div>
              <div v-if="!isDustPosition(pos)" class="target-labels">
                <span class="sl-val">SL -{{ Math.abs(pos.target_sl || 0).toFixed(1) }}%</span>
                <span class="tp-val">TP +{{ Math.abs(pos.target_tp || 0).toFixed(1) }}%</span>
              </div>
            </div>

            <div class="col-pnl" style="position: relative; z-index: 1;">
              <div class="pnl-val">{{ formatIdr(pos.pnl_net_idr) }}</div>
              <div class="pnl-pct">{{ (pos.pnl_net_pct ?? pos.pnl_pct) > 0 ? '+' : '' }}{{ Number((pos.pnl_net_pct ?? pos.pnl_pct) || 0).toFixed(2) }}%</div>
              <div class="pnl-kind" :title="isDustPosition(pos) ? 'Tracked dust estimate only' : 'Execution-aware net estimate from orderbook depth and fees'">
                {{ isDustPosition(pos) ? 'DUST TRACKED' : 'NET EXIT EST.' }}
              </div>
            </div>

            <div class="col-exit" style="position: relative; z-index: 1;">
              <button
                v-if="(pos.status || '').toUpperCase() === 'OPEN' && (pos.pnl_net_idr / (pos.amount_quote || 1)) < manualExitMaxNetPct"
                class="btn-manual-exit"
                :disabled="exitingPairs.has(pos.pair)"
                @click.stop="handleManualExit(pos.pair)"
                title="Manual EXIT — closes position at market price (Net PnL &lt; 0.1% only)"
              >
                {{ exitingPairs.has(pos.pair) ? '⏳' : 'EXIT' }}
              </button>
              <span v-else-if="isExitRequested(pos.pair)" class="exit-queued">QUEUED</span>
            </div>
          </div>
          <div v-if="filterMode === 'overview' && visiblePositions.length === 0" class="empty-state">
            No active positions.
          </div>
        </div>
      </div>

      <!-- Real History Mode (Fetched from DB) -->
      <div class="history-section" v-if="filterMode === 'history'">
        <div class="section-header">
          <div class="col-pair">PAIR</div>
          <div class="col-size">SIZE</div>
          <div class="col-price" style="display: flex; justify-content: space-between; padding: 0 10px;">
            <span>ENTRY</span>
            <span>EXIT</span>
          </div>
          <div class="col-pnl">RESULT</div>
        </div>
        <div class="history-list custom-scroll">
          <div
            v-for="trade in paginatedHistory"
            :key="trade.created_at"
            class="history-item"
            :class="trade.pnl_quote >= 0 ? 'green' : 'red'"
          >
            <div class="history-row">
              <div class="col-pair">
                <div class="pair-name">{{ formatPair(trade.pair) }}</div>
                <div class="time">{{ formatTimeShort(trade.created_at) }}</div>
              </div>
              <div class="col-size">
                <div class="val-idr">{{ formatIdr(trade.amount_quote || 0) }}</div>
              </div>
              <div class="col-price" style="display: flex; justify-content: space-between; padding: 0 10px;">
                <div class="entry mono">{{ formatNumber(trade.entry_price) }}</div>
                <div class="exit mono" style="font-weight: 700;">{{ formatNumber(trade.exit_price) }}</div>
              </div>
              <div class="col-pnl">
                <div class="pnl-val">{{ formatIdr(trade.pnl_quote) }}</div>
                <div class="pnl-pct">{{ trade.pnl_pct > 0 ? '+' : '' }}{{ (trade.pnl_pct || 0).toFixed(2) }}%</div>
                <div class="exit-reason" :class="exitReasonClass(trade)" v-if="trade.reason" style="margin-top: 4px;">{{ displayTradeReason(trade) }}</div>
              </div>
            </div>
          </div>
          <div v-if="sortedRecentTrades.length === 0" class="empty-state">
            No historical trades found.
          </div>
        </div>
        <div v-if="sortedRecentTrades.length > historyPageSize" class="pagination">
          <button class="page-btn" :disabled="historyPage === 1" @click="historyPage--">&lt;</button>
          <span class="page-info">{{ historyPage }} / {{ totalHistoryPages }}</span>
          <button class="page-btn" :disabled="historyPage === totalHistoryPages" @click="historyPage++">&gt;</button>
        </div>
      </div>

      <!-- Live Intelligence Stream (AI Analysis Only) -->
      <div class="analysis-section" v-if="filterMode === 'analysis'" style="flex: 1; display: flex; flex-direction: column; min-height: 0;">
        <div class="events-list custom-scroll" ref="eventsList">
          <transition-group name="feed-slide" tag="div">
            <div
              v-for="item in paginatedStream"
              :key="item.id"
              class="feed-item"
              :class="[itemClass(item)]"
            >
              <div class="feed-top">
                <span class="feed-time">{{ formatTime(item.created_at) }}</span>
                <span class="feed-tag" :class="tagClass(item)">{{ item.side }}</span>
                <span class="feed-pair">{{ formatPair(item.pair) }}</span>
              </div>
              <div class="feed-msg">{{ item.message }}</div>
            </div>
          </transition-group>
        </div>
        <div v-if="filteredStream.length > streamPageSize" class="pagination stream-pagination">
          <button class="page-btn" :disabled="streamPage === 1" @click="streamPage--">&lt;</button>
          <span class="page-info">{{ streamPage }} / {{ totalStreamPages }}</span>
          <button class="page-btn" :disabled="streamPage === totalStreamPages" @click="streamPage++">&gt;</button>
        </div>
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'

const { getAuthHeaders } = useAuth()

const store = useTradeStore()
const filterMode = ref('overview')

const historyPage = ref(1)
const historyPageSize = 10
const streamPage = ref(1)
const streamPageSize = 15

// Threshold from server config — EXIT allowed only when Net PnL < this value
const manualExitMaxNetPct = computed(() => store.stats.manual_exit_max_net_pct ?? 0.001)

// Track pairs currently being exited to disable the button during processing
const exitingPairs = ref(new Set())
const exitRequestedPairs = ref(new Set())
const temporarilyHiddenExitRows = ref(new Set())

const visiblePositions = computed(() =>
  store.positions.filter(p => !temporarilyHiddenExitRows.value.has(String(p.pair || '').toLowerCase()))
)

const sortedRecentTrades = computed(() => {
  const trades = store.stats.recent_trades || []
  return [...trades].sort((a, b) => {
    const timeA = a.created_at ? new Date(a.created_at).getTime() : 0
    const timeB = b.created_at ? new Date(b.created_at).getTime() : 0
    return timeB - timeA
  })
})

const totalHistoryPages = computed(() => Math.ceil(sortedRecentTrades.value.length / historyPageSize) || 1)
const paginatedHistory = computed(() => {
  const start = (historyPage.value - 1) * historyPageSize
  return sortedRecentTrades.value.slice(start, start + historyPageSize)
})

const activeVisibleCount = computed(() =>
  visiblePositions.value.filter(p => ['OPEN', 'EXITING'].includes(String(p.status || '').toUpperCase())).length
)

function normalizePair(pair) {
  return String(pair || '').toLowerCase()
}

function isExitRequested(pair) {
  return exitRequestedPairs.value.has(normalizePair(pair))
}

function isDustPosition(pos) {
  return String(pos?.status || '').toUpperCase() === 'DUST'
}

function displayStatus(pos) {
  if (isExitRequested(pos.pair) && String(pos.status || '').toUpperCase() === 'EXITING') {
    return 'EXIT REQUESTED'
  }
  if (isDustPosition(pos)) {
    return 'DUST'
  }
  return pos.status
}

function displayTradeReason(trade) {
  const reason = trade.reason || ''
  const pnl = Number(trade.pnl_quote || 0)
  if (reason === 'TAKE_PROFIT') return pnl < 0 ? 'TP · NET LOSS' : 'TAKE PROFIT'
  if (reason === 'STOP_LOSS') return pnl >= 0 ? 'TRAIL STOP' : 'STOP LOSS'
  if (reason === 'HARD_STOP_LOSS') return 'HARD STOP'
  if (reason === 'EXIT_TIMEOUT') return 'TIMEOUT'
  if (reason === 'BASKET_EXIT') return 'BASKET EXIT'
  if (reason === 'MANUAL_DASHBOARD_EXIT') return 'MANUAL EXIT'
  if (reason === 'FORCE_PROFIT_HIGH_TIER') return 'FORCE PROFIT'
  if (reason === 'RAPID_TP_GOOD_PROFIT') return 'RAPID TP'
  return reason.replace(/_/g, ' ')
}

function exitReasonClass(trade) {
  const reason = trade.reason || ''
  const pnl = Number(trade.pnl_quote || 0)
  if (reason === 'TAKE_PROFIT' && pnl < 0) return 'reason-tp-loss'
  if (reason === 'TAKE_PROFIT') return 'reason-tp'
  if ((reason === 'STOP_LOSS' || reason === 'HARD_STOP_LOSS') && pnl >= 0) return 'reason-trail'
  if (reason === 'STOP_LOSS' || reason === 'HARD_STOP_LOSS') return 'reason-sl'
  return 'reason-other'
}

function markExitRequestedUx(pair) {
  const key = normalizePair(pair)
  exitRequestedPairs.value = new Set([...exitRequestedPairs.value, key])

  // Hide row briefly to create instant feedback after successful click.
  setTimeout(() => {
    temporarilyHiddenExitRows.value = new Set([...temporarilyHiddenExitRows.value, key])
  }, 700)

  // Restore row visibility if it is still present, and clear requested marker.
  setTimeout(() => {
    const nextHidden = new Set(temporarilyHiddenExitRows.value)
    nextHidden.delete(key)
    temporarilyHiddenExitRows.value = nextHidden

    const nextReq = new Set(exitRequestedPairs.value)
    nextReq.delete(key)
    exitRequestedPairs.value = nextReq
  }, 12000)
}

async function handleManualExit(pair) {
  if (exitingPairs.value.has(pair)) return
  if (!confirm(`Exit position for ${pair.replace(/idr$/i, '').toUpperCase()} at market price?`)) return

  exitingPairs.value = new Set([...exitingPairs.value, pair])
  try {
    const backendUrl = import.meta.env.DEV
      ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
      : window.location.origin
    const res = await fetch(`${backendUrl}/api/trade/manual-exit`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders(),
      },
      body: JSON.stringify({ pair }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      if (res.status === 401 || err.login_required) {
        window.dispatchEvent(new CustomEvent('algovibe:require-login', {
          detail: { source: 'manual-exit', pair }
        }))
        alert('Login session required for manual EXIT. Please login first.')
      } else {
        alert(`Exit failed: ${err.error || res.statusText}`)
      }
    } else {
      const target = store.positions.find(p => (p.pair || '').toLowerCase() === String(pair).toLowerCase())
      if (target) {
        target.status = 'EXITING'
      }
      markExitRequestedUx(pair)
    }
  } catch (e) {
    alert(`Exit request error: ${e.message}`)
  } finally {
    // Keep button disabled for 5s to avoid double-fire while bot processes
    setTimeout(() => {
      const next = new Set(exitingPairs.value)
      next.delete(pair)
      exitingPairs.value = next
    }, 5000)
  }
}

const openPairSet = computed(() => new Set(store.positions.map(p => (p.pair || '').toLowerCase())))

const activeCoinPositions = computed(() =>
  store.positions.filter((p) => ['TP HOLD', 'EXIT-MONITOR'].includes(String(p?.status || '').toUpperCase()))
)

watch(openPairSet, (pairs) => {
  const alive = new Set(Array.from(pairs || []))

  const nextReq = new Set(Array.from(exitRequestedPairs.value).filter(p => alive.has(p)))
  if (nextReq.size !== exitRequestedPairs.value.size) {
    exitRequestedPairs.value = nextReq
  }

  const nextHidden = new Set(Array.from(temporarilyHiddenExitRows.value).filter(p => alive.has(p)))
  if (nextHidden.size !== temporarilyHiddenExitRows.value.size) {
    temporarilyHiddenExitRows.value = nextHidden
  }
}, { immediate: true })

const filteredStream = computed(() => {
  const items = store.activityFeed
  const _sortDesc = (arr) => [...arr].sort((a, b) => {
    const tA = a.created_at ? new Date(a.created_at).getTime() : 0
    const tB = b.created_at ? new Date(b.created_at).getTime() : 0
    return tB - tA
  })

  // Overview: show everything except dense market data, newest first
  return _sortDesc(items.filter((item) => {
    const src = String(item?.source || '').toUpperCase()
    return src !== 'MARKET_WS'
  }))
})

const totalStreamPages = computed(() => Math.ceil(filteredStream.value.length / streamPageSize) || 1)
const paginatedStream = computed(() => {
  const start = (streamPage.value - 1) * streamPageSize
  return filteredStream.value.slice(start, start + streamPageSize)
})

function itemClass(item) {
  const side = (item.side || '').toUpperCase()
  if (side === 'LLM_EXIT_SIGNAL') return 'type-llm'
  if (side === 'BUY' || side === 'LONG') return 'type-buy'
  if (side === 'SELL' || side === 'SHORT') return 'type-sell'
  if (side.includes('AI') || side.includes('SYSTEM')) return 'type-ai'
  if (side === 'ERROR' || side === 'WARN') return 'type-warn'
  return 'type-info'
}

function tagClass(item) {
  const side = (item.side || '').toUpperCase()
  if (side === 'LLM_EXIT_SIGNAL') return 'tag-llm'
  if (side === 'BUY' || side === 'LONG') return 'tag-buy'
  if (side === 'SELL' || side === 'SHORT') return 'tag-sell'
  if (side.includes('AI') || side.includes('SYSTEM')) return 'tag-ai'
  if (side === 'ERROR' || side === 'WARN') return 'tag-warn'
  return 'tag-info'
}

function formatTime(ts) {
  if (!ts) return '--:--'
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch { return ts.slice(-8) }
}

function formatPair(pair) {
  if (!pair) return ''
  if (pair.toLowerCase() === 'system' || pair.toLowerCase() === 'global') return pair.toUpperCase()
  return pair.replace(/(idr|usdt)$/i, '').toUpperCase()
}

function formatTimeShort(ts) {
  if (!ts) return ''
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })
  } catch { return '' }
}

function calculatePosition(pos) {
  const entry = Number(pos.entry_price || 0);
  const current = Number(pos.exit_price || 0);
  const targetTp = Number(pos.target_tp || 0);
  const targetSl = Number(pos.target_sl || 0);
  
  const tpPrice = entry * (1 + targetTp / 100);
  const slPrice = entry * (1 - targetSl / 100);
  
  if (tpPrice === slPrice) return 50;
  
  let pct = ((current - slPrice) / (tpPrice - slPrice)) * 100;
  return Math.min(Math.max(pct, 0), 100);
}

function formatNumber(val) {
  const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
  if (isUsdt) {
    return Number(val || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
  } else {
    return Number(val || 0).toLocaleString('id-ID', { maximumFractionDigits: 0 })
  }
}

function formatCoin(val) {
  const n = Number(val) || 0
  const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
  if (n === 0) return '0'
  if (n < 0.001) return n.toFixed(8)
  if (n < 0.1) return n.toFixed(6)
  if (n < 1) return n.toFixed(4)
  if (n < 1000) return n.toFixed(2)
  return n.toLocaleString(isUsdt ? 'en-US' : 'id-ID', { maximumFractionDigits: 2 })
}

function formatIdr(val, signed = true) {
  const n = Number(val) || 0
  const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
  const prefix = signed ? (n >= 0 ? '+' : '-') : ''
  const abs = Math.abs(n)
  if (isUsdt) {
    if (abs >= 1_000) return `${prefix}$${(abs / 1_000).toFixed(1)}k`
    return `${prefix}$${abs.toFixed(2)}`
  }
  if (abs >= 1_000_000) return `${prefix}Rp ${(abs / 1_000_000).toFixed(2)}M`
  return `${prefix}Rp ${abs.toLocaleString('id-ID')}`
}

function getCoinDepth(pair) {
  if (!pair) return null
  const pName = pair.toLowerCase()
  const depth = store.marketDepths[pName]
  if (!depth) return null
  
  const bidDepth = depth[0] || 0
  const askDepth = depth[1] || 0
  const total = bidDepth + askDepth
  if (total === 0) return null

  return {
    bidPct: ((bidDepth / total) * 100).toFixed(1),
    askPct: ((askDepth / total) * 100).toFixed(1),
    bidVol: bidDepth,
    askVol: askDepth
  }
}
</script>

<style scoped>
.activity-feed {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-surface);
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid var(--border-trader);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}

.feed-main-header {
  padding: 16px 20px;
  background: var(--bg-deep);
  border-bottom: 1px solid var(--border-trader);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.title { font-weight: 900; font-size: 14px; color: var(--text-primary); text-transform: uppercase; letter-spacing: 1px; }

.feed-nav { padding: 12px 20px; background: var(--bg-surface); border-bottom: 1px solid rgba(48, 54, 61, 0.5); }

.segmented-control {
  display: flex;
  background: var(--bg-deep);
  padding: 4px;
  border-radius: 8px;
  border: 1px solid var(--border-trader);
}

.segmented-control button {
  flex: 1; background: transparent; border: none; color: var(--text-secondary); font-size: 11px;
  font-weight: 700; padding: 8px 0; border-radius: 6px; cursor: pointer; transition: all 0.2s;
}

.segmented-control button.active { background: var(--border-light); color: #58a6ff; }

.tab-count { background: #f85149; color: white; font-size: 9px; padding: 1px 5px; border-radius: 10px; margin-left: 5px; }
.tab-count.history { background: var(--border-trader); color: var(--text-secondary); border: 1px solid rgba(139, 148, 158, 0.2); }
.tab-count.llm { background: rgba(255, 51, 102, 0.15); color: #ff3366; border: 1px solid rgba(255, 51, 102, 0.25); }

.context-panel { background: var(--bg-deep); max-height: 60%; overflow-y: auto; border-bottom: 1px solid var(--border-trader); transition: all 0.3s; }
.context-panel.expanded { flex: 1; max-height: 100%; border-bottom: none; display: flex; flex-direction: column; }

.section-header {
  display: flex;
  padding: 10px 20px;
  background: rgba(13, 17, 23, 0.8);
  border-bottom: 1px solid var(--border-trader);
  font-size: 10px;
  font-weight: 800;
  color: #484f58;
  letter-spacing: 0.5px;
}

.col-pair { flex: 1.2; min-width: 70px; }
.col-size { flex: 1; text-align: right; padding-right: 10px; min-width: 60px; }
.col-price-progress { flex: 2; padding: 0 5px; min-width: 110px; }
.col-price { flex: 2.5; padding: 0 10px; min-width: 160px; }
.col-pnl { flex: 1.2; text-align: right; min-width: 70px; }
.col-exit { flex: 0 0 75px; text-align: center; display: flex; justify-content: flex-end; align-items: center; margin-left: auto; padding-right: 5px; }
.text-center { text-align: center; }
.mono { font-family: monospace; }

.position-item, .history-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  margin: 6px 8px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.04);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

.position-item::before, .history-item::before {
  content: '';
  position: absolute;
  top: 0; left: 0; bottom: 0;
  width: 4px;
  background: transparent;
  transition: all 0.3s ease;
}

.position-item.green::before, .history-item.green::before { background: #39d353; box-shadow: 0 0 12px #39d353; }
.position-item.red::before, .history-item.red::before { background: #ff4d4d; box-shadow: 0 0 12px #ff4d4d; }

.position-item:hover, .history-item:hover {
  transform: translateY(-2px);
  background: rgba(255, 255, 255, 0.04);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
  border-color: rgba(255, 255, 255, 0.08);
}

.position-item.exit-pending {
  border-left: 3px solid rgba(210, 153, 34, 0.5);
  background: rgba(210, 153, 34, 0.06);
}

.position-item.dust {
  border-left: 3px solid rgba(139, 148, 158, 0.45);
  background: rgba(139, 148, 158, 0.05);
}

.depth-bg {
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  display: flex;
  z-index: 0;
  opacity: 0.15;
  pointer-events: none;
}
.depth-bg-bid { height: 100%; background: #3fb950; transition: width 0.5s ease; }
.depth-bg-ask { height: 100%; background: #f85149; transition: width 0.5s ease; }

.pair-name { font-weight: 900; color: var(--text-primary); font-size: 13px; margin-bottom: 2px; }
.exit-reason {
  font-size: 8px; font-weight: 700; text-transform: uppercase;
  color: var(--text-secondary); letter-spacing: 0.4px; margin-top: 2px; opacity: 0.75;
}
.exit-reason.reason-tp { color: #3fb950; opacity: 0.9; }
.exit-reason.reason-tp-loss { color: #f0883e; opacity: 1; font-weight: 900; letter-spacing: 0.6px; }
.exit-reason.reason-trail { color: #58a6ff; opacity: 0.9; }
.exit-reason.reason-sl { color: #f85149; opacity: 0.85; }
.exit-reason.reason-other { color: var(--text-secondary); opacity: 0.75; }
.status-pill {
  display: inline-block;
  font-size: 8px;
  font-weight: 900;
  padding: 2px 6px;
  border-radius: 6px;
  text-transform: uppercase;
  background: rgba(255, 255, 255, 0.05);
  color: var(--text-secondary);
  border: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow: inset 0 1px 2px rgba(255, 255, 255, 0.05);
}
.status-pill.exiting { color: #f85149; border: 1px solid rgba(248, 81, 73, 0.3); background: rgba(248, 81, 73, 0.1); box-shadow: 0 0 8px rgba(248, 81, 73, 0.2); }
.status-pill.pending_buy { color: #d29922; border: 1px solid rgba(210, 153, 34, 0.3); background: rgba(210, 153, 34, 0.1); box-shadow: 0 0 8px rgba(210, 153, 34, 0.2); }
.status-pill.open { color: #39d353; border: 1px solid rgba(57, 211, 83, 0.3); background: rgba(57, 211, 83, 0.1); box-shadow: 0 0 8px rgba(57, 211, 83, 0.2); }
.status-pill.fast-opportunity { 
  color: #fbbf24;
  background: rgba(251, 191, 36, 0.08);
  border: 1px solid rgba(251, 191, 36, 0.3);
  font-size: 9px;
}

.val-idr { font-weight: 800; font-size: 12px; color: #c9d1d9; }
.val-coin, .time { font-size: 10px; color: #484f58; font-family: monospace; }

.col-price-progress {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.price-labels, .target-labels {
  display: flex;
  justify-content: space-between;
  font-size: 9px;
  font-weight: 700;
  font-family: monospace;
}
.entry-label { color: var(--text-secondary); }
.current-label { color: var(--text-primary); font-weight: 900; }
.sl-val { color: rgba(248, 81, 73, 0.7); }
.tp-val { color: rgba(63, 185, 80, 0.7); }

.progress-container {
  position: relative;
  height: 6px;
  margin: 2px 0;
}
.progress-bg {
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  background: var(--border-light);
  border-radius: 3px;
}
.progress-fill {
  position: absolute;
  top: 1px; bottom: 1px;
  width: 4px;
  border-radius: 2px;
  box-shadow: 0 0 8px currentColor;
  z-index: 2;
  transition: left 0.3s ease;
}
.target-marker {
  position: absolute;
  top: -2px; bottom: -2px;
  width: 1px;
  z-index: 1;
}
.target-marker.sl { left: 0%; background: #f85149; }
.target-marker.tp { left: 100%; background: #3fb950; }

.pnl-val { font-weight: 900; font-size: 13px; }
.pnl-pct { font-size: 11px; font-weight: 800; padding: 1px 4px; border-radius: 4px; display: inline-block; }
.pnl-kind { font-size: 9px; font-weight: 700; opacity: 0.7; margin-top: 3px; letter-spacing: 0.3px; }
.dust-note {
  font-size: 9px;
  font-weight: 700;
  color: var(--text-secondary);
  margin-top: 4px;
  letter-spacing: 0.2px;
}

.green .pnl-val { color: #39d353; text-shadow: 0 0 12px rgba(57, 211, 83, 0.4); }
.green .pnl-pct { background: rgba(57, 211, 83, 0.15); color: #39d353; border: 1px solid rgba(57, 211, 83, 0.3); box-shadow: 0 0 10px rgba(57, 211, 83, 0.2); }
.red .pnl-val { color: #ff4d4d; text-shadow: 0 0 12px rgba(255, 77, 77, 0.4); }
.red .pnl-pct { background: rgba(255, 77, 77, 0.15); color: #ff4d4d; border: 1px solid rgba(255, 77, 77, 0.3); box-shadow: 0 0 10px rgba(255, 77, 77, 0.2); }

.empty-state { padding: 20px; text-align: center; color: #484f58; font-size: 12px; font-style: italic; }

.events-list { flex: 1; overflow-y: auto; background: var(--bg-deep); }
.feed-item { padding: 10px 20px; border-bottom: 1px solid rgba(48, 54, 61, 0.1); border-left: 3px solid transparent; }
.feed-item.type-buy  { border-left-color: #3fb950; }
.feed-item.type-sell { border-left-color: #f85149; }
.feed-item.type-ai   { border-left-color: #a371f7; }
.feed-item.type-llm  { border-left-color: #ff3366; background: rgba(255, 51, 102, 0.03); }

.feed-top { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }
.feed-time { color: var(--text-secondary); font-size: 10px; }
.feed-tag { font-size: 9px; font-weight: 900; padding: 1px 4px; border-radius: 3px; }
.tag-buy  { background: rgba(63, 185, 80, 0.1);   color: #3fb950; }
.tag-sell { background: rgba(248, 81, 73, 0.1);  color: #f85149; }
.tag-ai   { background: rgba(163, 113, 247, 0.1); color: #a371f7; }
.tag-llm  { background: rgba(255, 51, 102, 0.1);  color: #ff3366; font-size: 8px; }
.feed-pair { color: #58a6ff; font-weight: 800; font-size: 11px; }
.feed-msg { color: #c9d1d9; font-size: 12px; }
.mono-text { font-family: monospace; font-size: 11px; }

.badge { font-size: 9px; font-weight: 900; padding: 2px 8px; border-radius: 12px; }
.badge.live { color: #3fb950; background: rgba(63, 185, 80, 0.1); border: 1px solid rgba(63, 185, 80, 0.2); }
.badge.offline { color: var(--text-secondary); background: rgba(139, 148, 158, 0.1); border: 1px solid rgba(139, 148, 158, 0.2); }

.history-item { flex-direction: column; align-items: stretch; gap: 8px; }
.history-row { display: flex; align-items: center; gap: 15px; width: 100%; }

.ai-audit-trail {
  margin-top: 8px;
  background: rgba(163, 113, 247, 0.05);
  border-radius: 6px;
  border-left: 2px solid #a371f7;
  padding: 8px 12px;
}

.audit-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.audit-icon { font-size: 12px; }
.audit-title { font-size: 9px; font-weight: 900; color: #a371f7; letter-spacing: 1px; }
.audit-confidence { margin-left: auto; font-size: 9px; color: var(--text-secondary); font-weight: 700; }

.audit-body { display: flex; align-items: flex-start; gap: 8px; }
.audit-verdict {
  font-size: 9px;
  font-weight: 900;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--border-light);
  color: #c9d1d9;
}
.audit-verdict.buy { color: #3fb950; }
.audit-verdict.sell { color: #f85149; }

.audit-text {
  margin: 0;
  font-size: 11px;
  line-height: 1.4;
  color: var(--text-secondary);
  font-style: italic;
}

.btn-manual-exit {
  background: linear-gradient(135deg, #f85149 0%, #da3633 100%);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 10px;
  font-weight: 900;
  letter-spacing: 0.8px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 4px 12px rgba(248, 81, 73, 0.4);
  white-space: nowrap;
}
.btn-manual-exit:hover:not(:disabled) { 
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(248, 81, 73, 0.6);
  background: linear-gradient(135deg, #ff6b63 0%, #ed4845 100%);
}
.btn-manual-exit:disabled { opacity: 0.45; cursor: not-allowed; }

.exit-queued {
  display: inline-block;
  font-size: 9px;
  font-weight: 900;
  letter-spacing: 0.5px;
  color: #d29922;
  border: 1px solid rgba(210, 153, 34, 0.4);
  border-radius: 4px;
  padding: 2px 6px;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 8px 0;
  border-top: 1px solid var(--border-trader);
  background: var(--bg-surface);
}

.stream-pagination {
  border-top: 1px solid rgba(48, 54, 61, 0.5);
  background: var(--bg-deep);
}

.page-btn {
  background: rgba(255,255,255,0.05);
  color: var(--text-primary);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 4px;
  padding: 4px 10px;
  cursor: pointer;
  font-weight: 800;
}
.page-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.page-info {
  font-size: 10px;
  font-weight: 700;
  color: var(--text-secondary);
}

.custom-scroll::-webkit-scrollbar { width: 6px; }
.custom-scroll::-webkit-scrollbar-track { background: var(--bg-deep); }
.custom-scroll::-webkit-scrollbar-thumb { background: rgba(139, 148, 158, 0.3); border-radius: 3px; }
.custom-scroll::-webkit-scrollbar-thumb:hover { background: rgba(139, 148, 158, 0.5); }
</style>
