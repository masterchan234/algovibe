"""
services/litert_lm_decision/main.py — Grounded LiteRT-LM Exit Decision Service
===============================================================================
Reads market + open-position context, runs LiteRT-LM structured analysis, then
publishes SELL signals to ipc:///tmp/signal_stream.ipc.

Exit execution remains in executive-core via ExitManager.

Run:
    python -m services.litert_lm_decision.main
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import defaultdict, deque

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.domain.technicals import derive_trade_signal, get_technical_summary
from app.infrastructure.database import db
from config import (
    FEE_BUY_TAKER,
    FEE_SELL_TAKER,
    MODEL_CACHE_DIR,
    MODEL_PATH,
    ROUND_TRIP_FEE_PCT,
    SMART_BREAK_EVEN_PCT,
)
from shared.algovibe_pb2 import Signal, SignalAction, SignalSource, Tick  # type: ignore[attr-defined]
from shared.transport import IPC_MARKET, IPC_SIGNAL, ZMQPublisher, ZMQSubscriber, install_uvloop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("litert-lm-decision")

install_uvloop()

_ALLOWED_EVIDENCE = {
    "TECH_SELL",
    "NEGATIVE_PNL_AFTER_FEES",
    "MOMENTUM_DOWN",
    "ORDERBOOK_ASK_DOMINANCE",
    "FEE_HEADWIND",
}

_MIN_TECH_SELL_SCORE = 5.0
_MIN_REASON_LENGTH = 8
_MIN_TRUE_CONDITIONS = 2
_MIN_SUPPORTED_EVIDENCE = 1


class LiteRTLmDecisionService:
    def __init__(self):
        self._tick_sub = ZMQSubscriber(IPC_MARKET)
        self._signal_pub = ZMQPublisher(IPC_SIGNAL)
        self._running = False

        self._tickers = {}
        self._history = defaultdict(lambda: deque(maxlen=120))
        self._last_emitted = {}  # {pair: timestamp}

        self._interval_sec = int(os.getenv("LLM_EXIT_INTERVAL_SEC", "20"))
        self._pair_cooldown_sec = int(os.getenv("LLM_EXIT_COOLDOWN_SEC", "45"))
        self._min_confidence = float(os.getenv("LLM_EXIT_MIN_CONFIDENCE", "80"))
        self._max_pairs_per_cycle = int(os.getenv("LLM_EXIT_MAX_PAIRS", "4"))

        self._engine = None
        self._engine_enabled = self._init_engine()

    def _init_engine(self) -> bool:
        if not os.path.exists(MODEL_PATH):
            logger.warning("[litert-lm-decision] Model not found: %s", MODEL_PATH)
            return False
        try:
            from litert_lm import Engine  # lazy import

            os.makedirs(MODEL_CACHE_DIR, exist_ok=True)
            self._engine = Engine(MODEL_PATH, cache_dir=MODEL_CACHE_DIR)
            logger.info("[litert-lm-decision] ✅ LiteRT-LM ready.")
            return True
        except Exception as exc:
            logger.error("[litert-lm-decision] Failed to init LiteRT-LM: %s", exc)
            return False

    async def start(self):
        self._running = True
        self._tick_sub.start()
        self._signal_pub.start()
        self._stop_event = asyncio.Event()

        logger.info("[litert-lm-decision] ✅ Started.")
        await asyncio.gather(
            self._consume_ticks(),
            self._decision_loop(),
            self._stop_event.wait(),
        )

    def stop(self):
        self._running = False
        self._tick_sub.stop()
        self._signal_pub.stop()
        if hasattr(self, "_stop_event"):
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self._stop_event.set)
            except RuntimeError:
                pass
        logger.info("[litert-lm-decision] Stopped.")

    async def _consume_ticks(self):
        async for raw in self._tick_sub.aiter_messages():
            if not self._running:
                break
            try:
                tick = Tick()
                tick.ParseFromString(raw)
                pair = str(tick.symbol or "").lower()
                if not pair:
                    continue
                last = float(tick.price or 0)
                if last <= 0:
                    continue
                self._tickers[pair] = {
                    "last": last,
                    "bid": float(tick.bid or last),
                    "ask": float(tick.ask or last),
                    "high": float(tick.high_24h or last),
                    "low": float(tick.low_24h or last),
                    "vol_idr": float(tick.vol_idr or 0),
                }
                self._history[pair].append(last)
            except Exception as exc:
                logger.debug("[litert-lm-decision] Tick parse error: %s", exc)

    async def _decision_loop(self):
        while self._running:
            try:
                await self._evaluate_cycle()
            except Exception as exc:
                logger.error("[litert-lm-decision] Decision cycle error: %s", exc)
            await asyncio.sleep(self._interval_sec)

    async def _evaluate_cycle(self):
        if not self._engine_enabled:
            return

        open_positions = db.get_open_positions()
        if not isinstance(open_positions, dict) or not open_positions:
            return

        now = time.time()
        evaluated = 0
        for pair, pos in open_positions.items():
            if evaluated >= self._max_pairs_per_cycle:
                break
            if not isinstance(pos, dict):
                continue
            if pos.get("status") != "OPEN":
                continue
            if pair not in self._tickers:
                continue
            if (now - self._last_emitted.get(pair, 0)) < self._pair_cooldown_sec:
                continue

            ctx = self._build_pair_context(pair, pos)
            if not ctx:
                continue

            decision = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._ask_llm(ctx)
            )
            verdict = self._validate_decision(ctx, decision)
            evaluated += 1

            if verdict["action"] != "SELL":
                continue

            self._publish_sell_signal(pair, verdict["confidence"])
            self._last_emitted[pair] = now
            logger.info(
                "[litert-lm-decision] 🔻 SELL %s (%s%%) | %s",
                pair,
                verdict["confidence"],
                verdict["reason"],
            )
            db.log_system_event(pair, "LLM_EXIT_SIGNAL", verdict["reason"], "WARNING")

    def _build_pair_context(self, pair: str, pos: dict) -> dict | None:
        ticker = self._tickers.get(pair, {})
        current_price = float(ticker.get("last") or 0)
        entry_price = float(pos.get("entry_price") or 0)
        amount = float(pos.get("amount_crypto") or 0)
        amount_idr = float(pos.get("amount_idr") or 0)
        prices = list(self._history.get(pair, []))

        if current_price <= 0 or entry_price <= 0 or amount <= 0:
            return None
        if len(prices) < 20:
            return None

        gross_pnl_idr = (current_price - entry_price) * amount
        fee_buy = amount_idr * FEE_BUY_TAKER
        fee_sell = (amount * current_price) * FEE_SELL_TAKER
        net_pnl_idr = gross_pnl_idr - fee_buy - fee_sell
        gross_pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
        net_pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0.0

        tech = get_technical_summary(prices)
        tech_sig = derive_trade_signal(tech)

        depth = db.get_market_depth(pair)
        bids = depth.get("bids", []) if isinstance(depth, dict) else []
        asks = depth.get("asks", []) if isinstance(depth, dict) else []
        total_bid = sum(float(b.get("vol", 0) or 0) for b in bids)
        total_ask = sum(float(a.get("vol", 0) or 0) for a in asks)
        total_depth = total_bid + total_ask
        buy_pressure = (total_bid / total_depth * 100.0) if total_depth > 0 else 50.0
        imbalance = (total_bid / total_ask) if total_ask > 0 else 1.0
        best_bid = float(bids[0].get("price", 0) if bids else ticker.get("bid", current_price))
        best_ask = float(asks[0].get("price", 0) if asks else ticker.get("ask", current_price))
        spread_pct = ((best_ask - best_bid) / best_bid * 100.0) if best_bid > 0 else 0.0

        ch_5m = float(tech.get("ch_5m", 0) or 0)
        ch_15m = float(tech.get("ch_15m", 0) or 0)

        return {
            "pair": pair,
            "current_price": current_price,
            "entry_price": entry_price,
            "gross_pnl_pct": gross_pnl_pct,
            "net_pnl_idr": net_pnl_idr,
            "net_pnl_pct": net_pnl_pct,
            "round_trip_fee_pct": ROUND_TRIP_FEE_PCT * 100.0,
            "break_even_pct": SMART_BREAK_EVEN_PCT * 100.0,
            "tech": tech,
            "tech_sig": tech_sig,
            "buy_pressure": buy_pressure,
            "imbalance": imbalance,
            "spread_pct": spread_pct,
            "ch_5m": ch_5m,
            "ch_15m": ch_15m,
        }

    def _prompt(self, ctx: dict) -> str:
        return (
            "Anda risk-engine trading. Gunakan HANYA data numerik berikut.\n"
            "JANGAN asumsi data luar. Jika data kurang/kontradiktif, pilih HOLD.\n"
            "Balas hanya JSON valid (tanpa markdown):\n"
            '{"action":"SELL|HOLD","confidence":0-100,"reason":"...","evidence":["TECH_SELL","NEGATIVE_PNL_AFTER_FEES","MOMENTUM_DOWN","ORDERBOOK_ASK_DOMINANCE","FEE_HEADWIND"]}\n\n'
            f"PAIR={ctx['pair']}\n"
            f"PRICE={ctx['current_price']:.8f}\n"
            f"GROSS_PNL_PCT={ctx['gross_pnl_pct']:.4f}\n"
            f"NET_PNL_IDR={ctx['net_pnl_idr']:.2f}\n"
            f"NET_PNL_PCT={ctx['net_pnl_pct']:.4f}\n"
            f"ROUND_TRIP_FEE_PCT={ctx['round_trip_fee_pct']:.4f}\n"
            f"SMART_BREAK_EVEN_PCT={ctx['break_even_pct']:.4f}\n"
            f"TECH_VERDICT={ctx['tech_sig'].get('verdict', 'HOLD')}\n"
            f"TECH_BUY_SCORE={float(ctx['tech_sig'].get('buy_score', 0) or 0):.2f}\n"
            f"TECH_SELL_SCORE={float(ctx['tech_sig'].get('sell_score', 0) or 0):.2f}\n"
            f"MOMENTUM_5M={ctx['ch_5m']:.4f}\n"
            f"MOMENTUM_15M={ctx['ch_15m']:.4f}\n"
            f"ORDERBOOK_IMBALANCE={ctx['imbalance']:.4f}\n"
            f"BUY_PRESSURE_PCT={ctx['buy_pressure']:.2f}\n"
            f"SPREAD_PCT={ctx['spread_pct']:.4f}\n"
        )

    def _ask_llm(self, ctx: dict) -> dict:
        try:
            prompt = self._prompt(ctx)
            with self._engine.create_conversation() as conv:
                raw = conv.send_message(prompt)
            text = self._extract_text(raw)
            return self._parse_json(text)
        except Exception as exc:
            logger.debug("[litert-lm-decision] LLM call failed: %s", exc)
            return {"action": "HOLD", "confidence": 0, "reason": "llm_error", "evidence": []}

    @staticmethod
    def _extract_text(response) -> str:
        if isinstance(response, str):
            return response
        if hasattr(response, "text") and isinstance(response.text, str):
            return response.text
        if isinstance(response, dict):
            return json.dumps(response)
        if hasattr(response, "__iter__"):
            parts = []
            for chunk in response:
                if isinstance(chunk, str):
                    parts.append(chunk)
                elif isinstance(chunk, dict):
                    parts.append(json.dumps(chunk))
                elif hasattr(chunk, "text") and isinstance(chunk.text, str):
                    parts.append(chunk.text)
            return "".join(parts)
        return str(response)

    @staticmethod
    def _parse_json(text: str) -> dict:
        if not text:
            return {"action": "HOLD", "confidence": 0, "reason": "empty_response", "evidence": []}
        try:
            return json.loads(text)
        except Exception:
            try:
                start = text.find("{")
                end = text.rfind("}")
                if start >= 0 and end > start:
                    return json.loads(text[start : end + 1])
            except Exception:
                pass
        return {"action": "HOLD", "confidence": 0, "reason": "invalid_json", "evidence": []}

    def _validate_decision(self, ctx: dict, llm: dict) -> dict:
        action = str(llm.get("action", "HOLD") or "HOLD").upper().strip()
        reason = str(llm.get("reason", "") or "").strip()[:200]
        confidence = float(llm.get("confidence", 0) or 0)
        evidence = llm.get("evidence") if isinstance(llm.get("evidence"), list) else []
        evidence = [str(x).strip().upper() for x in evidence if str(x).strip().upper() in _ALLOWED_EVIDENCE]

        conditions = {
            "TECH_SELL": (
                str(ctx["tech_sig"].get("verdict", "")).upper() == "SELL"
                and float(ctx["tech_sig"].get("sell_score", 0) or 0) >= _MIN_TECH_SELL_SCORE
            ),
            "NEGATIVE_PNL_AFTER_FEES": ctx["net_pnl_pct"] <= 0.0,
            "MOMENTUM_DOWN": ctx["ch_5m"] < 0 and ctx["ch_15m"] <= 0,
            "ORDERBOOK_ASK_DOMINANCE": ctx["imbalance"] < 0.9 and ctx["buy_pressure"] < 45.0,
            "FEE_HEADWIND": ctx["gross_pnl_pct"] < ctx["break_even_pct"],
        }
        true_conditions = {k for k, v in conditions.items() if v}
        supported = true_conditions.intersection(set(evidence))

        if (
            action == "SELL"
            and confidence >= self._min_confidence
            and len(reason) >= _MIN_REASON_LENGTH
            and len(true_conditions) >= _MIN_TRUE_CONDITIONS
            and len(supported) >= _MIN_SUPPORTED_EVIDENCE
        ):
            return {
                "action": "SELL",
                "confidence": min(100.0, max(0.0, confidence)),
                "reason": f"{reason} | support={','.join(sorted(supported))}",
            }

        return {"action": "HOLD", "confidence": 0.0, "reason": "guard_rejected"}

    def _publish_sell_signal(self, pair: str, confidence: float):
        sig = Signal()
        sig.symbol = pair
        sig.action = SignalAction.Value("ACTION_SELL")
        sig.confidence = float(confidence)
        sig.source = SignalSource.Value("SOURCE_ORACLE")
        sig.timestamp = int(time.time() * 1000)
        self._signal_pub.send(sig.SerializeToString(), topic="signal")


def main():
    service = LiteRTLmDecisionService()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(signum, _frame):
        logger.info("[litert-lm-decision] Signal %s — shutting down.", signum)
        service.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(service.start())
    finally:
        service.stop()
        loop.stop()
        loop.close()
        logger.info("[litert-lm-decision] Exited cleanly.")


if __name__ == "__main__":
    main()
