<template>
  <div class="notification-center">
    <transition-group name="toast">
      <div
        v-for="n in notifications"
        :key="n.id"
        class="toast"
        :class="n.type"
        @click="remove(n.id)"
      >
        <div class="toast-icon">
          <span v-if="n.type === 'success'">✅</span>
          <span v-else-if="n.type === 'error'">❌</span>
          <span v-else-if="n.type === 'warning'">⚠️</span>
          <span v-else-if="n.type === 'ai'">🔮</span>
          <span v-else>ℹ️</span>
        </div>
        <div class="toast-content">
          <div class="toast-title">{{ n.title }}</div>
          <div class="toast-msg">{{ n.message }}</div>
        </div>
      </div>
    </transition-group>
  </div>
</template>

<script setup>
import { useNotifications } from '../composables/useNotifications'

const { notifications, remove } = useNotifications()
</script>

<style scoped>
.notification-center {
  position: fixed;
  top: 70px;
  right: 20px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 10px;
  pointer-events: none;
}

.toast {
  pointer-events: auto;
  width: 320px;
  background: var(--bg-surface);
  border: 1px solid var(--border-trader);
  border-radius: 10px;
  padding: 12px 16px;
  display: flex;
  gap: 12px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.5);
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}

.toast:hover {
  transform: translateX(-5px);
  background: var(--border-light);
}

.toast-icon {
  font-size: 20px;
  display: flex;
  align-items: center;
}

.toast-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.toast-title {
  font-weight: 800;
  font-size: 13px;
  color: #fff;
  letter-spacing: 0.5px;
}

.toast-msg {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.4;
}

/* Types */
.toast.success { border-left: 4px solid #3fb950; }
.toast.error   { border-left: 4px solid #f85149; }
.toast.warning { border-left: 4px solid #d29922; }
.toast.ai      { border-left: 4px solid #a371f7; }
.toast.info    { border-left: 4px solid #58a6ff; }

/* Animation */
.toast-enter-from { opacity: 0; transform: translateX(50px); }
.toast-leave-to   { opacity: 0; transform: scale(0.9); }
</style>
