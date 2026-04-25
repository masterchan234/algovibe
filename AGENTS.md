# AGENTS.md

Agent instructions for this repository. Keep changes small, safe, and trading-aware.

## 1) Project Purpose
- Crypto scalping bot for Indodax with WebSocket-driven market/private streams and SQLite persistence.
- Primary runtime starts from `main.py` and orchestrates modules in `engines/`.

## 2) Setup and Run
- Install deps: `pip install -r requirements.txt`
- Required env vars in `.env`:
  - `INDODAX_API_KEY`
  - `INDODAX_API_SECRET`
- Development runtime (preferred): `python3 main.py`
- Service runtime (production-like/native):
  - Bot Engine: `systemctl start indodax-scalper.service`
  - Dashboard: `systemctl start indodax-dashboard.service`
- Service stop after changes: 
  - `systemctl stop indodax-scalper.service indodax-dashboard.service`
- Do not use Docker during development.
- Docker run (`docker compose up -d`) is only for container deployment scenarios.
- Logs:
  - Docker: `docker compose logs -f`
  - Native app log file: `data/logs/scalper.log`

## 3) Validation Expectations
- There is no formal test suite configured in this repo.
- For non-trivial Python edits, at minimum run syntax checks on touched files:
  - `python3 -m py_compile main.py config.py`
  - Add touched module paths as needed.
- Do not run live-trading flows for validation unless explicitly requested.

## 4) Architecture Map
- `main.py`: bot lifecycle, ranking loop, trade trigger, dashboard launcher.
- `config.py`: central strategy/risk constants and paths.
- `engines/ws_market.py`: market WebSocket ingestion and ticker/orderbook state.
- `engines/ws_private.py`: private/order updates feed.
- `engines/exit_manager.py`: open-position monitoring and exits.
- `engines/oracle.py`, `engines/predictor.py`: adaptive regime/AI scoring logic.
- `engines/database.py`: SQLite schema + state/history persistence (calculates All-Time PnL/Winrate).
- `dashboard/server.py`: Flask + Socket.io server with real-time 3-column layout, WebSocket push loop, and dynamic pair subscription for `Market Activity`.

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
- Never hardcode API keys/secrets or commit credential material.

## 7) Reference Docs (Link, Don’t Duplicate)
- Project overview and runbook: [README.md](README.md)
- Market WebSocket protocol notes: [Marketdata-websocket.md](Marketdata-websocket.md)
- Private WebSocket protocol notes: [Private-websocket.md](Private-websocket.md)
