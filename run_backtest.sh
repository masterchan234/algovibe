#!/bin/bash
# ══════════════════════════════════════════════════════════════
# ALGOVIBE BACKTEST RUNNER (Docker Version)
# ══════════════════════════════════════════════════════════════

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}    ALGOVIBE BACKTESTER (DOCKER)                  ${NC}"
echo -e "${BLUE}==================================================${NC}"

# 1. Validasi Container
if ! docker ps | grep -q "algovibe-executive-core"; then
    echo -e "${RED}[ERROR] Container algovibe-executive-core tidak jalan!${NC}"
    echo "Pastikan stack sudah jalan: docker compose up -d"
    exit 1
fi

# 2. Eksekusi Backtest di dalam Container
echo -e "${YELLOW}[1/2] Menjalankan simulasi di dalam container...${NC}"
docker exec -it algovibe-executive-core python3 backtester.py

# 3. Verifikasi Hasil & Tampilkan Laporan Tuning Otomatis
if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}==================================================${NC}"
    echo -e "${GREEN}✅ BACKTEST SELESAI DENGAN SUKSES${NC}"
    echo -e "${GREEN}   Data telah disuntikkan ke TimescaleDB (Dry Run).${NC}"
    echo -e "${GREEN}==================================================${NC}"
    
    # Jalankan analisa penalaan parameter otomatis (Tuning Advisor)
    docker exec -it algovibe-executive-core python3 backtest_report.py
else
    echo -e "${RED}[ERROR] Backtest gagal. Cek log di atas.${NC}"
    exit 1
fi
