<template>
  <div class="latency-strip" v-if="pairs.length > 0">
    <div class="strip-label">
      <span class="label-dot" :class="anyActive ? 'dot-active' : 'dot-idle'"></span>
      BINANCE LEAD-LAG
    </div>

    <div class="pairs-row">
      <div
        v-for="p in pairs"
        :key="p.pair"
        class="pair-card"
        :class="`status-${p.status.toLowerCase()}`"
        :title="`${p.base}: ${p.b_return >= 0 ? '+' : ''}${p.b_return.toFixed(3)}% dalam 5s | window=${p.actual_ms}ms`"
      >
        <!-- Arah panah -->
        <span class="arrow" :class="arrowClass(p.b_return)">{{ arrowIcon(p.b_return, p.status) }}</span>

        <!-- Nama coin -->
        <span class="pair-name">{{ p.base }}</span>

        <!-- Persentase -->
        <span class="return-val" :class="returnClass(p.b_return)">
          {{ p.b_return >= 0 ? '+' : '' }}{{ p.b_return.toFixed(2) }}%
        </span>

        <!-- Label status simpel -->
        <span class="status-label" :class="`label-${p.status.toLowerCase()}`">
          <template v-if="p.status === 'FROZEN'">❄ {{ p.freeze_remaining.toFixed(0) }}s</template>
          <template v-else>{{ statusLabel(p.status) }}</template>
        </span>

        <!-- Lag info -->
        <span v-if="p.tau_ms > 0" class="tau-badge">{{ p.tau_ms }}ms</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { useTradeStore } from '../stores/trade'

const store = useTradeStore()
const pairs = ref([])

function arrowIcon(b_return, status) {
  if (status === 'FAST-TRACK') return '⚡'
  if (status === 'TOXIC')      return '☠'
  if (status === 'FROZEN')     return '❄'
  if (b_return >= 0.20)  return '▲▲'
  if (b_return >= 0.07)  return '▲'
  if (b_return <= -0.20) return '▼▼'
  if (b_return <= -0.07) return '▼'
  return '—'
}

function arrowClass(b_return) {
  if (b_return >= 0.20)  return 'arrow-strong-up'
  if (b_return >= 0.07)  return 'arrow-up'
  if (b_return <= -0.20) return 'arrow-strong-down'
  if (b_return <= -0.07) return 'arrow-down'
  return 'arrow-flat'
}

function returnClass(b_return) {
  if (b_return >= 0.20)  return 'val-strong-up'
  if (b_return >= 0.07)  return 'val-up'
  if (b_return <= -0.20) return 'val-strong-down'
  if (b_return <= -0.07) return 'val-down'
  return 'val-neutral'
}

// Label bahasa manusia
function statusLabel(status) {
  const map = {
    'FAST-TRACK': 'PUMP!',
    'BREAKOUT':   'Naik',
    'RISING':     'Naik',
    'NEUTRAL':    'Flat',
    'FADING':     'Turun',
    'DECLINING':  'Turun',
    'TOXIC':      'CRASH!',
  }
  return map[status] ?? status
}

const anyActive = computed(() =>
  pairs.value.some(p => ['FAST-TRACK', 'BREAKOUT', 'TOXIC'].includes(p.status))
)

function handleLatencyPulse(data) {
  if (Array.isArray(data?.pairs)) {
    pairs.value = data.pairs
  }
}

watch(() => store.socket, (newSocket, oldSocket) => {
  if (oldSocket) oldSocket.off('latency_pulse', handleLatencyPulse)
  if (newSocket) newSocket.on('latency_pulse', handleLatencyPulse)
}, { immediate: true })

onUnmounted(() => {
  if (store.socket) store.socket.off('latency_pulse', handleLatencyPulse)
})
</script>

<style scoped>
.latency-strip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 14px;
  background: rgba(255, 255, 255, 0.03);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  overflow-x: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(255,255,255,0.18) transparent;
  flex-shrink: 0;
}
.latency-strip::-webkit-scrollbar { height: 4px; }
.latency-strip::-webkit-scrollbar-track { background: transparent; }
.latency-strip::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.18); border-radius: 2px; }
.latency-strip::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.35); }

