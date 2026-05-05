"""
Resilience — Error Handling & Resilience system for the Algovibe Middleware Bridge.

Components
──────────
1. retry_with_backoff    — Exponential-backoff retry decorator for API calls.
2. CircuitBreaker        — Tracks consecutive exchange errors and latches the
                           system when the exchange is unreachable for > 60 s.
3. RateLimiter429        — Dynamic cooldown when the exchange returns 429.
4. ExecutionSafety       — Min-trade volume guard + insufficient-balance retry.
5. ShadowSync            — 5-minute reconciliation loop: exchange ↔ DB positions.
6. EmergencyNotifier     — CRITICAL / CIRCUIT_BREAKER alert dispatcher.
"""

import functools
import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

RETRY_MAX_RETRIES         = 3
RETRY_BASE_DELAY_SEC      = 1.0
RETRY_MULTIPLIER          = 2.0        # 1 s → 2 s → 4 s

CB_ERROR_WINDOW_SEC       = 60         # Trip breaker after this many seconds of continuous errors
CB_RESET_TIMEOUT_SEC      = 120        # Auto-transition to HALF_OPEN after this long

RATE_LIMIT_429_BASE_SLEEP = 5.0        # Initial cooldown on first 429
RATE_LIMIT_429_MAX_SLEEP  = 60.0       # Maximum cooldown cap
RATE_LIMIT_429_MULTIPLIER = 2.0        # Exponential factor for repeat 429s

SHADOW_SYNC_INTERVAL_SEC  = 300        # 5 minutes
SHADOW_SYNC_MIN_VALUE_IDR = 1_000      # Ignore dust balances below this IDR value

# HTTP status codes treated as server-side failures (circuit-breaker candidates)
_CB_HTTP_CODES = {500, 502, 503}

# Network-error substrings that indicate a server-side / connectivity issue
_CB_ERROR_SUBSTRINGS = (
    "connection error",
    "timeout",
    "read timed out",
    "remote end closed",
    "max retries exceeded",
)

# Indodax "insufficient balance" error keywords
_INSUF_BALANCE_KEYWORDS = ("insufficient", "not enough", "balance", "kurang")


# ── Retry with Backoff ────────────────────────────────────────────────────────

