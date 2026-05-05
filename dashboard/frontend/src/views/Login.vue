<template>
  <div class="login-page">
    <div class="login-container">
      <div class="login-header">
        <div class="brand">
          <div class="logo"></div>
          <h1>ALGOVIBE</h1>
        </div>
        <p class="subtitle">QUANTITATIVE TRADING SYSTEMS</p>
      </div>

      <form @submit.prevent="handleLogin" class="login-form">
        <div class="form-group">
          <label for="username">OPERATOR ID</label>
          <input 
            type="text" 
            id="username" 
            v-model="username" 
            placeholder="Enter ID" 
            required
            autocomplete="username"
          />
        </div>

        <div class="form-group">
          <label for="password">ACCESS KEY</label>
          <input 
            type="password" 
            id="password" 
            v-model="password" 
            placeholder="••••••••" 
            required
            autocomplete="current-password"
          />
        </div>

        <div v-if="error" class="error-msg">
          <span class="icon">⚠️</span> {{ error }}
        </div>

        <button type="submit" class="submit-btn" :disabled="loading">
          <span v-if="loading" class="loader"></span>
          <span v-else>AUTHENTICATE →</span>
        </button>
      </form>

      <div class="login-footer">
        <p>SECURE ACCESS ONLY &nbsp;·&nbsp; v1.4.2-STABLE</p>
      </div>
    </div>
    
    <div class="ambient-glow"></div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'

const router = useRouter()
const { login } = useAuth()

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

async function handleLogin() {
  if (!username.value || !password.value) return
  
  loading.value = true
  error.value = ''
  
  try {
    await login(username.value, password.value, backendUrl)
    // After login, redirect to mode selector
    router.push('/mode-select')
  } catch (err) {
    error.value = err.message || 'Authentication failed'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #020308;
  position: relative;
  overflow: hidden;
  font-family: 'Inter', system-ui, sans-serif;
  color: #fff;
}

.ambient-glow {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 100vw;
  height: 100vh;
  background: radial-gradient(circle at center, rgba(63, 81, 181, 0.08) 0%, transparent 70%);
  pointer-events: none;
  z-index: 1;
}

.login-container {
  width: 100%;
  max-width: 400px;
  padding: 40px;
  background: rgba(10, 12, 20, 0.8);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 24px;
  z-index: 10;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
  animation: fadeIn 0.8s ease-out;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

.login-header {
  text-align: center;
  margin-bottom: 40px;
}

.brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.logo {
  width: 48px;
  height: 48px;
  background: linear-gradient(135deg, #6366f1, #a855f7);
  border-radius: 12px;
  position: relative;
}

.logo::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 20px;
  height: 20px;
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 0 15px #fff;
}

h1 {
  font-size: 24px;
  font-weight: 800;
  letter-spacing: 4px;
  margin: 0;
  background: linear-gradient(to right, #fff, #888);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.subtitle {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 2px;
  color: #555;
  margin-top: 8px;
}

.form-group {
  margin-bottom: 24px;
}

label {
  display: block;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.5px;
  color: #444;
  margin-bottom: 10px;
}

input {
  width: 100%;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  padding: 14px 16px;
  color: #fff;
  font-size: 15px;
  transition: all 0.3s ease;
  box-sizing: border-box;
}

input:focus {
  outline: none;
  border-color: #6366f1;
  background: rgba(99, 102, 241, 0.05);
  box-shadow: 0 0 20px rgba(99, 102, 241, 0.1);
}

.error-msg {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.2);
  color: #ef4444;
  padding: 12px;
  border-radius: 12px;
  font-size: 13px;
  margin-bottom: 24px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.submit-btn {
  width: 100%;
  background: #fff;
  color: #000;
  border: none;
  border-radius: 12px;
  padding: 16px;
  font-size: 14px;
  font-weight: 800;
  letter-spacing: 1px;
  cursor: pointer;
  transition: all 0.3s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.submit-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 20px rgba(255, 255, 255, 0.1);
}

.submit-btn:active {
  transform: translateY(0);
}

.submit-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.loader {
  width: 20px;
  height: 20px;
  border: 2px solid #000;
  border-bottom-color: transparent;
  border-radius: 50%;
  display: inline-block;
  animation: rotation 1s linear infinite;
}

@keyframes rotation {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.login-footer {
  margin-top: 32px;
  text-align: center;
}

.login-footer p {
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 1px;
  color: #333;
}
</style>
