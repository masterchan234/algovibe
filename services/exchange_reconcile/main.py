"""
services/exchange_reconcile/main.py — Exchange Reconcile Service
=================================================================
Dedicated service that continuously reconciles exchange truth (wallet,
open orders, fills) against bot state in DB.

Why this exists:
  • Removes duplicate reconcile logic from dashboard runtime.
  • Keeps LIVE dashboard state aligned with actual exchange state.
  • Reuses ExitManager.reconcile_with_exchange() as the single source of truth.

Run:
    python -m services.exchange_reconcile.main
"""

import logging
import os
import signal
import sys
import time

# Allow repo-root imports when run as __main__
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import config as cfg
from app.application.exit_manager import ExitManager
from app.infrastructure.database import db
from app.infrastructure.exchange_factory import create_indodax_exchange

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("exchange-reconcile")


class ExchangeReconcileService:
    def __init__(self):
        self._exchange = create_indodax_exchange()
        self._exit_manager = ExitManager(self._exchange, oracle=None, bot=None)

        self._interval_sec = max(int(os.getenv("RECONCILE_INTERVAL_SEC", "10") or 10), 10)
        self._startup_delay_sec = max(int(os.getenv("RECONCILE_STARTUP_DELAY_SEC", "20") or 20), 0)
        self._running = False

    def _should_force_reconcile(self) -> bool:
        return str(db.get_state("trigger_wallet_reconcile", "0")) == "1"

    def _mark_force_reconcile_done(self) -> None:
        db.save_state("trigger_wallet_reconcile", "0")

    def _run_once(self) -> None:
        if cfg.DRY_RUN:
            logger.info("[exchange-reconcile] DRY_RUN mode — skip reconcile.")
            return
        # ExitManager already handles mode awareness (dry/live) internally.
        self._exit_manager.reconcile_with_exchange()
        db.save_state("wallet_reconcile_last_ok_ts", str(int(time.time())))

    def start(self) -> None:
        logger.info("=" * 60)
        logger.info("  AlgoVibe Exchange-Reconcile Service starting…")
        logger.info("=" * 60)
        logger.info(
            "[exchange-reconcile] interval=%ss startup_delay=%ss",
            self._interval_sec,
            self._startup_delay_sec,
        )

        self._running = True

        if self._startup_delay_sec > 0:
            time.sleep(self._startup_delay_sec)

        while self._running:
            cycle_started_at = time.time()
            force_mode = self._should_force_reconcile()

            try:
                self._run_once()
                if force_mode:
                    self._mark_force_reconcile_done()
                    logger.info("[exchange-reconcile] Manual reconcile request processed.")
            except Exception as exc:
                logger.error("[exchange-reconcile] Reconcile cycle failed: %s", exc)
                db.save_state("wallet_reconcile_last_err", str(exc)[:500])
                db.save_state("wallet_reconcile_last_err_ts", str(int(time.time())))
                if force_mode:
                    # Always clear manual trigger to avoid infinite retry storm.
                    self._mark_force_reconcile_done()

            elapsed = time.time() - cycle_started_at
            sleep_for = max(self._interval_sec - elapsed, 1)
            time.sleep(sleep_for)

    def stop(self) -> None:
        self._running = False
        logger.info("[exchange-reconcile] Stopped.")


def main() -> None:
    # Run migrations explicitly before service starts looping.
    db.run_migrations()

    service = ExchangeReconcileService()

    def _handle_signal(signum, _frame):
        logger.info("[exchange-reconcile] Signal %s — shutting down…", signum)
        service.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        service.start()
    finally:
        service.stop()


if __name__ == "__main__":
    main()
