import re

with open('dashboard/frontend/src/components/SignalRadarPanel.vue', 'r') as f:
    radar = f.read()

# Replace HTML
old_html_start = '<div class="btc-condition-bar"'
old_html_end = '<!-- Segmented Control -->'

new_html = """    <!-- BTC Condition Card -->
    <div class="btc-condition-card" :class="btcBarClass" >
      <div class="btc-card-header">
        <div class="btc-title">
          <span class="btc-icon">₿</span>
          <span class="btc-label">BTC/{{ (stats.btc_price && stats.btc_price < 500000) ? 'USDT' : 'IDR' }}</span>
        </div>
        <span class="btc-status-badge" :class="btcStatusBadgeClass">{{ btcStatusLabel }}</span>
      </div>
      
      <div class="btc-card-body">
        <div class="btc-price-wrapper">
          <span class="btc-price">{{ formatPrice(stats.btc_price) }}</span>
          <span class="btc-delta-main" :class="stats.btc_change_24h >= 0 ? 'delta-up' : 'delta-dn'">
            {{ stats.btc_change_24h >= 0 ? '+' : '' }}{{ (stats.btc_change_24h || 0).toFixed(2) }}%
          </span>
        </div>
        
        <div class="btc-metrics">
          <div class="metric-item">
            <span class="metric-label">5m</span>
            <span class="metric-val" :class="stats.btc_ch_5m >= 0 ? 'text-up' : 'text-dn'">
              {{ stats.btc_ch_5m >= 0 ? '+' : '' }}{{ (stats.btc_ch_5m || 0).toFixed(2) }}%
            </span>
          </div>
          <div class="metric-item">
            <span class="metric-label">1h</span>
            <span class="metric-val" :class="stats.btc_ch_1h >= 0 ? 'text-up' : 'text-dn'">
              {{ stats.btc_ch_1h >= 0 ? '+' : '' }}{{ (stats.btc_ch_1h || 0).toFixed(2) }}%
            </span>
          </div>
          <div class="metric-item">
            <span class="metric-label">Trend</span>
            <span class="btc-trend-chip" :class="btcTrendChipClass">{{ stats.btc_trend }}</span>
          </div>
          <div class="metric-item">
            <span class="metric-label">Score</span>
            <span class="btc-score-val">{{ (stats.btc_score || 0).toFixed(1) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Segmented Control -->"""

# Extract everything up to Segmented Control and replace
html_pattern = re.compile(r'<div class="btc-condition-bar".*?<!-- Segmented Control -->', re.DOTALL)
radar = html_pattern.sub(new_html, radar)


# Replace CSS
old_css_start = '.btc-condition-bar {'
old_css_end = '.radar-nav-wrapper {'

new_css = """/* BTC Condition Card (Premium UI) */
.btc-condition-card {
  margin: 12px 20px;
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.04);
  background: linear-gradient(145deg, rgba(20, 20, 25, 0.8), rgba(10, 10, 15, 0.9));
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3), inset 0 1px 1px rgba(255,255,255,0.03);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

/* Subtle glow at the top edge */
.btc-condition-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.1), transparent);
}

.btc-bar-strong {
  border-color: rgba(63, 185, 80, 0.2);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3), 0 0 15px rgba(63, 185, 80, 0.05);
}
.btc-bar-weak {
  border-color: rgba(210, 153, 34, 0.2);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3), 0 0 15px rgba(210, 153, 34, 0.05);
}
.btc-bar-crash {
  border-color: rgba(248, 81, 73, 0.3);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3), 0 0 15px rgba(248, 81, 73, 0.1);
  animation: btc-crash-pulse 2s ease-in-out infinite alternate;
}
@keyframes btc-crash-pulse {
  from { border-color: rgba(248, 81, 73, 0.2); box-shadow: 0 4px 12px rgba(0,0,0,0.3), 0 0 5px rgba(248,81,73,0.05); }
  to   { border-color: rgba(248, 81, 73, 0.6); box-shadow: 0 4px 12px rgba(0,0,0,0.3), 0 0 20px rgba(248,81,73,0.15); }
}

.btc-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.btc-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.btc-icon {
  font-size: 16px;
  color: #f0a500;
  text-shadow: 0 0 8px rgba(240, 165, 0, 0.4);
}
.btc-label {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.btc-status-badge {
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.8px;
  text-transform: uppercase;
  box-shadow: inset 0 1px 1px rgba(255,255,255,0.1);
}
.badge-strong { background: rgba(63, 185, 80, 0.1);  color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.25); }
.badge-weak   { background: rgba(210, 153, 34, 0.1); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.25); }
.badge-crash  { background: rgba(248, 81, 73, 0.1);  color: #f85149; border: 1px solid rgba(248, 81, 73, 0.25); }

.btc-card-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.btc-price-wrapper {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.btc-price {
  color: var(--text-primary);
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -0.5px;
  font-family: 'Inter', -apple-system, sans-serif;
}
.btc-delta-main {
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
}
.delta-up { color: #3fb950; background: rgba(63, 185, 80, 0.1); }
.delta-dn { color: #f85149; background: rgba(248, 81, 73, 0.1); }
.text-up { color: #3fb950; }
.text-dn { color: #f85149; }

.btc-metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  background: rgba(0, 0, 0, 0.2);
  padding: 10px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.02);
}
.metric-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.metric-label {
  font-size: 9px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-weight: 600;
}
.metric-val {
  font-size: 11px;
  font-weight: 700;
}
.btc-trend-chip {
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 9px;
  font-weight: 800;
  text-transform: uppercase;
}
.chip-up      { color: #3fb950; background: rgba(63, 185, 80, 0.1); }
.chip-dn      { color: #f85149; background: rgba(248, 81, 73, 0.1); }
.chip-neutral { color: var(--text-secondary); background: rgba(139, 148, 158, 0.1); }
.btc-score-val {
  font-size: 11px;
  color: #58a6ff;
  font-weight: 800;
}

.radar-nav-wrapper {"""

css_pattern = re.compile(r'\.btc-condition-bar \{.*?\.radar-nav-wrapper \{', re.DOTALL)
radar = css_pattern.sub(new_css, radar)

with open('dashboard/frontend/src/components/SignalRadarPanel.vue', 'w') as f:
    f.write(radar)
