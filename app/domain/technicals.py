"""
technicals.py — Pure Technical Indicator Engine (v2.0)

Semua indikator berbasis rumus matematis murni.
TIDAK ADA AI/LLM bias dalam fungsi ini.
Setiap sinyal HARUS dikonfirmasi oleh minimal 2 indikator berbeda (multi-confluence).
"""

import numpy as np

# Faktor normalisasi MACD histogram terhadap harga.
# Nilai 1000 dikalibrasi untuk scalping IDR: gerakan >= 0.1% harga dianggap sinyal kuat.
# Contoh: hist=1 IDR pada koin 1000 IDR → 1/1000*1000 = 1.0 (jenuh = sinyal penuh).
_MACD_NORM_FACTOR = 1000


# 
# INDICATOR CALCULATIONS
# 

def calculate_rsi(prices, periods=14):
    """
    RSI (Wilder's Smoothing Method) — standar industri.
    Returns 50.0 sebagai default jika data tidak cukup.
    """
    if len(prices) < periods + 1:
        return 50.0

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Inisialisasi dengan SMA dari periode pertama
    avg_gain = float(np.mean(gains[:periods]))
    avg_loss = float(np.mean(losses[:periods]))

    # Wilder's Smoothing
    for i in range(periods, len(deltas)):
        avg_gain = (avg_gain * (periods - 1) + float(gains[i])) / periods
        avg_loss = (avg_loss * (periods - 1) + float(losses[i])) / periods

    # Tangani edge case: semua naik atau semua turun
    if avg_loss == 0:
        return 100.0  # Pure uptrend — tidak ada loss
    if avg_gain == 0:
        return 0.0    # Pure downtrend — tidak ada gain

    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def calculate_stochastic_rsi(prices, rsi_period=14, stoch_period=14, k_smooth=3, d_smooth=3):
    """
    Stochastic RSI: Mengukur posisi RSI dalam range historisnya.
    Memberikan sinyal oversold/overbought lebih sensitif dari RSI biasa.
    Returns: (k_val, d_val) — 0-100, oversold < 20, overbought > 80
    """
    if len(prices) < rsi_period + stoch_period + k_smooth + d_smooth:
        return 50.0, 50.0

    # Hitung RSI untuk setiap titik
    rsi_values = []
    for i in range(rsi_period, len(prices) + 1):
        rsi_values.append(calculate_rsi(prices[:i], rsi_period))

    if len(rsi_values) < stoch_period:
        return 50.0, 50.0

    # Hitung Stochastic dari RSI values
    stoch_k = []
    for i in range(stoch_period - 1, len(rsi_values)):
        window = rsi_values[i - stoch_period + 1: i + 1]
        low_rsi = min(window)
        high_rsi = max(window)
        denom = high_rsi - low_rsi
        k = ((rsi_values[i] - low_rsi) / denom * 100) if denom > 0 else 50.0
        stoch_k.append(k)

    if len(stoch_k) < max(k_smooth, d_smooth):
        return 50.0, 50.0

    # Smooth K
    k_smoothed = []
    for i in range(k_smooth - 1, len(stoch_k)):
        k_smoothed.append(np.mean(stoch_k[i - k_smooth + 1: i + 1]))

    if len(k_smoothed) < d_smooth:
        return float(k_smoothed[-1]) if k_smoothed else 50.0, 50.0

    # D = SMA dari K yang sudah di-smooth
    d_val = np.mean(k_smoothed[-d_smooth:])
    return float(k_smoothed[-1]), float(d_val)


