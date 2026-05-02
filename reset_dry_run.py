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

    print(f"🚀 Memulai pembersihan TOTAL (Clean Slate) pada: {DB_PATH}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Hapus riwayat perdagangan & analitik (Total Wipe untuk Simulasi)
        # Kita gunakan filter yang lebih luas untuk memastikan tidak ada data hantu tertinggal
        cursor.execute("DELETE FROM trade_history")
        deleted_history = cursor.rowcount
        
        cursor.execute("DELETE FROM trade_analytics")
        cursor.execute("DELETE FROM trade_dna")
        
        # 2. Hapus semua posisi terbuka & Shadow Orders
        cursor.execute("DELETE FROM positions")
        deleted_positions = cursor.rowcount
        
        cursor.execute("DELETE FROM shadow_orders")
        deleted_shadows = cursor.rowcount
        
        # 3. Reset Global Pause & Bot States
        cursor.execute("UPDATE bot_state SET value = '0' WHERE key IN ('consecutive_losses', 'daily_trades', 'consecutive_shadow_wins')")
        cursor.execute("UPDATE bot_state SET value = '' WHERE key = 'trigger_exit_pair'")
        
        # 4. Bersihkan Cooldowns, Metadata Shadow, & AI Cache
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'pair_cooldown_until_%'")
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'shadow_meta_%'")
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'llm_reasoning_%'")
        cursor.execute("DELETE FROM bot_state WHERE key = 'llm_equity_report'")
        deleted_states = cursor.rowcount
        
        # 5. Bersihkan Activity Feed & Ticker History (Agar Grafik & UI Bersih)
        cursor.execute("DELETE FROM system_events")
        cursor.execute("DELETE FROM ticker_history")
        
        # 6. Optimize Database
        cursor.execute("VACUUM")
        
        conn.commit()
        
        print(f"✅ {deleted_history} riwayat transaksi dibersihkan.")
        print(f"✅ {deleted_positions} posisi terbuka dibersihkan.")
        print(f"✅ {deleted_shadows} shadow orders dibersihkan.")
        print(f"✅ {deleted_states} state & AI cache dibersihkan.")
        print("✅ Ticker history & System events dikosongkan.")
        print("✅ Database dioptimalkan (VACUUM).")
        print("🔓 Global Pause direset. Bot siap tempur!")
        print("\n✨ Sistem Algovibe Clean Slate (Total Wipe) BERHASIL.")

    except Exception as e:
        conn.rollback()
        print(f"❌ Gagal melakukan reset: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    print(f"Target DB: {os.path.abspath(DB_PATH)}")
    confirm = input("⚠️ Apakah Anda yakin ingin MENGHAPUS SEMUA data perdagangan di database ini? (y/n): ")
    if confirm.lower() == 'y':
        reset_dry_run_data()
    else:
        print("❌ Operasi dibatalkan.")
