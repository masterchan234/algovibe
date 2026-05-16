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


# ──────────────────────────────────────────────────────────────────────────────
# INDICATOR CALCULATIONS
# ──────────────────────────────────────────────────────────────────────────────

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

    Formula: W%R = (highest_high - close) / (highest_high - lowest_low) * -100
    Karena close selalu dalam [lowest, highest] dari window yang sama, hasil selalu [-100, 0].

    Interpretasi scalping:
      W%R <= -80  → deep oversold, peluang rebound tinggi
      W%R >= -20  → overbought, hati-hati masuk posisi baru
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
    Formula: (prices[-1] - prices[-period-1]) / prices[-period-1] * 100
    Returns: ROC dalam % (positif = naik, negatif = turun).

    Berguna sebagai konfirmasi momentum:
      ROC sangat negatif → oversold, potensi mean-reversion BUY
      ROC sangat positif → overbought, potensi mean-reversion SELL
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
    Mengukur apakah aktivitas trading saat ini di atas atau di bawah rata-rata.

    Returns: rasio float, 1.0 = rata-rata, >1.5 = spike signifikan, <0.5 = sepi.

    Berguna untuk:
      - Konfirmasi breakout: breakout valid harus disertai volume spike (ratio > 1.5).
      - Filter entry di pasar sepi (ratio < 0.5) → sinyal kurang reliabel.
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
    Jika harga naik → tambah volume; jika turun → kurangi volume.

    Returns: (obv_now, obv_slope_pct)
      obv_now       : nilai OBV terakhir (relative, bisa negatif)
      obv_slope_pct : slope OBV 5 periode terakhir dalam % (positif = OBV naik).

    Divergence bullish  : harga turun tapi OBV naik  → akumulasi diam-diam.
    Divergence bearish  : harga naik tapi OBV turun  → distribusi diam-diam.
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


