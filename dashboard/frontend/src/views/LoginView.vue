<template>
  <div class="login-page">
    <div class="login-card">
      <!-- Brand -->
      <div class="lp-brand">
        <span class="lp-dot"></span>
        <div class="lp-brand-text">
          <span class="lp-title">ALGOVIBE</span>
          <span class="lp-sub">QUANT EXECUTION SYSTEM</span>
        </div>
      </div>

      <h2 class="lp-heading">AUTHENTICATION REQUIRED</h2>
      <p class="lp-desc">Sign in to access the trading platform.</p>

      <!-- Error message -->
      <div v-if="errorMsg" class="lp-error" role="alert">
        ⚠️ {{ errorMsg }}
      </div>

      <form @submit.prevent="handleLogin" class="lp-form">
        <div class="lp-field">
          <label for="lp-username" class="lp-label">USERNAME</label>
          <input
            id="lp-username"
            v-model="username"
            type="text"
            class="lp-input"
            placeholder="admin"
            autocomplete="username"
            required
            :disabled="loading"
          />
        </div>

        <div class="lp-field">
          <label for="lp-password" class="lp-label">PASSWORD</label>
          <input
            id="lp-password"
            v-model="password"
            type="password"
            class="lp-input"
            placeholder="••••••••"
            autocomplete="current-password"
            required
            :disabled="loading"
          />
        </div>

        <button type="submit" class="lp-submit" :disabled="loading">
          <span v-if="loading" class="lp-spinner">⏳</span>
          {{ loading ? 'AUTHENTICATING...' : 'LOGIN' }}
        </button>
      </form>

      <div class="lp-footer-links">
        <button class="lp-link" @click="router.push('/')">← View Public Dashboard</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuth } from '../composables/useAuth'

const router = useRouter()
const route = useRoute()
const { login } = useAuth()

const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

async function handleLogin() {
  errorMsg.value = ''
  loading.value = true
  try {
    await login(username.value, password.value, backendUrl)
    // Redirect to originally requested page or mode-select
    const redirect = route.query.redirect
    if (redirect && typeof redirect === 'string' && redirect.startsWith('/')) {
      router.push(redirect)
    } else {
      router.push('/mode-select')
    }
  } catch (e) {
    errorMsg.value = e.message || 'Invalid credentials. Please try again.'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  background: #060810;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
  padding: 16px;
}

.login-card {
  width: min(420px, 100%);
  background: #0d1117;
  border: 1px solid #1e1e2e;
  border-radius: 16px;
  padding: 40px 36px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(163, 113, 247, 0.1);
}

/* Brand */
.lp-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 28px;
}

.lp-dot {
  width: 10px;
  height: 10px;
  background: #a371f7;
  border-radius: 50%;
  box-shadow: 0 0 12px #a371f7;
  animation: pulse-dot 2s ease-in-out infinite;
  flex-shrink: 0;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.5; transform: scale(0.7); }
}

.lp-brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}

.lp-title {
  font-size: 18px;
  font-weight: 900;
  letter-spacing: 3px;
  background: linear-gradient(90deg, #a371f7, #58a6ff);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.lp-sub {
  font-size: 9px;
  letter-spacing: 2px;
  color: #555;
  font-weight: 600;
}

/* Headings */
.lp-heading {
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 1.5px;
  color: #e6edf3;
  margin: 0 0 8px;
}

.lp-desc {
  font-size: 12px;
  color: #8b949e;
  margin: 0 0 24px;
}

/* Error */
.lp-error {
  background: rgba(248, 81, 73, 0.1);
  border: 1px solid rgba(248, 81, 73, 0.4);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12px;
  color: #f85149;
  margin-bottom: 16px;
}

/* Form */
.lp-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.lp-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lp-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.5px;
  color: #8b949e;
}

.lp-input {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  color: #e6edf3;
  font-size: 14px;
  padding: 10px 14px;
  font-family: inherit;
  transition: border-color 0.2s, box-shadow 0.2s;
  min-height: 44px;
  outline: none;
}

.lp-input::placeholder {
  color: #484f58;
}

.lp-input:focus {
  border-color: #a371f7;
  box-shadow: 0 0 0 3px rgba(163, 113, 247, 0.2);
}

.lp-input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.lp-submit {
  background: linear-gradient(135deg, #a371f7, #7b59d0);
  border: none;
  border-radius: 8px;
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 1.5px;
  padding: 13px;
  cursor: pointer;
  font-family: inherit;
  margin-top: 8px;
  min-height: 46px;
  transition: opacity 0.2s, transform 0.1s, box-shadow 0.2s;
}

.lp-submit:hover:not(:disabled) {
  box-shadow: 0 4px 20px rgba(163, 113, 247, 0.5);
}

.lp-submit:active:not(:disabled) {
  transform: scale(0.98);
}

.lp-submit:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.lp-spinner {
  margin-right: 6px;
}

/* Footer links */
.lp-footer-links {
  margin-top: 24px;
  text-align: center;
}

.lp-link {
  font-size: 12px;
  color: #555;
  text-decoration: none;
  background: none;
  border: none;
  cursor: pointer;
  font-family: inherit;
  padding: 0;
  transition: color 0.2s;
}

.lp-link:hover {
  color: #a371f7;
}
</style>
