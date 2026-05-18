"""
app/domain/lead_lag.py — Binance Lead-Lag Formula Engine
=========================================================
Implementasi lengkap formula kuantitatif untuk deteksi lead-lag signal
antara Binance (harga global, domain publik) dan Indodax (IDR, domestik).

FORMULA UTAMA:
  1. Binance Return Signal  : r_B(t, W) = (P_B(t) - P_B(t-W)) / P_B(t-W) * 100%
  2. Cross-Correlation τ*   : ρ(τ) = Corr[P̂_B(t), P̂_I(t+τ)],  τ* = argmax ρ(τ)
  3. Implied IDR Spread     : spread% = (ImpliedRate - USDT_IDR) / USDT_IDR * 100%
  4. Signal Gate Thresholds : Toxic-flow (-0.50%), Fast-track tier 1/2 (0.65%/0.80%)
  5. Confidence Scaling     : conf = clip(75 + (r_B - θ) × 20,  75, 95)
  6. Profitability Guard    : NetPnL = r_I(τ*) - 2 × fee_taker > 0

Author: Senior Python Engineer & Quantitative Analyst
"""

from __future__ import annotations

import time
from typing import List, Tuple, Optional, Dict

import numpy as np

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------
DEFAULT_WINDOW_MS: float = 400.0       # Default lookback window Binance (ms)
DEFAULT_FEE_TAKER: float = 0.0051      # Indodax taker fee 0.51%
DEFAULT_MAX_LAG_MS: int = 2000         # Max lag range cross-correlation
DEFAULT_GRID_MS: int = 50              # Resampling grid (ms)
USDT_IDR_FALLBACK: float = 17630.0    # Fallback kurs USDT/IDR jika tidak tersedia live

# Gate thresholds
TOXIC_FLOW_THRESHOLD: float = -0.50    # r_B <= ini → tolak (flash crash / dump)
FAST_TRACK_TIER1: float = 0.65         # r_B >= ini (Tier-1) → Fast-Track BUY
FAST_TRACK_TIER2: float = 0.80         # r_B >= ini (Tier-2) → Fast-Track BUY
PROACTIVE_REVERSAL_THRESHOLD: float = -0.35  # r_B <= ini → percepat TP jika profit
MIN_CROSS_CORR: float = 0.50           # Minimum ρ agar τ* dianggap valid
MIN_LAG_MS: int = 100                  # Lag minimal agar cross-corr diakui meaningful


# ---------------------------------------------------------------------------
# FORMULA 1 — Binance Return Signal
# ---------------------------------------------------------------------------

def compute_binance_return(
    b_hist_raw: List[Tuple[float, float]],
    window_ms: float = DEFAULT_WINDOW_MS,
) -> Tuple[float, float]:
    """
    Hitung return persentase Binance dalam window waktu aktual W (default 400ms).

    Formula:
        r_B(t, W) = (P_B(t) - P_B(t - W)) / P_B(t - W) × 100%

    Window berbasis WAKTU (bukan jumlah tick), sehingga konsisten saat Binance
    update cepat (< 5ms antar tick) maupun lambat.

    Args:
        b_hist_raw : List of (timestamp_ms, price) tuples, urut paling lama → terbaru.
        window_ms  : Lookback window dalam milidetik (default 400ms).

    Returns:
        (r_B, actual_window_ms) — return persen dan panjang window aktual terukur.
        Mengembalikan (0.0, 0.0) jika data tidak cukup.
    """
    if len(b_hist_raw) < 2:
        return 0.0, 0.0

    now_ms = time.time() * 1000
    b_last_ts, b_last = b_hist_raw[-1]

    # Cari tick paling lama yang masih dalam window_ms dari sekarang
    b_ref_ts, b_ref = b_hist_raw[0]  # fallback: tick tertua
    for entry_ts, entry_price in b_hist_raw:
        if now_ms - entry_ts <= window_ms:
            b_ref_ts, b_ref = entry_ts, entry_price
            break

    actual_window_ms = b_last_ts - b_ref_ts
    if b_ref <= 0 or actual_window_ms < 0:
        return 0.0, 0.0

    r_b = (b_last - b_ref) / b_ref * 100.0
    return r_b, actual_window_ms


# ---------------------------------------------------------------------------
# FORMULA 2 — Cross-Correlation τ* (Optimal Lead Estimation)
# ---------------------------------------------------------------------------

