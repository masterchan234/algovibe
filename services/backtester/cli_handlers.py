import os
from datetime import datetime, timezone

from services.backtester.advisor import (
    _apply_backtest_optimized_overrides,
    _create_profile_from_analysis,
    _sort_auto_selection_candidates,
    analyze_ohlcv_target_setup,
    tune_profile,
)
from services.backtester.db_backtest import transaction
from services.backtester.pipeline import _persist_results, run_backtest
from services.backtester.reporting import _print_comparison_table, _print_run_report
from services.backtester.runtime import DEFAULT_DAILY_TARGET_PNL_USDT, _load_active_profiles


def handle_restore_command() -> None:
    cache_file = "/app/data/backtest_cache.pkl"
    import pickle

    if not os.path.exists(cache_file):
        print(f" Error: No cache file found at {cache_file}")
        return

    print(f" Restoring results from {cache_file}...")
    with open(cache_file, "rb") as f:
        data = pickle.load(f)

    profiles = _load_active_profiles()
    _persist_results(data["results"], profiles, data["start"], data["now"])
    print(" Restore complete.")


def handle_analyze_command(args) -> None:
    analysis_result = analyze_ohlcv_target_setup(
        target_daily_pnl=float(args.target_daily_pnl or DEFAULT_DAILY_TARGET_PNL_USDT),
        balance_usdt=float(args.balance),
        days=int(args.days),
        pairs_filter=args.pairs,
        min_liquidity_24h_usdt=float(args.min_liquidity_24h_usdt),
        min_price_usdt=float(args.min_price_usdt),
    )

    if not args.create_profile_from_analysis:
        return
    if not analysis_result or not analysis_result.get("per_coin"):
        print(" Tidak bisa membuat profile: hasil analisis kosong.")
        return

    target_daily_pnl = float(args.target_daily_pnl or DEFAULT_DAILY_TARGET_PNL_USDT)
    if int(args.auto_select_top) > 0:
        ranked_candidates = _sort_auto_selection_candidates(list(analysis_result["per_coin"]))
        selected_rows = ranked_candidates[: int(args.auto_select_top)]
        if not selected_rows:
            print(" Auto-selection kosong setelah ranking kandidat.")
            return

        auto_batch_tag = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        if args.activate_new_profile and args.activate_only_new:
            with transaction() as cur:
                cur.execute("UPDATE backtest_profiles SET is_active = false WHERE is_active = true")

        created_profiles = []
        created_pairs = []
        skipped_negative = []
        print(
            f"\n[AI] Auto-select aktif: membuat {len(selected_rows)} profile dari universe "
            f"{len(analysis_result['per_coin'])} pair hasil filter."
        )

        for idx, selected in enumerate(selected_rows, start=1):
            edge_raw_selected = float(selected.get("math", {}).get("edge_trade_raw", 0.0) or 0.0)
            if edge_raw_selected <= 0 and not args.allow_negative_edge_profile:
                skipped_negative.append(str(selected["pair"]))
                continue

            recommendation_for_profile = dict(selected["recommendation"])
            if args.backtest_optimized:
                recommendation_for_profile = _apply_backtest_optimized_overrides(
                    recommendation_for_profile,
                    balance_usdt=float(args.balance),
                    target_daily_pnl=target_daily_pnl,
                )

            auto_name = args.new_profile_name or f"bt_auto_{int(args.days)}d_{auto_batch_tag}_{idx:02d}_{str(selected['pair']).lower()}"
            new_profile = _create_profile_from_analysis(
                pair=str(selected["pair"]),
                recommendation=recommendation_for_profile,
                target_daily_pnl=target_daily_pnl,
                profile_name=auto_name,
                timeframe_minutes=int(args.profile_timeframe_minutes),
                strategy_mode=str(args.profile_strategy_mode),
                backtest_optimized=bool(args.backtest_optimized),
                activate_new_profile=bool(args.activate_new_profile),
                activate_only_new=False,
            )
            created_profiles.append(new_profile)
            created_pairs.append(str(selected["pair"]).lower())
            print(
                f"[OK] [{len(created_profiles)}/{len(selected_rows)}] id={new_profile['id']} "
                f"name={new_profile['profile_name']} pair_ref={selected['pair']} "
                f"edge_raw={edge_raw_selected*100:.3f}%"
            )

        if skipped_negative:
            preview = ", ".join(skipped_negative[:10])
            if len(skipped_negative) > 10:
                preview += f", ... (+{len(skipped_negative)-10} lainnya)"
            print(f"ℹ Profile dilewati karena edge_raw <= 0 tanpa flag --allow-negative-edge-profile: {preview}")

        if not created_profiles:
            print(" Tidak ada profile berhasil dibuat dari auto-selection.")
            return

        if args.run_after_create:
            pairs_for_run = ",".join(sorted(set(created_pairs)))
            print(f"\n[BACKTEST] Running {len(created_profiles)} profiles auto-select ({args.days} days)...")
            run_backtest(
                days=int(args.days),
                pairs_filter=pairs_for_run if pairs_for_run else None,
                profiles_override=created_profiles,
                strict_per_coin=(not args.allow_global_fallback),
                min_liquidity_24h_usdt=float(args.min_liquidity_24h_usdt),
                min_price_usdt=float(args.min_price_usdt),
            )
            for profile in created_profiles:
                _print_run_report(profile["id"], profile["profile_name"])
            _print_comparison_table()
        return

    selected_pair = args.analysis_pair or analysis_result["top_pairs"][0]["pair"]
    selected = next((row for row in analysis_result["per_coin"] if str(row["pair"]).lower() == str(selected_pair).lower()), None)
    if not selected:
        print(f" Pair {selected_pair} tidak ditemukan di hasil analisis.")
        return

    edge_raw_selected = float(selected.get("math", {}).get("edge_trade_raw", 0.0) or 0.0)
    if edge_raw_selected <= 0 and not args.allow_negative_edge_profile:
        print(" Profile creation dibatalkan: edge_trade_raw <= 0 (negative expectancy).")
        print(
            "   Gunakan pair lain / ubah horizon analisis / ubah asumsi fee-slippage, "
            "atau pakai --allow-negative-edge-profile jika tetap ingin memaksa."
        )
        return

    recommendation_for_profile = dict(selected["recommendation"])
    if args.backtest_optimized:
        recommendation_for_profile = _apply_backtest_optimized_overrides(
            recommendation_for_profile,
            balance_usdt=float(args.balance),
            target_daily_pnl=target_daily_pnl,
        )

    new_profile = _create_profile_from_analysis(
        pair=str(selected["pair"]),
        recommendation=recommendation_for_profile,
        target_daily_pnl=target_daily_pnl,
        profile_name=args.new_profile_name,
        timeframe_minutes=int(args.profile_timeframe_minutes),
        strategy_mode=str(args.profile_strategy_mode),
        backtest_optimized=bool(args.backtest_optimized),
        activate_new_profile=bool(args.activate_new_profile),
        activate_only_new=bool(args.activate_only_new),
    )
    print(f"\nCreated profile baru: id={new_profile['id']} name={new_profile['profile_name']} pair_ref={selected['pair']}")

    if args.run_after_create:
        print(f"\n[BACKTEST] Running new profile for {selected['pair']} ({args.days} days)...")
        run_backtest(
            days=int(args.days),
            pairs_filter=str(selected["pair"]),
            profiles_override=[new_profile],
            strict_per_coin=(not args.allow_global_fallback),
            min_liquidity_24h_usdt=float(args.min_liquidity_24h_usdt),
            min_price_usdt=float(args.min_price_usdt),
        )
        _print_run_report(new_profile["id"], new_profile["profile_name"])


def handle_optimize_command(args) -> None:
    for g in range(1, args.generations + 1):
        print(f"\n--- GENERATION {g} ---")
        run_backtest(
            args.days,
            args.pairs,
            strict_per_coin=(not args.allow_global_fallback),
            min_liquidity_24h_usdt=float(args.min_liquidity_24h_usdt),
            min_price_usdt=float(args.min_price_usdt),
        )
        active = _load_active_profiles()
        for p in active:
            _print_run_report(p["id"], p["profile_name"])
        _print_comparison_table()

        statuses = [tune_profile(p["id"], args.target_daily_pnl) for p in active]
        if any(s == "mutated" for s in statuses):
            continue
        if any(s == "target_reached" for s in statuses):
            print("Stopping early: Daily target reached.")
            break
        if any(s == "no_data" for s in statuses):
            print("Stopping early: No backtest result rows were generated; check pair filter/window/data availability.")
            break
        if any(s == "no_change" for s in statuses):
            print("Stopping early: No further parameter changes proposed (local optimum).")
            break