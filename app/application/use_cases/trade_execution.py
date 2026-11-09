import os
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from app.infrastructure.database import db
from utils.logger import log_message, send_telegram_message
import config
from config import (
    INITIAL_CAPITAL,
    MIN_BALANCE_THRESHOLD,
    MIN_TRADE_IDR,
    CAPITAL_PER_TRADE_PCT,
    CAPITAL_PER_TRADE_PCT_HIGH_OPP,
    MAX_OPEN_POSITIONS,
    MIN_VOLUME_FILTER,
    LIQUIDITY_MULTIPLIER,
    CAPITAL_RESERVE_FAST_TRADE_PCT,
    POSITION_RESERVE_PCT,
    HIGH_OPPORTUNITY_TQ_PROB,
    SMALL_ACCOUNT_CLAMP_MIN_TQ_PROB,
    SMALL_ACCOUNT_CLAMP_MIN_BAL_MULT,
    SMALL_ACCOUNT_CLAMP_TIER1_BALANCE_MAX,
    SMALL_ACCOUNT_CLAMP_TIER2_BALANCE_MAX,
    SMALL_ACCOUNT_CLAMP_TIER1_PCT,
    SMALL_ACCOUNT_CLAMP_TIER2_PCT,
    SMALL_ACCOUNT_CLAMP_TIER3_PCT,
)

def _to_ccxt_symbol(pair: str) -> str:
    """Convert internal pair id (e.g. 'solusdt' / 'solidr') to CCXT symbol ('SOL/USDT' / 'SOL/IDR')."""
    s = pair.upper()
    if s.endswith("USDT"):
        return f"{s[:-4]}/USDT"
    if s.endswith("IDR"):
        return f"{s[:-3]}/IDR"
    return s


def _is_binance() -> bool:
    return os.getenv("EXCHANGE", "binance").lower() == "binance"


def _order_params(pair: str) -> dict:
    """Extra params untuk create_order.

    Indodax (ccxt) butuh `pair` berformat `btc_idr`. Binance MENOLAK parameter yang
    tidak dikenal (-1104 "Not all sent parameters were read"), jadi harus kosong.
    """
    if _is_binance():
        return {}
    _p = pair.lower()
    if _p.endswith("idr"):
        _p = _p[:-3] + "_idr"
    return {"pair": _p}


