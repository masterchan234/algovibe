import re

with open('dashboard/frontend/src/components/ProfitChart.vue', 'r') as f:
    content = f.read()

# 1. Wrap the panels in a container
content = content.replace('<!-- UNIFIED EQUITY PERFORMANCE ORACLE -->\n    <div class="intelligence-panel">', '<!-- UNIFIED EQUITY PERFORMANCE ORACLE -->\n    <div class="panels-container">\n      <div class="intelligence-panel">')

content = content.replace('        </div>\n      </div>\n    </div>\n\n    <AiAnalysisModal', '        </div>\n      </div>\n    </div>\n    </div>\n\n    <AiAnalysisModal')

# 2. Modify CSS for intelligence-panel and add panels-container
css_replace = """
.panels-container {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  padding-bottom: 16px;
}

.intelligence-panel {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  padding: 16px 20px;
  background: linear-gradient(180deg, rgba(163, 113, 247, 0.04) 0%, rgba(13, 17, 23, 0.4) 100%);
  border-top: 1px solid rgba(163, 113, 247, 0.15);
  border-bottom: 1px solid rgba(163, 113, 247, 0.15);
}
"""

old_css_pattern = r'\.intelligence-panel \{\n  flex: 1;\n  display: flex;\n  flex-direction: column;\n  padding: 16px 20px;\n  background: linear-gradient\(180deg, rgba\(163, 113, 247, 0\.04\) 0%, rgba\(13, 17, 23, 0\.4\) 100%\);\n  border-top: 1px solid rgba\(163, 113, 247, 0\.15\);\n  min-height: 0;\n  overflow-y: auto;\n\}'

content = re.sub(old_css_pattern, css_replace.strip(), content)

with open('dashboard/frontend/src/components/ProfitChart.vue', 'w') as f:
    f.write(content)
