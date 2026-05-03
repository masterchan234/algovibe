import math

# Sektor yang mendapat sector-momentum boost saat pumping
_BOOSTED_SECTORS = {"AI", "MEME", "L1"}


def calculate_score(args):
    """Fungsi pembantu untuk multiprocessing ranking.

    args dapat berupa (item, config_override) atau
    (item, config_override, market_regime) untuk bobot dinamis berbasis regime.
    """
    if len(args) == 3:
        item, config_override, market_regime = args
    else:
        item, config_override = args
        market_regime = "UNKNOWN"

    market_regime = (market_regime or "UNKNOWN").upper()
    pair, d = item
    try:
        last = float(d['last'])
        high_24h = float(d.get('high', last))
        low_24h = float(d.get('low', last))
        vol = float(d.get('vol_idr', 0))

        # Spread & Volatility
        bid = float(d.get('bid', last * 0.999))
        ask = float(d.get('ask', last * 1.001))
        spread = ((ask - bid) / last) * 100
        volatility = ((high_24h - low_24h) / last) * 100

        # Depth Imbalance (jika ada data OB)
        bid_depth = float(d.get('bid_depth', 0))
        ask_depth = float(d.get('ask_depth', 0))
        imbalance = (bid_depth / ask_depth) if ask_depth > 0 else 1.0

        max_spread = config_override['max_spread'] * 100
        min_vol = config_override['min_vol']
        min_price = config_override.get('min_price', 0)

        # [Anti-Shitcoin] Filter koin harga receh (1-10 perak)
        if last < min_price:
            return None

        # [Senior Engineer Fix] Lepaskan batas atas volatilitas untuk Dashboard Ranking.
        if spread < max_spread and volatility >= 0.1 and vol > min_vol:
            # [Smart Thinking] Gunakan Volatility-Adjusted Change.
            change_24h = d.get('change_24h', 0)
            rel_momentum = (abs(change_24h) * 5) / max(0.5, volatility)

            # 1. Volume Power (Logarithmic scaling)
            log_vol = math.log10(vol) if vol > 0 else 0
            score_vol = max(0, (log_vol - 7) * 8)

            # 2. Spread Penalty (Makin besar spread, makin tidak likuid)
            penalty_spread = spread * 12

            # --- Dynamic Regime Weights ---
            # BULLISH: kejar momentum dan volume – spread lebih ditoleransi
            # CHOPPY/BEARISH: prioritaskan likuiditas (spread tipis & bid depth kuat) untuk hindari jebakan
            if market_regime == "BULLISH":
                w_vol = 1.3       # Volume lebih penting (momentum market)
                w_momentum = 14   # Momentum dikuatkan
                w_spread = 8      # Spread diperlonggar sedikit
                bid_depth_bonus = 0.0
            elif market_regime in ("BEARISH", "CHOPPY", "CRASH"):
                w_vol = 0.8       # Volume kurang relevan saat pasar bearish
                w_momentum = 7    # Momentum dikurangi (banyak false breakout)
                w_spread = 18     # Spread dihukum lebih keras (likuiditas sangat penting)
                # Bid-depth bonus: apresiasi koin dengan buy wall kuat (ordebook menahan turun)
                bid_depth_bonus = (imbalance - 1.0) * 3.0 if imbalance > 1.0 else 0.0
            else:  # RANGING / UNKNOWN
                w_vol = 1.0
                w_momentum = 10
                w_spread = 12
                bid_depth_bonus = 0.0

            # Final Score (Normalized roughly to 0-100+)
            score = (score_vol * w_vol) + (rel_momentum * w_momentum) - (penalty_spread * (w_spread / 12)) + bid_depth_bonus

            # --- Sector Momentum Boost (+15% untuk sektor AI, MEME, L1 yang sedang pumping) ---
            try:
                from config import SECTOR_MAP
                sector = SECTOR_MAP.get(pair.lower())
                if sector in _BOOSTED_SECTORS:
                    # Hanya boost jika sektornya positif (change > 0) agar tidak boost koin rugi
                    if change_24h > 0:
                        score *= 1.15
            except Exception:
                pass

            return {
                'pair': pair,
                'last': last,
                'spread': spread,
                'volatility': volatility,
                'score': score,
                'imbalance': imbalance,
                'change': change_24h,
                'vol_idr': vol,
            }
    except Exception:
        pass

    return None
