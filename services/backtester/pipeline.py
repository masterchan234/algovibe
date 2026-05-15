import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from services.backtester.core import (
    _backfill_profile_pair_scope_for_quant_profiles,
    _compute_stats,
    _load_latest_coin_setup,
    _load_profile_pair_scopes,
    _process_pair_worker,
    _save_result,
    _save_result_pair_setups,
    _simulate_portfolio,
)
from services.backtester.db_backtest import get_connection
from services.backtester.runtime import (
    DEFAULT_MIN_LIQUIDITY_24H_IDR,
    DEFAULT_MIN_PRICE_IDR,
    OHLCV_DAYS,
    _get_latest_ohlcv_ts,
    _load_active_profiles,
    _progress,
    logger,
)


@dataclass
class BacktestRunContext:
    profiles: list[dict]
    pairs: list[str]
    start: datetime
    end: datetime


def _resolve_backtest_window(days: int) -> tuple[datetime, datetime] | None:
    data_end = _get_latest_ohlcv_ts()
    if data_end is None:
        return None
    if data_end.tzinfo is None:
        data_end = data_end.replace(tzinfo=timezone.utc)
    data_start = data_end - timedelta(days=days)
    return data_start, data_end


def _load_pairs_from_ohlcv_window(start: datetime, end: datetime, pairs_filter: str | None) -> list[str]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT pair
            FROM ohlcv_1m
            WHERE ts >= %s AND ts <= %s
            ORDER BY pair ASC
            """,
            (start, end),
        )
        pairs = [r["pair"] for r in cur.fetchall()]
    finally:
        conn.close()

    if pairs_filter:
        filter_set = {px.strip().lower() for px in pairs_filter.split(",") if px.strip()}
        pairs = [px for px in pairs if str(px).lower() in filter_set]
    return pairs


def _validate_pair_setup_coverage(pairs: list[str], pair_setup_map: dict[str, dict], strict_per_coin: bool) -> bool:
    missing_pairs = sorted([px for px in pairs if str(px).lower() not in pair_setup_map])
    if not missing_pairs:
        return True

    preview = ", ".join(missing_pairs[:10])
    if len(missing_pairs) > 10:
        preview += f", ... (+{len(missing_pairs)-10} lainnya)"

    if strict_per_coin:
        print("❌ Strict per-coin aktif: setup teknikal per-koin belum lengkap.")
        print(f"   Missing pair setup: {preview}")
        print("   Jalankan `analyze` dulu agar backtest_coin_analysis terisi untuk semua pair tersebut.")
        return False

    print("⚠️ Per-coin setup belum lengkap, menjalankan fallback ke profile global untuk pair yang missing.")
    print(f"   Missing pair setup: {preview}")
    return True


def _enrich_profiles_for_run(profiles: list[dict], pair_setup_map: dict[str, dict]) -> list[dict]:
    _backfill_profile_pair_scope_for_quant_profiles(profiles)
    scope_map = _load_profile_pair_scopes([int(p["id"]) for p in profiles if p.get("id") is not None])

    enriched_profiles = []
    for p in profiles:
        p2 = dict(p)
        pid = int(p2["id"])
        p2["pair_setup_map"] = pair_setup_map
        p2["allowed_pairs"] = sorted(scope_map.get(pid, set()))
        enriched_profiles.append(p2)
    return enriched_profiles


def _execute_backtest_workers(context: BacktestRunContext) -> list[dict]:
    n_pairs = len(context.pairs)
    all_results = []

    import multiprocessing

    max_w = min(4, multiprocessing.cpu_count())
    with ProcessPoolExecutor(max_workers=max_w) as pool:
        future_to_pair = {
            pool.submit(_process_pair_worker, px, context.start, context.end, context.profiles): px
            for px in context.pairs
        }
        for i, f in enumerate(as_completed(future_to_pair)):
            pair = future_to_pair[f]
            try:
                res = f.result()
                if res:
                    all_results.extend(res)
            except Exception as e:
                logger.error(f"[run] Worker failed for {pair}: {e}")
            _progress(i + 1, n_pairs)
    return all_results


def _cache_backtest_results(all_results: list[dict], days: int, start: datetime, end: datetime) -> None:
    cache_file = "/app/data/backtest_cache.pkl"
    try:
        import pickle

        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "wb") as f:
            pickle.dump({"results": all_results, "days": days, "start": start, "now": end}, f)
        logger.info(f"[run] 💾 Results cached to {cache_file}")
    except Exception as e:
        logger.warning(f"[run] ⚠️  Failed to cache results: {e}")


def run_backtest(
    days=OHLCV_DAYS,
    pairs_filter=None,
    profiles_override: list[dict] | None = None,
    strict_per_coin: bool = True,
    min_liquidity_24h_idr: float = DEFAULT_MIN_LIQUIDITY_24H_IDR,
    min_price_idr: float = DEFAULT_MIN_PRICE_IDR,
):
    profiles = profiles_override if profiles_override is not None else _load_active_profiles()
    if not profiles:
        logger.warning("[run] No profile available to run.")
        return

    window = _resolve_backtest_window(int(days))
    if window is None:
        logger.warning("[run] No ohlcv_1m data available.")
        return
    data_start, data_end = window

    pairs = _load_pairs_from_ohlcv_window(data_start, data_end, pairs_filter)
    if not pairs:
        logger.warning("[run] No pairs selected in requested time window.")
        return

    if float(min_liquidity_24h_idr) > 0 or float(min_price_idr) > 0:
        print("ℹ️ Catatan: filter likuiditas/harga diabaikan pada mode strict per-koin ini.")

    pair_setup_map = _load_latest_coin_setup(pairs)
    logger.info(f"[run] Loaded per-coin setup for {len(pair_setup_map)} pair(s) from backtest_coin_analysis.")
    print(f"ℹ️ Loaded per-coin technical setup for {len(pair_setup_map)} pair(s) from backtest_coin_analysis.")

    if not _validate_pair_setup_coverage(pairs, pair_setup_map, strict_per_coin):
        return

    context = BacktestRunContext(
        profiles=_enrich_profiles_for_run(profiles, pair_setup_map),
        pairs=pairs,
        start=data_start,
        end=data_end,
    )
    all_results = _execute_backtest_workers(context)
    _cache_backtest_results(all_results, int(days), context.start, context.end)
    _persist_results(all_results, context.profiles, context.start, context.end)


def _persist_results(all_results, profiles, start, now):
    logger.info("[db] 📥 Persisting results to database...")
    for p in profiles:
        p_trades = [t for t in all_results if t["profile_id"] == p["id"]]
        if p_trades:
            portfolio = _simulate_portfolio(p_trades, p)
            stats = _compute_stats(portfolio, p["initial_capital_idr"], start, now)
            if stats:
                rid = _save_result(p["id"], None, stats, portfolio)
                _save_result_pair_setups(
                    result_id=rid,
                    profile_id=int(p["id"]),
                    trades=portfolio,
                    pair_setup_map=p.get("pair_setup_map"),
                )
                print(f"   ✅ Saved: {p['profile_name']} ({len(portfolio)} trades)")