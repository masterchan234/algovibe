# Algovibe 🌊🤖

**Algovibe** adalah bot trading kripto otomatis (Scalping) untuk platform **Indodax** yang diciptakan sepenuhnya melalui proses **"Vibe Coding"**—sebuah metode pengembangan di mana developer berkolaborasi secara *end-to-end* dengan AI Agent untuk merancang, merangkai, dan men-*deploy* kode secara natural melalui instruksi bahasa manusia.

Bot ini dibangun menggunakan arsitektur *multiprocessing* Python untuk memastikan kecepatan dan efisiensi saat memproses ratusan *trading pair* secara bersamaan untuk mencari momentum ("vibe") market yang paling tepat.

## 🎯 Fitur Utama

- **Autonomous Background Execution:** Dirancang untuk berjalan stabil 24/7 menggunakan *Systemd Service* (`indodax-scalper.service`).
- **Parallel Ranking Engine:** Menggunakan 6 vCPUs secara paralel untuk melakukan *scoring* pada semua market Indodax berdasarkan *Volatility*, *Volume*, dan *Spread*.
- **Trend & Breakout Detection:** Menyimpan *micro-history* harga di RAM (`/mnt/ramdisk`) untuk mendeteksi momentum `UP` trend dan titik *Breakout*.
- **Strict Risk Management:**
  - Maksimal 10 transaksi per hari.
  - Berhenti jika saldo *balance* menyentuh batas minimum threshold.
- **Indodax Tax Compliant (PMK 68/2022):** Modul `Performance Tracker` yang sudah disesuaikan dengan aturan pajak Indonesia terbaru (PPh 0.1%, PPN 0.11%, dan Maker Fee 0.1%).
- **Dry Run Mode:** Mendukung mode simulasi untuk menguji strategi tanpa menggunakan uang asli.

## ⚙️ Arsitektur Mesin (*Engines*)

Proyek ini menggunakan pola arsitektur *modular engines*:
- `discovery.py`: Mengambil data pasar real-time dari API publik Indodax.
- `ranking.py`: Menyeleksi dan memberikan skor untuk top *high-speed pairs*.
- `tracker.py`: Memantau dan mencatat pergerakan *micro-history* setiap pair.
- `signals.py`: Mengeluarkan sinyal *ENTRY_BUY* berdasarkan parameter (Trend UP + Breakout + Spread tipis).
- `execution.py` & `main.py`: Mengeksekusi order *limit maker* ke bursa.
- `exit_manager.py`: Memantau order yang sudah masuk dan melakukan eksekusi *SELL* jika menyentuh titik Take Profit (TP) atau Stop Loss (SL).
- `performance_tracker.py`: Menghitung dan mencetak laporan PnL (*Profit and Loss*) harian secara terperinci.

## 📋 Persyaratan Sistem

- OS Linux (Debian/Ubuntu disarankan)
- Python 3.11+
- RAM Disk dikonfigurasi di `/mnt/ramdisk/algotrade` untuk I/O operasi yang sangat cepat.

## 🚀 Instalasi & Konfigurasi

### Opsi 1: Menggunakan Docker (Direkomendasikan)
Menjalankan Algovibe dengan Docker Compose adalah cara paling mudah dan bersih. Konfigurasi `docker-compose.yml` sudah mencakup pengaturan *tmpfs* untuk RAM Disk.

1. **Siapkan Environment:**
   Buat file `.env` dan isi API Key Indodax Anda:
   ```bash
   echo 'INDODAX_API_KEY="your_api_key_here"' > .env
   echo 'INDODAX_API_SECRET="your_secret_key_here"' >> .env
   ```

2. **Jalankan Bot:**
   ```bash
   docker compose up -d
   ```

3. **Melihat Log:**
   ```bash
   docker compose logs -f
   ```

---

### Opsi 2: Manual / Native OS

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Setup Konfigurasi Environment:**
   Buat file `.env` di root direktori proyek ini dan isi dengan API Key Indodax Anda:
   ```env
   INDODAX_API_KEY="your_api_key_here"
   INDODAX_API_SECRET="your_secret_key_here"
   ```

3. **Setup Konfigurasi Bot:**
   Sesuaikan parameter manajemen risiko, target TP/SL, dan mode eksekusi (Real/Dry Run) di dalam file `config.py`.

4. **Siapkan RAM Disk (Opsional tapi disarankan):**
   ```bash
   mkdir -p /mnt/ramdisk/algotrade
   mount -t tmpfs -o size=512m tmpfs /mnt/ramdisk/algotrade
   ```

## 🛠 Menjalankan Bot (Khusus Native OS)

Proyek ini telah disetup untuk berjalan sebagai layanan *Systemd*:
```bash
# Mengaktifkan service
sudo systemctl enable --now indodax-scalper.service

# Melihat status
systemctl status indodax-scalper.service

# Memantau log secara real-time
tail -f scalper.log
```

## 📊 Laporan Performa

Bot akan secara otomatis mencetak laporan PnL setiap pergantian hari. Untuk mencetak laporan 24 jam secara manual, jalankan:
```bash
python3 -c "from engines.performance_tracker import run_performance_report; run_performance_report()"
```

## ⚠️ Disclaimer

Software ini dibuat untuk tujuan edukasi dan eksperimen algoritma trading. **Gunakan dengan risiko Anda sendiri**. Pembuat tidak bertanggung jawab atas kerugian finansial yang terjadi akibat penggunaan software ini. Sangat disarankan untuk menjalankan bot dalam mode **DRY RUN** terlebih dahulu.
