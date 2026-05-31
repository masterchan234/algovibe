<template>
  <teleport to="body">
    <transition name="bec-fade">
      <div
        v-if="store.bracketExitConfirm"
        class="bec-overlay"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bec-title"
      >
        <div class="bec-backdrop" />
        <div class="bec-card">
          <!-- Header -->
          <div class="bec-header">
            <span class="bec-icon">⚠️</span>
            <div class="bec-header-text">
              <h2 id="bec-title" class="bec-title">BRACKET EXIT — LOSS PROJECTED</h2>
              <span class="bec-subtitle">Manual confirmation required before proceeding</span>
            </div>
          </div>

          <!-- Details -->
          <div class="bec-body">
            <div class="bec-row">
              <span class="bec-label">EXIT REASON</span>
              <span class="bec-value bec-reason">{{ store.bracketExitConfirm.reason || 'Strategy Exit' }}</span>
            </div>
            <div class="bec-row">
              <span class="bec-label">PROJECTED LOSS</span>
              <span class="bec-value bec-loss">{{ lossText }}</span>
            </div>
            <div class="bec-row" v-if="store.bracketExitConfirm.pairs?.length">
              <span class="bec-label">PAIRS AFFECTED</span>
              <span class="bec-value">{{ pairsText }}</span>
            </div>
            <div class="bec-row" v-if="store.bracketExitConfirm.description">
              <span class="bec-label">DETAILS</span>
              <span class="bec-value bec-details">{{ store.bracketExitConfirm.description }}</span>
            </div>
            <div class="bec-warning">
              <span class="bec-warn-icon">🔴</span>
              Exiting now will lock in a loss. Holding may allow recovery if market conditions improve.
            </div>
          </div>

          <!-- Action Buttons -->
          <div class="bec-actions">
            <button class="bec-btn bec-btn-abort" @click="respond('ABORT')" :disabled="pending">
              {{ pending ? '⏳ WAITING...' : '🛡️ ABORT &amp; HOLD' }}
            </button>
            <button class="bec-btn bec-btn-confirm" @click="respond('CONFIRM')" :disabled="pending">
              {{ pending ? '⏳ WAITING...' : '✅ CONFIRM EXIT' }}
            </button>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useTradeStore } from '../stores/trade'

const store = useTradeStore()
const pending = ref(false)

const lossText = computed(() => {
  const idr = store.bracketExitConfirm?.projected_pnl ?? 0
  const abs = Math.abs(idr)
  const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
  if (isUsdt) {
    return `-$${abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  } else {
    return `-Rp ${abs.toLocaleString('id-ID', { maximumFractionDigits: 0 })}`
  }
})

const pairsText = computed(() => {
  const pairs = store.bracketExitConfirm?.pairs || []
  return pairs.map(p => p.replace(/idr$/i, '').toUpperCase()).join(', ')
})

function respond(decision) {
  const data = store.bracketExitConfirm
  if (!data || pending.value) return
  pending.value = true
  store.socket?.emit('bracket_exit_response', {
    exit_id: data.exit_id,
    decision,
  })
  // Modal dismisses on bracket_exit_confirm_cleared event (handled in store),
  // not immediately, so the operator sees visual feedback until server confirms.
}
</script>

<style scoped>
/* ── Overlay ──────────────────────────────────────────────────────── */
.bec-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px 12px;
}

.bec-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.8);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

/* ── Card ─────────────────────────────────────────────────────────── */
.bec-card {
  position: relative;
  z-index: 1;
  width: min(480px, 100%);
  background: var(--bg-deep);
  border: 1px solid #f85149;
  border-radius: 14px;
  box-shadow: 0 0 40px rgba(248, 81, 73, 0.3), 0 20px 60px rgba(0, 0, 0, 0.8);
  overflow: hidden;
  animation: bec-pop 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes bec-pop {
  from { transform: scale(0.85); opacity: 0; }
  to   { transform: scale(1);    opacity: 1; }
}

/* ── Header ───────────────────────────────────────────────────────── */
.bec-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px 20px;
  background: linear-gradient(135deg, #1a0505 0%, #200808 100%);
  border-bottom: 1px solid #f85149;
}

.bec-icon {
  font-size: 24px;
  flex-shrink: 0;
  margin-top: 2px;
  animation: bec-icon-pulse 1.5s ease-in-out infinite;
}

@keyframes bec-icon-pulse {
  0%, 100% { transform: scale(1); }
  50%       { transform: scale(1.15); }
}

.bec-header-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bec-title {
  margin: 0;
  font-size: 14px;
  font-weight: 800;
  letter-spacing: 1.5px;
  color: #f85149;
  text-transform: uppercase;
}

.bec-subtitle {
  font-size: 10px;
  color: var(--text-secondary);
  letter-spacing: 0.5px;
}

/* ── Body ─────────────────────────────────────────────────────────── */
.bec-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.bec-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.bec-label {
  flex: 0 0 110px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1px;
  color: var(--text-secondary);
  text-transform: uppercase;
  padding-top: 2px;
}

.bec-value {
  flex: 1;
  font-size: 13px;
  color: var(--text-primary);
  word-break: break-word;
}

.bec-reason {
  color: #f5a623;
  font-weight: 700;
}

.bec-loss {
  color: #f85149;
  font-weight: 800;
  font-size: 15px;
}

.bec-details {
  font-size: 12px;
  color: var(--text-secondary);
}

.bec-warning {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  background: rgba(248, 81, 73, 0.08);
  border: 1px solid rgba(248, 81, 73, 0.2);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12px;
  color: #c9606a;
  line-height: 1.5;
  margin-top: 4px;
}

.bec-warn-icon {
  flex-shrink: 0;
  font-size: 14px;
}

/* ── Action Buttons ───────────────────────────────────────────────── */
.bec-actions {
  display: flex;
  gap: 12px;
  padding: 16px 20px;
  border-top: 1px solid var(--border-light);
}

.bec-btn {
  flex: 1;
  padding: 13px 16px;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.5px;
  cursor: pointer;
  font-family: inherit;
  min-height: 46px;
  transition: transform 0.1s, box-shadow 0.2s, background 0.2s;
}

.bec-btn:active {
  transform: scale(0.96);
}

.bec-btn-abort {
  background: var(--bg-surface);
  color: #3fb950;
  border: 1px solid #3fb950;
}

.bec-btn-abort:hover {
  background: rgba(63, 185, 80, 0.12);
  box-shadow: 0 0 16px rgba(63, 185, 80, 0.35);
}

.bec-btn-confirm {
  background: #f85149;
  color: #fff;
}

.bec-btn-confirm:hover {
  background: #d73a49;
  box-shadow: 0 0 16px rgba(248, 81, 73, 0.5);
}

/* ── Transition ───────────────────────────────────────────────────── */
.bec-fade-enter-active,
.bec-fade-leave-active {
  transition: opacity 0.2s ease;
}

.bec-fade-enter-from,
.bec-fade-leave-to {
  opacity: 0;
}

/* ── Mobile adjustments ───────────────────────────────────────────── */
@media (max-width: 480px) {
  .bec-actions {
    flex-direction: column;
  }
  .bec-label {
    flex: 0 0 90px;
  }
}
</style>
