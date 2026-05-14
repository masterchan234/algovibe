# Algovibe 🌊🤖

**Algovibe** adalah bot trading kripto otomatis (Scalping) untuk platform **Indodax** yang menggunakan arsitektur **Microservices**. Bot ini menggabungkan kecepatan eksekusi algoritma kuantitatif dengan analisis berbasis indikator teknikal murni untuk navigasi market yang presisi.

---

## 🎯 Fitur Utama

- **Microservice Architecture:** Terbagi menjadi 6 layanan independen (Market-Pipe, Brain-Inference, LiteRT-LM-Decision, Executive-Core, Dashboard-API, News-Maker) untuk skalabilitas dan stabilitas tinggi.
- **TECHNICAL_ONLY_MODE:** Semua keputusan trading 100% berbasis indikator teknikal (11 indikator, max score ~19.5). LLM hanya aktif untuk article writing.
- **Live Market Data:** Semua scoring dan sinyal menggunakan in-memory price history dari WebSocket stream — bukan data DB yang bisa stale.
- **Market Regime Detection:** Oracle sentral mendeteksi fase market (Bullish, Bearish, Choppy, Ranging, Crash) dan menyesuaikan strategi secara dinamis.
- **ZMQ-Driven Data Bus:** Komunikasi antar-layanan menggunakan ZeroMQ (IPC) untuk latency minimal.
- **Indodax Tax Compliant:** Kalkulasi PnL akurat termasuk pajak PMK 68/2022 (PPh & PPN) dan fee exchange.
- **Interactive Dashboard:** Monitoring realtime via Vue.js & SocketIO dengan visualisasi radar market dan eksekusi manual.
- **RF Auto-Trainer:** Background service yang secara otomatis melatih ulang model RandomForest (Scalper V2) berdasarkan data trade terbaru.
- **Automated News Engine:** News Maker menghasilkan artikel pasar otomatis (Morning Briefing, Trending Review, Evening Review, Breaking Headlines) berdasarkan sinyal teknikal live.

---

## 🔄 Workflow Arsitektur (Microservices)

```mermaid
graph TD
    ID[Indodax WebSocket] -- WebSocket --> MP[Market Pipe]
    MP -- ZMQ PUB ipc:market_stream.ipc --> BI[Brain Inference]
    MP -- ZMQ PUB ipc:market_stream.ipc --> NM[News Maker]
    MP -- ZMQ PUB --> EC[Executive Core]

    BI -- in-memory price history --> BI
    BI -- 11-indikator scoring live --> BI
    BI -- ZMQ PUB ipc:signal_stream.ipc --> EC
    BI -- ZMQ PUB ipc:signal_stream.ipc --> NM

    EC -- Risk Check & Order Execution --> ID
    EC -- PnL & Order Status --> DB[(PostgreSQL/TimescaleDB)]

    NM -- Articles & Briefings --> DB
    NM -- hero_ai_insight --> DB

    DB -- Shared State --> DA[Dashboard API]
    DA -- Web UI --> User((Trader))

    LLM[LiteRT-LM-Decision] -- Article Writing only --> DB
```

---

## 🧠 Cara Kerja (Technical Engine)

Algovibe menggunakan sistem **Multi-Confluence Technical Scoring** dengan 11 indikator:

| # | Indikator | Bobot | Catatan |
|---|-----------|-------|---------|
| 1 | RSI-14 | 3.0 | Wilder smoothing |
| 2 | MACD Histogram | 2.5 | Price-normalized (x1000) |
| 3 | Bollinger %B | 2.0 | Posisi relatif band |
| 4 | Stochastic RSI | 2.0 | K/D + crossover |
| 5 | EMA 9/21 Crossover | 1.5 | Trend jangka menengah |
| 6 | Williams %R | 1.5 | Oversold/overbought independent |
| 7 | Slope/Momentum | 1.0 | Linear regression slope |
| 8 | ROC-10 | 1.0 | Rate of Change, mean-reversion |
| 9 | Order Book Pressure | 2.0 | Fallback ke Volume Ratio jika OB tidak ada |
| 10 | Volume Ratio | 1.5 | Vol sekarang / MA-20; spike = breakout valid |
| 11 | OBV Slope | 1.5 | Akumulasi/distribusi diam-diam |

Syarat BUY: `buy_score >= 6.0` dan `sell_score < 4.0`. Max score ~19.5.

### Entry Gate Sequence (`entry_gates.py`)

1. Open position / blacklist guard
2. Cooldown guard
3. Radar recommendation + tracking log
4. Multi-confluence technical gate (SELL block jika `sell_score >= 5.0`)
5. **ATR Volatility Filter (4b)** — reject jika ATR > 3% harga (4.5% di loose gates)
6. **EMA-50 Trend Filter (4c)** — reject jika harga < EMA-50 (dilewati di loose gates)
7. Technical probability + imbalance threshold
8. Trend / breakout requirement
9. Radar recommendation guard
10. Dynamic threshold based on volatility + trend
11. Consecutive-loss halt / Shadow Recovery
12. K-NN pattern scoring
13. Final confidence threshold

