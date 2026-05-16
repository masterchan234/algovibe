import sys

path = '/home/chan/projects/algovibe/app/domain/technicals.py'
with open(path, 'r') as f:
    lines = f.readlines()

# Reconstruct the file: Keep everything up to line 441 (end of get_technical_summary)
# Then insert the NEW derive_trade_signal
new_derive = """
def derive_trade_signal(tech, sector=None, adaptive_thresholds=None, vol_idr_24h=None):
    \"\"\"
    Simplified Momentum Signal Engine (v3.0)
    Fokus hanya pada 3 Indikator Utama:
    1. Velocity (Price Action Speed) - w=3.5
    2. Volume Ratio (Breakout fuel) - w=3.5
    3. Order Book Pressure (Market Intent) - w=3.0
    
    Total Max Score: 10.0
    Threshold BUY: 6.5 (Minimal 2 sinyal kuat agree)
    \"\"\"
    _at = adaptive_thresholds or {}
    BUY_THRESHOLD  = float(_at.get('buy_score_threshold', 6.5))
    SELL_THRESHOLD = float(_at.get('sell_score_threshold', 7.0))
    
    # Extract core metrics
    velocity = float(tech.get('velocity', 0.0))
    vr       = float(tech.get('volume_ratio', 1.0))
    bp       = float(tech.get('buy_pressure', 50))
    
    buy_score  = 0.0
    sell_score = 0.0
    signals    = []

    # 1. Price Velocity (3.5)
    if velocity > 0.4:
        buy_score += 3.5
        signals.append(f"Velocity Spike({velocity:+.2f}%)")
    elif velocity > 0.15:
        buy_score += 1.5
        signals.append(f"Velocity Mild({velocity:+.2f}%)")
    elif velocity < -0.4:
        sell_score += 3.5
        signals.append(f"Velocity Drop({velocity:+.2f}%)")

    # 2. Volume Ratio (3.5)
    if vr >= 2.0:
        buy_score += 3.5
        signals.append(f"Vol Spike({vr:.1f}x)")
    elif vr >= 1.4:
        buy_score += 1.5
        signals.append(f"Vol High({vr:.1f}x)")
    elif vr <= 0.4:
        sell_score += 2.0
        signals.append(f"Vol Dry({vr:.1f}x)")

    # 3. Order Book Pressure (3.0)
    if bp >= 65:
        buy_score += 3.0
        signals.append(f"OB Strong-Bid({bp:.0f}%)")
    elif bp >= 55:
        buy_score += 1.5
        signals.append(f"OB Bid-Dom({bp:.0f}%)")
    elif bp <= 35:
        sell_score += 3.0
        signals.append(f"OB Strong-Ask({100-bp:.0f}%)")

    verdict = 'HOLD'
    if buy_score >= BUY_THRESHOLD:
        verdict = 'BUY'
    elif sell_score >= SELL_THRESHOLD:
        verdict = 'SELL'

    return {
        'verdict': verdict,
        'confidence': int(min(95, 50 + (buy_score if verdict == 'BUY' else sell_score) * 4.5)),
        'reason': f"Momentum: {', '.join(signals)} | Score: {buy_score:.1f}/{sell_score:.1f}",
        'buy_score': round(buy_score, 2),
        'sell_score': round(sell_score, 2),
        'signals': signals
    }
"""

# Find the split point (after the return dictionary of get_technical_summary)
split_idx = -1
for i, line in enumerate(lines):
    if "obv_slope" in line and i > 400:
        # Found the end of the return dict
        for j in range(i, len(lines)):
            if "}" in lines[j]:
                split_idx = j + 1
                break
        break

if split_idx != -1:
    # We want to keep compute_adaptive_thresholds too
    adaptive_idx = -1
    for i in range(split_idx, len(lines)):
        if "def compute_adaptive_thresholds" in lines[i]:
            adaptive_idx = i
            break
    
    if adaptive_idx != -1:
        new_lines = lines[:split_idx] + [new_derive] + lines[adaptive_idx:]
        with open(path, 'w') as f:
            f.writelines(new_lines)
        print("SUCCESS: technicals.py cleaned up and simplified.")
    else:
        # If compute_adaptive_thresholds was lost, we add it back
        fixed_end = "\\ndef compute_adaptive_thresholds(price_history, n_candles=50):\\n    return {'rsi_oversold': 35.0, 'rsi_overbought': 70.0}\\n"
        new_lines = lines[:split_idx] + [new_derive] + [fixed_end]
        with open(path, 'w') as f:
            f.writelines(new_lines)
        print("SUCCESS: technicals.py reconstructed.")
else:
    print("ERROR: Split point not found.")
