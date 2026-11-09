import re

with open('dashboard/frontend/src/components/ProfitChart.vue', 'r') as f:
    content = f.read()

html_insert = """
        <!-- Market Status Info -->
        <div class="market-status-section" v-if="stats.session_awareness">
          <div class="ms-header">MARKET STATUS</div>
          <div class="ms-grid">
             <div class="ms-card">
               <span class="ms-label">REGIME</span>
               <span class="ms-value" :class="'awareness-' + (stats.session_awareness.state || 'UNKNOWN').toLowerCase().replace(/_/g,'-')">{{ stats.session_awareness.state?.replace(/_/g, ' ') || 'UNKNOWN' }}</span>
             </div>
             <div class="ms-card ms-wide" v-if="stats.session_awareness.reason">
               <span class="ms-label">ORACLE REASONING</span>
               <span class="ms-text">{{ stats.session_awareness.reason }}</span>
             </div>
          </div>
        </div>
"""

content = content.replace('        <!-- Equity Attribution Heatmap (Replacing Table) -->', html_insert + '\n        <!-- Equity Attribution Heatmap (Replacing Table) -->')

css_insert = """
/* Market Status Section */
.market-status-section {
  margin: 16px 0;
  background: rgba(15, 18, 25, 0.4);
  border: 1px solid rgba(139, 92, 246, 0.15);
  border-radius: 8px;
  overflow: hidden;
}

.ms-header {
  padding: 6px 12px;
  font-size: 10px;
  font-weight: 700;
  color: var(--accent-purple);
  background: rgba(139, 92, 246, 0.05);
  border-bottom: 1px solid rgba(139, 92, 246, 0.1);
  letter-spacing: 0.5px;
}

.ms-grid {
  display: flex;
  gap: 12px;
  padding: 12px;
}

.ms-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ms-card.ms-wide {
  flex: 1;
  border-left: 1px solid rgba(255, 255, 255, 0.05);
  padding-left: 12px;
}

.ms-label {
  font-size: 9px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.ms-value {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
}

.ms-text {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.4;
}

/* Reusing awareness badge colors for the text */
.ms-value.awareness-normal { color: #4ade80; }
.ms-value.awareness-choppy { color: #f59e0b; }
.ms-value.awareness-recovery { color: #3b82f6; }
.ms-value.awareness-euphoric { color: #a855f7; }
.ms-value.awareness-crash { color: #ef4444; }

"""

content = content.replace('/* Pulse Metrics */', css_insert + '\n/* Pulse Metrics */')

with open('dashboard/frontend/src/components/ProfitChart.vue', 'w') as f:
    f.write(content)
