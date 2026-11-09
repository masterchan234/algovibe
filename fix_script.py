import re

with open('dashboard/frontend/src/components/SignalRadarPanel.vue', 'r') as f:
    radar = f.read()

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

# Check if it was already injected (in case I missed it)
if 'const btcStatusLabel' not in radar:
    # Inject it before function getTfValue
    radar = radar.replace('function getTfValue', btc_script + '\nfunction getTfValue')
    with open('dashboard/frontend/src/components/SignalRadarPanel.vue', 'w') as f:
        f.write(radar)
    print("Script variables injected")
else:
    print("Already injected")
