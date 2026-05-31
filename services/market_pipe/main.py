"""
services/market_pipe/main.py — Market-Pipe Microservice Entry Point
====================================================================
Standalone service that:
  1. Connects to the exchange WebSocket (Binance or Indodax, via env EXCHANGE).
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

_exchange = os.getenv("EXCHANGE", "binance").lower()
if _exchange == "binance":
    from services.market_pipe.providers.binance import BinanceProvider as _Provider
else:
    from services.market_pipe.providers.indodax import IndodaxProvider as _Provider

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

    provider = _Provider(
        on_tick=publisher.send,
        on_trade=lambda *args, **kwargs: None,  # Consumers parse pure Ticks; avoid corrupting the stream
    )
    provider.start()

    # Wait until the first tick arrives before logging "ready"
    if not provider.wait_ready(timeout=60):
        logger.error("[market-pipe] Provider did not become ready within 60s. Exiting.")
        provider.stop()
        publisher.stop()
        sys.exit(1)

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
                logger.warning("[market-pipe] Provider disconnected — reconnect handled internally.")
    finally:
        logger.info("[market-pipe] Stopping…")
        provider.stop()
        publisher.stop()
        logger.info("[market-pipe] Stopped.")


if __name__ == "__main__":
    main()