def calculate_ema(prices, period):
    """
    Exponential Moving Average (EMA) — standar trading.
    """
    if len(prices) < period:
        return float(np.mean(prices)) if prices else 0.0

    k = 2.0 / (period + 1)
    ema = float(np.mean(prices[:period]))
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def calculate_macd(prices, fast=12, slow=26, signal_period=9):
    """
    MACD (Moving Average Convergence Divergence).
    Returns: (macd_line, signal_line, histogram)
    Bullish: macd > signal (crossover dari bawah)
    Bearish: macd < signal (crossover dari atas)
    """
    if len(prices) < slow + signal_period:
        return 0.0, 0.0, 0.0

    prices_arr = np.array(prices, dtype=float)
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)

    # Hitung EMA fast dan slow untuk setiap titik
    ema_fast = float(np.mean(prices_arr[:fast]))
    ema_slow = float(np.mean(prices_arr[:slow]))
    macd_values = []

    for i in range(slow, len(prices_arr)):
        ema_fast = prices_arr[i] * k_fast + ema_fast * (1 - k_fast)
        ema_slow = prices_arr[i] * k_slow + ema_slow * (1 - k_slow)
        macd_values.append(ema_fast - ema_slow)

    if len(macd_values) < signal_period:
        return 0.0, 0.0, 0.0

    k_sig = 2.0 / (signal_period + 1)
    sig = float(np.mean(macd_values[:signal_period]))
    for val in macd_values[signal_period:]:
        sig = val * k_sig + sig * (1 - k_sig)

    macd_line = macd_values[-1]
    histogram = macd_line - sig
    return float(macd_line), float(sig), float(histogram)


def calculate_bollinger_bands(prices, period=20, std_dev=2.0):
    """
    Bollinger Bands: mengidentifikasi kondisi harga ekstrem relatif terhadap MA.
    Returns: (upper, middle, lower, bb_pct)
    bb_pct: 0 = di lower band, 1 = di upper band. <0.2 = oversold, >0.8 = overbought
    """
    if len(prices) < period:
        last = prices[-1] if prices else 0.0
        return last, last, last, 0.5

    window = np.array(prices[-period:], dtype=float)
    middle = float(np.mean(window))
    std = float(np.std(window, ddof=1))

    upper = middle + std_dev * std
    lower = middle - std_dev * std

    last_price = float(prices[-1])
    denom = upper - lower
    bb_pct = (last_price - lower) / denom if denom > 0 else 0.5
    return upper, middle, lower, float(bb_pct)


def calculate_atr(highs, lows, closes, period=14):
    """
    Average True Range (ATR) — mengukur volatilitas sesungguhnya.
    Menggunakan Wilder's smoothing untuk konsistensi dengan RSI.
    """
    if len(closes) < period + 1:
        return float(np.std(np.diff(closes))) if len(closes) > 1 else closes[-1] * 0.01

    tr_list = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_list.append(tr)

    # Wilder's Smoothing untuk ATR
    atr = float(np.mean(tr_list[:period]))
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period
    return float(atr)


def calculate_volatility(prices, window=20):
    """Volatilitas historical (standard deviation of returns × 100)."""
    if len(prices) < window:
        return 0.0
    returns = np.diff(prices[-window:]) / np.array(prices[-window:-1])
    return float(np.std(returns) * 100)


