import asyncio
import atexit
import logging
import os
import traceback
from app.application.scalper_bot import ScalperBot
from config import DATA_PATH

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


_PID_FILE = os.path.join(DATA_PATH, "algovibe_bot.pid")


def _acquire_pid_lock() -> None:
    """Prevent two bot instances from running against the same database.

    Writes the current PID to DATA_PATH/algovibe_bot.pid.  If the file already
    exists and the recorded PID is still alive, the process aborts with a clear
    error message.  The PID file is removed automatically on clean exit via
    atexit, and also on the next startup if the old PID is no longer running
    (i.e. the previous instance crashed without cleanup).
    """
    pid_file = _PID_FILE
    current_pid = os.getpid()

    if os.path.exists(pid_file):
        old_pid = None
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            # Check whether the old PID is still alive
            os.kill(old_pid, 0)  # signal 0 = existence check only
            # If we reach here, the process is alive — abort.
            raise RuntimeError(
                f"[PID LOCK] Another Algovibe bot instance is already running "
                f"(PID {old_pid}).  Aborting to prevent DB corruption.\n"
                f"If the previous instance crashed, delete '{pid_file}' and restart."
            )
        except (ValueError, ProcessLookupError, PermissionError):
            # Old PID file is stale (process gone) — overwrite it.
            logger.warning(
                "[Bootstrap] Stale PID file found (PID %s no longer running). "
                "Overwriting with current PID %s.",
                old_pid,
                current_pid,
            )

    os.makedirs(DATA_PATH, exist_ok=True)
    with open(pid_file, "w") as f:
        f.write(str(current_pid))

    def _release_pid_lock():
        try:
            if os.path.exists(pid_file):
                with open(pid_file) as f:
                    stored = f.read().strip()
                if stored == str(current_pid):
                    os.remove(pid_file)
        except Exception:
            pass

    atexit.register(_release_pid_lock)
    logger.info("[Bootstrap] PID lock acquired (PID %s → %s).", current_pid, pid_file)


def run_bot() -> None:
    _acquire_pid_lock()
    _install_uvloop()
    _preload_ml_model()
    try:
        bot = build_bot()
        bot.run()
    except Exception as e:
        logger.critical(f"❌ FATAL: Bot crashed at startup: {e}\n{traceback.format_exc()}")
        raise