---

## 🧪 Backtesting Engine (High-Fidelity)

Algovibe dilengkapi dengan mesin backtesting performa tinggi untuk memvalidasi strategi secara kuantitatif sebelum dijalankan di live market.

### Fitur Utama Backtester:
- **High-Performance NumPy Engine:** Menggunakan simulasi berbasis array (vektor) untuk memproses ribuan candle dalam hitungan detik.
- **1:1 Strategy Parity:** Menggunakan fungsi teknikal dan gerbang filter (`entry_gates.py`) yang persis sama dengan bot live.
- **High-Fidelity Simulation:**
    - **Slippage & Spread Simulation:** Mensimulasikan harga eksekusi di *bid/ask* spread untuk estimasi profit yang realistis.
    - **Portfolio Layer:** Mensimulasikan batasan modal riil dengan parameter `max_open_positions` global lintas pair.
- **Advanced Quantitative Metrics:**
    - **Sortino Ratio:** Mengukur performa terhadap risiko *downside*.
    - **Recovery Factor & Calmar Ratio:** Mengukur kemampuan strategi pulih dari *drawdown*.
    - **Kelly Criterion:** Memberikan rekomendasi ukuran posisi optimal.
    - **Expectancy:** Menghitung rata-rata profit yang diharapkan per trade dalam IDR.

### Operasional Backtest:

```bash
# 1. Fetch data historis (default 90 hari terakhir)
docker compose run --rm backtester fetch

# 2. Jalankan backtest untuk semua profil aktif
docker compose run --rm backtester run

# 3. Jalankan backtest dengan filter pair tertentu
docker compose run --rm backtester run --days 30 --pairs BTC-IDR,ETH-IDR

# 4. Lihat laporan ringkasan terakhir
docker compose run --rm backtester report

# 5. Rekursif Optimasi (Auto-Tuning) — Loop 5 generasi
# Sistem akan berhenti otomatis jika target profit harian tercapai
docker compose run --rm backtester optimize --generations 5
```

> [!TIP]
> Anda dapat menetapkan target harian (misal: 10rb/hari) di tabel `backtest_profiles` kolom `target_pnl_idr` untuk mengaktifkan fitur *Early Exit*.

Dokumentasi teknis lengkap tersedia di: [Functional Specification Document (Backtesting)](docs/backtesting_fsd.md)

Data historis disimpan di tabel `ohlcv_1m` pada database `algovibe` (TimescaleDB).

---

## ⚙️ Arsitektur Proyek

```
services/
├── market_pipe/        # Ingress: Indodax WS -> ZMQ Protobuf
├── brain_inference/    # Scoring & Oracle: live in-memory history, 11 indikator
├── litert_lm_decision/ # LLM: TECHNICAL_ONLY_MODE=true (article writing only)
├── executive_core/     # Execution: Risk Checks, P&L, Order Placement
├── news_maker/         # Automated Editorial Engine: briefings, headlines, trending review
└── dashboard_api/      # UI: Flask Server + SocketIO

app/
├── application/
│   ├── entry_gates.py          # Gerbang filter entry (11 gate + ATR + EMA50)
│   ├── use_cases/
│   │   ├── trade_execution.py  # BUY execution, fee-trap pure-% guard
│   │   └── candidate_enrichment.py
│   ├── oracle.py               # Market regime & K-NN pattern scoring
│   └── exit_manager.py         # TP/SL/Trailing; amount_idr tidak ditimpa saat reconcile
├── domain/
│   ├── technicals.py   # 11 indikator: RSI, MACD, BB, StochRSI, EMA, WilliamsR,
│   │                   # Slope, ROC, OB Pressure, Volume Ratio, OBV Slope
│   ├── scoring.py      # Radar scoring; terima price_history (live) sebagai arg ke-4
│   └── predictor.py    # ML model inference (Scalper V2, blend tech+ML)
└── infrastructure/
    ├── database.py     # PostgreSQL/TimescaleDB connector
    ├── ws_market.py    # WebSocket market stream + in-memory ticker/OB cache
    └── auto_trainer.py # Loop self-training model
```

---

## 🚀 Cara Menjalankan

### Docker (Rekomendasi - Production)

```bash
# 1. Setup Environment
cp .env-example .env
nano .env  # isi INDODAX_API_KEY dan INDODAX_API_SECRET

# 2. Jalankan Stack
docker compose up -d

# 3. Cek Status
docker compose ps
docker compose logs -f brain-inference executive-core
```

### Catatan Operasional Penting

- Jika ada perubahan di `.env` atau kode, lakukan restart/recreate yang sesuai:

```bash
# Setelah perubahan kode:
docker compose restart brain-inference executive-core

# Setelah perubahan env/config:
docker compose up -d --force-recreate brain-inference executive-core litert-lm-decision dashboard-api
```

