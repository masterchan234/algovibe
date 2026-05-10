# AGENTS.md

Agent instructions for this repository. Keep changes small, safe, and trading-aware.

## 1) Project Purpose
- Crypto scalping bot for Indodax with WebSocket-driven market/private streams and microservice architecture.
- Primary runtime uses Docker Compose services (`market-pipe`, `brain-inference`, `litert-lm-decision`, `executive-core`, `dashboard-api`).
- Persistence runtime utama menggunakan PostgreSQL/TimescaleDB (container `algovibe-timescaledb`) dengan state sinkron lintas service.

## 2) Setup and Run
- Install deps (opsional untuk native debugging): `pip install -r requirements.txt`
- Required env vars in `.env`:
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
- `services/brain_inference/main.py`: scoring, oracle, inference, publish signal.
- `services/litert_lm_decision/main.py`: grounded LLM exit signal publisher.
- `services/executive_core/main.py`: execution engine, reconciliation, position lifecycle.
- `config.py`: central strategy/risk constants and paths.
- `app/infrastructure/ws_market.py`: market stream cache + IPC subscriber bridge.
- `app/infrastructure/ws_private.py`: private/order updates feed.
- `app/application/exit_manager.py`: open-position monitoring and exits.
- `app/application/oracle.py`, `app/domain/predictor.py`, `app/infrastructure/llm_worker.py`: AI & reasoning layer.
- `dashboard/server.py`: Flask API/static dashboard (container `dashboard-api`).

## 5) Code Conventions in This Repo
- Keep edits modular; prefer changing one engine at a time.
- Preserve current style: Python with clear helper methods and practical inline comments.
- Many comments/log messages are Indonesian; keep language consistent in nearby code.
- Reuse existing utilities (`db`, `log_message`, telegram helpers) instead of introducing parallel patterns.

## 6) Trading Safety Rules
- Treat any order-placement path as high risk (`execution`, `main`, `exit_manager`).
- Keep default-safe behavior intact unless asked:
  - `DRY_RUN` behavior must remain predictable.
  - Risk caps and thresholds in `config.py` should not be altered incidentally.
- Keep minimum trade threshold consistent across containers (`MIN_TRADE_IDR=10000`).
  - If threshold appears stale in logs, force-recreate related containers.
- For `MICRO_PROFIT_EXIT`, preserve safety cushion behavior to avoid tiny estimated profits flipping negative at fill.
- Never hardcode API keys/secrets or commit credential material.

## 7) Reference Docs (Link, Don’t Duplicate)
- Project overview and runbook: [README.md](README.md)
- Market WebSocket protocol notes: [Marketdata-websocket.md](Marketdata-websocket.md)
- Private WebSocket protocol notes: [Private-websocket.md](Private-websocket.md)
