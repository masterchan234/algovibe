# FSD: Adaptive Technical Indicator Thresholds

**Status**: Draft  
**Versi**: 1.0  
**Tanggal**: 2026-05-13  
**Author**: Engineering  

---

## 1. Latar Belakang

Sistem indikator teknikal saat ini (`app/domain/technicals.py`) menggunakan **parameter dan threshold tetap** untuk semua coin:

| Indikator | Parameter Saat Ini |
|---|---|
| RSI | period=14, oversold<35, overbought>70 |
| MACD | fast=12, slow=26, signal=9 |
| Bollinger Bands | period=20, std=2 |
| StochRSI | rsi=14, stoch=14, K=3, D=3 |
| EMA Crossover | EMA-9 vs EMA-21 |
| Williams %R | period=14 |
| ROC | period=10 |
| BUY_THRESHOLD | 6.0 |
| SELL_THRESHOLD | 6.0 |

**Masalah**: Coin yang berbeda memiliki karakter volatilitas yang sangat berbeda.

- **MEME coins** (DOGE, PEPE, SHIB): pump 10-20% dalam 3-5 candle. RSI-14 dengan threshold 35 terlambat — sinyal muncul setelah pump selesai.
- **L1 bluechips** (BTC, ETH): slow-mover, mean-reverting kuat. RSI-14 sangat relevan.
- **Micro-cap Indodax** (volume < 200M IDR/hari): OBV Slope dan Volume Ratio noise tinggi, tidak reliable sebagai sinyal.

Akibatnya:
- Pada MEME coins: **terlalu banyak missed entry** karena sinyal terlambat.
- Pada micro-cap: **false positive** dari indikator volume karena data terlalu tipis.

---

## 2. Tujuan

1. Membuat threshold indikator **self-calibrating** berdasarkan volatilitas historis coin itu sendiri — bukan hardcode per-coin.
2. Menambahkan **sektor-based weight modifier** ke `derive_trade_signal()` agar indikator yang relevan mendapat bobot lebih besar sesuai karakter sektor.
3. Tidak mengubah struktur scoring yang sudah terbukti stabil. Semua perubahan bersifat **additive layer**, bukan rewrite.

---

## 3. Non-Goals

- **Bukan** per-coin hardcoded config (terlalu mudah overfit dan unmaintainable untuk 100+ coin).
- **Bukan** machine learning atau AI dalam jalur decision — `TECHNICAL_ONLY_MODE` tetap `true`.
- **Bukan** mengubah entry gates, risk management, atau execution path.

---

## 4. Desain Solusi

### 4.1 Volatility-Adaptive RSI Threshold

**Prinsip**: Threshold "oversold" bukan fixed `35`, tapi dihitung dari distribusi RSI historis coin itu sendiri.

```
rsi_oversold_threshold  = percentile_20(RSI_values[-N_candles])
rsi_overbought_threshold = percentile_80(RSI_values[-N_candles])
```

Jika RSI coin itu sendiri sering ada di 30-40 (MEME), maka threshold oversoldnya otomatis lebih rendah. Jika sering di 45-65 (L1 trending), thresholdnya lebih tinggi.

**Parameter**:
- `N_candles = 50` (jumlah candle historis untuk kalibrasi)
- Percentile default jika data < 30 candle: fallback ke threshold tetap (35/70)
- Clamp: oversold min 20, max 45; overbought min 55, max 85

**Dampak pada kode**: Fungsi `derive_trade_signal(tech)` menerima parameter opsional `adaptive_thresholds` (dict). Jika tidak disediakan, fallback ke nilai saat ini — **backward compatible**.

---

### 4.2 Sector-Based Weight Modifier

Setiap sektor mendapat multiplier bobot indikator yang berbeda di `derive_trade_signal()`.

**Tabel Weight Modifier per Sektor**:

| Indikator | L1 (default) | MEME | AI | L2 | DEFI |
|---|---|---|---|---|---|
| RSI (w=3.0) | 1.0× | 0.7× | 1.0× | 1.0× | 1.0× |
| MACD (w=2.5) | 1.0× | 1.4× | 1.2× | 1.0× | 1.0× |
| Bollinger %B (w=2.0) | 1.0× | 1.3× | 1.0× | 1.0× | 1.0× |
| StochRSI (w=2.0) | 1.0× | 1.5× | 1.2× | 1.0× | 1.0× |
| EMA Crossover (w=1.5) | 1.0× | 0.6× | 1.0× | 1.0× | 1.0× |
| OBV Slope (w=1.5) | 1.0× | 0.5× | 0.8× | 1.0× | 1.2× |
| Volume Ratio (w=1.5) | 1.0× | 1.3× | 1.0× | 1.0× | 1.0× |
| Williams %R (w=1.5) | 1.0× | 1.2× | 1.0× | 1.0× | 1.0× |

**Justifikasi MEME**:
- RSI dikurangi (0.7×): RSI sering "terlambat" di MEME — sinyal muncul setelah pump selesai.
- MACD, StochRSI, BB, Volume dinaikkan: lebih sensitif terhadap pergerakan cepat.
- EMA Crossover dikurangi (0.6×): EMA membutuhkan periode stabil, tidak cocok untuk spike cepat.
- OBV dikurangi (0.5×): volume spike MEME sering noise, bukan akumulasi nyata.

**Justifikasi AI**:
- MACD dan StochRSI sedikit lebih dominan karena momentum mid-term lebih predictable.

**Unknown/unlisted sector**: semua multiplier = 1.0 (perilaku default saat ini).

---

### 4.3 Volume Reliability Guard

Untuk coin dengan volume harian < threshold (default: 200M IDR), indikator berbasis volume (OBV Slope, Volume Ratio) di-**downgrade** bobotnya secara otomatis karena sinyal tidak reliabel.

