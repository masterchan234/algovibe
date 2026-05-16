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
    NET_ZERO_PNL_FLOOR_IDR,
    NET_ZERO_MIN_UNREALIZED_LOSS_PCT,
    NET_ZERO_MIN_POSITIONS,
    NET_ZERO_AUTO_RESET_SEC,
    FEE_BUY_TAKER,
    FEE_SELL_TAKER,
)

logger = logging.getLogger(__name__)

# Callback signature: on_basket_exit(reason: str) -> None
BasketExitCallback = Callable[[str], None]


class NetZeroGuard:
    """
    Stateful Net-Zero Survival Guard.

    Call evaluate() on every EquityUpdate message received from the
    executive-core's equity subscriber.

    The guard is a pure profit-protection mechanism: it only arms *after*
    the caller signals that the daily target has been reached at least once
    (target_reached=True parameter on evaluate()).  Before that point both
    the hysteresis and the immediate-bypass paths are fully disabled.
    """

    def __init__(self, on_basket_exit: Optional[BasketExitCallback] = None):
        self._on_basket_exit = on_basket_exit
        self._trigger_ts: float = 0.0   # when the condition was first detected
        self._fired: bool = False        # one-shot per session guard (resettable)
        self._fired_at: float = 0.0     # unix ts when guard last fired
        self._enabled: bool = NET_ZERO_SURVIVAL_ENABLED
        # Rolling velocity buffer — same 5-reading window as ExitManager.
        self._velocity_readings: list = []
        # One-shot audit log flag.
        self._pretarget_logged: bool = False

    #  Public 

    def evaluate(
        self,
        net_pnl: float,
        velocity: float,
        now_ts: Optional[float] = None,
        target_reached: bool = False,
    ) -> bool:
        """
        Evaluate the Net-Zero condition for the current equity snapshot.

        Parameters
        ----------
        net_pnl:
            total_realized + total_unrealized (IDR).
        velocity:
            Latest velocity sample (IDR/second).  Accumulated internally in a
            5-reading rolling buffer; hysteresis uses the smoothed average while
            the immediate bypass uses the raw last reading.
        now_ts:
            Current Unix timestamp (defaults to time.time()).

        Returns
        -------
        bool
            True if the basket-exit callback was fired this call.
        """
        if not self._enabled:
            return False

        ts = now_ts or time.time()

        #  Guard is disarmed until target profit has been reached 
        # Before target_reached, individual per-position SL/DCA handles losses.
        if not target_reached:
            if self._trigger_ts != 0.0:
                self._trigger_ts = 0.0
            if not self._pretarget_logged:
                self._pretarget_logged = True
                logger.info(
                    "[NetZeroGuard] PRE-TARGET: guard disarmed. "
                    "Akan aktif setelah target harian tercapai."
                )
            return False

        #  Auto-recovery check 
        if self._fired and NET_ZERO_AUTO_RESET_SEC > 0:
            if self._fired_at > 0 and (ts - self._fired_at) >= NET_ZERO_AUTO_RESET_SEC:
                if net_pnl > NET_ZERO_PNL_FLOOR_IDR:
                    self._fired = False
                    self._trigger_ts = 0.0
                    logger.info(
                        f"[NetZeroGuard] Auto-recovery: net_pnl={net_pnl:+,.0f} above floor "
                        f"after {NET_ZERO_AUTO_RESET_SEC}s cooldown."
                    )

        if self._fired:
            return False

        # Maintain rolling velocity buffer (max 5 readings)
        self._velocity_readings.append(velocity)
        if len(self._velocity_readings) > 5:
            self._velocity_readings.pop(0)

        avg_velocity = (
            sum(self._velocity_readings) / len(self._velocity_readings)
            if self._velocity_readings else 0.0
        )

        #  Immediate bypass — uses raw last velocity for fastest crash detection 
        if (net_pnl <= NET_ZERO_PNL_FLOOR_IDR
                and velocity < NET_ZERO_IMMEDIATE_VELOCITY):
            logger.warning(
                f"[NetZeroGuard] IMMEDIATE EXIT: velocity={velocity:.1f} IDR/s "
                f"(threshold={NET_ZERO_IMMEDIATE_VELOCITY}) | net_pnl={net_pnl:+,.0f}"
            )
            self._fire("NET_ZERO_IMMEDIATE")
            return True

        #  Hysteresis condition — uses AVERAGE velocity (noise-filtered) 
        condition = (
            net_pnl <= NET_ZERO_PNL_FLOOR_IDR
            and avg_velocity < NET_ZERO_MOMENTUM_THRESHOLD
        )

        if not condition:
            self._trigger_ts = 0.0
            return False

        # Start or continue the hysteresis window
        if self._trigger_ts == 0.0:
            self._trigger_ts = ts
            logger.info(
                f"[NetZeroGuard] PROFIT_PROTECT: hysteresis started. "
                f"net_pnl={net_pnl:+,.0f} avg_velocity={avg_velocity:+.1f}"
            )
            return False

        elapsed = ts - self._trigger_ts
        if elapsed >= NET_ZERO_HYSTERESIS_SEC:
            logger.warning(
                f"[NetZeroGuard] PROFIT_PROTECT BASKET_EXIT after {elapsed:.1f}s hysteresis. "
                f"net_pnl={net_pnl:+,.0f} avg_velocity={avg_velocity:+.1f}"
            )
            self._fire("NET_ZERO_SURVIVAL")
            return True

        return False

    def reset_session(self) -> None:
        """Call at the start of each trading session to allow the guard to fire again."""
        self._fired = False
        self._fired_at = 0.0
        self._trigger_ts = 0.0
        self._velocity_readings.clear()
        self._pretarget_logged = False
        logger.info("[NetZeroGuard] Session reset.")

    #  Internal 

    def _fire(self, reason: str) -> None:
        self._fired = True
        self._fired_at = time.time()
        self._trigger_ts = 0.0
        if self._on_basket_exit:
            try:
                self._on_basket_exit(reason)
            except Exception as exc:
                logger.error(f"[NetZeroGuard] basket_exit callback raised: {exc}")