def retry_with_backoff(
    retries: int = RETRY_MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY_SEC,
    multiplier: float = RETRY_MULTIPLIER,
    retriable_exceptions: tuple = (RuntimeError,),
    circuit_breaker: "CircuitBreaker | None" = None,
):
    """Exponential-backoff retry decorator.

    Parameters
    ----------
    retries:               Maximum number of retry attempts (not counting the first try).
    base_delay:            Initial sleep time in seconds before the first retry.
    multiplier:            Factor by which the delay grows on each subsequent retry.
    retriable_exceptions:  Exception types that trigger a retry.
    circuit_breaker:       Optional CircuitBreaker — checked before every attempt.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc: Exception | None = None
            for attempt in range(retries + 1):
                if circuit_breaker and circuit_breaker.is_open():
                    raise RuntimeError(
                        f"[CircuitBreaker] OPEN — refusing call to {fn.__name__}. "
                        "Exchange is unreachable. Try again later."
                    )
                try:
                    result = fn(*args, **kwargs)
                    if circuit_breaker:
                        circuit_breaker.record_success()
                    return result
                except retriable_exceptions as exc:
                    last_exc = exc
                    if circuit_breaker:
                        circuit_breaker.record_failure(str(exc))
                    if attempt < retries:
                        logger.warning(
                            "[RetryBackoff] %s failed (attempt %d/%d): %s — retrying in %.1f s",
                            fn.__name__, attempt + 1, retries, exc, delay,
                        )
                        time.sleep(delay)
                        delay *= multiplier
                    else:
                        logger.error(
                            "[RetryBackoff] %s failed after %d retries: %s",
                            fn.__name__, retries, exc,
                        )
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class CircuitBreaker:
    """Three-state circuit breaker (CLOSED → OPEN → HALF_OPEN → CLOSED).

    State transitions
    ─────────────────
    CLOSED    → OPEN      : consecutive failures persist for > CB_ERROR_WINDOW_SEC.
    OPEN      → HALF_OPEN : CB_RESET_TIMEOUT_SEC has elapsed since tripping.
    HALF_OPEN → CLOSED    : next successful call resets the breaker.
    HALF_OPEN → OPEN      : next failed call re-trips the breaker.
    """

    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        error_window_sec: float = CB_ERROR_WINDOW_SEC,
        reset_timeout_sec: float = CB_RESET_TIMEOUT_SEC,
        name: str = "default",
    ):
        self._lock              = threading.Lock()
        self._state             = self.CLOSED
        self._first_error_ts: float | None = None   # ts of first error in current window
        self._tripped_at: float | None     = None   # ts when OPEN was entered
        self._error_window      = error_window_sec
        self._reset_timeout     = reset_timeout_sec
        self._name              = name
        self._notifier: "EmergencyNotifier | None" = None

    def attach_notifier(self, notifier: "EmergencyNotifier") -> None:
        """Inject an EmergencyNotifier so the breaker can fire alerts on trip."""
        self._notifier = notifier

    # ── Public interface ─────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        with self._lock:
            self._maybe_transition_half_open()
            return self._state

    def is_open(self) -> bool:
        """Return True if the breaker is OPEN (all calls should be rejected)."""
        with self._lock:
            self._maybe_transition_half_open()
            return self._state == self.OPEN

    def record_success(self) -> None:
        """Call after a successful API response to close / reset the breaker."""
        with self._lock:
            if self._state in (self.OPEN, self.HALF_OPEN):
                logger.info(
                    "[CircuitBreaker:%s] Recovery detected — resetting to CLOSED.", self._name
                )
                self._persist_state(self.CLOSED)
            self._state = self.CLOSED
            self._first_error_ts = None
            self._tripped_at = None

    def record_failure(self, reason: str = "") -> None:
        """Call after a retriable failure (5xx / Timeout) to potentially trip the breaker."""
        with self._lock:
            now = time.time()
            if self._state == self.CLOSED:
                if self._first_error_ts is None:
                    self._first_error_ts = now
                    logger.warning(
                        "[CircuitBreaker:%s] First error recorded: %s", self._name, reason
                    )
                elif now - self._first_error_ts > self._error_window:
                    self._trip(reason, now - self._first_error_ts)
            elif self._state == self.HALF_OPEN:
                self._trip(reason, 0)

    def get_status(self) -> dict:
        """Return current circuit breaker status as a serialisable dict."""
        with self._lock:
            self._maybe_transition_half_open()
            return {
                "name":              self._name,
                "state":             self._state,
                "first_error_at":    self._first_error_ts,
                "tripped_at":        self._tripped_at,
                "error_window_sec":  self._error_window,
                "reset_timeout_sec": self._reset_timeout,
            }

    # ── Private helpers ──────────────────────────────────────────────────────

    def _maybe_transition_half_open(self) -> None:
        """If OPEN and reset timeout has elapsed, move to HALF_OPEN (lock already held)."""
        if self._state == self.OPEN and self._tripped_at is not None:
            if time.time() - self._tripped_at >= self._reset_timeout:
                logger.info(
                    "[CircuitBreaker:%s] Reset timeout elapsed — entering HALF_OPEN.",
                    self._name,
                )
                self._state = self.HALF_OPEN
                self._persist_state(self.HALF_OPEN)

    def _trip(self, reason: str, elapsed: float) -> None:
        """Transition to OPEN and trigger emergency notifications (lock already held)."""
        self._state      = self.OPEN
        self._tripped_at = time.time()
        msg = (
            f"[CircuitBreaker:{self._name}] TRIPPED — "
            f"{elapsed:.0f}s of consecutive errors. Last error: {reason}"
        )
        logger.critical(msg)
        self._persist_state(self.OPEN, reason)
        if self._notifier:
            self._notifier.send(
                event_type="CIRCUIT_BREAKER",
                message=msg,
                level="CRITICAL",
            )
        self._set_system_pause()

    def _set_system_pause(self) -> None:
        """Persist SYSTEM_PAUSE state to the database (lock already held)."""
        try:
            from app.infrastructure.database import db
            db.save_state("session_mode", "SYSTEM_PAUSE")
            db.save_state("session_latched", "true")
            db.save_state("circuit_breaker_status", self.OPEN)
            db.save_state("circuit_breaker_tripped_at", str(self._tripped_at or ""))
        except Exception as exc:
            logger.warning("[CircuitBreaker] Could not persist SYSTEM_PAUSE to DB: %s", exc)

    def _persist_state(self, new_state: str, reason: str = "") -> None:
        """Update circuit_breaker_status in bot_state (lock already held)."""
        try:
            from app.infrastructure.database import db
            db.save_state("circuit_breaker_status", new_state)
            if new_state == self.CLOSED:
                db.save_state("circuit_breaker_tripped_at", "")
                # Only reset session_mode if it was set by the circuit breaker
                current_mode = db.get_state("session_mode") or ""
                if current_mode == "SYSTEM_PAUSE":
                    db.save_state("session_mode", "NORMAL")
                    db.save_state("session_latched", "false")
        except Exception as exc:
            logger.warning("[CircuitBreaker] Could not persist state to DB: %s", exc)


# ── Rate Limiter 429 ──────────────────────────────────────────────────────────

class RateLimiter429:
    """Tracks 429 responses and enforces exponential cooldown.

    Also provides a pre-throttle signal so the adapter can slow down before
    the exchange rejects additional requests.
    """

    def __init__(
        self,
        base_sleep: float = RATE_LIMIT_429_BASE_SLEEP,
        max_sleep: float  = RATE_LIMIT_429_MAX_SLEEP,
        multiplier: float = RATE_LIMIT_429_MULTIPLIER,
    ):
        self._lock              = threading.Lock()
        self._current_sleep     = base_sleep
        self._base_sleep        = base_sleep
        self._max_sleep         = max_sleep
        self._multiplier        = multiplier
        self._last_429_ts: float | None = None
        self._consecutive_429s  = 0

    def on_429(self) -> float:
        """Called when a 429 is received. Applies cooldown and returns duration slept."""
        with self._lock:
            self._consecutive_429s += 1
            self._last_429_ts = time.time()
            sleep_dur = min(self._current_sleep, self._max_sleep)
            logger.warning(
                "[RateLimiter429] 429 received (#%d consecutive) — cooling down for %.1f s",
                self._consecutive_429s, sleep_dur,
            )
            self._current_sleep = min(self._current_sleep * self._multiplier, self._max_sleep)
        time.sleep(sleep_dur)
        return sleep_dur

    def on_success(self) -> None:
        """Called on a successful response to decay the consecutive-429 counter."""
        with self._lock:
            if self._consecutive_429s > 0:
                self._consecutive_429s = max(0, self._consecutive_429s - 1)
                if self._consecutive_429s == 0:
                    self._current_sleep = self._base_sleep
                    logger.info("[RateLimiter429] Consecutive 429s cleared — cooldown reset.")

    def should_pre_throttle(self) -> bool:
        """Return True if a recent 429 suggests we should slow down proactively."""
        with self._lock:
            if self._consecutive_429s > 0 and self._last_429_ts is not None:
                # Pre-throttle for twice the current cooldown window after a 429
                return time.time() - self._last_429_ts < self._current_sleep * 2
            return False


# ── Execution Safety ──────────────────────────────────────────────────────────

class ExecutionSafety:
    """Guards for order placement safety checks."""

    INSUFFICIENT_DECREMENT = 0.015  # 1.5% size reduction on insufficient-balance retry

    @staticmethod
    def validate_min_trade(volume: float, price: float, min_trade_idr: float) -> bool:
        """Return True if the order meets the exchange minimum trade volume."""
        if price <= 0 or volume <= 0:
            return False
        return (volume * price) >= min_trade_idr

    @staticmethod
    def is_insufficient_balance_error(error_msg: str) -> bool:
        """Return True if the error message signals an insufficient-balance condition."""
        msg_lower = error_msg.lower()
        return any(kw in msg_lower for kw in _INSUF_BALANCE_KEYWORDS)

    @classmethod
    def adjusted_volume(cls, volume: float) -> float:
        """Return volume decremented by INSUFFICIENT_DECREMENT (1.5%)."""
        return volume * (1.0 - cls.INSUFFICIENT_DECREMENT)


# ── Shadow Sync ───────────────────────────────────────────────────────────────

class ShadowSync:
    """Periodic reconciliation between exchange balances and the DB positions table.

    Runs as a daemon thread every SHADOW_SYNC_INTERVAL_SEC (5 minutes).

    Reconciliation logic
    ────────────────────
    • If a coin is held on the exchange but NOT tracked in ``active_positions``,
      log a CRITICAL warning and fire an emergency alert.
    • If a position is tracked in DB but the coin is NOT on the exchange,
      log a CRITICAL warning.
    • After every pass: update ``account_balances`` to reflect exchange truth.
    """

    def __init__(
        self,
        get_balances_fn: Callable,
        interval_sec: float = SHADOW_SYNC_INTERVAL_SEC,
        notifier: "EmergencyNotifier | None" = None,
    ):
        """
        Parameters
        ----------
        get_balances_fn : Callable that returns ``list[dict]`` (each dict has
                          ``asset``, ``free``, ``used``, ``total`` keys).
        interval_sec    : Reconciliation interval in seconds (default 300).
        notifier        : EmergencyNotifier instance for CRITICAL alerts.
        """
        self._get_balances  = get_balances_fn
        self._interval      = interval_sec
        self._notifier      = notifier
        self._lock          = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running       = False

    def start(self) -> None:
        """Start the background reconciliation thread (idempotent)."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._loop,
                name="ShadowSync",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "[ShadowSync] Background reconciliation thread started (interval=%ds).",
                self._interval,
            )

    def stop(self) -> None:
        """Signal the background thread to stop after the current pass."""
        self._running = False

    def is_running(self) -> bool:
        """Return True if the background reconciliation thread is active."""
        return self._running

    def run_once(self) -> dict:
        """Perform a single reconciliation pass and return a summary dict."""
        return self._reconcile()

    # ── Private ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # Initial delay so the bot has time to fully initialise
        time.sleep(30)
        while self._running:
            try:
                self._reconcile()
            except Exception as exc:
                logger.error("[ShadowSync] Reconciliation error: %s", exc)
            time.sleep(self._interval)

    def _reconcile(self) -> dict:
        from app.infrastructure.database import db
        import config as cfg

        summary: dict[str, Any] = {"discrepancies": [], "updated": [], "error": None}

        try:
            # Skip in dry-run mode — no real exchange positions to verify
            if cfg.DRY_RUN:
                logger.debug("[ShadowSync] Dry-run mode — skipping reconciliation.")
                return summary

            # 1. Fetch live balances from exchange
            try:
                balances = self._get_balances()
            except Exception as exc:
                logger.warning("[ShadowSync] Could not fetch exchange balances: %s", exc)
                summary["error"] = str(exc)
                return summary

            # Build exchange balance map: ASSET.upper() → total_amount
            exchange_map: dict[str, float] = {
                b.get("asset", "").upper(): float(b.get("total", 0))
                for b in balances
                if float(b.get("total", 0)) > 0
            }

            # 2. Read active positions from DB
            try:
                positions = db.get_open_positions()
            except Exception as exc:
                logger.warning("[ShadowSync] Could not read positions from DB: %s", exc)
                summary["error"] = str(exc)
                return summary

            # 3. Detect discrepancies

            # Case A: coin held on exchange but NOT tracked in DB
            for asset, amount in exchange_map.items():
                if asset == "IDR":
                    continue
                pair_key = f"{asset.lower()}idr"
                if pair_key not in positions:
                    msg = (
                        f"[ShadowSync] DISCREPANCY — {asset} ({amount:.8f}) held on "
                        f"exchange but NOT tracked in active_positions (pair={pair_key})."
                    )
                    logger.critical(msg)
                    summary["discrepancies"].append(
                        {
                            "type":            "UNTRACKED_HOLDING",
                            "asset":           asset,
                            "pair":            pair_key,
                            "exchange_amount": amount,
                        }
                    )
                    if self._notifier:
                        self._notifier.send(
                            event_type="SHADOW_SYNC_DISCREPANCY",
                            message=msg,
                            level="CRITICAL",
                            pair=pair_key,
                        )

            # Case B: position tracked in DB but coin NOT on exchange
            for pair, pos in positions.items():
                asset = pair[:-3].upper()  # strip 'idr' suffix
                exchange_amount = exchange_map.get(asset, 0.0)
                db_amount = float(pos.get("amount_crypto") or 0)
                if db_amount > 1e-8 and exchange_amount < 1e-8:
                    msg = (
                        f"[ShadowSync] DISCREPANCY — {pair} tracked in DB "
                        f"({db_amount:.8f}) but NOT found on exchange."
                    )
                    logger.critical(msg)
                    summary["discrepancies"].append(
                        {
                            "type":            "GHOST_POSITION",
                            "asset":           asset,
                            "pair":            pair,
                            "db_amount":       db_amount,
                            "exchange_amount": exchange_amount,
                        }
                    )
                    if self._notifier:
                        self._notifier.send(
                            event_type="SHADOW_SYNC_DISCREPANCY",
                            message=msg,
                            level="CRITICAL",
                            pair=pair,
                        )

            # 4. Reconcile: update account_balances in DB to reflect exchange truth
            for b in balances:
                try:
                    with db.get_connection() as conn:
                        conn.execute(
                            """
                            INSERT INTO account_balances (asset, free, used, total, updated_at)
                            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                            ON CONFLICT(asset) DO UPDATE SET
                                free       = excluded.free,
                                used       = excluded.used,
                                total      = excluded.total,
                                updated_at = CURRENT_TIMESTAMP
                            """,
                            (
                                b.get("asset", "UNKNOWN"),
                                b.get("free", 0.0),
                                b.get("used", 0.0),
                                b.get("total", 0.0),
                            ),
                        )
                    summary["updated"].append(b.get("asset", "UNKNOWN"))
                except Exception as exc:
                    logger.warning(
                        "[ShadowSync] Could not update account_balances for %s: %s",
                        b.get("asset"),
                        exc,
                    )

            if summary["discrepancies"]:
                logger.critical(
                    "[ShadowSync] Deep Sync complete — %d discrepanc(ies) found.",
                    len(summary["discrepancies"]),
                )
            else:
                logger.info(
                    "[ShadowSync] Deep Sync complete — %d asset(s) reconciled, 0 discrepancies.",
                    len(summary["updated"]),
                )

            # Log sync result to DB
            try:
                db.log_system_event(
                    pair="SYSTEM",
                    event_type="SHADOW_SYNC",
                    message=(
                        f"Deep Sync: {len(summary['updated'])} assets reconciled, "
                        f"{len(summary['discrepancies'])} discrepancies."
                    ),
                    level="CRITICAL" if summary["discrepancies"] else "INFO",
                )
            except Exception as log_exc:
                logger.debug("[ShadowSync] Could not persist sync event to DB: %s", log_exc)

        except Exception as exc:
            logger.error("[ShadowSync] Unexpected error: %s", exc)
            summary["error"] = str(exc)
        return summary


