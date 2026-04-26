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

def calculate_atr(highs, lows, closes, period=14):
    """
    Hitung Average True Range (ATR).
    Standard formula: max(H-L, abs(H-Cp), abs(L-Cp))
    """
    if len(closes) < period + 1:
        # Fallback: Estimasi dari standard deviation jika data kurang
        return np.std(np.diff(closes)) if len(closes) > 1 else closes[-1] * 0.01

    tr_list = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr_list.append(tr)
    
    # Simple Moving Average dari True Range
    atr = np.mean(tr_list[-period:])
    return float(atr)

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

    [Fix Signal Void Bug]:
    - Tier 1 BUY: Deep oversold (RSI<30) — tidak butuh Order Book.
    - Tier 2 BUY: RSI<40 + strong momentum — tidak butuh Order Book.
    - Tier 3 BUY: RSI<48 + slope + buy_pressure (konfirmasi premium).
    - SELL diperketat ke RSI>72 agar tidak memblok zona mean reversion (55-70).
    """
    rsi   = tech.get('rsi', 50)
    slope = tech.get('slope', 0)
    bp    = tech.get('buy_pressure', 50)  # orderbook imbalance, default netral

    verdict    = 'HOLD'
    confidence = 50
    reason     = 'Kondisi mixed, sinyal tidak cukup kuat.'

    # ── BUY CONDITIONS (3 Tier) ────────────────────────────────────────

    # Tier 1: Deep Oversold — tidak perlu Order Book (RSI sendiri sudah cukup)
    if rsi < 30 and slope > -0.001:
        verdict    = 'BUY'
        confidence = int(min(95, 65 + (30 - rsi) * 1.8))
        reason     = f"RSI deep oversold ({rsi:.0f}) — zona pantul historis tinggi"

    # Tier 2: Oversold + Momentum — cukup 2 faktor tanpa Order Book
    elif rsi < 40 and slope > 0.001:
        verdict    = 'BUY'
        confidence = int(min(85, 58 + (40 - rsi) * 1.2 + slope * 500))
        reason     = f"RSI oversold ({rsi:.0f}) + momentum naik (slope={slope:.4f})"

    # Tier 3: Pre-oversold + Slope kuat + Bid Dominance (sinyal paling solid)
    elif rsi < 48 and slope > 0.002 and bp > 55:
        verdict    = 'BUY'
        confidence = int(min(88, 55 + (48 - rsi) * 1.0 + (bp - 55) * 0.6))
        reason     = f"Momentum bullish + bid dominance ({bp:.0f}%), RSI={rsi:.0f}"

    # ── SELL CONDITIONS (diperketat, jangan block zona mean reversion) ──

    # Hanya blok jika RSI benar-benar overbought (>72) dan ada tekanan jual nyata
    elif rsi > 72 and (slope < -0.001 or bp < 42):
        verdict    = 'SELL'
        confidence = int(min(95, 60 + (rsi - 72) * 1.8 + max(0, (42 - bp)) * 0.5))
        reason     = f"RSI overbought ({rsi:.0f}), tekanan jual kuat ({100-bp:.0f}%)"

    # Momentum turun tajam — hati-hati tapi bukan hard block
    elif rsi > 60 and slope < -0.004:
        verdict    = 'SELL'
        confidence = int(min(78, 55 + (rsi - 60) * 0.8 + abs(slope) * 80))
        reason     = f"Momentum bearish kuat (slope={slope:.4f}), RSI={rsi:.0f}"

    return {
        'verdict':    verdict,
        'confidence': confidence,
        'reason':     reason,
    }
