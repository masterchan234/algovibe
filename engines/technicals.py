import numpy as np

def calculate_rsi(prices, periods=14):
    """Hitung RSI (Relative Strength Index)."""
    if len(prices) < periods + 1:
        return 50.0
    
    deltas = np.diff(prices)
    seed = deltas[:periods]
    up = seed[seed >= 0].sum() / periods
    down = -seed[seed < 0].sum() / periods
    rs = up / down if down != 0 else 0
    rsi = np.zeros_like(prices)
    rsi[:periods] = 100. - 100. / (1. + rs)

    for i in range(periods, len(prices)):
        delta = deltas[i - 1]
        if delta > 0:
            upval = delta
            downval = 0.
        else:
            upval = 0.
            downval = -delta

        up = (up * (periods - 1) + upval) / periods
        down = (down * (periods - 1) + downval) / periods

        rs = up / down if down != 0 else 0
        rsi[i] = 100. - 100. / (1. + rs)

    return float(rsi[-1])

def calculate_volatility(prices, window=20):
    """Hitung volatilitas real-time (standard deviation pct)."""
    if len(prices) < window:
        return 0.0
    returns = np.diff(prices) / prices[:-1]
    return float(np.std(returns[-window:]) * 100)

def get_technical_summary(prices):
    """Kembalikan rangkuman teknikal lengkap."""
    if not prices or len(prices) < 2:
        return {"rsi": 50, "volatility": 0, "signal": "NEUTRAL", "slope": 0}
    
    rsi = calculate_rsi(prices)
    vol = calculate_volatility(prices)
    
    # Hitung Slope (Momentum) sederhana
    n = len(prices)
    x = np.arange(n)
    y = np.array(prices)
    slope = 0
    if n > 5:
        slope = (n * np.sum(x*y) - np.sum(x) * np.sum(y)) / (n * np.sum(x**2) - (np.sum(x))**2)
        slope = (slope / prices[-1]) * 100 # Normalisasi ke %
    
    signal = "NEUTRAL"
    if rsi > 70: signal = "OVERBOUGHT"
    elif rsi < 30: signal = "OVERSOLD"
    elif rsi > 55 and slope > 0.001: signal = "STRONG BULLISH"
    elif rsi < 45 and slope < -0.001: signal = "STRONG BEARISH"
    elif rsi > 50: signal = "BULLISH"
    elif rsi < 50: signal = "BEARISH"
    
    return {
        "rsi": round(rsi, 1),
        "volatility": round(vol, 2),
        "slope": round(slope, 4),
        "signal": signal
    }

def derive_trade_signal(tech):
    """
    Konversi output get_technical_summary() + buy_pressure menjadi
    sinyal trading: BUY / HOLD / SELL beserta confidence (0-100) dan alasan.
    Confidence < 60 → badge tidak ditampilkan di UI (dianggap HOLD lemah).
    """
    rsi   = tech.get('rsi', 50)
    slope = tech.get('slope', 0)
    bp    = tech.get('buy_pressure', 50)  # orderbook imbalance, default netral

    verdict    = 'HOLD'
    confidence = 50
    reason     = 'Kondisi mixed, sinyal tidak cukup kuat.'

    # ── BUY CONDITIONS ─────────────────────────────────────────────────
    if rsi < 35 and slope > 0 and bp > 55:
        verdict    = 'BUY'
        confidence = int(min(95, 60 + (35 - rsi) * 1.5 + (bp - 55) * 0.5))
        reason     = f"RSI oversold ({rsi}) + momentum bullish + bid dominance ({bp:.0f}%)"

    elif rsi < 45 and slope > 0.002 and bp > 60:
        verdict    = 'BUY'
        confidence = int(min(85, 55 + (45 - rsi) * 1.0 + (bp - 60) * 0.5))
        reason     = f"Momentum bullish kuat (slope={slope:.4f}), bid dominance {bp:.0f}%"

    # ── SELL CONDITIONS ────────────────────────────────────────────────
    elif rsi > 65 and (slope < 0 or bp < 45):
        verdict    = 'SELL'
        confidence = int(min(95, 60 + (rsi - 65) * 1.5 + max(0, (45 - bp)) * 0.5))
        reason     = f"RSI overbought ({rsi}), tekanan jual dominan ({100-bp:.0f}%)"

    elif rsi > 55 and slope < -0.002:
        verdict    = 'SELL'
        confidence = int(min(85, 55 + (rsi - 55) * 1.0 + abs(slope) * 100))
        reason     = f"Momentum bearish (slope={slope:.4f}), RSI melemah ({rsi})"

    return {
        'verdict':    verdict,
        'confidence': confidence,
        'reason':     reason,
    }
