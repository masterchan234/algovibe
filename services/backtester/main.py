import argparse

from services.backtester.advisor import tune_profile
from services.backtester.cli_handlers import (
    handle_analyze_command,
    handle_optimize_command,
    handle_restore_command,
)
from services.backtester.core import _get_signal_history, _process_pair_worker, _run_backtest_pair
from services.backtester.pipeline import run_backtest
from services.backtester.reporting import _print_comparison_table, _print_run_report
from services.backtester.runtime import (
    DEFAULT_MIN_LIQUIDITY_24H_IDR,
    DEFAULT_MIN_PRICE_IDR,
    OHLCV_DAYS,
    _ensure_container_runtime,
    _ensure_schema_upgraded,
    _load_active_profiles,
)


def _build_container_command(args) -> str:
    cmd_parts = [args.command]
    if args.days != OHLCV_DAYS:
        cmd_parts.extend(["--days", str(args.days)])
    if args.pairs:
        cmd_parts.extend(["--pairs", args.pairs])
    if args.generations != 3:
        cmd_parts.extend(["--generations", str(args.generations)])
    if args.target_daily_pnl is not None:
        cmd_parts.extend(["--target-daily-pnl", str(args.target_daily_pnl)])
    if args.balance != 100000.0:
        cmd_parts.extend(["--balance", str(args.balance)])
    if args.create_profile_from_analysis:
        cmd_parts.append("--create-profile-from-analysis")
    if args.analysis_pair:
        cmd_parts.extend(["--analysis-pair", args.analysis_pair])
    if args.new_profile_name:
        cmd_parts.extend(["--new-profile-name", args.new_profile_name])
    if args.run_after_create:
        cmd_parts.append("--run-after-create")
    if args.activate_new_profile:
        cmd_parts.append("--activate-new-profile")
    if args.activate_only_new:
        cmd_parts.append("--activate-only-new")
    if args.profile_timeframe_minutes != 1:
        cmd_parts.extend(["--profile-timeframe-minutes", str(args.profile_timeframe_minutes)])
    if args.profile_strategy_mode != "scalping":
        cmd_parts.extend(["--profile-strategy-mode", args.profile_strategy_mode])
    if args.strict_per_coin:
        cmd_parts.append("--strict-per-coin")
    if args.allow_global_fallback:
        cmd_parts.append("--allow-global-fallback")
    if args.allow_negative_edge_profile:
        cmd_parts.append("--allow-negative-edge-profile")
    if args.backtest_optimized:
        cmd_parts.append("--backtest-optimized")
    if float(args.min_liquidity_24h_idr) != DEFAULT_MIN_LIQUIDITY_24H_IDR:
        cmd_parts.extend(["--min-liquidity-24h-idr", str(args.min_liquidity_24h_idr)])
    if float(args.min_price_idr) != DEFAULT_MIN_PRICE_IDR:
        cmd_parts.extend(["--min-price-idr", str(args.min_price_idr)])
    if int(args.auto_select_top) > 0:
        cmd_parts.extend(["--auto-select-top", str(int(args.auto_select_top))])
    return " ".join(cmd_parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["fetch", "run", "report", "tune", "optimize", "restore", "analyze", "sync-coin-analysis"])
    parser.add_argument("--days", type=int, default=OHLCV_DAYS)
    parser.add_argument("--pairs", type=str)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--target-daily-pnl", type=float, default=None)
    parser.add_argument("--balance", type=float, default=100000.0)
    parser.add_argument("--create-profile-from-analysis", action="store_true")
    parser.add_argument("--analysis-pair", type=str, default=None)
    parser.add_argument("--new-profile-name", type=str, default=None)
    parser.add_argument("--run-after-create", action="store_true")
    parser.add_argument("--activate-new-profile", action="store_true")
    parser.add_argument("--activate-only-new", action="store_true")
    parser.add_argument("--profile-timeframe-minutes", type=int, default=1)
    parser.add_argument("--profile-strategy-mode", type=str, default="scalping")
    parser.add_argument("--strict-per-coin", action="store_true")
    parser.add_argument("--allow-global-fallback", action="store_true")
    parser.add_argument("--allow-negative-edge-profile", action="store_true")
    parser.add_argument("--backtest-optimized", action="store_true")
    parser.add_argument("--min-liquidity-24h-idr", type=float, default=DEFAULT_MIN_LIQUIDITY_24H_IDR)
    parser.add_argument("--min-price-idr", type=float, default=DEFAULT_MIN_PRICE_IDR)
    parser.add_argument("--auto-select-top", type=int, default=0)
    args = parser.parse_args()

    _ensure_container_runtime(_build_container_command(args))
    _ensure_schema_upgraded()

    if args.command == "fetch":
        print("ℹ️ No standalone fetch step is required. Backtest reads from table ohlcv_1m.")
    elif args.command == "run":
        run_backtest(
            args.days,
            args.pairs,
            strict_per_coin=(not args.allow_global_fallback),
            min_liquidity_24h_idr=float(args.min_liquidity_24h_idr),
            min_price_idr=float(args.min_price_idr),
        )
    elif args.command == "report":
        for p in _load_active_profiles():
            _print_run_report(p["id"], p["profile_name"])
        _print_comparison_table()
    elif args.command == "tune":
        for p in _load_active_profiles():
            tune_profile(p["id"], args.target_daily_pnl)
    elif args.command == "restore":
        handle_restore_command()
    elif args.command == "sync-coin-analysis":
        print("🔄 Menyinkronkan setup teknikal per-koin dari backtest_coin_analysis ke coin_analysis trading...")
        from app.infrastructure.database import db as trading_db

        sync_result = trading_db.sync_backtest_coin_analysis_to_trading(
            pairs=args.pairs.split(",") if args.pairs else None,
        )
        if sync_result.get("ok"):
            print(
                "✅ Sync selesai: "
                f"fetched={sync_result.get('fetched', 0)} | "
                f"live={sync_result.get('live_written', 0)} | "
                f"dryrun={sync_result.get('dryrun_written', 0)}"
            )
        else:
            print(f"❌ Sync gagal: {sync_result.get('error', 'unknown error')}")
    elif args.command == "analyze":
        handle_analyze_command(args)
    elif args.command == "optimize":
        handle_optimize_command(args)


if __name__ == "__main__":
    main()