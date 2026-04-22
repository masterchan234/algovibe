def get_prediction(price_history: list) -> float:
    """
    Menghitung probabilitas eksekusi menggunakan indikator teknikal lanjutan.
    Menerima array history harga, mengembalikan float 0.0 - 1.0.
    """
    if not price_history or len(price_history) < 30:
        return 0.0

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
            prob += 0.20

        prob = max(0.0, min(prob, 0.99))
        return prob

    except Exception:
        return 0.0
