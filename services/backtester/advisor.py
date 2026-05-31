from datetime import datetime, timedelta, timezone

import numpy as _np

from services.backtester.db_backtest import get_connection, transaction
from services.backtester.runtime import (
    DEFAULT_BALANCE_USDT,
    DEFAULT_DAILY_TARGET_PNL_USDT,
    DEFAULT_MIN_LIQUIDITY_24H_USDT,
    DEFAULT_MIN_PRICE_USDT,
    MIN_TRADE_USDT,
    OHLCV_DAYS,
    RSI_OVERBOUGHT_BASE,
    RSI_OVERBOUGHT_MAX,
    RSI_OVERBOUGHT_MIN,
    RSI_OVERSOLD_BASE,
    RSI_OVERSOLD_MAX,
    RSI_OVERSOLD_MIN,
    _get_latest_ohlcv_ts,
    logger,
)


def tune_profile(profile_id: int, target_daily_pnl: float | None = None) -> str:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM backtest_profiles WHERE id = %s", (profile_id,))
        p = cur.fetchone()
        if not p:
            return "missing_profile"
        cur.execute("SELECT * FROM backtest_results WHERE profile_id = %s ORDER BY created_at DESC LIMIT 1", (profile_id,))
        res = cur.fetchone()
        if not res:
            print(f"   [WARN] [NO DATA] {p['profile_name']} has no backtest_result row yet; skip tuning.")
            return "no_data"

        total_pnl = res["total_pnl_quote"]
        total_roi = res["total_pnl_pct"]
        w_start = res["window_start"]
        w_end = res["window_end"]
        days_tested = (w_end - w_start).total_seconds() / 86400.0 if w_end and w_start and w_end > w_start else 90.0

        target_roi_daily = float(p.get("target_roi_pct", 0.0) or 0.0)
        target_pnl_daily = float(p.get("target_pnl_idr", 0.0) or 0.0)
        if target_daily_pnl is not None and target_daily_pnl > 0:
            target_pnl_daily = float(target_daily_pnl)
        elif target_pnl_daily <= 0:
            target_pnl_daily = DEFAULT_DAILY_TARGET_PNL_USDT

        avg_daily_pnl = total_pnl / days_tested
        avg_daily_roi = total_roi / days_tested
        reached_pnl = target_pnl_daily > 0 and avg_daily_pnl >= target_pnl_daily
        reached_roi = target_roi_daily > 0 and avg_daily_roi >= target_roi_daily

        if reached_pnl or reached_roi:
            status = f"$ {avg_daily_pnl:,.2f}/hari" if reached_pnl else f"{avg_daily_roi:.2f}%/hari"
            print(f"   [WIN] [DAILY TARGET REACHED] {p['profile_name']} ({status})")
            return "target_reached"

        logger.info("[ADVISOR] Analyzing: Avg $ {0:,.2f}/day (Target: {1:,.2f})".format(avg_daily_pnl, target_pnl_daily))

        new_p = dict(p)
        for k in ["id", "created_at", "updated_at"]:
            new_p.pop(k, None)

        import re

        m = re.search(r"_v(\d+)$", p["profile_name"])
        v = int(m.group(1)) + 1 if m else 2
        new_p["profile_name"] = f"{p['profile_name'].split('_v')[0]}_v{v}"
        new_p["target_pnl_idr"] = target_pnl_daily

        changes = []
        param_diffs = []

        def _canonical_bool(v):
            if isinstance(v, str):
                s = v.strip().lower()
                if s in {"1", "true", "t", "yes", "y", "on"}:
                    return True
                if s in {"0", "false", "f", "no", "n", "off"}:
                    return False
            return v

        def _set_if_changed(key, value, reason):
            old_val = new_p.get(key)
            if _canonical_bool(old_val) != _canonical_bool(value):
                new_p[key] = value
                changes.append(reason)
                param_diffs.append((key, old_val, value, reason))

        target_gap = target_pnl_daily - avg_daily_pnl
        win_rate = float(res.get("win_rate", 0.0) or 0.0)
        pf = float(res.get("profit_factor", 0.0) or 0.0)
        avg_pnl_pct = float(res.get("avg_pnl_per_trade_pct", 0.0) or 0.0)
        trades_per_day = float(res.get("total_trades", 0) or 0) / days_tested

        if avg_daily_pnl <= 0 or win_rate < 0.35 or pf < 0.8:
            _set_if_changed("buy_threshold", round(min(8.5, float(p["buy_threshold"]) + 0.7), 2), "Recovery mode: stricter entry threshold")
            _set_if_changed("target_tp_pct", round(max(0.01, min(float(p["target_tp_pct"]), 0.02)), 4), "Recovery mode: lower TP to improve hit-rate")
            _set_if_changed("target_sl_pct", round(min(-0.01, float(p["target_sl_pct"]) + 0.01), 4), "Recovery mode: tighter SL to cap downside")
            _set_if_changed("max_hold_minutes", int(max(120, int(float(p.get("max_hold_minutes", 1440))) * 0.5)), "Recovery mode: shorter holding window")
            _set_if_changed("ema_trend_filter_enabled", True, "Recovery mode: enforce EMA trend filter")
            _set_if_changed("dca_enabled", False, "Recovery mode: disable DCA while expectancy negative")
            _set_if_changed("cooldown_seconds", int(min(1800, max(300, int(float(p.get("cooldown_seconds", 300))) + 120))), "Recovery mode: increase cooldown to reduce overtrading")
        elif target_gap > 0 and win_rate >= 0.5 and pf >= 1.2:
            current_cap = float(p.get("initial_capital_idr", 1000.0) or 1000.0)
            current_pos = float(p.get("max_position_quote", 120.0) or 120.0)
            cap_limit = current_cap * 0.35
            bumped_pos = round(min(cap_limit, max(current_pos + MIN_TRADE_USDT, current_pos * 1.2)), 2)
            _set_if_changed("max_position_quote", bumped_pos, "Growth mode: increase position sizing toward daily target")
            _set_if_changed("buy_threshold", round(max(4.0, float(p["buy_threshold"]) - 0.1), 2), "Growth mode: slightly loosen threshold for more valid entries")
            _set_if_changed("target_tp_pct", round(min(0.06, float(p["target_tp_pct"]) + 0.003), 4), "Growth mode: modestly increase TP to lift payoff ratio")

        if avg_pnl_pct < 0.2 and trades_per_day > 5.0:
            _set_if_changed("buy_threshold", round(min(8.5, float(new_p["buy_threshold"]) + 0.3), 2), "Overtrading guard: stricter entry quality gate")

        if avg_daily_pnl > 0 and target_gap > 0:
            if trades_per_day < 1.0:
                _set_if_changed("buy_threshold", round(max(4.0, float(new_p["buy_threshold"]) - 0.3), 2), "Undertrading guard: increase signal frequency")
            elif trades_per_day < 2.0:
                _set_if_changed("buy_threshold", round(max(4.0, float(new_p["buy_threshold"]) - 0.1), 2), "Undertrading guard: mildly increase signal frequency")

        if not changes:
            print(f"   ℹ [NO CHANGE] {p['profile_name']} produced no parameter updates this generation.")
            return "no_change"

        print(f"    [MUTATION] {p['profile_name']} -> {new_p['profile_name']}")
        for key, old_val, new_val, reason in param_diffs:
            print(f"      - {key}: {old_val} -> {new_val} ({reason})")

        cols = ", ".join(new_p.keys())
        placeholders = ", ".join(["%s"] * len(new_p))
        cur.execute(f"INSERT INTO backtest_profiles ({cols}) VALUES ({placeholders})", list(new_p.values()))
        cur.execute("UPDATE backtest_profiles SET is_active = false WHERE id = %s", (profile_id,))
        conn.commit()
        return "mutated"
    finally:
        conn.close()


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _build_coin_recommendation(
    *,
    q: float,
    p_hat: float,
    up_ratio: float,
    acf1: float,
    day_range: float,
    sigma_day: float,
    jump_1pct: float,
    edge_trade_raw: float,
    edge_trade_effective: float,
    trades_needed_full_cap: float,
    balance_usdt: float,
    max_position_usdt: float,
    max_open_positions: int,
    buy_threshold: float,
    tp_pct: float,
    sl_pct: float,
    cooldown_seconds: int,
    max_hold_minutes: int,
    atr_max_pct: float,
) -> dict:
    trend_bias = _clamp((2.0 * up_ratio) - 1.0, -1.0, 1.0)
    acf_norm = _clamp((acf1 + 0.35) / 0.70, 0.0, 1.0)
    noise_regime = _clamp(jump_1pct / 0.05, 0.0, 1.0)
    vol_regime = _clamp(day_range / 0.10, 0.0, 1.0)
    prob_conf = _clamp((p_hat - 0.5) / 0.22, -1.0, 1.0)
    edge_conf = _clamp(edge_trade_effective / 0.01, 0.0, 1.0)
    pressure = _clamp((trades_needed_full_cap - 3.0) / 12.0, 0.0, 1.0)

    buy_score_threshold = _clamp(6.1 - (0.55 * edge_conf) + (0.35 * pressure) + (0.45 * (1.0 - q)) + (0.20 * (1.0 - acf_norm)), 6.5, 7.5)  # v3.0 min 6.5
    sell_score_threshold = _clamp(5.6 + (0.40 * noise_regime) - (0.20 * prob_conf) + (0.25 * (1.0 - q)), 6.5, 7.5)  # v3.0 min 6.5
    opposing_max = _clamp(3.8 + (0.45 * noise_regime) + (0.25 * (1.0 - acf_norm)) - (0.20 * edge_conf), 5.0, 5.5)  # live bot entry gate ≥5.0
    sell_block_threshold = _clamp(4.9 + (0.45 * noise_regime) - (0.25 * trend_bias) + (0.20 * (1.0 - q)), 5.0, 6.3)  # min 5.0

    mean_reversion = _clamp((0.55 * (1.0 - q)) + (0.45 * noise_regime), 0.0, 1.0)
    trend_strength = _clamp((0.45 * q) + (0.30 * acf_norm) + (0.25 * max(0.0, trend_bias)), 0.0, 1.0)

    rsi_oversold = _clamp(RSI_OVERSOLD_BASE + (6.0 * mean_reversion) - (2.0 * trend_strength), RSI_OVERSOLD_MIN, RSI_OVERSOLD_MAX)
    rsi_overbought = _clamp(RSI_OVERBOUGHT_BASE + (2.0 * trend_strength) - (6.0 * mean_reversion), RSI_OVERBOUGHT_MIN, RSI_OVERBOUGHT_MAX)

    bb_center_shift = _clamp(0.05 * trend_bias, -0.05, 0.05)
    bb_width = _clamp(0.30 + (0.06 * noise_regime) - (0.04 * trend_strength), 0.22, 0.38)
    bb_low = _clamp(0.50 - bb_width + bb_center_shift, 0.15, 0.40)
    bb_high = _clamp(0.50 + bb_width + bb_center_shift, 0.60, 0.85)
    bb_extreme_low = _clamp(bb_low - (0.12 + (0.03 * noise_regime)), 0.05, 0.22)
    bb_extreme_high = _clamp(bb_high + (0.12 + (0.03 * noise_regime)), 0.78, 0.95)

    stoch_k_oversold = _clamp(18.0 + (7.0 * mean_reversion), 15.0, 35.0)
    stoch_d_oversold = _clamp(stoch_k_oversold + 10.0, 22.0, 45.0)
    stoch_k_overbought = _clamp(82.0 - (7.0 * mean_reversion), 65.0, 85.0)
    stoch_d_overbought = _clamp(stoch_k_overbought - 10.0, 55.0, 78.0)

    williams_oversold = _clamp(-70.0 - (10.0 * mean_reversion), -88.0, -60.0)
    williams_deep_oversold = _clamp(williams_oversold - (10.0 + (3.0 * noise_regime)), -95.0, -72.0)
    williams_overbought = _clamp(-30.0 + (10.0 * mean_reversion), -40.0, -12.0)
    williams_deep_overbought = _clamp(williams_overbought + (10.0 + (3.0 * noise_regime)), -25.0, -5.0)

    slope_base = _clamp(0.0008 + (0.06 * sigma_day), 0.0008, 0.0050)
    slope_up = slope_base
    slope_strong_up = _clamp(slope_base * 3.0, 0.0025, 0.0120)
    slope_down = -slope_base
    slope_strong_down = -slope_strong_up

    roc_abs = _clamp((sigma_day * 100.0) * (0.55 + (0.35 * noise_regime)), 1.2, 5.0)
    roc_mild = _clamp(roc_abs * 0.35, 0.8, 2.0)

    bid_shift = _clamp(4.0 * trend_strength - 3.0 * noise_regime, -3.0, 4.0)
    ob_strong_bid = _clamp(65.0 + bid_shift, 60.0, 72.0)
    ob_bid_dom = _clamp(55.0 + (bid_shift * 0.8), 50.0, 62.0)
    ob_ask_dom = _clamp(45.0 - (bid_shift * 0.8), 38.0, 50.0)
    ob_strong_ask = _clamp(35.0 - bid_shift, 28.0, 40.0)

    volume_spike = _clamp(1.7 + (0.7 * noise_regime) + (0.2 * vol_regime), 1.5, 2.8)
    volume_high = _clamp(1.25 + (0.30 * vol_regime), 1.2, 1.8)
    volume_low = _clamp(0.75 - (0.15 * noise_regime), 0.45, 0.8)
    volume_dry = _clamp(volume_low - (0.20 + (0.05 * noise_regime)), 0.25, 0.65)

    obv_strong = _clamp(3.0 + (4.0 * trend_strength) + (1.5 * edge_conf), 3.0, 9.0)
    obv_mild = _clamp(obv_strong * 0.4, 1.5, 4.0)

    return {
        "initial_capital_idr": float(balance_usdt),
        "min_position_idr": float(MIN_TRADE_USDT),
        "max_position_quote": round(max_position_usdt, 2),
        "max_open_positions": int(max_open_positions),
        "buy_threshold": round(buy_threshold, 2),
        "sell_block_threshold": round(sell_block_threshold, 3),
        "buy_score_threshold": round(buy_score_threshold, 3),
        "sell_score_threshold": round(sell_score_threshold, 3),
        "opposing_max": round(opposing_max, 3),
        "target_tp_pct": round(tp_pct, 4),
        "target_sl_pct": round(sl_pct, 4),
        "cooldown_seconds": int(cooldown_seconds),
        "max_hold_minutes": int(max_hold_minutes),
        "ema_trend_filter_enabled": bool((prob_conf >= -0.2) or (trend_strength >= 0.45)),
        "atr_max_pct": round(atr_max_pct, 3),
        "dca_enabled": bool((edge_trade_raw > 0.0) and (p_hat >= 0.56) and (noise_regime < 0.35)),
        "dca_max_tiers": int(_clamp(1.0 + (1.5 * edge_conf) - (1.0 * noise_regime), 1.0, 3.0)),
        "rsi_oversold": round(rsi_oversold, 3),
        "rsi_overbought": round(rsi_overbought, 3),
        "bb_extreme_low": round(bb_extreme_low, 3),
        "bb_low": round(bb_low, 3),
        "bb_high": round(bb_high, 3),
        "bb_extreme_high": round(bb_extreme_high, 3),
        "stoch_k_oversold": round(stoch_k_oversold, 3),
        "stoch_d_oversold": round(stoch_d_oversold, 3),
        "stoch_k_overbought": round(stoch_k_overbought, 3),
        "stoch_d_overbought": round(stoch_d_overbought, 3),
        "williams_deep_oversold": round(williams_deep_oversold, 3),
        "williams_oversold": round(williams_oversold, 3),
        "williams_overbought": round(williams_overbought, 3),
        "williams_deep_overbought": round(williams_deep_overbought, 3),
        "slope_strong_up": round(slope_strong_up, 6),
        "slope_up": round(slope_up, 6),
        "slope_down": round(slope_down, 6),
        "slope_strong_down": round(slope_strong_down, 6),
        "roc_oversold": round(-roc_abs, 3),
        "roc_mild_down": round(-roc_mild, 3),
        "roc_mild_up": round(roc_mild, 3),
        "roc_overbought": round(roc_abs, 3),
        "ob_strong_bid": round(ob_strong_bid, 3),
        "ob_bid_dom": round(ob_bid_dom, 3),
        "ob_ask_dom": round(ob_ask_dom, 3),
        "ob_strong_ask": round(ob_strong_ask, 3),
        "volume_spike": round(volume_spike, 3),
        "volume_high": round(volume_high, 3),
        "volume_low": round(volume_low, 3),
        "volume_dry": round(volume_dry, 3),
        "obv_accumulate": round(obv_strong, 3),
        "obv_rising": round(obv_mild, 3),
        "obv_falling": round(-obv_mild, 3),
        "obv_distribute": round(-obv_strong, 3),
    }


