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
          :class="{ active: filterMode === 'all' }" 
          @click="filterMode = 'all'"
        >
          Overview
        </button>
        <button 
          :class="{ active: filterMode === 'active' }" 
          @click="filterMode = 'active'"
        >
          Active
          <span v-if="activeVisibleCount > 0" class="tab-count">
            {{ activeVisibleCount }}
          </span>
        </button>
        <button 
          :class="{ active: filterMode === 'history' }" 
          @click="filterMode = 'history'"
        >
          History
          <span v-if="store.stats.history_count > 0" class="tab-count history">{{ store.stats.history_count }}</span>
        </button>
      </div>
    </nav>

    <!-- Contextual Panel (Positions / History) -->
    <div class="context-panel" :class="{ 'collapsed': filterMode === 'all' && store.positions.length === 0 }">
      <!-- Active Positions Mode -->
      <div class="positions-section" v-if="filterMode === 'active' || (filterMode === 'all' && store.positions.length > 0)">
        <div class="section-header">
          <div class="col-pair">PAIR</div>
          <div class="col-size">SIZE</div>
          <div class="col-price">ENTRY/MARK</div>
          <div class="col-target">TARGETS</div>
          <div class="col-pnl">PnL</div>
          <div class="col-exit">ACTION</div>
        </div>
        <div class="positions-list">
          <div
            v-for="pos in visiblePositions"
            :key="pos.pair"
            class="position-item"
            :class="[pos.pnl_net_idr >= 0 ? 'green' : 'red', { 'exit-pending': isExitRequested(pos.pair) }]"
          >
            <div class="col-pair">
              <div class="pair-name">{{ formatPair(pos.pair) }}</div>
              <div style="display: flex; gap: 6px; margin-top: 4px;">
                <div class="status-pill" :class="pos.status.toLowerCase()">{{ displayStatus(pos) }}</div>
                <div v-if="pos.is_fast_opportunity" class="status-pill fast-opportunity" title="High-opportunity fast trade using reserved capacity">⚡ FAST</div>
              </div>
            </div>
            
            <div class="col-size">
              <div class="val-idr">{{ formatIdr(pos.amount_idr) }}</div>
              <div class="val-coin">{{ (pos.amount_coin || 0).toFixed(2) }} units</div>
            </div>

            <div class="col-price-progress">
              <div class="price-labels">
                <span class="entry-label">ENT: {{ formatNumber(pos.entry_price) }}</span>
                <span class="current-label">MARK: {{ formatNumber(pos.exit_price) }}</span>
              </div>
              <div class="progress-container">
                <div class="progress-bg"></div>
                <div 
                  class="progress-fill" 
                  :style="{ 
                    left: calculatePosition(pos) + '%',
                    backgroundColor: pos.pnl_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'
                  }"
                ></div>
                <div class="target-marker sl" title="Stop Loss"></div>
                <div class="target-marker tp" title="Take Profit"></div>
              </div>
              <div class="target-labels">
                <span class="sl-val">SL -{{ Math.abs(pos.target_sl || 0).toFixed(1) }}%</span>
                <span class="tp-val">TP +{{ Math.abs(pos.target_tp || 0).toFixed(1) }}%</span>
              </div>
            </div>

            <div class="col-pnl">
              <div class="pnl-val">{{ formatIdr(pos.pnl_net_idr) }}</div>
              <div class="pnl-pct">{{ pos.pnl_pct > 0 ? '+' : '' }}{{ (pos.pnl_pct || 0).toFixed(2) }}%</div>
            </div>

            <div class="col-exit">
              <button
                v-if="(pos.status || '').toUpperCase() === 'OPEN' && (pos.pnl_net_idr / (pos.amount_idr || 1)) < manualExitMaxNetPct"
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
          <div v-if="filterMode === 'active' && visiblePositions.length === 0" class="empty-state">
            No active positions.
          </div>
        </div>
      </div>

      <!-- Real History Mode (Fetched from DB) -->
      <div class="history-section" v-if="filterMode === 'history'">
        <div class="section-header">
          <div class="col-pair">PAIR</div>
          <div class="col-size">SIZE</div>
          <div class="col-price">ENTRY/EXIT</div>
          <div class="col-pnl">RESULT</div>
        </div>
        <div class="history-list">
          <div
            v-for="trade in store.stats.recent_trades"
            :key="trade.created_at"
            class="history-item"
            :class="trade.pnl_idr >= 0 ? 'green' : 'red'"
          >
            <div class="history-row">
              <div class="col-pair">
                <div class="pair-name">{{ formatPair(trade.pair) }}</div>
                <div class="time">{{ formatTimeShort(trade.created_at) }}</div>
                <div class="exit-reason" v-if="trade.reason">{{ trade.reason }}</div>
              </div>
              <div class="col-size">
                <div class="val-idr">{{ formatIdr(trade.pnl_idr + (trade.amount_idr || 0)) }}</div>
              </div>
              <div class="col-price">
                <div class="entry">{{ formatNumber(trade.entry_price) }}</div>
                <div class="exit">{{ formatNumber(trade.exit_price) }}</div>
              </div>
              <div class="col-pnl">
                <div class="pnl-val">{{ formatIdr(trade.pnl_idr) }}</div>
                <div class="pnl-pct">{{ trade.pnl_pct > 0 ? '+' : '' }}{{ (trade.pnl_pct || 0).toFixed(2) }}%</div>
              </div>
            </div>
          </div>
          <div v-if="store.stats.recent_trades.length === 0" class="empty-state">
            No historical trades found.
          </div>
        </div>
      </div>
    </div>

    <!-- Live Intelligence Stream (Market / Analisa / LLM Exit) -->
    <div class="stream-container">
      <div class="stream-tabs">
        <button 
          :class="{ active: activeStreamTab === 'market' }" 
          @click="activeStreamTab = 'market'"
        >
          <span class="tab-icon">📊</span> Market Data
        </button>
        <button 
          :class="{ active: activeStreamTab === 'analisa' }" 
          @click="activeStreamTab = 'analisa'"
        >
          <span class="tab-icon">🧠</span> Analisa AI
        </button>
        <button
          :class="{ active: activeStreamTab === 'llm' }"
          @click="activeStreamTab = 'llm'"
        >
          <span class="tab-icon">🤖</span> LLM Exit
          <span v-if="llmExitCount > 0" class="tab-count llm">{{ llmExitCount }}</span>
        </button>
        <div class="tab-indicator" :class="activeStreamTab"></div>
      </div>

      <div class="events-list" ref="eventsList">
        <transition-group name="feed-slide" tag="div">
          <div
            v-for="item in filteredStream"
            :key="item.id"
            class="feed-item"
            :class="[itemClass(item), activeStreamTab === 'market' ? 'dense-item' : '']"
          >
            <div class="feed-top">
              <span class="feed-time">{{ formatTime(item.created_at) }}</span>
              <span class="feed-tag" :class="tagClass(item)">{{ item.side }}</span>
              <span class="feed-pair">{{ formatPair(item.pair) }}</span>
            </div>
            <div class="feed-msg" :class="{ 'mono-text': activeStreamTab === 'market' }">{{ item.message }}</div>
          </div>
        </transition-group>
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
const filterMode = ref('all')
const activeStreamTab = ref('analisa') 

