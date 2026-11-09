import re

with open('dashboard/server.py', 'r') as f:
    content = f.read()

# I will replace eid = cfg.EXCHANGE.lower() with:
# import os
# eid = os.getenv("EXCHANGE", "binance").lower()

content = content.replace('eid = cfg.EXCHANGE.lower()', 'import os\n        eid = os.getenv("EXCHANGE", "binance").lower()')

with open('dashboard/server.py', 'w') as f:
    f.write(content)
