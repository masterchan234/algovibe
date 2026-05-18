"""
services/market_pipe/main.py — Market-Pipe Microservice Entry Point
====================================================================
Standalone service that:
  1. Connects to the Indodax WebSocket (via IndodaxProvider).
  2. Normalises every price update into a Protobuf Tick.
  3. Publishes the serialised Tick bytes over ZMQ IPC to
     ipc:///tmp/market_stream.ipc.

All downstream services (executive-core, brain-inference, dashboard-api)
subscribe to that IPC socket to receive ticks with sub-millisecond latency.

Run:
    python -m services.market_pipe.main
  or in Docker:
    CMD ["python", "-m", "services.market_pipe.main"]
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

from shared.transport import ZMQPublisher, IPC_MARKET, install_uvloop
from services.market_pipe.providers.indodax import IndodaxProvider
from services.market_pipe.providers.binance import BinanceProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("market-pipe")

# Install uvloop for the fastest event loop (used by async consumers downstream)
install_uvloop()


def main() -> None:
    logger.info("=" * 60)
    logger.info("  AlgoVibe Market-Pipe Service starting…")
    logger.info("=" * 60)

    from app.infrastructure.database import db
    db.run_migrations()

    publisher = ZMQPublisher(IPC_MARKET)
    publisher.start()

    provider = IndodaxProvider(
        on_tick=publisher.send,
        on_trade=publisher.send,
    )
    provider.start()

    binance_provider = BinanceProvider(
        on_tick=publisher.send
    )
    binance_provider.start()

    # Wait until the first ticks arrive before logging "ready"
    if not provider.wait_ready(timeout=60):
        logger.error("[market-pipe] Indodax Provider did not become ready within 60s. Exiting.")
        provider.stop()
        binance_provider.stop()
        publisher.stop()
        sys.exit(1)

    if not binance_provider.wait_ready(timeout=20):
        logger.warning("[market-pipe] Binance Provider did not become ready within 20s. Continuing anyway.")

    logger.info("[MARKET-PIPE] Live - publishing ticks to %s", IPC_MARKET)

    #  Graceful shutdown on SIGTERM / SIGINT 
    _shutdown = False

    def _handle_signal(signum, _frame):
        nonlocal _shutdown
        logger.info(f"[market-pipe] Signal {signum} received — shutting down…")
        _shutdown = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    #  Keep-alive loop 
    try:
        while not _shutdown:
            time.sleep(1)
            if not provider.is_connected():
                logger.warning("[market-pipe] Indodax Provider disconnected — reconnect handled internally.")
            if not binance_provider.is_connected():
                logger.warning("[market-pipe] Binance Provider disconnected — reconnect handled internally.")
    finally:
        logger.info("[market-pipe] Stopping…")
        provider.stop()
        binance_provider.stop()
        publisher.stop()
        logger.info("[market-pipe] Stopped.")


if __name__ == "__main__":
    main()