def compute_cross_correlation(
    b_ticks: List[Tuple[float, float]],
    i_ticks: List[Tuple[float, float]],
    max_lag_ms: int = DEFAULT_MAX_LAG_MS,
    grid_ms: int = DEFAULT_GRID_MS,
) -> Dict:
    """
    Hitung cross-correlation Binance vs Indodax untuk menemukan optimal lag τ*.

    Formula:
        ρ(τ) = Corr[P̂_B(t), P̂_I(t + τ)]
        τ*   = argmax_{τ ∈ [-T, T]} ρ(τ)

    Di mana P̂ = return ternormalisasi dari harga awal sesi:
        P̂(t) = (P(t) - P(t₀)) / P(t₀) × 100%

    Series di-resample ke grid reguler dengan forward-fill (last-tick-carry),
    mensimulasikan harga terakhir yang diketahui.

    Args:
        b_ticks    : List (timestamp_ms, price) Binance.
        i_ticks    : List (timestamp_ms, price) Indodax.
        max_lag_ms : Range pengujian lag [-max_lag_ms, +max_lag_ms].
        grid_ms    : Interval resample dalam ms.

    Returns:
        Dict dengan:
          "tau_star_ms"  : int  — optimal lag dalam ms (positif = Binance leads)
          "peak_corr"    : float — korelasi maksimum ρ(τ*)
          "is_valid"     : bool — True jika τ* >= MIN_LAG_MS dan ρ >= MIN_CROSS_CORR
          "profile"      : List[(lag_ms, corr)] — profil korrelasi lengkap
    """
    if len(b_ticks) < 20 or len(i_ticks) < 20:
        return {"tau_star_ms": 0, "peak_corr": 0.0, "is_valid": False, "profile": []}

    # Susun sebagai arrays (timestamp_ms, price)
    b_arr = np.array(b_ticks, dtype=float)
    i_arr = np.array(i_ticks, dtype=float)

    # Overlap window
    t_start = max(b_arr[:, 0].min(), i_arr[:, 0].min())
    t_end   = min(b_arr[:, 0].max(), i_arr[:, 0].max())
    if t_end - t_start < grid_ms * 20:
        return {"tau_star_ms": 0, "peak_corr": 0.0, "is_valid": False, "profile": []}

    # Bangun grid reguler
    grid = np.arange(t_start, t_end, grid_ms, dtype=float)

    # Forward-fill (last observation carried forward)
    def ffill(arr_sorted: np.ndarray, grid_pts: np.ndarray) -> np.ndarray:
        prices = np.full(len(grid_pts), np.nan)
        j = 0
        n = len(arr_sorted)
        for i, t in enumerate(grid_pts):
            while j < n - 1 and arr_sorted[j + 1, 0] <= t:
                j += 1
            if arr_sorted[j, 0] <= t:
                prices[i] = arr_sorted[j, 1]
        return prices

    # Urutkan dan ffill
    b_sorted = b_arr[b_arr[:, 0].argsort()]
    i_sorted = i_arr[i_arr[:, 0].argsort()]

    p_b = ffill(b_sorted, grid)
    p_i = ffill(i_sorted, grid)

    # Masker valid (keduanya tidak NaN)
    valid = ~(np.isnan(p_b) | np.isnan(p_i))
    p_b, p_i = p_b[valid], p_i[valid]

    if len(p_b) < 50:
        return {"tau_star_ms": 0, "peak_corr": 0.0, "is_valid": False, "profile": []}

    # Normalisasi: % return dari titik awal
    norm_b = (p_b - p_b[0]) / p_b[0] * 100.0
    norm_i = (p_i - p_i[0]) / p_i[0] * 100.0

    # Guard: jika salah satu series flat (stddev=0), corrcoef akan divide-by-zero
    if norm_b.std() < 1e-10 or norm_i.std() < 1e-10:
        return {"tau_star_ms": 0, "peak_corr": 0.0, "is_valid": False, "profile": []}

    # Helper: hitung corrcoef dengan guard stddev per-slice
    def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
        if len(a) < 2 or a.std() < 1e-10 or b.std() < 1e-10:
            return 0.0
        with np.errstate(invalid='ignore', divide='ignore'):
            r = np.corrcoef(a, b)[0, 1]
        return 0.0 if np.isnan(r) else float(r)

    # Cross-correlation via lag shift
    steps_per_ms = 1.0 / grid_ms
    lag_range = range(-max_lag_ms, max_lag_ms + 1, grid_ms)
    profile: List[Tuple[int, float]] = []

    for lag_ms in lag_range:
        shift = int(round(lag_ms * steps_per_ms))
        if shift == 0:
            corr = _safe_corr(norm_b, norm_i)
        elif shift > 0:
            if shift >= len(norm_b):
                profile.append((lag_ms, 0.0))
                continue
            corr = _safe_corr(norm_b[shift:], norm_i[:-shift])
        else:
            s = -shift
            if s >= len(norm_b):
                profile.append((lag_ms, 0.0))
                continue
            corr = _safe_corr(norm_b[:-s], norm_i[s:])

        profile.append((lag_ms, corr))

    if not profile:
        return {"tau_star_ms": 0, "peak_corr": 0.0, "is_valid": False, "profile": []}

    best = max(profile, key=lambda x: x[1])
    tau_star, peak_corr = best

    is_valid = (tau_star >= MIN_LAG_MS) and (peak_corr >= MIN_CROSS_CORR)

    return {
        "tau_star_ms": tau_star,
        "peak_corr": peak_corr,
        "is_valid": is_valid,
        "profile": profile,
    }


