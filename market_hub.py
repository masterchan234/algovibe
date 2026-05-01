#!/usr/bin/env python3
"""
SaaS Market Data Hub — AlgoVibe
===============================
Sentralisasi data Indodax untuk didistribusikan ke semua bot lokal.
Mencegah banned IP dan menghemat resource server.
"""

import zmq
import json
import time
import logging
import sys
import os

# Tambahkan path ke engines agar bisa import MarketDataWS
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.infrastructure.ws_market import MarketDataWS

# Konfigurasi Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("MarketHub")

# Konfigurasi ZMQ
ZMQ_PORT = 5555

def main():
    logger.info("=" * 50)
    logger.info("   AlgoVibe SaaS Market Hub Starting...   ")
    logger.info("=" * 50)

    # 1. Setup ZMQ Publisher
    context = zmq.Context()
    pub_socket = context.socket(zmq.PUB)
    pub_socket.bind(f"tcp://0.0.0.0:{ZMQ_PORT}")
    logger.info(f"[*] ZMQ Publisher bound to port {ZMQ_PORT}")

    # 2. Setup Indodax WS Client
    mws = MarketDataWS()
    mws.start()
    
    logger.info("[*] Waiting for Indodax connection...")
    if not mws.wait_ready(timeout=60):
        logger.error("[!] Failed to connect to Indodax WS. Exiting.")
        sys.exit(1)
    
    logger.info("✅ Connected to Indodax. Starting broadcast loop.")

    try:
        last_heartbeat = 0
        while True:
            # Ambil snapshot ticker terbaru
            tickers = mws.get_tickers()
            
            if tickers:
                # Publish ke channel 'tickers'
                payload = {
                    "type": "tickers",
                    "data": tickers,
                    "ts": time.time()
                }
                pub_socket.send_string(f"tickers {json.dumps(payload)}")
            
            # Heartbeat setiap 5 detik
            if time.time() - last_heartbeat > 5:
                pub_socket.send_string(f"heartbeat {json.dumps({'status': 'alive', 'ts': time.time()})}")
                last_heartbeat = time.time()
                logger.info(f"[HUB] Heartbeat sent. Connected pairs: {len(tickers)}")

            time.sleep(1) # Broadcast rate: 1Hz (bisa dipercepat jika perlu)

    except KeyboardInterrupt:
        logger.info("[!] Stopping Market Hub...")
    finally:
        mws.stop()
        pub_socket.close()
        context.term()
        logger.info("[*] Market Hub shutdown complete.")

if __name__ == "__main__":
    main()
