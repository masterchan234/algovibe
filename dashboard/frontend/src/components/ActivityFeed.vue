<template>
  <div class="activity-feed">
    <div class="feed-header">
      <div class="header-left">
        <span class="title">Execution Engine</span>
        <div class="filter-tabs">
          <button 
            :class="{ active: filterMode === 'all' }" 
            @click="filterMode = 'all'"
            title="Show all system and market events"
          >
            All
          </button>
          <button 
            :class="{ active: filterMode === 'positions' }" 
            @click="filterMode = 'positions'"
            title="Filter by open positions only"
          >
            Active
          </button>
        </div>
      </div>
      <span class="badge" :class="store.connected ? 'live' : 'offline'">
        {{ store.connected ? '● LIVE' : '○ OFFLINE' }}
      </span>
    </div>

    <!-- Active Positions Widget -->
    <div class="positions-section" v-if="store.positions.length > 0">
      <div class="ai-vision-notice">
        <span class="pulse-icon">🔮</span>
        melihat bagaimana AI Anda memilih koin-koin potensial secara lebih bijak
      </div>
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
            <span class="pos-info">Rp {{ formatNumber(pos.entry_price) }}</span>
          </div>
          <div class="pos-pnl">
            <span class="pnl">{{ formatIdr(pos.pnl_idr) }}</span>
            <span class="pct">{{ pos.pnl_pct > 0 ? '+' : '' }}{{ pos.pnl_pct.toFixed(2) }}%</span>
          </div>
          <div class="pos-reason" :title="pos.reason">{{ pos.reason }}</div>
        </div>
      </div>
    </div>

    <div class="section-label">Stream Data</div>
    <div class="events-list" ref="eventsList">
      <transition-group name="feed-slide" tag="div">
        <div
          v-for="item in visibleFeed"
          :key="item.id"
          class="feed-item"
          :class="itemClass(item)"
        >
          <div class="feed-top">
            <span class="feed-time">{{ formatTime(item.created_at) }}</span>
            <span class="feed-tag" :class="tagClass(item)">{{ item.side }}</span>
            <span class="feed-pair">{{ formatPair(item.pair) }}</span>
          </div>
          <div class="feed-msg">{{ item.message }}</div>
        </div>
      </transition-group>
      <div v-if="visibleFeed.length === 0" class="empty-feed">
        No events matching filter
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useTradeStore } from '../stores/trade'

const store = useTradeStore()
const filterMode = ref('all')

const openPairSet = computed(() => new Set(store.positions.map(p => (p.pair || '').toLowerCase())))

const visibleFeed = computed(() => {
  const items = store.activityFeed
  if (filterMode.value === 'positions') {
    return items.filter(item => {
      const p = (item.pair || '').toLowerCase()
      return p === 'system' || p === 'global' || openPairSet.value.has(p)
    })
  }
  return items
})

function itemClass(item) {
  const side = (item.side || '').toUpperCase()
  if (side === 'BUY' || side === 'LONG') return 'type-buy'
  if (side === 'SELL' || side === 'SHORT') return 'type-sell'
  if (side.includes('AI') || side.includes('ORACLE') || side.includes('PREDICTION')) return 'type-ai'
  if (side === 'ERROR' || side === 'WARN' || side.includes('FAKE') || side.includes('WHALE')) return 'type-warn'
  return 'type-info'
}

function tagClass(item) {
  const side = (item.side || '').toUpperCase()
  if (side === 'BUY' || side === 'LONG') return 'tag-buy'
  if (side === 'SELL' || side === 'SHORT') return 'tag-sell'
  if (side.includes('AI') || side.includes('ORACLE') || side.includes('PREDICTION')) return 'tag-ai'
  if (side === 'ERROR' || side === 'WARN' || side.includes('FAKE') || side.includes('WHALE')) return 'tag-warn'
  return 'tag-info'
}

function formatTime(ts) {
  if (!ts) return '--:--'
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ts.length > 8 ? ts.slice(-8) : ts
  }
}

function formatPair(pair) {
  if (!pair) return ''
  if (pair.toLowerCase() === 'system' || pair.toLowerCase() === 'global') return pair.toUpperCase()
  return pair.replace(/idr$/i, '').replace(/usdt$/i, '').toUpperCase()
}

function formatNumber(val) {
  return Number(val || 0).toLocaleString('id-ID')
}

