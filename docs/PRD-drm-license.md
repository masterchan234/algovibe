# PRD: DRM & License Management System
**Algovibe Intelligence — Retail Distribution Layer**
**Status**: Draft | **Version**: 1.1 | **Date**: 2026-05-26
**Changelog**: v1.1 — Tambah section anti-reverse-engineering & code integrity (Section 5.6, 8, 6)

---

## 1. Background & Motivation

Algovibe saat ini berjalan sebagai sistem private. Untuk distribusi ke retail, dibutuhkan mekanisme DRM (Digital Rights Management) agar:
- Setiap instance bot terikat ke satu lisensi yang valid
- Bot tidak bisa dijalankan tanpa pembayaran aktif
- Satu lisensi tidak bisa digunakan di banyak mesin sekaligus (*seat enforcement*)
- Pemilik dapat mencabut akses kapan saja (revoke)

---

## 2. Objectives

| # | Objective | Metric |
|---|-----------|--------|
| 1 | Setiap bot instance wajib punya license key valid | 100% startup check |
| 2 | License terikat ke hardware fingerprint (1 key = 1 mesin) | Machine activation tracking |
| 3 | License expired/revoked → bot tidak bisa buka posisi baru | Entry gate enforcement |
| 4 | Pemilik bisa issue, suspend, revoke license via dashboard | Keygen.sh admin panel |
| 5 | Tidak ada offline bypass (online check required) | No grace period |

---

## 3. Scope

### In Scope
- License validation di startup
- Background re-validation (interval 5 menit)
- Hardware fingerprinting (CPU + disk + MAC)
- Entry gate block saat license tidak valid
- Logging terstandarisasi untuk license events
- Env var `LICENSE_KEY` sebagai input user
- Config flag `LICENSE_ENABLED` untuk dev/testing bypass

### Out of Scope (v1)
- UI dashboard untuk user melihat status license
- Auto-renewal / payment integration
- Trial mode / free tier
- Multi-seat license
- Offline grace period
- Full binary compilation (Nuitka) — dijadwalkan v2

---

## 4. User Stories

### Retail User (Bot Operator)
- **US-01**: Sebagai user, saya ingin memasukkan license key di `.env` agar bot bisa jalan.
- **US-02**: Sebagai user, saya ingin bot langsung memberitahu saya jika license tidak valid saat startup.
- **US-03**: Sebagai user, saya ingin bot tetap memantau posisi terbuka meskipun license expired (tidak force-close posisi yang sudah ada).

### Pemilik (License Admin)
- **US-04**: Sebagai admin, saya ingin menerbitkan license key baru untuk setiap customer yang membayar.
- **US-05**: Sebagai admin, saya ingin mencabut (revoke) license jika customer chargeback atau melanggar ToS.
- **US-06**: Sebagai admin, saya ingin melihat berapa banyak instance aktif per license key.
- **US-07**: Sebagai admin, saya ingin set expiry date pada license (monthly/yearly subscription).

---

## 5. Technical Architecture

### 5.1 License Server

