#!/usr/bin/env python3
import sqlite3
import time
import subprocess
import os
import logging
from datetime import datetime

# Konfigurasi
CENTRAL_DB = "data/saas_central.db"
HUB_SCRIPT = "market_hub.py"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SaaS-Manager] %(message)s'
)
logger = logging.getLogger("SaaSManager")

def check_subscriptions():
    """Cek expiry date dan matikan bot yang sudah lewat waktu."""
    conn = sqlite3.connect(CENTRAL_DB)
    cursor = conn.cursor()
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Ambil tenant yang ACTIVE tapi sudah expired
    cursor.execute(
        "SELECT username FROM tenants WHERE status = 'ACTIVE' AND expiry_date < ?", 
        (now,)
    )
    expired_users = cursor.fetchall()
    
    for (username,) in expired_users:
        logger.warning(f"⚠️ Subscription EXPIRED for user: {username}. Stopping container...")
        
        # Stop Docker
        subprocess.run(f"docker stop algovibe_{username}", shell=True, capture_output=True)
        
        # Update Status
        cursor.execute(
            "UPDATE tenants SET status = 'EXPIRED' WHERE username = ?",
            (username,)
        )
        conn.commit()
        logger.info(f"✅ User {username} has been deactivated.")

    conn.close()

def manage_market_hub():
    """Pastikan Market Hub selalu menyala."""
    # Cek apakah proses market_hub.py sedang jalan
    result = subprocess.run("pgrep -f market_hub.py", shell=True, capture_output=True, text=True)
    
    if not result.stdout.strip():
        logger.warning("🚨 Market Hub is DOWN! Restarting...")
        # Jalankan di background
        subprocess.Popen(["python3", HUB_SCRIPT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("✅ Market Hub triggered.")

def main():
    logger.info("=" * 50)
    logger.info("   AlgoVibe SaaS Manager Watchdog Started   ")
    logger.info("=" * 50)

    while True:
        try:
            # 1. Kelola Market Hub
            manage_market_hub()
            
            # 2. Kelola Subscription
            check_subscriptions()
            
            # 3. Tunggu 60 detik sebelum pengecekan berikutnya
            time.sleep(60)
            
        except KeyboardInterrupt:
            logger.info("Stopping SaaS Manager...")
            break
        except Exception as e:
            logger.error(f"Error in manager loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
