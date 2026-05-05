"""
services/executive_core/net_zero.py — Net-Zero Survival Guard
==============================================================
Subscribes to the real-time EquityUpdate ZMQ stream and fires an emergency
BASKET_EXIT when both conditions are simultaneously true:

  1. net_pnl  ≤  0  (today's realised + current unrealised is ≤ 0 IDR)
  2. velocity  <  NET_ZERO_MOMENTUM_THRESHOLD  (portfolio is dropping)

The guard uses a hysteresis window (NET_ZERO_HYSTERESIS_SEC) to avoid false
positives on transient dips.  If velocity falls below the extreme threshold
(NET_ZERO_IMMEDIATE_VELOCITY), the basket exit fires instantly — no wait.

This module is purely the *detection* layer.  Actual order placement is
delegated to the ExecutiveCoreService via the on_basket_exit callback.
"""

import logging
import time
from typing import Callable, Optional

import sys, os
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import (
    NET_ZERO_SURVIVAL_ENABLED,
    NET_ZERO_MOMENTUM_THRESHOLD,
    NET_ZERO_HYSTERESIS_SEC,
    NET_ZERO_IMMEDIATE_VELOCITY,
)

logger = logging.getLogger(__name__)

# Callback signature: on_basket_exit(reason: str) -> None
BasketExitCallback = Callable[[str], None]


class NetZeroGuard:
    """
    Stateful Net-Zero Survival Guard.

    Call evaluate() on every EquityUpdate message received from the
    executive-core's equity subscriber.
    """

    def __init__(self, on_basket_exit: Optional[BasketExitCallback] = None):
        self._on_basket_exit = on_basket_exit
        self._trigger_ts: float = 0.0   # when the condition was first detected
        self._fired: bool = False        # one-shot per session guard
        self._enabled: bool = NET_ZERO_SURVIVAL_ENABLED

    # ── Public ────────────────────────────────────────────────────────────────

    def evaluate(
        self,
        net_pnl: float,
        velocity: float,
        now_ts: Optional[float] = None,
    ) -> bool:
        """
        Evaluate the Net-Zero condition for the current equity snapshot.

        Parameters
        ----------
        net_pnl:
            total_realized + total_unrealized (IDR).
        velocity:
            Portfolio value velocity in IDR/second (negative = falling).
        now_ts:
            Current Unix timestamp (defaults to time.time()).

        Returns
        -------
        bool
            True if the basket-exit callback was fired this call.
        """
        if not self._enabled or self._fired:
            return False

        ts = now_ts or time.time()

        condition = (
            net_pnl <= 0
            and velocity < NET_ZERO_MOMENTUM_THRESHOLD
        )

        if not condition:
            self._trigger_ts = 0.0
            return False

        # Immediate bypass — no hysteresis
        if velocity < NET_ZERO_IMMEDIATE_VELOCITY:
            logger.warning(
                f"[NetZeroGuard] IMMEDIATE EXIT: velocity={velocity:.1f} IDR/s "
                f"(threshold={NET_ZERO_IMMEDIATE_VELOCITY}) | net_pnl={net_pnl:.2f}"
            )
            self._fire("NET_ZERO_IMMEDIATE")
            return True

        # Start or continue the hysteresis window
        if self._trigger_ts == 0.0:
            self._trigger_ts = ts
            logger.info(
                f"[NetZeroGuard] Condition triggered — starting {NET_ZERO_HYSTERESIS_SEC}s hysteresis. "
                f"net_pnl={net_pnl:.2f} velocity={velocity:.1f}"
            )
            return False

        elapsed = ts - self._trigger_ts
        if elapsed >= NET_ZERO_HYSTERESIS_SEC:
            logger.warning(
                f"[NetZeroGuard] BASKET_EXIT after {elapsed:.1f}s hysteresis. "
                f"net_pnl={net_pnl:.2f} velocity={velocity:.1f}"
            )
            self._fire("NET_ZERO_SURVIVAL")
            return True

        return False

    def reset_session(self) -> None:
        """
        Call at the start of each trading session to allow the guard to fire again.
        """
        self._fired = False
        self._trigger_ts = 0.0
        logger.info("[NetZeroGuard] Session reset.")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fire(self, reason: str) -> None:
        self._fired = True
        self._trigger_ts = 0.0
        if self._on_basket_exit:
            try:
                self._on_basket_exit(reason)
            except Exception as exc:
                logger.error(f"[NetZeroGuard] basket_exit callback raised: {exc}")
