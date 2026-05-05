"""
services/brain_inference/main.py — Brain-Inference Microservice Entry Point
===========================================================================
Long-running Oracle inference worker for the microservice stack.

Responsibilities:
  - Subscribe to market ticks from ipc:///tmp/market_stream.ipc
  - Keep process alive as a dedicated inference service
  - Publish safe heartbeat signals to ipc:///tmp/signal_stream.ipc

Run:
    python -m services.brain_inference.main
"""

import asyncio
import logging
import os
import signal
import sys
import time

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.transport import ZMQPublisher, ZMQSubscriber, IPC_MARKET, IPC_SIGNAL, install_uvloop
from shared.algovibe_pb2 import Tick, Signal, SignalAction, SignalSource  # type: ignore[attr-defined]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("brain-inference")

install_uvloop()


class BrainInferenceService:
    """Minimal safe inference loop for containerized microservice runtime."""

    def __init__(self):
        self._tick_sub = ZMQSubscriber(IPC_MARKET)
        self._signal_pub = ZMQPublisher(IPC_SIGNAL)
        self._running = False
        self._last_heartbeat = 0.0

    async def start(self) -> None:
        self._running = True
        self._tick_sub.start()
        self._signal_pub.start()
        logger.info("[brain-inference] Started — market=%s signal=%s", IPC_MARKET, IPC_SIGNAL)
        await self._consume_ticks()

    def stop(self) -> None:
        self._running = False
        self._tick_sub.stop()
        self._signal_pub.stop()
        logger.info("[brain-inference] Stopped.")

    async def _consume_ticks(self) -> None:
        """Consume market ticks and emit a conservative heartbeat signal."""
        async for raw in self._tick_sub.aiter_messages():
            if not self._running:
                break

            try:
                tick = Tick()
                tick.ParseFromString(raw)
            except Exception as exc:
                logger.warning("[brain-inference] Tick parse error: %s", exc)
                continue

            now = time.time()
            if now - self._last_heartbeat < 5:
                continue
            self._last_heartbeat = now

            signal_msg = Signal(
                symbol=tick.symbol,
                action=SignalAction.ACTION_NONE,
                confidence=0.0,
                source=SignalSource.SOURCE_ORACLE,
                timestamp=int(now * 1000),
            )
            self._signal_pub.send(signal_msg.SerializeToString())


def main() -> None:
    service = BrainInferenceService()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(signum, _frame):
        logger.info("[brain-inference] Signal %s received — shutting down.", signum)
        service.stop()
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(service.start())
    finally:
        if service._running:
            service.stop()
        if not loop.is_closed():
            loop.close()


if __name__ == "__main__":
    main()