def _resolve_buy_fill(order: dict, pair: str, intended_price: float, intended_amount: float):
    """Ambil (harga rata-rata fill, qty bersih diterima, biaya quote) dari respons order.

    Market order Binance: `price` bisa None — harga eksekusi ada di `average`, dan fee
    BUY dipotong dari base asset (kecuali dibayar BNB), sehingga koin yang benar-benar
    masuk wallet = filled - fee. Tanpa koreksi ini cost basis dan qty posisi meleset.
    """
    def _f(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    filled = _f(order.get('filled')) or _f(intended_amount)
    cost = _f(order.get('cost'))
    avg = _f(order.get('average')) or _f(order.get('price'))
    if avg <= 0 and cost > 0 and filled > 0:
        avg = cost / filled
    if avg <= 0:
        avg = _f(intended_price)
    if cost <= 0:
        cost = avg * filled

    base = pair.upper()
    for _suffix in ("USDT", "IDR"):
        if base.endswith(_suffix):
            base = base[:-len(_suffix)]
            break
    fees = order.get('fees') or ([order['fee']] if order.get('fee') else [])
    fee_base = sum(
        _f(f.get('cost')) for f in fees
        if isinstance(f, dict) and str(f.get('currency') or '').upper() == base
    )
    net_amount = filled - fee_base if 0 < fee_base < filled else filled
    return avg, net_amount, cost


def format_currency(val, is_price=False):
    is_usdt = os.getenv("EXCHANGE", "indodax").lower() == 'binance'
    prefix = "$" if is_usdt else "Rp "
    val_float = float(val or 0.0)
    if is_usdt:
        if is_price:
            return f"{prefix}{val_float:,.4f}"
        return f"{prefix}{val_float:,.2f}"
    else:
        if is_price and val_float < 1000:
            return f"{prefix}{val_float:,.2f}"
        return f"{prefix}{val_float:,.0f}"

#  Per-pair minimum trade IDR cache (dari fetch_pairs() API) 
# Di-refresh setiap jam, sehingga jarang hit API dan tidak memblokir eksekusi.
_PAIR_MIN_IDR_CACHE: dict = {}   # {pair: min_idr_float}
_PAIR_MIN_LAST_FETCH: float = 0.0
_PAIR_MIN_REFRESH_SEC = 3600     # 1 jam

def _get_pair_min_idr(bot, pair: str) -> float:
    """Return minimum trade IDR untuk pair tertentu.

    Menggunakan data live dari API (di-cache 1 jam). Fallback ke konstanta
    global MIN_TRADE_IDR jika adapter tidak tersedia atau API gagal.
    """
    global _PAIR_MIN_IDR_CACHE, _PAIR_MIN_LAST_FETCH
    now = time.time()
    if now - _PAIR_MIN_LAST_FETCH > _PAIR_MIN_REFRESH_SEC:
        adapter = getattr(bot, '_adapter', None)
        if adapter is not None:
            try:
                pairs = adapter.fetch_pairs()
                _PAIR_MIN_IDR_CACHE = {
                    str(p.get('id', '')).lower(): float(p.get('trade_min_base_currency', 0) or 0)
                    for p in pairs
                }
                _PAIR_MIN_LAST_FETCH = now
                log_message(f"[trade_execution] Per-pair min IDR cache diperbarui: {len(_PAIR_MIN_IDR_CACHE)} pair")
            except Exception as e:
                log_message(f"[trade_execution] fetch_pairs gagal, fallback ke MIN_TRADE_IDR global: {e}")
    # Gunakan nilai exact dari exchange jika tersedia; fallback ke global hanya jika tidak ada data.
    pair_min = _PAIR_MIN_IDR_CACHE.get(pair.lower(), 0)
    return float(pair_min) if pair_min > 0 else float(MIN_TRADE_IDR)


def check_live_liquidity(exchange_client, pair, order_size_idr, max_spread_pct=0.005):
    """Validate live order book liquidity from exchange before entry.
    
    Fetches real-time order book and checks:
    1. Bid depth (ada cukup seller interested?)
    2. Spread (bid-ask tidak terlalu lebar?)
    3. Recent bid activity
    
    Returns:
        (is_liquid: bool, reason: str, bid_depth_idr: float)
        - is_liquid: True if entry is safe, False if skip
        - reason: explanation for gate/skip decision
        - bid_depth_idr: total IDR available at bid side for our order
    """
    try:
        symbol = _to_ccxt_symbol(pair)
        orderbook = exchange_client.fetch_order_book(symbol, limit=20)
        
        if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:
            return False, "Order book unavailable", 0.0
        
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])
        
        if not bids or not asks or len(bids) < 2 or len(asks) < 2:
            return False, "Order book depth insufficient", 0.0
        
        # Get best bid/ask
        best_bid_price = float(bids[0][0]) if bids else 0
        best_ask_price = float(asks[0][0]) if asks else 0
        best_bid_vol = float(bids[0][1]) if bids else 0
        
        if best_bid_price <= 0 or best_ask_price <= 0:
            return False, "Invalid bid/ask prices", 0.0
        
        # 1. Check spread
        spread_pct = (best_ask_price - best_bid_price) / best_bid_price if best_bid_price > 0 else 0
        if spread_pct > max_spread_pct:
            return False, f"Spread terlalu lebar {spread_pct*100:.3f}% (max {max_spread_pct*100:.2f}%)", 0.0
        
        # 2. Calculate total bid depth (IDR available at bid side)
        bid_depth_idr = 0.0
        for bid_price, bid_vol in bids[:10]:  # Top 10 bids
            bid_depth_idr += float(bid_price) * float(bid_vol)
        
        # We need at least 3x our order size in bid depth untuk cukup liquid
        required_depth = order_size_idr * 3.0
        if bid_depth_idr < required_depth:
            return False, f"Bid depth {format_currency(bid_depth_idr)} < required {format_currency(required_depth)}", bid_depth_idr
        
        # 3. Check if top 2 bids have meaningful volume
        if best_bid_vol < order_size_idr / best_bid_price * 0.5:
            # Top bid doesn't have 50% of our order volume — check next level
            if len(bids) > 1:
                second_bid_vol = float(bids[1][1]) if bids[1] else 0
                if (best_bid_vol + second_bid_vol) < order_size_idr / best_bid_price * 0.5:
                    return False, f"Top bids volume too low for order size", bid_depth_idr
        
        return True, f"Liquid (spread {spread_pct*100:.3f}%, bid depth {format_currency(bid_depth_idr)})", bid_depth_idr
        
    except Exception as e:
        log_message(f"[WARN] Liquidity check error for {pair}: {e}")
        return False, f"Liquidity check failed: {str(e)[:60]}", 0.0


