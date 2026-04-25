# Algovibe 🌊🤖

**Algovibe** adalah bot trading kripto otomatis (Scalping) untuk platform **Indodax** yang diciptakan sepenuhnya melalui proses **"Vibe Coding"**—sebuah metode pengembangan di mana developer berkolaborasi secara *end-to-end* dengan AI Agent untuk merancang, merangkai, dan men-*deploy* kode secara natural melalui instruksi bahasa manusia.

Bot ini dibangun menggunakan arsitektur *multiprocessing* Python untuk memastikan kecepatan dan efisiensi saat memproses ratusan *trading pair* secara bersamaan untuk mencari momentum ("vibe") market yang paling tepat.

## 🎯 Fitur Utama

- **Autonomous Background Execution:** Dirancang untuk berjalan stabil 24/7 menggunakan *Systemd Service* (`indodax-scalper.service`).
- **Parallel Ranking Engine:** Melakukan *scoring* pada semua market Indodax secara paralel berdasarkan *Volatility*, *Volume*, *Spread*, dan *Orderbook Imbalance*.
- **SQLite Persistence (High Concurrency):** Menggunakan SQLite dengan mode **WAL (Write-Ahead Logging)** untuk menyimpan histori harga, posisi terbuka, dan log transaksi dengan performa tinggi tanpa perlu RAM Disk.
- **AI-Powered Analysis:** Terintegrasi dengan **LiteRT-LM (Gemma-4-E2B)** untuk melakukan analisis sentimen dan narasi teknikal secara otomatis di background.
- **Indodax Tax Compliant (PMK 68/2022):** Perhitungan PnL yang sudah disesuaikan dengan aturan pajak Indonesia terbaru (PPh 0.1%, PPN 0.11%, dan Maker Fee 0.1%).
- **Interactive Dashboard:** Dashboard berbasis Flask & SocketIO untuk monitoring realtime, visualisasi radar market, dan eksekusi manual.
- **Dry Run Mode:** Mendukung mode simulasi untuk menguji strategi tanpa menggunakan uang asli.

## ⚙️ Arsitektur Mesin (*Engines*)

Proyek ini menggunakan pola arsitektur *modular engines*:
- `database.py`: Layer persistensi SQLite untuk semua data stateful.
- `ws_market.py` & `ws_private.py`: Handler WebSocket untuk data pasar dan update order pribadi.
- `oracle.py`: Mesin kecerdasan sentral yang mendeteksi *Market Regime* (Bullish/Bearish) dan mengatur bobot strategi secara dinamis.
- `llm_worker.py`: Integrasi model LiteRT-LM untuk reasoning kualitatif.
- `exit_manager.py`: Memantau TP/SL dan melakukan eksekusi *SELL* otomatis.
- `predictor.py`: Logika prediksi harga berbasis data historis.
- `technicals.py`: Indikator teknikal (RSI, MA, dll) untuk konfirmasi sinyal.

## 📋 Persyaratan Sistem

- OS Linux (Ubuntu/Debian disarankan)
- Python 3.12+
- RAM: Minimal 4GB (disarankan 8GB untuk menjalankan LLM Worker dengan lancar)
- Disk Space: ~3GB (untuk Model LLM)

## 🚀 Instalasi & Konfigurasi (Native OS)

1. **Clone & Setup Venv:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Setup Environment:**
   Buat file `.env` di root direktori:
   ```env
   INDODAX_API_KEY="your_api_key_here"
   INDODAX_API_SECRET="your_secret_key_here"
   TELEGRAM_BOT_TOKEN="optional"
   TELEGRAM_CHAT_ID="optional"
   ```

3. **Konfigurasi Strategi:**
   Sesuaikan parameter manajemen risiko (TP/SL, Max Trades, dll) di file `config.py`.

## 🛠 Menjalankan Bot (sebagai Service)

Gunakan **Systemd User Service** agar bot berjalan otomatis tanpa perlu akses root.

### 1. Membuat File Service
Buat direktori jika belum ada: `mkdir -p ~/.config/systemd/user/`

**Bot Utama (`~/.config/systemd/user/indodax-scalper.service`):**
```ini
[Unit]
Description=Indodax Scalper Autonomous Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/chan/projects/algovibe
ExecStart=/home/chan/projects/algovibe/venv/bin/python /home/chan/projects/algovibe/main.py
Restart=always
RestartSec=10
Environment="PYTHONUNBUFFERED=1"
StandardOutput=append:/home/chan/projects/algovibe/data/logs/scalper.log
StandardError=append:/home/chan/projects/algovibe/data/logs/scalper.log

[Install]
WantedBy=default.target
```

**Dashboard (`~/.config/systemd/user/indodax-dashboard.service`):**
```ini
[Unit]
Description=AlgoVibe Dashboard Server
After=network.target indodax-scalper.service
BindsTo=indodax-scalper.service

[Service]
Type=simple
WorkingDirectory=/home/chan/projects/algovibe
ExecStart=/home/chan/projects/algovibe/venv/bin/python /home/chan/projects/algovibe/dashboard/server.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/home/chan/projects/algovibe/data/logs/dashboard.log
StandardError=append:/home/chan/projects/algovibe/data/logs/dashboard.log

[Install]
WantedBy=default.target
```

### 2. Mengaktifkan Service
```bash
systemctl --user daemon-reload
systemctl --user enable indodax-scalper.service indodax-dashboard.service
systemctl --user start indodax-scalper.service indodax-dashboard.service
```

## 📊 Monitoring & Log
```bash
# Cek status
systemctl --user status indodax-scalper.service indodax-dashboard.service

# Pantau log realtime
tail -f data/logs/scalper.log
tail -f data/logs/dashboard.log
```

## ⚠️ Disclaimer
Software ini dibuat untuk tujuan edukasi dan eksperimen algoritma trading. **Gunakan dengan risiko Anda sendiri**. Pembuat tidak bertanggung jawab atas kerugian finansial yang terjadi akibat penggunaan software ini. Sangat disarankan untuk menjalankan bot dalam mode **DRY RUN** terlebih dahulu.
