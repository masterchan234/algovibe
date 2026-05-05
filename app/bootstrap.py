import asyncio
import logging
import traceback
from app.application.scalper_bot import ScalperBot

logger = logging.getLogger(__name__)


def _install_uvloop() -> None:
    """Install uvloop as the asyncio event loop policy for maximum I/O throughput.

    Falls back silently if uvloop is not available (e.g. Windows / non-Linux).
    """
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        logger.info("[Bootstrap] uvloop event loop policy installed.")
    except ImportError:
        logger.info("[Bootstrap] uvloop not available — using default asyncio event loop.")
    except Exception as exc:
        logger.warning("[Bootstrap] Could not install uvloop policy: %s", exc)


def _preload_ml_model() -> None:
    """Pre-load the XGBoost/RF model into memory at startup.

    This ensures the first call to get_prediction() runs sub-millisecond rather
    than paying the full joblib load cost mid-trade.
    """
    try:
        from app.domain.predictor import preload_model
        preload_model()
    except Exception as exc:
        logger.warning("[Bootstrap] ML model pre-load failed (will load on demand): %s", exc)


def build_bot() -> ScalperBot:
    """Composition root for the trading bot runtime."""
    return ScalperBot()


def run_bot() -> None:
    _install_uvloop()
    _preload_ml_model()
    try:
        bot = build_bot()
        bot.run()
    except Exception as e:
        logger.critical(f"❌ FATAL: Bot crashed at startup: {e}\n{traceback.format_exc()}")
        raise
