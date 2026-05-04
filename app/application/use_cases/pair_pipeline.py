"""
pair_pipeline.py

Candidate-pair pipeline orchestrator:
  1. build_high_speed_candidates — selects and prioritises pairs to evaluate
  2. process_high_speed_candidates — drives each pair through the enrichment →
     gates → targeting → dispatch sub-use-cases
"""

import time

from config import (
    MAX_TRACKED_PAIRS,
    MAX_TRADES_PER_DAY,
    MIN_TRACKING_VOLATILITY,
)
import config
from app.infrastructure.database import db
from utils.logger import log_message, send_telegram_message

from .candidate_enrichment import enrich_pair
from .entry_dispatch import dispatch_entry
from .entry_gates import check_entry_gates
from .entry_targeting import calculate_entry_target

_BREAK = "BREAK"


def build_high_speed_candidates(bot, ranked, tickers):
    open_positions = bot.exit_manager.get_open_positions()
    watch_pair = db.get_state('current_watch_pair', 'btcidr')

    priority_pairs = list(open_positions.keys())
    if watch_pair not in priority_pairs:
        priority_pairs.append(watch_pair)

    high_speed_tickers = []
    seen_pairs = set()

    for pair in priority_pairs:
        ticker = next((p for p in ranked if p['pair'] == pair), None)
        if ticker:
            high_speed_tickers.append(ticker)
            seen_pairs.add(pair)

    for p in ranked:
        if len(high_speed_tickers) >= MAX_TRACKED_PAIRS:
            break

        relative_momentum = p['score'] / max(0.1, p['volatility'])

        if p['pair'] not in seen_pairs:
            if relative_momentum > 0.2 or p['volatility'] >= MIN_TRACKING_VOLATILITY:
                high_speed_tickers.append(p)
                seen_pairs.add(p['pair'])

    high_speed = high_speed_tickers

    btc_data = tickers.get('btcidr')
    if btc_data:
        btc_last = float(btc_data['last'])
    else:
        btc_history = db.get_ticker_history("BTC/IDR", limit=1)
        btc_last = btc_history[0]['last'] if btc_history else 0
        if btc_last == 0:
            log_message("⚠️ Data BTC tidak tersedia di WS/DB. Melewati filter regime.")
            btc_last = 999_999_999

    btc_history = db.get_ticker_history("BTC/IDR", limit=20)
    if btc_history and len(btc_history) >= 20 and not config.DRY_RUN_LOOSE_GATES:
        btc_prices = [h['last'] for h in btc_history]
        btc_sma = sum(btc_prices) / 20
        if btc_last < btc_sma:
            log_message(f"⚠️ [BTC Filter] BTC sedang bearish ({btc_last} < {btc_sma:.0f}). Bot stand-by.")
            time.sleep(30)
            return None

    open_positions = db.get_open_positions()
    held_pairs = list(open_positions.keys()) if open_positions else []

    current_track_pairs = [p['pair'] for p in high_speed]
    for hp in held_pairs:
        if hp not in current_track_pairs:
            try:
                tickers_snapshot = bot.market_ws.get_tickers()
                ticker_data = tickers_snapshot.get(hp)
                if ticker_data:
                    last_price = float(ticker_data.get('last', 0))
                    high = float(ticker_data.get('high', last_price))
                    low = float(ticker_data.get('low', last_price))
                    volatility = ((high - low) / last_price) * 100 if last_price > 0 else 0.0

                    p_dict = {
                        "pair": hp,
                        "last": last_price,
                        "volatility": volatility,
                        "score": 0.0,
                        "imbalance": 1.0,
                        "change": float(ticker_data.get('change_24h', 0)),
                        "vol_idr": float(ticker_data.get('vol_idr', 0)),
                    }
                    high_speed.append(p_dict)
            except Exception as e:
                log_message(f"⚠️ Warning: Gagal sinkronisasi tracking untuk {hp}: {e}")

    return high_speed


