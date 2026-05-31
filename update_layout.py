import re

with open('dashboard/frontend/src/components/ProfitChart.vue', 'r') as f:
    profit_chart = f.read()

# 1. Remove the entire <div class="radar-section"> ... </div> from ProfitChart.vue
start_idx = profit_chart.find('    <!-- Top Radar -->')
end_idx = profit_chart.find('    <AiAnalysisModal', start_idx)

if start_idx != -1 and end_idx != -1:
    profit_chart = profit_chart[:start_idx] + profit_chart[end_idx:]

with open('dashboard/frontend/src/components/ProfitChart.vue', 'w') as f:
    f.write(profit_chart)
    
print("ProfitChart updated")

