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

// Apply theme class on module load to restore the theme after a page refresh
if (selectedMode.value === 'live') {
  document.body.classList.add('theme-live')
} else if (selectedMode.value === 'dry') {
  document.body.classList.add('theme-dry')
}

function _applyTheme(mode) {
  document.body.classList.remove('theme-live', 'theme-dry')
  if (mode === 'live') {
    document.body.classList.add('theme-live')
  } else if (mode === 'dry') {
    document.body.classList.add('theme-dry')
  }
}

function selectMode(mode) {
  if (mode !== 'dry' && mode !== 'live') return
  selectedMode.value = mode
  localStorage.setItem(_MODE_KEY, mode)
  // Inject theme class immediately so CSS transitions apply without a page reload
  _applyTheme(mode)
}

function clearMode() {
  selectedMode.value = null
  localStorage.removeItem(_MODE_KEY)
  document.body.classList.remove('theme-live', 'theme-dry')
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
