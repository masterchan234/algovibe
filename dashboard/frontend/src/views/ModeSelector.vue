<template>
  <div class="mode-selector" :class="hoveredMode">
    <!-- Header -->
    <div class="ms-header">
      <div class="ms-brand">
        <span class="ms-dot"></span>
        <div class="ms-brand-text">
          <span class="ms-title">ALGOVIBE</span>
          <span class="ms-sub">SELECT OPERATING MODE</span>
        </div>
      </div>
      <div class="ms-user">
        <span class="ms-user-name">{{ userInfo?.username || 'admin' }}</span>
        <button class="ms-logout-btn" @click="handleLogout">LOGOUT</button>
      </div>
    </div>

    <!-- Mode Cards -->
    <div class="ms-cards">
      <!-- Simulation Lab (Dry Run) -->
      <div
        class="ms-card ms-card--dry"
        :class="{ 'is-hovered': hoveredMode === 'dry' }"
        @mouseenter="hoveredMode = 'dry'"
        @mouseleave="hoveredMode = ''"
        @click="enter('dry')"
        role="button"
        tabindex="0"
        @keydown.enter="enter('dry')"
        aria-label="Enter Simulation Lab"
      >
        <div class="ms-card-glow ms-glow-purple"></div>
        <div class="ms-card-icon">🧪</div>
        <div class="ms-card-badge ms-badge-dry">SIMULATION</div>
        <h2 class="ms-card-title">SIMULATION LAB</h2>
        <p class="ms-card-desc">
          Zero-capital risk environment. All strategies execute virtually against
          real-time market data. Perfect for testing, calibration, and learning.
        </p>
        <ul class="ms-feature-list">
          <li><span class="ms-check">✓</span> Real market data, virtual execution</li>
          <li><span class="ms-check">✓</span> Unlimited strategy testing</li>
          <li><span class="ms-check">✓</span> Zero capital at risk</li>
          <li><span class="ms-check">✓</span> Tech-Purple analytics theme</li>
        </ul>
        <div class="ms-card-footer">
          <span class="ms-footer-db">DB: algotrade_dryrun.db</span>
          <button class="ms-enter-btn ms-enter-dry">ENTER LAB →</button>
        </div>
      </div>

      <!-- Separator -->
      <div class="ms-separator">
        <div class="ms-sep-line"></div>
        <span class="ms-sep-vs">VS</span>
        <div class="ms-sep-line"></div>
      </div>

      <!-- Live Trading Floor -->
      <div
        class="ms-card ms-card--live"
        :class="{ 'is-hovered': hoveredMode === 'live' }"
        @mouseenter="hoveredMode = 'live'"
        @mouseleave="hoveredMode = ''"
        @click="enter('live')"
        role="button"
        tabindex="0"
        @keydown.enter="enter('live')"
        aria-label="Enter Live Trading Floor"
      >
        <div class="ms-card-glow ms-glow-gold"></div>
        <div class="ms-card-icon">⚡</div>
        <div class="ms-card-badge ms-badge-live">
          <span class="ms-live-dot"></span>LIVE
        </div>
        <h2 class="ms-card-title">LIVE TRADING FLOOR</h2>
        <p class="ms-card-desc">
          Real capital execution on Indodax exchange. Every order is live.
          High-stakes environment — proceed with discipline.
        </p>
        <ul class="ms-feature-list">
          <li><span class="ms-check-gold">✓</span> Real money, real execution</li>
          <li><span class="ms-check-gold">✓</span> Obsidian &amp; Gold command theme</li>
          <li><span class="ms-check-gold">✓</span> Hold-to-confirm safety locks</li>
          <li><span class="ms-check-gold">✓</span> Circuit breaker protection</li>
        </ul>
        <div class="ms-card-footer">
          <span class="ms-footer-db">DB: algotrade_live.db</span>
          <button class="ms-enter-btn ms-enter-live">ENTER FLOOR →</button>
        </div>
      </div>
    </div>

    <!-- Footer -->
    <div class="ms-footer-bar">
      ALGOVIBE v1.4.2-STABLE &nbsp;·&nbsp; All strategies are identical across modes &nbsp;·&nbsp; {{ new Date().toLocaleTimeString('id-ID') }}
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useMode } from '../composables/useMode'

