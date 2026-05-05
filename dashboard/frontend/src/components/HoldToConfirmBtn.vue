<template>
  <button
    class="htc-btn"
    :class="[variantClass, { 'is-holding': isHolding, 'is-done': isDone }]"
    :disabled="disabled || isDone"
    @mousedown="startHold"
    @mouseup="cancelHold"
    @mouseleave="cancelHold"
    @touchstart.prevent="startHold"
    @touchend.prevent="cancelHold"
    @touchcancel="cancelHold"
    :aria-label="label"
    :title="`Hold ${holdMs / 1000}s to confirm`"
  >
    <!-- Progress ring overlay -->
    <svg class="htc-ring" viewBox="0 0 44 44" aria-hidden="true">
      <circle class="htc-ring-bg" cx="22" cy="22" r="18" />
      <circle
        class="htc-ring-bar"
        cx="22" cy="22" r="18"
        :stroke-dashoffset="ringOffset"
      />
    </svg>

    <span class="htc-label">
      <span v-if="isHolding">{{ holdingText }}</span>
      <span v-else-if="isDone">{{ doneText }}</span>
      <span v-else><slot>{{ label }}</slot></span>
    </span>
  </button>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'

const props = defineProps({
  /** Button label when idle */
  label: { type: String, default: 'Hold to Confirm' },
  /** Text shown while holding */
  holdingText: { type: String, default: 'Holding…' },
  /** Text shown after successful hold */
  doneText: { type: String, default: '✓ Confirmed' },
  /** Time in ms to hold before firing */
  holdMs: { type: Number, default: 2000 },
  /** Colour variant: 'danger' | 'warning' | 'primary' */
  variant: { type: String, default: 'danger' },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm'])

const isHolding = ref(false)
const isDone = ref(false)
const progress = ref(0)  // 0–1

// SVG ring: circumference ≈ 2π × 18 ≈ 113
const CIRC = 2 * Math.PI * 18
const ringOffset = computed(() => CIRC * (1 - progress.value))

const variantClass = computed(() => `htc-${props.variant}`)

let _raf = null
let _startTs = 0

function startHold() {
  if (props.disabled || isDone.value) return
  isHolding.value = true
  _startTs = Date.now()
  _tick()
}

function _tick() {
  const elapsed = Date.now() - _startTs
  progress.value = Math.min(elapsed / props.holdMs, 1)
  if (progress.value >= 1) {
    // Hold complete
    isHolding.value = false
    isDone.value = true
    emit('confirm')
    // Auto-reset after 2 s so button can be re-used
    setTimeout(() => {
      isDone.value = false
      progress.value = 0
    }, 2000)
    return
  }
  _raf = requestAnimationFrame(_tick)
}

function cancelHold() {
  if (!isHolding.value) return
  isHolding.value = false
  progress.value = 0
  if (_raf) cancelAnimationFrame(_raf)
  _raf = null
}

onUnmounted(cancelHold)
</script>

<style scoped>
.htc-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 12px 20px;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 1px;
  cursor: pointer;
  font-family: inherit;
  user-select: none;
  -webkit-user-select: none;
  min-height: 44px;
  min-width: 140px;
  overflow: hidden;
  transition: opacity 0.2s, box-shadow 0.2s;
}

.htc-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* Variants */
.htc-danger  { background: rgba(248,81,73,0.12); color: #f85149; border: 1px solid rgba(248,81,73,0.4); }
.htc-warning { background: rgba(245,166,35,0.12); color: #f5a623; border: 1px solid rgba(245,166,35,0.4); }
.htc-primary { background: rgba(88,166,255,0.12); color: #58a6ff; border: 1px solid rgba(88,166,255,0.4); }

.htc-danger.is-holding  { background: rgba(248,81,73,0.2);  box-shadow: 0 0 16px rgba(248,81,73,0.4); }
.htc-warning.is-holding { background: rgba(245,166,35,0.2); box-shadow: 0 0 16px rgba(245,166,35,0.4); }
.htc-primary.is-holding { background: rgba(88,166,255,0.2); box-shadow: 0 0 16px rgba(88,166,255,0.4); }

.htc-danger.is-done  { background: rgba(63,185,80,0.15); color: #3fb950; border-color: #3fb950; }
.htc-warning.is-done { background: rgba(63,185,80,0.15); color: #3fb950; border-color: #3fb950; }
.htc-primary.is-done { background: rgba(63,185,80,0.15); color: #3fb950; border-color: #3fb950; }

/* SVG ring */
.htc-ring {
  position: absolute;
  left: 8px;
  width: 28px;
  height: 28px;
  transform: rotate(-90deg);
  opacity: 0;
  transition: opacity 0.15s;
}

.htc-btn.is-holding .htc-ring { opacity: 1; }

.htc-ring-bg {
  fill: none;
  stroke: rgba(255,255,255,0.1);
  stroke-width: 3;
}

.htc-danger  .htc-ring-bar { stroke: #f85149; }
.htc-warning .htc-ring-bar { stroke: #f5a623; }
.htc-primary .htc-ring-bar { stroke: #58a6ff; }

.htc-ring-bar {
  fill: none;
  stroke-width: 3;
  stroke-linecap: round;
  stroke-dasharray: 113;
  transition: stroke-dashoffset 0.05s linear;
}

.htc-label { position: relative; z-index: 1; }
</style>
