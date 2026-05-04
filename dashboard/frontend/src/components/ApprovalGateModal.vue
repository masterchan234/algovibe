<template>
  <teleport to="body">
    <transition name="ag-fade">
      <div v-if="store.pendingAction" class="ag-overlay" role="dialog" aria-modal="true" aria-labelledby="ag-title">
        <div class="ag-backdrop" />
        <div class="ag-card">
          <!-- Header -->
          <div class="ag-header">
            <span class="ag-icon">⚠️</span>
            <h2 id="ag-title" class="ag-title">SUPREME COMMANDER APPROVAL</h2>
            <div class="ag-timer-ring">
              <svg viewBox="0 0 36 36" class="ag-ring-svg">
                <circle class="ag-ring-bg" cx="18" cy="18" r="15.9" />
                <circle
                  class="ag-ring-bar"
                  cx="18" cy="18" r="15.9"
                  :stroke-dashoffset="ringOffset"
                />
              </svg>
              <span class="ag-timer-value">{{ countdown }}</span>
            </div>
          </div>

          <!-- Action Details -->
          <div class="ag-body">
            <div class="ag-row">
              <span class="ag-label">ACTION</span>
              <span class="ag-value ag-action-type">{{ store.pendingAction.action_type }}</span>
            </div>
            <div class="ag-row">
              <span class="ag-label">ASSETS</span>
              <span class="ag-value">{{ store.pendingAction.description }}</span>
            </div>
            <div class="ag-row">
              <span class="ag-label">REASON</span>
              <span class="ag-value">{{ store.pendingAction.reason }}</span>
            </div>
            <div class="ag-row">
              <span class="ag-label">IMPACT</span>
              <span class="ag-value" :class="impactClass">{{ impactText }}</span>
            </div>
            <div class="ag-row ag-fallback-row">
              <span class="ag-label">TIMEOUT DEFAULT</span>
              <span class="ag-value" :class="store.pendingAction.default_approve ? 'ag-approve-text' : 'ag-reject-text'">
                {{ store.pendingAction.default_approve ? 'AUTO-APPROVE' : 'AUTO-REJECT' }}
                in {{ countdown }}s
              </span>
            </div>
          </div>

          <!-- Action Buttons -->
          <div class="ag-actions">
            <button class="ag-btn ag-btn-reject" @click="respond('REJECTED')">
              🚫 REJECT &amp; HOLD
            </button>
            <button class="ag-btn ag-btn-approve" @click="respond('APPROVED')">
              ✅ APPROVE &amp; EXECUTE
            </button>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup>
import { computed, watch, ref, onUnmounted } from 'vue'
import { useTradeStore } from '../stores/trade'

const store = useTradeStore()

const TIMEOUT_SEC = 30
const countdown = ref(TIMEOUT_SEC)
let _intervalId = null

function startCountdown() {
  clearInterval(_intervalId)
  countdown.value = TIMEOUT_SEC
  _intervalId = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      clearInterval(_intervalId)
    }
  }, 1000)
}

function stopCountdown() {
  clearInterval(_intervalId)
  countdown.value = TIMEOUT_SEC
}

watch(
  () => store.pendingAction,
  (newVal) => {
    if (newVal) {
      startCountdown()
    } else {
      stopCountdown()
    }
  }
)

onUnmounted(() => stopCountdown())

// SVG progress ring: circumference ≈ 100 (r=15.9)
const CIRCUMFERENCE = 2 * Math.PI * 15.9
const ringOffset = computed(() => {
  const progress = countdown.value / TIMEOUT_SEC
  return CIRCUMFERENCE * (1 - progress)
})

const impactText = computed(() => {
  const idr = store.pendingAction?.impact_idr ?? 0
  const sign = idr >= 0 ? '+' : ''
  return `${sign}Rp ${Math.abs(idr).toLocaleString('id-ID', { maximumFractionDigits: 0 })}`
})

const impactClass = computed(() => {
  const idr = store.pendingAction?.impact_idr ?? 0
  return idr >= 0 ? 'ag-profit' : 'ag-loss'
})

