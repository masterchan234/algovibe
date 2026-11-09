<template>
  <div class="execution-panel">
    <ActivityFeed />
  </div>
</template>

<script setup>
import ActivityFeed from './ActivityFeed.vue'

const props = defineProps({
  isLive: { type: Boolean, required: true }
})
</script>

<style scoped>
.execution-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-radius: 12px;
  overflow: hidden;
}
</style>

