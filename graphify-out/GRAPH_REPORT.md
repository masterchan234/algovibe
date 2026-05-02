# Graph Report - .  (2026-05-02)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 579 nodes · 927 edges · 18 communities detected
- Extraction: 83% EXTRACTED · 17% INFERRED · 0% AMBIGUOUS · INFERRED: 159 edges (avg confidence: 0.63)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Oracle & Intelligence|Oracle & Intelligence]]
- [[_COMMUNITY_UI Frontend Logic|UI Frontend Logic]]
- [[_COMMUNITY_Persistence & Database|Persistence & Database]]
- [[_COMMUNITY_Dashboard Data Streams|Dashboard Data Streams]]
- [[_COMMUNITY_Execution & Bot Lifecycle|Execution & Bot Lifecycle]]
- [[_COMMUNITY_Public API Endpoints|Public API Endpoints]]
- [[_COMMUNITY_ML & Technical Analysis|ML & Technical Analysis]]
- [[_COMMUNITY_Frontend Chart Helpers|Frontend Chart Helpers]]
- [[_COMMUNITY_Private WS Streaming|Private WS Streaming]]
- [[_COMMUNITY_Deadman Switch Safety|Deadman Switch Safety]]
- [[_COMMUNITY_UI Frontend Logic0|UI Frontend Logic0]]
- [[_COMMUNITY_UI Frontend Logic1|UI Frontend Logic1]]
- [[_COMMUNITY_UI Frontend Logic2|UI Frontend Logic2]]
- [[_COMMUNITY_UI Frontend Logic3|UI Frontend Logic3]]
- [[_COMMUNITY_UI Frontend Logic4|UI Frontend Logic4]]
- [[_COMMUNITY_UI Frontend Logic5|UI Frontend Logic5]]
- [[_COMMUNITY_UI Frontend Logic6|UI Frontend Logic6]]
- [[_COMMUNITY_UI Frontend Logic8|UI Frontend Logic8]]

## God Nodes (most connected - your core abstractions)
1. `LLMWorker` - 69 edges
2. `MarketDataWS` - 53 edges
3. `Database` - 46 edges
4. `get_connection()` - 44 edges
5. `Oracle` - 38 edges
6. `log_message()` - 29 edges
7. `ExitManager` - 20 edges
8. `AlgoChart` - 20 edges
9. `get_db_connection()` - 18 edges
10. `ScalperBot` - 16 edges

## Surprising Connections (you probably didn't know these)
- `main()` --calls--> `MarketDataWS`  [INFERRED]
  market_hub.py → app/infrastructure/ws_market.py
- `test_handle_order_update()` --calls--> `ExitManager`  [INFERRED]
  scratch/test_exit_manager.py → app/application/exit_manager.py
- `test_reconcile_with_exchange()` --calls--> `ExitManager`  [INFERRED]
  scratch/test_exit_manager.py → app/application/exit_manager.py
- `force_reconcile()` --calls--> `ExitManager`  [INFERRED]
  scratch/force_reconcile.py → app/application/exit_manager.py
- `test_exit()` --calls--> `ExitManager`  [INFERRED]
  scratch/test_manual_exit.py → app/application/exit_manager.py

## Communities

### Oracle & Intelligence - "Oracle & Intelligence"
Cohesion: 0.04
Nodes (40): MarketRegime, Oracle, Scan Order Book for massive buy walls.         Returns a score multiplier boost, Detect if a price surge is artificial (low liquidity support)., Analyze the top mover every few minutes to keep the dashboard active., Triggered when a coin is 'exploding' or crashing rapidly., Check if the pair's sector is pumping., Returns the raw sentiment score for a pair's sector. (+32 more)

### UI Frontend Logic - "UI Frontend Logic"
Cohesion: 0.05
Nodes (39): AlgoChart, _applyFilter(), applyLedgerDensity(), _applySort(), closeAllSidebars(), fetchSettings(), fireNotif(), flashEl() (+31 more)

