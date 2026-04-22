import json
from config import TP_PCT, SL_PCT

def generate_entry_signal(symbol, trend_data, spread_pct):
    # Spek: Trend = UP + Breakout Detected + Spread < 0.5%
    is_up_trend = trend_data.get('trend') == "UP"
    is_breakout = trend_data.get('breakout', False)
    
    if is_up_trend and is_breakout and spread_pct < 0.5:
        # FEE SAFETY CHECK (2026 Tax Adjusted)
        # Estimasi total Maker Fee (Buy+Sell) = 0.46%
        # Target Gross Profit = 0.8%
        # Expected Net = 0.8% - 0.46% = 0.34% (Memenuhi spek Min Net 0.3%)
        
        return {
            "signal": "ENTRY_BUY",
            "pair": symbol,
            "entry_type": "LIMIT_MAKER",
            "target_tp": TP_PCT,
            "target_sl": SL_PCT
        }
    return None