function formatIdr(val) {
  const n = Number(val) || 0
  const abs = Math.abs(n)
  const prefix = n >= 0 ? '+' : '-'
  if (abs >= 1_000_000) return `${prefix}Rp ${(abs / 1_000_000).toFixed(2)}jt`
  if (abs >= 1_000) return `${prefix}Rp ${(abs / 1_000).toFixed(1)}k`
  return `${prefix}Rp ${abs.toFixed(0)}`
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
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
}
.ai-vision-notice {
  background: linear-gradient(135deg, rgba(163, 113, 247, 0.1) 0%, rgba(88, 166, 255, 0.05) 100%);
  border-bottom: 1px solid rgba(163, 113, 247, 0.2);
  padding: 10px 16px;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-purple);
  display: flex;
  align-items: center;
  gap: 10px;
  line-height: 1.4;
}
.pulse-icon {
  font-size: 16px;
  animation: ptr-rotate 4s linear infinite;
}

.feed-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #30363d;
  background: #0d1117;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.title {
  font-weight: 800;
  font-size: 14px;
  color: #e6edf3;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

.filter-tabs {
  display: flex;
  background: #21262d;
  padding: 2px;
  border-radius: 6px;
  gap: 2px;
}

.filter-tabs button {
  background: transparent;
  border: none;
  color: #8b949e;
  font-size: 11px;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.filter-tabs button:hover {
  color: #e6edf3;
}

.filter-tabs button.active {
  background: #30363d;
  color: #58a6ff;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}

.badge {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.5px;
  padding: 3px 10px;
  border-radius: 20px;
}

.badge.live {
  color: #3fb950;
  background: rgba(63, 185, 80, 0.1);
  border: 1px solid rgba(63, 185, 80, 0.2);
}

.badge.offline {
  color: #8b949e;
  background: rgba(139, 148, 158, 0.1);
  border: 1px solid rgba(139, 148, 158, 0.2);
}

.section-label {
  padding: 10px 16px 6px;
  font-size: 10px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: #8b949e;
  background: rgba(13, 17, 23, 0.5);
  border-bottom: 1px solid #21262d;
}

.positions-section {
  flex-shrink: 0;
  max-height: 40%;
  display: flex;
  flex-direction: column;
}

.positions-list {
  overflow-y: auto;
  background: #0d1117;
}

.position-item {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: auto auto;
  gap: 4px;
  padding: 10px 16px;
  border-bottom: 1px solid #21262d;
  transition: background 0.2s;
}

.position-item:hover {
  background: rgba(88, 166, 255, 0.05);
}

.position-item.green { border-left: 4px solid #3fb950; }
.position-item.red   { border-left: 4px solid #f85149; }

.pos-main {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pair { font-weight: 800; color: #e6edf3; font-size: 14px; }
.pos-info { color: #8b949e; font-size: 11px; font-variant-numeric: tabular-nums; }

.pos-pnl {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.pnl { font-weight: 800; font-size: 13px; font-variant-numeric: tabular-nums; }
.pct { font-size: 11px; font-weight: 700; }

.position-item.green .pnl,
.position-item.green .pct { color: #3fb950; }
.position-item.red .pnl,
.position-item.red .pct   { color: #f85149; }

.pos-reason {
  grid-column: span 2;
  font-size: 10px;
  color: #8b949e;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-style: italic;
}

.events-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
  background: #0d1117;
}

.feed-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(48, 54, 61, 0.3);
  transition: background 0.15s;
}

.feed-item:hover { background: rgba(88, 166, 255, 0.04); }

.feed-item.type-buy   { border-left: 3px solid #3fb950; }
.feed-item.type-sell  { border-left: 3px solid #f85149; }
.feed-item.type-ai    { border-left: 3px solid #a371f7; }
.feed-item.type-warn  { border-left: 3px solid #d29922; }
.feed-item.type-info  { border-left: 3px solid #30363d; }

.feed-top {
  display: flex;
  align-items: center;
  gap: 10px;
}

.feed-time {
  color: #8b949e;
  font-size: 10px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  min-width: 55px;
}

.feed-tag {
  font-size: 9px;
  font-weight: 900;
  padding: 1px 6px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.tag-buy  { background: rgba(63, 185, 80, 0.15); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.3); }
.tag-sell { background: rgba(248, 81, 73, 0.15); color: #f85149; border: 1px solid rgba(248, 81, 73, 0.3); }
.tag-ai   { background: rgba(163, 113, 247, 0.15); color: #a371f7; border: 1px solid rgba(163, 113, 247, 0.3); }
.tag-warn { background: rgba(210, 153, 34, 0.15); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.3); }
.tag-info { background: rgba(139, 148, 158, 0.1); color: #8b949e; border: 1px solid rgba(139, 148, 158, 0.2); }

.feed-pair {
  color: #58a6ff;
  font-weight: 800;
  font-size: 11px;
}

.feed-msg {
  color: #c9d1d9;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}

.empty-feed {
  padding: 40px;
  text-align: center;
  color: #484f58;
  font-size: 13px;
  font-style: italic;
}

/* Transition */
.feed-slide-enter-active { transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275); }
.feed-slide-enter-from { opacity: 0; transform: translateX(-20px); }
</style>
