import { ref, computed } from 'vue'

const _MODE_KEY = 'algovibe_mode'

// 'dry' | 'live' | null (not yet selected)
const selectedMode = ref(localStorage.getItem(_MODE_KEY) || null)

const isDryMode = computed(() => selectedMode.value === 'dry')
const isLiveMode = computed(() => selectedMode.value === 'live')
const hasModeSelected = computed(() => selectedMode.value !== null)

// API base URL for the currently-selected mode
const apiBase = computed(() =>
  selectedMode.value === 'live' ? '/api/live' : '/api/dry'
)

function selectMode(mode) {
  if (mode !== 'dry' && mode !== 'live') return
  selectedMode.value = mode
  localStorage.setItem(_MODE_KEY, mode)
}

function clearMode() {
  selectedMode.value = null
  localStorage.removeItem(_MODE_KEY)
}

export function useMode() {
  return {
    selectedMode,
    isDryMode,
    isLiveMode,
    hasModeSelected,
    apiBase,
    selectMode,
    clearMode,
  }
}