// Threshold from server config — EXIT allowed only when Net PnL < this value
const manualExitMaxNetPct = computed(() => store.stats.manual_exit_max_net_pct ?? 0.001)

// Track pairs currently being exited to disable the button during processing
const exitingPairs = ref(new Set())
const exitRequestedPairs = ref(new Set())
const temporarilyHiddenExitRows = ref(new Set())

const visiblePositions = computed(() =>
  store.positions.filter(p => !temporarilyHiddenExitRows.value.has(String(p.pair || '').toLowerCase()))
)

const activeVisibleCount = computed(() =>
  visiblePositions.value.filter(p => ['OPEN', 'EXITING'].includes(String(p.status || '').toUpperCase())).length
)

function normalizePair(pair) {
  return String(pair || '').toLowerCase()
}

function isExitRequested(pair) {
  return exitRequestedPairs.value.has(normalizePair(pair))
}

function displayStatus(pos) {
  if (isExitRequested(pos.pair) && String(pos.status || '').toUpperCase() === 'EXITING') {
    return 'EXIT REQUESTED'
  }
  return pos.status
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
        alert('Sesi login diperlukan untuk manual EXIT. Silakan login terlebih dahulu.')
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
  const marketItems = items.filter((item) => {
    const src = String(item?.source || '').toUpperCase()
    const side = String(item?.side || '').toUpperCase()
    return src === 'MARKET_WS' && (side === 'BUY' || side === 'SELL' || side === 'MARKET')
  })
  const llmItems = items.filter((item) => {
    const side = String(item?.side || '').toUpperCase()
    return side.startsWith('LLM_')
  })

  if (activeStreamTab.value === 'market') return marketItems
  if (activeStreamTab.value === 'llm') return store.llmDecisions
  
  // Overview: show everything except dense market data
  return items.filter((item) => {
    const src = String(item?.source || '').toUpperCase()
    return src !== 'MARKET_WS'
  })
})