### Persistence & Database - "Persistence & Database"
Cohesion: 0.05
Nodes (18): Database, get_connection(), Metode khusus untuk menyuntikkan hasil simulasi masa lalu ke database memori AI., Mengambil rekap performa untuk 24 jam terakhir. Default = last 24h., Ambil list harga terakhir untuk pair tertentu., Ambil value dan timestamp updated_at., Laporan audit kuantitatif untuk dashboard:         - Precision per LLM verdict, Menarik data DNA historis untuk Pattern Matching (k-NN). (+10 more)

### Dashboard Data Streams - "Dashboard Data Streams"
Cohesion: 0.05
Nodes (49): _build_logs_payload(), _build_market_trades_payload(), _build_radar_payload(), _build_stats_payload(), _build_trades_payload(), _generate_equity_reasoning(), _generate_equity_report_llm(), _generate_technical_reasoning() (+41 more)

### Execution & Bot Lifecycle - "Execution & Bot Lifecycle"
Cohesion: 0.05
Nodes (27): build_bot(), Composition root for the trading bot runtime., run_bot(), ExitManager, Recover active jaring orders from SQLite on startup., Main entry for jaring logic. Called from the main loop., Set cooldown per pair setelah trade loss agar tidak re-entry di market chop., Callback dari PrivateWS. Memproses order yang terisi (Filled). (+19 more)

### Public API Endpoints - "Public API Endpoints"
Cohesion: 0.05
Nodes (27): main(), Place a deep-limit buy order., get_ohlcv(), manual_exit(), Background thread — push semua data via WebSocket setiap 1 detik., Trigger LLM analysis for a specific pair on-demand., Force an emergency exit for a specific pair via dashboard., Ambil data OHLCV dari bursa untuk timeframe tertentu. (+19 more)