- **TECHNICAL_ONLY_MODE = true** — LLM tidak akan membuat keputusan trading. Untuk mengaktifkan kembali LLM trading, set `technical_only_mode=false` di tabel `bot_state` DB dan ubah flag di `config.py`.
- `MIN_TRADE_IDR` (default: **10000**) — pastikan konsisten di semua container.
- Fee-trap guard menggunakan pure-percentage (0.5% dari nilai trade), bukan hard floor.
- `MICRO_PROFIT_EXIT` dievaluasi di harga `bid` (bukan `last`) dengan safety cushion.
- Cost basis (`amount_idr`) **tidak pernah** ditimpa saat reconcile — hanya `amount_crypto` yang disync dari exchange.

### Native (Development / Debugging)

```bash
python3 -m services.market_pipe.main        # Terminal 1
python3 -m services.brain_inference.main    # Terminal 2
python3 -m services.executive_core.main     # Terminal 3
python3 -m services.litert_lm_decision.main # Terminal 4 (article writing only)
python3 -m services.news_maker.main         # Terminal 5 (news engine)
python3 -m dashboard.server                 # Terminal 6
```

---

## 📰 News Maker (Automated Editorial Engine)

`services/news_maker/` adalah layanan AI editorial yang menghasilkan artikel pasar kripto secara otomatis dalam Bahasa Indonesia, berdasarkan data live dari WebSocket stream dan sinyal teknikal dari Brain Inference.

### Jadwal Otomatis

| Trigger | Waktu / Kondisi | Tipe Artikel | Kategori DB |
| :--- | :--- | :--- | :--- |
| Scheduler | **08:00 WIB** setiap hari | Morning Briefing | `MORNING_BRIEF` |
| Scheduler | **14:00 WIB** setiap hari | Daily Trending Review (top 7 coins) | `TRENDING_REVIEW` |
| Scheduler | **20:00 WIB** setiap hari | Evening Market Review | `EVENING_REVIEW` |
| Realtime monitor (setiap 30s) | Coin bergerak >3% in 1h atau >10% in 24h | Breaking Headline | `REALTIME_HEADLINE` |
| Realtime monitor (setiap ~3 menit) | Top gainer >2% + volume >30M IDR | Headline | `REALTIME_HEADLINE` |
| **Signal-triggered** | Brain Inference kirim BUY/SELL dengan confidence ≥ 75% | Headline | `REALTIME_HEADLINE` |

### Generate Manual

```bash
# Dari host — semua mode tersedia:
./generate_news.sh morning
./generate_news.sh evening
./generate_news.sh headline BTC
./generate_news.sh brief
./generate_news.sh landing [COIN] [COUNT]
```

Script otomatis mendeteksi apakah container `news-maker` sedang berjalan dan mengeksekusi di dalamnya; jika tidak, berjalan native. Dilengkapi progress bar + elapsed time.

### Konfigurasi News Maker (`.env`)

```env
NEWS_LLM_PROVIDER=local          # local | hf | cf | auto
NEWS_LLM_TIMEOUT_SEC=45           # timeout LLM local (default 45s)
NEWS_TRENDING_REVIEW_HOUR=14      # jam trending review WIB (default 14)
NEWS_SIGNAL_CONFIDENCE_MIN=0.75   # min confidence sinyal untuk trigger headline
NEWS_MIN_CONTENT_CHARS=2000       # minimum panjang konten artikel

# Remote provider (opsional — fallback jika local model timeout)
NEWS_HF_API_URL=https://...
NEWS_HF_API_TOKEN=hf_...
NEWS_CF_API_URL=https://...
NEWS_CF_API_TOKEN=...
```

### LLM Fallback Strategy

```
Local Engine (timeout 45s)
  └─ timeout → Remote Provider (CF atau HF, timeout 60s)
        └─ gagal → artikel tidak dibuat (skip, tidak crash)
```

Artikel disimpan ke tabel `intelligence_reports`. Deduplication: headline untuk pair yang sama tidak dibuat ulang dalam 20 menit.

---

## 🎭 Market Regimes (Oracle)

Oracle mendeteksi kondisi pasar global dan menyesuaikan strategi secara dinamis:

| Regime | Deskripsi | Perilaku Bot |
| :--- | :--- | :--- |
| **BULLISH** | Tren naik, volume tinggi. | **Agresif**: Threshold diturunkan. |
| **BEARISH** | Tren turun, risiko tinggi. | **Defensif**: Filter diperketat. |
| **RANGING** | Harga sideways dalam range. | **Mean Reversion**: Fokus pada pullback. |
| **CHOPPY** | Sideways tajam/acak. | **Selective**: Cooldown diperpanjang. |
| **CRASH** | Penurunan tajam tak terkendali. | **Emergency Stop**: Berhenti otomatis. |

---

## ⚠️ Disclaimer

Software ini dibuat untuk tujuan edukasi dan eksperimen. **Gunakan dengan risiko Anda sendiri.** Pembuat tidak bertanggung jawab atas kerugian finansial. Selalu gunakan **DRY RUN** sebelum masuk ke live market.
