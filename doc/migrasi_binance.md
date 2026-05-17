# Rencana Migrasi & Inventaris Overhaul Exchange: Indodax ke Binance Spot

Dokumen ini berisi rencana teknis komprehensif, pemetaan arsitektur, dan daftar inventaris file yang perlu dimodifikasi atau dibuat untuk memigrasikan bot scalping **AlgoVibe** dari **Indodax** ke **Binance Spot**.

---

## 1. Perbandingan Konseptual & Strategi Pemetaan

| Dimensi | Indodax (Sistem Lama) | Binance Spot (Sistem Baru) | Solusi & Pemetaan |
| :--- | :--- | :--- | :--- |
| **Mata Uang Kutipan (Quote)** | Rupiah (`IDR`) | Dollar Tether (`USDT`) / FDUSD | Mengubah default quote asset menjadi `USDT`. Semua parameter nominal disesuaikan dari Rupiah ke USDT. |
| **Format Simbol** | Lowercase, no slash (contoh: `btcidr`, `ethidr`) | Uppercase dengan slash (contoh: `BTC/USDT`) | Adapter akan melakukan normalisasi internal: `btcusdt` (di internal bot) $\leftrightarrow$ `BTC/USDT` (di CCXT/Exchange). |
| **Biaya Transaksi (Fee)** | Tinggi (~0.21% - 0.42% taker) | Rendah (0.1% standar, bisa turun dengan BNB/VIP) | Mengupdate `fee_structure` di adapter dan `config.py`. Ini sangat meningkatkan profitabilitas scalping bot. |
| **Minimum Pembelian** | Rendah (~Rp 12.000 / $0.75) | Batasan Binance Spot (biasanya $5.0 atau $10.0 USDT) | Mengubah `MIN_TRADE_IDR` di database/config menjadi `MIN_TRADE_USDT = 10.0`. |
| **WebSocket Publik** | `wss://ws3.indodax.com/ws/` | `wss://stream.binance.com:9443/ws` | Mengganti client WebSocket ke Binance Spot menggunakan stream gabungan (combined stream) atau stream tunggal (`!ticker@arr`, `<symbol>@depth10`, `<symbol>@trade`). |
| **WebSocket Privat** | Token dynamic + token endpoint | ListenKey (`POST /api/v3/userDataStream`) | Menggunakan endpoint UserDataStream Binance untuk generate dan refresh listenKey setiap 30 menit. |
| **Deadman Switch** | `/countdownCancelAll` (Native TAPI) | Tidak didukung secara native di Spot | Menonaktifkan Deadman Switch Indodax atau mengubahnya menjadi fallback safety loop berbasis software (membatalkan semua order terbuka jika bot terputus). |
| **Penyimpanan Database** | Menggunakan nama kolom berakhiran `_idr` (contoh: `amount_idr`, `pnl_idr`) | Konseptual diubah menjadi `USDT` | Untuk menjaga kompatibilitas database tanpa memicu migrasi skema destruktif, kolom `_idr` akan digunakan untuk menyimpan nilai riil dalam `USDT` (contoh: PnL $1.50 disimpan di kolom `pnl_idr` sebagai `1.50`). |

---

## 2. Inventaris File yang Perlu Diubah/Dibuat

Berikut adalah daftar file yang terpengaruh dalam microservice architecture AlgoVibe:

### A. Infrastructure & Adapters (`app/infrastructure/`)
1. **[NEW]** `app/infrastructure/providers/binance_adapter.py`
   - Mengimplementasikan `BaseExchangeAdapter`.
   - Mengintegrasikan CCXT Binance client untuk eksekusi order (`limit`/`market`), sinkronisasi balance, dan query data pasar.
   - Menyediakan `fee_structure` baru (Maker: 0.001, Taker: 0.001).
