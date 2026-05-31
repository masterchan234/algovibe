# AGENTS.md

Agent instructions for this repository. Keep changes small, safe, and trading-aware.

## 1) Project Purpose
- Crypto scalping bot for Indodax and Binance Spot with WebSocket-driven market/private streams and microservice architecture.
- Primary runtime uses Docker Compose services (`market-pipe`, `brain-inference`, `litert-lm-decision`, `executive-core`, `dashboard-api`).
- Persistence runtime utama menggunakan PostgreSQL/TimescaleDB (container `algovibe-timescaledb`) dengan state sync lintas service.
- **TECHNICAL_ONLY_MODE = true** (default dan sudah di-set di DB `bot_state`). LLM (`litert-lm-decision`) hanya digunakan untuk article writing; semua keputusan trading 100% dari indikator teknikal.

## 2) Setup and Run
- Install deps (opsional untuk native debugging): `pip install -r requirements.txt`
- Required env vars in `.env`:
  - `EXCHANGE` (either `binance` or `indodax`)
  - `BINANCE_API_KEY`
  - `BINANCE_API_SECRET`
  - `INDODAX_API_KEY`
  - `INDODAX_API_SECRET`
- Primary runtime (recommended): Docker Compose
  - Start stack: `docker compose up -d`
  - Recreate service after env/runtime config changes: `docker compose up -d --force-recreate <service>`
  - Restart after code changes: `docker compose restart <service>`
- Native runtime hanya untuk debugging terisolasi, bukan jalur utama operasi.
- Logs:
  - Docker: `docker compose logs -f`
  - Contoh service: `docker compose logs -f executive-core dashboard-api`

## 3) Validation Expectations
- There is no formal test suite configured in this repo.
- For non-trivial Python edits, at minimum run syntax checks on touched files:
  - `python3 -m py_compile main.py config.py`
  - Add touched module paths as needed.
- Do not run live-trading flows for validation unless explicitly requested.

## 4) Architecture Map
- `services/market_pipe/main.py`: ingress market WS -> IPC stream.
- `services/knn_worker/main.py`: background K-NN classifier worker; standardizes technical DNA and writes verdicts to Redis.
- `app/infrastructure/volume_tracker.py`: real-time CVD sliding volume tracker parsing aggTrades from WebSocket.
- `services/brain_inference/main.py`: scoring, oracle, inference, publish signal; menggunakan in-memory price history (bukan DB) untuk RSI/MACD agar tidak stale.
- `services/litert_lm_decision/main.py`: LLM service — trading signal path dinonaktifkan (TECHNICAL_ONLY_MODE); tetap UP untuk article writing.
- `services/executive_core/main.py`: execution engine, reconciliation, position lifecycle; LLM signal path diberi guard TECHNICAL_ONLY_MODE.
- `config.py`: central strategy/risk constants and paths.
- `app/infrastructure/ws_market.py`: market stream cache + IPC subscriber bridge (supports dynamic Indodax/Binance feeds).
- `app/infrastructure/ws_private.py`: private/order updates feed (handles Binance listenKeys and executionReport mappings).
- `app/application/exit_manager.py`: open-position monitoring and exits. Cost basis (`amount_idr`) TIDAK boleh ditimpa saat reconcile.
- `app/application/use_cases/entry_gates.py`: semua pre-entry gate checks; berisi ATR filter (gate 4b) dan EMA-50 trend filter (gate 4c).
- `app/application/use_cases/trade_execution.py`: BUY execution; fee-trap guard menggunakan pure-percentage (0.5%), bukan hard floor.
- `app/domain/technicals.py`: semua indikator teknikal; 11 indikator aktif di `derive_trade_signal`.
- `app/domain/scoring.py`: radar scoring; menerima `price_history` dict (in-memory) sebagai arg ke-4 agar RSI/MACD menggunakan data live bukan DB stale.
- `app/application/oracle.py`, `app/domain/predictor.py`, `app/infrastructure/llm_worker.py`: AI & reasoning layer.
- `dashboard/server.py`: Flask API/static dashboard (container `dashboard-api`).

## 5) Code Conventions in This Repo
- Keep edits modular; prefer changing one engine at a time.
- Preserve current style: Python with clear helper methods and practical inline comments.
- **LOG STANDARDIZATION**: No emojis/icons in backend logs. Use categorical tags like `[SIGNAL]`, `[OK]`, `[WARN]`, `[ERROR]`, `[EXECUTIVE]`.
- Many comments/log messages are Indonesian; keep language consistent in nearby code.
- Reuse existing utilities (`db`, `log_message`, telegram helpers) instead of introducing parallel patterns.
- `calculate_score` dipanggil dengan 4 arg: `((pair, data), adaptive_cfg, regime, price_history)` — selalu sertakan `price_history` agar tidak fallback ke DB.
- `get_technical_summary` menerima `volumes` sebagai arg opsional ke-2 — sertakan jika data volume per candle tersedia.

