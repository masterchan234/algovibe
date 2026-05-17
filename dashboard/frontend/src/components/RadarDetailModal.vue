<template>
  <Teleport to="body">
    <div class="rdm-overlay" @click.self="onClose">
      <div class="rdm-panel">

        <!-- Header -->
        <div class="rdm-header">
          <div class="rdm-title-row">
            <span class="rdm-pair-name">{{ formatPair(pair?.pair) }}</span>
            <span v-if="isLeader" class="rdm-crown">👑</span>
            <span class="rdm-signal-badge" :class="signalBadgeClass">{{ recommendation }}</span>
          </div>
          <button class="rdm-close" @click="onClose">✕</button>
        </div>

        <!-- Score bar -->
        <div class="rdm-score-row">
          <span class="rdm-label">Score</span>
          <div class="rdm-bar-wrap">
            <div class="rdm-bar-fill" :style="{ width: scoreWidth, background: scoreBarColor }"></div>
          </div>
          <span class="rdm-score-val">{{ (pair?.score || 0).toFixed(0) }}</span>
        </div>

        <!-- Stats grid -->
        <div class="rdm-stats">
          <div class="rdm-stat">
            <span class="rdm-stat-label">Prediksi</span>
            <span class="rdm-stat-val rdm-prob">{{ pair?.prediction != null ? ((pair.prediction * 100).toFixed(0) + '%') : 'N/A' }}</span>
          </div>
          <div class="rdm-stat">
            <span class="rdm-stat-label">Trend</span>
            <span class="rdm-stat-val" :class="trendClass">{{ pair?.trend || 'N/A' }}</span>
          </div>
          <div class="rdm-stat">
            <span class="rdm-stat-label">Status</span>
            <span class="rdm-stat-val" :class="pair?.tradable ? 'rdm-ready' : 'rdm-blocked'">
              {{ pair?.tradable ? 'TRADABLE' : 'BLOCKED' }}
            </span>
          </div>
          <div class="rdm-stat" v-if="pair?.vol_score != null">
            <span class="rdm-stat-label">Vol Score</span>
            <span class="rdm-stat-val">{{ pair.vol_score }}</span>
          </div>
          <div class="rdm-stat" v-if="pair?.last_price != null">
            <span class="rdm-stat-label">Harga</span>
            <span class="rdm-stat-val">{{ formatPrice(pair.last_price) }}</span>
          </div>
          <div class="rdm-stat" v-if="pair?.change_24h != null">
            <span class="rdm-stat-label">24h</span>
            <span class="rdm-stat-val" :class="pair.change_24h >= 0 ? 'rdm-up' : 'rdm-dn'">
              {{ pair.change_24h >= 0 ? '+' : '' }}{{ Number(pair.change_24h).toFixed(2) }}%
            </span>
          </div>
        </div>

        <!-- AI Veto block -->
        <div class="rdm-veto-block" v-if="pair?.ai_veto">
          <span class="rdm-veto-chip">AI VETO {{ pair.ai_veto_confidence || 0 }}%</span>
          <span class="rdm-veto-reason">{{ pair.ai_veto_reason || 'Diveto oleh AI' }}</span>
        </div>

        <!-- Status / reason text -->
        <div class="rdm-status-text">
          <span class="rdm-status-icon" :class="pair?.tradable ? 'rdm-ready' : 'rdm-blocked'">
            {{ pair?.tradable ? '✓' : '✗' }}
          </span>
          {{ fullStatus }}
        </div>

        <!-- Footer -->
        <div class="rdm-footer">
          <button class="rdm-btn-cancel" @click="onClose">Tutup</button>
          <button class="rdm-btn-ai" @click="onDeepAnalysis">
            🤖 AI Deep Analysis
          </button>
        </div>

      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed } from 'vue'
import { useTradeStore } from '../stores/trade'

const store = useTradeStore()