const llmExitCount = computed(() => store.llmDecisions.length)

function itemClass(item) {
  const side = (item.side || '').toUpperCase()
  if (side === 'LLM_EXIT_SIGNAL') return 'type-llm'
  if (side === 'BUY' || side === 'LONG') return 'type-buy'
  if (side === 'SELL' || side === 'SHORT') return 'type-sell'
  if (side.includes('AI') || side.includes('ORACLE')) return 'type-ai'
  if (side === 'ERROR' || side === 'WARN') return 'type-warn'
  return 'type-info'
}

function tagClass(item) {
  const side = (item.side || '').toUpperCase()
  if (side === 'LLM_EXIT_SIGNAL') return 'tag-llm'
  if (side === 'BUY' || side === 'LONG') return 'tag-buy'
  if (side === 'SELL' || side === 'SHORT') return 'tag-sell'
  if (side.includes('AI') || side.includes('ORACLE')) return 'tag-ai'
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
  return pair.replace(/idr$/i, '').toUpperCase()
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
  return Number(val || 0).toLocaleString('id-ID')
}

function formatIdr(val) {
  const n = Math.round(Number(val) || 0)
  const abs = Math.abs(n)
  const prefix = n >= 0 ? '+' : '-'
  if (abs >= 1_000_000) return `${prefix}Rp ${(abs / 1_000_000).toFixed(2)}jt`
  return `${prefix}Rp ${abs.toLocaleString('id-ID')}`
}
</script>

