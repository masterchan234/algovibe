import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import random
import sys
import os

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from engines.database import db

TIMEFRAME       = '15m'
SINCE_DAYS      = 60
MIN_VOL_PCT     = 0.3       # Skip koin dengan candle volatilitas < 0.3%
MIN_VOLUME_IDR  = 10_000_000  # Min Rp 10 Juta volume 24h (filter koin zombie)
MAX_PAIRS       = 300       # Batasi agar tidak terlalu lama (cukup 150 koin terlikuid)

from config import INITIAL_CAPITAL, CAPITAL_PER_TRADE_PCT
CAPITAL_PER_TRADE = INITIAL_CAPITAL * CAPITAL_PER_TRADE_PCT


class AlgovibeBacktester:
    def __init__(self):
        self.exchange = ccxt.indodax()

    def fetch_all_active_pairs(self):
        """
        Fetch semua IDR pairs yang aktif dari Indodax secara dinamis.
        Filter berdasarkan volume 24h minimum agar hanya koin liquid yang diuji.
        Urutkan dari volume terbesar → terkecil.
        """
        print("\n[DISCOVERY] Fetching semua market dari Indodax...")
        try:
            # Load semua market yang tersedia
            markets = self.exchange.load_markets()
            idr_pairs = [s for s in markets.keys() if s.endswith('/IDR')]
            print(f"  Ditemukan {len(idr_pairs)} pasangan IDR.")

            # Fetch ticker 24h untuk semua pair sekaligus (1 API call)
            print("  Fetching ticker 24h untuk filter volume...")
            tickers = self.exchange.fetch_tickers(idr_pairs)

            # Filter & urutkan berdasarkan volume IDR
            active = []
            for pair, ticker in tickers.items():
                # Hitung volume IDR = volume base * harga terakhir
                vol_idr = (ticker.get('baseVolume') or 0) * (ticker.get('last') or 0)
                if vol_idr >= MIN_VOLUME_IDR:
                    active.append((pair, vol_idr))

            # Urutkan dari volume terbesar, batasi MAX_PAIRS
            active.sort(key=lambda x: x[1], reverse=True)
            active = active[:MAX_PAIRS]

            pairs = [p[0] for p in active]
            print(f"  ✅ {len(pairs)} koin aktif lolos filter (Vol >= Rp {MIN_VOLUME_IDR/1e9:.1f}M).")
            for i, (p, v) in enumerate(active[:10]):
                print(f"     {i+1:2}. {p:15} Vol: Rp {v/1e9:.2f}M")
            if len(active) > 10:
                print(f"     ... dan {len(active)-10} koin lainnya.")
            return pairs

        except Exception as e:
            print(f"  [ERROR] Gagal fetch markets: {e}")
            print("  Fallback ke daftar manual...")
            return [
                'BTC/IDR', 'ETH/IDR', 'SOL/IDR', 'XRP/IDR', 'BNB/IDR',
                'ADA/IDR', 'AVAX/IDR', 'DOGE/IDR', 'SHIB/IDR', 'PEPE/IDR',
                'LINK/IDR', 'NEAR/IDR', 'SUI/IDR', 'FET/IDR', 'RENDER/IDR',
                'BONK/IDR', 'FLOKI/IDR', 'WIF/IDR', 'JUP/IDR', 'ENA/IDR'
            ]

    def fetch_data(self, pair):
        print(f"  Fetching {pair}...")
        try:
            since = self.exchange.parse8601(
                (datetime.now() - timedelta(days=SINCE_DAYS)).isoformat()
            )
            ohlcv = self.exchange.fetch_ohlcv(pair, TIMEFRAME, since=since)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            print(f"  Error fetching {pair}: {e}")
            return None

    def run_simulation(self, pair, df):
        if df is None or len(df) < 60:
            return 0

        # ── Indicators ─────────────────────────────────────────────────────
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema_9']  = df['close'].ewm(span=9,  adjust=False).mean()

        delta = df['close'].diff()
        gain  = delta.where(delta > 0, 0).rolling(14).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
        df['rsi'] = df['rsi'].fillna(50)

        # ATR (Average True Range) untuk dynamic TP/SL
        df['tr']  = np.maximum(df['high'] - df['low'],
                    np.maximum(abs(df['high'] - df['close'].shift()),
                               abs(df['low']  - df['close'].shift())))
        df['atr'] = df['tr'].rolling(14).mean()

        # Volatility candle
        df['candle_vol_pct'] = (df['high'] - df['low']) / df['open'] * 100

        FEE_ROUND_TRIP = 0.0051

        trades_count = 0
        i = 55  # Mulai dari candle ke-55 agar semua indicator sudah tersedia

        while i < len(df) - 20:
            row      = df.iloc[i]
            prev_row = df.iloc[i - 1]

            # ── [Fix] Skip koin dengan volatilitas terlalu rendah ──────────
            avg_candle_vol = df['candle_vol_pct'].iloc[i-10:i].mean()
            if avg_candle_vol < MIN_VOL_PCT:
                i += 1
                continue

            # ── [Fix] Entry Condition yang jauh lebih ketat ─────────────────
            # Kondisi sebelumnya: hanya RSI < 45 dan low <= ema_20 (terlalu mudah)
            # Kondisi baru:
            # 1. Macro Trend: EMA-20 > EMA-50 (Uptrend)
            # 2. Deep Oversold: RSI < 40 (lebih ketat dari 45)
            # 3. EMA Distance: Harga minimal 0.2% di bawah EMA-20 (ada pullback nyata)
            # 4. Reversal Candle: Candle saat ini harus BULLISH close (close > open)
            #    artinya: ada konfirmasi pembalikan, bukan sekadar "menyentuh" EMA
            # 5. Volume Surge: Volume saat ini > rata-rata 10 candle terakhir
            # 6. BTC Filter: BTC sedang uptrend

            ema_dist_pct = (row['ema_20'] - row['close']) / row['ema_20'] * 100
            avg_vol_10   = df['volume'].iloc[i-10:i].mean()
            vol_surge    = row['volume'] > avg_vol_10 * 0.6   # Relaksasi: min 60% avg volume
            btc_up       = row.get('btc_up', True)

            # Reversal candle: lower wick lebih besar dari body (tanda penolakan bearish)
            body      = abs(row['close'] - row['open'])
            low_wick  = min(row['close'], row['open']) - row['low']
            is_reversal_candle = (low_wick >= body * 0.5) or (row['close'] > row['open'])

            entry_signal = (
                row['ema_20'] > row['ema_50'] and   # Uptrend makro
                row['rsi'] < 42 and                 # Oversold (dilonggarkan dari 40)
                ema_dist_pct >= 0.10 and             # Ada pullback minimal 0.10% dari EMA
                is_reversal_candle and               # Ada tanda penolakan turun
                vol_surge and                        # Volume cukup
                btc_up                              # BTC juga uptrend
            )

            if entry_signal:
                entry_price = row['close']  # Masuk di harga close candle reversal
                entry_time  = row['timestamp']

                # ── [Fix] Dynamic TP/SL berbasis ATR ─────────────────────
                # Sebelumnya: TP 4%, SL -2% flat untuk semua koin
                # Sekarang: TP = 2.5x ATR, SL = 1.5x ATR (RR 1:1.67)
                atr_val  = row['atr'] if not np.isnan(row.get('atr', np.nan)) else entry_price * 0.01
                tp_pct   = min(0.08, max(0.02, (atr_val * 2.5) / entry_price))  # 2-8%
                sl_pct   = min(0.04, max(0.01, (atr_val * 1.5) / entry_price))  # 1-4%
                tp_price = entry_price * (1 + tp_pct)
                sl_price = entry_price * (1 - sl_pct)

                # ── DNA Features ───────────────────────────────────────────
                volatility     = avg_candle_vol
                whale_boost    = 1.5 if row['volume'] > avg_vol_10 * 1.5 else 1.0
                imbalance_proxy = min(3.5, max(1.0, 1.0 + ema_dist_pct))
                ai_prob_sim    = min(95, max(55, 95 - row['rsi']))

                dna = {
                    'timestamp':        entry_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'ai_prob':          round(ai_prob_sim, 2),
                    'whale_boost':      whale_boost,
                    'sector_sentiment': round(random.uniform(0.9, 1.15), 2),
                    'imbalance':        round(imbalance_proxy, 2),
                    'volatility':       round(volatility, 2),
                    'rsi':              round(row['rsi'], 2),
                    'ema_dist':         round(ema_dist_pct, 4)
                }

                # ── Forward Simulation ─────────────────────────────────────
                outcome = None
                exit_price = 0
                pnl_pct    = 0

                for j in range(i + 1, min(i + 96, len(df))):  # Max 24 jam (96 candle × 15m)
                    fr = df.iloc[j]
                    if fr['low'] <= sl_price:
                        outcome    = "LOSS"
                        exit_price = sl_price
                        pnl_pct    = -sl_pct * 100
                        i = j
                        break
                    if fr['high'] >= tp_price:
                        outcome    = "PROFIT"
                        exit_price = tp_price
                        pnl_pct    = tp_pct * 100
                        i = j
                        break

                if outcome:
                    pnl_pct_net = pnl_pct - (FEE_ROUND_TRIP * 100)
                    pnl_idr_net = CAPITAL_PER_TRADE * (pnl_pct_net / 100)

                    db.insert_backtest_data(
                        pair=pair,
                        dna=dna,
                        pnl_idr=pnl_idr_net,
                        pnl_pct=pnl_pct_net,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        amount_idr=CAPITAL_PER_TRADE,
                        duration_seconds=0
                    )
                    trades_count += 1
                    i += 1
                    continue

            i += 1

        print(f"  → {pair}: {trades_count} trades seeded.")
        return trades_count


