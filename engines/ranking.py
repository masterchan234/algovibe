import multiprocessing

def calculate_score(ticker_data):
    """Fungsi ini akan dijalankan secara paralel di 6 vCPU"""
    pair, data = ticker_data
    try:
        buy = float(data['buy'])
        sell = float(data['sell'])
        high = float(data['high'])
        low = float(data['low'])
        vol = float(data['vol_idr']) # Menggunakan volume dalam IDR untuk standarisasi

        # Kedalaman Order Book
        bid_depth = float(data.get('bid_depth', 0))
        ask_depth = float(data.get('ask_depth', 0))

        # 1. Perhitungan SPREAD_PERCENT
        spread_pct = (sell - buy) / sell * 100
        
        # 2. Perhitungan VOLATILITY_PERCENT
        volatility_pct = (high - low) / low * 100
        
        # 3. Kalkulasi Imbalance Ratio
        imbalance = (bid_depth / ask_depth) if ask_depth > 0 else 0

        # Filter Awal + Fake Pump/Bandar Filter
        if spread_pct < 0.5 and 0.3 <= volatility_pct <= 6.0 and vol > 100_000_000:
            if bid_depth > 100_000_000 and imbalance > 1.5:
                # Perhitungan Ranking Score
                score = (vol * 0.5) + (volatility_pct * 0.3) - (spread_pct * 0.2) + (imbalance * 100_000_000)
                
                return {
                    "pair": pair,
                    "spread": spread_pct,
                    "volatility": volatility_pct,
                    "score": score,
                    "last_price": float(data['last']),
                    "imbalance": imbalance,
                    "bid_depth": bid_depth
                }
    except (ZeroDivisionError, KeyError, ValueError):
        pass
    return None

def run_ranking_engine(tickers):
    # Gunakan semua 6 vCPU
    with multiprocessing.Pool(processes=6) as pool:
        results = pool.map(calculate_score, tickers.items())
    
    # Filter hasil yang None dan urutkan berdasarkan Score tertinggi
    ranked = [r for r in results if r is not None]
    ranked.sort(key=lambda x: x['score'], reverse=True)
    
    return ranked[:10] # Ambil Top 10 PRIMARY_SCALPING_PAIRS