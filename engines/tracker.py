import json
import os

from config import RAM_PATH

def update_trends(high_speed_pairs):
    trends = {}
    
    for p in high_speed_pairs:
        symbol = p['pair']
        price = p['last_price']
        file_path = f"{RAM_PATH}/{symbol}_history.json"
        
        # 1. Load History dari RAM
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    history = json.load(f)
            except:
                history = []
        else:
            history = []
            
        # 2. Update History
        history.append(price)
        if len(history) > 40:
            history.pop(0)
            
        # Simpan kembali ke RAM
        with open(file_path, 'w') as f:
            json.dump(history, f)
            
        # 3. Logika Breakout (Current > Max of Prev 5)
        is_breakout = False
        if len(history) >= 6:
            current = history[-1]
            prev_5 = history[-6:-1]
            is_breakout = current > max(prev_5)
            
        # 4. Logika Trend (Last 4 strictly increasing)
        trend = "WAITING_DATA"
        if len(history) >= 4:
            last_4 = history[-4:]
            if all(last_4[i] < last_4[i+1] for i in range(len(last_4)-1)):
                trend = "UP"
            elif all(last_4[i] > last_4[i+1] for i in range(len(last_4)-1)):
                trend = "DOWN"
            else:
                trend = "SIDEWAYS"
        
        # 5. Pack data
        trends[symbol] = {
            "current_price": price,
            "trend": trend,
            "breakout": is_breakout,
            "history_count": len(history)
        }
        
    return trends