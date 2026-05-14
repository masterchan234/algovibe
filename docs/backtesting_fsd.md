# Functional Specification Document (FSD): Backtesting Engine

## 1. Overview
Algovibe Backtesting Engine adalah sistem simulasi perdagangan kuantitatif berkinerja tinggi yang dirancang untuk memvalidasi strategi scalping dan trend-pullback pada data historis Indodax. Sistem ini menjamin paritas keputusan 1:1 dengan bot produksi.

## 2. Arsitektur Teknis
### 2.1. Vectorized Simulation
- **Engine**: Menggunakan NumPy dan Pandas untuk kalkulasi sinyal secara vektor.
- **Data Source**: TimescaleDB (`ohlcv_1m`).
- **Parallelism**: Menggunakan `ProcessPoolExecutor` (Python Multi-processing) untuk mendistribusikan beban simulasi pair ke seluruh core CPU yang tersedia.

### 2.2. Kepastian Paritas (Fidelity)
- Menggunakan fungsi `derive_trade_signal` yang sama dengan bot live.
- Mensimulasikan *bid/ask spread* dan *slippage* secara realistis.
- Perhitungan biaya transaksi (Taker/Maker fee) sesuai struktur biaya Indodax.

## 3. Fitur Utama
### 3.1. Multi-Profile Management
Sistem mendukung beberapa profil strategi secara bersamaan. Setiap profil memiliki parameter unik seperti:
- Timeframe (1m, 5m, dst).
- Threshold Buy/Sell.
- Pengaturan DCA (Dollar Cost Averaging).
- Target Take Profit & Stop Loss.

### 3.2. Evolutionary Optimization (Auto-Tuning)
Sistem dapat melakukan perbaikan mandiri melalui perintah `optimize`.
- **Logic**: Menggunakan teori probabilitas kuantitatif untuk mutasi parameter.
- **Versioning**: Setiap iterasi menciptakan versi profil baru (v1, v2, v3) untuk pelacakan performa.
- **Early Exit (Daily Target Aware)**: Bot berhenti melakukan optimasi jika target **Profit Rata-rata Harian** (`target_pnl_idr`) atau **ROI Rata-rata Harian** (`target_roi_pct`) tercapai.

## 4. Metrik Kuantitatif
Laporan performa mencakup metrik standar industri:
- **Net PnL & ROI**: Keuntungan bersih dalam IDR dan persentase.
- **Win Rate**: Persentase transaksi yang menguntungkan.
- **Profit Factor**: Rasio kotor profit terhadap kerugian.
- **Max Drawdown (MDD)**: Penurunan saldo terbesar dari titik puncak.
- **Sharpe & Sortino Ratio**: Efisiensi return terhadap risiko.
- **Expectancy**: Estimasi profit rata-rata per transaksi.

## 5. Alur Kerja (Workflow)
1. **Fetch**: Mengambil data OHLCV dari exchange.
2. **Run**: Menjalankan simulasi global portfolio.
3. **Report**: Menghasilkan laporan kuantitatif dan tabel perbandingan.
4. **Tune**: (Opsional) Melakukan mutasi parameter jika hasil belum mencapai target.
5. **Optimize**: Menjalankan loop (Run -> Report -> Tune) secara rekursif.

## 6. Struktur Database
- `backtest_profiles`: Konfigurasi strategi dan target.
- `backtest_results`: Statistik agregat per sesi backtest.
- `backtest_trades`: Detail transaksi per eksekusi.
