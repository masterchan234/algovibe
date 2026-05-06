import { ref, computed } from 'vue'
import { useAuth } from './useAuth'

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

async function selectMode(mode, backendUrl = '') {
  if (mode !== 'dry' && mode !== 'live') return
  
  const { getAuthHeaders, logout } = useAuth()
  
  const res = await fetch(`${backendUrl}/api/bot/select-mode`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': _getCookie('csrf_token'),
      ...getAuthHeaders()
    },
    body: JSON.stringify({ mode: mode === 'dry' ? 'DRY' : 'LIVE' }),
    credentials: 'include'
  })
  
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.message || err.error || 'Failed to select mode')
  }
  
  const data = await res.json()
  
  selectedMode.value = mode
  localStorage.setItem(_MODE_KEY, mode)
  // Inject theme class immediately so CSS transitions apply without a page reload
  _applyTheme(mode)
  
  if (data.logged_out) {
    await logout()
  }
  
  return data
}

function clearMode() {
  selectedMode.value = null
  localStorage.removeItem(_MODE_KEY)
  document.body.classList.remove('theme-live', 'theme-dry')
}

function _getCookie(name) {
  const value = `; ${document.cookie}`
  const parts = value.split(`; ${name}=`)
  if (parts.length === 2) return parts.pop().split(';').shift()
  return ''
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
