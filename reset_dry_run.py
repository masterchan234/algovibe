import os
import sys
import sqlite3

# Import DB path dari infrastruktur bot agar selalu sinkron
try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from app.infrastructure.database import db
    DB_PATH = db.db_path
except ImportError:
    DB_PATH = 'data/algotrade.db'

def reset_dry_run_data():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database tidak ditemukan di: {DB_PATH}")
        return

    print(f"🚀 Memulai pembersihan DRY RUN (Clean Slate) pada: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Tabel dengan kolom dry_run — filter hanya data simulasi
        cursor.execute("DELETE FROM trade_history WHERE dry_run = 1")
        deleted_history = cursor.rowcount

        cursor.execute("DELETE FROM trade_analytics WHERE dry_run = 1")
        deleted_analytics = cursor.rowcount

        cursor.execute("DELETE FROM positions WHERE dry_run = 1")
        deleted_positions = cursor.rowcount

        # 2. shadow_orders — 100% milik simulasi, hapus semua
        cursor.execute("DELETE FROM shadow_orders")
        deleted_shadows = cursor.rowcount

        # 3. system_events & ticker_history — UI feed, reset total
        cursor.execute("DELETE FROM system_events")
        cursor.execute("DELETE FROM ticker_history")

        # 4. Reset counter & state bot (berlaku untuk dry run)
        cursor.execute("""
            UPDATE bot_state SET value = '0'
            WHERE key IN ('consecutive_losses', 'daily_trades', 'consecutive_shadow_wins')
        """)
        cursor.execute("UPDATE bot_state SET value = '' WHERE key = 'trigger_exit_pair'")

        # 5. Bersihkan cooldown, shadow meta, AI cache, dan state radar/veto
        deleted_states = 0
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'pair_cooldown_until_%'")
        deleted_states += cursor.rowcount
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'shadow_meta_%'")
        deleted_states += cursor.rowcount
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'llm_reasoning_%'")
        deleted_states += cursor.rowcount
        cursor.execute("DELETE FROM bot_state WHERE key = 'llm_equity_report'")
        deleted_states += cursor.rowcount

        # Radar execution visibility state (live + audit)
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'radar_reco_%'")
        deleted_states += cursor.rowcount
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'radar_reco_reason_%'")
        deleted_states += cursor.rowcount
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'gate_skip_%'")
        deleted_states += cursor.rowcount
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'gate_ts_%'")
        deleted_states += cursor.rowcount
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'llm_veto_%'")
        deleted_states += cursor.rowcount
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'llm_veto_ts_%'")
        deleted_states += cursor.rowcount

        conn.commit()

        print(f"✅ {deleted_history} riwayat transaksi dry run dibersihkan.")
        print(f"✅ {deleted_analytics} trade analytics dry run dibersihkan.")
        print(f"✅ {deleted_positions} posisi dry run dibersihkan.")
        print(f"✅ {deleted_shadows} shadow orders dibersihkan.")
        print(f"✅ {deleted_states} state & AI cache dibersihkan.")
        print("✅ Ticker history & System events dikosongkan.")
        print("🔓 Global Pause direset. Bot siap tempur!")

    except Exception as e:
        conn.rollback()
        print(f"❌ Gagal melakukan reset: {e}")
    finally:
        conn.close()

    # VACUUM harus di luar transaksi (koneksi terpisah)
    try:
        vconn = sqlite3.connect(DB_PATH)
        vconn.execute("VACUUM")
        vconn.close()
        print("✅ Database dioptimalkan (VACUUM).")
    except Exception as e:
        print(f"⚠️ VACUUM gagal (tidak kritikal): {e}")

    print("\n✨ Sistem Algovibe Dry Run Clean Slate BERHASIL.")

if __name__ == "__main__":
    print(f"Target DB: {os.path.abspath(DB_PATH)}")
    print("⚠️  Pastikan service bot sudah DISTOP sebelum reset:")
    print("    systemctl --user stop indodax-scalper.service indodax-dashboard.service")
    confirm = input("⚠️ Apakah Anda yakin ingin MENGHAPUS SEMUA data dry run? (y/n): ")
    if confirm.lower() == 'y':
        reset_dry_run_data()
    else:
        print("❌ Operasi dibatalkan.")
