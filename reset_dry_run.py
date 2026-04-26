import sqlite3
import os

# Konfigurasi Path
DB_PATH = 'data/algotrade.db'

def reset_dry_run_data():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database tidak ditemukan di: {DB_PATH}")
        return

    print("🚀 Memulai pembersihan data DRY RUN...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Hapus riwayat perdagangan simulasi
        cursor.execute("DELETE FROM trade_history WHERE dry_run = 1")
        deleted_history = cursor.rowcount
        
        # 2. Hapus posisi terbuka simulasi
        cursor.execute("DELETE FROM positions WHERE dry_run = 1")
        deleted_positions = cursor.rowcount
        
        # 3. Opsional: Bersihkan analitik yang berkaitan dengan transaksi lama
        # cursor.execute("DELETE FROM trade_analytics")
        
        conn.commit()
        
        print(f"✅ Berhasil menghapus {deleted_history} riwayat transaksi (Dry Run).")
        print(f"✅ Berhasil menghapus {deleted_positions} posisi terbuka (Dry Run).")
        print("\n✨ Dashboard Anda sekarang seharusnya kembali ke Rp 0 dan 0.0% Win Rate.")
        print("💡 Jangan lupa merestart layanan dashboard untuk melihat perubahan.")

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
