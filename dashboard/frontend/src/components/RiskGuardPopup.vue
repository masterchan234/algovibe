<template>
  <Transition name="fade">
    <div v-if="pendingAction" class="risk-guard-overlay">
      <div class="risk-guard-modal" :class="{ 'is-critical': isCritical }">
        <div class="rg-header">
          <div class="rg-icon">⚠️</div>
          <div class="rg-title-group">
            <h2 class="rg-title">RISK GUARD ACTIVATED</h2>
            <p class="rg-subtitle">SUPREME COMMANDER APPROVAL REQUIRED</p>
          </div>
        </div>

        <div class="rg-content">
          <div class="rg-alert-box" v-if="isNegativeImpact">
            <span class="rg-alert-icon">🧨</span>
            <div class="rg-alert-text">
              <strong>NEGATIVE PnL DETECTED</strong>
              <p>Executing this basket exit will realize a net loss of Rp {{ formatNumber(Math.abs(pendingAction.impact_idr)) }}.</p>
            </div>
          </div>

          <div class="rg-details">
            <div class="rg-detail-row">
              <span class="rg-label">ACTION</span>
              <span class="rg-value">{{ pendingAction.action_type }}</span>
            </div>
            <div class="rg-detail-row">
              <span class="rg-label">REASON</span>
              <span class="rg-value highlight">{{ pendingAction.reason }}</span>
            </div>
            <div class="rg-detail-row">
              <span class="rg-label">AFFECTED ASSETS</span>
              <span class="rg-value">{{ pendingAction.description }}</span>
            </div>
            <div class="rg-detail-row">
              <span class="rg-label">PROJECTED IMPACT</span>
              <span class="rg-value" :class="pnlClass">
                Rp {{ formatNumber(pendingAction.impact_idr) }}
              </span>
            </div>
          </div>

          <div class="rg-timer">
            <div class="timer-bar" :style="{ width: timerWidth + '%' }"></div>
            <p class="timer-text">
              Auto-{{ pendingAction.default_approve ? 'Approve' : 'Reject' }} in {{ timeLeft }}s
            </p>
          </div>
        </div>

        <div class="rg-actions">
          <button class="btn-abort" @click="respond('REJECTED')" :disabled="isResponding">
            ABORT & HOLD
          </button>
          <button class="btn-confirm" @click="respond('APPROVED')" :disabled="isResponding">
            <span v-if="isResponding">SYNC...</span>
            <span v-else>CONFIRM EXIT</span>
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps({
  socket: { type: Object, default: null }
})

const pendingAction = ref(null)
const isResponding = ref(false)
const timeLeft = ref(0)
const timerWidth = ref(100)
let timerInterval = null

const isCritical = computed(() => pendingAction.value?.impact_idr < 0)
const isNegativeImpact = computed(() => pendingAction.value?.impact_idr < 0)
const pnlClass = computed(() => {
  if (!pendingAction.value) return ''
  return pendingAction.value.impact_idr >= 0 ? 'c-bull' : 'c-bear'
})

function formatNumber(val) {
  if (val === undefined || val === null) return '0'
  return new Intl.NumberFormat('id-ID').format(Math.round(val))
}

function handlePendingAction(data) {
  console.log('🛡️ Risk Guard: Received pending action', data)
  pendingAction.value = data
  timeLeft.value = 10
  timerWidth.value = 100
  startTimer()
}

function clearAction() {
  pendingAction.value = null
  isResponding.value = false
  if (timerInterval) clearInterval(timerInterval)
}

function startTimer() {
  if (timerInterval) clearInterval(timerInterval)
  const startTime = Date.now()
  const duration = 10000 // 10s
  
  timerInterval = setInterval(() => {
    const elapsed = Date.now() - startTime
    timeLeft.value = Math.max(0, Math.ceil((duration - elapsed) / 1000))
    timerWidth.value = Math.max(0, ((duration - elapsed) / duration) * 100)
    
    if (elapsed >= duration) {
      clearInterval(timerInterval)
      // The backend handles the timeout, so we just clear the UI
      clearAction()
    }
  }, 100)
}