/* Label strip */
.strip-label {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: rgba(255, 255, 255, 0.35);
  white-space: nowrap;
  text-transform: uppercase;
  flex-shrink: 0;
}
.label-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: rgba(255, 255, 255, 0.2);
}
.dot-active {
  background: #00ff88;
  box-shadow: 0 0 6px #00ff88;
  animation: pulse-dot 1.2s ease-in-out infinite;
}
.dot-idle { background: rgba(255, 255, 255, 0.2); }

@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.5; transform: scale(0.8); }
}

/* Pairs row */
.pairs-row {
  display: flex;
  gap: 5px;
  align-items: center;
}

/* Card */
.pair-card {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px 3px 6px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.07);
  transition: border-color 0.3s, background 0.3s;
  white-space: nowrap;
  cursor: default;
}

/* Status card colors */
.status-fast-track {
  border-color: rgba(0, 255, 136, 0.45) !important;
  background: rgba(0, 255, 136, 0.07) !important;
  animation: glow-green 1.5s ease-in-out infinite;
}
.status-toxic {
  border-color: rgba(255, 51, 102, 0.45) !important;
  background: rgba(255, 51, 102, 0.07) !important;
  animation: glow-red 1.2s ease-in-out infinite;
}
.status-breakout {
  border-color: rgba(0, 200, 255, 0.3) !important;
  background: rgba(0, 200, 255, 0.05) !important;
}
.status-rising {
  border-color: rgba(0, 255, 136, 0.18) !important;
  background: rgba(0, 255, 136, 0.03) !important;
}
.status-fading {
  border-color: rgba(255, 160, 0, 0.18) !important;
  background: rgba(255, 160, 0, 0.03) !important;
}
.status-declining {
  border-color: rgba(255, 80, 80, 0.28) !important;
  background: rgba(255, 80, 80, 0.04) !important;
}
.status-frozen {
  border-color: rgba(100, 160, 255, 0.35) !important;
  background: rgba(100, 160, 255, 0.05) !important;
}

@keyframes glow-green {
  0%, 100% { box-shadow: none; }
  50%       { box-shadow: 0 0 8px rgba(0, 255, 136, 0.45); }
}
@keyframes glow-red {
  0%, 100% { box-shadow: none; }
  50%       { box-shadow: 0 0 8px rgba(255, 51, 102, 0.5); }
}

/* Arrow */
.arrow {
  font-size: 12px;
  width: 18px;
  text-align: center;
  flex-shrink: 0;
}
.arrow-strong-up   { color: #00ff88; }
.arrow-up          { color: #66ffb2; }
.arrow-flat        { color: rgba(255,255,255,0.25); font-size: 10px; }
.arrow-down        { color: #ffb866; }
.arrow-strong-down { color: #ff5555; }

/* Coin name */
.pair-name {
  font-size: 10px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.75);
  letter-spacing: 0.03em;
  min-width: 26px;
}

/* Percentage */
.return-val {
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  min-width: 44px;
  text-align: right;
}
.val-strong-up   { color: #00ff88; }
.val-up          { color: #66ffb2; }
.val-neutral     { color: rgba(255,255,255,0.35); }
.val-down        { color: #ffb866; }
.val-strong-down { color: #ff5555; }

/* Human-readable status label */
.status-label {
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.05em;
  padding: 1px 5px;
  border-radius: 3px;
  text-transform: uppercase;
  opacity: 0.85;
}
.label-fast-track { background: rgba(0,255,136,0.18); color: #00ff88; }
.label-breakout   { background: rgba(0,200,255,0.15); color: #00c8ff; }
.label-rising     { background: rgba(0,255,136,0.10); color: #66ffb2; }
.label-neutral    { background: rgba(255,255,255,0.07); color: rgba(255,255,255,0.3); }
.label-fading     { background: rgba(255,160,0,0.12); color: #ffa000; }
.label-declining  { background: rgba(255,80,80,0.15); color: #ff5555; }
.label-toxic      { background: rgba(255,51,102,0.22); color: #ff3366; font-weight: 900; }
.label-frozen     { background: rgba(100,160,255,0.15); color: #80aaff; }

/* Lag badge */
.tau-badge {
  font-size: 8px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.28);
  letter-spacing: 0.02em;
  font-variant-numeric: tabular-nums;
  margin-left: 1px;
}
</style>