<style scoped>
.activity-feed {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #161b22;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid #30363d;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}

.feed-main-header {
  padding: 16px 20px;
  background: #0d1117;
  border-bottom: 1px solid #30363d;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.title { font-weight: 900; font-size: 14px; color: #e6edf3; text-transform: uppercase; letter-spacing: 1px; }

.feed-nav { padding: 12px 20px; background: #161b22; border-bottom: 1px solid rgba(48, 54, 61, 0.5); }

.segmented-control {
  display: flex;
  background: #0d1117;
  padding: 4px;
  border-radius: 8px;
  border: 1px solid #30363d;
}

.segmented-control button {
  flex: 1; background: transparent; border: none; color: #8b949e; font-size: 11px;
  font-weight: 700; padding: 8px 0; border-radius: 6px; cursor: pointer; transition: all 0.2s;
}

.segmented-control button.active { background: #21262d; color: #58a6ff; }

.tab-count { background: #f85149; color: white; font-size: 9px; padding: 1px 5px; border-radius: 10px; margin-left: 5px; }
.tab-count.history { background: #30363d; color: #8b949e; border: 1px solid rgba(139, 148, 158, 0.2); }
.tab-count.llm { background: rgba(255, 51, 102, 0.15); color: #ff3366; border: 1px solid rgba(255, 51, 102, 0.25); }

.context-panel { background: #0d1117; max-height: 60%; overflow-y: auto; border-bottom: 1px solid #30363d; transition: all 0.3s; }
.context-panel.collapsed { max-height: 0; border-bottom: none; }

.section-header {
  display: flex;
  padding: 10px 20px;
  background: rgba(13, 17, 23, 0.8);
  border-bottom: 1px solid #30363d;
  font-size: 10px;
  font-weight: 800;
  color: #484f58;
  letter-spacing: 0.5px;
}

.col-pair { flex: 1.2; }
.col-size { flex: 1; text-align: right; padding-right: 15px; }
.col-price-progress { flex: 3; padding: 0 10px; }
.col-pnl { flex: 1.2; text-align: right; }
.col-exit { flex: 0 0 56px; text-align: center; }

.position-item, .history-item {
  display: flex;
  align-items: center;
  gap: 15px;
  padding: 12px 20px;
  border-bottom: 1px solid rgba(48, 54, 61, 0.4);
  transition: background 0.2s;
}

.position-item:hover, .history-item:hover {
  background: rgba(88, 166, 255, 0.03);
}

.position-item.exit-pending {
  border-left: 3px solid rgba(210, 153, 34, 0.5);
  background: rgba(210, 153, 34, 0.06);
}

.pair-name { font-weight: 900; color: #e6edf3; font-size: 13px; margin-bottom: 2px; }
.exit-reason {
  font-size: 8px; font-weight: 700; text-transform: uppercase;
  color: #8b949e; letter-spacing: 0.4px; margin-top: 2px; opacity: 0.75;
}
.status-pill {
  display: inline-block;
  font-size: 8px;
  font-weight: 900;
  padding: 1px 5px;
  border-radius: 4px;
  text-transform: uppercase;
  background: #21262d;
  color: #8b949e;
}
.status-pill.exiting { color: #f85149; border: 1px solid rgba(248, 81, 73, 0.2); }
.status-pill.pending_buy { color: #d29922; border: 1px solid rgba(210, 153, 34, 0.2); }
.status-pill.open { color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.2); }
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
.entry-label { color: #8b949e; }
.current-label { color: #e6edf3; font-weight: 900; }
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
  background: #21262d;
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

.green .pnl-val { color: #3fb950; }
.green .pnl-pct { background: rgba(63, 185, 80, 0.1); color: #3fb950; }
.red .pnl-val { color: #f85149; }
.red .pnl-pct { background: rgba(248, 81, 73, 0.1); color: #f85149; }

.empty-state { padding: 20px; text-align: center; color: #484f58; font-size: 12px; font-style: italic; }

.stream-container { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.stream-tabs { display: flex; position: relative; background: #161b22; border-bottom: 1px solid #30363d; }
.stream-tabs button { flex: 1; background: transparent; border: none; padding: 14px 0; color: #8b949e; font-size: 11px; font-weight: 800; cursor: pointer; }
.stream-tabs button.active { color: #e6edf3; }

.tab-indicator { position: absolute; bottom: 0; height: 2px; width: calc(100% / 3); transition: all 0.3s; }
.tab-indicator.market  { transform: translateX(0);   background: #58a6ff; }
.tab-indicator.analisa { transform: translateX(100%); background: #a371f7; }
.tab-indicator.llm     { transform: translateX(200%); background: #ff3366; }

.events-list { flex: 1; overflow-y: auto; background: #0d1117; }
.feed-item { padding: 10px 20px; border-bottom: 1px solid rgba(48, 54, 61, 0.1); border-left: 3px solid transparent; }
.feed-item.type-buy  { border-left-color: #3fb950; }
.feed-item.type-sell { border-left-color: #f85149; }
.feed-item.type-ai   { border-left-color: #a371f7; }
.feed-item.type-llm  { border-left-color: #ff3366; background: rgba(255, 51, 102, 0.03); }

.feed-top { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }
.feed-time { color: #8b949e; font-size: 10px; }
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
.badge.offline { color: #8b949e; background: rgba(139, 148, 158, 0.1); border: 1px solid rgba(139, 148, 158, 0.2); }

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
.audit-confidence { margin-left: auto; font-size: 9px; color: #8b949e; font-weight: 700; }

.audit-body { display: flex; align-items: flex-start; gap: 8px; }
.audit-verdict {
  font-size: 9px;
  font-weight: 900;
  padding: 1px 5px;
  border-radius: 4px;
  background: #21262d;
  color: #c9d1d9;
}
.audit-verdict.buy { color: #3fb950; }
.audit-verdict.sell { color: #f85149; }

.audit-text {
  margin: 0;
  font-size: 11px;
  line-height: 1.4;
  color: #8b949e;
  font-style: italic;
}

.btn-manual-exit {
  background: #da3633;
  color: #fff;
  border: none;
  border-radius: 5px;
  padding: 4px 8px;
  font-size: 10px;
  font-weight: 900;
  letter-spacing: 0.5px;
  cursor: pointer;
  transition: background 0.15s, opacity 0.15s;
  white-space: nowrap;
}
.btn-manual-exit:hover:not(:disabled) { background: #f85149; }
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
</style>