2. **[MODIFY]** `app/infrastructure/exchange_factory.py`
   - Mengimpor dan mendaftarkan `BinanceAdapter`.
   - Mengganti `create_indodax_exchange()` menjadi `create_binance_exchange()` (atau mengeneralisasikannya menjadi `create_exchange_client()` yang dynamic).
   - Memastikan `create_exchange_provider()` mendukung `"binance"`.
3. **[MODIFY]** `app/infrastructure/trading_bridge.py`
   - Mendaftarkan `"binance"` di registry adapter.
   - Menyesuaikan validasi pengaman (misal: penanganan balance tipis, deteksi error balance binance).
4. **[MODIFY]** `app/infrastructure/ws_market.py`
   - Mengubah URL WS ke `wss://stream.binance.com:9443/ws` atau `wss://stream.binance.com:9443/stream`.
   - Mengganti subscription ke channel Binance (`!ticker@arr`, `<symbol>@depth10`, `<symbol>@trade`).
   - Menyesuaikan parser JSON sesuai format Binance ticker array (`!ticker@arr`) dan depth (`@depth10`).
5. **[MODIFY]** `app/infrastructure/ws_private.py`
   - Mengubah mekanisme handshake dengan generate `listenKey` via REST API Binance.
   - Mengkoneksikan WebSocket privat ke stream `listenKey`.
   - Mengimplementasikan background keep-alive thread untuk mengirim `PUT` request setiap 30 menit.
   - Menguraikan `executionReport` Binance untuk memicu callback order updates.
6. **[MODIFY]** `app/infrastructure/deadman.py`
   - Menonaktifkan atau mengganti native Indodax countdown switch dengan soft-safety loop (manual cancel-all) karena Binance Spot tidak memiliki native countdown cancel.

### B. Core Strategy & Configuration (`config.py` & `app/application/`)
7. **[MODIFY]** `config.py`
   - Menyesuaikan nominal trading untuk **USDT**:
     - `INITIAL_CAPITAL = 1000.0` (Tether)
     - `MIN_TRADE_IDR` $\rightarrow$ `MIN_TRADE_USDT = 10.0`
     - `MIN_VOLUME_FILTER = 30000.0` (Volume 24 jam minimal $30k)
     - `WHALE_THRESHOLD_IDR = 20000.0`
     - `VOLUME_RELIABILITY_THRESHOLD = 15000.0`
     - `BASKET_HARVEST_TARGET_IDR = 20.0` (Target profit $20)
     - `MOMENTUM_TARGET_PNL_IDR = 20.0`
     - `NET_ZERO_PNL_FLOOR_IDR = -150.0` (Batas stop out rugi -$150)
   - Memperbarui konstanta biaya transaksi (Fee) Binance Spot (Buy/Sell Maker: 0.1%, Taker: 0.1%).
   - Memperbarui `SECTOR_MAP` dengan koin-koin Spot Binance berlikuiditas tinggi berpasangan dengan USDT (contoh: `btcusdt`, `ethusdt`, `solusdt`, `linkusdt`, `adausdt`, dll.).
8. **[MODIFY]** `app/application/scalper_bot.py`
   - Mengganti referensi `create_indodax_exchange()` ke client Binance.
   - Mengubah inisialisasi `PrivateWS` dan `deadman` sesuai antarmuka Binance.
   - Menyesuaikan ticker caching untuk memelihara format pair baru (`btcusdt`, dll.).
9. **[MODIFY]** `app/application/exit_manager.py`
   - Menyelaraskan evaluasi TP/SL berbasis USDT.
   - Memastikan evaluasi basket exit dan trailing profit membandingkan nilai desimal USDT dengan benar (menghindari error skala).
10. **[MODIFY]** `app/application/use_cases/trade_execution.py`
    - Menyesuaikan perhitungan minimal order volume dan perlindungan trap biaya menggunakan persentase fee 0.1%.

### C. Services & Main Microservices (`services/`)
11. **[MODIFY]** `services/market_pipe/main.py` & `services/market_pipe/providers/indodax.py`
    - Membuat provider baru `BinanceProvider` (di `services/market_pipe/providers/binance.py`).
    - Menyambungkan `market_pipe` microservice ke `BinanceProvider`.
    - Mengemas data tick dan trade ke format Protobuf ZMQ dengan `exchange_id = EXCHANGE_BINANCE`.
