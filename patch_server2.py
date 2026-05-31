import re

with open('dashboard/server.py', 'r') as f:
    content = f.read()

old_block = """            results.append({
                "pair": pair.upper(),
                "price": last,
                "change": round(change, 2),
                "ch_5m": float(row.get('ch_5m') or 0),
                "ch_15m": float(row.get('ch_15m') or 0),
                "ch_1h": float(row.get('ch_1h') or 0),
                "ch_24h": round(float(v.get('change_24h') or 0), 2),
                "volatility": float(row.get('volatility') or 0),
                "score": score,
                "prediction": float(row.get('prediction_prob') or 0.0),
                "trend": trend
            })"""

new_block = """            results.append({
                "pair": pair.upper(),
                "price": last,
                "change": round(change, 2),
                "ch_5m": float(row.get('ch_5m') or 0),
                "ch_15m": float(row.get('ch_15m') or 0),
                "ch_1h": float(row.get('ch_1h') or 0),
                "ch_24h": round(float(v.get('change_24h') or 0), 2),
                "volatility": float(row.get('volatility') or 0),
                "volume": float(v.get('vol_idr') or v.get('quoteVolume') or v.get('vol_usdt') or 0),
                "score": score,
                "prediction": float(row.get('prediction_prob') or 0.0),
                "trend": trend
            })"""

if old_block in content:
    with open('dashboard/server.py', 'w') as f:
        f.write(content.replace(old_block, new_block))
    print("Patched!")
else:
    print("Not found!")

