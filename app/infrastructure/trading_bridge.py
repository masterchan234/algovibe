"""
TradingBridge — Exchange-agnostic middleware between the execution engine
                and exchange adapters.

Responsibilities
────────────────
1. Routing      — Instantiates the correct adapter for the configured
                  EXCHANGE value (env var or DB setting).
2. Algorithm    — Net PnL, Momentum state, Break-even checks, and fee
                  accounting are centralised here so they stay consistent
                  across all exchanges.
3. Fee handler  — Fetches exchange-specific fee structure and applies it
                  automatically during PnL calculations so that Target
                  Floor Protection remains accurate regardless of venue.
4. Persistence  — After every bridge action (place order, cancel, close
                  position) the relevant algotrade_live.db tables
                  (order_book, account_balances, active_positions) are
                  kept in sync for the Live Dashboard.

Usage
─────
    bridge = TradingBridge()                         # reads EXCHANGE from config/DB
    balance = bridge.get_balance()
    order   = bridge.place_order("btcidr", "buy", "limit", 0.001, 1_500_000_000)
    pnl     = bridge.calculate_net_pnl(
                  entry_price=1_500_000_000,
                  exit_price=1_545_000_000,
                  amount_crypto=0.001,
              )
"""

import logging
import os
import time
from typing import Any

from app.infrastructure.resilience import ExecutionSafety, ShadowSync, emergency_notifier

logger = logging.getLogger(__name__)

# ── Supported exchanges ───────────────────────────────────────────────────────
_REGISTRY: dict[str, type] = {}

def _register(exchange_id: str):
    """Class decorator: registers an adapter class in the bridge registry."""
    def _decorator(cls):
        _REGISTRY[exchange_id.lower()] = cls
        return cls
    return _decorator


# Import adapters — each one self-registers via the decorator above.
# We do this lazily to avoid circular imports at module level.
def _load_adapters():
    """Lazily import all known adapter modules so they can register."""
    from app.infrastructure.providers.indodax_adapter import IndodaxAdapter  # noqa: F401
    _REGISTRY.setdefault("indodax", IndodaxAdapter)


# ── TradingBridge ─────────────────────────────────────────────────────────────

