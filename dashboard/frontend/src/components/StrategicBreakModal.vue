<template>
  <teleport to="body">
    <transition name="sb-fade">
      <div v-if="store.strategicBreak" class="sb-overlay" role="dialog" aria-modal="true" aria-labelledby="sb-title">
        <div class="sb-backdrop" />
        <div class="sb-card">
          <!-- Header -->
          <div class="sb-header">
            <span class="sb-icon">🛑</span>
            <h2 id="sb-title" class="sb-title">TARGET HARIAN TERCAPAI — STRATEGIC BREAK</h2>
          </div>

          <!-- Session Analytics -->
          <div class="sb-body">
            <div class="sb-row">
              <span class="sb-label">TARGET</span>
              <span class="sb-value sb-highlight">Rp {{ fmt(store.strategicBreak.target_idr) }}</span>
            </div>
            <div class="sb-row">
              <span class="sb-label">NET PnL</span>
              <span class="sb-value" :class="store.strategicBreak.net_pnl >= 0 ? 'sb-profit' : 'sb-loss'">
                Rp {{ fmt(store.strategicBreak.net_pnl, true) }}
              </span>
            </div>
            <div class="sb-row">
              <span class="sb-label">REALIZED</span>
              <span class="sb-value" :class="store.strategicBreak.realized_pnl >= 0 ? 'sb-profit' : 'sb-loss'">
                Rp {{ fmt(store.strategicBreak.realized_pnl, true) }}
              </span>
            </div>
            <div class="sb-row">
              <span class="sb-label">UNREALIZED</span>
              <span class="sb-value" :class="store.strategicBreak.unrealized_pnl >= 0 ? 'sb-profit' : 'sb-loss'">
                Rp {{ fmt(store.strategicBreak.unrealized_pnl, true) }}
              </span>
            </div>
            <div class="sb-divider" />
            <div class="sb-row">
              <span class="sb-label">WIN RATE</span>
              <span class="sb-value">{{ store.strategicBreak.win_count }}/{{ store.strategicBreak.total_trades }} ({{ store.strategicBreak.win_rate?.toFixed(1) }}%)</span>
            </div>
            <div class="sb-row">
              <span class="sb-label">PROFIT FACTOR</span>
              <span class="sb-value">{{ profitFactorDisplay }}</span>
            </div>
            <div class="sb-row">
              <span class="sb-label">MAX DRAWDOWN</span>
              <span class="sb-value sb-loss">Rp {{ fmt(store.strategicBreak.max_drawdown) }}</span>
            </div>
            <div class="sb-row">
              <span class="sb-label">EST. FEES</span>
              <span class="sb-value">Rp {{ fmt(store.strategicBreak.estimated_fees) }}</span>
            </div>
            <div class="sb-row">
              <span class="sb-label">VOLUME</span>
              <span class="sb-value">Rp {{ fmt(store.strategicBreak.total_volume_idr) }}</span>
            </div>
          </div>

          <!-- Action Buttons -->
          <div class="sb-actions">
            <button class="sb-btn sb-btn-stop" :disabled="isLoading" @click="act('STOP')">
              🔒 STOP (Tetap HARVEST)
            </button>
            <button class="sb-btn sb-btn-reset" :disabled="isLoading" @click="act('RESET')">
              🔄 RESET ke NORMAL
            </button>
            <button class="sb-btn sb-btn-continue" :disabled="isLoading" @click="act('CONTINUE')">
              🚀 CONTINUE (Target Baru)
            </button>
          </div>

          <div v-if="errorMsg" class="sb-error">{{ errorMsg }}</div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'

const { getAuthHeaders } = useAuth()

const store = useTradeStore()
const isLoading = ref(false)
const errorMsg = ref('')

function fmt(val, signed = false) {
  const n = Number(val || 0)
  const prefix = signed ? (n >= 0 ? '+' : '') : ''
  return prefix + n.toLocaleString('id-ID', { maximumFractionDigits: 0 })
}

const profitFactorDisplay = computed(() => {
  const pf = store.strategicBreak?.profit_factor ?? 0
  if (pf >= 9999) return '∞ (no losses)'
  return pf.toFixed(2)
})

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