# ── Emergency Notifier ────────────────────────────────────────────────────────

class EmergencyNotifier:
    """Dispatches CRITICAL / CIRCUIT_BREAKER alerts to all configured channels.

    Channels
    ────────
    1. Python logger (CRITICAL level)
    2. SQLite ``system_events`` table (for dashboard polling)
    3. Telegram bot
    4. SocketIO emit (optional — inject via ``set_socketio_fn``)
    """

    def __init__(self):
        self._socketio_fn: Callable | None = None
        self._fn_lock = threading.Lock()

    def set_socketio_fn(self, fn: Callable) -> None:
        """Inject a SocketIO emit callable so the notifier can push to the frontend.

        Thread-safe: protected by an internal lock.

        Example
        -------
        ::

            notifier.set_socketio_fn(
                lambda event, data: socketio.emit(event, data, room="public")
            )
        """
        with self._fn_lock:
            self._socketio_fn = fn

    def send(
        self,
        event_type: str,
        message: str,
        level: str = "CRITICAL",
        pair: str = "SYSTEM",
    ) -> None:
        """Dispatch an emergency alert to all configured channels.

        Parameters
        ----------
        event_type : Short event identifier, e.g. ``"CIRCUIT_BREAKER"``.
        message    : Human-readable description.
        level      : Log severity (``"CRITICAL"``, ``"WARNING"``, …).
        pair       : Trading pair or ``"SYSTEM"`` for exchange-wide events.
        """
        # 1. Python logger
        logger.critical("[EmergencyNotifier] %s | %s | %s", event_type, pair, message)

        # 2. SQLite
        try:
            from app.infrastructure.database import db
            db.log_system_event(pair=pair, event_type=event_type, message=message, level=level)
        except Exception as exc:
            logger.warning("[EmergencyNotifier] DB write failed: %s", exc)

        # 3. Telegram (non-blocking)
        try:
            from app.interfaces.telegram_bot import send_telegram_message
            send_telegram_message(f"🚨 *{event_type}*\n{message}")
        except Exception as exc:
            logger.warning("[EmergencyNotifier] Telegram send failed: %s", exc)

        # 4. SocketIO (if injected) — read under lock, call outside lock
        with self._fn_lock:
            emit_fn = self._socketio_fn
        if emit_fn:
            try:
                emit_fn(
                    event_type.lower(),
                    {"message": message, "level": level, "pair": pair},
                )
            except Exception as exc:
                logger.warning("[EmergencyNotifier] SocketIO emit failed: %s", exc)


