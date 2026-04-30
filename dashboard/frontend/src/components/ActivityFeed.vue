<template>
  <div class="activity-feed">
    <div class="feed-header">
      <span class="title">Activity Feed</span>
      <span class="badge" :class="store.connected ? 'live' : 'offline'">
        {{ store.connected ? '● LIVE' : '○ OFFLINE' }}
      </span>
    </div>

    <div class="positions-section" v-if="store.positions.length > 0">
      <div class="section-label">Open Positions</div>
      <div
        v-for="pos in store.positions"
        :key="pos.id"
        class="position-item"
        :class="pos.pnl_idr >= 0 ? 'green' : 'red'"
      >
        <span class="pair">{{ pos.pair }}</span>
        <span class="pos-reason">{{ pos.reason }}</span>
        <span class="pnl">{{ formatIdr(pos.pnl_idr) }}</span>
        <span class="pct">{{ pos.pnl_pct > 0 ? '+' : '' }}{{ pos.pnl_pct }}%</span>
      </div>
    </div>

    <div class="section-label">Events</div>
    <div class="events-list">
      <transition-group name="feed-slide" tag="div">
        <div
          v-for="item in store.activityFeed"
          :key="item.id"
          class="feed-item"
          :class="itemClass(item)"
        >
          <span class="feed-time">{{ formatTime(item.created_at) }}</span>
          <span class="feed-tag" :class="tagClass(item)">{{ item.side }}</span>
          <span class="feed-pair">{{ item.pair }}</span>
          <span class="feed-msg">{{ item.message }}</span>
        </div>
      </transition-group>
    </div>
  </div>
</template>

<script setup>
import { useTradeStore } from '../stores/trade'

const store = useTradeStore()

function itemClass(item) {
  const side = (item.side || '').toUpperCase()
  if (side === 'BUY') return 'type-buy'
  if (side === 'SELL') return 'type-sell'
  if (side === 'AI' || side === 'ORACLE') return 'type-ai'
  if (side === 'ERROR' || side === 'WARN') return 'type-warn'
  return 'type-info'
}

function tagClass(item) {
  const side = (item.side || '').toUpperCase()
  if (side === 'BUY') return 'tag-buy'
  if (side === 'SELL') return 'tag-sell'
  if (side === 'AI' || side === 'ORACLE') return 'tag-ai'
  if (side === 'ERROR' || side === 'WARN') return 'tag-warn'
  return 'tag-info'
}

function formatTime(ts) {
  if (!ts) return '--:--'
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ts.slice(-8)
  }
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
  border-radius: 8px;
  overflow: hidden;
}

.feed-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #21262d;
  background: #0d1117;
}

.title {
  font-weight: 600;
  font-size: 14px;
  color: #58a6ff;
  letter-spacing: 0.5px;
}

.badge {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.5px;
  padding: 2px 8px;
  border-radius: 10px;
}

.badge.live {
  color: #3fb950;
  background: rgba(63, 185, 80, 0.1);
}

.badge.offline {
  color: #8b949e;
  background: rgba(139, 148, 158, 0.1);
}

.section-label {
  padding: 6px 16px 4px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: #8b949e;
  border-bottom: 1px solid #21262d;
}

.positions-section {
  flex-shrink: 0;
}

.position-item {
  display: grid;
  grid-template-columns: 80px 1fr 90px 60px;
  gap: 8px;
  padding: 6px 16px;
  border-bottom: 1px solid #21262d;
  font-size: 12px;
  align-items: center;
}

.position-item.green { border-left: 3px solid #3fb950; }
.position-item.red   { border-left: 3px solid #f85149; }

.pair { font-weight: 600; color: #e6edf3; }
.pos-reason { color: #8b949e; font-size: 11px; }
.pnl { text-align: right; font-weight: 600; }
.pct { text-align: right; font-size: 11px; }
.position-item.green .pnl,
.position-item.green .pct { color: #3fb950; }
.position-item.red .pnl,
.position-item.red .pct   { color: #f85149; }

.events-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}

.feed-item {
  display: grid;
  grid-template-columns: 62px 60px 80px 1fr;
  gap: 6px;
  padding: 5px 12px;
  border-bottom: 1px solid rgba(33, 38, 45, 0.5);
  font-size: 11.5px;
  align-items: start;
  transition: background 0.15s;
}

.feed-item:hover { background: rgba(88, 166, 255, 0.04); }

.feed-item.type-buy   { border-left: 2px solid #3fb950; }
.feed-item.type-sell  { border-left: 2px solid #f85149; }
.feed-item.type-ai    { border-left: 2px solid #a371f7; }
.feed-item.type-warn  { border-left: 2px solid #d29922; }
.feed-item.type-info  { border-left: 2px solid #30363d; }

.feed-time {
  color: #8b949e;
  font-size: 10.5px;
  line-height: 1.6;
  font-variant-numeric: tabular-nums;
}

.feed-tag {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 3px;
  text-align: center;
  line-height: 1.8;
  align-self: start;
}

.tag-buy  { background: rgba(63, 185, 80, 0.15); color: #3fb950; }
.tag-sell { background: rgba(248, 81, 73, 0.15); color: #f85149; }
.tag-ai   { background: rgba(163, 113, 247, 0.15); color: #a371f7; }
.tag-warn { background: rgba(210, 153, 34, 0.15); color: #d29922; }
.tag-info { background: rgba(139, 148, 158, 0.1); color: #8b949e; }

.feed-pair {
  color: #58a6ff;
  font-weight: 600;
  font-size: 11px;
  line-height: 1.6;
}

.feed-msg {
  color: #c9d1d9;
  line-height: 1.5;
  word-break: break-word;
}

/* Transition */
.feed-slide-enter-active { transition: all 0.3s ease; }
.feed-slide-enter-from { opacity: 0; transform: translateY(-8px); }
</style>