### ML & Technical Analysis - "ML & Technical Analysis"
Cohesion: 0.07
Nodes (32): [Anti-Hallucination]          Secara perlahan mengembalikan bobot ke 1.0 agar bo, Setiap 60 detik, ambil top-5 koin dengan sinyal BUY terkuat dan         antri ke, get_prediction(), _load_model(), Menghitung probabilitas eksekusi menggunakan Heuristic + Machine Learning (Scalp, calculate_atr(), calculate_rsi(), calculate_volatility() (+24 more)

### Frontend Chart Helpers - "Frontend Chart Helpers"
Cohesion: 0.1
Nodes (7): buildChartOption(), fetchPnlHistory(), getCommonOption(), scheduleUpdate(), setTimeframe(), handleTouchEnd(), triggerRefresh()

### Private WS Streaming - "Private WS Streaming"
Cohesion: 0.12
Nodes (7): PrivateWS, Private WebSocket Client — Indodax ==================================== Mendenga, Loop utama dengan auto-reconnect dan token refresh., WebSocket client untuk Private Channel Indodax.      Menerima event order_update, Mulai koneksi WS di background thread., Blok hingga berhasil subscribe ke private channel., Request connToken + channel dari REST endpoint.         Return True jika berhasi

### Deadman Switch Safety - "Deadman Switch Safety"
Cohesion: 0.2
Nodes (4): DeadmanSwitch, Indodax Deadman Switch Implementation.     Calls /countdownCancelAll as a heartb, Update the list of pairs to protect., Send a single heartbeat request.

### UI Frontend Logic0 - "UI Frontend Logic0"
Cohesion: 0.27
Nodes (11): fetch_balance(), fetch_order_history(), fetch_trade_history(), get_db(), main(), Reconcile: Bandingkan data order/trade di database lokal vs Indodax API.  Checks, Ambil saldo akun dari Indodax., Kirim GET request dengan HMAC-SHA512 signature ke Indodax v2 API. (+3 more)

### UI Frontend Logic1 - "UI Frontend Logic1"
Cohesion: 0.31
Nodes (8): calculate_24h_performance(), print_daily_summary(), Performance Tracker — Indodax Scalper Bot ======================================, Entry-point utama — dipanggil oleh main.py saat daily reset.     Menghitung, men, Hitung ringkasan performa hari ini dari database trade_history., Cetak ringkasan harian ke terminal dalam format yang mudah dibaca., run_performance_report(), save_daily_report()

### UI Frontend Logic2 - "UI Frontend Logic2"
Cohesion: 0.28
Nodes (6): _llm_process_loop(), Inferensi teks biasa dengan consciousness context., Inferensi dengan Function Calling untuk output JSON terstruktur.     Model meman, Background worker process yang memuat LiteRT model ke CPU RAM.      Protocol req, _run_structured_inference(), _run_text_inference()

### UI Frontend Logic3 - "UI Frontend Logic3"
Cohesion: 0.36
Nodes (4): fetchChartData(), fetchMarketContext(), initChart(), loadInsight()

### UI Frontend Logic4 - "UI Frontend Logic4"
Cohesion: 0.29
Nodes (2): AlgovibeBacktester, Fetch semua IDR pairs yang aktif dari Indodax secara dinamis.         Filter ber

### UI Frontend Logic5 - "UI Frontend Logic5"
Cohesion: 0.43
Nodes (6): find_free_port(), main(), print_banner(), Mencari port yang tersedia mulai dari start_port., Inisialisasi database khusus untuk tenant baru., setup_tenant_db()

### UI Frontend Logic6 - "UI Frontend Logic6"
Cohesion: 0.47
Nodes (5): check_subscriptions(), main(), manage_market_hub(), Cek expiry date dan matikan bot yang sudah lewat waktu., Pastikan Market Hub selalu menyala.

### UI Frontend Logic8 - "UI Frontend Logic8"
Cohesion: 0.67
Nodes (2): calculate_score(), Fungsi pembantu untuk multiprocessing ranking.

## Knowledge Gaps
- **85 isolated node(s):** `Fetch semua IDR pairs yang aktif dari Indodax secara dinamis.         Filter ber`, `Mencari port yang tersedia mulai dari start_port.`, `Inisialisasi database khusus untuk tenant baru.`, `Cek expiry date dan matikan bot yang sudah lewat waktu.`, `Pastikan Market Hub selalu menyala.` (+80 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `UI Frontend Logic4`** (7 nodes): `AlgovibeBacktester`, `.fetch_all_active_pairs()`, `.fetch_data()`, `.__init__()`, `.run_simulation()`, `Fetch semua IDR pairs yang aktif dari Indodax secara dinamis.         Filter ber`, `backtester.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `UI Frontend Logic8`** (3 nodes): `scoring.py`, `calculate_score()`, `Fungsi pembantu untuk multiprocessing ranking.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MarketDataWS` connect `Public API Endpoints` to `Dashboard Data Streams`, `Execution & Bot Lifecycle`?**
  _High betweenness centrality (0.147) - this node is a cross-community bridge._
- **Why does `log_message()` connect `Execution & Bot Lifecycle` to `Oracle & Intelligence`, `Persistence & Database`, `UI Frontend Logic2`, `ML & Technical Analysis`?**
  _High betweenness centrality (0.131) - this node is a cross-community bridge._
- **Why does `LLMWorker` connect `Oracle & Intelligence` to `Dashboard Data Streams`, `UI Frontend Logic2`, `Public API Endpoints`, `ML & Technical Analysis`?**
  _High betweenness centrality (0.130) - this node is a cross-community bridge._
- **Are the 59 inferred relationships involving `LLMWorker` (e.g. with `MarketRegime` and `Oracle`) actually correct?**
  _`LLMWorker` has 59 INFERRED edges - model-reasoned connections that need verification._
- **Are the 27 inferred relationships involving `MarketDataWS` (e.g. with `ScalperBot` and `Generate token handshake untuk view-only mode.`) actually correct?**
  _`MarketDataWS` has 27 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `Oracle` (e.g. with `ScalperBot` and `LLMWorker`) actually correct?**
  _`Oracle` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Fetch semua IDR pairs yang aktif dari Indodax secara dinamis.         Filter ber`, `Mencari port yang tersedia mulai dari start_port.`, `Inisialisasi database khusus untuk tenant baru.` to the rest of the system?**
  _85 weakly-connected nodes found - possible documentation gaps or missing edges._