# ── Shared singleton instances ────────────────────────────────────────────────

#: Shared EmergencyNotifier — used by CircuitBreaker, ShadowSync, and the bridge.
emergency_notifier = EmergencyNotifier()


# ── Auto-Resume Heartbeat ─────────────────────────────────────────────────────

# How often to ping the exchange while SYSTEM_PAUSE is active (seconds).
AUTO_RESUME_PING_INTERVAL_SEC = 300      # 5 minutes
# Number of consecutive successful pings required to auto-resume.
AUTO_RESUME_SUCCESS_THRESHOLD = 3


class AutoResumeHeartbeat:
    """Background task that pings the exchange every 5 minutes during SYSTEM_PAUSE.

    Recovery Protocol
    ─────────────────
    • When session_mode is SYSTEM_PAUSE, begin pinging the exchange health endpoint.
    • After AUTO_RESUME_SUCCESS_THRESHOLD consecutive successful pings, automatically:
        1. Reset session_mode → ACTIVE / NORMAL.
        2. Reset session_latched → false.
        3. Fire a Telegram alert and an EmergencyNotifier SUCCESS event.

    Usage
    ─────
    ::
        heartbeat = AutoResumeHeartbeat(ping_fn=trading_bridge.get_balances)
        heartbeat.start()
    """

    def __init__(
        self,
        ping_fn: "Callable",
        ping_interval_sec: float = AUTO_RESUME_PING_INTERVAL_SEC,
        success_threshold: int   = AUTO_RESUME_SUCCESS_THRESHOLD,
        notifier: "EmergencyNotifier | None" = None,
    ):
        """
        Parameters
        ----------
        ping_fn          : Callable that performs a lightweight exchange API call.
                           Should raise on failure, return any value on success.
        ping_interval_sec: Seconds between each ping while SYSTEM_PAUSE is active.
        success_threshold: Number of consecutive successes required to auto-resume.
        notifier         : EmergencyNotifier for sending alerts on resume.
        """
        self._ping_fn           = ping_fn
        self._interval          = ping_interval_sec
        self._threshold         = success_threshold
        self._notifier          = notifier or emergency_notifier
        self._lock              = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running           = False
        self._consecutive_ok    = 0

    def start(self) -> None:
        """Start the heartbeat daemon thread (idempotent)."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._loop,
                name="AutoResumeHeartbeat",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "[AutoResumeHeartbeat] Started (interval=%ds, threshold=%d pings).",
                self._interval, self._threshold,
            )

    def stop(self) -> None:
        """Signal the heartbeat thread to stop."""
        self._running = False

    # ── Private ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                session_mode = self._get_session_mode()
                if session_mode == "SYSTEM_PAUSE":
                    self._ping_once()
                else:
                    # Reset streak whenever we leave SYSTEM_PAUSE (graceful or manual)
                    if self._consecutive_ok > 0:
                        logger.debug(
                            "[AutoResumeHeartbeat] Session no longer SYSTEM_PAUSE — resetting streak."
                        )
                    self._consecutive_ok = 0
            except Exception as exc:
                logger.warning("[AutoResumeHeartbeat] Loop error: %s", exc)
            time.sleep(self._interval)

    def _ping_once(self) -> None:
        """Perform one exchange ping and handle the result."""
        try:
            self._ping_fn()
            self._consecutive_ok += 1
            logger.info(
                "[AutoResumeHeartbeat] Ping OK (%d/%d consecutive).",
                self._consecutive_ok, self._threshold,
            )
            if self._consecutive_ok >= self._threshold:
                self._trigger_auto_resume()
        except Exception as exc:
            logger.warning(
                "[AutoResumeHeartbeat] Ping FAILED (streak reset): %s", exc
            )
            self._consecutive_ok = 0

    def _trigger_auto_resume(self) -> None:
        """Restore trading state after sufficient consecutive ping successes."""
        self._consecutive_ok = 0  # Reset streak immediately to avoid repeated fires.
        try:
            from app.infrastructure.database import db
            db.save_state("session_mode",    "NORMAL")
            db.save_state("session_latched", "false")
            db.save_state("circuit_breaker_status", "CLOSED")
        except Exception as exc:
            logger.error("[AutoResumeHeartbeat] Could not persist resume state: %s", exc)
            return

        msg = (
            f"✅ *AUTO-RESUME SUCCESS* ✅\n"
            f"{AUTO_RESUME_SUCCESS_THRESHOLD} consecutive successful API pings.\n"
            f"Session restored to NORMAL mode. Trading resumed."
        )
        logger.critical("[AutoResumeHeartbeat] %s", msg.replace("*", "").replace("\n", " | "))

        # Telegram alert
        try:
            from app.interfaces.telegram_bot import send_telegram_message
            send_telegram_message(msg)
        except Exception as exc:
            logger.warning("[AutoResumeHeartbeat] Telegram alert failed: %s", exc)

        # EmergencyNotifier (dashboard + SocketIO)
        self._notifier.send(
            event_type="AUTO_RESUME_SUCCESS",
            message=msg.replace("*", "").replace("\n", " "),
            level="INFO",
        )

    @staticmethod
    def _get_session_mode() -> str:
        try:
            from app.infrastructure.database import db
            return (db.get_state("session_mode") or "NORMAL").upper()
        except Exception:
            return "NORMAL"