async function act(action) {
  if (isLoading.value) return
  isLoading.value = true
  errorMsg.value = ''
  try {
    const res = await fetch(`${backendUrl}/api/session/action`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders(),
      },
      body: JSON.stringify({ action }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      errorMsg.value = err.error || `Server error (${res.status})`
    }
    // The server emits strategic_break_cleared via socket — modal will close automatically
  } catch (e) {
    errorMsg.value = 'Network error. Coba lagi.'
  } finally {
    isLoading.value = false
  }
}
</script>

<style scoped>
/* ── Overlay ─────────────────────────────────────────────────── */
.sb-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
}

.sb-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.80);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}

/* ── Card ─────────────────────────────────────────────────────── */
.sb-card {
  position: relative;
  z-index: 1;
  width: min(520px, calc(100vw - 32px));
  background: #0d1117;
  border: 1px solid #f85149;
  border-radius: 14px;
  box-shadow: 0 0 40px rgba(248, 81, 73, 0.25), 0 20px 60px rgba(0, 0, 0, 0.85);
  overflow: hidden;
  animation: sb-pop 0.28s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes sb-pop {
  from { transform: scale(0.82); opacity: 0; }
  to   { transform: scale(1);    opacity: 1; }
}

/* ── Header ─────────────────────────────────────────────────── */
.sb-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  background: linear-gradient(135deg, #1a0a0a 0%, #200d0d 100%);
  border-bottom: 1px solid #f85149;
}

.sb-icon {
  font-size: 22px;
  flex-shrink: 0;
}

.sb-title {
  flex: 1;
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #f85149;
  text-transform: uppercase;
}

/* ── Body rows ──────────────────────────────────────────────── */
.sb-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sb-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.sb-label {
  flex: 0 0 120px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1px;
  color: #8b949e;
  text-transform: uppercase;
  padding-top: 2px;
}

.sb-value {
  flex: 1;
  font-size: 13px;
  color: #e6edf3;
}

.sb-highlight { color: #58a6ff; font-weight: 700; }
.sb-profit    { color: #3fb950; font-weight: 600; }
.sb-loss      { color: #f85149; font-weight: 600; }

.sb-divider {
  border-top: 1px dashed #30363d;
  margin: 4px 0;
}

/* ── Buttons ────────────────────────────────────────────────── */
.sb-actions {
  display: flex;
  gap: 10px;
  padding: 16px 20px;
  border-top: 1px solid #21262d;
  flex-wrap: wrap;
}

.sb-btn {
  flex: 1;
  min-width: 130px;
  padding: 11px 14px;
  border: none;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.4px;
  cursor: pointer;
  transition: transform 0.1s, box-shadow 0.2s, opacity 0.2s;
}

.sb-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.sb-btn:not(:disabled):active { transform: scale(0.96); }

.sb-btn-stop {
  background: #21262d;
  color: #f85149;
  border: 1px solid #f85149;
}
.sb-btn-stop:not(:disabled):hover {
  background: rgba(248, 81, 73, 0.15);
  box-shadow: 0 0 12px rgba(248, 81, 73, 0.3);
}

.sb-btn-reset {
  background: #21262d;
  color: #d29922;
  border: 1px solid #d29922;
}
.sb-btn-reset:not(:disabled):hover {
  background: rgba(210, 153, 34, 0.15);
  box-shadow: 0 0 12px rgba(210, 153, 34, 0.3);
}

.sb-btn-continue {
  background: #238636;
  color: #ffffff;
}
.sb-btn-continue:not(:disabled):hover {
  background: #2ea043;
  box-shadow: 0 0 12px rgba(63, 185, 80, 0.4);
}

.sb-error {
  padding: 0 20px 14px;
  font-size: 12px;
  color: #f85149;
  text-align: center;
}

/* ── Transition ─────────────────────────────────────────────── */
.sb-fade-enter-active,
.sb-fade-leave-active {
  transition: opacity 0.2s ease;
}
.sb-fade-enter-from,
.sb-fade-leave-to {
  opacity: 0;
}
</style>
