import sys
import os

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.infrastructure.database import db

def run_report():
    print("\n" + "=" * 65)
    print(" 🚀 ALGOVIBE AUTOMATED QUANTITATIVE PARAMETER ADVISOR v2.0")
    print("=" * 65)
    
    with db.get_connection() as conn:
        # Check if table has data
        count_res = conn.execute("SELECT COUNT(*) FROM backtest_history;").fetchone()
        total_records = count_res[0] if count_res else 0
        
        if total_records == 0:
            print("\n❌ Tidak ada data transaksi di backtest_history.")
            print("Silakan jalankan simulasi backtest terlebih dahulu: ./run_backtest.sh")
            print("=" * 65 + "\n")
            return
            
        # 1. Overall Performance Metrics
        metrics = conn.execute("""
            SELECT 
                COUNT(*) as total_trades,
                COALESCE(SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END), 0) as wins,
                COALESCE(SUM(CASE WHEN pnl_idr <= 0 THEN 1 ELSE 0 END), 0) as losses,
                COALESCE(SUM(pnl_idr), 0) as total_pnl_idr,
                COALESCE(AVG(pnl_pct), 0) as avg_pnl_pct,
                COALESCE(AVG(CASE WHEN pnl_idr > 0 THEN pnl_pct ELSE NULL END), 0) as avg_win_pct,
                COALESCE(AVG(CASE WHEN pnl_idr <= 0 THEN pnl_pct ELSE NULL END), 0) as avg_loss_pct,
                COALESCE(SUM(CASE WHEN pnl_idr > 0 THEN pnl_idr ELSE 0 END), 0) as gross_profit,
                COALESCE(SUM(CASE WHEN pnl_idr <= 0 THEN ABS(pnl_idr) ELSE 0 END), 0) as gross_loss
            FROM backtest_history;
        """).fetchone()
        
        total_trades = metrics['total_trades']
        wins = metrics['wins']
        losses = metrics['losses']
        total_pnl = metrics['total_pnl_idr']
        avg_pnl_pct = metrics['avg_pnl_pct']
        avg_win_pct = metrics['avg_win_pct']
        avg_loss_pct = metrics['avg_loss_pct']
        gross_profit = float(metrics['gross_profit'])
        gross_loss = float(metrics['gross_loss'])
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)
        
        print("\n📈 OVERALL PERFORMANCE PORTFOLIO:")
        print(f"   • Total Trades Simulated : {total_trades}")
        print(f"   • Win / Loss Ratio       : {wins} Win / {losses} Loss")
        print(f"   • Win Rate               : {win_rate:.2f}%")
        print(f"   • Total Net Profit/Loss  : Rp {total_pnl:,.2f}")
        print(f"   • Average Return/Trade   : {avg_pnl_pct:+.2f}%")
        print(f"   • Avg Win / Avg Loss     : {avg_win_pct:.2f}% / {avg_loss_pct:.2f}%")
        print(f"   • Profit Factor          : {profit_factor:.2f}x")
        
        # 2. Top 5 Performing Pairs
        print("\n🏆 TOP 5 PERFORMING ASSETS (HIGH YIELD):")
        top_pairs = conn.execute("""
            SELECT 
                pair,
                COUNT(*) as trades,
                SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins,
                SUM(pnl_idr) as net_pnl
            FROM backtest_history
            GROUP BY pair
            ORDER BY net_pnl DESC
            LIMIT 5;
        """).fetchall()
        
        for i, row in enumerate(top_pairs, 1):
            p_wr = (row['wins'] / row['trades'] * 100) if row['trades'] > 0 else 0
            print(f"   {i}. {row['pair']:12} | Trades: {row['trades']:3} | WR: {p_wr:5.1f}% | Net PnL: Rp {row['net_pnl']:+,.2f}")
            
        # 3. Bottom 5 Performing Pairs
        print("\n⚠️ BOTTOM 5 PERFORMING ASSETS (HEMORRHAGING COINS):")
        bottom_pairs = conn.execute("""
            SELECT 
                pair,
                COUNT(*) as trades,
                SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins,
                SUM(pnl_idr) as net_pnl
            FROM backtest_history
            GROUP BY pair
            ORDER BY net_pnl ASC
            LIMIT 5;
        """).fetchall()
        
        for i, row in enumerate(bottom_pairs, 1):
            p_wr = (row['wins'] / row['trades'] * 100) if row['trades'] > 0 else 0
            print(f"   {i}. {row['pair']:12} | Trades: {row['trades']:3} | WR: {p_wr:5.1f}% | Net PnL: Rp {row['net_pnl']:+,.2f}")
            
        # 4. Automated Tuning & Risk Optimization Recommendations
        print("\n⚙️ AUTOMATED QUANTITATIVE TUNING ADVICE:")
        
        # Recommendation 1: Blacklist candidates
        bleeders = [b['pair'] for b in bottom_pairs if b['net_pnl'] < -1000]
        if bleeders:
            print(f"   [1] ASSET BLACKLIST:")
            print(f"       • Rekomendasi: Masukkan {', '.join(bleeders[:4])} ke 'DEFAULT_BLACKLIST_COINS' di config.py.")
            print(f"       • Alasan: Koin ini memiliki korelasi negatif yang tinggi dengan sinyal confluence bot.")
        else:
            print("   [1] ASSET SELECTION: Kinerja koin terdistribusi dengan aman.")
            
        # Recommendation 2: Risk-to-Reward Tuning
        print("   [2] RISK-TO-REWARD OPTIMIZATION:")
        if abs(avg_loss_pct) > 0:
            rr_ratio = abs(avg_win_pct) / abs(avg_loss_pct)
            if rr_ratio < 1.1 and win_rate < 55:
                print(f"       • Rekomendasi: Naikkan TP ATR multiplier ke 3.0x atau ketatkan SL ke 1.2x ATR di backtester.py.")
                print(f"       • Alasan: Rasio keuntungan berbanding kerugian ({rr_ratio:.2f}x) terlalu rendah untuk Win Rate {win_rate:.1f}%.")
            elif rr_ratio >= 1.5:
                print(f"       • Rekomendasi: Pertahankan target ATR keluar saat ini.")
                print(f"       • Alasan: Rasio asimetris keuntungan berbanding kerugian ({rr_ratio:.2f}x) sangat prima.")
            else:
                print(f"       • Rekomendasi: Rasio keekonomian trade ({rr_ratio:.2f}x) sudah cukup seimbang.")
        
        # Recommendation 3: Confluence Thresholds Tuning
        print("   [3] CONFLUENCE ENGINE SENSITIVITY:")
        if win_rate < 40 and total_trades > 50:
            print(f"       • Rekomendasi: Naikkan BUY_THRESHOLD dari 6.0 ke 7.0 atau turunkan OPPOSING_MAX ke 3.0 di app/domain/technicals.py.")
            print(f"       • Alasan: Win Rate rendah ({win_rate:.1f}%) menandakan bot terlalu sensitif memasuki sinyal palsu (noise).")
        elif win_rate >= 60 and total_trades < 10:
            print(f"       • Rekomendasi: Turunkan sedikit BUY_THRESHOLD dari 6.0 ke 5.5 agar tidak kelewatan peluang (missed opportunities).")
            print(f"       • Alasan: Win Rate tinggi ({win_rate:.1f}%) dengan jumlah transaksi sangat sedikit menunjukkan seleksi terlalu over-strict.")
        else:
            print(f"       • Rekomendasi: Nilai Confluence Threshold saat ini (Win Rate {win_rate:.1f}%) sudah berada di zona manis (Sweet Spot).")

    print("=" * 65 + "\n")

if __name__ == "__main__":
    run_report()