12. **[MODIFY]** `services/exchange_reconcile/main.py`
    - Menggunakan client exchange Binance untuk verifikasi status saldo dompet riil dan order terbuka.
13. **[MODIFY]** `services/brain_inference/main.py`
    - Menggunakan `BinanceAdapter` untuk query data historis OHLC via REST API publik Binance (`GET /api/v3/klines`).
    - Mengganti logic backfill koin agar menargetkan universe `USDT` Spot Binance.

### D. VueJS Frontend Dashboard & Backend API (`dashboard/`)
14. **[MODIFY]** `dashboard/server.py` (Dashboard API)
    - Mengganti instansiasi adapter publik/privat ke `BinanceAdapter`.
    - Mengubah parser estimasi asset value ke USDT.
    - Menyesuaikan display format agar ramah nominal desimal USD/USDT.
15. **[MODIFY]** `dashboard/frontend/src/views/ModeSelector.vue` & `About.vue`
    - Memperbarui brand dan teks dari "Indodax" menjadi "Binance Spot".
16. **[MODIFY]** `dashboard/frontend/index.html`
    - Memperbarui meta keywords dan judul aplikasi (dari "Indodax Scalper" $\rightarrow$ "Binance Spot Scalper").

---

## 3. Detail Implementasi: Endpoint & Skema Binance Spot

### A. API Endpoints Utama (REST)
- **Base URL**: `https://api.binance.com`
- **Ticker 24 jam**: `GET /api/v3/ticker/24hr`
- **Klines (OHLC)**: `GET /api/v3/klines` (parameter: `symbol`, `interval=1m`, `limit=100`)
- **UserDataStream (ListenKey)**:
  - `POST /api/v3/userDataStream` (untuk generate listenKey)
  - `PUT /api/v3/userDataStream` (keep-alive, kirim tiap 30 menit)
  - `DELETE /api/v3/userDataStream` (close stream)

### B. WebSocket Endpoints
- **Stream Publik**: `wss://stream.binance.com:9443/ws`
  - Ticker Gabungan: `!ticker@arr`
  - Order Book (Top 10): `<symbol>@depth10` (contoh: `btcusdt@depth10`)
  - Trade: `<symbol>@trade`
- **Stream Privat**: `wss://stream.binance.com:9443/ws/<listenKey>`

---

## 4. Rencana Pengujian & Validasi

Sebelum sistem dijalankan di mode **LIVE**, pengujian ketat akan dilakukan melalui tahapan berikut:

1. **Syntax & PyCompile Check**:
   - Menjalankan `python3 -m py_compile` pada semua file Python yang disentuh untuk memastikan tidak ada kesalahan sintaksis atau indentasi.
2. **Dry Run Testing**:
   - Menyalakan bot dalam mode `DRY_RUN = True`.
   - Memverifikasi WebSocket publik (`MarketDataWS`) menerima ticker array `!ticker@arr` dan mengupdate radar dashboard secara real-time.
   - Memastikan engine `brain-inference` berhasil memproses indikator teknikal (RSI-14, MACD, Bollinger %B) untuk pasangan USDT dan mengalirkan sinyal ke `executive-core`.
3. **Connectivity & Auth Verification**:
   - Menjalankan connectivity test lewat `TradingBridge.test_connection()` menggunakan credential Binance testnet/live yang dibaca dari `.env`.
   - Menguji handshake `userDataStream` dan koneksi `PrivateWS` untuk menerima feed data saldo secara asinkron.
4. **Mock Execution Check**:
   - Memastikan order buy/sell dapat di-parse dan diformat dengan presisi desimal Binance Spot (`stepSize` dan `tickSize` CCXT).

---

Rencana dan inventaris ini siap dieksekusi setelah mendapatkan persetujuan pengguna.
