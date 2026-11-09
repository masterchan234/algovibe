import re

with open('dashboard/frontend/src/App.vue', 'r') as f:
    app = f.read()

app = app.replace('grid-template-columns: 360px 1fr 550px;', 'grid-template-columns: 380px 1fr 520px;')
app = app.replace('gap: 8px;', 'gap: 12px;\n  padding: 12px;')
app = app.replace('padding: 8px;', '')

with open('dashboard/frontend/src/App.vue', 'w') as f:
    f.write(app)
print("App updated")