const router = useRouter()
const { userInfo, logout } = useAuth()
const { selectMode } = useMode()

const hoveredMode = ref('')

async function enter(mode) {
  try {
    await selectMode(mode, backendUrl)
    if (mode === 'live') {
      router.push('/dashboard-live')
    } else {
      router.push('/dashboard-dry')
    }
  } catch (err) {
    console.error('Failed to enter mode:', err)
    alert(err.message || 'Gagal masuk ke mode ini. Pastikan Anda sudah login.')
  }
}

const backendUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080')
  : window.location.origin

async function handleLogout() {
  await logout(backendUrl)
  router.push('/')
}
</script>

<style scoped>
/* ── Root ──────────────────────────────────────────────────────── */
.mode-selector {
  min-height: 100vh;
  background: #060810;
  display: flex;
  flex-direction: column;
  transition: background 0.6s ease;
  font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
}

.mode-selector.dry  { background: #08060f; }
.mode-selector.live { background: #0d0900; }

/* ── Header ────────────────────────────────────────────────────── */
.ms-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 32px;
  background: rgba(255,255,255,0.02);
  border-bottom: 1px solid #1e1e2e;
}

.ms-brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.ms-dot {
  width: 10px;
  height: 10px;
  background: #a371f7;
  border-radius: 50%;
  box-shadow: 0 0 12px #a371f7;
  animation: pulse-dot 2s ease-in-out infinite;
}

@keyframes pulse-dot {
  0%,100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.5; transform: scale(0.7); }
}

.ms-brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}

.ms-title {
  font-size: 18px;
  font-weight: 900;
  letter-spacing: 3px;
  background: linear-gradient(90deg, #a371f7, #58a6ff);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.ms-sub {
  font-size: 9px;
  letter-spacing: 2px;
  color: #555;
  font-weight: 600;
}

.ms-user {
  display: flex;
  align-items: center;
  gap: 14px;
}

.ms-user-name {
  font-size: 12px;
  color: #8b949e;
  letter-spacing: 1px;
}

.ms-logout-btn {
  background: transparent;
  border: 1px solid #333;
  color: #666;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 11px;
  letter-spacing: 1px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.2s;
  min-height: 32px;
}

.ms-logout-btn:hover {
  border-color: #f85149;
  color: #f85149;
}

/* ── Cards Container ────────────────────────────────────────────── */
.ms-cards {
  display: flex;
  align-items: stretch;
  justify-content: center;
  gap: 0;
  flex: 1;
  padding: 48px 32px;
  max-width: 1100px;
  margin: 0 auto;
  width: 100%;
}

/* ── Card Base ─────────────────────────────────────────────────── */
.ms-card {
  position: relative;
  flex: 1;
  max-width: 440px;
  background: #0d1117;
  border-radius: 16px;
  padding: 36px 32px;
  cursor: pointer;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 16px;
  transition: transform 0.3s cubic-bezier(.34,1.56,.64,1), box-shadow 0.3s, border-color 0.3s;
  border: 1px solid #1e1e2e;
}

.ms-card:focus-visible {
  outline: 2px solid #a371f7;
  outline-offset: 3px;
}

.ms-card--dry.is-hovered {
  transform: translateY(-6px) scale(1.01);
  border-color: #a371f7;
  box-shadow: 0 20px 60px rgba(163,113,247,0.25), 0 0 0 1px rgba(163,113,247,0.3);
}

.ms-card--live.is-hovered {
  transform: translateY(-6px) scale(1.01);
  border-color: #f5a623;
  box-shadow: 0 20px 60px rgba(245,166,35,0.2), 0 0 0 1px rgba(245,166,35,0.3);
}

/* Glow layer */
.ms-card-glow {
  position: absolute;
  inset: 0;
  opacity: 0;
  transition: opacity 0.4s;
  pointer-events: none;
  border-radius: inherit;
}

.ms-glow-purple { background: radial-gradient(circle at 30% 20%, rgba(163,113,247,0.12) 0%, transparent 60%); }
.ms-glow-gold   { background: radial-gradient(circle at 70% 20%, rgba(245,166,35,0.12) 0%, transparent 60%); }

.ms-card.is-hovered .ms-card-glow { opacity: 1; }

/* ── Card Content ──────────────────────────────────────────────── */
.ms-card-icon {
  font-size: 42px;
  line-height: 1;
}

.ms-card-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 2px;
  padding: 4px 12px;
  border-radius: 20px;
  align-self: flex-start;
}