function respond(decision) {
  const action = store.pendingAction
  if (!action) return
  store.socket?.emit('approval_response', {
    action_id: action.action_id,
    decision,
  })
  store.pendingAction = null
  stopCountdown()
}
</script>

<style scoped>
/* ── Overlay ─────────────────────────────────────────────────── */
.ag-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ag-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}

/* ── Card ─────────────────────────────────────────────────────── */
.ag-card {
  position: relative;
  z-index: 1;
  width: min(480px, calc(100vw - 32px));
  background: #0d1117;
  border: 1px solid #f0a500;
  border-radius: 14px;
  box-shadow: 0 0 40px rgba(240, 165, 0, 0.25), 0 20px 60px rgba(0, 0, 0, 0.8);
  overflow: hidden;
  animation: ag-pop 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes ag-pop {
  from { transform: scale(0.85); opacity: 0; }
  to   { transform: scale(1);    opacity: 1; }
}

/* ── Header ─────────────────────────────────────────────────── */
.ag-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  background: linear-gradient(135deg, #1a1000 0%, #221500 100%);
  border-bottom: 1px solid #f0a500;
}

.ag-icon {
  font-size: 22px;
  flex-shrink: 0;
}

.ag-title {
  flex: 1;
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 1.5px;
  color: #f0a500;
  text-transform: uppercase;
}

/* ── Countdown ring ─────────────────────────────────────────── */
.ag-timer-ring {
  position: relative;
  width: 44px;
  height: 44px;
  flex-shrink: 0;
}

.ag-ring-svg {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}

.ag-ring-bg {
  fill: none;
  stroke: #2d2200;
  stroke-width: 3;
}

.ag-ring-bar {
  fill: none;
  stroke: #f0a500;
  stroke-width: 3;
  stroke-linecap: round;
  stroke-dasharray: 100;
  transition: stroke-dashoffset 1s linear;
}

.ag-timer-value {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  color: #f0a500;
}

/* ── Body rows ──────────────────────────────────────────────── */
.ag-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ag-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.ag-label {
  flex: 0 0 110px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1px;
  color: #8b949e;
  text-transform: uppercase;
  padding-top: 2px;
}

.ag-value {
  flex: 1;
  font-size: 13px;
  color: #e6edf3;
  word-break: break-word;
}

.ag-action-type {
  color: #f0a500;
  font-weight: 700;
  font-size: 14px;
}

.ag-profit { color: #3fb950; font-weight: 700; }
.ag-loss   { color: #f85149; font-weight: 700; }

.ag-fallback-row {
  border-top: 1px dashed #30363d;
  padding-top: 10px;
  margin-top: 4px;
}

.ag-approve-text { color: #3fb950; font-weight: 600; }
.ag-reject-text  { color: #f85149; font-weight: 600; }

/* ── Buttons ────────────────────────────────────────────────── */
.ag-actions {
  display: flex;
  gap: 12px;
  padding: 16px 20px;
  border-top: 1px solid #21262d;
}

.ag-btn {
  flex: 1;
  padding: 12px 16px;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.5px;
  cursor: pointer;
  transition: transform 0.1s, box-shadow 0.2s;
}

.ag-btn:active { transform: scale(0.96); }

.ag-btn-reject {
  background: #21262d;
  color: #f85149;
  border: 1px solid #f85149;
}
.ag-btn-reject:hover {
  background: rgba(248, 81, 73, 0.15);
  box-shadow: 0 0 12px rgba(248, 81, 73, 0.3);
}

.ag-btn-approve {
  background: #238636;
  color: #ffffff;
}
.ag-btn-approve:hover {
  background: #2ea043;
  box-shadow: 0 0 12px rgba(63, 185, 80, 0.4);
}

/* ── Transition ─────────────────────────────────────────────── */
.ag-fade-enter-active,
.ag-fade-leave-active {
  transition: opacity 0.2s ease;
}
.ag-fade-enter-from,
.ag-fade-leave-to {
  opacity: 0;
}
</style>
