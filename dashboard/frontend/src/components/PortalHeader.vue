<template>
  <header class="portal-header">
    <div class="header-container">
      <div class="portal-branding" @click="goHome" style="cursor: pointer;">
        <p class="brand-kicker">ALGORITHMIC JOURNAL</p>
        <h1>ALGOVIBE <span class="sub">INTELLIGENCE</span></h1>
      </div>
      <nav class="portal-nav" aria-label="Primary Navigation">
        <router-link to="/about-us">About Us</router-link>
        <router-link to="/backtest-report">Backtest Report</router-link>
        <a href="/login#hero-2" @click.prevent="goToSection('hero-2')">Featured Story</a>
        <a href="/login#articles" @click.prevent="goToSection('articles')">Articles</a>
        <a href="/login#signals" @click.prevent="goToSection('signals')">Live Signals</a>
        <button @click="handleLogin" class="login-trigger">login</button>
      </nav>
    </div>
  </header>
</template>

<script setup>
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()
const emit = defineEmits(['open-login'])

function goHome() {
  router.push({ name: 'login' }).catch(() => {})
}

function goToSection(id) {
  router.push({ name: 'login', hash: `#${id}` }).catch(() => {})
}

function handleLogin() {
  if (route.name !== 'login') {
    router.push({ name: 'login', query: { showAuth: 'true' } }).catch(() => {})
  } else {
    emit('open-login')
  }
}
</script>

<style scoped>
.portal-header {
  border-bottom: 1px solid rgba(133, 168, 192, 0.2);
  background: rgba(8, 15, 24, 0.9);
  backdrop-filter: blur(8px);
  position: sticky;
  top: 0;
  z-index: 100;
}
.header-container {
  max-width: 1160px;
  margin: 0 auto;
  width: 100%;
  padding: 0 28px;
  min-height: 84px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.brand-kicker {
  margin: 0 0 4px;
  font-size: 10px;
  letter-spacing: 2px;
  color: #7ea3c1;
  font-weight: 700;
}
.portal-branding h1 {
  font-size: 20px;
  font-weight: 900;
  letter-spacing: 3px;
  color: #f4f8fb;
  margin: 0;
}
.portal-branding h1 .sub {
  color: #5dc6e7;
  font-weight: 500;
  font-size: 13px;
}
.portal-nav {
  display: flex;
  align-items: center;
  gap: 18px;
}
.portal-nav a {
  color: #b6c8d7;
  font-size: 11px;
  text-decoration: none;
  font-weight: 700;
  letter-spacing: 0.6px;
  transition: color 0.2s ease;
}
.portal-nav a:hover,
.portal-nav a.router-link-active {
  color: #5dc6e7;
}
.login-trigger {
  background: linear-gradient(120deg, #79d1ee, #5fc4a4);
  color: #051018;
  border: 1px solid rgba(178, 236, 255, 0.35);
  padding: 8px 18px;
  font-size: 11px;
  font-weight: 800;
  border-radius: 999px;
  cursor: pointer;
  transition: transform 0.25s ease, box-shadow 0.25s ease;
}
.login-trigger:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 18px rgba(80, 177, 214, 0.35);
}
</style>
