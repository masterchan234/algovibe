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
          <span v-if="store.positions.length > 0" class="tab-count">{{ store.positions.length }}</span>
        </button>
        <button 
          :class="{ active: filterMode === 'history' }" 
          @click="filterMode = 'history'"
        >
          History
        </button>
      </div>
    </nav>

    <!-- Contextual Panel (Positions / History) -->
    <div class="context-panel" :class="{ 'collapsed': filterMode === 'all' && store.positions.length === 0 }">
      <!-- Active Positions Mode -->
      <div class="positions-section" v-if="filterMode === 'active' || (filterMode === 'all' && store.positions.length > 0)">
        <div class="section-label">Active Positions ({{ store.positions.length }})</div>
        <div class="positions-list">
          <div
            v-for="pos in store.positions"
            :key="pos.pair"
            class="position-item"
            :class="pos.pnl_idr >= 0 ? 'green' : 'red'"
          >
            <div class="pos-main">
              <span class="pair">{{ formatPair(pos.pair) }}</span>
              <span class="pos-info">Entry: {{ formatNumber(pos.entry_price) }}</span>
            </div>
            <div class="pos-pnl">
              <span class="pnl">{{ formatIdr(pos.pnl_idr) }}</span>
              <span class="pct">{{ pos.pnl_pct > 0 ? '+' : '' }}{{ pos.pnl_pct.toFixed(2) }}%</span>
            </div>
          </div>
          <div v-if="filterMode === 'active' && store.positions.length === 0" class="empty-state">
            No active positions.
          </div>
        </div>
      </div>

      <!-- Real History Mode (Fetched from DB) -->
      <div class="history-section" v-if="filterMode === 'history'">
        <div class="section-label">Recent Trades (Database)</div>
        <div class="history-list">
          <div
            v-for="trade in store.stats.recent_trades"
            :key="trade.created_at"
            class="history-item"
            :class="trade.pnl_idr >= 0 ? 'green' : 'red'"
          >
            <div class="hist-main">
              <span class="pair">{{ formatPair(trade.pair) }}</span>
              <span class="hist-info">Exit: {{ formatNumber(trade.exit_price) }}</span>
            </div>
            <div class="hist-pnl">
              <span class="pnl">{{ formatIdr(trade.pnl_idr) }}</span>
              <span class="pct">{{ trade.pnl_pct > 0 ? '+' : '' }}{{ trade.pnl_pct.toFixed(2) }}%</span>
            </div>
          </div>
          <div v-if="store.stats.recent_trades.length === 0" class="empty-state">
            No historical trades found.
          </div>
        </div>
      </div>
    </div>

    <!-- Live Intelligence Stream (Market / Analisa) -->
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
import { ref, computed } from 'vue'
import { useTradeStore } from '../stores/trade'

const store = useTradeStore()
const filterMode = ref('all')
const activeStreamTab = ref('analisa') 

const openPairSet = computed(() => new Set(store.positions.map(p => (p.pair || '').toLowerCase())))

const visibleFeed = computed(() => {
  const items = store.activityFeed
  if (filterMode.value === 'active') {
    return items.filter(item => {
      const p = (item.pair || '').toLowerCase()
      return p === 'system' || p === 'global' || openPairSet.value.has(p)
    })
  }
  if (filterMode.value === 'history') {
    return items.filter(item => {
      const msg = (item.message || '').toUpperCase()
      const side = (item.side || '').toUpperCase()
      return side === 'SELL' || msg.includes('EXIT') || msg.includes('TP') || msg.includes('SL') || msg.includes('CLOSED')
    })
  }
  return items
})

const filteredStream = computed(() => {
  const items = visibleFeed.value
  if (activeStreamTab.value === 'market') {
    return items.filter(item => item.status === 'LIVE')
  } else {
    return items.filter(item => item.status !== 'LIVE')
  }
})

