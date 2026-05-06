import { ref, computed } from 'vue'

const _TOKEN_KEY = 'algovibe_jwt'

const token = ref(localStorage.getItem(_TOKEN_KEY) || null)
const userInfo = ref(null)

function _parseToken(t) {
  if (!t) return null
  try {
    const parts = t.split('.')
    if (parts.length !== 3) return null
    const payload = JSON.parse(atob(parts[1]))
    // Check expiry
    if (payload.exp && Date.now() / 1000 > payload.exp) {
      return null
    }
    return payload
  } catch {
    return null
  }
}

// Initialise userInfo from stored token on module load
const _initial = _parseToken(token.value)
if (_initial) {
  userInfo.value = _initial
} else {
  // Token was invalid or expired — clear storage
  localStorage.removeItem(_TOKEN_KEY)
  token.value = null
}

const isAuthenticated = computed(() => {
  if (!token.value) return false
  return _parseToken(token.value) !== null
})

/** Return the session_id embedded in the current JWT, or null. */
const sessionId = computed(() => {
  const payload = _parseToken(token.value)
  return payload?.session_id || null
})

function getAuthHeaders() {
  if (!token.value) return {}
  return { Authorization: `Bearer ${token.value}` }
}

async function login(username, password, backendUrl = '') {
  const csrfToken = _getCookie('csrf_token')
  const res = await fetch(`${backendUrl}/api/login`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken,
    },
    body: JSON.stringify({ username, password }),
    credentials: 'include',
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.message || 'Login failed')
  }
  const data = await res.json()
  if (data.token) {
    token.value = data.token
    localStorage.setItem(_TOKEN_KEY, data.token)
    userInfo.value = _parseToken(data.token)
  }
  return data
}

async function logout(backendUrl = '') {
  try {
    await fetch(`${backendUrl}/api/logout`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': _getCookie('csrf_token'),
        ...getAuthHeaders(),
      },
      credentials: 'include',
    })
  } catch { /* ignore network errors on logout */ }
  token.value = null
  userInfo.value = null
  localStorage.removeItem(_TOKEN_KEY)
}

async function refreshToken(backendUrl = '') {
  if (!token.value) return false
  try {
    const res = await fetch(`${backendUrl}/api/refresh-token`, {
      method: 'POST',
      headers: getAuthHeaders(),
      credentials: 'include',
    })
    if (!res.ok) return false
    const data = await res.json()
    if (data.token) {
      token.value = data.token
      localStorage.setItem(_TOKEN_KEY, data.token)
      userInfo.value = _parseToken(data.token)
      return true
    }
  } catch { /* ignore */ }
  return false
}

function _getCookie(name) {
  const value = `; ${document.cookie}`
  const parts = value.split(`; ${name}=`)
  if (parts.length === 2) return parts.pop().split(';').shift()
  return ''
}

export function useAuth() {
  return {
    token,
    userInfo,
    sessionId,
    isAuthenticated,
    getAuthHeaders,
    login,
    logout,
    refreshToken,
  }
}