## 6) Trading Safety Rules
- Treat any order-placement path as high risk (`execution`, `main`, `exit_manager`).
- **EMA-9 Safety Guard**: Jika histori harga < 2 candle (cold start), eksekusi wajib fallback ke harga pasar saat ini untuk menghindari `index out of bounds`.
- Keep default-safe behavior intact unless asked:
  - `DRY_RUN` behavior must remain predictable.
  - Risk caps and thresholds in `config.py` should not be altered incidentally.
- **Quote / DB Alignment**:
  - Codebase saat ini mendukung dual exchange (Binance & Indodax). Gunakan field agnostik seperti `amount_quote`, `pnl_quote`, atau `vol_quote` untuk nilai nominal fiat/stablecoin. IDR murni digunakan untuk Indodax, dan USDT untuk Binance. Jangan gunakan field legacy `amount_idr` jika konteksnya adalah Binance.
- Keep minimum trade threshold consistent across containers (`MIN_TRADE_IDR=10000` untuk Indodax / `MIN_TRADE_USDT=10` untuk Binance).
  - If threshold appears stale in logs, force-recreate related containers.
- **TTP Target Floor Lock**: Jika puncak harga melampaui `tp_price`, batas trailing stop wajib dikunci minimal setara `tp_price` untuk mencegah profit erosion.
- **Dynamic Breakeven & Trailing SL**: Menggeser SL ke breakeven + fee cushion saat profit $\ge +0.35\%$ gross (SMART_BREAK_EVEN_PCT). Jika profit $\ge +0.55\%$ dan durasi $\ge 2$ menit, SL digeser ke trailing stop 0.2% dari puncak.
- **Momentum Fading Profit Lock**: Jika posisi sudah net-profit ($\ge +0.3\%$ gross) dan momentum melemah (signal != `BUY` saat durasi $\ge 2$ menit, atau signal `SELL`/confidence < 0.40), bot langsung mengeksekusi Market Sell.
- For `MICRO_PROFIT_EXIT`, preserve safety cushion behavior — evaluasi di harga `bid`, bukan `last`.
- JANGAN timpa `amount_quote` (cost basis) di reconcile path. Hanya `amount_crypto` yang boleh disync dari exchange.
- JANGAN ubah TECHNICAL_ONLY_MODE ke false tanpa instruksi eksplisit dari user.
- Never hardcode API keys/secrets or commit credential material.

## 7) Indikator Teknikal Aktif (`derive_trade_signal` - v3.0)
Fokus eksklusif pada 3 indikator utama momentum breakout untuk scalping HFT (Total Max Score: 10.0, Threshold BUY >= 6.5, Threshold SELL >= 7.0):

| # | Indikator | Bobot | Catatan / Kondisi |
|---|-----------|-------|-------------------|
| 1 | Price Velocity | 3.5 | Kenaikan/penurunan harga cepat 4-periode; >0.4% = skor penuh (+3.5), >0.15% = skor sedang (+1.5) |
| 2 | Volume Ratio | 3.5 | Volume saat ini dibanding MA-20; >=2.0x = skor penuh (+3.5), >=1.4x = skor sedang (+1.5) |
| 3 | Order Book Pressure | 3.0 | Rasio Bid/Ask depth volume; >=65% Bid = skor penuh (+3.0), >=55% Bid = skor sedang (+1.5) |

## 8) Entry Gate Sequence (`entry_gates.py`)
- Gerbang dievaluasi secara asinkron: **State Monitors** (berat: Macro BTC 1H Guard, ATR, EMAs, Cooldowns, consecutive losses) dihitung hanya 1x per menit dalam cache memori, sedangkan **Trigger Monitors** (ringan: RSI, volume imbalance, K-NN verdict dari Redis) dievaluasi instan pada setiap 1s tick.
- **5s Wick Delay & Retrace Abort Guard**: Menunda eksekusi order selama 5 detik pada lonjakan momentum (`velocity > 0.3%`), membatalkan order jika harga memantul turun (retracement $> 30\%$) dari puncak breakout.

0. **Macro-Regime Guard (BTC 1H Trend Guard)** — reject semua sinyal jika harga BTC berada di bawah EMA-50 pada TF 1 Jam (Kecuali pair BTC itu sendiri).
1. Open position / blacklist guard
2. Cooldown guard
3. Radar recommendation + tracking log
4. Multi-confluence technical gate (SELL block jika sell_score >= 5.0)
4b. **ATR Volatility Filter** — reject jika ATR > 3% harga (4.5% di loose gates)
4c. **EMA-50 Trend Filter** — reject jika harga < EMA-50.
    - **EXCEPTION (Bottom-Explosion)**: Bypass filter ini jika deteksi Mean Reversion ekstrim: `RSI < 26` + `Buy Pressure > 66%` + `Volume Ratio > 2.1`.
5. Technical probability + imbalance threshold
6. Trend / breakout requirement
7. Radar recommendation guard
9. Dynamic threshold based on volatility + trend
10. Consecutive-loss halt / Shadow Recovery
11. K-NN pattern scoring
13. Final confidence threshold

## 9) Reference Docs (Link, Don't Duplicate)
- Project overview and runbook: [README.md](README.md)
- Market WebSocket protocol notes: [Marketdata-websocket.md](Marketdata-websocket.md)
- Private WebSocket protocol notes: [Private-websocket.md](Private-websocket.md)