# ---------------------------------------------------------------------------
# FORMULA 3 — Implied IDR Rate & Spread
# ---------------------------------------------------------------------------

def compute_implied_spread(
    indodax_price: float,
    binance_price_usdt: float,
    usdt_idr_rate: float = USDT_IDR_FALLBACK,
) -> Tuple[float, float]:
    """
    Hitung implied IDR rate dan spread antara Indodax vs Binance (setelah konversi kurs).

    Formula:
        ImpliedRate = P_I_IDR / P_B_USDT
        Spread%     = (ImpliedRate - USDT_IDR) / USDT_IDR × 100%

    Interpretasi:
        Spread% > 0  → Indodax premium (lebih mahal dari Binance)
        Spread% < 0  → Indodax diskon (lebih murah dari Binance)
        |Spread%| besar → peluang konvergensi harga

    Returns:
        (implied_rate, spread_pct). Mengembalikan (0.0, 0.0) jika input tidak valid.
    """
    if indodax_price <= 0 or binance_price_usdt <= 0 or usdt_idr_rate <= 0:
        return 0.0, 0.0

    implied_rate = indodax_price / binance_price_usdt
    spread_pct = (implied_rate - usdt_idr_rate) / usdt_idr_rate * 100.0
    return implied_rate, spread_pct


# ---------------------------------------------------------------------------
# FORMULA 5 — Fast-Track Confidence Scaling
# ---------------------------------------------------------------------------

def compute_fasttrack_confidence(
    r_b: float,
    threshold: float,
    min_conf: float = 75.0,
    max_conf: float = 95.0,
) -> float:
    """
    Hitung confidence Fast-Track berdasarkan besarnya return Binance di atas threshold.

    Formula:
        conf = clip(75 + (r_B - θ) × 20,  75%, 95%)

    Semakin besar r_B melebihi θ, semakin tinggi confidence (capped di 95%).
    """
    raw = min_conf + (r_b - threshold) * 20.0
    return max(min_conf, min(max_conf, raw))


# ---------------------------------------------------------------------------
# FORMULA 6 — Profitability Guard
# ---------------------------------------------------------------------------

def estimate_net_pnl(
    r_indodax_pct: float,
    fee_taker: float = DEFAULT_FEE_TAKER,
) -> float:
    """
    Estimasi net PnL setelah fee taker Indodax (double-sided: buy + sell).

    Formula:
        NetPnL = r_I(τ*) - 2 × fee_taker

    Signal valid jika NetPnL > 0, artinya r_I > 1.02%.

    Args:
        r_indodax_pct : Expected return Indodax dalam persen (misal 1.5 → 1.5%).
        fee_taker     : Biaya taker per sisi (default 0.0051 = 0.51%).

    Returns:
        Net PnL dalam persen (negatif = rugi setelah fee).
    """
    return (r_indodax_pct / 100.0) - 2.0 * fee_taker


def is_trade_profitable(
    r_indodax_pct: float,
    fee_taker: float = DEFAULT_FEE_TAKER,
) -> bool:
    """Return True jika expected return Indodax melampaui break-even 2× fee."""
    return estimate_net_pnl(r_indodax_pct, fee_taker) > 0.0


# ---------------------------------------------------------------------------
# COMBINED EVALUATION — Lead-Lag Gate Result
# ---------------------------------------------------------------------------

class LeadLagResult:
    """Container untuk hasil evaluasi gate Binance lead-lag."""

    def __init__(
        self,
        r_b: float,
        actual_window_ms: float,
        implied_rate: float,
        spread_pct: float,
        tau_star_ms: int,
        peak_corr: float,
        signal: str,           # "TOXIC", "FAST_TRACK", "NEUTRAL", "REVERSAL", "NO_DATA"
        confidence: float,
        reason: str,
    ):
        self.r_b = r_b
        self.actual_window_ms = actual_window_ms
        self.implied_rate = implied_rate
        self.spread_pct = spread_pct
        self.tau_star_ms = tau_star_ms
        self.peak_corr = peak_corr
        self.signal = signal
        self.confidence = confidence
        self.reason = reason

    def is_toxic(self) -> bool:
        return self.signal == "TOXIC"

    def is_fast_track(self) -> bool:
        return self.signal == "FAST_TRACK"

    def is_reversal(self) -> bool:
        return self.signal == "REVERSAL"

    def to_log_str(self, pair: str) -> str:
        return (
            f"[LEAD-LAG] {pair} | r_B={self.r_b:+.3f}% in {self.actual_window_ms:.0f}ms | "
            f"spread={self.spread_pct:+.2f}% | τ*={self.tau_star_ms}ms ρ={self.peak_corr:.3f} | "
            f"signal={self.signal} conf={self.confidence:.1f}%"
        )


