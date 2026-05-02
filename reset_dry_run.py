import sqlite3
import os

# Konfigurasi Path
DB_PATH = 'data/algotrade.db'

def reset_dry_run_data():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database tidak ditemukan di: {DB_PATH}")
        return

    print("🚀 Memulai pembersihan total data DRY RUN & System State...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Hapus riwayat perdagangan & analitik (Semua Simulasi & Backtest)
        cursor.execute("DELETE FROM trade_history WHERE dry_run = 1 OR dry_run IS NULL")
        deleted_history = cursor.rowcount
        
        cursor.execute("DELETE FROM trade_analytics")
        
        # 2. Hapus posisi terbuka simulasi & Shadow Orders
        cursor.execute("DELETE FROM positions WHERE dry_run = 1 OR dry_run IS NULL")
        deleted_positions = cursor.rowcount
        
        cursor.execute("DELETE FROM shadow_orders")
        deleted_shadows = cursor.rowcount
        
        # 3. Reset Global Pause & Daily Stats
        cursor.execute("UPDATE bot_state SET value = '0' WHERE key IN ('consecutive_losses', 'daily_trades')")
        cursor.execute("UPDATE bot_state SET value = '' WHERE key = 'trigger_exit_pair'")
        
        # 4. Bersihkan Cooldowns, Metadata Shadow, & LLM Cache
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'pair_cooldown_until_%'")
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'shadow_meta_%'")
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'llm_reasoning_%'")
        cursor.execute("DELETE FROM bot_state WHERE key = 'llm_equity_report'")
        deleted_states = cursor.rowcount

        # 5. Bersihkan Activity Feed & Ticker History (Agar Grafik Bersih)
        cursor.execute("DELETE FROM system_events")
        cursor.execute("DELETE FROM ticker_history")
        
        conn.commit()
        
        print(f"✅ {deleted_history} riwayat transaksi dibersihkan.")
        print(f"✅ {deleted_positions} posisi terbuka dibersihkan.")
        print(f"✅ {deleted_shadows} shadow orders dibersihkan.")
        print(f"✅ {deleted_states} state & AI cache dibersihkan.")
        print("✅ Ticker history & System events dikosongkan.")
        print("🔓 Global Pause direset. Bot siap tempur!")
        print("\n✨ Sistem Algovibe Clean Slate (Total Wipe).")

    except Exception as e:
        conn.rollback()
        print(f"❌ Gagal melakukan reset: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    confirm = input("⚠️ Apakah Anda yakin ingin MENGHAPUS SEMUA data simulasi? (y/n): ")
    if confirm.lower() == 'y':
        reset_dry_run_data()
    else:
        print("❌ Operasi dibatalkan.")
