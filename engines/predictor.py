def get_prediction(price_history: list) -> float:
    """
    Menghitung probabilitas eksekusi menggunakan indikator teknikal lanjutan.
    Menerima array history harga, mengembalikan float 0.0 - 1.0.
    """
    from config import AI_MIN_HISTORY_POINTS
    if not price_history or len(price_history) < AI_MIN_HISTORY_POINTS:
        return -1.0  # -1 indicates 'Warming Up' status

    try:
        prob = 0.40  # Base Score
        current_price = price_history[-1]

        # 1. RSI (Relative Strength Index) - 14
        gains = []
        losses = []
        for i in range(1, 15):
            delta = price_history[i] - price_history[i-1]
            if delta > 0:
                gains.append(delta)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(-delta)
        
        avg_gain = sum(gains) / 14.0
        avg_loss = sum(losses) / 14.0
        
        for i in range(15, len(price_history)):
            delta = price_history[i] - price_history[i-1]
            gain = delta if delta > 0 else 0
            loss = -delta if delta < 0 else 0
            
            avg_gain = (avg_gain * 13 + gain) / 14.0
            avg_loss = (avg_loss * 13 + loss) / 14.0
            
        if avg_loss == 0:
            rsi_val = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_val = 100.0 - (100.0 / (1.0 + rs))

        if rsi_val > 70:
            prob -= 0.10
        elif rsi_val > 55:
            prob += 0.20

        # 2. MACD (12, 26, 9)
        def ema(data, span):
            alpha = 2 / (span + 1)
            ema_vals = [data[0]]
            for price in data[1:]:
                ema_vals.append(price * alpha + ema_vals[-1] * (1 - alpha))
            return ema_vals

        ema12 = ema(price_history, 12)
        ema26 = ema(price_history, 26)
        macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
        signal_line = ema(macd_line, 9)
        
        macd_val = macd_line[-1]
        sig_val = signal_line[-1]
        
        if macd_val > sig_val:
            prob += 0.20

        # 3. Bollinger Bands (20, 2)
        window = price_history[-20:]
        ma = sum(window) / 20.0
        variance = sum((x - ma) ** 2 for x in window) / 20.0
        std = variance ** 0.5
        
        lower_band = ma - (std * 2)
        upper_band = ma + (std * 2)
        
        if current_price <= lower_band * 1.002 or current_price >= upper_band * 0.998:
            prob += 0.15 # Mengurangi sedikit bobot BB klasik

        # 4. ANTI-FALLING-KNIFE (Velocity Check)
        # Jika harga turun > 1.2% dalam 5 tick terakhir, ini Panic Dump.
        if len(price_history) >= 6:
            prev_5_price = price_history[-6]
            velocity_5 = (current_price - prev_5_price) / prev_5_price
            if velocity_5 < -0.012:
                prob -= 0.40 # Penalti berat, jangan coba lawan gravitasi
            elif velocity_5 < -0.006:
                prob -= 0.15

        # 5. REBOUND DETECTION (Bounce from Local Support)
        # Cek apakah harga saat ini sudah naik dari titik terendah 10 tick terakhir
        local_min = min(price_history[-10:])
        if current_price > local_min * 1.0015: # Naik 0.15% dari dasar
            prob += 0.20 # Bonus probabilitas untuk potensi pantulan (Hammer effect)

        prob = max(0.0, min(prob, 0.99))
        return prob

    except Exception:
        return 0.0