def execute_trade(
    bot,
    pair,
    price,
    trend="N/A",
    tq_prob=0.0,
    whale_boost=1.0,
    sector_boost=1.0,
    imbalance=1.0,
    volatility=0.0,
    dynamic_tp=None,
    dynamic_sl=None,
    entry_size_factor=1.0,
    taker=False,
):
    try:
        def _mark_gate(reason: str):
            try:
                db.save_state("entry_gate_state", "GATED")
                db.save_state("entry_gate_reason", str(reason)[:240])
                db.save_state("entry_gate_pair", pair)
                db.save_state("entry_gate_at", datetime.now().isoformat())
            except Exception:
                pass

        def _mark_entry_ok():
            try:
                state_label = "DRY_ENTRY_EXECUTED" if config.DRY_RUN else "LIVE_ENTRY_EXECUTED"
                db.save_state("entry_gate_state", state_label)
                db.save_state("entry_gate_reason", "Entry executed successfully")
                db.save_state("entry_gate_pair", pair)
                db.save_state("entry_gate_at", datetime.now().isoformat())
            except Exception:
                pass

        # --- [Risk Check: Max Positions + Reserved Slots] ---
        active_count = db.get_active_positions_count(dry_run=1 if config.DRY_RUN else 0)

        # High-opportunity path: fast entries with strong technical probability can use reserved capacity.
        is_high_opportunity_fast = bool(taker or (tq_prob >= HIGH_OPPORTUNITY_TQ_PROB))

        # [Senior Audit] Adaptive Position Limits based on Equity
        effective_max_pos = MAX_OPEN_POSITIONS
        is_small_account = False

        if not config.DRY_RUN:
            balance = getattr(bot, 'cached_balance', 0)
            if balance < 500000: # < 500rb IDR
                is_small_account = True
                effective_max_pos = min(5, MAX_OPEN_POSITIONS)

        reserve_slots = max(1, int(round(effective_max_pos * max(0.0, min(0.5, POSITION_RESERVE_PCT)))))
        standard_slot_limit = max(1, effective_max_pos - reserve_slots)

        if is_high_opportunity_fast:
            if active_count >= effective_max_pos:
                log_message(f"[WARN] SKIP {pair}: Max active positions ({effective_max_pos}) reached.")
                _mark_gate(f"Max active positions reached ({effective_max_pos})")
                return False
        else:
            if active_count >= standard_slot_limit:
                log_message(
                    f"[WARN] SKIP {pair}: Standard slot limit reached ({standard_slot_limit}/{effective_max_pos}). "
                    f"{reserve_slots} slot reserved for fast high-opportunity setups."
                )
                _mark_gate(f"Standard slot limit reached ({standard_slot_limit}); reserve slots active")
                return False

        # --- [Risk Check: Dynamic Liquidity Guard] ---
        tickers = bot.market_ws.get_tickers()
        coin_data = tickers.get(pair, {})
        vol_24h = float(coin_data.get('vol_idr', 0))
        
        # 1. Global Minimum check
        if vol_24h < MIN_VOLUME_FILTER:
            log_message(f"[WARN] SKIP {pair}: Volume 24h ({format_currency(vol_24h)}) below Min Filter ({format_currency(MIN_VOLUME_FILTER)})")
            _mark_gate(f"24h volume below minimum filter: {vol_24h:,.0f}")
            return False

        # Position sizing: velocity_score and confidence both derived from pure technical probability.
        multiplier = bot.oracle.get_sizing_multiplier(
            pair=pair, 
            velocity_score=tq_prob,
            confidence=tq_prob,   # pure technical, no AI bias
        )
        
        if multiplier <= 0:
            log_message(f"SKIP: Oracle blocked trade ({bot.oracle.get_rationale(pair, 'SKIP_REGIME')})")
            _mark_gate("Oracle blocked trade for current regime")
            return False

        reserve_pct = max(0.0, min(0.5, CAPITAL_RESERVE_FAST_TRADE_PCT))

        if config.DRY_RUN:
            # Derive simulated balance from INITIAL_CAPITAL adjusted by today's realised
            # PnL and currently open DRY positions so sizing decays correctly with losses
            # (parity requirement: must mirror the LIVE balance-based sizing path).
            try:
                dry_stats = db.get_daily_stats()
                realized_pnl_dry = float(dry_stats.get("total_pnl_quote") or 0.0)
                open_pos = db.get_open_positions()
                pos_iter = open_pos.values() if isinstance(open_pos, dict) else open_pos
                used_idr = sum(
                    float(p.get("amount_quote", 0))
                    for p in pos_iter
                    if int(p.get("dry_run", 0)) == 1
                )
                dry_balance = INITIAL_CAPITAL + realized_pnl_dry - used_idr
            except Exception:
                dry_balance = INITIAL_CAPITAL
            if dry_balance < MIN_BALANCE_THRESHOLD:
                log_message(f"STOP DRY: Simulated balance {dry_balance:.0f} IDR below {MIN_BALANCE_THRESHOLD}")
                _mark_gate(f"Dry balance below threshold ({dry_balance:.0f})")
                return False

            reserve_idr = dry_balance * reserve_pct
            if is_high_opportunity_fast:
                usable_capital = dry_balance - MIN_BALANCE_THRESHOLD
            else:
                usable_capital = dry_balance - reserve_idr - MIN_BALANCE_THRESHOLD

            if usable_capital <= 0:
                _mark_gate("Dry usable capital exhausted after reserve buffer")
                return False

            active_pct = CAPITAL_PER_TRADE_PCT_HIGH_OPP if is_high_opportunity_fast else CAPITAL_PER_TRADE_PCT
            target_amount = dry_balance * active_pct * multiplier
            amount_quote = min(target_amount, usable_capital)
        else:
            balance = getattr(bot, 'cached_balance', 0)
            if balance < MIN_BALANCE_THRESHOLD:
                log_message(f"STOP: Balance {balance:.0f} IDR below {MIN_BALANCE_THRESHOLD}")
                _mark_gate(f"Live balance below threshold ({balance:.0f})")
                return False
            # Use real balance as the basis for sizing in LIVE mode
            active_pct = CAPITAL_PER_TRADE_PCT_HIGH_OPP if is_high_opportunity_fast else CAPITAL_PER_TRADE_PCT
            target_amount = balance * active_pct * multiplier

            # [Small Account Optimization]
            # If the calculated size is too small for the exchange but the account can afford it,
            # we 'clamp' to the minimum trade amount to ensure the bot can actually execute.
            if is_small_account and target_amount < MIN_TRADE_IDR:
                # Only elevate if technical probability is solid (no AI bias)
                if tq_prob >= SMALL_ACCOUNT_CLAMP_MIN_TQ_PROB and balance >= MIN_TRADE_IDR * SMALL_ACCOUNT_CLAMP_MIN_BAL_MULT:
                    # Tiered clamp avoids a single hard floor for 25k-60k balances.
                    if balance < SMALL_ACCOUNT_CLAMP_TIER1_BALANCE_MAX:
                        clamp_target = MIN_TRADE_IDR * max(1.0, SMALL_ACCOUNT_CLAMP_TIER1_PCT)
                    elif balance < SMALL_ACCOUNT_CLAMP_TIER2_BALANCE_MAX:
                        clamp_target = MIN_TRADE_IDR * max(1.0, SMALL_ACCOUNT_CLAMP_TIER2_PCT)
                    else:
                        clamp_target = MIN_TRADE_IDR * max(1.0, SMALL_ACCOUNT_CLAMP_TIER3_PCT)

                    log_message(
                        f"[EXECUTIVE] [Intelligent Sizing] Tiered clamp {pair}: "
                        f"{format_currency(target_amount)} -> {format_currency(clamp_target)}"
                    )
                    target_amount = clamp_target

            reserve_idr = balance * reserve_pct
            if is_high_opportunity_fast:
                usable_capital = balance - MIN_BALANCE_THRESHOLD
            else:
                usable_capital = balance - reserve_idr - MIN_BALANCE_THRESHOLD

            if usable_capital <= 0:
                _mark_gate("Live usable capital exhausted after reserve buffer")
                return False

            amount_quote = min(target_amount, usable_capital)

        # [Recovery Mode] Kurangi ukuran posisi 50% saat bot dalam mode pemulihan setelah loss limit
        recovery_factor = getattr(bot, '_recovery_size_factor', 1.0)
        if recovery_factor < 1.0:
            amount_quote *= recovery_factor
            log_message(
                f"[SELL] [RECOVERY SIZING] {pair}: Ukuran posisi dikurangi menjadi "
                f"{format_currency(amount_quote)} ({recovery_factor*100:.0f}% dari normal)"
            )

        # Regime-aware sizing factor dari scoring gate.
        entry_size_factor = max(0.30, min(1.50, float(entry_size_factor or 1.0)))
        if abs(entry_size_factor - 1.0) > 1e-6:
            amount_quote *= entry_size_factor
            log_message(
                f"[EXECUTIVE] [REGIME SIZING] {pair}: size_factor={entry_size_factor:.2f} -> {format_currency(amount_quote)}"
            )

        # 2. Dynamic check (Participation Rate)
        # Kita tidak ingin trade kita > 1/Multiplier (e.g. 1/50 = 2%) dari volume harian
        required_vol = amount_quote * LIQUIDITY_MULTIPLIER
        if vol_24h < required_vol:
            log_message(f"[WARN] SKIP {pair}: Insufficient Liquidity. Need {format_currency(required_vol)} vol, but only has {format_currency(vol_24h)}")
            _mark_gate(f"Insufficient liquidity: need {required_vol:,.0f}, got {vol_24h:,.0f}")
            return False

        _pair_min_idr = _get_pair_min_idr(bot, pair)
        if amount_quote < _pair_min_idr:
            log_message(f"SKIP: Capital {amount_quote:.0f} IDR below Min Trade {_pair_min_idr:.0f} (pair-specific)")
            _mark_gate(f"Capital below minimum trade ({amount_quote:.0f} < {_pair_min_idr:.0f})")
            return False

        from config import TP_PCT, SL_PCT, ROUND_TRIP_FEE_PCT

        final_tp = dynamic_tp if dynamic_tp is not None else TP_PCT
        final_sl = dynamic_sl if dynamic_sl is not None else SL_PCT

        # [Fee-Viability Guard] Gunakan ROUND_TRIP_FEE_PCT (0.6322%) sebagai baseline.
        # Guard memastikan TP > round-trip fee sehingga ada net profit nyata.
        # _min_viable_net = 0.5% dari trade size (pure persentase, tanpa hard floor),
        # sehingga guard proporsional di semua ukuran akun dan tidak memblokir trade
        # kecil yang secara matematis sudah menghasilkan profit bersih setelah fee.
        _fee_rt = ROUND_TRIP_FEE_PCT
        _max_net_profit = amount_quote * (final_tp - _fee_rt)
        _min_viable_net = amount_quote * 0.005  # 0.5% dari trade size, proporsional
        if _max_net_profit < _min_viable_net:
            log_message(
                f"[WARN] SKIP {pair}: Fee-trap! Max net profit {format_currency(_max_net_profit)} "
                f"< {format_currency(_min_viable_net)} (trade={format_currency(amount_quote)}, fee={_fee_rt*100:.4f}%, TP={final_tp*100:.1f}%). "
                f"Perlu trade lebih besar atau TP lebih tinggi."
            )
            _mark_gate("Fee viability guard blocked trade")
            return False

        history = list(bot.price_history[pair])
        prices = np.array(history, dtype=float)

        # Entry SELALU market/taker di harga pasar saat ini. Jalur "Maker Entry EMA-9" lama
        # tidak pernah memasang limit order (LIVE tetap mengirim market order), tetapi di
        # DRY RUN ia mencatat fill instan di EMA-9 — di bawah harga pasar saat breakout —
        # sehingga PnL simulasi lebih bagus daripada yang bisa dicapai LIVE.
        buy_price = price
        log_message(f"[EXECUTION] [Taker Execute] Market Buy at {format_currency(buy_price, True)}")

        symbol = _to_ccxt_symbol(pair)
        amount_crypto = amount_quote / buy_price

        tp_target = buy_price * (1 + final_tp)
        sl_target = buy_price * (1 + final_sl)

        if config.DRY_RUN:
            # Simulate fees/slippage purely by reducing the amount of coins received, 
            # keeping the entry price (buy_price) perfectly aligned with the market ticker.
            _sim_slippage = 0.0021  # 0.21% taker fee + slippage penalty
            amount_crypto = (amount_quote / buy_price) * (1 - _sim_slippage)
            order = {
                'id': f"DRY-{int(time.time())}",
                'status': 'closed',
                'price': buy_price,   # [SIMULATION] 100% pure market price
                'amount': amount_crypto,
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
            }
            predator_msg = ""
            if whale_boost > 1.0:
                predator_msg += "\nWhale Backed: Massive wall detected."
            if sector_boost > 1.0:
                predator_msg += "\nSector Driven: Hot sector momentum."

            msg = (
                f"[RECOVERY] *DRY RUN BUY* [RECOVERY]\n"
                f"Pair: `{pair}`\n"
                f"Reason: `{trend}` (Prob: {tq_prob*100:.1f}%)\n"
                f"Rationale: _{bot.oracle.get_rationale(pair, 'BUY')}_{predator_msg}\n"
                f"Price: {format_currency(buy_price, True)}\n"
                f"Qty: {amount_crypto:.8f}\n"
                f"Target TP: {format_currency(tp_target, True)}\n"
                f"Target SL: {format_currency(sl_target, True)}"
            )
            log_message(msg.replace('*', '').replace('\n', ' | '))
            send_telegram_message(msg)
        else:
            msg = (
                f"[RECOVERY] *LIVE BUY* [RECOVERY]\n"
                f"Pair: `{pair}`\n"
                f"Reason: `{trend}` (Prob: {tq_prob*100:.1f}%)\n"
                f"Price: {format_currency(buy_price, True)}\n"
                f"Qty: {amount_crypto:.8f}\n"
                f"Target TP: {format_currency(tp_target, True)}\n"
                f"Target SL: {format_currency(sl_target, True)}"
            )
            log_message(msg.replace('*', '').replace('\n', ' | '))
            send_telegram_message(msg)

            # --- [LIVE LIQUIDITY CHECK] Validate order book depth before entry ---
            from config import MAX_ALLOWED_SPREAD_PCT
            is_liquid, liq_reason, bid_depth = check_live_liquidity(
                bot.exchange, pair, amount_quote, max_spread_pct=MAX_ALLOWED_SPREAD_PCT
            )
            
            if not is_liquid:
                log_message(f"[BLOCK] [SKIP ENTRY] {pair}: Liquidity check failed — {liq_reason}")
                _mark_gate(f"Live liquidity check failed: {liq_reason}")
                return False
            
            log_message(f"[OK] [LIQUIDITY OK] {pair}: {liq_reason}")

            params = _order_params(pair)
            # ccxt memakai price * amount sebagai nilai quote market-buy, jadi price harus
            # harga asli. Floor 1.0 hanya valid untuk IDR; pada USDT ia menggandakan
            # nilai order untuk koin berharga < $1 (mis. $0.05 -> 20x ukuran).
            market_buy_price = float(buy_price)
            if not _is_binance():
                market_buy_price = max(market_buy_price, 1.0)
            order = bot.exchange.create_order(symbol, 'market', 'buy', amount_crypto, market_buy_price, params)
            # Cost basis dari fill asli: harga rata-rata, qty bersih (setelah fee base), biaya quote.
            buy_price, amount_crypto, actual_idr = _resolve_buy_fill(order, pair, price, amount_crypto)
            tp_target = buy_price * (1 + final_tp)
            sl_target = buy_price * (1 + final_sl)
            log_message(
                f" [LIVE-AUDIT] {pair} order executed. Intended: {format_currency(amount_quote)} | "
                f"Actual Cost: {format_currency(actual_idr)} | Qty: {amount_crypto:.8f}"
            )
            # Use actual cost if available from exchange
            if actual_idr > 0:
                amount_quote = actual_idr

        _rsi_dna = None
        _ema_dist_dna = None
        try:
            if len(prices) >= 14:
                _deltas = np.diff(prices[-15:])
                _gains = np.where(_deltas > 0, _deltas, 0)
                _losses = np.where(_deltas < 0, -_deltas, 0)
                _avg_gain = np.mean(_gains) if np.mean(_gains) > 0 else 1e-9
                _avg_loss = np.mean(_losses) if np.mean(_losses) > 0 else 1e-9
                _rsi_dna = round(100 - (100 / (1 + _avg_gain / _avg_loss)), 2)
            if len(prices) >= 20:
                _ema20 = prices[-20:].mean()
                _ema_dist_dna = round((buy_price - _ema20) / _ema20, 6) if _ema20 > 0 else 0.0
        except Exception:
            pass

        import threading
        def background_logging(p, d, o, m_label, t_prob, b_p, f_tp, f_sl):
            try:
                db.save_trade_dna(p, d)
                db.log_order(p, "BUY", b_p, d.get("amount_crypto", 0), o.get('status'), o)
                db.log_system_event(
                    p, "BUY",
                    f"{m_label} | Prob:{t_prob*100:.1f}% | {format_currency(b_p, True)} | TP:{f_tp*100:.1f}% SL:{f_sl*100:.1f}%",
                    'INFO'
                )
            except Exception as ex:
                log_message(f"[WARN] Async Logging Error: {ex}")

        dna_data = {
            "amount_crypto": amount_crypto,
            "ai_prob": tq_prob,           # technical probability stored as ai_prob
            "tq_prob": tq_prob * 100,
            "whale_boost": whale_boost,
            "sector_sentiment": sector_boost,
            "imbalance": imbalance,
            "volatility": volatility,
            "rsi": _rsi_dna,
            "ema_dist": _ema_dist_dna,
            "dry_run": config.DRY_RUN,
        }
        
        mode_label = "DRY RUN BUY" if config.DRY_RUN else "LIVE BUY"
        threading.Thread(
            target=background_logging,
            args=(pair, dna_data, order, mode_label, tq_prob, buy_price, final_tp, final_sl),
            daemon=True
        ).start()

        data = {
            "entry_price": buy_price,
            "amount": amount_crypto,
            "amount_crypto": amount_crypto,
            "amount_quote": amount_quote,
            "timestamp": datetime.now().isoformat(),
            "dry_run": config.DRY_RUN,
            # Market order yang sudah `closed` langsung OPEN. Menunggu event PrivateWS
            # rawan race (event fill bisa tiba sebelum baris posisi ditulis) dan posisi
            # PENDING_BUY tidak dijaga SL/TP oleh exit monitor.
            "status": "OPEN" if (
                config.DRY_RUN or str(order.get('status') or '').lower() in ('closed', 'filled')
            ) else "PENDING_BUY",
            "entry_order_id": order.get('id') if not config.DRY_RUN else None,
            "exit_order_id": None,
            "target_tp_pct": final_tp,
            "target_sl_pct": final_sl,
            "reason_backup": trend,
            "ai_confidence": tq_prob * 100,
            "is_high_opportunity_fast": 1 if is_high_opportunity_fast else 0,
            "highest_price": buy_price,              # [FIX] Initialize highest_price for TTP tracking
            "trailing_sl_price": buy_price * (1 - 0.007),  # [FIX] Initialize trailing stop (0.7% from entry)
        }

        db.upsert_position(pair, data)
        _mark_entry_ok()
        return True
    except Exception as e:
        log_message(f"Trade Error: {e}")
        try:
            db.save_state("entry_gate_state", "ERROR")
            db.save_state("entry_gate_reason", f"Trade execution error: {e}"[:240])
            db.save_state("entry_gate_pair", pair)
            db.save_state("entry_gate_at", datetime.now().isoformat())
        except Exception:
            pass
        return False


