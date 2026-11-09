<template>
  <footer class="app-footer">
    <div class="footer-left">
      <span class="status-indicator" :class="{ connected: store.connected }"></span>
      <span class="status-text">{{ store.connected ? 'SYSTEM ONLINE' : 'SYSTEM OFFLINE' }}</span>
      <span class="divider">|</span>
      <span class="uptime">UPTIME: {{ formatUptime(store.stats.system_uptime) }}</span>
      <span class="divider">|</span>
      <span class="latency" :class="latencyClass">LATENCY: {{ store.latency }}ms</span>
    </div>
    
    <div class="footer-right">
      <button 
        v-if="!isLive" 
        class="reset-btn" 
        @click="confirmReset" 
        :disabled="isResetting"
      >
        <span class="icon">⚠️</span> {{ isResetting ? 'RESETTING...' : 'RESET DATA' }}
      </button>
    </div>
  </footer>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useTradeStore } from '../stores/trade'
import { useAuth } from '../composables/useAuth'

const props = defineProps({
  isLive: {
    type: Boolean,
    default: false
  }
})

const store = useTradeStore()
const { getAuthHeaders } = useAuth()
const isResetting = ref(false)

const backendUrl = import.meta.env.VITE_BACKEND_URL || ''

const latencyClass = computed(() => {
  if (store.latency < 50) return 'good'
  if (store.latency < 150) return 'warn'
  return 'bad'
})

function formatUptime(seconds) {
  if (!seconds || isNaN(seconds)) return '00:00:00'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

async function confirmReset() {
  if (!confirm('WARNING: This will delete ALL trade history, positions, analytics, and reset the equity curve back to $0.00 for DRY RUN. Proceed?')) {
    return
  }
  
  isResetting.value = true
  try {
    const res = await fetch(`${backendUrl}/api/dryrun/reset`, {
      method: 'POST',
      headers: getAuthHeaders()
    })
    
    if (res.ok) {
      alert('Data successfully reset. The dashboard will now reload.')
      window.location.reload()
    } else {
      const data = await res.json()
      alert(`Reset failed: ${data.error || 'Unknown error'}`)
    }
  } catch (err) {
    alert(`Reset failed: ${err.message}`)
  } finally {
    isResetting.value = false
  }
}
</script>

<style scoped>
.app-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 16px;
  background-color: var(--bg-card);
  border-top: 1px solid var(--border-color);
  font-family: 'Inter', sans-serif;
  font-size: 0.75rem;
  color: var(--text-muted);
  z-index: 100;
}

.footer-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--danger-color);
  box-shadow: 0 0 8px var(--danger-color);
}
.status-indicator.connected {
  background-color: var(--success-color);
  box-shadow: 0 0 8px var(--success-color);
}

.status-text {
  font-weight: 600;
  letter-spacing: 0.05em;
  color: var(--text-color);
}

.divider {
  color: var(--border-color);
}

.uptime, .latency {
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
}

.latency.good { color: var(--success-color); }
.latency.warn { color: var(--warning-color); }
.latency.bad { color: var(--danger-color); }

.reset-btn {
  background: rgba(248, 81, 73, 0.1);
  border: 1px solid rgba(248, 81, 73, 0.3);
  color: var(--danger-color);
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 0.7rem;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.2s ease;
}

.reset-btn:hover:not(:disabled) {
  background: rgba(248, 81, 73, 0.2);
  border-color: rgba(248, 81, 73, 0.5);
  box-shadow: 0 0 10px rgba(248, 81, 73, 0.2);
}

.reset-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@media (max-width: 768px) {
  .app-footer {
    flex-direction: column;
    gap: 8px;
    padding-bottom: env(safe-area-inset-bottom, 16px);
  }
}
</style>