const props = defineProps({
  pair: { type: Object, default: null },
  show: { type: Boolean, default: false },
  regimeLeader: { type: String, default: '' },
  onClose: { type: Function, required: true },
  onDeepAnalysis: { type: Function, required: true },
})

const isLeader = computed(() =>
  props.pair?.pair && props.regimeLeader &&
  props.pair.pair.toUpperCase() === props.regimeLeader.toUpperCase()
)

function formatPair(pair) {
  if (!pair) return ''
  return pair.replace(/_idr$/i, '').replace(/_usdt$/i, '').replace(/idr$/i, '').replace(/usdt$/i, '').toUpperCase()
}

function formatPrice(v) {
  if (v == null) return '-'
  const n = Number(v)
  if (isNaN(n)) return '-'
  
  const isUsdt = store.stats.exchange === 'binance' || (store.stats.btc_price && store.stats.btc_price < 500000)
  if (isUsdt) {
    if (n >= 1_000_000) return '$' + (n / 1_000_000).toFixed(2) + 'M'
    if (n >= 1_000) return '$' + n.toLocaleString('en-US')
    return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
  } else {
    if (n >= 1_000_000) return 'Rp ' + (n / 1_000_000).toFixed(2) + 'M'
    if (n >= 1_000) return 'Rp ' + n.toLocaleString('id-ID')
    return 'Rp ' + n.toFixed(4)
  }
}

const recommendation = computed(() => {
  const r = String(props.pair?.radar_reco || '').toUpperCase()
  if (r === 'BUY' || r === 'SELL' || r === 'HOLD') return r
  const s = String(props.pair?.signal || '').toUpperCase()
  if (s.includes('BUY')) return 'BUY'
  if (s.includes('SELL')) return 'SELL'
  const sc = Number(props.pair?.score || 0)
  const pr = Number(props.pair?.prediction || 0)
  const tr = String(props.pair?.trend || '').toUpperCase()
  if (sc >= 70 && pr >= 0.55) return 'BUY'
  if (sc <= 30 && pr <= 0.45) return 'SELL'
  if ((tr.includes('BULL') || tr.includes('UP')) && sc >= 60) return 'BUY'
  if ((tr.includes('BEAR') || tr.includes('DOWN')) && sc <= 40) return 'SELL'
  return 'HOLD'
})

const signalBadgeClass = computed(() => {
  if (recommendation.value === 'BUY') return 'rdm-sig-buy'
  if (recommendation.value === 'SELL') return 'rdm-sig-sell'
  return 'rdm-sig-hold'
})

const scoreWidth = computed(() => (props.pair?.score || 0) + '%')
const scoreBarColor = computed(() => {
  const s = Number(props.pair?.score || 0)
  if (s >= 75) return '#3fb950'
  if (s >= 55) return '#d29922'
  return '#f85149'
})

const trendClass = computed(() => {
  const t = String(props.pair?.trend || '').toUpperCase()
  if (t.includes('UP') || t.includes('BULL')) return 'rdm-up'
  if (t.includes('DOWN') || t.includes('BEAR')) return 'rdm-dn'
  return 'rdm-neutral'
})

const fullStatus = computed(() => {
  if (props.pair?.ai_veto) {
    return String(props.pair?.ai_veto_reason || props.pair?.tradable_reason || 'AI memveto entry ini')
  }
  if (props.pair?.tradable === false) {
    return String(props.pair?.tradable_reason || props.pair?.gate_status || 'Belum memenuhi gate entry')
  }
  return String(props.pair?.radar_reason || 'Ready untuk eksekusi jika gate lain lolos')
})
</script>

<style scoped>
.rdm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.65);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9000;
  backdrop-filter: blur(2px);
}

