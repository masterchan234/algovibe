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

        # 1. Linear Regression Slope (Momentum Sejati)
        # Menghitung tren arah harga secara matematis, mengabaikan noise tick.
        # x = range(n), y = price_history
        sum_x = n * (n - 1) / 2
        sum_y = sum(price_history)
        sum_xy = sum(i * price_history[i] for i in range(n))
        sum_x2 = sum(i * i for i in range(n))
        
        denominator = (n * sum_x2 - sum_x * sum_x)
        if denominator == 0:
            slope = 0
        else:
            slope = (n * sum_xy - sum_x * sum_y) / denominator
            
        # Normalisasi slope ke percentage
        slope_pct = (slope / current_price) * 100
        
        # Jika slope sangat positif (Momentum naik kuat)
        if slope_pct > 0.005:
            prob += 0.25
        elif slope_pct > 0.001:
            prob += 0.10
        elif slope_pct < -0.005:
            prob -= 0.30 # Trend turun tajam

        # 2. Tick Volatility Z-Score (Deteksi Anomali / Breakout)
        # Seberapa jauh harga saat ini dari rata-rata (dalam satuan standar deviasi)
        mean_price = sum_y / n
        variance = sum((p - mean_price) ** 2 for p in price_history) / n
        std_dev = variance ** 0.5
        
        if std_dev > 0:
            z_score = (current_price - mean_price) / std_dev
            
            # Jika harga melonjak jauh di atas rata-rata (Breakout asli)
            if z_score > 2.0:
                prob += 0.15
            elif z_score < -2.0:
                prob -= 0.20 # Dump
        
        # 3. Micro Mean-Reversion (Anti-Falling-Knife)
        # Deteksi penurunan tiba-tiba dalam 10 tick terakhir
        if n >= 10:
            recent_10 = price_history[-10:]
            recent_max = max(recent_10)
            drop_from_recent_max = (recent_max - current_price) / recent_max
            
            if drop_from_recent_max > 0.01: # Turun > 1% dalam 10 tick
                prob -= 0.40 # Hindari menangkap pisau jatuh
                
        # 4. Micro-Bounce / Floor Detection
        # Jika harga stabil di support lokal lalu sedikit naik
        if n >= 20:
            recent_20 = price_history[-20:]
            local_min = min(recent_20)
            if current_price > local_min * 1.002: # Naik 0.2% dari titik terendah 20 tick terakhir
                prob += 0.15

        prob = max(0.0, min(prob, 0.99))
        return prob

    except Exception:
        return 0.0
