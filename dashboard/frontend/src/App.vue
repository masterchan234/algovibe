<template>
  <div class="app">
    <header class="app-header">
      <div class="brand">
        <span class="brand-dot"></span>
        AlgoVibe
      </div>
      <div class="header-center">
        <span v-if="store.stats.regime_leader" class="header-leader">
          Leader: <b>{{ store.stats.regime_leader }}</b>
        </span>
      </div>
      <div class="header-right">
        <span class="conn-dot" :class="store.connected ? 'conn-live' : 'conn-dead'"></span>
        {{ store.connected ? 'Connected' : 'Connecting…' }}
      </div>
    </header>

    <main class="main-grid">
      <section class="left-panel">
        <ActivityFeed />
      </section>
      <section class="right-panel">
        <ProfitChart />
      </section>
    </main>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useTradeStore } from './stores/trade'
import ActivityFeed from './components/ActivityFeed.vue'
import ProfitChart from './components/ProfitChart.vue'

const store = useTradeStore()

onMounted(() => {
  store.connect()
})
</script>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: #0d1117;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  height: 44px;
  background: #161b22;
  border-bottom: 1px solid #21262d;
  flex-shrink: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 15px;
  color: #58a6ff;
  letter-spacing: 0.5px;
}

.brand-dot {
  width: 8px;
  height: 8px;
  background: #3fb950;
  border-radius: 50%;
  box-shadow: 0 0 6px #3fb950;
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.header-center {
  font-size: 12px;
  color: #8b949e;
}
.header-leader b { color: #e6edf3; }

.header-right {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #8b949e;
}

.conn-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.conn-live { background: #3fb950; }
.conn-dead { background: #8b949e; }

.main-grid {
  display: grid;
  grid-template-columns: 42% 1fr;
  gap: 8px;
  padding: 8px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.left-panel,
.right-panel {
  min-height: 0;
  overflow: hidden;
}
</style>