def _apply_backtest_optimized_overrides(recommendation: dict, balance_usdt: float, target_daily_pnl: float) -> dict:
    rec = dict(recommendation)
    rec["buy_threshold"] = round(_clamp(float(rec.get("buy_threshold", 6.0)) - 0.8, 4.0, 7.0), 2)
    rec["buy_score_threshold"] = round(_clamp(float(rec.get("buy_score_threshold", 6.0)) - 0.5, 5.0, 7.0), 3)
    rec["sell_score_threshold"] = round(_clamp(float(rec.get("sell_score_threshold", 6.0)) - 0.2, 4.8, 6.8), 3)
    rec["sell_block_threshold"] = round(_clamp(float(rec.get("sell_block_threshold", 5.0)) - 0.4, 4.2, 6.0), 3)
    rec["opposing_max"] = round(_clamp(float(rec.get("opposing_max", 4.0)) + 0.3, 3.5, 5.0), 3)
    rec["target_tp_pct"] = round(_clamp(float(rec.get("target_tp_pct", 0.02)), 0.010, 0.020), 4)
    rec["target_sl_pct"] = round(-_clamp(abs(float(rec.get("target_sl_pct", -0.015))), 0.008, 0.015), 4)
    rec["cooldown_seconds"] = int(_clamp(float(rec.get("cooldown_seconds", 300)) * 0.5, 90, 600))
    rec["max_hold_minutes"] = int(_clamp(float(rec.get("max_hold_minutes", 240)), 45, 240))
    rec["ema_trend_filter_enabled"] = False
    rec["atr_max_pct"] = round(_clamp(float(rec.get("atr_max_pct", 3.0)) + 0.7, 2.5, 5.0), 3)
    est_trades = max(2, min(12, int(round(target_daily_pnl / max(1.0, balance_usdt * 0.0035)))))
    pos_target = target_daily_pnl / max(1e-9, est_trades * 0.0035)
    rec["max_position_quote"] = round(_clamp(pos_target, MIN_TRADE_USDT, balance_usdt * 0.30), 2)
    rec["max_open_positions"] = 2 if rec["max_position_quote"] <= (0.2 * balance_usdt) else 1
    rec["dca_enabled"] = False
    rec["dca_max_tiers"] = 1
    return rec


