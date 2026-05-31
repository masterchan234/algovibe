import re

with open('dashboard/frontend/src/components/SignalRadarPanel.vue', 'r') as f:
    radar = f.read()

# Add the template part
btc_bar_html = """
    <!-- BTC Condition Bar -->
    <div class="btc-condition-bar" :class="btcBarClass" v-if="stats && stats.btc_price">
      <div class="btc-bar-left">
        <span class="btc-icon">₿</span>
        <span class="btc-label">BTC/{{ (stats.btc_price && stats.btc_price < 500000) ? 'USDT' : 'IDR' }}</span>
        <span class="btc-price">{{ formatPrice(stats.btc_price) }}</span>
      </div>
      <div class="btc-bar-mid">
        <span class="btc-delta" :class="stats.btc_ch_5m >= 0 ? 'delta-up' : 'delta-dn'">
          5m {{ stats.btc_ch_5m >= 0 ? '+' : '' }}{{ (stats.btc_ch_5m || 0).toFixed(2) }}%
        </span>
        <span class="btc-delta" :class="stats.btc_ch_1h >= 0 ? 'delta-up' : 'delta-dn'">
          1h {{ stats.btc_ch_1h >= 0 ? '+' : '' }}{{ (stats.btc_ch_1h || 0).toFixed(2) }}%
        </span>
        <span class="btc-delta" :class="stats.btc_change_24h >= 0 ? 'delta-up' : 'delta-dn'">
          24h {{ stats.btc_change_24h >= 0 ? '+' : '' }}{{ (stats.btc_change_24h || 0).toFixed(2) }}%
        </span>
        <span class="btc-trend-chip" :class="btcTrendChipClass">{{ stats.btc_trend }}</span>
      </div>
      <div class="btc-bar-right">
        <span class="btc-status-badge" :class="btcStatusBadgeClass">{{ btcStatusLabel }}</span>
        <span class="btc-score-val">S{{ (stats.btc_score || 0).toFixed(0) }}</span>
      </div>
    </div>
"""

# Inject after panel-header
radar = radar.replace('    <!-- Segmented Control -->', btc_bar_html + '\n    <!-- Segmented Control -->')


# Add the script part
btc_script = """
const stats = computed(() => store.stats)

const btcStatusLabel = computed(() => {
  if (store.stats.btc_crash) return '🔴 CRASH'
  if (store.stats.btc_weakening) return '🟡 WEAK'
  return '🟢 STRONG'
})

const btcBarClass = computed(() => {
  if (store.stats.btc_crash) return 'btc-bar-crash'
  if (store.stats.btc_weakening) return 'btc-bar-weak'
  return 'btc-bar-strong'
})

const btcStatusBadgeClass = computed(() => {
  if (store.stats.btc_crash) return 'badge-crash'
  if (store.stats.btc_weakening) return 'badge-weak'
  return 'badge-strong'
})

const btcTrendChipClass = computed(() => {
  const t = (store.stats.btc_trend || '').toUpperCase()
  if (t === 'UP' || t === 'BULLISH') return 'chip-up'
  if (t === 'DOWN' || t === 'BEARISH') return 'chip-dn'
  return 'chip-neutral'
})
"""

# Inject before function getTfValue
radar = radar.replace('const getTfValue =', btc_script + '\nconst getTfValue =')

with open('dashboard/frontend/src/components/SignalRadarPanel.vue', 'w') as f:
    f.write(radar)

print("SignalRadarPanel updated")