function itemClass(item) {
  const side = (item.side || '').toUpperCase()
  if (side === 'BUY' || side === 'LONG') return 'type-buy'
  if (side === 'SELL' || side === 'SHORT') return 'type-sell'
  if (side.includes('AI') || side.includes('ORACLE')) return 'type-ai'
  if (side === 'ERROR' || side === 'WARN') return 'type-warn'
  return 'type-info'
}

function tagClass(item) {
  const side = (item.side || '').toUpperCase()
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

function formatNumber(val) {
  return Number(val || 0).toLocaleString('id-ID')
}

function formatIdr(val) {
  const n = Number(val) || 0
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

.context-panel { background: #0d1117; max-height: 40%; overflow-y: auto; border-bottom: 1px solid #30363d; transition: all 0.3s; }
.context-panel.collapsed { max-height: 0; border-bottom: none; }

.section-label { padding: 12px 20px 6px; font-size: 10px; font-weight: 800; text-transform: uppercase; color: #8b949e; }

.position-item, .history-item { display: flex; justify-content: space-between; padding: 12px 20px; border-bottom: 1px solid rgba(48, 54, 61, 0.2); }
.pair { font-weight: 800; color: #e6edf3; font-size: 14px; }
.pos-info, .hist-info { color: #8b949e; font-size: 10px; }
.pnl { font-weight: 900; font-size: 13px; }
.pct { font-size: 11px; font-weight: 800; }

.green .pnl, .green .pct { color: #3fb950; }
.red .pnl, .red .pct { color: #f85149; }

.empty-state { padding: 20px; text-align: center; color: #484f58; font-size: 12px; font-style: italic; }

.stream-container { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.stream-tabs { display: flex; position: relative; background: #161b22; border-bottom: 1px solid #30363d; }
.stream-tabs button { flex: 1; background: transparent; border: none; padding: 14px 0; color: #8b949e; font-size: 11px; font-weight: 800; cursor: pointer; }
.stream-tabs button.active { color: #e6edf3; }

.tab-indicator { position: absolute; bottom: 0; height: 2px; width: 50%; transition: all 0.3s; }
.tab-indicator.market { transform: translateX(0); background: #58a6ff; }
.tab-indicator.analisa { transform: translateX(100%); background: #a371f7; }

.events-list { flex: 1; overflow-y: auto; background: #0d1117; }
.feed-item { padding: 10px 20px; border-bottom: 1px solid rgba(48, 54, 61, 0.1); border-left: 3px solid transparent; }
.feed-item.type-buy { border-left-color: #3fb950; }
.feed-item.type-sell { border-left-color: #f85149; }
.feed-item.type-ai { border-left-color: #a371f7; }

.feed-top { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }
.feed-time { color: #8b949e; font-size: 10px; }
.feed-tag { font-size: 9px; font-weight: 900; padding: 1px 4px; border-radius: 3px; }
.tag-buy { background: rgba(63, 185, 80, 0.1); color: #3fb950; }
.tag-sell { background: rgba(248, 81, 73, 0.1); color: #f85149; }
.tag-ai { background: rgba(163, 113, 247, 0.1); color: #a371f7; }
.feed-pair { color: #58a6ff; font-weight: 800; font-size: 11px; }
.feed-msg { color: #c9d1d9; font-size: 12px; }
.mono-text { font-family: monospace; font-size: 11px; }

.badge { font-size: 9px; font-weight: 900; padding: 2px 8px; border-radius: 12px; }
.badge.live { color: #3fb950; background: rgba(63, 185, 80, 0.1); border: 1px solid rgba(63, 185, 80, 0.2); }
.badge.offline { color: #8b949e; background: rgba(139, 148, 158, 0.1); border: 1px solid rgba(139, 148, 158, 0.2); }

.feed-slide-enter-active { transition: all 0.3s; }
.feed-slide-enter-from { opacity: 0; transform: translateX(-10px); }
</style>