def execute_basket_exit_live(exchange_client, positions: list) -> dict:
    """Issue simultaneous market sell orders for all given live positions.

    Each position dict must contain at least: pair, amount_crypto, entry_price, amount_quote.
    Orders are placed in parallel via a ThreadPoolExecutor.  After a successful fill
    the position is deleted from the DB and the order is recorded in order_book.

    Returns a summary dict: {filled: [...], failed: [...]}.
    """
    if not positions:
        return {"filled": [], "failed": []}

    filled = []
    failed = []

    def _sell_one(pos):
        pair = pos.get("pair", "")
        amount_crypto = float(pos.get("amount_crypto") or 0)
        entry_price   = float(pos.get("entry_price") or 0)
        amount_quote    = float(pos.get("amount_quote") or 0)

        if amount_crypto <= 0 or not pair:
            return {"pair": pair, "ok": False, "error": "invalid position data"}

        symbol = _to_ccxt_symbol(pair)
        # Build Indodax param: ensure suffix is exactly '_idr', not mid-string
        _pair_param = pair.lower()
        if _pair_param.endswith("idr"):
            _pair_param = _pair_param[:-3] + "_idr"
        try:
            order = exchange_client.create_order(
                symbol, "market", "sell", amount_crypto, None,
                {"pair": _pair_param}
            )
            import random as _random
            order_id = order.get("id") or (
                f"LIVE-SELL-{pair}-{int(time.time())}-{_random.randint(1000, 9999)}"
            )
            fill_price = float(order.get("price") or entry_price)

            # Record in order_book
            db.upsert_order_book(
                order_id=order_id,
                pair=pair,
                side="SELL",
                price=fill_price,
                amount=amount_crypto,
                status=order.get("status", "filled"),
                raw_response=order,
            )
            # Delete position from DB atomically
            db.delete_position(pair)

            pnl = (fill_price - entry_price) * amount_crypto
            log_message(
                f"[FAST] [LIVE BASKET EXIT] {pair}: SOLD {amount_crypto:.6f} @ Rp {fill_price:,.0f} "
                f"| PnL ≈ Rp {pnl:,.0f}"
            )
            return {"pair": pair, "ok": True, "order_id": order_id, "price": fill_price}
        except Exception as exc:
            log_message(f" [LIVE BASKET EXIT] {pair} sell failed: {exc}")
            return {"pair": pair, "ok": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=min(8, len(positions))) as pool:
        futures = {pool.submit(_sell_one, p): p for p in positions}
        for future in as_completed(futures):
            result = future.result()
            if result.get("ok"):
                filled.append(result)
            else:
                failed.append(result)

    log_message(
        f"[BASKET] [LIVE BASKET EXIT] Complete — filled: {len(filled)}, failed: {len(failed)}"
    )
    return {"filled": filled, "failed": failed}
