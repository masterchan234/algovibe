<template>
  <div class="main-chart-panel">
    <div class="panel-header">
      <div class="header-left">
        <h3>Equity Curve & Market Status</h3>
      </div>
      <div class="header-right">
        <!-- Future: Candlestick / PnL toggle -->
        <span class="view-toggle active">PnL View</span>
      </div>
    </div>
    
    <div class="panel-content">
      <ProfitChart />
    </div>
  </div>
</template>

<script setup>
import ProfitChart from './ProfitChart.vue'

const props = defineProps({
  isLive: { type: Boolean, required: true }
})
</script>

<style scoped>
.main-chart-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.panel-header {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-trader);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.panel-header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
  font-family: var(--font-mono);
}

.view-toggle {
  font-size: 11px;
  color: var(--accent-purple);
  background: rgba(139, 92, 246, 0.1);
  padding: 4px 8px;
  border-radius: 4px;
  font-weight: 600;
}

.panel-content {
  flex: 1;
  min-height: 0;
  position: relative;
}
</style>
EOF