.ms-badge-dry  { background: rgba(163,113,247,0.15); color: #a371f7; border: 1px solid rgba(163,113,247,0.3); }
.ms-badge-live { background: rgba(255,30,30,0.12);   color: #ff4444; border: 1px solid rgba(255,30,30,0.3); }

.ms-live-dot {
  width: 6px; height: 6px;
  background: #ff1e1e;
  border-radius: 50%;
  animation: pulse-red 1.2s ease-in-out infinite;
}

@keyframes pulse-red {
  0%,100% { opacity: 1; }
  50%      { opacity: 0.3; }
}

.ms-card-title {
  font-size: 22px;
  font-weight: 800;
  letter-spacing: 1.5px;
  color: #e6edf3;
  margin: 0;
}

.ms-card--dry  .ms-card-title { color: #c9b1ff; }
.ms-card--live .ms-card-title { color: #ffd700; }

.ms-card-desc {
  font-size: 13px;
  line-height: 1.6;
  color: #8b949e;
}

/* Feature List */
.ms-feature-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}

.ms-feature-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #8b949e;
}

.ms-check      { color: #a371f7; font-weight: 700; }
.ms-check-gold { color: #f5a623; font-weight: 700; }

/* Card Footer */
.ms-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 16px;
  border-top: 1px solid #21262d;
  margin-top: auto;
}

.ms-footer-db {
  font-size: 10px;
  color: #444;
  font-family: 'JetBrains Mono', monospace;
  letter-spacing: 0.5px;
}

.ms-enter-btn {
  padding: 10px 20px;
  border: none;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.2s;
  min-height: 44px;
  min-width: 120px;
}

.ms-enter-dry {
  background: rgba(163,113,247,0.15);
  color: #a371f7;
  border: 1px solid rgba(163,113,247,0.4);
}

.ms-card--dry.is-hovered .ms-enter-dry {
  background: #a371f7;
  color: #000;
  box-shadow: 0 4px 20px rgba(163,113,247,0.5);
}

.ms-enter-live {
  background: rgba(245,166,35,0.12);
  color: #f5a623;
  border: 1px solid rgba(245,166,35,0.4);
}

.ms-card--live.is-hovered .ms-enter-live {
  background: #f5a623;
  color: #000;
  box-shadow: 0 4px 20px rgba(245,166,35,0.5);
}

/* ── Separator ──────────────────────────────────────────────────── */
.ms-separator {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 0 24px;
  gap: 12px;
  flex-shrink: 0;
}

.ms-sep-line {
  width: 1px;
  flex: 1;
  background: linear-gradient(to bottom, transparent, #2a2a3a, transparent);
}

.ms-sep-vs {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 2px;
  color: #333;
}

/* ── Footer Bar ──────────────────────────────────────────────────── */
.ms-footer-bar {
  padding: 12px 32px;
  text-align: center;
  font-size: 10px;
  color: #333;
  letter-spacing: 1px;
  border-top: 1px solid #111;
}

/* ── Mobile ──────────────────────────────────────────────────────── */
@media (max-width: 768px) {
  .ms-cards {
    flex-direction: column;
    align-items: center;
    padding: 24px 16px;
    gap: 16px;
  }

  .ms-card { max-width: 100%; padding: 24px 20px; gap: 12px; }
  .ms-card-title { font-size: 18px; }
  .ms-card-icon { font-size: 32px; }

  .ms-separator {
    flex-direction: row;
    padding: 0;
    width: 100%;
    max-width: 320px;
  }

  .ms-sep-line {
    width: auto;
    height: 1px;
    flex: 1;
    background: linear-gradient(to right, transparent, #2a2a3a, transparent);
  }

  .ms-header { padding: 14px 16px; }
  .ms-title  { font-size: 15px; }
}
</style>