def process_high_speed_candidates(bot, high_speed, tickers, regime, adaptive_cfg):
    open_positions = db.get_open_positions()

    # ── [DAILY TARGET & LOSS LIMIT GATE] ──────────────────────────────────────
    # Cek status progress target profit harian sebelum membuka posisi baru.
    target_status = bot.oracle.get_daily_target_status()
    target_idr = target_status["target_idr"]
    current_pnl = target_status["current_pnl"]
    progress_pct = target_status["progress_pct"]

    # Counter posisi baru yang berhasil dibuka dalam siklus recovery ini
    recovery_new_positions = 0

    if target_status["loss_limit_hit"]:
        # [RECOVERY MODE] Jangan buta total — tetap scan market dengan filter ultra-ketat.
        # Kondisi wajib: AI confidence ≥80%, LLM verdict BUY ≥75%, max 1 posisi baru,
        # ukuran posisi diperkecil 50%.
        adaptive_cfg["tech_threshold"] = 0.80
        adaptive_cfg["recovery_mode"] = True
        # Simpan faktor sizing 50% ke bot agar trade_execution bisa membacanya
        bot._recovery_size_factor = 0.5
        log_message(
            f"⚡ [RECOVERY MODE] Loss harian Rp {current_pnl:,.0f} melampaui batas. "
            f"Threshold ultra-ketat (80%), max 1 posisi baru, ukuran -50%. "
            f"Tetap scan peluang — tidak buta terhadap market."
        )

    if target_status["reached"]:
        log_message(
            f"🎯 [TARGET HIT] Target profit harian Rp {target_idr:,.0f} sudah tercapai "
            f"(PnL hari ini: Rp {current_pnl:,.0f}). "
            f"Bot stand-by untuk menjaga cuan — hanya memantau posisi terbuka."
        )
        return

    # Jika sudah 80%+ menuju target, naikkan threshold entry secara signifikan
    if target_status["near"]:
        original_threshold = adaptive_cfg.get("tech_threshold", 0.35)
        adaptive_cfg["tech_threshold"] = min(0.85, original_threshold + 0.15)
        log_message(
            f"⚠️ [NEAR TARGET] Progress {progress_pct:.0f}% (Rp {current_pnl:,.0f} / Rp {target_idr:,.0f}). "
            f"Threshold entry dinaikkan {original_threshold*100:.0f}% → {adaptive_cfg['tech_threshold']*100:.0f}% untuk menjaga cuan."
        )
    # ── END GATE ──────────────────────────────────────────────────────────────

    for p in high_speed:
        pair = p.get('pair', 'UNKNOWN')
        try:
            enriched = enrich_pair(bot, p, tickers)

            gate = check_entry_gates(bot, p, enriched, regime, adaptive_cfg, open_positions)
            if gate is None:
                continue
            if gate == _BREAK:
                break

            # [RECOVERY MODE] Gate tambahan: hanya entri dengan confidence teknikal sangat tinggi (≥75%),
            # dan maksimal 1 posisi baru selama sesi recovery ini.
            if adaptive_cfg.get("recovery_mode"):
                total_conf = float(gate.get("total_confidence", 0))
                if total_conf < 75.0:
                    log_message(
                        f"🔒 [RECOVERY GATE] {pair} ditolak — butuh confidence teknikal ≥75%, "
                        f"dapat: {total_conf:.0f}%"
                    )
                    continue
                # Batasi ke 1 posisi baru dalam siklus recovery ini
                if recovery_new_positions >= 1:
                    log_message(
                        f"🔒 [RECOVERY GATE] {pair} ditolak — sudah ada posisi baru di sesi recovery."
                    )
                    continue

            target = calculate_entry_target(bot, p, enriched, gate, adaptive_cfg)

            # [Senior Fix] Shadow Recovery Path
            if isinstance(gate, dict) and gate.get('mode') == "SHADOW":
                bot.execute_shadow_trade(pair, p['last'], gate, target)
                continue

            if bot.daily_trades >= MAX_TRADES_PER_DAY:
                continue

            if dispatch_entry(bot, p, enriched, gate, target):
                bot.daily_trades += 1
                db.save_state("daily_trades", bot.daily_trades)
                if adaptive_cfg.get("recovery_mode"):
                    recovery_new_positions += 1

        except Exception as e:
            log_message(f"⚠️ [Pair Loop Error] {pair}: {e}")
            continue

    # Bersihkan recovery flag agar tidak bocor ke siklus berikutnya
    if hasattr(bot, '_recovery_size_factor'):
        del bot._recovery_size_factor
    adaptive_cfg.pop("recovery_mode", None)
