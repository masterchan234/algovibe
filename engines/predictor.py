def get_prediction(price_history: list) -> float:
    """
    Menghitung probabilitas eksekusi menggunakan Microstructure & Statistical Arbitrage.
    Menerima array history harga (raw ticks, max 100), mengembalikan float 0.0 - 1.0.
    """
    from config import AI_MIN_HISTORY_POINTS
    
    n = len(price_history)
    if n < AI_MIN_HISTORY_POINTS:
        return -1.0  # -1 indicates 'Warming Up' status

    try:
        prob = 0.40  # Base Score
        current_price = price_history[-1]

        # --- Noise Filtering (EMA Smoothing) ---
        # Ticks di kripto sangat berisik. Kita haluskan dulu historinya.
        alpha = 0.2  # Smoothing factor
        smoothed_history = [price_history[0]]
        for i in range(1, n):
            ema = (price_history[i] * alpha) + (smoothed_history[-1] * (1 - alpha))
            smoothed_history.append(ema)

        # Gunakan harga yang sudah dihaluskan untuk analisa tren
        smoothed_current = smoothed_history[-1]

        # 1. Linear Regression Slope (Momentum Sejati) dari Smoothed History
        sum_x = n * (n - 1) / 2
        sum_y = sum(smoothed_history)
        sum_xy = sum(i * smoothed_history[i] for i in range(n))
        sum_x2 = sum(i * i for i in range(n))
        
        denominator = (n * sum_x2 - sum_x * sum_x)
        if denominator == 0:
            slope = 0
        else:
            slope = (n * sum_xy - sum_x * sum_y) / denominator
            
        slope_pct = (slope / smoothed_current) * 100
        
        # Momentum harus lebih solid karena sudah di-smooth
        if slope_pct > 0.003:
            prob += 0.25
        elif slope_pct > 0.001:
            prob += 0.10
        elif slope_pct < -0.003:
            prob -= 0.35 # Trend turun jelas

        # 2. Tick Volatility Z-Score (Mendeteksi Anomali / Breakout pada Smoothed Data)
        mean_price = sum_y / n
        variance = sum((p - mean_price) ** 2 for p in smoothed_history) / n
        std_dev = variance ** 0.5
        
        if std_dev > 0:
            z_score = (smoothed_current - mean_price) / std_dev
            
            # Membutuhkan Z-Score lebih stabil (karena data sudah dihaluskan)
            if z_score > 1.8:
                prob += 0.15
            elif z_score < -1.5:
                prob -= 0.20
        
        # 3. Micro Mean-Reversion (Anti-Falling-Knife Murni)
        # Deteksi penurunan tiba-tiba menggunakan data raw
        if n >= 10:
            recent_10 = price_history[-10:]
            recent_max = max(recent_10)
            drop_from_recent_max = (recent_max - current_price) / recent_max
            
            # Penurunan lebih tajam = risiko lebih tinggi
            if drop_from_recent_max > 0.005: # Turun > 0.5% dalam 10 tick terakhir
                prob -= 0.40 # Hindari pisau jatuh
                
        # 4. Micro-Bounce / Floor Detection (Support)
        if n >= 20:
            recent_20 = smoothed_history[-20:]
            local_min = min(recent_20)
            if smoothed_current > local_min * 1.0015: # Naik pelan dari support (0.15%)
                prob += 0.15

        prob = max(0.0, min(prob, 0.99))
        return prob

    except Exception:
        return 0.0