**Platform**: [Keygen.sh](https://keygen.sh)
- REST API untuk issue, validate, activate, revoke
- Support machine fingerprinting (`machine` resource)
- Webhook untuk event: `license.expired`, `license.revoked`
- Free tier: 50 licenses (cukup untuk pilot)

### 5.2 Hardware Fingerprint

Fingerprint dibuat dari kombinasi deterministik:
```
fingerprint = SHA256(
  machine_id      # /etc/machine-id (Linux) atau registry (Windows)
  + mac_address   # MAC address interface utama (non-loopback)
  + cpu_id        # CPU serial / model string
)[:16]            # ambil 16 hex char
```

Tujuan: stabil antar reboot, berubah jika pindah hardware signifikan.

### 5.3 Validation Flow

```
Bot Startup
    │
    ▼
[1] Baca LICENSE_KEY dari env
    │ tidak ada
    ├──────────────► [ERROR] Bot tidak start, log: "LICENSE_KEY not set"
    │
    ▼
[2] Generate machine fingerprint
    │
    ▼
[3] POST /v1/licenses/{key}/actions/validate-key
    + machine fingerprint ke Keygen.sh API
    │
    ├── valid + activated ──► [OK] Bot start normal
    │
    ├── valid + not activated ──► [4] Activate machine
    │                                  │ success ──► [OK] Bot start
    │                                  │ gagal (seat limit) ──► [ERROR] Stop
    │
    ├── expired ──────────────► [WARN] Log + block new entries, posisi existing tetap dipantau
    │
    └── invalid/revoked ───────► [ERROR] Bot tidak start
```

### 5.4 Background Re-validation

```
Setiap 5 menit (background thread):
    validate() → cache result
    Gagal 3x berturut → set license_valid = False
    entry_gates.py membaca license_valid sebelum approve entry
```

### 5.5 Entry Gate Enforcement

Ditambahkan sebagai **Gate 0** di `entry_gates.py` (sebelum semua gate lain):
```python
if not license_manager.is_valid():
    return False, "LICENSE_INVALID: bot tidak berlisensi aktif"
```

Bot **tidak** force-close posisi existing — hanya mencegah posisi **baru**.

### 5.6 Anti-Reverse-Engineering & Code Integrity

#### Filosofi
Python tidak bisa 100% dilindungi dari determined attacker. Tujuan adalah:
> **Cost of cracking > Cost of buying license**

Tiga lapisan pertahanan diterapkan secara berlapis (*defense in depth*):

#### Lapisan 1 — Code Integrity Verification

Saat build, generate `integrity_manifest.json` berisi SHA-256 hash semua file `.py` kritis.
Manifest di-**sign** dengan ECDSA private key (disimpan oleh developer, tidak didistribusikan).
Public key di-embed di `license.py`.

Saat startup, bot:
1. Recompute hash semua file kritis
2. Compare ke manifest
3. Verifikasi signature manifest pakai public key
4. Jika hash mismatch → **shutdown** dengan log `[LICENSE] [ERROR] Integrity check failed`

```
File kritis yang di-hash:
  app/infrastructure/license.py
  app/application/use_cases/entry_gates.py
  app/application/use_cases/trade_execution.py
  app/domain/technicals.py
  config.py
```

Kelemahan & mitigasi:
- Manifest + public key juga bisa dimodifikasi → **mitigasi**: manifest hash juga di-check ke Keygen.sh metadata saat validasi (server-side truth)
- Attacker bisa patch integrity checker itu sendiri → **mitigasi**: Lapisan 2

#### Lapisan 2 — PyArmor Obfuscation (v1)

Modul kritis di-obfuscate dengan PyArmor sebelum distribusi:
```bash
pyarmor gen --pack onefile \
  app/infrastructure/license.py \
  app/application/use_cases/entry_gates.py \
  app/application/use_cases/trade_execution.py
```

Hasil: bytecode terenkripsi + C extension loader. Source `.py` asli tidak ada di distribusi.

| Aspek | Tanpa Obfuscation | Dengan PyArmor |
|-------|-------------------|----------------|
| Baca source | Langsung | Perlu unpack effort |
| Patch license check | 2 menit | Butuh reverse bytecode |
| Distribusi | `.py` file | Encrypted `.pyc` + `.so` |

#### Lapisan 3 — Runtime Tamper Detection

Cek dilakukan di `license.py` saat startup:

```python
# 1. Debugger detection
import sys
if sys.gettrace() is not None:
    # debugger attached → kemungkinan sedang di-reverse
    shutdown("DEBUGGER_DETECTED")

# 2. Critical function monkey-patch detection
# Cek apakah fungsi entry gate masih original
import hashlib, inspect
expected_hash = "<hash dari source asli>"
actual_hash = hashlib.sha256(
    inspect.getsource(check_entry_gates).encode()
).hexdigest()
if actual_hash != expected_hash:
    shutdown("FUNCTION_TAMPERED")
```

#### Summary Defense Layers

```
 Attacker Action              Layer yang Harus Di-bypass
 ─────────────────────────────────────────────────────
 Edit entry_gates.py      →   Layer 1 (hash mismatch)
 Replace manifest         →   Layer 1 (signature invalid)
 Patch license.py         →   Layer 2 (obfuscated)
 Attach debugger          →   Layer 3 (runtime detection)
 Patch di memory          →   Layer 3 (function hash check)
 Full binary RE            →   Layer 2 (Nuitka di v2)
```

#### Build Script

`compile_prod.py` (sudah ada) akan di-extend untuk:
1. Generate integrity manifest
2. Sign manifest dengan private key
3. Run PyArmor pada modul kritis
4. Build Docker image dari hasil obfuscation

---

## 6. File Changes

| File | Action | Description |
|------|--------|-------------|
| `app/infrastructure/license.py` | **CREATE** | `LicenseManager` class: fingerprint, validate, activate, background thread, integrity check, tamper detection |
| `app/bootstrap.py` | **MODIFY** | Inisialisasi `LicenseManager`, hard-stop jika license tidak valid |
| `app/application/use_cases/entry_gates.py` | **MODIFY** | Gate 0: cek `license_manager.is_valid()` |
| `config.py` | **MODIFY** | Tambah `LICENSE_ENABLED`, `LICENSE_KEY`, `KEYGEN_ACCOUNT_ID` |
| `docker-compose.yml` | **MODIFY** | Pass `LICENSE_KEY` env var ke semua service |
| `.env.example` | **MODIFY** | Tambah `LICENSE_KEY=your-key-here` |
| `requirements.txt` | **MODIFY** | Tambah `requests`, `cryptography` (untuk ECDSA signature verify) |
| `integrity_manifest.json` | **CREATE** (build-time) | SHA-256 hash semua file kritis + ECDSA signature |
| `compile_prod.py` | **MODIFY** | Extend: generate manifest, sign, run PyArmor, build Docker image |
| `keys/public_key.pem` | **CREATE** | ECDSA public key untuk verifikasi manifest (masuk ke distribusi) |

---

## 7. API Contract (Keygen.sh)

### Validate License
```
POST https://api.keygen.sh/v1/accounts/{ACCOUNT_ID}/licenses/actions/validate-key
Authorization: Bearer {LICENSE_KEY}
Content-Type: application/vnd.api+json

{
  "meta": {
    "key": "{LICENSE_KEY}",
    "scope": {
      "fingerprint": "{MACHINE_FINGERPRINT}"
    }
  }
}
```

Response codes yang ditangani:
| Code | Meaning | Action |
|------|---------|--------|
| `VALID` | License valid & fingerprint match | Allow |
| `NO_MACHINES` | Belum diaktivasi | Activate machine |
| `TOO_MANY_MACHINES` | Seat limit tercapai | Block + log error |
| `EXPIRED` | License expired | Block entry, log warn |
| `SUSPENDED` / `REVOKED` | Admin cabut akses | Block + log error |
| `NOT_FOUND` | Key tidak dikenal | Block + log error |

### Activate Machine
```
POST https://api.keygen.sh/v1/accounts/{ACCOUNT_ID}/machines
Authorization: Bearer {LICENSE_KEY}

{
  "data": {
    "type": "machines",
    "attributes": {
      "fingerprint": "{MACHINE_FINGERPRINT}",
      "name": "{hostname}",
      "platform": "linux/docker"
    },
    "relationships": {
      "license": {
        "data": { "type": "licenses", "id": "{LICENSE_ID}" }
      }
    }
  }
}
```

---

## 8. Security Considerations

| Threat | Attack Vector | Mitigation | Layer |
|--------|--------------|-----------|-------|
| Key sharing | User bagikan key ke orang lain | Machine fingerprint binding (1 key = 1 mesin) | License |
| Key hardcoding | Key di-bake ke Docker image | Key hanya dari env var runtime | Ops |
| Man-in-the-middle | Intercept Keygen.sh API call | HTTPS only + SSL cert pinning | Network |
| Source patching | Edit `entry_gates.py` langsung | Code integrity hash + signature | Layer 1 |
| Manifest forgery | Replace `integrity_manifest.json` | ECDSA signature verifikasi pakai embedded public key | Layer 1 |
| Integrity checker patching | Edit `license.py` sendiri | PyArmor obfuscation pada `license.py` | Layer 2 |
| Debugger attachment | GDB/pyrasite attach saat runtime | `sys.gettrace()` detection → shutdown | Layer 3 |
| Function monkey-patching | Override fungsi entry gate di memory | Runtime function hash check | Layer 3 |
| Full decompile | Decompile `.pyc` obfuscated | PyArmor C extension loader (non-trivial) | Layer 2 |
| Binary RE | Reverse Docker image layer | Nuitka compile → native binary (v2) | v2 |
| Log scraping | Baca log untuk dapat key | License key di-mask di semua log output | Ops |
| Network unavailability | Pull network cable agar skip online check | Gagal validasi → re-try 3x → block entry | License |
| Private key leak | Developer private key bocor | Private key tidak masuk distribusi, hanya public key | Ops |

### Security Boundary

```
┌─────────────────────────────────────────┐
│           DISTRIBUSI (user punya)        │
│  - Docker image (obfuscated source)      │
│  - integrity_manifest.json (signed)      │
│  - keys/public_key.pem                   │
│  - .env (berisi LICENSE_KEY user)        │
└─────────────────────────────────────────┘
┌─────────────────────────────────────────┐
│        TIDAK DIDISTRIBUSIKAN             │
│  - Source .py asli (sebelum obfuscation) │
│  - keys/private_key.pem  ← KRITIS       │
│  - Keygen.sh admin credentials           │
└─────────────────────────────────────────┘
```

---

## 9. Config Variables

```env
# .env
LICENSE_ENABLED=true          # Set false untuk dev/testing bypass
LICENSE_KEY=algv-xxxx-xxxx-xxxx-xxxx
KEYGEN_ACCOUNT_ID=your-keygen-account-id
```

```python
# config.py additions
LICENSE_ENABLED    = os.getenv("LICENSE_ENABLED", "true").lower() == "true"
LICENSE_KEY        = os.getenv("LICENSE_KEY", "")
KEYGEN_ACCOUNT_ID  = os.getenv("KEYGEN_ACCOUNT_ID", "")
LICENSE_CHECK_INTERVAL = 300   # detik, re-validate background
LICENSE_MAX_FAILURES   = 3     # sebelum block entry
```

---

## 10. Logging Standards

Semua license log menggunakan tag `[LICENSE]`:

```
[LICENSE] [OK] License valid — key=algv-xxxx...xxxx machine=a1b2c3d4
[LICENSE] [WARN] License expired — new positions blocked
[LICENSE] [ERROR] License invalid (REVOKED) — bot shutdown
[LICENSE] [ERROR] LICENSE_KEY not set — bot shutdown
[LICENSE] [WARN] Validation failed (attempt 2/3) — retrying
[LICENSE] [ERROR] Validation failed 3x — new positions blocked
```

---

## 11. Distribution Model (Docker)

User mendapat:
1. `docker-compose.yml` (versi retail, image dari Docker Hub)
2. `.env.template` dengan `LICENSE_KEY=` kosong
3. Instruksi: isi `LICENSE_KEY`, jalankan `docker compose up -d`

Image `algovibe/bot:latest` di-push ke Docker Hub setelah source di-compile/obfuscate (v2).

---

## 12. Acceptance Criteria

| ID | Kriteria | Test |
|----|---------|------|
| AC-01 | Bot tidak start jika `LICENSE_KEY` kosong | Set env kosong, jalankan bot |
| AC-02 | Bot tidak start jika key invalid/revoked | Gunakan key palsu |
| AC-03 | Bot berjalan normal jika key valid | Gunakan key valid |
| AC-04 | Mesin kedua dengan key sama → blocked (seat limit 1) | Aktivasi di 2 mesin |
| AC-05 | Key expired → bot jalan tapi tidak bisa buka posisi | Set expiry di masa lalu via Keygen admin |
| AC-06 | Background re-check 5 menit → revoke saat jalan → entry blocked | Revoke via Keygen admin saat bot sedang running |
| AC-07 | License key tidak muncul di log output | Review semua log |
| AC-08 | `LICENSE_ENABLED=false` → bypass semua check (dev mode) | Set false, jalankan dengan key kosong |
| AC-09 | File kritis diedit → integrity check fail → shutdown | Edit satu baris di `entry_gates.py`, jalankan bot |
| AC-10 | Manifest di-replace tanpa re-sign → signature invalid → shutdown | Replace manifest dengan versi palsu |
| AC-11 | Debugger attach → runtime detection → shutdown | Attach `pdb` saat bot running |
| AC-12 | Function monkey-patch terdeteksi → shutdown | Override `check_all_gates` di runtime |

---

## 13. Milestones

| Milestone | Deliverable |
|-----------|-------------|
| M1 | `license.py`: fingerprint + Keygen.sh validate + activate + background thread |
| M2 | Integration ke `bootstrap.py` + `entry_gates.py` (Gate 0) |
| M3 | `config.py` + `.env` update |
| M4 | Code integrity: generate manifest, ECDSA sign/verify, startup check |
| M5 | Runtime tamper detection (debugger + function hash) |
| M6 | PyArmor obfuscation pipeline di `compile_prod.py` |
| M7 | End-to-end test dengan Keygen.sh account nyata |
| M8 | Docker image build + push ke Docker Hub |

---

## 14. Open Questions

1. **Berapa seat per license?** Default 1 (satu mesin per key). Apakah perlu paket multi-seat?
2. **License duration?** Monthly (30 hari) atau annual (365 hari)?
3. **Harga tier?** Berdampak ke limit yang di-set di Keygen.sh.
4. **Apakah dashboard web untuk user perlu?** (Lihat status license, tanggal expiry)
5. **Private key management?** ECDSA private key harus disimpan di secrets manager (bukan Git). Tool yang digunakan: 1Password / AWS Secrets Manager / local secure storage?
6. **PyArmor license?** PyArmor free tier punya batasan. Apakah perlu beli lisensi PyArmor Pro?
7. **Nuitka timeline?** Full binary compile dijadwalkan v2 — kapan target release?
8. **Manifest update flow?** Setiap kali source berubah, manifest harus di-regenerate + re-sign. Apakah ini otomatis di CI/CD atau manual?
