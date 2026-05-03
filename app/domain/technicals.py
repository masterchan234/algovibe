"""
technicals.py — Pure Technical Indicator Engine (v2.0)

Semua indikator berbasis rumus matematis murni.
TIDAK ADA AI/LLM bias dalam fungsi ini.
Setiap sinyal HARUS dikonfirmasi oleh minimal 2 indikator berbeda (multi-confluence).
"""

import numpy as np


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


# ──────────────────────────────────────────────────────────────────────────────
# MULTI-INDICATOR CONFLUENCE ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def get_technical_summary(prices):
    """
    Kembalikan rangkuman teknikal lengkap dari semua indikator.
    Data ini dipakai oleh derive_trade_signal() untuk keputusan akhir.
    """
    if not prices or len(prices) < 2:
        return {
            "rsi": 50, "volatility": 0, "signal": "NEUTRAL", "slope": 0,
            "macd_hist": 0, "bb_pct": 0.5, "stoch_k": 50, "stoch_d": 50,
            "ema9": 0, "ema21": 0, "ema_cross": "NEUTRAL",
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

    return {
        "rsi":        round(rsi, 1),
        "volatility": round(vol, 2),
        "slope":      round(slope, 4),
        "signal":     signal,
        "macd_line":  round(macd_line, 6),
        "macd_sig":   round(macd_sig, 6),
        "macd_hist":  round(macd_hist, 6),
        "bb_pct":     round(bb_pct, 4),
        "stoch_k":    round(stoch_k, 1),
        "stoch_d":    round(stoch_d, 1),
        "ema9":       round(ema9, 6),
        "ema21":      round(ema21, 6),
        "ema_cross":  ema_cross,
    }


def derive_trade_signal(tech):
    """
    Multi-Confluence Technical Signal Engine (v2.0)
    ────────────────────────────────────────────────
    Keputusan BUY/SELL/HOLD berdasarkan SKOR BOBOT dari semua indikator.
    Tidak ada bias AI. Setiap indikator memberikan vote dengan bobot berbeda.

    SISTEM SCORING:
    - Setiap indikator berkontribusi skor +/- ke pool BUY atau SELL.
    - Keputusan akhir diambil hanya jika ambang minimum terpenuhi.
    - Confidence = fungsi dari kekuatan sinyal dan jumlah indikator yang agree.

    Indikator & Bobot:
    1. RSI (w=3.0)        — momentum overbought/oversold klasik
    2. MACD Histogram (w=2.5) — momentum crossover
    3. Bollinger %B (w=2.0) — posisi harga relatif volatilitas
    4. Stochastic RSI (w=2.0) — overbought/oversold sensitif
    5. EMA Crossover (w=1.5) — konfirmasi tren jangka menengah
    6. Slope/Momentum (w=1.0) — percepatan harga
    7. Order Book Pressure (w=2.0) — permintaan nyata dari pasar

    Total max buy score  ≈ 14.0
    Total max sell score ≈ 14.0

    Syarat BUY : buy_score >= 6.0 DAN sell_score < 4.0
    Syarat SELL: sell_score >= 6.0 DAN buy_score < 4.0
    Else       : HOLD
    """
    rsi     = float(tech.get('rsi', 50))
    slope   = float(tech.get('slope', 0))
    bp      = float(tech.get('buy_pressure', 50))  # order book imbalance
    mh      = float(tech.get('macd_hist', 0))
    bb      = float(tech.get('bb_pct', 0.5))
    sk      = float(tech.get('stoch_k', 50))
    sd      = float(tech.get('stoch_d', 50))
    ema_cr  = str(tech.get('ema_cross', 'NEUTRAL'))

    buy_score  = 0.0
    sell_score = 0.0
    signals    = []   # List alasan untuk logging

    # ── 1. RSI (Bobot 3.0) ────────────────────────────────────────────────────
    if rsi < 25:
        buy_score += 3.0
        signals.append(f"RSI deep-oversold({rsi:.0f})")
    elif rsi < 35:
        buy_score += 2.5
        signals.append(f"RSI oversold({rsi:.0f})")
    elif rsi < 45:
        buy_score += 1.5
        signals.append(f"RSI mild-oversold({rsi:.0f})")
    elif rsi > 80:
        sell_score += 3.0
        signals.append(f"RSI extreme-overbought({rsi:.0f})")
    elif rsi > 70:
        sell_score += 2.5
        signals.append(f"RSI overbought({rsi:.0f})")
    elif rsi > 60:
        sell_score += 1.0
        signals.append(f"RSI high({rsi:.0f})")

    # ── 2. MACD Histogram (Bobot 2.5) ─────────────────────────────────────────
    if mh > 0:
        # Normalisasi: boost berdasarkan kekuatan histogram relatif vs harga
        strength = min(1.0, abs(mh) * 10000)
        buy_score += min(2.5, 1.0 + strength * 1.5)
        signals.append(f"MACD bullish({mh:+.4f})")
    elif mh < 0:
        strength = min(1.0, abs(mh) * 10000)
        sell_score += min(2.5, 1.0 + strength * 1.5)
        signals.append(f"MACD bearish({mh:+.4f})")

    # ── 3. Bollinger %B (Bobot 2.0) ───────────────────────────────────────────
    if bb < 0.10:
        buy_score += 2.0
        signals.append(f"BB extreme-low({bb:.2f})")
    elif bb < 0.25:
        buy_score += 1.2
        signals.append(f"BB low({bb:.2f})")
    elif bb > 0.90:
        sell_score += 2.0
        signals.append(f"BB extreme-high({bb:.2f})")
    elif bb > 0.75:
        sell_score += 1.2
        signals.append(f"BB high({bb:.2f})")

    # ── 4. Stochastic RSI (Bobot 2.0) ─────────────────────────────────────────
    # K dan D harus bergerak bersama (konfirmasi)
    if sk < 20 and sd < 30:
        buy_score += 2.0
        signals.append(f"StochRSI oversold(K={sk:.0f},D={sd:.0f})")
    elif sk < 30:
        buy_score += 1.0
        signals.append(f"StochRSI mild-oversold(K={sk:.0f})")
    elif sk > 80 and sd > 70:
        sell_score += 2.0
        signals.append(f"StochRSI overbought(K={sk:.0f},D={sd:.0f})")
    elif sk > 70:
        sell_score += 1.0
        signals.append(f"StochRSI mild-overbought(K={sk:.0f})")

    # Stoch crossover (K memotong D dari bawah = bullish signal)
    if sk > sd and sk < 50:
        buy_score += 0.5
        signals.append("StochRSI K>D bullish-cross")
    elif sk < sd and sk > 50:
        sell_score += 0.5
        signals.append("StochRSI K<D bearish-cross")

    # ── 5. EMA Crossover (Bobot 1.5) ──────────────────────────────────────────
    if ema_cr == "BULLISH":
        buy_score += 1.5
        signals.append("EMA9>EMA21 bullish")
    elif ema_cr == "BEARISH":
        sell_score += 1.5
        signals.append("EMA9<EMA21 bearish")

    # ── 6. Slope/Momentum (Bobot 1.0) ─────────────────────────────────────────
    if slope > 0.005:
        buy_score += 1.0
        signals.append(f"slope strong-up({slope:.4f})")
    elif slope > 0.001:
        buy_score += 0.5
        signals.append(f"slope up({slope:.4f})")
    elif slope < -0.005:
        sell_score += 1.0
        signals.append(f"slope strong-down({slope:.4f})")
    elif slope < -0.001:
        sell_score += 0.5
        signals.append(f"slope down({slope:.4f})")

    # ── 7. Order Book Buy Pressure (Bobot 2.0) ────────────────────────────────
    # Hanya berlaku jika data OB tersedia (bp != 50.0 default)
    if bp != 50.0:
        if bp >= 65:
            buy_score += 2.0
            signals.append(f"OB strong-bid({bp:.0f}%)")
        elif bp >= 55:
            buy_score += 1.0
            signals.append(f"OB bid-dom({bp:.0f}%)")
        elif bp <= 35:
            sell_score += 2.0
            signals.append(f"OB strong-ask({100 - bp:.0f}%)")
        elif bp <= 45:
            sell_score += 1.0
            signals.append(f"OB ask-dom({100 - bp:.0f}%)")

    # ── KEPUTUSAN AKHIR (Multi-Confluence Gate) ───────────────────────────────
    BUY_THRESHOLD  = 6.0   # Minimal skor BUY untuk trigger entry
    SELL_THRESHOLD = 6.0   # Minimal skor SELL untuk trigger exit/block
    OPPOSING_MAX   = 4.0   # Skor lawanan tidak boleh lebih dari ini

    verdict    = 'HOLD'
    confidence = 50
    reason     = 'Sinyal campuran, tidak ada confluence yang cukup kuat.'

    if buy_score >= BUY_THRESHOLD and sell_score < OPPOSING_MAX:
        verdict    = 'BUY'
        # Confidence: skala 50-95, berbanding lurus dengan selisih skor
        net_score  = buy_score - sell_score
        confidence = int(min(95, 50 + net_score * 3.5))
        reason     = f"Multi-Confluence BUY [{', '.join(signals[:4])}] | BS={buy_score:.1f} SS={sell_score:.1f}"

    elif sell_score >= SELL_THRESHOLD and buy_score < OPPOSING_MAX:
        verdict    = 'SELL'
        net_score  = sell_score - buy_score
        confidence = int(min(95, 50 + net_score * 3.5))
        reason     = f"Multi-Confluence SELL [{', '.join(signals[:4])}] | BS={buy_score:.1f} SS={sell_score:.1f}"

    elif buy_score >= 4.0 and sell_score < 3.0:
        # Sinyal lemah tapi satu arah — HOLD tapi beri tahu
        reason = f"Sinyal BUY lemah (skor={buy_score:.1f}) — tunggu konfirmasi lebih lanjut."

    elif sell_score >= 4.0 and buy_score < 3.0:
        reason = f"Sinyal SELL lemah (skor={sell_score:.1f}) — tunggu konfirmasi lebih lanjut."

    return {
        'verdict':    verdict,
        'confidence': confidence,
        'reason':     reason,
        'buy_score':  round(buy_score, 2),
        'sell_score': round(sell_score, 2),
        'signals':    signals,
    }
