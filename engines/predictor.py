def get_prediction(price_history: list, ticker_data: dict = None, llm_boost: float = 0.0) -> float:
    """
    Menghitung probabilitas eksekusi menggunakan Microstructure, Orderbook Imbalance, & Micro-Pullbacks.
    Juga menerima `llm_boost` dari Oracle untuk memperhitungkan opini kualitatif AI.
    """
    from config import AI_MIN_HISTORY_POINTS
    
    n = len(price_history)
    if n < AI_MIN_HISTORY_POINTS:
        return -1.0  # Warming Up

    try:
        prob = 0.40  # Base Score
        current_price = price_history[-1]

        # --- 1. LLM Qualitative Boost ---
        # Interaksi langsung dengan hasil pemikiran LiteRT-LM (Oracle)
        prob += llm_boost

        # --- 2. Orderbook Imbalance (Filter Fake Breakout) ---
        if ticker_data:
            bid_depth = float(ticker_data.get("bid_depth", 0))
            ask_depth = float(ticker_data.get("ask_depth", 0))
            total_depth = bid_depth + ask_depth
            if total_depth > 0:
                imbalance = bid_depth / total_depth
                if imbalance > 0.65:
                    prob += 0.20  # Strong Buying Pressure
                elif imbalance < 0.40:
                    prob -= 0.35  # Fake Breakout / Strong Selling Pressure

        # --- 3. Noise Filtering (EMA Smoothing) ---
        alpha = 0.2
        smoothed_history = [price_history[0]]
        for i in range(1, n):
            ema = (price_history[i] * alpha) + (smoothed_history[-1] * (1 - alpha))
            smoothed_history.append(ema)

        smoothed_current = smoothed_history[-1]

        # --- 4. Micro-Pullback & Peak Penalty ---
        # Jangan beli di pucuk (Peak Penalty)
        recent_10 = price_history[-10:]
        recent_max = max(recent_10)
        
        if current_price >= recent_max:
            # Harga sedang di ujung atas, rawan Fomo/Terjebak
            prob -= 0.25 
        else:
            # Harga turun sedikit dari pucuk
            drop_from_max = (recent_max - current_price) / recent_max
            # Jika turun antara 0.1% hingga 0.4% (Ideal Pullback)
            if 0.001 <= drop_from_max <= 0.004:
                prob += 0.30 # Bonus Pullback Entry

        # --- 5. Linear Regression Slope (Trend Check) ---
        sum_x = n * (n - 1) / 2
        sum_y = sum(smoothed_history)
        sum_xy = sum(i * smoothed_history[i] for i in range(n))
        sum_x2 = sum(i * i for i in range(n))
        
        denominator = (n * sum_x2 - sum_x * sum_y) if (n * sum_x2 - sum_x * sum_y) != 0 else 1
        slope = (n * sum_xy - sum_x * sum_y) / denominator
        slope_pct = (slope / smoothed_current) * 100
        
        if slope_pct > 0.002:
            prob += 0.15 # Trend masih naik

        prob = max(0.0, min(prob, 0.99))
        return prob

    except Exception:
        return 0.0
