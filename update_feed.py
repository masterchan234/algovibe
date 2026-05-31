import re

with open('dashboard/frontend/src/components/ActivityFeed.vue', 'r') as f:
    content = f.read()

# Update .position-item padding and gap
content = content.replace(
    '  gap: 15px;\n  padding: 14px 20px;\n  margin: 6px 12px;',
    '  gap: 8px;\n  padding: 12px 14px;\n  margin: 6px 8px;'
)

# Update col-exit width and margin
content = content.replace(
    '.col-exit { flex: 0 0 70px; text-align: center; display: flex; justify-content: center; align-items: center; }',
    '.col-exit { flex: 0 0 75px; text-align: center; display: flex; justify-content: flex-end; align-items: center; margin-left: auto; padding-right: 5px; }'
)

with open('dashboard/frontend/src/components/ActivityFeed.vue', 'w') as f:
    f.write(content)
