import sys

with open('dashboard/server.py', 'r') as f:
    content = f.read()

# Add SERVER_START_TIME
if 'SERVER_START_TIME = time.time()' not in content:
    content = content.replace('app = Flask(__name__)', 'app = Flask(__name__)\nSERVER_START_TIME = time.time()')

# Add to _build_stats_payload
old_payload = """        # Gabungkan semua data
        payload = {
            'mode': 'DRY RUN' if dr_val else 'LIVE',
            'status': stats_dict.get('master_status', 'OFFLINE'),"""

new_payload = """        # Gabungkan semua data
        payload = {
            'system_uptime': time.time() - SERVER_START_TIME,
            'mode': 'DRY RUN' if dr_val else 'LIVE',
            'status': stats_dict.get('master_status', 'OFFLINE'),"""

content = content.replace(old_payload, new_payload)

with open('dashboard/server.py', 'w') as f:
    f.write(content)
