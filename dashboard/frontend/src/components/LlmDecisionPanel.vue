<template>
  <div class="llm-panel">
    <div class="panel-header">
      <span class="panel-title">🤖 LLM EXIT DECISIONS</span>
      <span class="service-badge" :class="serviceStatusClass">{{ serviceStatusLabel }}</span>
    </div>

    <div class="decisions-list">
      <div v-if="decisions.length === 0" class="empty-state">
        No LLM decisions yet. The service is actively monitoring open positions.
      </div>
      <div
        v-for="d in decisions"
        :key="d.id || d.created_at"
        class="decision-item"
      >
        <div class="decision-top">
          <span class="d-pair">{{ formatPair(d.pair) }}</span>
          <span class="d-confidence" :class="confidenceClass(d.confidence)">
            {{ d.confidence }}%
          </span>
          <span class="d-action" :class="d.side.toLowerCase().includes('exit') ? 'sell' : 'buy'">
            {{ formatSide(d.side) }}
          </span>
          <span class="d-time">{{ formatTime(d.created_at) }}</span>
        </div>
        <div class="d-reason">{{ d.reason }}</div>
        <div class="d-support" v-if="d.support.length">
          <span v-for="s in d.support" :key="s" class="support-tag">{{ s }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useTradeStore } from '../stores/trade'

const store = useTradeStore()

/** Parse raw message `"[85%] reason | support=A,B"` into structured fields. */
const decisions = computed(() =>
  store.llmDecisions.map((d) => {
    const msg = d.message || ''
    const confMatch = msg.match(/^\[(\d+)%\]\s*/)
    const confidence = confMatch ? parseInt(confMatch[1], 10) : 0
    const rest = confMatch ? msg.slice(confMatch[0].length) : msg
    const parts = rest.split(' | support=')
    const reason = parts[0].trim()
    const support = parts[1]
      ? parts[1].split(',').map((s) => s.trim()).filter(Boolean)
      : []
    return { ...d, confidence, reason, support }
  })
)

/** ACTIVE: last event < 60s; IDLE: last > 60s; INACTIVE: no events at all. */
const serviceStatusClass = computed(() => {
  if (!store.llmDecisions.length) return 'status-inactive'
  const age = Date.now() - new Date(store.llmDecisions[0]?.created_at || 0).getTime()
  return age < 60_000 ? 'status-active' : 'status-idle'
})

const serviceStatusLabel = computed(() => {
  if (!store.llmDecisions.length) return 'INACTIVE'
  const age = Date.now() - new Date(store.llmDecisions[0]?.created_at || 0).getTime()
  return age < 60_000 ? '● ACTIVE' : '○ IDLE'
})

function confidenceClass(c) {
  if (c >= 90) return 'conf-high'
  if (c >= 80) return 'conf-med'
  return 'conf-low'
}

function formatSide(side) {
  return (side || '').replace(/^LLM_/, '').replace(/_/g, ' ').toUpperCase()
}

function formatPair(pair) {
  return (pair || '').replace(/idr$/i, '').toUpperCase()
}

function formatTime(ts) {
  if (!ts) return '--:--'
  try {
    return new Date(ts).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return ''
  }
}
</script>

<style scoped>
.llm-panel {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  height: 100%;
  min-height: 0;
}

.panel-header {
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.02);
  border-bottom: 1px solid #1a1c24;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
}

.panel-title {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1.5px;
  color: #f5a623;
}

.service-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 10px;
}

.status-active  { color: #00ff88; background: rgba(0, 255, 136, 0.1); }
.status-idle    { color: #f5a623; background: rgba(245, 166, 35, 0.1); }
.status-inactive{ color: #555;    background: rgba(85, 85, 85, 0.1);  }

.decisions-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.empty-state {
  padding: 20px;
  text-align: center;
  color: #444;
  font-size: 12px;
}

.decision-item {
  border: 1px solid #1a2030;
  border-left: 3px solid #ff3366;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
  background: rgba(255, 51, 102, 0.04);
  transition: background 0.15s;
}

.decision-item:hover {
  background: rgba(255, 51, 102, 0.08);
}

.decision-top {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.d-pair {
  font-size: 13px;
  font-weight: 800;
  color: #fff;
}

.d-confidence {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 6px;
}

.conf-high { color: #ff3366; background: rgba(255, 51, 102, 0.15); }
.conf-med  { color: #f5a623; background: rgba(245, 166, 35, 0.15); }
.conf-low  { color: #888;    background: rgba(136, 136, 136, 0.15); }

.d-action {
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.5px;
  padding: 2px 6px;
  border-radius: 4px;
}

.d-action.sell {
  background: rgba(255, 51, 102, 0.12);
  color: #ff3366;
  border: 1px solid rgba(255, 51, 102, 0.25);
}

.d-time {
  font-size: 10px;
  color: #555;
  margin-left: auto;
}

.d-reason {
  font-size: 11px;
  color: #aaa;
  margin-bottom: 6px;
  line-height: 1.4;
}

.d-support {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.support-tag {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.5px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(100, 150, 255, 0.08);
  color: #6496ff;
  border: 1px solid rgba(100, 150, 255, 0.18);
}
</style>
