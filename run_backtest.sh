#!/bin/bash
# ══════════════════════════════════════════════════════════════
# ALGOVIBE BACKTEST RUNNER & DNA SEEDER
# ══════════════════════════════════════════════════════════════
# Skrip ini menjalankan simulasi teknikal mendalam menggunakan
# data historis Indodax untuk melatih sistem k-NN (Project Evolution).

# Warna untuk output terminal
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}    ALGOVIBE BACKTESTER & DNA SEEDING ENGINE      ${NC}"
echo -e "${BLUE}==================================================${NC}"

# 1. Validasi Lingkungan
if [ ! -d "venv" ]; then
    echo -e "${RED}[ERROR] Virtual environment (venv) tidak ditemukan!${NC}"
    echo "Pastikan Anda berada di direktori proyek dan venv sudah terinstall."
    exit 1
fi

# 2. Persiapan Database
echo -e "${YELLOW}[1/3] Menyiapkan environment...${NC}"
export PYTHONPATH=$PYTHONPATH:.

# 3. Eksekusi Backtest
echo -e "${YELLOW}[2/3] Menjalankan simulasi (60 hari terakhir)...${NC}"
echo -e "${YELLOW}      Strategi: Mean Reversion (EMA-20 Bounce)${NC}"
echo -e "${YELLOW}      Filter: BTC-Trend Correlation Enabled${NC}"
echo ""

# Jalankan skrip python backtester
./venv/bin/python3 backtester.py

# 4. Verifikasi Hasil
if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}==================================================${NC}"
    echo -e "${GREEN}✅ BACKTEST SELESAI DENGAN SUKSES${NC}"
    echo -e "${GREEN}   Pola DNA telah disuntikkan ke dalam Database.${NC}"
    echo -e "${GREEN}   Bot (Oracle) kini memiliki referensi historis baru.${NC}"
    echo -e "${GREEN}==================================================${NC}"
    
    # Rekap singkat dari DB menggunakan Python
    echo -e "${BLUE}Rekap Data DNA Baru:${NC}"
    ./venv/bin/python3 -c "import sqlite3; conn=sqlite3.connect('data/algotrade.db'); r=conn.execute('SELECT count(*) FROM trade_analytics WHERE status=\'CLOSED\'').fetchone(); print(f'Total {r[0]} pola DNA telah tersimpan dalam database.')"
else
    echo -e "${RED}[ERROR] Backtest gagal di tengah jalan. Cek pesan error di atas.${NC}"
    exit 1
fi
