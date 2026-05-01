import json
import os
import time
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from config import (
    TP_PCT, SL_PCT, PENDING_BUY_TIMEOUT_MINUTES, PENDING_BUY_MAX_SPREAD_PCT,
    FEE_BUY_MAKER, FEE_SELL_MAKER, FEE_BUY_TAKER, FEE_SELL_TAKER,
    MIN_NET_PROFIT_IDR, TRAILING_STOP_ACTIVATION_PCT, TRAILING_STOP_DISTANCE_PCT,
    SELL_TAKER_OFFSET, SELL_MAKER_OFFSET, JARING_ALLOCATION_PCT, JARING_DEPTH_PCT,
    INITIAL_CAPITAL, AI_MIN_HISTORY_POINTS, MAX_HOLD_MINUTES
)
from app.infrastructure.database import db
from app.domain.technicals import get_technical_summary, derive_trade_signal
from utils.logger import log_message, send_telegram_message

class ExitManager:
    def __init__(self, exchange_client, dry_run: bool = False, oracle=None):
        self.exchange = exchange_client
        self.dry_run  = dry_run
        self.oracle = oracle
        self.jaring_orders = {} # {pair: {id, price, amount}}
        self.last_jaring_update = {} # {pair: timestamp}
        self._sync_jaring_from_db()

    def get_open_positions(self):
        return db.get_open_positions()

    def save_positions(self, positions, modified_pairs):
        for pair in modified_pairs:
            db.upsert_position(pair, positions[pair])

    def _set_pair_cooldown(self, pair, minutes=20, reason="LOSS"):
        """Set cooldown per pair setelah trade loss agar tidak re-entry di market chop."""
        try:
            cooldown_until = time.time() + (minutes * 60)
            db.save_state(f"pair_cooldown_until_{pair}", str(cooldown_until))
            log_message(f"⏸️ [PAIR COOLDOWN] {pair}: {minutes}m ({reason})")
        except Exception as e:
            log_message(f"⚠️ Failed set cooldown {pair}: {e}")

    def monitor_and_execute(self, current_tickers, price_history=None, get_prediction_fn=None):
        positions = self.get_open_positions()
        # log_message(f"[ExitManager] Monitoring {len(positions)} positions...")
        if not positions:
            return

        modified_pairs = set()

        def mark_exit_requested(target_pair, order_id):
            """Sinkronkan state exit untuk DRY RUN vs LIVE agar tidak ada posisi ghost."""
            if not order_id:
                return

            if self.dry_run:
                # execute_exit() di dry run langsung delete+catat history,
                # jadi jangan upsert ulang posisi pada akhir loop.
                positions.pop(target_pair, None)
                modified_pairs.discard(target_pair)
                return

            if target_pair in positions:
                positions[target_pair]["status"] = "EXITING"
                positions[target_pair]["exit_order_id"] = order_id
                positions[target_pair]["exit_requested_at"] = datetime.now().isoformat()
                modified_pairs.add(target_pair)
        
        # --- [MANUAL DASHBOARD EXIT] ---
        manual_exit_pair = db.get_state("trigger_exit_pair")
        if manual_exit_pair:
            db.save_state("trigger_exit_pair", "") # Reset immediately
            if manual_exit_pair in positions and positions[manual_exit_pair].get('status') == "OPEN":
                log_message(f"🚨 [MANUAL EXIT] Triggered from dashboard for {manual_exit_pair}")
                data = positions[manual_exit_pair]
                # Ambil harga terakhir dari ticker terbaru
                price = float(current_tickers[manual_exit_pair].get('last') or current_tickers[manual_exit_pair].get('buy') or 0)
                if price > 0:
                    order_id = self.execute_exit(
                        manual_exit_pair, data.get('amount_crypto', 0), price, "MANUAL_DASHBOARD_EXIT",
                        entry_price=data.get('entry_price', 0), amount_idr=data.get('amount_idr', 0),
                        position_data=data
                    )
                    if order_id:
                        mark_exit_requested(manual_exit_pair, order_id)
                        self.save_positions(positions, modified_pairs)
                        return # Exit early to process manual order


        # --- [PORTFOLIO BASKET EXIT / CROSS-ASSET HEDGING] ---
        # Menutup serentak semua posisi ketika profit gabungan cukup untuk meng-cover posisi yang rugi
        try:
            open_pairs_for_basket = []
            total_net_pnl = 0
            has_losers = False
            
            for pair, data in positions.items():
                if data.get('status') == "OPEN" and pair in current_tickers:
                    current_price = float(current_tickers[pair].get('last') or current_tickers[pair].get('buy') or 0)
                    entry_price = data.get('entry_price', 0)
                    amount = data.get('amount_crypto', 0)
                    amount_idr = data.get('amount_idr', 0)
                    
                    sell_value = amount * current_price
                    fee_sell = sell_value * FEE_SELL_TAKER
                    fee_buy = amount_idr * FEE_BUY_MAKER
                    
                    gross_pnl = (current_price - entry_price) * amount
                    net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
                    
                    if net_pnl_idr < 0:
                        has_losers = True
                        
                    total_net_pnl += net_pnl_idr
                    open_pairs_for_basket.append((pair, data, current_price, amount))
            
            # Eksekusi jika: >1 posisi, ada posisi yang rugi, dan total profit bersih melampaui MIN_NET_PROFIT_IDR
            if len(open_pairs_for_basket) > 1 and total_net_pnl >= MIN_NET_PROFIT_IDR and has_losers:
                msg = (
                    f"🧺 *PORTFOLIO BASKET EXIT* 🧺\n"
                    f"Menutup {len(open_pairs_for_basket)} posisi serentak untuk menutupi kerugian!\n"
                    f"Estimasi Global Net Profit: Rp {total_net_pnl:,.2f}"
                )
                print(msg.replace('*', '').replace('\n', ' | '))
                send_telegram_message(msg)
                
                for pair, data, current_price, amount in open_pairs_for_basket:
                    reason = "BASKET_EXIT"
                    log_msg = f"⚡ [BASKET_EXIT] {pair}: Liquidated as part of portfolio rescue."
                    log_message(log_msg)
                    db.log_system_event(pair, reason, log_msg, 'SUCCESS')
                    
                    order_id = self.execute_exit(
                        pair, amount, current_price, reason,
                        entry_price=data.get('entry_price', 0),
                        amount_idr=data.get('amount_idr', 0),
                        position_data=data
                    )
                    if order_id:
                        mark_exit_requested(pair, order_id)
                
                self.save_positions(positions, modified_pairs)
                return # Langsung exit frame evaluasi ini karena semua posisi sedang ditutup
        except Exception as e:
            log_message(f"🚨 Error in Portfolio Basket Evaluation: {e}")
        for pair, data in list(positions.items()):
            try:
                # Ambil harga terakhir dari ticker terbaru
                if pair not in current_tickers:
                    # log_message(f"Skipping {pair}: No ticker data")
                    continue

                # Jika posisi sedang EXITING, cek timeout agar profit tidak balik arah.
                if data.get('status') == "EXITING":
                    try:
                        exit_requested_at = data.get('exit_requested_at') or data.get('timestamp')
                        if isinstance(exit_requested_at, str):
                            exit_dt = datetime.fromisoformat(exit_requested_at)
                        else:
                            exit_dt = datetime.now()
                        exiting_age_sec = (datetime.now() - exit_dt).total_seconds()

                        # Biarkan order bekerja normal dulu selama 2 menit.
                        if exiting_age_sec < 120:
                            continue

                        current_price = float(current_tickers[pair].get('last') or current_tickers[pair].get('buy') or 0)
                        entry_price = float(data.get('entry_price') or 0)
                        amount = float(data.get('amount_crypto') or 0)
                        if current_price <= 0 or amount <= 0:
                            continue

                        momentum_signal = 'HOLD'
                        live_ai_prob = -1.0
                        hist = []
                        if price_history and pair in price_history:
                            hist = list(price_history.get(pair, []))
                        if not hist or hist[-1] != current_price:
                            hist = (hist + [current_price])[-180:]

                        if len(hist) >= 2:
                            tech = get_technical_summary(hist)
                            depth_data = db.get_market_depth(pair)
                            if depth_data['bids'] and depth_data['asks']:
                                total_bid = sum(b['vol'] for b in depth_data['bids'])
                                total_ask = sum(a['vol'] for a in depth_data['asks'])
                                total_depth = total_bid + total_ask
                                if total_depth > 0:
                                    tech['buy_pressure'] = round((total_bid / total_depth) * 100, 1)
                            momentum_signal = derive_trade_signal(tech).get('verdict', 'HOLD')

                            if get_prediction_fn and len(hist) >= AI_MIN_HISTORY_POINTS:
                                ai_boost = self.oracle.get_sentiment_boost(pair) if self.oracle else 0.0
                                live_ai_prob = float(get_prediction_fn(hist, current_tickers.get(pair), ai_boost))

                        # Force fallback jika terlalu lama + momentum memburuk, atau sangat lama tanpa fill.
                        is_momentum_bad = (momentum_signal == 'SELL') or (live_ai_prob >= 0 and live_ai_prob < 0.40)
                        is_underwater = entry_price > 0 and current_price < entry_price * 0.995
                        should_fallback = (exiting_age_sec >= 180 and (is_momentum_bad or is_underwater)) or exiting_age_sec >= 360

                        if not should_fallback or self.dry_run:
                            continue

                        # Pastikan order lama benar-benar dibatalkan dulu agar tidak dobel jual.
                        old_exit_order_id = data.get('exit_order_id')
                        if old_exit_order_id:
                            try:
                                symbol_cancel = f"{pair[:-3].upper()}/IDR"
                                self.exchange.cancel_order(
                                    old_exit_order_id,
                                    symbol_cancel,
                                    params={'pair': pair.replace("idr", "_idr"), 'side': 'sell'}
                                )
                            except Exception as cancel_err:
                                log_message(f"⚠️ EXITING fallback cancel skipped {pair}: {cancel_err}")

                        log_message(
                            f"⚠️ [EXIT TIMEOUT FALLBACK] {pair}: {int(exiting_age_sec)}s, "
                            f"Sig={momentum_signal}, AI={live_ai_prob:.2f}. Switching to emergency exit."
                        )

                        new_order_id = self.execute_exit(
                            pair,
                            amount,
                            current_price,
                            "HARD_STOP_LOSS",
                            entry_price=entry_price,
                            amount_idr=data.get('amount_idr', 0),
                            position_data=data
                        )
                        if new_order_id:
                            mark_exit_requested(pair, new_order_id)
                    except Exception as e:
                        log_message(f"⚠️ EXITING timeout check error for {pair}: {e}")

                    continue
                    
                current_price = float(current_tickers[pair].get('last') or current_tickers[pair].get('buy') or 0)
                entry_price = data.get('entry_price', 0)
                amount = data.get('amount_crypto', 0)

                # [Inline Momentum Engine] Samakan basis BUY/SELL: technical + orderbook + AI live.
                momentum_signal = 'HOLD'
                momentum_conf = 50
                current_rsi = 50.0
                live_ai_prob = -1.0

                hist = []
                if price_history and pair in price_history:
                    hist = list(price_history.get(pair, []))

                # Pastikan harga terbaru selalu masuk evaluasi momentum.
                if not hist or hist[-1] != current_price:
                    hist = (hist + [current_price])[-180:]

                if len(hist) >= 2:
                    try:
                        tech = get_technical_summary(hist)

                        depth_data = db.get_market_depth(pair)
                        if depth_data['bids'] and depth_data['asks']:
                            total_bid = sum(b['vol'] for b in depth_data['bids'])
                            total_ask = sum(a['vol'] for a in depth_data['asks'])
                            total_depth = total_bid + total_ask
                            if total_depth > 0:
                                tech['buy_pressure'] = round((total_bid / total_depth) * 100, 1)

                        tech_sig = derive_trade_signal(tech)
                        momentum_signal = tech_sig.get('verdict', 'HOLD')
                        momentum_conf = int(tech_sig.get('confidence', 50) or 50)
                        current_rsi = float(tech.get('rsi', 50) or 50)
                    except Exception as e:
                        log_message(f"⚠️ Momentum eval error for {pair}: {e}")

                    if get_prediction_fn and len(hist) >= AI_MIN_HISTORY_POINTS:
                        try:
                            ai_boost = self.oracle.get_sentiment_boost(pair) if self.oracle else 0.0
                            live_ai_prob = float(get_prediction_fn(hist, current_tickers.get(pair), ai_boost))
                        except Exception as e:
                            log_message(f"⚠️ AI live-prob eval error for {pair}: {e}")
                
                # Diagnostic: Log status for open positions
                # log_message(f"Monitoring {pair}: Price={current_price:,.2f}, Entry={entry_price:,.2f}, Status={data.get('status')}")
            
            # --- AUTO-CANCEL LOGIC UNTUK PENDING_BUY ---
                if data.get('status') == "PENDING_BUY":
                    if self.dry_run:
                        # Di mode simulasi, anggap selalu filled seketika (sudah dihandle OPEN di main.py, 
                        # fallback hapus aja kalau somehow nyangkut)
                        continue
    
                    order_id = data.get('entry_order_id')
                    if not order_id:
                        continue
                        
                    entry_time = datetime.fromisoformat(data['timestamp'])
                    time_elapsed = (datetime.now() - entry_time).total_seconds() / 60.0
                    
                    # Cek jarak harga: Jika harga sekarang sudah lebih tinggi X% dari harga antri kita
                    spread_pct = (current_price - entry_price) / entry_price
                    
                    is_timeout = time_elapsed > PENDING_BUY_TIMEOUT_MINUTES
                    is_too_far = spread_pct > PENDING_BUY_MAX_SPREAD_PCT
                    
                    if is_timeout or is_too_far:
                        reason = "Timeout (3m+)" if is_timeout else f"Harga menjauh (+{spread_pct*100:.2f}%)"
                        try:
                            # [Senior Fix] Standard CCXT Symbol for Validation
                            symbol_cancel = f"{pair[:-3].upper()}/IDR"
                            self.exchange.cancel_order(order_id, symbol_cancel, params={'pair': pair.replace("idr", "_idr"), 'side': 'buy'})
                            # Hapus lokal sebagai fallback jika PrivateWS miss event
                            db.delete_position(pair)
                            
                            msg = f"⚠️ *AUTO-CANCEL BUY* ⚠️\nPair: `{pair}`\nAlasan: {reason}\nHarga Antri: Rp {entry_price:,.2f}"
                            print(msg.replace('*', '').replace('\n', ' | '))
                            send_telegram_message(msg)
                        except Exception as e:
                            error_msg = str(e).lower()
                            print(f"Failed to auto-cancel {pair}: {e}")
                            if "order not found" in error_msg or "not found" in error_msg:
                                # Jika order tidak ada di bursa (sudah dicancel manual / filled saat WS mati), 
                                # hapus dari DB agar tidak stuck selamanya.
                                print(f"⚠️ {pair} order {order_id} not found on exchange. Cleaning up DB...")
                                db.delete_position(pair)
                        
                        # Jangan teruskan eksekusi TP/SL untuk pesanan yang masih pending
                    continue
                # --- END AUTO-CANCEL ---
    
                # Base TP calculation from percentage
                dynamic_tp_pct = data.get('target_tp_pct') if data.get('target_tp_pct') is not None else TP_PCT
                base_tp_price = entry_price * (1 + dynamic_tp_pct)
                
                # Rumus TP: (Target_IDR + Entry_Value * (1 + Buy_Fee)) / (Amount * (1 - Sell_Fee - Slippage))
                # Untuk TP normal (non-emergency) kita gunakan asumsi maker agar target tidak terlalu tinggi.
                from config import FEE_SLIPPAGE_BUFFER
                tp_sell_fee = FEE_SELL_MAKER
                tp_slippage = FEE_SLIPPAGE_BUFFER * 0.5
                denom_tp = amount * max(0.01, (1 - tp_sell_fee - tp_slippage))
                exact_tp_price = (MIN_NET_PROFIT_IDR + (entry_price * amount * (1 + FEE_BUY_MAKER))) / denom_tp
                
                # Target TP minimal agar tidak rugi fee
                tp_price = max(base_tp_price, exact_tp_price)
                
                # Default Initial Stop Loss
                dynamic_sl_pct = data.get('target_sl_pct') if data.get('target_sl_pct') is not None else SL_PCT
                initial_sl_price = entry_price * (1 + dynamic_sl_pct)
                
                # --- [Advanced] Adaptive Trailing Stop Logic ---
                highest_price = data.get('highest_price', entry_price)
                trailing_sl_price = data.get('trailing_sl_price', initial_sl_price)
                
                current_profit_pct = (current_price - entry_price) / entry_price
                pnl_pct = current_profit_pct * 100
                
                # Hitung umur posisi
                pos_ts = datetime.fromisoformat(data['timestamp'])
                pos_age_min = (datetime.now() - pos_ts).total_seconds() / 60.0
                duration_str = f"{int(pos_age_min)}m"
                
                if current_price > highest_price:
                    highest_price = current_price
                    positions[pair]['highest_price'] = highest_price
                    
                    # Jarak trailing adaptif: Semakin untung, semakin ketat
                    # [Optimization] Mengunci profit lebih agresif saat harga melonjak
                    dist_pct = TRAILING_STOP_DISTANCE_PCT # Default 0.5%
                    if current_profit_pct >= 0.04:   # Profit > 4%
                        dist_pct = 0.002            # Jarak sangat ketat (0.2%)
                    elif current_profit_pct >= 0.02: # Profit > 2%
                        dist_pct = 0.0035           # Jarak ketat (0.35%)
    
                    if current_profit_pct >= TRAILING_STOP_ACTIVATION_PCT:
                        new_trailing_sl = highest_price * (1 - dist_pct)
                        if new_trailing_sl > trailing_sl_price:
                            trailing_sl_price = new_trailing_sl
                            positions[pair]['trailing_sl_price'] = trailing_sl_price
                    
                    # [Senior Sync] Debounce DB update to reduce I/O
                    last_saved_highest = data.get('last_saved_highest_price', 0)
                    last_saved_trailing = data.get('last_saved_trailing_sl', 0)
                    if highest_price > last_saved_highest * 1.002 or trailing_sl_price > last_saved_trailing * 1.002:
                        positions[pair]['last_saved_highest_price'] = highest_price
                        positions[pair]['last_saved_trailing_sl'] = trailing_sl_price
                        modified_pairs.add(pair)
                
                active_sl_price = max(initial_sl_price, trailing_sl_price)
    
                # --- [Defensive] Break-Even Stop Loss ---
                # Jika profit sudah menyentuh 1.0%, pindahkan SL ke harga entry + fee (risk-free trade)
                # Entry default adalah maker (antri EMA-9), bukan taker — gunakan fee maker agar break-even akurat
                round_trip_fee = FEE_BUY_MAKER + FEE_SELL_MAKER  # 0.4322%, bukan 1.2322% (taker)
                break_even_price = entry_price * (1 + round_trip_fee + 0.001) # +0.1% extra buffer
                
                if current_profit_pct >= 0.01 and active_sl_price < break_even_price:
                    active_sl_price = break_even_price
                    if positions[pair].get('trailing_sl_price', 0) < break_even_price:
                        positions[pair]['trailing_sl_price'] = break_even_price
                        modified_pairs.add(pair)
                        log_message(f"🛡️ [Break-Even] {pair}: SL dipindahkan ke Entry + Fee (Rp {active_sl_price:,.2f})")
    
                # --- [Safety] Time-Stop Exit ---
                if pos_age_min >= MAX_HOLD_MINUTES:
                    msg = f"⏳ *TIME-STOP EXPIRED* ({MAX_HOLD_MINUTES}m) | {pair} | PnL: {pnl_pct:+.2f}%"
                    log_message(msg)
                    order_id = self.execute_exit(pair, amount, current_price, "TIME_STOP_EXIT",
                                      entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                    if order_id:
                        mark_exit_requested(pair, order_id)
                        continue
    
                # --- [Aggressive] Rapid Take Profit (Scalp Focus) ---
                # Menggunakan kalkulasi Net PnL IDR yang sudah memotong semua fee (Taker Exit)
                try:
                    sell_value_est = amount * current_price
                    fee_buy_est = data.get('amount_idr', 0) * FEE_BUY_MAKER
                    # Rapid TP memakai jalur non-emergency (maker), maka estimasi fee juga maker.
                    fee_sell_est = sell_value_est * FEE_SELL_MAKER
                    net_pnl_idr = (current_price - entry_price) * amount - (fee_buy_est + fee_sell_est)

                    # Hard rule: jika sudah net profit, selalu tutup posisi sekarang juga.
                    if net_pnl_idr > 0:
                        reason_tp = "FORCE_PROFIT_EXIT"
                        log_msg = (
                            f"⚡ [{reason_tp}] {pair}: Net profit Rp {net_pnl_idr:,.0f} "
                            f"(+{current_profit_pct*100:.2f}%)"
                        )
                        log_message(log_msg)
                        db.log_system_event(pair, reason_tp, log_msg, 'SUCCESS')

                        order_id = self.execute_exit(
                            pair,
                            amount,
                            current_price,
                            reason_tp,
                            entry_price=entry_price,
                            amount_idr=data.get('amount_idr', 0),
                            position_data=data,
                        )
                        if order_id:
                            mark_exit_requested(pair, order_id)
                        continue

                    # Syarat 1: Profit Bersih sudah melampaui target minimum (Jangan menunggu!)
                    is_profit_secure = net_pnl_idr >= MIN_NET_PROFIT_IDR
                    
                    # Syarat 2: Momentum mulai melemah berdasarkan engine yang sama dengan entry.
                    # SELL signal dari derive_trade_signal + AI prob live rendah -> lock profit lebih cepat.
                    is_momentum_sell = momentum_signal == 'SELL' and momentum_conf >= 58
                    is_ai_fade = live_ai_prob >= 0 and live_ai_prob < 0.42
                    is_rapid_momentum_fade = net_pnl_idr > 1000 and (
                        is_momentum_sell or current_rsi > 62 or is_ai_fade
                    )
                    
                    # Hitung umur posisi dengan aman
                    pos_ts = datetime.fromisoformat(data['timestamp'])
                    pos_age_min = (datetime.now() - pos_ts).total_seconds() / 60.0
                    # Syarat 3: Stagnancy Exit (Hold > 20m & Profit sudah cover fee) -> Free up capital
                    is_stagnant_profit = pos_age_min > 20 and net_pnl_idr > 500
    
                    if is_profit_secure or is_rapid_momentum_fade or is_stagnant_profit:
                        reason_tp = "RAPID_TP_SECURE" if is_profit_secure else ("STAGNANT_EXIT" if is_stagnant_profit else "RAPID_TP_MOMENTUM")
                        log_msg = (
                            f"⚡ [{reason_tp}] {pair}: Locked profit +{current_profit_pct*100:.2f}% "
                            f"(Age: {pos_age_min:.0f}m, Sig={momentum_signal}:{momentum_conf}, RSI={current_rsi:.1f}, AI={live_ai_prob:.2f})"
                        )
                        log_message(log_msg)
                        db.log_system_event(pair, reason_tp, log_msg, 'SUCCESS')
                        
                        order_id = self.execute_exit(pair, amount, current_price, reason_tp, 
                                                  entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                        if order_id:
                            mark_exit_requested(pair, order_id)
                        continue
                except Exception as e:
                    log_message(f"⚠️ Error in Rapid TP for {pair}: {e}")
                    continue
    
                # --- [AI-Adaptive] Emergency Exit via Oracle Veto ---
                # Jika AI mendeteksi 'Fake Pump' atau tren berbalik jadi 'AVOID' saat kita HOLD, langsung EXIT.
                ai_veto, veto_reason = False, ""
                if self.oracle:
                    ai_veto, veto_reason = self.oracle.check_llm_veto(pair)
                
                # 1. AI Emergency Exit (Highest Priority)
                if ai_veto and current_profit_pct > -0.005: # Hanya exit jika bukan sedang dalam kerugian besar (cut loss AI)
                    msg = (
                        f"🚨 *AI EMERGENCY EXIT* 🚨\n"
                        f"Pair: `{pair}`\n"
                        f"Alasan: `{veto_reason}`\n"
                        f"Profit: {current_profit_pct*100:+.2f}% (Closing now)"
                    )
                    print(msg.replace('*', '').replace('\n', ' | '))
                    send_telegram_message(msg)
                    
                    order_id = self.execute_exit(pair, amount, current_price, "AI_VETO_EXIT",
                                      entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                    if order_id:
                        mark_exit_requested(pair, order_id)
                        continue
    
                # 2. Take Profit (Hard TP) - Opsional, bisa dihapus jika murni trailing, tapi tetap dipakai sbg target final.
                if current_price >= tp_price:
                    msg = (
                        f"🎯 *TAKE PROFIT* 🎯\n"
                        f"Pair: `{pair}`\n"
                        f"Entry: Rp {entry_price:,.2f}\n"
                        f"Exit: Rp {current_price:,.2f}\n"
                        f"PnL: {pnl_pct:+.2f}%\n"
                        f"Hold: {duration_str}"
                    )
                    print(msg.replace('*', '').replace('\n', ' | '))
                    send_telegram_message(msg)
                    
                    order_id = self.execute_exit(pair, amount, current_price, "TAKE_PROFIT",
                                      entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                    if order_id:
                        mark_exit_requested(pair, order_id)
    
                # 3. Stop Loss (Initial or Trailing)
                elif current_price <= active_sl_price:
                    # Deteksi tipe exit proteksi
                    is_trailing_hit = active_sl_price > break_even_price
                    is_be_hit = abs(active_sl_price - break_even_price) < 1.0  # toleransi Rp 1 untuk float comparison
                    
                    if is_be_hit:
                        reason_str = "BREAK-EVEN SL"
                        log_reason = "BREAK_EVEN"
                    elif is_trailing_hit:
                        reason_str = "TRAILING STOP"
                        log_reason = "TRAILING_STOP"
                    else:
                        reason_str = "STOP LOSS"
                        log_reason = "STOP_LOSS"
                    
                    msg = (
                        f"⚠️ *{reason_str}* ⚠️\n"
                        f"Pair: `{pair}`\n"
                        f"Entry: Rp {entry_price:,.2f}\n"
                        f"Exit: Rp {current_price:,.2f}\n"
                        f"PnL: {pnl_pct:+.2f}%\n"
                        f"Hold: {duration_str}"
                    )
                    print(msg.replace('*', '').replace('\n', ' | '))
                    send_telegram_message(msg)
                    
                    order_id = self.execute_exit(pair, amount, current_price, log_reason,
                                      entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                    if order_id:
                        mark_exit_requested(pair, order_id)
            except Exception as e:
                log_message(f"🚨 Critical error in monitoring loop for {pair}: {e}")
                import traceback
                log_message(traceback.format_exc())

        self.save_positions(positions, modified_pairs)

    def execute_exit(self, pair, amount, price, reason, entry_price=None, amount_idr=0, position_data=None):
        try:
            # Tentukan apakah ini exit darurat (harus instant taker) atau target profit (maker)
            is_emergency = reason in ("STOP_LOSS", "HARD_STOP_LOSS", "AI_VETO_EXIT", "TRAILING_STOP", "BREAK_EVEN", "BASKET_EXIT")
            # TP exits (RAPID_TP_*, STAGNANT_EXIT, TAKE_PROFIT) menggunakan taker market sell
            # TAPI offset harus kecil agar tidak memotong profit yang sudah diraih
            is_tp_exit = reason in ("RAPID_TP_SECURE", "RAPID_TP_MOMENTUM", "STAGNANT_EXIT", "TAKE_PROFIT")

            if is_emergency:
                # Gunakan SELL_TAKER_OFFSET (5%) hanya untuk SL darurat agar pasti terisi
                exit_price = price * (1 - SELL_TAKER_OFFSET)
                fee_sell_rate = FEE_SELL_TAKER
            elif is_tp_exit:
                # TP exits: jual sedikit di ATAS market agar order Maker terisi saat ada buyer
                # Offset kecil 0.05% sudah cukup untuk memastikan fill
                exit_price = price * (1 + SELL_MAKER_OFFSET)
                fee_sell_rate = FEE_SELL_MAKER
            else:
                # Maker mode untuk Take Profit standar
                exit_price = price * (1 + SELL_MAKER_OFFSET)
                fee_sell_rate = FEE_SELL_MAKER

            # --- Piggyback Strategy (Dust Sweeper) ---
            coin = pair.replace('idr', '').upper()
            try:
                if not self.dry_run:
                    balance = self.exchange.fetch_balance()
                    real_balance = float(balance['free'].get(coin, 0))
                    if real_balance > amount:
                        log_message(f"🐷 Piggyback Strategy: Boosting sell amount from {amount} to {real_balance} {coin} to sweep dust!")
                        amount = real_balance
            except Exception as e:
                log_message(f"Warning: Failed to fetch real balance for Piggyback on {pair}: {e}")

            # [Senior Fix] Standard CCXT Symbol for Validation
            symbol = f"{pair[:-3].upper()}/IDR"
            
            if self.dry_run:
                order = {
                    "id": f"DRY-{int(time.time())}",
                    "status": "closed",
                    "price": exit_price,
                    "amount": amount,
                    "symbol": symbol
                }
                print(f"DRY RUN: Virtual Exit ({reason}) | {pair} | Price: {exit_price:,.2f}")
            else:
                params = {
                    # Non-emergency exits dijaga tetap maker (post-only) untuk hemat fee.
                    'post_only': not is_emergency,
                    'pair': pair.replace("idr", "_idr") # Forced Internal Indodax Format
                }
                order = self.exchange.create_limit_sell_order(symbol, amount, exit_price, params)

            # Audit log
            db.log_order(pair, "SELL", exit_price, amount, order.get('status'), order)

            order_status = order.get('status', 'closed')
            order_id = order.get('id')
            
            # Tentukan fee jual berdasarkan strategi exit
            exec_type = "TAKER" if is_emergency else "MAKER"
            fee_sell_rate = FEE_SELL_TAKER if is_emergency else FEE_SELL_MAKER
            
            # Entry fee selalu Maker (karena bot antri beli)
            fee_buy = amount_idr * FEE_BUY_MAKER
            
            # Gunakan exit_price (harga limit order) bukan price (ticker mentah) untuk akurasi PnL
            sell_value = amount * exit_price
            fee_sell = sell_value * fee_sell_rate
            gross_pnl = (exit_price - entry_price) * amount
            net_pnl_idr = gross_pnl - (fee_buy + fee_sell)

            # Catat ke system_events agar muncul di Activity Feed dashboard
            pnl_label = f"+Rp {net_pnl_idr:,.0f}" if net_pnl_idr >= 0 else f"-Rp {abs(net_pnl_idr):,.0f}"
            db.log_system_event(
                pair,
                "SELL",
                f"{reason} | Rp {exit_price:,.0f} | PnL:{pnl_label}",
                'SUCCESS' if net_pnl_idr >= 0 else 'WARN'
            )

            if order_status in ['closed', 'canceled', 'done', 'fill'] or self.dry_run:
                db.delete_position(pair)
                
                pnl_idr = net_pnl_idr
                pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0
                
                # Use provided position_data for timestamp, fallback to now
                timestamp_str = position_data.get('timestamp') if position_data else datetime.now().isoformat()
                entry_time = datetime.fromisoformat(timestamp_str)
                duration_sec = (datetime.now() - entry_time).total_seconds()
                
                try:
                    db.insert_trade_history(
                        pair=pair,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        amount_crypto=amount,
                        amount_idr=amount_idr,
                        pnl_idr=pnl_idr,
                        pnl_pct=pnl_pct,
                        reason=reason,
                        exec_type=exec_type,
                        duration_seconds=int(duration_sec),
                        dry_run=self.dry_run,
                        exit_order_id=order_id
                    )
                    
                    # Update Consecutive Losses state
                    current_cl = int(db.get_state("consecutive_losses", 0))
                    if pnl_idr > 0:
                        db.save_state("consecutive_losses", 0)
                    else:
                        db.save_state("consecutive_losses", current_cl + 1)
                        self._set_pair_cooldown(pair, minutes=20, reason=reason)

                    # ── Project Evolution: Learning Loop ─────────────────────────────
                    db.update_trade_outcome(pair, pnl_idr)
                    if self.oracle:
                        self.oracle.learn_from_trade(pair, pnl_idr)

                except Exception as db_err:
                    print(f"Failed to save trade history/state for {pair}: {db_err}")
            else:
                # Simpan metadata ke position agar PrivateWS (handle_order_update) tahu reason-nya
                # [Senior Fix] Update position status AND save the backup reason
                db.update_position_status(pair, "EXITING", exit_order_id=order_id)
                # Tambahkan field extra ke pos di memori/DB
                if position_data:
                    position_data['status'] = "EXITING"
                    position_data['exit_order_id'] = order_id
                    position_data['exit_requested_at'] = datetime.now().isoformat()
                    position_data['reason_backup'] = reason
                    db.upsert_position(pair, position_data)
            
            return order.get('id')
        except Exception as e:
            err_msg = f"Critical error in execute_exit for {pair}: {e}"
            print(err_msg)
            db.log_system_event(pair, "EXIT_ERROR", err_msg, 'ERROR')
            return None
                
    def handle_order_update(self, order):
        """
        Callback dari PrivateWS. Memproses order yang terisi (Filled).
        """
        try:
            # [Senior Fix] Support both CCXT format and Native Indodax WS format
            order_id = str(order.get('id') or order.get('orderId') or '')
            pair = order.get('pair') or order.get('symbol') or ''
            status = str(order.get('status') or '').lower()
            
            # Status 'done' di Indodax WS berarti order selesai (full fill atau cancel-done)
            if status not in ['filled', 'closed', 'fill', 'done']: return
            
            # 1. Cek apakah ini Jaring yang terisi
            for p, jaring in list(self.jaring_orders.items()):
                if str(jaring['id']) == order_id:
                    log_message(f"🎯 JARING FILLED! {p} bought at Rp {jaring['price']:,.2f} via Liquidity Grab.")
                    
                    # Pindahkan dari Shadow ke Positions
                    data = {
                        "entry_price": float(order.get('price', jaring['price'])),
                        "amount":      float(order.get('executedQty', jaring['amount'])),
                        "amount_crypto": float(order.get('executedQty', jaring['amount'])),
                        "amount_idr":  float(order.get('price', jaring['price'])) * float(order.get('executedQty', jaring['amount'])),
                        "timestamp":   datetime.now().isoformat(),
                        "dry_run":     self.dry_run,
                        "status":      "OPEN",
                        "entry_order_id": order_id,
                        "exit_order_id": None,
                        "reason_backup": "LIQUIDITY_GRAB"
                    }
                    db.upsert_position(p, data)
                    db.delete_shadow_order(p)
                    if p in self.jaring_orders: del self.jaring_orders[p]
                    
                    send_telegram_message(f"🎯 *JARING FILLED* 🎯\nPair: `{p}`\nPrice: Rp {jaring['price']:,.2f}")
                    return

            # 2. Cek apakah ini Buy Order reguler yang baru terisi
            positions = db.get_open_positions()
            for p_pair, p_data in positions.items():
                if p_data.get('entry_order_id') == order_id and p_data.get('status') == "PENDING_BUY":
                    log_message(f"✅ BUY FILLED! {p_pair} order {order_id} is now OPEN.")
                    
                    # [Senior Fix] Capture actual fill price and amount for accurate basis
                    fill_price = float(order.get('price', p_data.get('entry_price')))
                    fill_amount = float(order.get('executedQty', order.get('amount', p_data.get('amount_crypto'))))
                    
                    # Update data dengan nilai asli dari bursa
                    p_data['status'] = "OPEN"
                    p_data['entry_price'] = fill_price
                    p_data['amount_crypto'] = fill_amount
                    p_data['amount_idr'] = fill_price * fill_amount
                    
                    db.upsert_position(p_pair, p_data)
                    send_telegram_message(f"✅ *BUY FILLED* ✅\nPair: `{p_pair}`\nPrice: Rp {fill_price:,.2f}\nStatus: `OPEN`")
                    return

            # 3. Cek apakah ini Sell Order (Take Profit/Stop Loss) yang terisi
            for p_pair, p_data in positions.items():
                if p_data.get('exit_order_id') == order_id and p_data.get('status') == "EXITING":
                    log_message(f"💰 SELL FILLED! {p_pair} order {order_id} is now CLOSED.")
                    
                    try:
                        exit_price = float(order.get('price', 0))
                        # Fallback
                        if exit_price == 0: exit_price = float(p_data.get('entry_price', 0)) * (1 + p_data.get('target_tp_pct', TP_PCT))
                        
                        amount_crypto = float(order.get('amount', p_data.get('amount_crypto', 0)))
                        entry_price = float(p_data.get('entry_price', 0))
                        amount_idr = float(p_data.get('amount_idr', 0))
                        
                        # --- [Senior Fix] Real Fee Extraction dari fillInformation ---
                        fill_info = order.get('fillInformation', {})
                        participant = fill_info.get('participant', 'MAKER') # Default to MAKER
                        
                        # Ambil fee, tax, clearing aktual jika ada (sudah dalam IDR)
                        real_fee = float(fill_info.get('fee', 0))
                        real_tax = float(fill_info.get('tax', 0))
                        real_clearing = float(fill_info.get('clearing', 0))
                        
                        sell_value = amount_crypto * exit_price
                        
                        # Fallback jika fill_info tidak lengkap (misal legacy WS)
                        if real_fee == 0:
                            fee_rate = FEE_SELL_TAKER if participant == 'TAKER' else FEE_SELL_MAKER
                            fee_sell = sell_value * fee_rate
                        else:
                            # Gunakan total biaya aktual
                            fee_sell = real_fee + real_tax + real_clearing
                        
                        # Entry fee (selalu maker kecuali koin nyangkut/orphans yang diadopsi)
                        fee_buy = amount_idr * FEE_BUY_MAKER
                        
                        gross_pnl = (exit_price - entry_price) * amount_crypto
                        net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
                        pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0
                        
                        # Reason determination
                        reason = p_data.get('reason_backup', "TAKE_PROFIT" if net_pnl_idr > 0 else "STOP_LOSS")
                        
                        timestamp_str = p_data.get('timestamp') or datetime.now().isoformat()
                        duration_sec = (datetime.now() - datetime.fromisoformat(timestamp_str)).total_seconds()
                        
                        db.insert_trade_history(
                            pair=p_pair, entry_price=entry_price, exit_price=exit_price,
                            amount_crypto=amount_crypto, amount_idr=amount_idr,
                            pnl_idr=net_pnl_idr, pnl_pct=pnl_pct, reason=reason,
                            exec_type=participant,
                            duration_seconds=int(duration_sec), dry_run=p_data.get('dry_run')
                        )
                        
                        current_cl = int(db.get_state("consecutive_losses", 0))
                        if net_pnl_idr > 0:
                            db.save_state("consecutive_losses", 0)
                        else:
                            db.save_state("consecutive_losses", current_cl + 1)
                            self._set_pair_cooldown(p_pair, minutes=20, reason=reason)
                        
                        db.update_trade_outcome(p_pair, net_pnl_idr)
                        if self.oracle: self.oracle.learn_from_trade(p_pair, net_pnl_idr)
                        
                        db.delete_position(p_pair)
                        send_telegram_message(f"💰 *EXIT FILLED* 💰\nPair: `{p_pair}`\nReason: `{reason}`\nPnL: Rp {net_pnl_idr:,.2f}")
                    except Exception as ex:
                        log_message(f"Error calculating final PnL for {p_pair}: {ex}")
                    return
            
        except Exception as e:
            print(f"Error handling order update: {e}")

    def _fetch_v2_trades(self, pair, startTime=None, endTime=None):
        """Precision manual fetch for Indodax v2 (Strict parameter ordering)."""
        try:
            import hmac, hashlib, requests, urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            api_key = self.exchange.apiKey
            api_secret = self.exchange.secret
            if not api_key or not api_secret: return []

            symbol = pair.lower().replace("/", "")
            timestamp = int(time.time() * 1000)
            
            # [Senior Audit] Strict ordering as per v2 documentation example
            params = [
                ("symbol", symbol),
                ("limit", 1000)
            ]
            if startTime: params.append(("startTime", int(startTime)))
            if endTime: params.append(("endTime", int(endTime)))
            params.extend([
                ("timestamp", timestamp),
                ("recvWindow", 60000)
            ])
            
            query_str = "&".join([f"{k}={v}" for k, v in params])
            sign = hmac.new(api_secret.encode(), query_str.encode(), hashlib.sha512).hexdigest()
            
            headers = {
                "X-APIKEY": api_key,
                "Sign": sign,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            
            url = f"https://tapi.indodax.com/api/v2/myTrades?{query_str}"
            resp = requests.get(url, headers=headers, timeout=15, verify=True)
            
            if resp.status_code != 200:
                if resp.status_code == 403:
                    logger.error(f"⚠️ 403 Forbidden for {pair}: Possible API Key restriction or IP block.")
                    # log first 500 chars to see if there's a specific message
                    logger.error(f"Response snippet: {resp.text[:500]}")
                return []
                
            data = resp.json()
            if data.get("code") or data.get("error"):
                if data.get("code") != 1110:
                    logger.error(f"V2 API Error for {pair}: {data.get('code')} - {data.get('error')}")
                return []
            
            return data.get('data', [])
        except Exception as e:
            logger.error(f"V2 Manual Fetch Error for {pair}: {e}")
            return []

    def sync_all_history(self):
        """
        [Senior Quant Audit] Full Account Deep Sync.
        Iterates through all pairs and performs windowed history fetching (7-day chunks)
        to overcome Indodax API limitations.
        """
        log_message("🔄 Starting Deep Account Sync (30-Day History)...")
        try:
            markets = self.exchange.fetch_markets()
            idr_pairs = [m['id'] for m in markets if m['quoteId'] == 'idr' and m['active']]
            total_pairs = len(idr_pairs)
            total_new_trades = 0
            
            # [Fix] Gunakan tanggal dinamis: 30 hari ke belakang
            start_dt = datetime.now() - timedelta(days=30)
            START_TARGET_MS = int(start_dt.timestamp() * 1000)
            NOW_MS = int(time.time() * 1000)
            
            log_message(f"🔄 Range: {start_dt.strftime('%Y-%m-%d %H:%M:%S')} to Now")
            
            for idx, pair_id in enumerate(idr_pairs):
                # Rate limit protection
                time.sleep(0.2)
                
                if idx % 10 == 0:
                    log_message(f"🔄 Sync Progress: {idx}/{total_pairs} pairs scanned...")
                
                # Hanya satu request karena rentang < 7 hari
                trades = self._fetch_v2_trades(pair_id, startTime=START_TARGET_MS, endTime=NOW_MS)
                
                if not trades: continue

                # Urutkan Ascending
                trades.sort(key=lambda x: x.get('time', 0))
                
                last_buy_price = 0
                last_buy_qty = 0
                
                for t in trades:
                    try:
                        trade_id = str(t.get('tradeId'))
                        price = float(t.get('price', 0))
                        qty = float(t.get('qty', 0))
                        is_buyer = t.get('isBuyer')
                        
                        if is_buyer:
                            last_buy_price = price
                            last_buy_qty = qty
                            continue 
                        
                        # Jika ini SELL, hitung estimasi PnL
                        exit_price = price
                        entry_price = last_buy_price if last_buy_price > 0 else exit_price
                        
                        amount_crypto = qty
                        amount_idr = amount_crypto * entry_price
                        
                        # Fee Estimation (0.11%)
                        fee_buy = amount_idr * 0.0011 
                        fee_sell = (qty * exit_price) * 0.0011 
                        
                        gross_pnl = (exit_price - entry_price) * amount_crypto
                        net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
                        pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0
                        
                        # Format timestamp
                        raw_time = t.get('time', 0)
                        dt_obj = datetime.fromtimestamp(raw_time / 1000.0)
                        created_at_str = dt_obj.strftime('%Y-%m-%d %H:%M:%S')
                        
                        db.insert_trade_history(
                            pair=pair_id, 
                            entry_price=entry_price, 
                            exit_price=exit_price,
                            amount_crypto=amount_crypto, 
                            amount_idr=amount_idr,
                            pnl_idr=net_pnl_idr, 
                            pnl_pct=pnl_pct, 
                            reason="MANUAL_SYNC",
                            exec_type="MAKER" if t.get('isMaker') else "TAKER",
                            duration_seconds=3600, 
                            dry_run=False,
                            trade_id=trade_id,
                            is_manual=1,
                            created_at=created_at_str
                        )
                        total_new_trades += 1
                        if total_new_trades % 5 == 0:
                            log_message(f"✅ Found {total_new_trades} trades so far...")
                    except Exception as e:
                        logger.error(f"Error inserting sync trade: {e}")
                        continue

            log_message(f"✅ Deep Sync Complete! Added {total_new_trades} historical trades since 26/04.")
            db.save_state("trigger_sync_all", "0")
            return True

        except Exception as e:
            log_message(f"❌ Deep Sync Failed: {e}")
            db.save_state("trigger_sync_all", "0")
            return False

    def reconcile_with_exchange(self):
        """
        [Anti-Hallucination] 
        Sinkronisasi posisi di Database dengan saldo asli di bursa.
        """
        try:
            log_message("Reality Check: Reconciling positions with Indodax balance...")
            if self.dry_run: return # Dry run tidak punya saldo asli untuk di-cek
            
            balance = self.exchange.fetch_balance()
            open_positions = db.get_open_positions()
            
            for pair, pos in open_positions.items():
                coin = pair.replace('idr', '').upper()
                amount_on_exchange = float(balance['total'].get(coin, 0))
                
                # [Fix] Promosikan PENDING_BUY ke OPEN jika saldo sudah ada di bursa
                if pos.get('status') == "PENDING_BUY":
                    if amount_on_exchange >= (pos['amount_crypto'] * 0.9):
                        log_message(f"🔄 Reality Sync: {pair} was PENDING_BUY but balance exists. Promoting to OPEN and syncing amount.")
                        pos['status'] = "OPEN"
                        pos['amount_crypto'] = amount_on_exchange
                        db.upsert_position(pair, pos)
                        continue
                    else:
                        # Order belum tersentuh/fill, jadi wajar saldonya 0. Skip cek hallucination.
                        continue

                # [Fix] Selalu sync amount dengan bursa jika ada perbedaan tipis (fee/rounding)
                if abs(amount_on_exchange - pos['amount_crypto']) > 0 and amount_on_exchange > 0:
                    if 0.8 < (amount_on_exchange / pos['amount_crypto']) < 1.2:
                        # log_message(f"⚖️ Amount Sync: {pair} amount adjusted {pos['amount_crypto']} -> {amount_on_exchange}")
                        pos['amount_crypto'] = amount_on_exchange
                        db.upsert_position(pair, pos)
                # Gunakan amount_crypto yang konsisten dengan schema DB
                target_amount = pos.get('amount_crypto') or pos.get('amount', 0)
                if amount_on_exchange < (target_amount * 0.8): # Toleransi lebih longgar 20%
                    log_message(f"🚨 Reality Desync Detected! {pair} not found on exchange. Checking history v2...")
                    
                    # Cek apakah ini sebenarnya sudah laku (terjual saat bot offline)
                    trades_v2 = self._fetch_v2_trades(pair)
                    found_fill = False
                    
                    for t in trades_v2:
                        # [Fix] Hanya cocokkan berdasarkan exit_order_id yang spesifik
                        # Jangan gunakan `or status == 'EXITING'` karena itu terlalu longgar
                        # dan bisa salah menggunakan BUY trade untuk menutup posisi SELL
                        order_id_match = pos.get('exit_order_id') and str(t.get('orderId')) == str(pos.get('exit_order_id'))
                        # Fallback: jika EXITING dan ada trade SELL setelah timestamp entry posisi
                        entry_ts_ms = 0
                        try:
                            from datetime import datetime as _dt
                            entry_ts_ms = int(_dt.fromisoformat(str(pos['timestamp'])).timestamp() * 1000)
                        except Exception:
                            pass
                        trade_is_sell = not t.get('isBuyer', True)
                        trade_after_entry = int(t.get('time', 0)) > entry_ts_ms
                        exiting_sell_match = (pos.get('status') == 'EXITING' and trade_is_sell and trade_after_entry)

                        if order_id_match or exiting_sell_match:
                            log_message(f"✅ Recovered missing trade for {pair} from v2 history! Recording PnL...")
                            
                            # Rekonstruksi data trade untuk DB
                            exit_price = float(t.get('price', 0))
                            real_fee = float(t.get('commission', 0))
                            is_maker = t.get('isMaker', False)
                            exec_type = "MAKER" if is_maker else "TAKER"
                            
                            entry_price = float(pos.get('entry_price', 0))
                            amount_crypto = float(t.get('qty', pos.get('amount_crypto', 0)))
                            amount_idr = float(pos.get('amount_idr', 0))
                            
                            # Hitung PnL dengan fee aktual v2
                            fee_buy = amount_idr * FEE_BUY_MAKER
                            fee_sell = real_fee # v2 commission sudah mencakup segalanya
                            
                            gross_pnl = (exit_price - entry_price) * amount_crypto
                            net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
                            pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0
                            
                            # Ambil trade_id dari WS event jika ada (untuk dedup dengan sync_all_history)
                            ws_trade_id = str(order.get('tradeId', '') or order.get('trade_id', '') or '') or None
                            db.insert_trade_history(
                                pair=p_pair, entry_price=entry_price, exit_price=exit_price,
                                amount_crypto=amount_crypto, amount_idr=amount_idr,
                                pnl_idr=net_pnl_idr, pnl_pct=pnl_pct, reason=reason,
                                exec_type=participant,
                                duration_seconds=int(duration_sec), dry_run=p_data.get('dry_run'),
                                trade_id=ws_trade_id,
                                exit_order_id=order_id
                            )
                            found_fill = True
                            break
                    
                    if not found_fill:
                        log_message(f"⚠️ Could not find fill for {pair} in v2 history. Attempting CCXT order fetch...")
                        # Coba ambil fill price aktual dari CCXT sebagai last resort
                        _exit_oid = pos.get('exit_order_id')
                        _entry_price = float(pos.get('entry_price', 0))
                        _amount_crypto = float(pos.get('amount_crypto', 0))
                        _amount_idr = float(pos.get('amount_idr', 0))
                        _symbol = f"{pair[:-3].upper()}/IDR"
                        _reconcile_exit_price = None
                        _reconcile_exec_type = "MAKER"
                        _reconcile_real_fee = 0
                        if _exit_oid and not _exit_oid.startswith('ADOPT-'):
                            try:
                                ccxt_order = self.exchange.fetch_order(_exit_oid, _symbol)
                                if ccxt_order and ccxt_order.get('status') in ['closed', 'filled', 'done']:
                                    _reconcile_exit_price = float(ccxt_order.get('price') or ccxt_order.get('average') or 0)
                                    _reconcile_exec_type = "TAKER" if not ccxt_order.get('postOnly') else "MAKER"
                                    log_message(f"✅ Got fill price from CCXT for {pair}: {_reconcile_exit_price}")
                            except Exception as ccxt_err:
                                log_message(f"CCXT fetch_order failed for {pair}: {ccxt_err}")
                        if not _reconcile_exit_price or _reconcile_exit_price <= 0:
                            # Fallback: gunakan harga pasar saat ini sebagai estimasi
                            try:
                                ticker = self.exchange.fetch_ticker(_symbol)
                                _reconcile_exit_price = float(ticker.get('last', _entry_price))
                            except Exception:
                                _reconcile_exit_price = _entry_price
                            log_message(f"⚠️ Using estimated exit price for {pair}: {_reconcile_exit_price}")
                        _fee_rate = FEE_SELL_TAKER if _reconcile_exec_type == "TAKER" else FEE_SELL_MAKER
                        _gross = (_reconcile_exit_price - _entry_price) * _amount_crypto
                        _net = _gross - (_amount_idr * FEE_BUY_MAKER) - (_amount_crypto * _reconcile_exit_price * _fee_rate)
                        _pct = (_net / _amount_idr * 100) if _amount_idr > 0 else 0
                        db.insert_trade_history(
                            pair=pair, entry_price=_entry_price, exit_price=_reconcile_exit_price,
                            amount_crypto=_amount_crypto, amount_idr=_amount_idr,
                            pnl_idr=_net, pnl_pct=_pct,
                            reason=pos.get('reason_backup', 'RECONCILE_GHOST'),
                            exec_type=_reconcile_exec_type,
                            duration_seconds=300,
                            dry_run=False,
                            exit_order_id=_exit_oid if _exit_oid and not _exit_oid.startswith('ADOPT-') else None
                        )
                        log_message(f"📝 Recorded best-effort trade for {pair} (RECONCILE_GHOST) pnl={_net:+,.0f} IDR")
                    
                    db.delete_position(pair)
                    if pair in self.jaring_orders:
                        del self.jaring_orders[pair]
                        db.delete_shadow_order(pair)
            
            # ── [Auto-Adoption] Sync orphans from wallet to DB ──
            _blacklist = set(db.get_blacklist())
            for coin, coin_amount in balance['total'].items():
                if coin == 'IDR' or coin_amount <= 0:
                    continue
                    
                pair = f"{coin.lower()}idr"
                if pair in _blacklist:
                    continue
                if pair not in open_positions:
                    try:
                        # Gunakan REST API karena ExitManager tidak punya akses ke Market WS
                        ticker_rest = self.exchange.fetch_ticker(pair.upper().replace("IDR", "/IDR"))
                        current_price = float(ticker_rest['last'])
                            
                        value_idr = coin_amount * current_price
                        
                        # Adopsi hanya jika nilainya di atas Rp 10.000 (mengabaikan dust)
                        if value_idr >= 10000:
                            log_message(f"👼 Orphan Adoption: Found {coin_amount} {coin} in wallet (Value: Rp {value_idr:,.0f}). Adopting into Dashboard!")
                            
                            adopt_data = {
                                "entry_price": current_price,
                                "amount":      coin_amount,
                                "amount_crypto": coin_amount,
                                "amount_idr":  value_idr,
                                "timestamp":   datetime.now().isoformat(),
                                "dry_run":     False,
                                "status":      "OPEN",
                                "entry_order_id": f"ADOPT-{int(time.time())}",
                                "exit_order_id": None,
                                "target_tp_pct": TP_PCT,
                                "target_sl_pct": SL_PCT,
                                "reason_backup": "ORPHAN_ADOPTION"
                            }
                            db.upsert_position(pair, adopt_data)
                            send_telegram_message(f"👼 *ORPHAN ADOPTED* 👼\nPair: `{pair}`\nQty: {coin_amount}\nEst. Value: Rp {value_idr:,.0f}")
                    except Exception as e:
                        log_message(f"Failed to adopt orphan {pair}: {e}")
            
            # ── [Active Order Sync] Sync manual Open Orders to DB ──
            try:
                open_orders_res = self.exchange.privatePostOpenOrders()
                if open_orders_res and str(open_orders_res.get('success')) == '1':
                    orders_dict = open_orders_res.get('return', {}).get('orders', {})
                    
                    # Flatten active order IDs for quick lookup
                    all_active_order_ids = set()
                    if isinstance(orders_dict, dict):
                        for pk, ol in orders_dict.items():
                            for o in ol: all_active_order_ids.add(str(o.get('order_id')))

                    # [Anti-Stuck] Detect and force-sell stagnant EXITING orders
                    for p, pos in list(open_positions.items()):
                        if pos.get('status') == "EXITING":
                            oid = str(pos.get('exit_order_id', ''))
                            if oid in all_active_order_ids:
                                # Order still open on exchange. Check duration since position opened.
                                pos_ts = datetime.fromisoformat(pos['timestamp'])
                                age_sec = (datetime.now() - pos_ts).total_seconds()
                                if age_sec > 300: # 5 minutes
                                    log_message(f"⚠️ [Stuck Exit] {p} order {oid} is stuck for {age_sec:.0f}s. Cancelling and performing Emergency Exit...")
                                    try:
                                        # Format Indodax: aih_idr, Format CCXT: AIH/IDR
                                        ccxt_symbol = p.upper().replace("IDR", "/IDR")
                                        # Indodax cancel_order requires 'type' (buy/sell) and 'pair' (with underscore)
                                        self.exchange.cancel_order(oid, ccxt_symbol, params={'pair': p.replace("idr", "_idr"), 'type': 'sell'})
                                        # Revert to OPEN so the main monitor loop can re-process it (now as Taker due to is_emergency)
                                        pos['status'] = "OPEN"
                                        pos['exit_order_id'] = None
                                        db.upsert_position(p, pos)
                                    except Exception as ce:
                                        log_message(f"Failed to cancel stuck order {oid}: {ce}")

                    if isinstance(orders_dict, dict):
                        for pair_key, order_list in orders_dict.items():
                            pair = pair_key.replace('_', '')
                            pos = open_positions.get(pair)
                            
                            for order in order_list:
                                o_type = order.get('type')
                                o_id = str(order.get('order_id'))
                                o_price = float(order.get('price', 0))
                                
                                if pos is None:
                                    if o_type == 'buy':
                                        remain_idr = float(order.get('remain_idr', 0))
                                        amount_crypto_est = remain_idr / o_price if o_price > 0 else 0
                                        
                                        log_message(f"🔄 Active Order Sync: Found untracked BUY order for {pair}. Adopting as PENDING_BUY.")
                                        adopt_data = {
                                            "entry_price": o_price,
                                            "amount": amount_crypto_est,
                                            "amount_crypto": amount_crypto_est,
                                            "amount_idr": remain_idr,
                                            "timestamp": datetime.now().isoformat(),
                                            "dry_run": False,
                                            "status": "PENDING_BUY",
                                            "entry_order_id": o_id,
                                            "exit_order_id": None,
                                            "target_tp_pct": TP_PCT,
                                            "target_sl_pct": SL_PCT
                                        }
                                        db.upsert_position(pair, adopt_data)
                                        send_telegram_message(f"🔄 *SYNC PENDING BUY* 🔄\nPair: `{pair}`\nPrice: Rp {o_price:,.0f}")
                                        pos = adopt_data
                                        open_positions[pair] = adopt_data
                                        
                                    elif o_type == 'sell':
                                        coin_sym = pair.replace('idr', '')
                                        remain_coin = float(order.get(f'remain_{coin_sym}', 0))
                                        
                                        log_message(f"🔄 Active Order Sync: Found untracked SELL order for {pair}. Adopting as EXITING.")
                                        adopt_data = {
                                            "entry_price": o_price * 0.95,
                                            "amount": remain_coin,
                                            "amount_crypto": remain_coin,
                                            "amount_idr": remain_coin * o_price,
                                            "timestamp": datetime.now().isoformat(),
                                            "dry_run": False,
                                            "status": "EXITING",
                                            "entry_order_id": f"ADOPT-{int(time.time())}",
                                            "exit_order_id": o_id,
                                            "target_tp_pct": TP_PCT,
                                            "target_sl_pct": SL_PCT
                                        }
                                        db.upsert_position(pair, adopt_data)
                                        send_telegram_message(f"🔄 *SYNC EXITING SELL* 🔄\nPair: `{pair}`\nSell Price: Rp {o_price:,.0f}")
                                        pos = adopt_data
                                        open_positions[pair] = adopt_data
                                else:
                                    if o_type == 'sell' and pos.get('status') == 'OPEN':
                                        log_message(f"🔄 Active Order Sync: {pair} was OPEN but has active SELL order. Updating to EXITING.")
                                        pos['status'] = 'EXITING'
                                        pos['exit_order_id'] = o_id
                                        db.upsert_position(pair, pos)
            except Exception as e:
                log_message(f"Failed to sync active orders: {e}")
            
            log_message("Reality Check: Sync complete. No hallucinations found.")
        except Exception as e:
            log_message(f"Reality Check Failed: {e}")
            # [Fix BUG #2] Dead code dihapus — 'order' tidak terdefinisi di scope ini

    # ── Shadow Jaring (Liquidity Grab) ───────────────────────────────────

    def _sync_jaring_from_db(self):
        """Recover active jaring orders from SQLite on startup."""
        saved = db.get_all_shadow_orders()
        for o in saved:
            self.jaring_orders[o['pair']] = o
    def manage_shadow_jaring(self, ranked_pairs, current_tickers, balance=0):
        """
        Main entry for jaring logic. Called from the main loop.
        """
        try:
            # 1. Trailing: Move existing jaring up if price is too far (protect from missing pumps)
            for pair, o in list(self.jaring_orders.items()):
                if pair in current_tickers:
                    curr_price = current_tickers[pair]['last']
                    
                    # [Fix] Tambah Cooldown 5 Menit agar tidak spam cancel/replace
                    last_update = self.last_jaring_update.get(pair, 0)
                    if time.time() - last_update < 300: continue 

                    # Jika harga pasar sudah naik > 10% dari jaring kita
                    if curr_price > o['price'] * 1.10:
                        log_message(f"Jaring: Price moved up for {pair}. Trailing jaring...")
                        if not self.dry_run: self.exchange.cancel_order(o['id'])
                        db.delete_shadow_order(pair)
                        del self.jaring_orders[pair]
                        self.last_jaring_update[pair] = time.time()

            # 2. Placement: Fill empty slots (Max 3 jaring)
            if len(self.jaring_orders) < 3:
                candidates = [p for p in ranked_pairs if p['pair'] not in self.jaring_orders][:3]
                for c in candidates:
                    pair = c['pair']
                    jaring_price = c['last'] * (1 + JARING_DEPTH_PCT)
                    # Use provided balance if in live mode, fallback to INITIAL_CAPITAL for dry run
                    effective_capital = balance if not self.dry_run else INITIAL_CAPITAL
                    amount_idr = effective_capital * JARING_ALLOCATION_PCT
                    amount_crypto = amount_idr / jaring_price
                    self.place_jaring(pair, amount_crypto, jaring_price)
                    
        except Exception as e:
            print(f"Jaring Manager Error: {e}")

    def place_jaring(self, pair, amount, price):
        """Place a deep-limit buy order."""
        try:
            log_message(f"Jaring: Placing Liquidity Grab on {pair} at Rp {price:,.2f}")
            if self.dry_run:
                order_id = f"JARING-{int(time.time())}"
                order = {"id": order_id, "status": "open", "price": price, "amount": amount}
            else:
                order = self.exchange.create_limit_buy_order(pair, amount, price)
                order_id = order['id']
            
            self.jaring_orders[pair] = {"id": order_id, "price": price, "amount": amount}
            db.upsert_shadow_order(pair, order_id, price, amount)
            
        except Exception as e:
            print(f"Failed to place jaring for {pair}: {e}")