def _upsert_coin_analysis(window_start: datetime, window_end: datetime, target_daily_pnl: float, balance_usdt: float, rows: list[dict]) -> None:
    if not rows:
        return

    def _py(v):
        if hasattr(v, "item"):
            return v.item()
        return v

    with transaction() as cur:
        for r in rows:
            cur.execute(
                """
                INSERT INTO backtest_coin_analysis (
                    window_start, window_end, pair, n_candles, n_days,
                    mu_1m, sigma_1m, mad_1m, up_ratio, jump_1pct_ratio, acf1,
                    avg_day_range, sigma_day, mu_day, sharpe_day_like, momentum_quality,
                    target_daily_pnl, balance_quote,
                    fee_rt, p_hat, breakeven_p, win_net, loss_net, edge_trade, trades_needed_full_cap,
                    edge_trade_raw, edge_trade_effective,
                    rec_buy_threshold, rec_sell_block_threshold, rec_target_tp_pct, rec_target_sl_pct,
                    rec_buy_score_threshold, rec_sell_score_threshold, rec_opposing_max,
                    rec_cooldown_seconds, rec_max_hold_minutes, rec_ema_trend_filter_enabled, rec_atr_max_pct,
                    rec_max_position_idr, rec_max_open_positions, rec_dca_enabled, rec_dca_max_tiers,
                    rec_rsi_oversold, rec_rsi_overbought,
                    rec_bb_extreme_low, rec_bb_low, rec_bb_high, rec_bb_extreme_high,
                    rec_stoch_k_oversold, rec_stoch_d_oversold, rec_stoch_k_overbought, rec_stoch_d_overbought,
                    rec_williams_deep_oversold, rec_williams_oversold, rec_williams_overbought, rec_williams_deep_overbought,
                    rec_slope_strong_up, rec_slope_up, rec_slope_down, rec_slope_strong_down,
                    rec_roc_oversold, rec_roc_mild_down, rec_roc_mild_up, rec_roc_overbought,
                    rec_ob_strong_bid, rec_ob_bid_dom, rec_ob_ask_dom, rec_ob_strong_ask,
                    rec_volume_spike, rec_volume_high, rec_volume_low, rec_volume_dry,
                    rec_obv_accumulate, rec_obv_rising, rec_obv_falling, rec_obv_distribute,
                    analysis_ts
                ) VALUES (
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,%s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    NOW()
                )
                ON CONFLICT (pair, window_start, window_end, target_daily_pnl, balance_quote)
                DO UPDATE SET
                    n_candles = EXCLUDED.n_candles,
                    n_days = EXCLUDED.n_days,
                    mu_1m = EXCLUDED.mu_1m,
                    sigma_1m = EXCLUDED.sigma_1m,
                    mad_1m = EXCLUDED.mad_1m,
                    up_ratio = EXCLUDED.up_ratio,
                    jump_1pct_ratio = EXCLUDED.jump_1pct_ratio,
                    acf1 = EXCLUDED.acf1,
                    avg_day_range = EXCLUDED.avg_day_range,
                    sigma_day = EXCLUDED.sigma_day,
                    mu_day = EXCLUDED.mu_day,
                    sharpe_day_like = EXCLUDED.sharpe_day_like,
                    momentum_quality = EXCLUDED.momentum_quality,
                    fee_rt = EXCLUDED.fee_rt,
                    p_hat = EXCLUDED.p_hat,
                    breakeven_p = EXCLUDED.breakeven_p,
                    win_net = EXCLUDED.win_net,
                    loss_net = EXCLUDED.loss_net,
                    edge_trade_raw = EXCLUDED.edge_trade_raw,
                    edge_trade = EXCLUDED.edge_trade,
                    edge_trade_effective = EXCLUDED.edge_trade_effective,
                    trades_needed_full_cap = EXCLUDED.trades_needed_full_cap,
                    rec_buy_threshold = EXCLUDED.rec_buy_threshold,
                    rec_sell_block_threshold = EXCLUDED.rec_sell_block_threshold,
                    rec_target_tp_pct = EXCLUDED.rec_target_tp_pct,
                    rec_target_sl_pct = EXCLUDED.rec_target_sl_pct,
                    rec_buy_score_threshold = EXCLUDED.rec_buy_score_threshold,
                    rec_sell_score_threshold = EXCLUDED.rec_sell_score_threshold,
                    rec_opposing_max = EXCLUDED.rec_opposing_max,
                    rec_cooldown_seconds = EXCLUDED.rec_cooldown_seconds,
                    rec_max_hold_minutes = EXCLUDED.rec_max_hold_minutes,
                    rec_ema_trend_filter_enabled = EXCLUDED.rec_ema_trend_filter_enabled,
                    rec_atr_max_pct = EXCLUDED.rec_atr_max_pct,
                    rec_max_position_idr = EXCLUDED.rec_max_position_idr,
                    rec_max_open_positions = EXCLUDED.rec_max_open_positions,
                    rec_dca_enabled = EXCLUDED.rec_dca_enabled,
                    rec_dca_max_tiers = EXCLUDED.rec_dca_max_tiers,
                    rec_rsi_oversold = EXCLUDED.rec_rsi_oversold,
                    rec_rsi_overbought = EXCLUDED.rec_rsi_overbought,
                    rec_bb_extreme_low = EXCLUDED.rec_bb_extreme_low,
                    rec_bb_low = EXCLUDED.rec_bb_low,
                    rec_bb_high = EXCLUDED.rec_bb_high,
                    rec_bb_extreme_high = EXCLUDED.rec_bb_extreme_high,
                    rec_stoch_k_oversold = EXCLUDED.rec_stoch_k_oversold,
                    rec_stoch_d_oversold = EXCLUDED.rec_stoch_d_oversold,
                    rec_stoch_k_overbought = EXCLUDED.rec_stoch_k_overbought,
                    rec_stoch_d_overbought = EXCLUDED.rec_stoch_d_overbought,
                    rec_williams_deep_oversold = EXCLUDED.rec_williams_deep_oversold,
                    rec_williams_oversold = EXCLUDED.rec_williams_oversold,
                    rec_williams_overbought = EXCLUDED.rec_williams_overbought,
                    rec_williams_deep_overbought = EXCLUDED.rec_williams_deep_overbought,
                    rec_slope_strong_up = EXCLUDED.rec_slope_strong_up,
                    rec_slope_up = EXCLUDED.rec_slope_up,
                    rec_slope_down = EXCLUDED.rec_slope_down,
                    rec_slope_strong_down = EXCLUDED.rec_slope_strong_down,
                    rec_roc_oversold = EXCLUDED.rec_roc_oversold,
                    rec_roc_mild_down = EXCLUDED.rec_roc_mild_down,
                    rec_roc_mild_up = EXCLUDED.rec_roc_mild_up,
                    rec_roc_overbought = EXCLUDED.rec_roc_overbought,
                    rec_ob_strong_bid = EXCLUDED.rec_ob_strong_bid,
                    rec_ob_bid_dom = EXCLUDED.rec_ob_bid_dom,
                    rec_ob_ask_dom = EXCLUDED.rec_ob_ask_dom,
                    rec_ob_strong_ask = EXCLUDED.rec_ob_strong_ask,
                    rec_volume_spike = EXCLUDED.rec_volume_spike,
                    rec_volume_high = EXCLUDED.rec_volume_high,
                    rec_volume_low = EXCLUDED.rec_volume_low,
                    rec_volume_dry = EXCLUDED.rec_volume_dry,
                    rec_obv_accumulate = EXCLUDED.rec_obv_accumulate,
                    rec_obv_rising = EXCLUDED.rec_obv_rising,
                    rec_obv_falling = EXCLUDED.rec_obv_falling,
                    rec_obv_distribute = EXCLUDED.rec_obv_distribute,
                    analysis_ts = NOW()
                """,
                (
                    window_start,
                    window_end,
                    r["pair"],
                    r["n"],
                    r["n_days"],
                    _py(r["mu_1m"]),
                    _py(r["sigma_1m"]),
                    _py(r["mad_1m"]),
                    _py(r["up_ratio"]),
                    _py(r["jump_1pct"]),
                    _py(r["acf1"]),
                    _py(r["avg_day_range"]),
                    _py(r["sigma_day"]),
                    _py(r["mu_day"]),
                    _py(r["sharpe_day_like"]),
                    _py(r["momentum_quality"]),
                    _py(target_daily_pnl),
                    _py(balance_usdt),
                    _py(r["math"]["fee_rt"]),
                    _py(r["math"]["p_hat"]),
                    _py(r["math"]["breakeven_p"]),
                    _py(r["math"]["win_net"]),
                    _py(r["math"]["loss_net"]),
                    _py(r["math"]["edge_trade"]),
                    _py(r["math"]["trades_needed_full_cap"]),
                    _py(r["math"]["edge_trade_raw"]),
                    _py(r["math"]["edge_trade_effective"]),
                    _py(r["recommendation"]["buy_threshold"]),
                    _py(r["recommendation"]["sell_block_threshold"]),
                    _py(r["recommendation"]["target_tp_pct"]),
                    _py(r["recommendation"]["target_sl_pct"]),
                    _py(r["recommendation"]["buy_score_threshold"]),
                    _py(r["recommendation"]["sell_score_threshold"]),
                    _py(r["recommendation"]["opposing_max"]),
                    _py(r["recommendation"]["cooldown_seconds"]),
                    _py(r["recommendation"]["max_hold_minutes"]),
                    _py(r["recommendation"]["ema_trend_filter_enabled"]),
                    _py(r["recommendation"]["atr_max_pct"]),
                    _py(r["recommendation"]["max_position_quote"]),
                    _py(r["recommendation"]["max_open_positions"]),
                    _py(r["recommendation"]["dca_enabled"]),
                    _py(r["recommendation"]["dca_max_tiers"]),
                    _py(r["recommendation"]["rsi_oversold"]),
                    _py(r["recommendation"]["rsi_overbought"]),
                    _py(r["recommendation"]["bb_extreme_low"]),
                    _py(r["recommendation"]["bb_low"]),
                    _py(r["recommendation"]["bb_high"]),
                    _py(r["recommendation"]["bb_extreme_high"]),
                    _py(r["recommendation"]["stoch_k_oversold"]),
                    _py(r["recommendation"]["stoch_d_oversold"]),
                    _py(r["recommendation"]["stoch_k_overbought"]),
                    _py(r["recommendation"]["stoch_d_overbought"]),
                    _py(r["recommendation"]["williams_deep_oversold"]),
                    _py(r["recommendation"]["williams_oversold"]),
                    _py(r["recommendation"]["williams_overbought"]),
                    _py(r["recommendation"]["williams_deep_overbought"]),
                    _py(r["recommendation"]["slope_strong_up"]),
                    _py(r["recommendation"]["slope_up"]),
                    _py(r["recommendation"]["slope_down"]),
                    _py(r["recommendation"]["slope_strong_down"]),
                    _py(r["recommendation"]["roc_oversold"]),
                    _py(r["recommendation"]["roc_mild_down"]),
                    _py(r["recommendation"]["roc_mild_up"]),
                    _py(r["recommendation"]["roc_overbought"]),
                    _py(r["recommendation"]["ob_strong_bid"]),
                    _py(r["recommendation"]["ob_bid_dom"]),
                    _py(r["recommendation"]["ob_ask_dom"]),
                    _py(r["recommendation"]["ob_strong_ask"]),
                    _py(r["recommendation"]["volume_spike"]),
                    _py(r["recommendation"]["volume_high"]),
                    _py(r["recommendation"]["volume_low"]),
                    _py(r["recommendation"]["volume_dry"]),
                    _py(r["recommendation"]["obv_accumulate"]),
                    _py(r["recommendation"]["obv_rising"]),
                    _py(r["recommendation"]["obv_falling"]),
                    _py(r["recommendation"]["obv_distribute"]),
                ),
            )


