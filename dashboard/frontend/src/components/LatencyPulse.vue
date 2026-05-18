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
        :class="[`status-${p.status.toLowerCase()}`, { 'is-frozen': p.freeze_remaining > 0 }]"
        :title="`${p.base}: Binance ${p.b_return >= 0 ? '+' : ''}${p.b_return.toFixed(3)}% / ${p.actual_ms}ms`"
      >
        <!-- Pair label -->
        <span class="pair-name">{{ p.base }}</span>

        <!-- Return bar (symmetric, center = 0) -->
        <div class="bar-track">
          <div
            class="bar-fill"
            :class="p.b_return >= 0 ? 'bar-up' : 'bar-down'"
            :style="barStyle(p.b_return)"
          ></div>
          <div class="bar-zero"></div>
        </div>

        <!-- Return value -->
        <span class="return-val" :class="returnClass(p.b_return)">
          {{ p.b_return >= 0 ? '+' : '' }}{{ p.b_return.toFixed(2) }}%
        </span>

        <!-- Status badge -->
        <span class="status-badge" :class="`badge-${p.status.toLowerCase()}`">
          <template v-if="p.status === 'FROZEN'">
            FROZEN {{ p.freeze_remaining.toFixed(0) }}s
          </template>
          <template v-else>{{ p.status }}</template>
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { useTradeStore } from '../stores/trade'

const store = useTradeStore()
const pairs = ref([])

// Maximum % range for bar width (clip at ±1%)
const MAX_PCT = 1.0

function barStyle(b_return) {
  const pct = Math.min(Math.abs(b_return) / MAX_PCT, 1.0) * 50  // max 50% of half-track
  if (b_return >= 0) {
    return { left: '50%', width: pct + '%' }
  } else {
    return { right: '50%', width: pct + '%' }
  }
}

function returnClass(b_return) {
  if (b_return >= 0.35) return 'val-strong-up'
  if (b_return >= 0.10) return 'val-up'
  if (b_return <= -0.50) return 'val-toxic'
  if (b_return <= -0.25) return 'val-down'
  return 'val-neutral'
}

const anyActive = computed(() =>
  pairs.value.some(p => p.status === 'FAST-TRACK' || p.status === 'BREAKOUT' || p.status === 'TOXIC')
)

function handleLatencyPulse(data) {
  if (Array.isArray(data?.pairs)) {
    pairs.value = data.pairs
  }
}

// Daftarkan listener saat socket tersedia (bisa mount sebelum koneksi)
watch(() => store.socket, (newSocket, oldSocket) => {
  if (oldSocket) {
    oldSocket.off('latency_pulse', handleLatencyPulse)
  }
  if (newSocket) {
    newSocket.on('latency_pulse', handleLatencyPulse)
  }
}, { immediate: true })

onUnmounted(() => {
  if (store.socket) {
    store.socket.off('latency_pulse', handleLatencyPulse)
  }
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
  scrollbar-width: none;
  flex-shrink: 0;
}
.latency-strip::-webkit-scrollbar { display: none; }

/* Label */
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
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.2);
}
.dot-active {
  background: #00ff88;
  box-shadow: 0 0 6px #00ff88;
  animation: pulse-dot 1.2s ease-in-out infinite;
}
.dot-idle {
  background: rgba(255, 255, 255, 0.2);
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.5; transform: scale(0.8); }
}

/* Pairs row */
.pairs-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

/* Individual pair card */
.pair-card {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px 3px 6px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.07);
  transition: border-color 0.3s, background 0.3s;
  white-space: nowrap;
  cursor: default;
  min-width: 140px;
}

/* Status-specific card styling */
.status-fast-track {
  border-color: rgba(0, 255, 136, 0.4) !important;
  background: rgba(0, 255, 136, 0.05) !important;
  animation: glow-green 1.5s ease-in-out infinite;
}
.status-toxic, .status-frozen {
  border-color: rgba(255, 51, 102, 0.4) !important;
  background: rgba(255, 51, 102, 0.05) !important;
  animation: glow-red 1.2s ease-in-out infinite;
}
.status-breakout {
  border-color: rgba(0, 200, 255, 0.3) !important;
  background: rgba(0, 200, 255, 0.04) !important;
}

@keyframes glow-green {
  0%, 100% { box-shadow: 0 0 0px rgba(0, 255, 136, 0); }
  50%       { box-shadow: 0 0 8px rgba(0, 255, 136, 0.4); }
}
@keyframes glow-red {
  0%, 100% { box-shadow: 0 0 0px rgba(255, 51, 102, 0); }
  50%       { box-shadow: 0 0 8px rgba(255, 51, 102, 0.5); }
}

/* Pair name */
.pair-name {
  font-size: 10px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.7);
  letter-spacing: 0.04em;
  min-width: 28px;
}

/* Bar track */
.bar-track {
  position: relative;
  width: 52px;
  height: 4px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 2px;
  overflow: hidden;
  flex-shrink: 0;
}
.bar-fill {
  position: absolute;
  top: 0;
  height: 100%;
  border-radius: 2px;
  transition: width 0.4s ease, left 0.4s ease, right 0.4s ease;
}
.bar-up   { background: #00ff88; }
.bar-down { background: #ff3366; }
.bar-zero {
  position: absolute;
  top: 0;
  left: 50%;
  width: 1px;
  height: 100%;
  background: rgba(255, 255, 255, 0.25);
}

/* Return value */
.return-val {
  font-size: 10px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  min-width: 40px;
  text-align: right;
}
.val-strong-up { color: #00ff88; }
.val-up        { color: #66ffb2; }
.val-neutral   { color: rgba(255, 255, 255, 0.4); }
.val-down      { color: #ff8866; }
.val-toxic     { color: #ff3366; font-weight: 800; }

/* Status badge */
.status-badge {
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 1px 4px;
  border-radius: 3px;
  text-transform: uppercase;
}
.badge-fast-track { background: rgba(0, 255, 136, 0.18); color: #00ff88; }
.badge-breakout   { background: rgba(0, 200, 255, 0.15); color: #00c8ff; }
.badge-neutral    { background: rgba(255, 255, 255, 0.07); color: rgba(255,255,255,0.35); }
.badge-declining  { background: rgba(255, 136, 50, 0.15); color: #ff8832; }
.badge-toxic      { background: rgba(255, 51, 102, 0.2); color: #ff3366; font-weight: 900; }
.badge-frozen     { background: rgba(255, 51, 102, 0.12); color: #ff6688; }
</style>
