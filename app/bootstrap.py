from app.application.scalper_bot import ScalperBot


def build_bot() -> ScalperBot:
    """Composition root for the trading bot runtime."""
    return ScalperBot()


def run_bot() -> None:
    bot = build_bot()
    bot.run()