class TradingBridge:
    """Exchange-agnostic middleware hub.

    Parameters
    ----------
    exchange_id : str | None
        Which exchange to use.  When *None* the bridge reads the value from
        the DB (``bot_state.exchange_id``) or the ``EXCHANGE`` env var,
        falling back to ``'indodax'``.
    api_key : str | None
        Exchange API key.  Defaults to ``INDODAX_API_KEY`` env var.
    api_secret : str | None
        Exchange API secret.  Defaults to ``INDODAX_API_SECRET`` env var.
    """

    def __init__(
        self,
        exchange_id: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        _load_adapters()

        resolved_id = (
            exchange_id
            or self._read_exchange_id_from_db()
            or os.getenv("EXCHANGE", "indodax")
        ).lower()

        if resolved_id not in _REGISTRY:
            logger.warning(
                "[TradingBridge] Unknown exchange '%s', falling back to 'indodax'.",
                resolved_id,
            )
            resolved_id = "indodax"

        adapter_cls = _REGISTRY[resolved_id]

        # Resolve credentials — check exchange-specific env vars first, then
        # fall back to the generic EXCHANGE_API_KEY / EXCHANGE_API_SECRET pair
        # so the bridge works correctly for any configured exchange.
        env_prefix = resolved_id.upper()
        key = (
            api_key
            or os.getenv(f"{env_prefix}_API_KEY")
            or os.getenv("EXCHANGE_API_KEY", "")
        )
        secret = (
            api_secret
            or os.getenv(f"{env_prefix}_API_SECRET")
            or os.getenv("EXCHANGE_API_SECRET", "")
        )

        self._adapter = adapter_cls(key, secret)
        self._exchange_id = resolved_id

        # ── Resilience: Shadow Sync ───────────────────────────────────────────
        self._shadow_sync = ShadowSync(
            get_balances_fn=self.get_all_balances,
            notifier=emergency_notifier,
        )

        logger.info("[TradingBridge] Initialised with exchange='%s'.", resolved_id)

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _read_exchange_id_from_db() -> str | None:
        """Try to read the active exchange from the DB without hard-crashing."""
        try:
            from app.infrastructure.database import db  # avoid circular import at module level
            return db.get_state("exchange_id")
        except Exception:
            return None

    @property
    def adapter(self):
        """Direct access to the underlying exchange adapter (use sparingly)."""
        return self._adapter

    # ── Fee helpers ──────────────────────────────────────────────────────────

    def get_exchange_fees(self) -> dict:
        """Return the fee structure for the active exchange.

        Returns
        -------
        dict
            {maker_buy, taker_buy, maker_sell, taker_sell,
             round_trip_taker, slippage_buffer}
        """
        return self._adapter.fee_structure

    def get_round_trip_fee(self, taker: bool = True) -> float:
        """Return the round-trip fee fraction for a taker or maker trade."""
        fees = self.get_exchange_fees()
        if taker:
            return fees["round_trip_taker"]
        return fees["maker_buy"] + fees["maker_sell"]

    # ── PnL & Momentum calculations ──────────────────────────────────────────

    def calculate_net_pnl(
        self,
        entry_price: float,
        exit_price: float,
        amount_crypto: float,
        taker: bool = True,
    ) -> dict:
        """Calculate Net PnL after all exchange fees.

        Parameters
        ----------
        entry_price:   Price at which the position was opened (IDR/unit).
        exit_price:    Current or closing price (IDR/unit).
        amount_crypto: Quantity of the base asset held.
        taker:         True → apply taker fees, False → maker fees.

        Returns
        -------
        dict
            {
                "gross_pnl_idr":   float,   # raw price difference × amount
                "fee_idr":         float,   # total fee cost in IDR
                "net_pnl_idr":     float,   # profit after fees
                "net_pnl_pct":     float,   # fraction relative to cost basis
                "break_even_pct":  float,   # minimum gross % to be profitable
                "is_profitable":   bool,
                "entry_price":     float,
                "exit_price":      float,
                "amount_crypto":   float,
                "fee_pct":         float,   # round-trip fee fraction used
            }
        """
        fees = self.get_exchange_fees()
        fee_pct = fees["round_trip_taker"] if taker else (fees["maker_buy"] + fees["maker_sell"])
        slippage = fees["slippage_buffer"]
        break_even_pct = fee_pct + slippage

        cost_basis   = entry_price * amount_crypto
        exit_value   = exit_price  * amount_crypto
        gross_pnl    = exit_value  - cost_basis
        fee_idr      = cost_basis  * fee_pct
        net_pnl      = gross_pnl   - fee_idr
        net_pnl_pct  = (net_pnl / cost_basis) if cost_basis > 0 else 0.0

        return {
            "gross_pnl_idr":  round(gross_pnl,   2),
            "fee_idr":        round(fee_idr,      2),
            "net_pnl_idr":    round(net_pnl,      2),
            "net_pnl_pct":    round(net_pnl_pct,  6),
            "break_even_pct": round(break_even_pct, 6),
            "is_profitable":  net_pnl > 0,
            "entry_price":    entry_price,
            "exit_price":     exit_price,
            "amount_crypto":  amount_crypto,
            "fee_pct":        fee_pct,
        }

    def calculate_momentum(
        self,
        pnl_history: list[float],
        interval_sec: float = 5.0,
        recovery_threshold: float = 50.0,
    ) -> dict:
        """Derive momentum state from a rolling PnL history.

        Parameters
        ----------
        pnl_history:         Ordered list of Net PnL samples (IDR), newest last.
        interval_sec:        Seconds between consecutive samples.
        recovery_threshold:  IDR/sec velocity required to classify as IMPROVING.

        Returns
        -------
        dict
            {"state": "IMPROVING"|"STALLING"|"DROPPING", "velocity": float}
        """
        if len(pnl_history) < 2:
            return {"state": "STALLING", "velocity": 0.0}

        velocities = [
            (pnl_history[i] - pnl_history[i - 1]) / interval_sec
            for i in range(1, len(pnl_history))
        ]
        avg_velocity = sum(velocities) / len(velocities)

        if avg_velocity > recovery_threshold:
            state = "IMPROVING"
        elif avg_velocity < -recovery_threshold:
            state = "DROPPING"
        else:
            state = "STALLING"

        return {"state": state, "velocity": round(avg_velocity, 4)}

    # ── Bridge actions — each one calls the adapter + persists to DB ─────────

    def get_balance(self) -> dict:
        """Fetch balance and persist to account_balances table."""
        balance = self._adapter.get_balance()
        self._persist_balance(balance)
        return balance

    def get_all_balances(self) -> list[dict]:
        """Fetch all asset balances and persist each to account_balances."""
        balances = self._adapter.get_all_balances()
        for b in balances:
            self._persist_balance(b)
        return balances

    def get_ticker(self, symbol: str) -> dict:
        """Pass-through to adapter; no persistence needed for ticker data."""
        return self._adapter.get_ticker(symbol)

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        volume: float,
        price: float | None = None,
    ) -> dict:
        """Place an order via the adapter and persist the result to order_book.

        Resilience features
        ───────────────────
        • Min-trade volume guard: rejects orders below MIN_TRADE_IDR.
        • Insufficient-balance retry: on a BUY 'insufficient balance' error,
          decrements volume by 1.5 % and retries once.

        Parameters match BaseExchangeAdapter.place_order.

        Returns
        -------
        dict — unified order response (see BaseExchangeAdapter docstring).
        """
        from config import MIN_TRADE_IDR

        # ── Resolve execution price for pre-flight checks ─────────────────────
        exec_price = price or 0.0
        if exec_price <= 0:
            try:
                ticker = self._adapter.get_ticker(symbol)
                exec_price = ticker["ask"] if side.lower() == "buy" else ticker["bid"]
            except Exception:
                pass  # If ticker fetch fails, skip the min-trade pre-check

        def _check_min_trade(vol: float) -> None:
            """Raise ValueError if vol × exec_price is below the minimum."""
            if exec_price > 0 and not ExecutionSafety.validate_min_trade(
                vol, exec_price, MIN_TRADE_IDR
            ):
                raise ValueError(
                    f"[ExecutionSafety] Order {side.upper()} {symbol.upper()} "
                    f"vol={vol:.8f} × price={exec_price:.0f} = {vol * exec_price:.0f} IDR "
                    f"is below MIN_TRADE_IDR={MIN_TRADE_IDR}."
                )

        # ── Min-trade volume guard ────────────────────────────────────────────
        _check_min_trade(volume)

        # ── Place order with insufficient-balance retry (BUY only) ───────────
        actual_volume = volume
        try:
            order = self._adapter.place_order(symbol, side, order_type, actual_volume, price)
        except RuntimeError as exc:
            if side.lower() == "buy" and ExecutionSafety.is_insufficient_balance_error(
                str(exc)
            ):
                adjusted = ExecutionSafety.adjusted_volume(actual_volume)
                logger.warning(
                    "[TradingBridge] Insufficient balance for BUY %s vol=%.8f — "
                    "retrying with reduced vol=%.8f (−%.1f%%)",
                    symbol.upper(),
                    actual_volume,
                    adjusted,
                    ExecutionSafety.INSUFFICIENT_DECREMENT * 100,
                )
                # Verify adjusted size still meets min-trade before retrying
                try:
                    _check_min_trade(adjusted)
                except ValueError:
                    logger.error(
                        "[TradingBridge] Adjusted vol %.8f × %.0f = %.0f IDR is still below "
                        "MIN_TRADE_IDR=%d. Aborting.",
                        adjusted,
                        exec_price,
                        adjusted * exec_price,
                        MIN_TRADE_IDR,
                    )
                    raise exc  # re-raise the original insufficient-balance error
                order = self._adapter.place_order(
                    symbol, side, order_type, adjusted, price
                )
                actual_volume = adjusted
            else:
                raise

        self._persist_order(order)
        logger.info(
            "[TradingBridge] Order placed: %s %s %s vol=%.8f price=%s → id=%s",
            side.upper(), order_type.upper(), symbol.upper(),
            actual_volume, price, order.get("order_id"),
        )
        return order

    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """Return open orders via the adapter (no persistence, read-only)."""
        return self._adapter.get_open_orders(symbol)

    def cancel_order(self, order_id: str, symbol: str | None = None) -> dict:
        """Cancel an order via the adapter and update order_book status."""
        result = self._adapter.cancel_order(order_id, symbol)
        if result.get("success"):
            self._update_order_status(order_id, "cancelled")
            logger.info("[TradingBridge] Order %s cancelled.", order_id)
        else:
            logger.warning("[TradingBridge] Cancel failed for order %s.", order_id)
        return result

    def close_position(
        self,
        pair: str,
        amount_crypto: float,
        entry_price: float,
        exit_price: float | None = None,
        taker: bool = True,
    ) -> dict:
        """Issue a sell order to close a position and calculate Net PnL.

        Parameters
        ----------
        pair:          Trading pair, e.g. 'btcidr'.
        amount_crypto: Quantity to sell.
        entry_price:   Original buy price (for PnL calculation).
        exit_price:    Override exit price; if None, fetches live ask price.
        taker:         Whether this is a taker (market) or maker (limit) exit.

        Returns
        -------
        dict
            Merged order response + PnL breakdown + position update status.
        """
        if exit_price is None:
            ticker = self._adapter.get_ticker(pair)
            exit_price = ticker["bid"]

        order = self.place_order(
            symbol=pair,
            side="sell",
            order_type="market" if taker else "limit",
            volume=amount_crypto,
            price=exit_price,
        )

        pnl = self.calculate_net_pnl(entry_price, exit_price, amount_crypto, taker=taker)
        self._persist_position_close(pair, pnl)

        result = {**order, "pnl": pnl}
        logger.info(
            "[TradingBridge] Position closed: %s | Net PnL: Rp %.2f (%.4f%%)",
            pair.upper(), pnl["net_pnl_idr"], pnl["net_pnl_pct"] * 100,
        )
        return result

    def test_connection(self) -> bool:
        """Proxy the adapter's connectivity test."""
        return self._adapter.test_connection()

    def start_shadow_sync(self) -> None:
        """Start the background Shadow Sync reconciliation thread.

        Call this once after the bridge is initialised (e.g. in main.py or
        scalper_bot.py).  The thread is a daemon and will stop when the
        process exits.
        """
        self._shadow_sync.start()

    def get_resilience_status(self) -> dict:
        """Return resilience / circuit-breaker status for monitoring endpoints.

        Returns
        -------
        dict
            {
                "circuit_breaker": { state, first_error_at, tripped_at, … },
                "shadow_sync_running": bool,
            }
        """
        cb_status = {}
        try:
            cb_status = self._adapter.get_circuit_breaker_status()  # type: ignore[attr-defined]
        except AttributeError:
            pass
        return {
            "circuit_breaker":    cb_status,
            "shadow_sync_running": self._shadow_sync.is_running(),
        }

    # ── DB persistence helpers ────────────────────────────────────────────────

    @staticmethod
    def _get_db():
        """Import the DB router lazily to avoid circular imports."""
        from app.infrastructure.database import db  # noqa: PLC0415
        return db

    def _persist_balance(self, balance: dict) -> None:
        """Upsert a single asset balance row in account_balances."""
        try:
            db = self._get_db()
            with db._active().get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO account_balances (asset, free, used, total, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(asset) DO UPDATE SET
                        free       = excluded.free,
                        used       = excluded.used,
                        total      = excluded.total,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        balance.get("asset", "UNKNOWN"),
                        balance.get("free", 0.0),
                        balance.get("used", 0.0),
                        balance.get("total", 0.0),
                    ),
                )
        except Exception as exc:
            logger.warning("[TradingBridge] _persist_balance failed: %s", exc)

    def _persist_order(self, order: dict) -> None:
        """Insert or update a row in the order_book table."""
        try:
            db = self._get_db()
            db.upsert_order_book(
                order_id=str(order.get("order_id", "")),
                pair=order.get("symbol", "").lower(),
                side=order.get("side", ""),
                price=float(order.get("price", 0)),
                amount=float(order.get("volume", 0)),
                status=order.get("status", "open"),
                raw_response=order.get("raw", {}),
            )
        except Exception as exc:
            logger.warning("[TradingBridge] _persist_order failed: %s", exc)

    def _update_order_status(self, order_id: str, status: str) -> None:
        """Update the status column for an existing order_book row."""
        try:
            db = self._get_db()
            with db._active().get_connection() as conn:
                conn.execute(
                    "UPDATE order_book SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE order_id = %s",
                    (status, order_id),
                )
        except Exception as exc:
            logger.warning("[TradingBridge] _update_order_status failed: %s", exc)

    def _persist_position_close(self, pair: str, pnl: dict) -> None:
        """Log a system event when a position is closed via the bridge."""
        try:
            db = self._get_db()
            db.log_system_event(
                pair=pair.lower(),
                event_type="BRIDGE_CLOSE",
                message=(
                    f"Net PnL: Rp {pnl['net_pnl_idr']:.2f} "
                    f"({pnl['net_pnl_pct']*100:.4f}%) | "
                    f"Fee: Rp {pnl['fee_idr']:.2f}"
                ),
                level="INFO",
            )
        except Exception as exc:
            logger.warning("[TradingBridge] _persist_position_close log failed: %s", exc)
