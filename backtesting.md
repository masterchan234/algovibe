# 🔬 Algovibe Backtester & Automated Tuning Operations Manual (v2.1)

Dokumentasi ini menjelaskan arsitektur, mode operasi, cara menjalankan, serta cara membaca laporan rekomendasi penalaan parameter (**Automated Parameter Tuning Advisor**) pada Algovibe Backtester.

---

## 🗺️ Gambaran Umum & Sinkronisasi Mutlak (Exact Parity)

Backtester Algovibe dirancang untuk melakukan simulasi perdagangan historis selama **90 hari ke belakang** menggunakan data candlestick 15 menit (`15m`) langsung dari bursa Indodax. 

Untuk memastikan hasil pengujian **100% akurat dan bebas dari bias atau halusinasi matematika**, backtester ini menggunakan prinsip **Exact Parity**:
1.  **Re-use Engine Produksi**: Backtester menggunakan baris kode indikator dan pembobotan yang sama dengan bot live melalui modul [technicals.py](file:///home/chan/projects/algovibe/app/domain/technicals.py).
2.  **Pencegahan Bias Masa Depan (Look-Ahead Bias)**: Data historis disajikan dalam bentuk potongan rolling sekuensial (`iloc[:i+1]`), menyamai aliran data in-memory cache real-time WebSocket bot.
3.  **ATR Volatility Exits**: Target Take Profit (TP) dan Stop Loss (SL) dihitung dinamis menggunakan volatilitas ATR bawaan bot, menghindari bias persentase flat yang kaku.

---

## ⚙️ Mode Strategi Backtest (`BACKTEST_MODE`)

Di dalam file [backtester.py](file:///home/chan/projects/algovibe/backtester.py), Anda dapat dengan mudah berpindah di antara dua mode strategi melalui konstanta `BACKTEST_MODE`:

### 1. Mode `BOT_CONFLUENCE` (Rekomendasi Utama)
Menggunakan keputusan murni dari **11 Indikator Teknis Berbobot** milik engine bot live:
*   **Indikator Terlibat**: RSI-14, MACD Histogram, Bollinger %B, Stochastic RSI, Williams %R, ROC-10, Volume Ratio, OBV Slope, dll.
*   **Syarat Masuk (Entry)**: Pemicu BUY aktif hanya jika skor Confluence Beli $\ge 6.0$ dan skor lawan (SELL) $< 4.0$ (ditentukan oleh `derive_trade_signal`).
*   **Tujuan**: Mengetahui kinerja murni bot sebelum diterjunkan ke akun Live atau Dry Run riil.

### 2. Mode `PULLBACK` (Trend Mean-Reversion)
Strategi khusus pullback tren naik makro:
*   **Syarat Masuk (Entry)**: Membaca kondisi pullback di bawah EMA-20 saat tren makro sedang bullish (`EMA-20 > EMA-50`), terkonfirmasi oleh pola candlestick pembalikan (*reversal candle*) dan volume perdagangan yang cukup.

---

## 🚀 Panduan Eksekusi & Operasi

### 1. Menjalankan Backtest Seluruh Pasar
Untuk menjalankan simulasi backtest pada seluruh koin aktif IDR yang tersedia di Indodax (sekitar 340+ koin yang lolos filter volume), eksekusi perintah shell berikut di direktori utama project:
```bash
./run_backtest.sh
```

### 2. Memantau Proses Simulasi Secara Real-Time
Proses backtest berjalan di latar belakang di dalam container `algovibe-executive-core`. Untuk melihat progres pengerjaan koin demi koin, jalankan perintah:
```bash
docker compose logs -f executive-core
```

### 3. Melihat Laporan Kinerja Sementara
Jika Anda tidak ingin menunggu seluruh koin selesai diproses dan ingin melihat rangkuman kinerja sementara dari koin-koin yang sudah selesai disimulasikan, Anda dapat memicu Laporan Tuning Advisor kapan saja dengan perintah:
```bash
docker exec -it algovibe-executive-core python3 backtest_report.py
```

---

## 📈 Cara Membaca Laporan Automated Tuning Advisor

Ketika backtest selesai, script [backtest_report.py](file:///home/chan/projects/algovibe/backtest_report.py) akan otomatis menyajikan laporan visual yang rapi di terminal Anda:

### 1. Indikator Kinerja Utama (KPI)
*   **Win Rate (%)**: Rasio jumlah trade profit dibanding total trade. Target ideal scalping adalah di atas $45\%$ dengan pengali ATR yang tepat.
*   **Profit Factor (x)**: Total Gross Profit dibagi total Gross Loss. Nilai $> 1.1\text{x}$ menunjukkan strategi yang menguntungkan secara keseluruhan, sedangkan $> 1.5\text{x}$ dinilai sebagai performa yang sangat prima.
*   **Average Return/Trade**: Ekspektasi keuntungan rata-rata per transaksi dalam persentase net (sudah dikurangi estimasi komisi fee bursa round-trip $0.51\%$).

### 2. Penalaan Parameter Otomatis (Tuning Advice)
Laporan ini memberikan 3 saran aksi kuantitatif konkret:
*   **ASSET BLACKLIST**: Mendeteksi otomatis koin-koin "bocor" (merugikan secara konsisten) selama 90 hari terakhir. 
    > **Aksi**: Salin daftar koin yang disarankan dan tempel ke konstanta `DEFAULT_BLACKLIST_COINS` di [config.py](file:///home/chan/projects/algovibe/config.py) untuk menghentikan kerugian di perdagangan live.
*   **RISK-TO-REWARD OPTIMIZATION**: Membandingkan rata-rata persentase kemenangan dengan rata-rata kerugian.
    > **Aksi**: Jika rata-rata kerugian terlalu besar dibanding rata-rata profit, advisor akan menyarankan untuk mempererat Stop Loss (SL) atau melonggarkan Take Profit (TP) berbasis ATR multiplier di [backtester.py](file:///home/chan/projects/algovibe/backtester.py).
*   **CONFLUENCE ENGINE SENSITIVITY**: Mendiagnosis tingkat kepekaan pemicu confluence.
    > **Aksi**: Jika Win Rate terlalu rendah, direkomendasikan untuk menaikkan `BUY_THRESHOLD` (misal dari `6.0` ke `7.0`) pada file [technicals.py](file:///home/chan/projects/algovibe/app/domain/technicals.py) agar bot mengabaikan sinyal palsu (*market noise*).

---

## 🗄️ Struktur Penyimpanan di Database

Hasil simulasi disimpan secara terisolasi pada database **TimescaleDB** (`algovibe_dryrun`) di tabel berikut:
*   `backtest_history`: Menyimpan riwayat transaksi simulasi (harga masuk, harga keluar, waktu, PnL IDR, PnL %, dan durasi).
*   `backtest_analytics`: Menyimpan rekaman DNA indikator pada saat entry dipicu. Data DNA inilah yang dibaca secara real-time oleh algoritma **K-Nearest Neighbors (K-NN)** bot live untuk melakukan klasifikasi pola kemenangan.