def calculate_volume_profile(volumes, prices, n_bins=10):
    """
    Volume-at-Price profiling sederhana.
    Returns: (poc_price, val_low, val_high) — Point of Control dan Value Area.
    poc_price: Harga dengan volume tertinggi (support/resistance kuat).
    """
    if not volumes or not prices or len(volumes) != len(prices):
        return 0.0, 0.0, 0.0

    # Buat bins harga
    p_arr = np.array(prices, dtype=float)
    v_arr = np.array(volumes, dtype=float)
    p_min, p_max = p_arr.min(), p_arr.max()

    if p_max <= p_min:
        return float(p_arr[-1]), float(p_min), float(p_max)

    bin_edges = np.linspace(p_min, p_max, n_bins + 1)
    bin_volumes = np.zeros(n_bins)

    for p, v in zip(p_arr, v_arr):
        idx = min(n_bins - 1, int((p - p_min) / (p_max - p_min) * n_bins))
        bin_volumes[idx] += v

    poc_idx = int(np.argmax(bin_volumes))
    poc_price = float((bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2)

    # Value Area: 70% dari total volume
    total_vol = float(bin_volumes.sum())
    val_target = total_vol * 0.70
    sorted_idx = np.argsort(bin_volumes)[::-1]
    val_vol = 0.0
    val_bins = []
    for idx in sorted_idx:
        val_vol += bin_volumes[idx]
        val_bins.append(idx)
        if val_vol >= val_target:
            break

    val_indices = sorted(val_bins)
    val_low = float(bin_edges[val_indices[0]])
    val_high = float(bin_edges[val_indices[-1] + 1])
    return poc_price, val_low, val_high


def calculate_ema_slope(prices, ema_period=20, lookback=5):
    """
    Slope EMA sebagai proksi momentum tren.
    Returns: slope dalam persen per candle (negatif = turun, positif = naik).
    """
    if len(prices) < ema_period + lookback:
        return 0.0

    ema_now = calculate_ema(prices, ema_period)
    ema_prev = calculate_ema(prices[:-lookback], ema_period)

    if ema_prev <= 0:
        return 0.0
    return float((ema_now - ema_prev) / ema_prev * 100)


def calculate_williams_r(prices, period=14):
    """
    Williams %R: mengukur posisi harga penutupan di dalam range (high-low) N periode.
    Returns: nilai antara -100 (oversold/potensi bounce) dan 0 (overbought/potensi koreksi).
    """
    if len(prices) < period:
        return -50.0
    window = prices[-period:]
    highest = max(window)
    lowest = min(window)
    denom = highest - lowest
    if denom == 0:
        return -50.0
    return float((highest - float(prices[-1])) / denom * -100.0)


def calculate_roc(prices, period=10):
    """
    Rate of Change (ROC): perubahan harga dalam persen selama N periode.
    """
    if len(prices) < period + 1:
        return 0.0
    prev = float(prices[-(period + 1)])
    if prev <= 0:
        return 0.0
    return float((float(prices[-1]) - prev) / prev * 100.0)


def calculate_volume_ratio(volumes, ma_period=20):
    """
    Rasio volume sekarang terhadap volume MA-20.
    """
    if not volumes or len(volumes) < 2:
        return 1.0
    v_arr = np.array(volumes, dtype=float)
    window = v_arr[-ma_period:] if len(v_arr) >= ma_period else v_arr
    ma_vol = float(np.mean(window[:-1])) if len(window) > 1 else float(window[0])
    if ma_vol <= 0:
        return 1.0
    return float(v_arr[-1] / ma_vol)


def calculate_obv(prices, volumes):
    """
    On-Balance Volume (OBV) — mengakumulasi volume berdasarkan arah harga.
    """
    if len(prices) < 2 or len(volumes) < 2 or len(prices) != len(volumes):
        return 0.0, 0.0

    p_arr = np.array(prices, dtype=float)
    v_arr = np.array(volumes, dtype=float)

    obv = [0.0]
    for i in range(1, len(p_arr)):
        if p_arr[i] > p_arr[i - 1]:
            obv.append(obv[-1] + v_arr[i])
        elif p_arr[i] < p_arr[i - 1]:
            obv.append(obv[-1] - v_arr[i])
        else:
            obv.append(obv[-1])

    obv_arr = np.array(obv)
    obv_now = float(obv_arr[-1])

    # Slope OBV 5 periode terakhir
    lookback = min(5, len(obv_arr) - 1)
    obv_prev = float(obv_arr[-lookback - 1])
    if obv_prev == 0:
        obv_slope_pct = 0.0
    else:
        obv_slope_pct = float((obv_now - obv_prev) / abs(obv_prev) * 100.0)

    return obv_now, obv_slope_pct


# 
# MULTI-INDICATOR CONFLUENCE ENGINE
# 

def get_technical_summary(prices, volumes=None):
    """
    Kembalikan rangkuman teknikal lengkap dari semua indikator.
    Data ini dipakai oleh derive_trade_signal() untuk keputusan akhir.
    """
    if not prices or len(prices) < 2:
        return {
            "rsi": 50, "volatility": 0, "signal": "NEUTRAL", "slope": 0,
            "velocity": 0, "macd_hist": 0, "bb_pct": 0.5, "stoch_k": 50, "stoch_d": 50,
            "ema9": 0, "ema21": 0, "ema_cross": "NEUTRAL",
            "last_price": 0, "williams_r": -50, "roc": 0,
            "volume_ratio": 1.0, "obv_slope": 0.0,
        }

    prices_list = list(prices)
    n = len(prices_list)

    # Price Velocity (Momentum Impulse)
    velocity = 0.0
    if n >= 4:
        velocity = (prices_list[-1] - prices_list[-4]) / prices_list[-4] * 100

    # RSI 14
    rsi = calculate_rsi(prices_list)
    vol = calculate_volatility(prices_list)

    # Slope EMA-20
    x = np.arange(n)
    y = np.array(prices_list, dtype=float)
    slope = 0.0
    if n > 5:
        slope = (n * np.sum(x * y) - np.sum(x) * np.sum(y)) / (n * np.sum(x ** 2) - np.sum(x) ** 2)
        slope = float((slope / prices_list[-1]) * 100)

    # MACD
    macd_line, macd_sig, macd_hist = calculate_macd(prices_list)

    # Bollinger Bands
    _, _, _, bb_pct = calculate_bollinger_bands(prices_list)

    # Stochastic RSI
    stoch_k, stoch_d = calculate_stochastic_rsi(prices_list)

    # EMA Crossover (EMA-9 vs EMA-21)
    ema9 = calculate_ema(prices_list, 9)
    ema21 = calculate_ema(prices_list, 21)
    ema_cross = "BULLISH" if ema9 > ema21 else ("BEARISH" if ema9 < ema21 else "NEUTRAL")

    # Sinyal sederhana (untuk kompatibilitas backward)
    signal = "NEUTRAL"
    if rsi > 70:
        signal = "OVERBOUGHT"
    elif rsi < 30:
        signal = "OVERSOLD"
    elif rsi > 55 and slope > 0.001 and ema_cross == "BULLISH":
        signal = "STRONG BULLISH"
    elif rsi < 45 and slope < -0.001 and ema_cross == "BEARISH":
        signal = "STRONG BEARISH"
    elif rsi > 50:
        signal = "BULLISH"
    elif rsi < 50:
        signal = "BEARISH"

    # Williams %R (14-period)
    williams_r = calculate_williams_r(prices_list) if len(prices_list) >= 14 else -50.0

    # Rate of Change (10-period)
    roc = calculate_roc(prices_list) if len(prices_list) >= 11 else 0.0

    # Volume Ratio dan OBV (opsional — butuh data volume)
    vols_list = list(volumes) if volumes and len(volumes) == len(prices_list) else None
    if vols_list and len(vols_list) >= 2:
        volume_ratio = calculate_volume_ratio(vols_list)
        _, obv_slope = calculate_obv(prices_list, vols_list)
    else:
        volume_ratio = 1.0
        obv_slope = 0.0

    return {
        "rsi":          round(rsi, 1),
        "volatility":   round(vol, 2),
        "slope":        round(slope, 4),
        "velocity":     round(velocity, 4),
        "signal":       signal,
        "macd_line":    round(macd_line, 6),
        "macd_sig":     round(macd_sig, 6),
        "macd_hist":    round(macd_hist, 6),
        "bb_pct":       round(bb_pct, 4),
        "stoch_k":      round(stoch_k, 1),
        "stoch_d":      round(stoch_d, 1),
        "ema9":         round(ema9, 6),
        "ema21":        round(ema21, 6),
        "ema_cross":    ema_cross,
        "last_price":   round(float(prices_list[-1]), 6),
        "williams_r":   round(williams_r, 1),
        "roc":          round(roc, 4),
        "volume_ratio": round(volume_ratio, 3),
        "obv_slope":    round(obv_slope, 4),
    }

def derive_trade_signal(tech, sector=None, adaptive_thresholds=None, vol_idr_24h=None):
    """
    Simplified Momentum Signal Engine (v3.0)
    Fokus hanya pada 3 Indikator Utama:
    1. Velocity (Price Action Speed) - w=3.5
    2. Volume Ratio (Breakout fuel) - w=3.5
    3. Order Book Pressure (Market Intent) - w=3.0
    
    Total Max Score: 10.0
    Threshold BUY: 6.5 (Minimal 2 sinyal kuat agree)
    """
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
def compute_adaptive_thresholds(price_history, n_candles=50):
    return {'rsi_oversold': 35.0, 'rsi_overbought': 70.0}