# ──────────────────────────────────────────────────────────────────────────────
# MULTI-INDICATOR CONFLUENCE ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def get_technical_summary(prices, volumes=None):
    """
    Kembalikan rangkuman teknikal lengkap dari semua indikator.
    Data ini dipakai oleh derive_trade_signal() untuk keputusan akhir.

    Indikator:
      RSI, MACD, Bollinger Bands, Stochastic RSI, EMA Crossover,
      EMA Slope, Williams %R, Rate of Change (ROC),
      Volume Ratio (vol sekarang / MA-20), OBV slope.

    Args:
      prices  : list harga penutupan
      volumes : list volume per candle (opsional) — aktifkan volume indicators
    """
    if not prices or len(prices) < 2:
        return {
            "rsi": 50, "volatility": 0, "signal": "NEUTRAL", "slope": 0,
            "macd_hist": 0, "bb_pct": 0.5, "stoch_k": 50, "stoch_d": 50,
            "ema9": 0, "ema21": 0, "ema_cross": "NEUTRAL",
            "last_price": 0, "williams_r": -50, "roc": 0,
            "volume_ratio": 1.0, "obv_slope": 0.0,
        }

    prices_list = list(prices)

    # RSI 14
    rsi = calculate_rsi(prices_list)
    vol = calculate_volatility(prices_list)

    # Slope EMA-20
    n = len(prices_list)
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
    Multi-Confluence Technical Signal Engine (v2.0)
    ────────────────────────────────────────────────
    Keputusan BUY/SELL/HOLD berdasarkan SKOR BOBOT dari semua indikator.
    Tidak ada bias AI. Setiap indikator memberikan vote dengan bobot berbeda.

    SISTEM SCORING:
    - Setiap indikator berkontribusi skor +/- ke pool BUY atau SELL.
    - Keputusan akhir diambil hanya jika ambang minimum terpenuhi.
    - Confidence = fungsi dari kekuatan sinyal dan jumlah indikator yang agree.

    Args:
        tech               : dict hasil get_technical_summary()
        sector             : str | None — sektor coin dari SECTOR_MAP ('MEME','AI','L1','L2','DEFI',dst)
                             None = gunakan weight default (backward compatible)
        adaptive_thresholds: dict | None — {'rsi_oversold': float, 'rsi_overbought': float}
                             dari compute_adaptive_thresholds(). None = pakai threshold tetap.
        vol_idr_24h        : float | None — volume 24h IDR coin. Digunakan untuk volume
                             reliability guard: jika < threshold, bobot OBV/VolumeRatio diturunkan.

    Indikator & Bobot:
    1. RSI (w=3.0)            — momentum overbought/oversold klasik
    2. MACD Histogram (w=2.5) — momentum crossover (price-relative normalisasi)
    3. Bollinger %B (w=2.0)   — posisi harga relatif volatilitas
    4. Stochastic RSI (w=2.0) — overbought/oversold sensitif
    5. EMA Crossover (w=1.5)  — konfirmasi tren jangka menengah
    6. Williams %R (w=1.5)    — konfirmasi oversold/overbought lintas-metode
    7. Slope/Momentum (w=1.0) — percepatan harga
    8. ROC-10 (w=1.0)         — momentum harga langsung (mean-reversion)
    9. Order Book Pressure (w=2.0) — permintaan nyata dari pasar
   10. Volume Ratio (w=1.5)   — konfirmasi volume; spike = breakout valid
   11. OBV Slope (w=1.5)      — akumulasi/distribusi diam-diam

    Total max buy score  ≈ 19.5
    Total max sell score ≈ 19.5

    Syarat BUY : buy_score >= 6.0 DAN sell_score < 4.0
    Syarat SELL: sell_score >= 6.0 DAN buy_score < 4.0
    Else       : HOLD
    """
    # ── Sector-based weight modifiers ────────────────────────────────────────
    # Setiap sektor punya karakteristik berbeda — weight disesuaikan agar
    # indikator yang paling relevan mendapat bobot lebih besar.
    # Default = 1.0 (perilaku identik dengan sebelumnya untuk unknown sector).
    _SECTOR_WEIGHTS = {
        # MEME: pump cepat, RSI sering terlambat, momentum & volume lebih relevan
        'MEME': {
            'rsi': 0.7, 'macd': 1.4, 'bb': 1.3, 'stoch': 1.5,
            'ema': 0.6, 'wr': 1.2, 'slope': 1.0, 'roc': 1.0,
            'ob': 1.0, 'vol': 1.3, 'obv': 0.5,
        },
        # AI: mid-term momentum lebih predictable
        'AI': {
            'rsi': 1.0, 'macd': 1.2, 'bb': 1.0, 'stoch': 1.2,
            'ema': 1.0, 'wr': 1.0, 'slope': 1.0, 'roc': 1.0,
            'ob': 1.0, 'vol': 1.0, 'obv': 0.8,
        },
        # L1: bluechip slow-mover, semua indikator relevan secara default
        'L1': {
            'rsi': 1.0, 'macd': 1.0, 'bb': 1.0, 'stoch': 1.0,
            'ema': 1.0, 'wr': 1.0, 'slope': 1.0, 'roc': 1.0,
            'ob': 1.0, 'vol': 1.0, 'obv': 1.0,
        },
        # L2: mirip L1
        'L2': {
            'rsi': 1.0, 'macd': 1.0, 'bb': 1.0, 'stoch': 1.0,
            'ema': 1.0, 'wr': 1.0, 'slope': 1.0, 'roc': 1.0,
            'ob': 1.0, 'vol': 1.0, 'obv': 1.0,
        },
        # DEFI: OBV lebih relevan (smart money flows), sisanya default
        'DEFI': {
            'rsi': 1.0, 'macd': 1.0, 'bb': 1.0, 'stoch': 1.0,
            'ema': 1.0, 'wr': 1.0, 'slope': 1.0, 'roc': 1.0,
            'ob': 1.0, 'vol': 1.0, 'obv': 1.2,
        },
    }
    _DEFAULT_W = {'rsi': 1.0, 'macd': 1.0, 'bb': 1.0, 'stoch': 1.0,
                  'ema': 1.0, 'wr': 1.0, 'slope': 1.0, 'roc': 1.0,
                  'ob': 1.0, 'vol': 1.0, 'obv': 1.0}
    sw = _SECTOR_WEIGHTS.get(str(sector or '').upper(), _DEFAULT_W)

    # ── Volume Reliability Guard ──────────────────────────────────────────────
    # Coin dengan volume harian sangat tipis: OBV & Volume Ratio tidak reliable.
    _VOL_RELIABILITY_THRESHOLD = 200_000_000  # 200M IDR
    vol_mult = 1.0
    if vol_idr_24h is not None and float(vol_idr_24h or 0) < _VOL_RELIABILITY_THRESHOLD:
        vol_mult = 0.3  # downgrade bobot volume-based indicators

    # ── Adaptive thresholds (per-coin overrides) ─────────────────────────────
    _at = adaptive_thresholds or {}
    RSI_OVERSOLD   = float(_at.get('rsi_oversold',  35.0))
    RSI_OVERBOUGHT = float(_at.get('rsi_overbought', 70.0))

    BB_EXTREME_LOW  = float(_at.get('bb_extreme_low', 0.10))
    BB_LOW          = float(_at.get('bb_low', 0.25))
    BB_HIGH         = float(_at.get('bb_high', 0.75))
    BB_EXTREME_HIGH = float(_at.get('bb_extreme_high', 0.90))

    STOCH_K_OVERSOLD   = float(_at.get('stoch_k_oversold', 20.0))
    STOCH_D_OVERSOLD   = float(_at.get('stoch_d_oversold', 30.0))
    STOCH_K_OVERBOUGHT = float(_at.get('stoch_k_overbought', 80.0))
    STOCH_D_OVERBOUGHT = float(_at.get('stoch_d_overbought', 70.0))
    STOCH_K_MILD_OVERSOLD = STOCH_K_OVERSOLD + 10.0
    STOCH_K_MILD_OVERBOUGHT = STOCH_K_OVERBOUGHT - 10.0

    WR_DEEP_OVERSOLD   = float(_at.get('williams_deep_oversold', -85.0))
    WR_OVERSOLD        = float(_at.get('williams_oversold', -70.0))
    WR_OVERBOUGHT      = float(_at.get('williams_overbought', -30.0))
    WR_DEEP_OVERBOUGHT = float(_at.get('williams_deep_overbought', -15.0))

    SLOPE_STRONG_UP   = float(_at.get('slope_strong_up', 0.005))
    SLOPE_UP          = float(_at.get('slope_up', 0.001))
    SLOPE_DOWN        = float(_at.get('slope_down', -0.001))
    SLOPE_STRONG_DOWN = float(_at.get('slope_strong_down', -0.005))

    ROC_OVERSOLD  = float(_at.get('roc_oversold', -3.0))
    ROC_MILD_DOWN = float(_at.get('roc_mild_down', -1.0))
    ROC_MILD_UP   = float(_at.get('roc_mild_up', 1.0))
    ROC_OVERBOUGHT = float(_at.get('roc_overbought', 3.0))

    OB_STRONG_BID = float(_at.get('ob_strong_bid', 65.0))
    OB_BID_DOM    = float(_at.get('ob_bid_dom', 55.0))
    OB_ASK_DOM    = float(_at.get('ob_ask_dom', 45.0))
    OB_STRONG_ASK = float(_at.get('ob_strong_ask', 35.0))

    VOL_SPIKE = float(_at.get('volume_spike', 2.0))
    VOL_HIGH  = float(_at.get('volume_high', 1.5))
    VOL_LOW   = float(_at.get('volume_low', 0.6))
    VOL_DRY   = float(_at.get('volume_dry', 0.4))

    OBV_ACCUMULATE = float(_at.get('obv_accumulate', 5.0))
    OBV_RISING     = float(_at.get('obv_rising', 2.0))
    OBV_FALLING    = float(_at.get('obv_falling', -2.0))
    OBV_DISTRIBUTE = float(_at.get('obv_distribute', -5.0))

    BUY_THRESHOLD  = float(_at.get('buy_score_threshold', 6.0))
    SELL_THRESHOLD = float(_at.get('sell_score_threshold', 6.0))
    OPPOSING_MAX   = float(_at.get('opposing_max', 4.0))
    # Turunkan deep-oversold/deep-overbought secara proporsional
    RSI_DEEP_OVERSOLD   = max(15.0, RSI_OVERSOLD - 10.0)
    RSI_MILD_OVERSOLD   = RSI_OVERSOLD + 10.0
    RSI_EXTREME_OB      = min(95.0, RSI_OVERBOUGHT + 10.0)
    RSI_MILD_OB         = RSI_OVERBOUGHT - 10.0
    rsi     = float(tech.get('rsi', 50))
    slope   = float(tech.get('slope', 0))
    bp      = float(tech.get('buy_pressure', 50))  # order book imbalance
    mh      = float(tech.get('macd_hist', 0))
    bb      = float(tech.get('bb_pct', 0.5))
    sk      = float(tech.get('stoch_k', 50))
    sd      = float(tech.get('stoch_d', 50))
    ema_cr  = str(tech.get('ema_cross', 'NEUTRAL'))
    wr      = float(tech.get('williams_r', -50))   # Williams %R
    roc     = float(tech.get('roc', 0))             # Rate of Change (10-period)
    vr      = float(tech.get('volume_ratio', 1.0))  # Volume Ratio (vol / MA-20)
    obv_sl  = float(tech.get('obv_slope', 0.0))     # OBV Slope (% per 5 candle)
    last_price = max(1.0, float(tech.get('last_price', 1.0)))  # harga terakhir untuk normalisasi MACD
    ema9       = float(tech.get('ema9', last_price))

    buy_score  = 0.0
    sell_score = 0.0
    signals    = []   # List alasan untuk logging

    # ── 1. RSI (Bobot 3.0 × sector_weight) ───────────────────────────────────
    _w_rsi = 3.0 * sw['rsi']
    if rsi < RSI_DEEP_OVERSOLD:
        buy_score += _w_rsi
        signals.append(f"RSI deep-oversold({rsi:.0f})")
    elif rsi < RSI_OVERSOLD:
        buy_score += _w_rsi * (2.5 / 3.0)
        signals.append(f"RSI oversold({rsi:.0f})")
    elif rsi < RSI_MILD_OVERSOLD:
        buy_score += _w_rsi * (1.5 / 3.0)
        signals.append(f"RSI mild-oversold({rsi:.0f})")
    elif rsi > RSI_EXTREME_OB:
        sell_score += _w_rsi
        buy_score  -= _w_rsi * 1.5  # Heavy penalty for buying at extreme peak
        signals.append(f"RSI extreme-overbought({rsi:.0f}) [PENALTY]")
    elif rsi > RSI_OVERBOUGHT:
        sell_score += _w_rsi * (2.5 / 3.0)
        buy_score  -= _w_rsi * 0.8  # Prevent buying when already overbought
        signals.append(f"RSI overbought({rsi:.0f}) [PENALTY]")
    elif rsi > RSI_MILD_OB:
        sell_score += _w_rsi * (1.0 / 3.0)
        buy_score  -= _w_rsi * 0.3
        signals.append(f"RSI high({rsi:.0f})")

    # ── 2. MACD Histogram (Bobot 2.5 × sector_weight) ────────────────────────
    # Normalisasi price-relative: hindari scale bias antar koin IDR.
    # Menggunakan (|hist| / harga) * _MACD_NORM_FACTOR agar sinyal konsisten.
    _w_macd = sw['macd']
    if mh > 0:
        strength = min(1.0, (abs(mh) / last_price) * _MACD_NORM_FACTOR)
        buy_score += min(2.5, 1.0 + strength * 1.5) * _w_macd
        signals.append(f"MACD bullish({mh:+.4f})")
    elif mh < 0:
        strength = min(1.0, (abs(mh) / last_price) * _MACD_NORM_FACTOR)
        sell_score += min(2.5, 1.0 + strength * 1.5) * _w_macd
        signals.append(f"MACD bearish({mh:+.4f})")

    # ── 3. Bollinger %B (Bobot 2.0 × sector_weight) ──────────────────────────
    _w_bb = sw['bb']
    if bb < BB_EXTREME_LOW:
        buy_score += 2.0 * _w_bb
        signals.append(f"BB extreme-low({bb:.2f})")
    elif bb < BB_LOW:
        buy_score += 1.2 * _w_bb
        signals.append(f"BB low({bb:.2f})")
    elif bb > BB_EXTREME_HIGH:
        sell_score += 2.0 * _w_bb
        signals.append(f"BB extreme-high({bb:.2f})")
    elif bb > BB_HIGH:
        sell_score += 1.2 * _w_bb
        signals.append(f"BB high({bb:.2f})")

    # ── 4. Stochastic RSI (Bobot 2.0 × sector_weight) ────────────────────────
    # K dan D harus bergerak bersama (konfirmasi)
    _w_stoch = sw['stoch']
    if sk < STOCH_K_OVERSOLD and sd < STOCH_D_OVERSOLD:
        buy_score += 2.0 * _w_stoch
        signals.append(f"StochRSI oversold(K={sk:.0f},D={sd:.0f})")
    elif sk < STOCH_K_MILD_OVERSOLD:
        buy_score += 1.0 * _w_stoch
        signals.append(f"StochRSI mild-oversold(K={sk:.0f})")
    elif sk > STOCH_K_OVERBOUGHT and sd > STOCH_D_OVERBOUGHT:
        sell_score += 2.0 * _w_stoch
        buy_score  -= 1.5 * _w_stoch  # Penalty for peak entry
        signals.append(f"StochRSI overbought(K={sk:.0f},D={sd:.0f}) [PENALTY]")
    elif sk > STOCH_K_MILD_OVERBOUGHT:
        sell_score += 1.0 * _w_stoch
        buy_score  -= 0.5 * _w_stoch
        signals.append(f"StochRSI mild-overbought(K={sk:.0f})")

    # Stoch crossover (K memotong D dari bawah = bullish signal)
    if sk > sd and sk < 50:
        buy_score += 0.5 * _w_stoch
        signals.append("StochRSI K>D bullish-cross")
    elif sk < sd and sk > 50:
        sell_score += 0.5 * _w_stoch
        signals.append("StochRSI K<D bearish-cross")

    # ── 5. EMA Crossover (Bobot 1.5 × sector_weight) ─────────────────────────
    _w_ema = sw['ema']
    if ema_cr == "BULLISH":
        buy_score += 1.5 * _w_ema
        signals.append("EMA9>EMA21 bullish")
    elif ema_cr == "BEARISH":
        sell_score += 1.5 * _w_ema
        signals.append("EMA9<EMA21 bearish")

    # ── 6. Williams %R (Bobot 1.5 × sector_weight) ───────────────────────────
    # Konfirmator oversold/overbought yang independen dari RSI — range -100 s/d 0.
    # Oversold (<=-80) memperkuat sinyal BUY; overbought (>=-20) memperkuat SELL.
    _w_wr = sw['wr']
    if wr <= WR_DEEP_OVERSOLD:
        buy_score += 1.5 * _w_wr
        signals.append(f"W%R deep-oversold({wr:.0f})")
    elif wr <= WR_OVERSOLD:
        buy_score += 1.0 * _w_wr
        signals.append(f"W%R oversold({wr:.0f})")
    elif wr >= WR_DEEP_OVERBOUGHT:
        sell_score += 1.5 * _w_wr
        signals.append(f"W%R deep-overbought({wr:.0f})")
    elif wr >= WR_OVERBOUGHT:
        sell_score += 1.0 * _w_wr
        signals.append(f"W%R overbought({wr:.0f})")

    # ── 7. Slope/Momentum (Bobot 1.0 × sector_weight) ────────────────────────
    _w_slope = sw['slope']
    if slope > SLOPE_STRONG_UP:
        buy_score += 1.0 * _w_slope
        signals.append(f"slope strong-up({slope:.4f})")
    elif slope > SLOPE_UP:
        buy_score += 0.5 * _w_slope
        signals.append(f"slope up({slope:.4f})")
    elif slope < SLOPE_STRONG_DOWN:
        sell_score += 1.0 * _w_slope
        signals.append(f"slope strong-down({slope:.4f})")
    elif slope < SLOPE_DOWN:
        sell_score += 0.5 * _w_slope
        signals.append(f"slope down({slope:.4f})")

    # ── 8. Rate of Change / ROC-10 (Bobot 1.0 × sector_weight) ──────────────
    # Interpretasi mean-reversion: harga turun tajam = potensi bounce (BUY),
    # harga naik tajam = potensi koreksi (SELL).
    _w_roc = sw['roc']
    if roc < ROC_OVERSOLD:
        buy_score += 1.0 * _w_roc
        signals.append(f"ROC oversold({roc:.2f}%)")
    elif roc < ROC_MILD_DOWN:
        buy_score += 0.5 * _w_roc
        signals.append(f"ROC mild-down({roc:.2f}%)")
    elif roc > ROC_OVERBOUGHT:
        sell_score += 1.0 * _w_roc
        signals.append(f"ROC overbought({roc:.2f}%)")
    elif roc > ROC_MILD_UP:
        sell_score += 0.5 * _w_roc
        signals.append(f"ROC mild-up({roc:.2f}%)")

    # ── 9. Order Book Buy Pressure (Bobot 2.0 × sector_weight) ───────────────
    # Jika data OB tersedia (bp != 50.0 default), gunakan langsung.
    # Jika OB tidak ada, fallback: volume spike → proxy demand, volume kering → proxy supply.
    _w_ob = sw['ob']
    if bp != 50.0:
        if bp >= OB_STRONG_BID:
            buy_score += 2.0 * _w_ob
            signals.append(f"OB strong-bid({bp:.0f}%)")
        elif bp >= OB_BID_DOM:
            buy_score += 1.0 * _w_ob
            signals.append(f"OB bid-dom({bp:.0f}%)")
        elif bp <= OB_STRONG_ASK:
            sell_score += 2.0 * _w_ob
            signals.append(f"OB strong-ask({100 - bp:.0f}%)")
        elif bp <= OB_ASK_DOM:
            sell_score += 1.0 * _w_ob
            signals.append(f"OB ask-dom({100 - bp:.0f}%)")
    elif vr != 1.0:
        # Fallback: volume ratio sebagai proxy demand jika OB tidak tersedia
        # Spike volume saat harga naik = demand nyata (partial credit 1.0 maks)
        if vr >= VOL_SPIKE:
            buy_score += 1.0 * _w_ob
            signals.append(f"OB-fallback: Vol spike({vr:.1f}x) demand proxy")
        elif vr <= VOL_DRY:
            sell_score += 1.0 * _w_ob
            signals.append(f"OB-fallback: Vol dry({vr:.1f}x) supply proxy")

    # ── 10. Volume Ratio (Bobot 1.5 × sector_weight × vol_reliability) ───────
    # Hanya aktif jika data volume tersedia (vr != 1.0 default berarti ada data nyata).
    # Spike volume (>1.5x) mengkonfirmasi breakout valid.
    # Volume sepi (<0.5x) memperlemah sinyal arah apapun.
    _w_vol = sw['vol'] * vol_mult
    if vr != 1.0:
        if vr >= VOL_SPIKE:
            buy_score += 1.5 * _w_vol
            signals.append(f"Vol spike({vr:.1f}x) BUY confirm")
        elif vr >= VOL_HIGH:
            buy_score += 0.8 * _w_vol
            signals.append(f"Vol high({vr:.1f}x)")
        elif vr <= VOL_DRY:
            sell_score += 1.5 * _w_vol
            signals.append(f"Vol dry({vr:.1f}x) SELL confirm")
        elif vr <= VOL_LOW:
            sell_score += 0.5 * _w_vol
            signals.append(f"Vol low({vr:.1f}x)")

    # ── 11. OBV Slope (Bobot 1.5 × sector_weight × vol_reliability) ──────────
    # OBV naik = akumulasi (smart money beli diam-diam) → BUY bias.
    # OBV turun = distribusi (smart money jual diam-diam) → SELL bias.
    # Divergence: harga naik tapi OBV turun adalah sinyal jual kuat.
    _w_obv = sw['obv'] * vol_mult
    if obv_sl != 0.0:
        if obv_sl >= OBV_ACCUMULATE:
            buy_score += 1.5 * _w_obv
            signals.append(f"OBV accumulate({obv_sl:+.1f}%)")
        elif obv_sl >= OBV_RISING:
            buy_score += 0.8 * _w_obv
            signals.append(f"OBV rising({obv_sl:+.1f}%)")
        elif obv_sl <= OBV_DISTRIBUTE:
            sell_score += 1.5 * _w_obv
            signals.append(f"OBV distribute({obv_sl:+.1f}%)")
        elif obv_sl <= OBV_FALLING:
            sell_score += 0.8 * _w_obv
            signals.append(f"OBV falling({obv_sl:+.1f}%)")

    # ── 12. EMA-9 Extension Guard (Bobot -2.0 fixed penalty) ───────────────
    # Jika harga melambung terlalu jauh di atas EMA-9 tanpa pullback,
    # risiko koreksi (mean-reversion) sangat tinggi.
    _ema_dist_pct = (last_price - ema9) / ema9 * 100.0 if ema9 > 0 else 0
    if _ema_dist_pct > 2.5:  # Harga > 2.5% di atas EMA-9
        buy_score -= 2.0
        signals.append(f"Vertical spike({_ema_dist_pct:.1f}% > EMA9) [PENALTY]")
    elif _ema_dist_pct < -2.5:
        sell_score -= 2.0
        signals.append(f"Vertical crash({_ema_dist_pct:.1f}% < EMA9) [PENALTY]")

    # ── KEPUTUSAN AKHIR (Multi-Confluence Gate) ───────────────────────────────
    verdict    = 'HOLD'
    confidence = 50
    _sector_tag = f"[sector={sector}] " if sector else ""
    reason     = f'{_sector_tag}Sinyal campuran, tidak ada confluence yang cukup kuat.'

    # Dynamic opposing max: makin tinggi RSI, makin rendah toleransi counter-signal untuk BUY
    _effective_opposing_max = OPPOSING_MAX
    if rsi > RSI_EXTREME_OB:
        _effective_opposing_max = 1.0
    elif rsi > RSI_OVERBOUGHT:
        _effective_opposing_max = 2.0
    elif rsi > RSI_MILD_OB:
        _effective_opposing_max = 3.0

    if buy_score >= BUY_THRESHOLD and sell_score < _effective_opposing_max:
        verdict    = 'BUY'
        # Confidence: skala 50-95, berbanding lurus dengan selisih skor
        net_score  = buy_score - sell_score
        confidence = int(min(95, 50 + net_score * 3.5))
        reason     = f"{_sector_tag}Multi-Confluence BUY [{', '.join(signals[:4])}] | BS={buy_score:.1f} SS={sell_score:.1f}"

    elif sell_score >= SELL_THRESHOLD and buy_score < OPPOSING_MAX:
        verdict    = 'SELL'
        net_score  = sell_score - buy_score
        confidence = int(min(95, 50 + net_score * 3.5))
        reason     = f"{_sector_tag}Multi-Confluence SELL [{', '.join(signals[:4])}] | BS={buy_score:.1f} SS={sell_score:.1f}"

    elif buy_score >= 4.0 and sell_score < 3.0:
        # Sinyal lemah tapi satu arah — HOLD tapi beri tahu
        reason = f"{_sector_tag}Sinyal BUY lemah (skor={buy_score:.1f}) — tunggu konfirmasi lebih lanjut."

    elif sell_score >= 4.0 and buy_score < 3.0:
        reason = f"{_sector_tag}Sinyal SELL lemah (skor={sell_score:.1f}) — tunggu konfirmasi lebih lanjut."

    return {
        'verdict':    verdict,
        'confidence': confidence,
        'reason':     reason,
        'buy_score':  round(buy_score, 2),
        'sell_score': round(sell_score, 2),
        'signals':    signals,
    }


def compute_adaptive_thresholds(price_history, n_candles=50):
    """
    Hitung threshold RSI adaptif dari distribusi RSI historis coin itu sendiri.

    Prinsip: threshold 'oversold' bukan fixed 35, tapi percentile ke-20 dari
    RSI historis coin tersebut. Coin yang secara alami berada di RSI 30-40 (MEME)
    akan memiliki threshold lebih rendah secara otomatis.

    Args:
        price_history : list/deque harga penutupan
        n_candles     : jumlah candle terakhir yang digunakan (default 50)

    Returns:
        dict {
            'rsi_oversold'   : float,  # percentile ke-20 RSI historis, clamp [20, 45]
            'rsi_overbought' : float,  # percentile ke-80 RSI historis, clamp [55, 85]
        }
        Jika data tidak cukup (< 30 candle), kembalikan threshold default.
    """
    DEFAULT = {'rsi_oversold': 35.0, 'rsi_overbought': 70.0}

    prices = list(price_history or [])
    if len(prices) < 30:
        return DEFAULT

    # Hitung RSI untuk setiap titik dalam window n_candles terakhir
    window = prices[-n_candles:] if len(prices) >= n_candles else prices
    rsi_values = []
    for i in range(15, len(window) + 1):
        r = calculate_rsi(window[:i])
        if r != 50.0 or i >= 16:  # 50.0 hanya valid default jika data benar-benar cukup
            rsi_values.append(r)

    if len(rsi_values) < 10:
        return DEFAULT

    arr = np.array(rsi_values, dtype=float)
    oversold  = float(np.percentile(arr, 20))
    overbought = float(np.percentile(arr, 80))

    # Clamp agar tidak terlalu ekstrem
    oversold   = max(20.0, min(45.0, oversold))
    overbought = max(55.0, min(85.0, overbought))

    return {
        'rsi_oversold':   round(oversold, 1),
        'rsi_overbought': round(overbought, 1),
    }