.rdm-panel {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 12px;
  padding: 20px 24px 16px;
  width: 360px;
  max-width: 92vw;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(163, 113, 247, 0.12);
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.rdm-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.rdm-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rdm-pair-name {
  font-size: 22px;
  font-weight: 900;
  color: #e6edf3;
  letter-spacing: 0.5px;
}

.rdm-crown { font-size: 18px; filter: drop-shadow(0 0 4px gold); }

.rdm-signal-badge {
  font-size: 11px;
  font-weight: 900;
  padding: 3px 10px;
  border-radius: 999px;
  letter-spacing: 0.5px;
  border: 1px solid transparent;
}
.rdm-sig-buy  { background: rgba(63, 185, 80, 0.15); color: #3fb950; border-color: rgba(63, 185, 80, 0.4); }
.rdm-sig-sell { background: rgba(248, 81, 73, 0.15);  color: #f85149; border-color: rgba(248, 81, 73, 0.4); }
.rdm-sig-hold { background: rgba(210, 153, 34, 0.15); color: #d29922; border-color: rgba(210, 153, 34, 0.35); }

.rdm-close {
  background: none;
  border: none;
  color: #8b949e;
  cursor: pointer;
  font-size: 16px;
  padding: 0 4px;
  line-height: 1;
}
.rdm-close:hover { color: #e6edf3; }

/* Score bar */
.rdm-score-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rdm-label {
  font-size: 11px;
  color: #8b949e;
  min-width: 40px;
}
.rdm-bar-wrap {
  flex: 1;
  height: 6px;
  background: #21262d;
  border-radius: 3px;
  overflow: hidden;
}
.rdm-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s ease;
}
.rdm-score-val {
  font-size: 13px;
  font-weight: 900;
  color: #e6edf3;
  min-width: 28px;
  text-align: right;
}

/* Stats grid */
.rdm-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px 12px;
  background: #0d1117;
  border-radius: 8px;
  padding: 12px;
}
.rdm-stat {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.rdm-stat-label {
  font-size: 10px;
  color: #8b949e;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}
.rdm-stat-val {
  font-size: 12px;
  font-weight: 700;
  color: #e6edf3;
}
.rdm-prob { color: #a371f7; }
.rdm-up   { color: #3fb950; }
.rdm-dn   { color: #f85149; }
.rdm-neutral { color: #8b949e; }
.rdm-ready   { color: #3fb950; }
.rdm-blocked { color: #f85149; }

/* Veto */
.rdm-veto-block {
  background: rgba(248, 81, 73, 0.08);
  border: 1px solid rgba(248, 81, 73, 0.3);
  border-radius: 8px;
  padding: 8px 12px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.rdm-veto-chip {
  font-size: 9px;
  font-weight: 900;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(248, 81, 73, 0.2);
  color: #f85149;
  border: 1px solid rgba(248, 81, 73, 0.4);
  white-space: nowrap;
  flex-shrink: 0;
}
.rdm-veto-reason {
  font-size: 11px;
  color: #f85149;
  line-height: 1.4;
}

/* Status text */
.rdm-status-text {
  font-size: 11px;
  color: #8b949e;
  line-height: 1.5;
  display: flex;
  gap: 6px;
  align-items: flex-start;
}
.rdm-status-icon {
  font-size: 12px;
  font-weight: 900;
  flex-shrink: 0;
  margin-top: 1px;
}

/* Footer */
.rdm-footer {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 2px;
}
.rdm-btn-cancel {
  background: #21262d;
  border: 1px solid #30363d;
  color: #8b949e;
  padding: 7px 16px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
}
.rdm-btn-cancel:hover { background: #30363d; color: #e6edf3; }
.rdm-btn-ai {
  background: linear-gradient(135deg, #a371f7, #7928ca);
  border: none;
  color: #fff;
  padding: 7px 18px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.3px;
  box-shadow: 0 4px 12px rgba(163, 113, 247, 0.35);
}
.rdm-btn-ai:hover {
  background: linear-gradient(135deg, #b589ff, #8b2fd9);
  box-shadow: 0 6px 18px rgba(163, 113, 247, 0.5);
}
</style>
