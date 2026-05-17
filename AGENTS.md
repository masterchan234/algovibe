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
  - Untuk menghindari resiko skema database, nilai nominal `USDT` (saat `EXCHANGE=binance` aktif) disimpan langsung di bawah kolom legacy bertipe numeric/varchar (seperti `amount_idr`, `pnl_idr`, `vol_idr`).
- Keep minimum trade threshold consistent across containers (`MIN_TRADE_IDR=10000` / `MIN_TRADE_USDT=10`).
  - If threshold appears stale in logs, force-recreate related containers.
- For `MICRO_PROFIT_EXIT`, preserve safety cushion behavior — evaluasi di harga `bid`, bukan `last`.
- JANGAN timpa `amount_idr` (cost basis) di reconcile path. Hanya `amount_crypto` yang boleh disync dari exchange.
- JANGAN ubah TECHNICAL_ONLY_MODE ke false tanpa instruksi eksplisit dari user.
- Never hardcode API keys/secrets or commit credential material.

## 7) Indikator Teknikal Aktif (`derive_trade_signal`)
Total 11 indikator, max score ~19.5 (BUY atau SELL). Threshold BUY/SELL: score >= 6.0, opposing < 4.0.

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

## 8) Entry Gate Sequence (`entry_gates.py`)
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
