import sys
import os
sys.path.append(os.getcwd())

from dashboard.server import _build_stats_payload
import json

try:
    stats = _build_stats_payload()
    print("Stats Payload Generated Successfully:")
    print(json.dumps(stats, indent=2))
except Exception as e:
    import traceback
    print(f"Error generating payload: {e}")
    traceback.print_exc()