def evaluate_lead_lag(
    b_hist_raw: List[Tuple[float, float]],
    indodax_price: float,
    usdt_idr_rate: float = USDT_IDR_FALLBACK,
    tier: int = 3,
    window_ms: float = DEFAULT_WINDOW_MS,
    tau_star_ms: int = 0,
    peak_corr: float = 0.0,
) -> LeadLagResult:
    """
    Evaluasi lengkap semua formula lead-lag dan kembalikan sinyal terstruktur.

    Args:
        b_hist_raw      : Buffer Binance — List[(timestamp_ms, price)].
        indodax_price   : Harga last Indodax (IDR).
        usdt_idr_rate   : Kurs USDT/IDR live (dari ticker 'usdtidr').
        tier            : 1=Tier-1 pairs, 2=Tier-2, 3=Domestik (bypass).
        window_ms       : Lookback window Binance (default 400ms).
        tau_star_ms     : Optimal lag dari kalibrasi cross-correlation (0 = tidak tersedia).
        peak_corr       : Peak correlation ρ(τ*) terkait tau_star_ms.

    Returns:
        LeadLagResult dengan signal dan confidence.
    """
    NO_DATA = LeadLagResult(
        r_b=0.0, actual_window_ms=0.0,
        implied_rate=0.0, spread_pct=0.0,
        tau_star_ms=tau_star_ms, peak_corr=peak_corr,
        signal="NO_DATA", confidence=0.0, reason="Insufficient Binance history",
    )

    if not b_hist_raw or len(b_hist_raw) < 2:
        return NO_DATA

    # Formula 1: Binance return
    r_b, actual_window_ms = compute_binance_return(b_hist_raw, window_ms)

    # Formula 3: Spread (dari harga Binance terbaru)
    b_last_price = b_hist_raw[-1][1]
    implied_rate, spread_pct = compute_implied_spread(indodax_price, b_last_price, usdt_idr_rate)

    # Formula 4: Gate thresholds berdasarkan tier
    fast_track_threshold = FAST_TRACK_TIER1 if tier == 1 else FAST_TRACK_TIER2

    # --- TOXIC FLOW guard (Formula 4a) ---
    if r_b <= TOXIC_FLOW_THRESHOLD:
        return LeadLagResult(
            r_b=r_b, actual_window_ms=actual_window_ms,
            implied_rate=implied_rate, spread_pct=spread_pct,
            tau_star_ms=tau_star_ms, peak_corr=peak_corr,
            signal="TOXIC",
            confidence=0.0,
            reason=f"Toxic-flow: r_B={r_b:+.3f}% <= {TOXIC_FLOW_THRESHOLD}%",
        )

    # --- PROACTIVE REVERSAL (Formula 4c) ---
    if r_b <= PROACTIVE_REVERSAL_THRESHOLD:
        return LeadLagResult(
            r_b=r_b, actual_window_ms=actual_window_ms,
            implied_rate=implied_rate, spread_pct=spread_pct,
            tau_star_ms=tau_star_ms, peak_corr=peak_corr,
            signal="REVERSAL",
            confidence=0.0,
            reason=f"Proactive reversal: r_B={r_b:+.3f}% <= {PROACTIVE_REVERSAL_THRESHOLD}%",
        )

    # --- FAST-TRACK BREAKOUT (Formula 4b) ---
    if r_b >= fast_track_threshold:
        # Formula 5: confidence scaling
        conf = compute_fasttrack_confidence(r_b, fast_track_threshold)
        return LeadLagResult(
            r_b=r_b, actual_window_ms=actual_window_ms,
            implied_rate=implied_rate, spread_pct=spread_pct,
            tau_star_ms=tau_star_ms, peak_corr=peak_corr,
            signal="FAST_TRACK",
            confidence=conf,
            reason=(
                f"Fast-track breakout: r_B={r_b:+.3f}% >= θ={fast_track_threshold}% "
                f"(Tier {tier}), spread={spread_pct:+.2f}%"
            ),
        )

    # Neutral — tidak ada sinyal khusus dari Binance
    return LeadLagResult(
        r_b=r_b, actual_window_ms=actual_window_ms,
        implied_rate=implied_rate, spread_pct=spread_pct,
        tau_star_ms=tau_star_ms, peak_corr=peak_corr,
        signal="NEUTRAL",
        confidence=0.0,
        reason=f"Neutral: r_B={r_b:+.3f}%, no gate triggered",
    )
