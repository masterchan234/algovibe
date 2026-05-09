
import sys
import os
import datetime
sys.path.append('/root/algovibe')
from app.infrastructure.database import db

def test_radar_audit_logic():
    # Mocking rows to test aware/naive handling
    now = datetime.datetime.now()
    
    # Test case 1: Aware timestamp (UTC)
    ts_aware = datetime.datetime.now(datetime.timezone.utc)
    print(f"Testing with aware timestamp: {ts_aware}")
    
    # Simulate normalization
    ts = ts_aware
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    
    try:
        diff = (now - ts).total_seconds()
        print(f"Diff (aware->naive): {diff}")
    except Exception as e:
        print(f"FAIL: {e}")

    # Test case 2: Naive timestamp
    ts_naive = datetime.datetime.now()
    print(f"Testing with naive timestamp: {ts_naive}")
    ts = ts_naive
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    
    try:
        diff = (now - ts).total_seconds()
        print(f"Diff (naive->naive): {diff}")
    except Exception as e:
        print(f"FAIL: {e}")

    # Test case 3: String timestamp (aware)
    ts_str = "2026-05-08T01:35:35.982821+00:00"
    print(f"Testing with string aware: {ts_str}")
    ts = datetime.datetime.fromisoformat(ts_str.replace(' ', 'T'))
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    try:
        diff = (now - ts).total_seconds()
        print(f"Diff (str aware->naive): {diff}")
    except Exception as e:
        print(f"FAIL: {e}")

if __name__ == "__main__":
    test_radar_audit_logic()