def _create_profile_from_analysis(
    *,
    pair: str,
    recommendation: dict,
    target_daily_pnl: float,
    profile_name: str | None = None,
    timeframe_minutes: int = 1,
    strategy_mode: str = "scalping",
    backtest_optimized: bool = False,
    activate_new_profile: bool = False,
    activate_only_new: bool = False,
) -> dict:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    effective_name = profile_name or f"quant_{pair}_{ts}"

    payload = {
        "profile_name": effective_name,
        "initial_capital_idr": float(recommendation["initial_capital_idr"]),
        "min_position_idr": float(recommendation["min_position_idr"]),
        "max_position_quote": float(recommendation["max_position_quote"]),
        "max_open_positions": int(recommendation["max_open_positions"]),
        "timeframe_minutes": int(max(1, timeframe_minutes)),
        "strategy_mode": "backtest_optimized" if backtest_optimized else str(strategy_mode),
        "buy_threshold": float(recommendation["buy_threshold"]),
        "sell_block_threshold": float(recommendation["sell_block_threshold"]),
        "target_tp_pct": float(recommendation["target_tp_pct"]),
        "target_sl_pct": float(recommendation["target_sl_pct"]),
        "cooldown_seconds": int(recommendation["cooldown_seconds"]),
        "max_hold_minutes": int(recommendation["max_hold_minutes"]),
        "ema_trend_filter_enabled": bool(recommendation["ema_trend_filter_enabled"]),
        "atr_max_pct": float(recommendation["atr_max_pct"]),
        "dca_enabled": bool(recommendation["dca_enabled"]),
        "dca_max_tiers": int(recommendation["dca_max_tiers"]),
        "target_pnl_idr": float(target_daily_pnl),
        "target_roi_pct": 0.0,
        "is_active": bool(activate_new_profile),
    }

    with transaction() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'backtest_profiles'
            """
        )
        existing_cols = {r["column_name"] for r in cur.fetchall()}
        data = {k: v for k, v in payload.items() if k in existing_cols}
        cols = list(data.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        columns_sql = ", ".join(cols)

        if activate_new_profile and activate_only_new and "is_active" in existing_cols:
            cur.execute("UPDATE backtest_profiles SET is_active = false WHERE is_active = true")

        cur.execute(
            f"INSERT INTO backtest_profiles ({columns_sql}) VALUES ({placeholders}) RETURNING id",
            [data[c] for c in cols],
        )
        new_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO backtest_profile_pair_scope (profile_id, pair)
            VALUES (%s, %s)
            ON CONFLICT (profile_id, pair) DO NOTHING
            """,
            (int(new_id), str(pair).lower()),
        )
        cur.execute("SELECT * FROM backtest_profiles WHERE id = %s", (new_id,))
        return cur.fetchone()


