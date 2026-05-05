<template>
  <div class="nf-page">
    <div class="nf-card">
      <div class="nf-code">404</div>
      <div class="nf-brand">
        <span class="nf-dot"></span>
        <span class="nf-title">ALGOVIBE</span>
      </div>
      <h1 class="nf-heading">PAGE NOT FOUND</h1>
      <p class="nf-desc">
        The route <code class="nf-path">{{ currentPath }}</code> does not exist in the system.
      </p>
      <div class="nf-actions">
        <button class="nf-btn nf-btn-primary" @click="goHome">← BACK TO DASHBOARD</button>
        <button class="nf-btn nf-btn-secondary" @click="router.back()">GO BACK</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()

const currentPath = computed(() => route.fullPath)

function goHome() {
  router.push('/')
}
</script>

<style scoped>
.nf-page {
  min-height: 100vh;
  background: #060810;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
  padding: 16px;
}

.nf-card {
  text-align: center;
  max-width: 480px;
  width: 100%;
}

.nf-code {
  font-size: 96px;
  font-weight: 900;
  letter-spacing: -4px;
  background: linear-gradient(135deg, #a371f7 0%, #58a6ff 50%, #3fb950 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  line-height: 1;
  margin-bottom: 16px;
  animation: nf-glow 3s ease-in-out infinite;
}

@keyframes nf-glow {
  0%, 100% { filter: brightness(1); }
  50%       { filter: brightness(1.2); }
}

.nf-brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-bottom: 20px;
}

.nf-dot {
  width: 8px;
  height: 8px;
  background: #a371f7;
  border-radius: 50%;
  box-shadow: 0 0 10px #a371f7;
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.4; transform: scale(0.7); }
}

.nf-title {
  font-size: 16px;
  font-weight: 900;
  letter-spacing: 3px;
  background: linear-gradient(90deg, #a371f7, #58a6ff);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.nf-heading {
  font-size: 22px;
  font-weight: 800;
  letter-spacing: 2px;
  color: #e6edf3;
  margin: 0 0 12px;
}

.nf-desc {
  font-size: 13px;
  color: #8b949e;
  margin: 0 0 32px;
  line-height: 1.6;
}

.nf-path {
  background: rgba(163, 113, 247, 0.15);
  color: #a371f7;
  border-radius: 4px;
  padding: 2px 6px;
  font-family: 'JetBrains Mono', 'Courier New', monospace;
  font-size: 12px;
  word-break: break-all;
}

.nf-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}

.nf-btn {
  padding: 11px 22px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1px;
  cursor: pointer;
  font-family: inherit;
  min-height: 44px;
  transition: all 0.2s;
}

.nf-btn:active {
  transform: scale(0.97);
}

.nf-btn-primary {
  background: linear-gradient(135deg, #a371f7, #7b59d0);
  border: none;
  color: #fff;
}

.nf-btn-primary:hover {
  box-shadow: 0 4px 20px rgba(163, 113, 247, 0.5);
}

.nf-btn-secondary {
  background: transparent;
  border: 1px solid #30363d;
  color: #8b949e;
}

.nf-btn-secondary:hover {
  border-color: #8b949e;
  color: #e6edf3;
}
</style>