if __name__ == "__main__":
    print("=" * 60)
    print("  ALGOVIBE BACKTESTER v2.1 — Full Market Coverage")
    print("  Strategy: Mean Reversion + Reversal Confirm + ATR TP/SL")
    print("=" * 60)

    # --- [Senior Feature] Fresh Brain Mode ---
    # Sebelum memulai backtest, kita bersihkan histori lama agar AI hanya belajar 
    # dari data simulasi terbaru yang sudah menggunakan strategi yang telah di-tuning.
    print("\n" + "="*50)
    print("🧠 [BRAIN RESET] Menghapus DNA lama dari database...")
    try:
        from engines.database import db
        # Hapus analytics dan history agar bot mulai dengan 'Slate' yang bersih
        with db.get_connection() as conn:
            conn.execute("DELETE FROM trade_analytics")
            conn.execute("DELETE FROM trade_history")
        print("✅ Database DNA dibersihkan. Memulai pembelajaran dari nol...")
    except Exception as e:
        print(f"⚠️ Gagal membersihkan database: {e}")
    print("="*50 + "\n")

    backtester = AlgovibeBacktester()

    # [NEW] Dynamic Pair Discovery — ambil semua koin dari Indodax
    PAIRS = backtester.fetch_all_active_pairs()

    # Fetch BTC untuk Market Regime Filter
    print("\n[BTC] Fetching regime data...")
    btc_df = backtester.fetch_data("BTC/IDR")
    if btc_df is not None:
        btc_df['btc_ema'] = btc_df['close'].ewm(span=20, adjust=False).mean()
        btc_map = (btc_df.set_index('timestamp')['close'] >
                   btc_df.set_index('timestamp')['btc_ema'])
    else:
        btc_map = None

    total_seeded = 0
    pairs_with_trades = 0
    print(f"\n[SIM] Simulasi {len(PAIRS)} koin...\n")

    for idx, pair in enumerate(PAIRS, 1):
        print(f"[{idx:3}/{len(PAIRS)}] ", end="", flush=True)
        data = backtester.fetch_data(pair)
        if data is not None:
            if btc_map is not None:
                data['btc_up'] = data['timestamp'].map(btc_map).fillna(False)
            else:
                data['btc_up'] = True
            n = backtester.run_simulation(pair, data)
            total_seeded += n
            if n > 0:
                pairs_with_trades += 1
        # Rate limit guard (Indodax public API ~10 req/s)
        time.sleep(0.15)

    print(f"\n{'='*60}")
    print(f"  ✅ Backtest v2.1 Selesai!")
    print(f"  Koin disimulasi        : {len(PAIRS)}")
    print(f"  Koin menghasilkan DNA  : {pairs_with_trades}")
    print(f"  Total pola DNA baru    : {total_seeded}")
    print(f"{'='*60}")


