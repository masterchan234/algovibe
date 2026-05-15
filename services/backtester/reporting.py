from services.backtester.db_backtest import get_connection
from services.backtester.runtime import _load_active_profiles


def _print_run_report(profile_id: int, profile_name: str) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM backtest_results WHERE profile_id = %s ORDER BY created_at DESC LIMIT 1", (profile_id,))
        row = cur.fetchone()
        if not row:
            print(f"\n[REPORT] Profile: {profile_name} — No data.")
            return

        cur.execute("SELECT pair, pnl_idr, pnl_pct FROM backtest_trades WHERE result_id = %s", (row["id"],))
        trades = cur.fetchall()
        n_pairs = len(set(t["pair"] for t in trades))

        print("\n" + "╔" + "═" * 68 + "╗")
        print(f"║  QUANTITATIVE PERFORMANCE REPORT: {profile_name:<28} ║")
        print("╠" + "═" * 68 + "╣")
        print(f"║  Pairs Tested    : {n_pairs:<10} | Total Trades : {row['total_trades']:<10} ║")
        print(f"║  Win Rate        : {row['win_rate']*100:6.2f}%    | Net PnL (IDR): Rp {row['total_pnl_idr']:>12,.0f} ║")
        print(f"║  ROI             : {row['total_pnl_pct']:>+6.2f}%     | Profit Factor: {row['profit_factor']:>12.2f} ║")
        print(f"║  Max Drawdown    : Rp {row['max_drawdown_idr']:>8,.0f} | Avg Duration : {row['avg_trade_duration_sec']/60:>10.1f} min ║")
        print("╚" + "═" * 68 + "╝")
    finally:
        conn.close()


def _print_comparison_table() -> None:
    profiles = _load_active_profiles()
    if not profiles or len(profiles) < 2:
        return

    conn = get_connection()
    try:
        cur = conn.cursor()
        data = []
        for p in profiles:
            cur.execute("SELECT * FROM backtest_results WHERE profile_id = %s ORDER BY created_at DESC LIMIT 1", (p["id"],))
            row = cur.fetchone()
            if row:
                data.append(
                    {
                        "name": p["profile_name"],
                        "wr": row["win_rate"] * 100,
                        "trades": row["total_trades"],
                        "roi": row["total_pnl_pct"],
                        "pf": row["profit_factor"],
                        "mdd": row["max_drawdown_idr"],
                    }
                )
        if not data:
            return
        print("\n" + "═" * 90)
        print(" 🏁 STRATEGY COMPARISON TABLE")
        print("─" * 90)
        print(f"{'Profile Name':<25} | {'WR%':>6} | {'Trades':>6} | {'ROI%':>8} | {'PF':>6} | {'MaxDD (IDR)':>12}")
        print("─" * 90)
        for d in data:
            print(f"{d['name']:<25} | {d['wr']:6.1f} | {d['trades']:6} | {d['roi']:>+7.2f}% | {d['pf']:6.2f} | {d['mdd']:>12,.0f}")
        print("═" * 90)
    finally:
        conn.close()