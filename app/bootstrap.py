import logging
import traceback
from app.application.scalper_bot import ScalperBot

logger = logging.getLogger(__name__)


def build_bot() -> ScalperBot:
    """Composition root for the trading bot runtime."""
    return ScalperBot()


def run_bot() -> None:
    try:
        bot = build_bot()
        bot.run()
    except Exception as e:
        logger.critical(f"❌ FATAL: Bot crashed at startup: {e}\n{traceback.format_exc()}")
        raise