function respond(decision) {
  if (!pendingAction.value || isResponding.value) return
  
  // Extreme defensive check for socket existence
  const s = props.socket
  if (!s || typeof s.emit !== 'function') {
    console.warn('🛡️ Risk Guard: Socket unavailable, clearing action locally.')
    clearAction()
    return
  }
  
  isResponding.value = true
  console.log(`🛡️ Risk Guard: Sending response ${decision} for ${pendingAction.value.action_id}`)
  
  try {
    s.emit('approval_response', {
      action_id: pendingAction.value.action_id,
      decision: decision
    })
  } catch (e) {
    console.error('🛡️ Risk Guard: Failed to emit response', e)
  }
  
  // Wait for clear event from backend or just clear after small delay
  setTimeout(() => {
    clearAction()
  }, 500)
}

function attachSocketListeners(socket) {
  if (!socket || typeof socket.on !== 'function') return
  socket.on('BRACKET_EXIT_CONFIRM', handlePendingAction)
  socket.on('pending_action', handlePendingAction)
  socket.on('pending_action_cleared', clearAction)
}

function detachSocketListeners(socket) {
  if (!socket || typeof socket.off !== 'function') return
  socket.off('BRACKET_EXIT_CONFIRM', handlePendingAction)
  socket.off('pending_action', handlePendingAction)
  socket.off('pending_action_cleared', clearAction)
}

onMounted(() => {
  attachSocketListeners(props.socket)
})

watch(() => props.socket, (nextSocket, prevSocket) => {
  if (prevSocket !== nextSocket) {
    detachSocketListeners(prevSocket)
    attachSocketListeners(nextSocket)
  }
})

onUnmounted(() => {
  detachSocketListeners(props.socket)
  if (timerInterval) clearInterval(timerInterval)
})
</script>

<style scoped>
.risk-guard-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.85);
  backdrop-filter: blur(10px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
  padding: 20px;
}

.risk-guard-modal {
  width: 100%;
  max-width: 500px;
  background: #0a0c14;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 24px;
  overflow: hidden;
  box-shadow: 0 50px 100px rgba(0, 0, 0, 0.8);
  animation: modalIn 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}

@keyframes modalIn {
  from { opacity: 0; transform: scale(0.9) translateY(30px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}

.risk-guard-modal.is-critical {
  border-color: rgba(255, 51, 102, 0.4);
  box-shadow: 0 0 50px rgba(255, 51, 102, 0.1), 0 50px 100px rgba(0, 0, 0, 0.8);
}

.rg-header {
  padding: 32px;
  display: flex;
  align-items: center;
  gap: 20px;
  background: linear-gradient(to bottom, rgba(255, 255, 255, 0.03), transparent);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.rg-icon {
  font-size: 40px;
}

.rg-title {
  font-size: 20px;
  font-weight: 900;
  letter-spacing: 2px;
  color: #fff;
  margin: 0;
}

.rg-subtitle {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.5px;
  color: #555;
  margin: 4px 0 0 0;
}

.rg-content {
  padding: 32px;
}

.rg-alert-box {
  background: rgba(255, 51, 102, 0.1);
  border: 1px solid rgba(255, 51, 102, 0.2);
  border-radius: 16px;
  padding: 16px;
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
}

.rg-alert-icon { font-size: 24px; }
.rg-alert-text { font-size: 13px; color: #ff3366; }
.rg-alert-text strong { display: block; margin-bottom: 4px; font-weight: 800; letter-spacing: 1px; }

.rg-details {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.rg-detail-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
}

.rg-label {
  font-size: 10px;
  font-weight: 800;
  color: #444;
  letter-spacing: 1px;
  white-space: nowrap;
}

.rg-value {
  font-size: 13px;
  font-weight: 700;
  color: #eee;
  text-align: right;
}

.rg-value.highlight { color: #6366f1; }
.c-bull { color: #00ff88; }
.c-bear { color: #ff3366; }

.rg-timer {
  margin-top: 32px;
  background: rgba(255, 255, 255, 0.03);
  height: 4px;
  border-radius: 2px;
  position: relative;
}

.timer-bar {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  background: #fff;
  border-radius: 2px;
  transition: width 0.1s linear;
}

.timer-text {
  position: absolute;
  top: 12px;
  right: 0;
  font-size: 10px;
  font-weight: 700;
  color: #333;
}

.rg-actions {
  display: flex;
  padding: 0 32px 32px 32px;
  gap: 16px;
}

.btn-abort, .btn-confirm {
  flex: 1;
  padding: 16px;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 1px;
  cursor: pointer;
  transition: all 0.3s ease;
}

.btn-abort {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #888;
}

.btn-abort:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

.btn-confirm {
  background: #fff;
  border: none;
  color: #000;
}

.btn-confirm:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 10px 20px rgba(255, 255, 255, 0.1);
}

.btn-confirm:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
