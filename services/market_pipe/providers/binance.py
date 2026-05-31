"""
services/market_pipe/providers/binance.py — Binance Market Provider
====================================================================
Subclass IndodaxProvider dan override seluruh perilaku yang spesifik untuk
Binance: initial subscriptions via live top movers, dan mover refresh loop
setiap 5 menit.

Hanya dipakai saat env var EXCHANGE=binance.
"""

import logging
import time
import threading

import requests as _req

from services.market_pipe.providers.indodax import IndodaxProvider

logger = logging.getLogger(__name__)

# Pair stablecoin yang tidak perlu di-trade
_STABLECOIN_BASES = frozenset({
    'USDC', 'FDUSD', 'TUSD', 'USDP', 'BUSD', 'DAI', 'USDB', 'USD1',
    'XUSD', 'U', 'RLUSD', 'XAUT', 'PAXG', 'EUR', 'GBP', 'TRY',
})


class BinanceProvider(IndodaxProvider):
    """Market provider untuk Binance Spot exchange.

    Menggunakan live Binance public API untuk menentukan pair yang perlu
    di-watch: top gainers + losers + high-volume, di-refresh setiap 5 menit.
    """

    @property
    def exchange_id(self) -> str:
        return "binance"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._running = True
        self._mws.start()
        threading.Thread(target=self._initial_subscriptions, daemon=True).start()
        threading.Thread(target=self._mover_refresh_loop, daemon=True).start()
        threading.Thread(target=self._depth_broadcast_loop, daemon=True).start()
        logger.info("[BINANCE Provider] Started.")

    # ── Depth Broadcasting ────────────────────────────────────────────────────

    def _depth_broadcast_loop(self) -> None:
        """Broadcast bid/ask depth for all tickers every 1s via ZMQ as JSON."""
        import json as _json
        # Wait for WS to be ready
        time.sleep(5)
        while self._running:
            try:
                tickers = self._mws.get_tickers() or {}
                depth_payload = {}
                for pair, t in tickers.items():
                    bd = float(t.get('bid_depth', 0))
                    ad = float(t.get('ask_depth', 0))
                    if bd > 0 or ad > 0:
                        depth_payload[pair] = [bd, ad]
                if depth_payload and self._on_tick is not None:
                    raw = _json.dumps(depth_payload).encode()
                    try:
                        self._on_tick(raw, topic="depth")
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("[BINANCE Provider] Depth broadcast error: %s", e)
            time.sleep(1)

    # ── Initial Subscriptions ─────────────────────────────────────────────────

    def _initial_subscriptions(self) -> None:
        """Subscribe pair-pair dari live Binance movers + open positions."""
        # Tunggu sampai WS connected sebelum mulai bulk subscribe
        for _ in range(30):
            if self._mws.is_connected():
                break
            time.sleep(1)
        else:
            logger.warning("[BINANCE Provider] WS not connected after 30s, proceeding anyway.")
        time.sleep(0.5)  # sedikit buffer biar koneksi stabil

        from app.infrastructure.database import db

        watch_pair = 'btcusdt'
        db_ready = False
        for i in range(30):
            try:
                watch_pair = db.get_state('current_watch_pair') or 'btcusdt'
                with db.get_connection() as conn:
                    conn.execute("SELECT 1 FROM positions LIMIT 1")
                db_ready = True
                break
            except Exception as e:
                logger.warning("[BINANCE Provider] DB not ready (attempt %d/30): %s", i + 1, e)
                time.sleep(2)

        if not db_ready:
            logger.error("[BINANCE Provider] DB tables never became ready. Falling back.")

        pairs = {'btcusdt', str(watch_pair).lower()}
        try:
            with db.get_connection() as conn:
                rows = conn.execute("SELECT pair FROM positions").fetchall()
                for r in rows:
                    p = str(r['pair'] or '').lower().strip()
                    if p:
                        pairs.add(p)
        except Exception as e:
            logger.warning("[BINANCE Provider] Could not load open positions: %s", e)

        # Tambahkan live top movers dari Binance API
        pairs.update(self._fetch_live_mover_pairs())

        try:
            self._mws.bulk_subscribe_market_trades(set(pairs))
            subscribed = len(pairs)
        except Exception as e:
            logger.warning("[BINANCE Provider] Trade subscriptions failed: %s", e)

        logger.info("[BINANCE Provider] Initial subscriptions: %d pairs", subscribed)

    # ── Tick/Trade Handlers ───────────────────────────────────────────────────

    def _handle_tick_update(self, pair: str, data: dict) -> None:
        import time as _time
        from shared.algovibe_pb2 import Tick, ExchangeId  # type: ignore[attr-defined]
        try:
            ts_ms = int(_time.time() * 1000)
            price = float(data.get("last") or 0)
            if price <= 0:
                return
            tick = Tick()
            tick.symbol      = pair
            tick.price       = price
            tick.volume      = float(data.get("vol_coin") or 0)
            tick.timestamp   = ts_ms
            tick.exchange_id = ExchangeId.Value("EXCHANGE_BINANCE")
            tick.bid         = float(data.get("buy") or 0)
            tick.ask         = float(data.get("sell") or 0)
            tick.high_24h    = float(data.get("high") or 0)
            tick.low_24h     = float(data.get("low") or 0)
            tick.vol_idr     = float(data.get("vol_idr") or 0)
            latency_ms = 0.0
            try:
                epoch = data.get("epoch")
                if epoch:
                    latency_ms = (_time.time() - float(epoch)) * 1000.0
            except Exception:
                pass
            self._emit(tick.SerializeToString(), latency_ms=latency_ms)
            if not self._ready_event.is_set():
                self._ready_event.set()
        except Exception as e:
            logger.error("[BINANCE Provider] Tick error for %s: %s", pair, e)

    def _handle_trade_update(self, pair: str, trade_data: list) -> None:
        from shared.algovibe_pb2 import Trade, ExchangeId  # type: ignore[attr-defined]
        try:
            t = Trade()
            t.symbol      = pair
            t.timestamp   = int(trade_data[1] * 1000)
            t.tid         = str(trade_data[2])
            t.side        = str(trade_data[3])
            t.price       = float(trade_data[4])
            t.amount      = float(trade_data[6])
            t.exchange_id = ExchangeId.Value("EXCHANGE_BINANCE")
            logger.info("[BINANCE Provider] Emitting trade: %s", pair)
            self._emit_trade(t.SerializeToString())
        except Exception as e:
            logger.error("[BINANCE Provider] Trade error for %s: %s", pair, e)

    # ── Mover Logic ───────────────────────────────────────────────────────────

    def _fetch_live_mover_pairs(self, top_n_change: int = 30, top_n_vol: int = 20) -> set:
        """Fetch top gainers + losers + high-volume dari Binance public API.

        Returns set of normalised lowercase pair strings (e.g. 'xlmusdt').
        """
        try:
            resp = _req.get("https://api.binance.com/api/v3/ticker/24hr", timeout=8)
            if resp.status_code != 200:
                logger.warning("[BINANCE Provider] fetch_live_mover_pairs HTTP %d", resp.status_code)
                return set()
            raw = resp.json()
            candidates = []
            for t in raw:
                sym = str(t.get('symbol', ''))
                if not sym.endswith('USDT'):
                    continue
                if sym[:-4] in _STABLECOIN_BASES:
                    continue
                vol = float(t.get('quoteVolume', 0) or 0)
                if vol < 5_000_000:
                    continue
                change = float(t.get('priceChangePercent', 0) or 0)
                candidates.append((sym.lower(), change, vol))

            by_gain = sorted(candidates, key=lambda x: x[1], reverse=True)[:top_n_change]
            by_loss = sorted(candidates, key=lambda x: x[1])[:top_n_change]
            by_vol  = sorted(candidates, key=lambda x: x[2], reverse=True)[:top_n_vol]
            result  = {x[0] for x in by_gain + by_loss + by_vol}

            gainers_brief = " ".join(
                f"{x[0].replace('usdt', '')}({x[1]:+.1f}%)" for x in by_gain[:6]
            )
            losers_brief = " ".join(
                f"{x[0].replace('usdt', '')}({x[1]:+.1f}%)" for x in by_loss[:6]
            )
            logger.info(
                "[BINANCE Provider] Live movers: %d pairs | G: %s | L: %s",
                len(result), gainers_brief, losers_brief,
            )
            return result
        except Exception as e:
            logger.warning("[BINANCE Provider] _fetch_live_mover_pairs failed: %s", e)
            return set()

    def _mover_refresh_loop(self) -> None:
        """Subscribe ke top gainer/loser WS streams, di-refresh setiap 5 menit.

        Memastikan brain_inference mendapat data live untuk semua pair yang
        sedang trending di market.
        """
        # Tunggu initial subscriptions selesai dulu
        time.sleep(35)
        while self._running:
            target_pairs = self._fetch_live_mover_pairs()
            seeded = 0
            if self._running and target_pairs:
                try:
                    self._mws.bulk_subscribe_market_trades(set(target_pairs))
                    seeded = len(target_pairs)
                except Exception:
                    pass
            if seeded:
                logger.info("[BINANCE Provider] Mover refresh: %d pairs re-subscribed.", seeded)

            # Tunggu 5 menit sebelum refresh berikutnya
            for _ in range(60):
                if not self._running:
                    break
                time.sleep(5)
