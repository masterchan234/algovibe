import json

def process_login():
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/views/Login.vue', 'r') as f:
        content = f.read()
    
    # We will just do a standard replace for simple strings in python and write it back.
    content = content.replace('BY_ALGOVIBE_ORACLE', 'BY_ALGOVIBE_SYSTEM')
    content = content.replace('Algovibe Oracle', 'Algovibe Analytics')
    
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/views/Login.vue', 'w') as f:
        f.write(content)

def process_about():
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/views/About.vue', 'r') as f:
        content = f.read()
    
    content = content.replace('AI Layer (The Oracle)', 'AI Analytics Layer')
    content = content.replace('AI & Oracle Editorial Role', 'AI Editorial Role')
    content = content.replace('The AI layer acts as an isolated research analyst (The Oracle).', 'The AI layer acts as an isolated research analyst.')
    content = content.replace('Oracle Architecture', 'AI Architecture')
    content = content.replace('AI LAYER (THE ORACLE)', 'AI ANALYTICS LAYER')
    
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/views/About.vue', 'w') as f:
        f.write(content)

def process_ai_modal():
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/components/AiAnalysisModal.vue', 'r') as f:
        content = f.read()
    
    content = content.replace('ORACLE INTELLIGENCE:', 'SYSTEM INTELLIGENCE:')
    
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/components/AiAnalysisModal.vue', 'w') as f:
        f.write(content)

def process_activity():
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/components/ActivityFeed.vue', 'r') as f:
        content = f.read()
    
    content = content.replace('ORACLE', 'SYSTEM')
    
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/components/ActivityFeed.vue', 'w') as f:
        f.write(content)

def process_profit_chart():
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/components/ProfitChart.vue', 'r') as f:
        content = f.read()
    
    content = content.replace('UNIFIED EQUITY PERFORMANCE ORACLE', 'UNIFIED EQUITY PERFORMANCE')
    content = content.replace('ORACLE REASONING', 'SYSTEM REASONING')
    content = content.replace('Failed to fetch Oracle intelligence data', 'Failed to fetch intelligence data')
    content = content.replace('Belum ada data koin (menunggu trade pertama)', 'No coin data yet (waiting for first trade)')
    content = content.replace('Intelligence Panel (Unified Oracle)', 'Intelligence Panel')
    
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/components/ProfitChart.vue', 'w') as f:
        f.write(content)

process_login()
process_about()
process_ai_modal()
process_activity()
process_profit_chart()