def _sort_auto_selection_candidates(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda x: (
            float(x.get("math", {}).get("edge_trade_raw", -1e9) or -1e9),
            float(x.get("momentum_quality", 0.0) or 0.0),
            float(x.get("avg_day_notional_window", 0.0) or 0.0),
            float(x.get("up_ratio", 0.0) or 0.0),
        ),
        reverse=True,
    )


def analyze_ohlcv_target_setup(
    target_daily_pnl: float = DEFAULT_DAILY_TARGET_PNL_USDT,
    balance_usdt: float = DEFAULT_BALANCE_USDT,
    days: int = OHLCV_DAYS,
    pairs_filter: str | None = None,
    min_liquidity_24h_usdt: float = DEFAULT_MIN_LIQUIDITY_24H_USDT,
    min_price_usdt: float = DEFAULT_MIN_PRICE_USDT,
) -> dict:
    end = _get_latest_ohlcv_ts()
    if end is None:
        print(" Tidak ada data ohlcv_1m untuk dianalisis.")
        return {}
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(days=days)
    filter_set = {p.strip().lower() for p in pairs_filter.split(",") if p.strip()} if pairs_filter else None

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            WITH px AS (
                SELECT
                    pair,
                    ts,
                    close::double precision AS close,
                    high::double precision AS high,
                    low::double precision AS low,
                    LAG(close::double precision) OVER (PARTITION BY pair ORDER BY ts) AS prev_close
                FROM ohlcv_1m
                WHERE ts >= %s
            ), ret AS (
                SELECT
                    pair,
                    ts,
                    CASE
                        WHEN prev_close IS NULL OR prev_close = 0 THEN NULL
                        ELSE (close / prev_close) - 1
                    END AS r,
                    high,
                    low,
                    close
                FROM px
            ), ret_lag AS (
                SELECT
                    pair,
                    ts,
                    r,
                    LAG(r) OVER (PARTITION BY pair ORDER BY ts) AS r_l1
                FROM ret
            ), stats AS (
                SELECT
                    pair,
                    COUNT(r) AS n,
                    AVG(r) AS mu_1m,
                    STDDEV_POP(r) AS sigma_1m,
                    AVG(ABS(r)) AS mad_1m,
                    AVG(CASE WHEN r > 0 THEN 1.0 ELSE 0.0 END) AS up_ratio,
                    AVG(CASE WHEN ABS(r) > 0.01 THEN 1.0 ELSE 0.0 END) AS jump_1pct_ratio,
                    CORR(r, r_l1) AS acf1
                FROM ret_lag
                WHERE r IS NOT NULL
                GROUP BY pair
            ), day_bar AS (
                SELECT
                    pair,
                    date_trunc('day', ts) AS d,
                    MAX(high::double precision) AS hi,
                    MIN(low::double precision) AS lo,
                    AVG(close::double precision) AS c
                FROM ohlcv_1m
                WHERE ts >= %s
                GROUP BY pair, date_trunc('day', ts)
            ), day_stats AS (
                SELECT
                    pair,
                    COUNT(*) AS n_days,
                    AVG(CASE WHEN c > 0 THEN (hi - lo) / c ELSE NULL END) AS avg_day_range
                FROM day_bar
                GROUP BY pair
            ), liq AS (
                SELECT
                    pair,
                    AVG(day_notional) AS avg_day_notional_window
                FROM (
                    SELECT
                        pair,
                        date_trunc('day', ts) AS d,
                        SUM(close::double precision * volume::double precision) AS day_notional
                    FROM ohlcv_1m
                    WHERE ts >= %s AND ts <= %s
                    GROUP BY pair, date_trunc('day', ts)
                ) q
                GROUP BY pair
            ), px_guard AS (
                SELECT
                    pair,
                    AVG(close::double precision) AS avg_close_window,
                    MAX(ts) AS last_ts
                FROM ohlcv_1m
                WHERE ts >= %s AND ts <= %s
                GROUP BY pair
            )
            SELECT
                s.pair,
                s.n,
                s.mu_1m,
                s.sigma_1m,
                s.mad_1m,
                s.up_ratio,
                s.jump_1pct_ratio,
                s.acf1,
                d.n_days,
                d.avg_day_range,
                l.avg_day_notional_window,
                g.avg_close_window
            FROM stats s
            JOIN day_stats d USING (pair)
            JOIN liq l USING (pair)
            JOIN px_guard g USING (pair)
            WHERE s.n > 10000
                AND d.n_days >= 10
                AND COALESCE(l.avg_day_notional_window, 0) >= %s
                AND (%s <= 0 OR COALESCE(g.avg_close_window, 0) >= %s)
            ORDER BY s.pair ASC
            """,
            (
                start,
                start,
                start,
                end,
                start,
                end,
                float(min_liquidity_24h_usdt),
                float(min_price_usdt),
                float(min_price_usdt),
            ),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print(" Tidak ada data ohlcv_1m pada window analisis.")
        return {}

    universe = []
    for r in rows:
        pair = str(r["pair"])
        if filter_set and pair.lower() not in filter_set:
            continue

        sigma_1m = float(r["sigma_1m"] or 0.0)
        mad_1m = float(r["mad_1m"] or 0.0)
        mu_1m = float(r["mu_1m"] or 0.0)
        up_ratio = float(r["up_ratio"] or 0.5)
        acf1 = float(r["acf1"] or 0.0)
        day_range = float(r["avg_day_range"] or 0.0)
        jump_1pct = float(r["jump_1pct_ratio"] or 0.0)

        sigma_day = sigma_1m * _np.sqrt(1440.0)
        mu_day = mu_1m * 1440.0
        sharpe_day_like = (mu_day / sigma_day) if sigma_day > 0 else 0.0

        drift_bias = (2.0 * up_ratio) - 1.0
        q_persist = _clamp((acf1 + 0.05) / 0.15, 0.0, 1.0)
        q_drift = _clamp((drift_bias + 0.10) / 0.25, 0.0, 1.0)
        q_range = _clamp(day_range / 0.08, 0.0, 1.0)
        q_noise = _clamp(jump_1pct / 0.04, 0.0, 1.0)
        momentum_quality = _clamp((0.40 * q_persist) + (0.25 * q_drift) + (0.25 * q_range) - (0.10 * q_noise), 0.0, 1.0)

        universe.append(
            {
                "pair": pair,
                "n": int(r["n"]),
                "n_days": int(r["n_days"]),
                "avg_day_notional_window": float(r["avg_day_notional_window"] or 0.0),
                "avg_close_window": float(r["avg_close_window"] or 0.0),
                "mu_1m": mu_1m,
                "sigma_1m": sigma_1m,
                "mad_1m": mad_1m,
                "up_ratio": up_ratio,
                "acf1": acf1,
                "jump_1pct": jump_1pct,
                "avg_day_range": day_range,
                "sigma_day": sigma_day,
                "mu_day": mu_day,
                "sharpe_day_like": sharpe_day_like,
                "momentum_quality": momentum_quality,
            }
        )

    if not universe:
        print(" Pair hasil filter kosong. Cek --pairs terhadap symbol di DB.")
        return {}

    ranked = sorted(universe, key=lambda x: (x["momentum_quality"], x["sharpe_day_like"], x["up_ratio"]), reverse=True)
    top_k = ranked[: min(5, len(ranked))]

    fee_buy = 0.001          # Binance Spot taker 0.1%
    fee_sell = 0.001         # Binance Spot taker 0.1%
    slippage_in = 0.0005     # config.py FEE_SLIPPAGE_BUFFER 0.05%
    slippage_out = 0.0005    # 0.05% market impact
    fee_rt = fee_buy + fee_sell + slippage_in + slippage_out  # 0.3% round-trip
    per_coin = []
    for c in ranked:
        q = float(c["momentum_quality"])
        day_range = float(c["avg_day_range"])
        sigma_day = float(c["sigma_day"])
        up_ratio = float(c["up_ratio"])
        acf1 = float(c["acf1"])

        tp_pct = _clamp(max(3.0 * fee_rt, 0.35 * day_range), 0.018, 0.06)
        sl_abs = _clamp(max(2.0 * fee_rt, 0.45 * tp_pct), 0.010, 0.030)
        sl_pct = -sl_abs
        p_hat = _clamp(0.5 + (0.30 * acf1) + (0.20 * ((2.0 * up_ratio) - 1.0)) + (0.10 * (q - 0.5)), 0.35, 0.72)
        win_net = tp_pct - fee_rt
        loss_net = sl_abs + fee_rt
        breakeven_p = loss_net / (win_net + loss_net) if (win_net + loss_net) > 0 else 1.0
        edge_trade_raw = (p_hat * win_net) - ((1.0 - p_hat) * loss_net)
        edge_trade = edge_trade_raw if edge_trade_raw > 0 else 0.0035
        edge_trade_effective = edge_trade
        trades_needed_full_cap = max(0.0, target_daily_pnl / max(1.0, balance_usdt * edge_trade))
        buy_threshold = _clamp(7.5 - (1.0 * q), 6.5, 7.5)  # v3.0: best coin→6.5, worst→7.5
        cooldown_seconds = int(_clamp(900 - (600 * q), 240, 900))
        atr_max_pct = _clamp(day_range * 100.0 * 0.45, 2.0, 4.5)
        max_hold_minutes = 10  # live bot fixed: MAX_HOLD_MINUTES=10 (config.py)
        target_trades_day = int(_clamp(_np.ceil(trades_needed_full_cap), 1, 12))
        needed_position = target_daily_pnl / max(1e-9, target_trades_day * edge_trade)
        max_position_usdt = float(_clamp(needed_position, MIN_TRADE_USDT, balance_usdt))
        max_open_positions = int(1 if max_position_usdt >= (0.6 * balance_usdt) else 2)

        recommendation = _build_coin_recommendation(
            q=q,
            p_hat=p_hat,
            up_ratio=up_ratio,
            acf1=acf1,
            day_range=day_range,
            sigma_day=sigma_day,
            jump_1pct=jump_1pct,
            edge_trade_raw=edge_trade_raw,
            edge_trade_effective=edge_trade_effective,
            trades_needed_full_cap=trades_needed_full_cap,
            balance_usdt=balance_usdt,
            max_position_usdt=max_position_usdt,
            max_open_positions=max_open_positions,
            buy_threshold=buy_threshold,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            cooldown_seconds=cooldown_seconds,
            max_hold_minutes=max_hold_minutes,
            atr_max_pct=atr_max_pct,
        )

        c["recommendation"] = recommendation
        c["math"] = {
            "fee_rt": fee_rt,
            "p_hat": p_hat,
            "breakeven_p": breakeven_p,
            "win_net": win_net,
            "loss_net": loss_net,
            "edge_trade_raw": edge_trade_raw,
            "edge_trade": edge_trade,
            "edge_trade_effective": edge_trade_effective,
            "trades_needed_full_cap": trades_needed_full_cap,
        }
        per_coin.append(c)

    top_k = per_coin[: min(5, len(per_coin))]
    best = top_k[0]
    recommendation = best["recommendation"]

    print("\n[ANALYSIS] OHLCV_1M QUANT ANALYSIS (DATA-DRIVEN)")
    print(
        f"Window: {days} hari | Pair dianalisis: {len(universe)} | "
        f"Target: $ {target_daily_pnl:,.2f}/hari | Balance: $ {balance_usdt:,.2f} | "
        f"Min Avg Daily Notional: $ {float(min_liquidity_24h_usdt):,.2f}"
    )
    if float(min_price_usdt) > 0:
        print(f"Min Price filter aktif: $ {float(min_price_usdt):,.4f}")
    print("\nTop momentum-quality pairs:")
    for i, p in enumerate(top_k, start=1):
        print(
            f" {i}. {p['pair']:<12} MQ={p['momentum_quality']:.3f} "
            f"acf1={p['acf1']:+.4f} up={p['up_ratio']*100:5.2f}% "
            f"sigma_day={p['sigma_day']*100:5.2f}% day_range={p['avg_day_range']*100:5.2f}%"
        )

    print("\nMath summary (best pair):")
    print(f" pair={best['pair']} | fee_rt={best['math']['fee_rt']*100:.3f}% | p_hat={best['math']['p_hat']*100:.2f}% | breakeven_p={best['math']['breakeven_p']*100:.2f}%")
    print(f" win_net={best['math']['win_net']*100:.3f}% | loss_net={best['math']['loss_net']*100:.3f}% | edge_trade={best['math']['edge_trade']*100:.3f}%")
    print(f" edge_raw={best['math']['edge_trade_raw']*100:.3f}% | edge_effective={best['math']['edge_trade_effective']*100:.3f}%")
    print(f" trades_needed_full_cap≈{best['math']['trades_needed_full_cap']:.2f}/hari")

    if best["math"]["edge_trade_raw"] <= 0:
        print(" ℹ edge_effective memakai fallback floor 0.35% karena edge_raw <= 0.")

    print("\nRecommended backtest_profiles setup:")
    for k, v in recommendation.items():
        print(f" - {k}: {v}")

    if best["math"]["trades_needed_full_cap"] > 6:
        print("\n[WARN] Feasibility warning: target 5000/hari kemungkinan sangat agresif untuk balance ini pada edge historis saat ini.")

    _upsert_coin_analysis(window_start=start, window_end=end, target_daily_pnl=float(target_daily_pnl), balance_usdt=float(balance_usdt), rows=per_coin)
    print(f"\n Saved {len(per_coin)} row(s) to backtest_coin_analysis.")
    return {"top_pairs": top_k, "recommendation": recommendation, "math": best["math"], "per_coin": per_coin}