import re

with open('dashboard/frontend/src/components/SignalRadarPanel.vue', 'r') as f:
    radar = f.read()

btc_css = """
.btc-condition-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 12px 20px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.05);
  background: rgba(0, 0, 0, 0.3);
  font-size: 10px;
  font-weight: 700;
  transition: border-color 0.3s ease, background 0.3s ease;
}
.btc-bar-strong {
  border-color: rgba(63, 185, 80, 0.4);
  background: linear-gradient(90deg, rgba(63, 185, 80, 0.06) 0%, rgba(0,0,0,0.3) 100%);
}
.btc-bar-weak {
  border-color: rgba(210, 153, 34, 0.4);
  background: linear-gradient(90deg, rgba(210, 153, 34, 0.06) 0%, rgba(0,0,0,0.3) 100%);
}
.btc-bar-crash {
  border-color: rgba(248, 81, 73, 0.5);
  background: linear-gradient(90deg, rgba(248, 81, 73, 0.08) 0%, rgba(0,0,0,0.3) 100%);
  animation: btc-crash-pulse 1.5s ease-in-out infinite alternate;
}
@keyframes btc-crash-pulse {
  from { border-color: rgba(248, 81, 73, 0.5); }
  to   { border-color: rgba(248, 81, 73, 0.9); }
}
.btc-bar-left {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.btc-icon {
  font-size: 12px;
  color: #f0a500;
}
.btc-label {
  color: var(--text-secondary);
  letter-spacing: 0.5px;
}
.btc-price {
  color: var(--text-primary);
  font-size: 11px;
}
.btc-bar-mid {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.btc-delta {
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 9px;
  font-weight: 800;
}
.delta-up { color: #3fb950; background: rgba(63, 185, 80, 0.1); }
.delta-dn { color: #f85149; background: rgba(248, 81, 73, 0.1); }
.btc-trend-chip {
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.3px;
}
.chip-up      { color: #3fb950; background: rgba(63, 185, 80, 0.12); }
.chip-dn      { color: #f85149; background: rgba(248, 81, 73, 0.12); }
.chip-neutral { color: var(--text-secondary); background: rgba(139, 148, 158, 0.1); }
.btc-bar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.btc-status-badge {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 9px;
  font-weight: 900;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.badge-strong { background: rgba(63, 185, 80, 0.15);  color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.3);  }
.badge-weak   { background: rgba(210, 153, 34, 0.15); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.3); }
.badge-crash  { background: rgba(248, 81, 73, 0.15);  color: #f85149; border: 1px solid rgba(248, 81, 73, 0.5);  }
.btc-score-val {
  font-size: 9px;
  color: var(--text-secondary);
  font-weight: 800;
}
"""

radar = radar.replace('/* Segmented Control */', btc_css + '\n/* Segmented Control */')
if '/* Segmented Control */' not in radar and '/* Modern Controls */' in radar:
    radar = radar.replace('/* Modern Controls */', btc_css + '\n/* Modern Controls */')

with open('dashboard/frontend/src/components/SignalRadarPanel.vue', 'w') as f:
    f.write(radar)
