import os
import sys
import psycopg2
from config import DATABASE_URL_DRYRUN

def reset_dry_run_data():
    print(f"🚀 Memulai pembersihan DRY RUN (Clean Slate) pada database PostgreSQL")

    try:
        conn = psycopg2.connect(DATABASE_URL_DRYRUN)
        conn.autocommit = False
        cursor = conn.cursor()

        # 1. Tabel dengan kolom dry_run — filter hanya data simulasi (TRUE)
        cursor.execute("DELETE FROM trade_history WHERE dry_run = TRUE")
        deleted_history = cursor.rowcount

        cursor.execute("DELETE FROM trade_analytics WHERE dry_run = TRUE")
        deleted_analytics = cursor.rowcount

        cursor.execute("DELETE FROM positions WHERE dry_run = TRUE")
        deleted_positions = cursor.rowcount

        # 2. shadow_orders — 100% milik simulasi, hapus semua
        cursor.execute("DELETE FROM shadow_orders")
        deleted_shadows = cursor.rowcount

        # 3. system_events, ticker_history & market_data — UI feed, reset total
        cursor.execute("DELETE FROM system_events")
        cursor.execute("DELETE FROM ticker_history")
        cursor.execute("DELETE FROM market_depth")
        cursor.execute("DELETE FROM order_book")

        # 4. Reset counter & state bot (berlaku untuk dry run)
        cursor.execute("""
            UPDATE bot_state SET value = '0'
            WHERE key IN ('consecutive_losses', 'daily_trades', 'consecutive_shadow_wins')
        """)
        cursor.execute("UPDATE bot_state SET value = '' WHERE key = 'trigger_exit_pair'")
        
        # Reset Session Mode to Clean Slate
        cursor.execute("UPDATE bot_state SET value = 'NORMAL' WHERE key = 'session_mode'")
        cursor.execute("UPDATE bot_state SET value = 'false' WHERE key = 'session_latched'")
        cursor.execute("UPDATE bot_state SET value = 'true' WHERE key = 'session_buy_enabled'")
        
        # Reset Profit & Net-Zero Protection States
        cursor.execute("UPDATE bot_state SET value = '0' WHERE key = 'peak_pnl_today'")
        cursor.execute("UPDATE bot_state SET value = '0' WHERE key = 'net_zero_target_floor'")
        cursor.execute("UPDATE bot_state SET value = 'false' WHERE key = 'net_zero_survival_active'")
        cursor.execute("UPDATE bot_state SET value = 'false' WHERE key = 'target_protection_active'")
        cursor.execute("UPDATE bot_state SET value = '0' WHERE key = 'net_zero_fired_at'")
        cursor.execute("UPDATE bot_state SET value = '0' WHERE key = 'net_zero_floor_locked'")
        cursor.execute("UPDATE bot_state SET value = '50000' WHERE key = 'daily_profit_target'")
        cursor.execute("UPDATE bot_state SET value = '' WHERE key = 'strategic_break_payload'")
        cursor.execute("UPDATE bot_state SET value = '0' WHERE key = 'daily_target_hit'")

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
        print("✅ Ticker history, Market depth, & System events dikosongkan.")
        print("🔓 Global Pause & Session Mode direset. Bot siap tempur!")

    except Exception as e:
        if 'conn' in locals(): conn.rollback()
        print(f"❌ Gagal melakukan reset: {e}")
    finally:
        if 'conn' in locals(): conn.close()

    print("\n✨ Sistem Algovibe Dry Run Clean Slate BERHASIL.")

def restart_bot():
    """Attempts to restart the bot services to flush in-memory state."""
    print("\n🔄 Mencoba merestart layanan bot untuk sinkronisasi memori...")
    
    # Try docker compose restart (common dev environment)
    import subprocess
    try:
        # We only restart the core and dashboard to apply the new DB state
        cmd = ["docker", "compose", "restart", "executive-core", "dashboard-api"]
        print(f"执行: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ Layanan berhasil direstart.")
            return True
    except Exception:
        pass
        
    # Try systemctl fallback (native service environment)
    try:
        cmd = ["systemctl", "--user", "restart", "indodax-scalper.service", "indodax-dashboard.service"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ Layanan systemd berhasil direstart.")
            return True
    except Exception:
        pass

    print("⚠️  Gagal merestart otomatis. Silakan jalankan 'docker compose restart' secara manual.")
    return False

if __name__ == "__main__":
    print(f"Target DB: {DATABASE_URL_DRYRUN}")
    print("⚠️  Script ini akan membersihkan SEMUA data simulasi.")
    confirm = input("⚠️ Apakah Anda yakin ingin MENGHAPUS SEMUA data dry run? (y/n): ")
    if confirm.lower() == 'y':
        reset_dry_run_data()
        restart_bot()
    else:
        print("❌ Operasi dibatalkan.")