```python
if vol_idr_24h < VOLUME_RELIABILITY_THRESHOLD:
    obv_weight_modifier  *= 0.3
    vol_ratio_modifier   *= 0.3
```

`VOLUME_RELIABILITY_THRESHOLD` = 200_000_000 (200M IDR) — configurable.

---

## 5. Interface / API Perubahan

### 5.1 `derive_trade_signal(tech, sector=None, adaptive_thresholds=None)`

**File**: `app/domain/technicals.py`

Tambah dua parameter opsional:

```python
def derive_trade_signal(tech, sector=None, adaptive_thresholds=None):
    """
    sector: str | None
        Sektor coin dari SECTOR_MAP (config.py).
        None = gunakan weight default (perilaku saat ini).
    
    adaptive_thresholds: dict | None
        {
          'rsi_oversold': float,   # default 35
          'rsi_overbought': float, # default 70
        }
        None = gunakan threshold tetap (perilaku saat ini).
    """
```

### 5.2 `compute_adaptive_thresholds(price_history, n_candles=50)`

**File**: `app/domain/technicals.py` (fungsi baru)

```python
def compute_adaptive_thresholds(price_history, n_candles=50):
    """
    Hitung threshold RSI adaptif dari distribusi RSI historis coin.
    
    Args:
        price_history: list/deque harga penutupan
        n_candles: jumlah candle terakhir yang dipakai
    
    Returns:
        dict {
            'rsi_oversold': float,   # percentile ke-20 dari RSI historis
            'rsi_overbought': float, # percentile ke-80 dari RSI historis
        }
    """
```

### 5.3 Pemanggil: `brain_inference` dan `entry_gates`

**File**: `services/brain_inference/main.py` dan `app/application/use_cases/entry_gates.py`

Sebelum memanggil `derive_trade_signal`, compute adaptive thresholds dari `price_history` yang sudah tersedia:

```python
from config import SECTOR_MAP
from app.domain.technicals import compute_adaptive_thresholds, derive_trade_signal

sector = SECTOR_MAP.get(pair.lower())
adaptive = compute_adaptive_thresholds(price_history[pair]) if len(price_history[pair]) >= 30 else None
signal = derive_trade_signal(tech_summary, sector=sector, adaptive_thresholds=adaptive)
```

---

## 6. Implementasi Plan

### Phase 1 — Pondasi (low risk)

- [ ] Tambah `compute_adaptive_thresholds()` ke `technicals.py`
- [ ] Tambah parameter `sector` dan `adaptive_thresholds` ke `derive_trade_signal()` — fully backward compatible
- [ ] Unit test: pastikan perilaku identik jika kedua parameter `None`
- [ ] Syntax check: `python3 -m py_compile app/domain/technicals.py`

### Phase 2 — Sector Weights

- [ ] Implementasi weight modifier table ke dalam `derive_trade_signal()` menggunakan `sector` param
- [ ] Tambah `VOLUME_RELIABILITY_THRESHOLD` ke `config.py`
- [ ] Implementasi volume reliability guard
- [ ] Dry-run test: jalankan dengan `DRY_RUN=true`, amati log sinyal per-pair apakah masuk akal

### Phase 3 — Adaptive RSI Threshold

- [ ] Integrasikan `compute_adaptive_thresholds()` ke `brain_inference/main.py`
- [ ] Integrasikan ke `entry_gates.py` (hanya gate 4 — multi-confluence check)
- [ ] Pantau log selama 24 jam dry-run: hitung false positive rate vs baseline

### Phase 4 — Tuning & Observasi

- [ ] Bandingkan win-rate per sektor: MEME vs L1 vs AI sebelum dan sesudah
- [ ] Adjust weight multiplier jika ada sektor yang terlalu agresif/konservatif
- [ ] Pertimbangkan expose sector_weights ke dashboard untuk live-tuning

---

## 7. Risiko dan Mitigasi

| Risiko | Tingkat | Mitigasi |
|---|---|---|
| Adaptive threshold terlalu sensitif → false positive naik | Medium | Clamp min/max pada percentile, fallback jika data < 30 candle |
| MEME weight modifier terlalu agresif → overtrading | Medium | Mulai Phase 2 di DRY_RUN, amati 48 jam sebelum live |
| Regression di L1 (default behavior berubah) | Low | Parameter opsional + backward compat test |
| Volume guard terlalu ketat → missed entry valid | Low | Threshold configurable di `config.py`, bukan hardcode |

---

## 8. File yang Akan Diubah

| File | Jenis Perubahan |
|---|---|
| `app/domain/technicals.py` | Tambah `compute_adaptive_thresholds()`, update signature `derive_trade_signal()` |
| `config.py` | Tambah `VOLUME_RELIABILITY_THRESHOLD`, sector weight table |
| `services/brain_inference/main.py` | Pass `sector` dan `adaptive_thresholds` ke `derive_trade_signal` |
| `app/application/use_cases/entry_gates.py` | Pass `sector` dan `adaptive_thresholds` di gate 4 |

**File yang TIDAK diubah**: `exit_manager.py`, `trade_execution.py`, `scoring.py`, semua service selain `brain_inference`.

---

## 9. Definisi Done

- [ ] `derive_trade_signal(tech)` tanpa parameter opsional menghasilkan output identik dengan sekarang (regression test pass)
- [ ] Log menunjukkan sektor coin pada setiap sinyal: `[sector=MEME] BUY BS=7.2 SS=1.1`
- [ ] MEME coins tidak lagi menghasilkan sinyal BUY saat RSI masih > 60 (yang sekarang sering terjadi post-pump)
- [ ] Tidak ada perubahan pada path eksekusi order, risk cap, atau DCA logic
