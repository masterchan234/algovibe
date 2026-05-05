"""
services/executive_core/profit_lock.py — Dynamic Trailing Profit Lock
======================================================================
Implements the daily Profit Lock mechanism:

  Once daily net_pnl exceeds PROFIT_LOCK_THRESHOLD_IDR (default Rp 1 000 000),
  lock 50% of the session Peak Equity (ATH net_pnl) as an absolute floor.

  If net_pnl subsequently drops below the locked floor, a BASKET_EXIT is
  triggered to secure the locked profit.

Constants (all overridable via env vars):
  PROFIT_LOCK_THRESHOLD_IDR  = 1_000_000  IDR  (activates locking)
  PROFIT_LOCK_PCT            = 0.50            (lock fraction of ATH)
"""

import logging
import os
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Configuration (env overrides) ─────────────────────────────────────────────
PROFIT_LOCK_THRESHOLD_IDR = float(
    os.getenv("PROFIT_LOCK_THRESHOLD_IDR", "1000000")
)  # Rp 1 jt
PROFIT_LOCK_PCT = float(os.getenv("PROFIT_LOCK_PCT", "0.50"))  # 50%

# Callback signature: on_profit_lock_exit(reason: str, floor: float) -> None
ProfitLockCallback = Callable[[str, float], None]


class ProfitLock:
    """
    Dynamic Trailing Profit Lock.

    Call update() on every EquityUpdate to maintain the ATH and check
    whether the floor has been breached.
    """

    def __init__(self, on_profit_lock_exit: Optional[ProfitLockCallback] = None):
        self._on_exit = on_profit_lock_exit
        self._peak_pnl: float = 0.0       # session ATH net_pnl
        self._floor: float = 0.0          # locked profit floor
        self._active: bool = False         # True once threshold crossed
        self._fired: bool = False          # one-shot per session
        self._activated_at: float = 0.0    # timestamp when lock first activated

    # ── Public ────────────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def floor(self) -> float:
        """Current locked profit floor in IDR (0 if not yet active)."""
        return self._floor

    @property
    def peak_pnl(self) -> float:
        """Session ATH net_pnl in IDR."""
        return self._peak_pnl

    def update(self, net_pnl: float, now_ts: Optional[float] = None) -> bool:
        """
        Process a new net_pnl reading.

        Steps:
        1. Update ATH (peak_pnl).
        2. If peak_pnl >= threshold AND not yet active, activate the lock.
        3. Recalculate floor as PROFIT_LOCK_PCT × peak_pnl.
        4. If net_pnl < floor and lock is active, fire the exit callback.

        Returns
        -------
        bool
            True if the exit callback was fired this call.
        """
        if self._fired:
            return False

        ts = now_ts or time.time()

        # Update ATH
        if net_pnl > self._peak_pnl:
            self._peak_pnl = net_pnl

        # Activation check
        if not self._active and self._peak_pnl >= PROFIT_LOCK_THRESHOLD_IDR:
            self._active = True
            self._activated_at = ts
            logger.info(
                f"[ProfitLock] ACTIVATED — peak_pnl={self._peak_pnl:,.0f} IDR "
                f"(threshold={PROFIT_LOCK_THRESHOLD_IDR:,.0f})"
            )

        if not self._active:
            return False

        # Recalculate floor (trailing — rises with ATH, never drops)
        new_floor = self._peak_pnl * PROFIT_LOCK_PCT
        if new_floor > self._floor:
            if self._floor > 0:
                logger.info(
                    f"[ProfitLock] Floor raised: {self._floor:,.0f} → {new_floor:,.0f} IDR"
                )
            self._floor = new_floor

        # Breach check
        if net_pnl < self._floor:
            logger.warning(
                f"[ProfitLock] FLOOR BREACHED — net_pnl={net_pnl:,.0f} < floor={self._floor:,.0f}. "
                f"Triggering BASKET_EXIT."
            )
            self._fired = True
            if self._on_exit:
                try:
                    self._on_exit("PROFIT_LOCK_FLOOR_BREACH", self._floor)
                except Exception as exc:
                    logger.error(f"[ProfitLock] exit callback raised: {exc}")
            return True

        return False

    def reset_session(self) -> None:
        """Reset all state for a new trading session (UTC midnight roll)."""
        self._peak_pnl = 0.0
        self._floor = 0.0
        self._active = False
        self._fired = False
        self._activated_at = 0.0
        logger.info("[ProfitLock] Session reset.")

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "active": self._active,
            "peak_pnl": self._peak_pnl,
            "floor": self._floor,
            "fired": self._fired,
            "threshold": PROFIT_LOCK_THRESHOLD_IDR,
            "lock_pct": PROFIT_LOCK_PCT,
        }
