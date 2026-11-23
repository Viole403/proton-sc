# proton-sc — Source Code Pembuatan Akun Proton (Proton-only)

Paket ini berisi **source code untuk pembuatan akun Proton** dari proyek
"brutalism" — fokus HANYA alur Proton (signup browser + API, fingerprint,
captcha, recovery email, inbox/OTP). Modul GitHub/CodeBuddy/qoder TIDAK
ikut; yang disertakan dari luar folder `proton/` hanyalah **modul
pendukung yang langsung diimpor** oleh kode Proton.

**Sudah diperiksa: paket ini TIDAK memuat kredensial apa pun** (tidak ada
password akun, token, API key, kredensial proxy, atau daftar akun).
File data (`data/`, `results/`, `arsip/`, cache) sengaja tidak disertakan.

---

## Struktur

```
proton-sc/
├── README.md            (dokumen ini)
├── setup.sh             (skrip instalasi otomatis)
├── requirements.txt     (dependensi Python)
├── .env.example         (template variabel lingkungan → salin ke .env)
├── proton/              INTI PAKET — kode pembuatan Proton
│   ├── signup.py            driver signup (password generator + alur dasar)
│   ├── core.py              orkestrasi inti (browser, router, run per akun)
│   ├── full_fidelity.py     modul fidelity generasi-1
│   ├── full_fidelity2.py    modul fidelity generasi-2 (persona/blob, TZ,
│   │                        mutasi pre-encrypt — modul utama)
│   ├── fp_profile.py        pembangun profil/fingerprint (persona per akun)
│   ├── fp_forge.js          injeksi sisi-klien: noise render kanvas/WebGL,
│   │                        jitter math, visitorId per attempt
│   ├── make_auth.js         helper autentikasi sisi-klien (Node.js SRP)
│   ├── captcha_slider.py    penyelesaian slider captcha (CV)
│   ├── captcha_service.py   layanan captcha (integrasi solver 2Captcha/Capsolver)
│   ├── recovery_email.py    pemasangan email pemulihan (alias + OTP)
│   ├── alias_otp.py         → ada di cb/ (dipakai recovery_email)
│   ├── proton_api.py        klien API Proton (SRP login, baca OTP/keys)
│   ├── proton_inbox.py      pembaca inbox Proton (OTP, device-verify)
│   ├── proton_otp.py        util OTP CLI
│   ├── smsbower.py          integrasi SMS (opsional)
│   ├── router_cache.py      addon mitmproxy: router + cache aset
│   ├── mitm_browser.py      browser via mitm (untuk perekaman/rute)
│   ├── prewarm.py           pemanasan profil/cache sebelum run
│   ├── harvest.py           utilitas panen
│   ├── lengkapi_token.py    pelengkapan token pasca-signup
│   ├── export_combo.py      ekspor hasil ke format combo
│   ├── cek_aman.py          pemeriksaan kesehatan akun
│   ├── cek_semua.py         pemeriksaan massal
│   ├── probe_form.py        probe struktur form signup
│   ├── diag_modal.py        diagnostik modal/dialog
│   ├── pabrik-murni.py      varian "pabrik" (producer berjendela)
│   └── tui.py               TUI pengendali (perintah: --auto run ...)
├── inti/                MODUL PENDUKUNG (diimpor proton/)
│   ├── konfig.py            satu sumber path/port (stdlib saja)
│   ├── retry.py             util retry
│   ├── tulis.py             util tulis ber-lock (flock lintas-proses)
│   ├── px_forward.py        forwarder proxy lokal
│   ├── fp_lite.py           pembersih sinyal ringan
│   └── run.py               patch runtime (pasang_patch)
├── github/              MODUL PENDUKUNG (diimpor proton/)
│   ├── cdp_gate.py          kolam Chrome + display X (shared infra)
│   ├── gh_api.py            helper proxy/URL (dipakai proton_inbox)
│   └── dd_panen.py          util panen (dipakai beberapa modul)
├── cb/                  MODUL PENDUKUNG (diimpor recovery_email)
│   └── alias_otp.py         layanan mailbox alias + pembaca OTP
└── vendor/              MODUL PENDUKUNG (diimpor proton/)
    ├── dekrip.py            util dekripsi PGP lokal
    └── srp.py               implementasi SRP (autentikasi Proton API)
```

---

## Instalasi (setup)

### Cepat (direkomendasikan)

```bash
# 1. Masuk ke folder paket
cd /path/to/proton-sc

# 2. Jalankan skrip setup
chmod +x setup.sh
./setup.sh
```

Skrip akan:
- Membuat venv Python di `./venv/`
- Menginstall dependensi dari `requirements.txt`
- Menginstall browser Chromium untuk Playwright
- Menyalin `.env.example` ke `.env`
- Membuat direktori `data/`, `results/`, `cache/`, dll.

### Manual

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium --with-deps
cp .env.example .env
```

---

## Konfigurasi

Edit file `.env` dan isi minimal:

| Variabel | Keterangan | Wajib |
|----------|------------|-------|
| `HEADMAIL_KEY` | API key layanan alias headmail | Ya (untuk recovery email) |
| `EVILMAIL_KEY` | API key layanan alias evilmail | Tidak (fallback) |
| `TWOCAPTCHA_KEY` / `CAPSOLVER_KEY` | Kunci solver captcha | Tidak (untuk slider captcha) |
| `SMSBOWER_KEY` | Kunci layanan SMS | Tidak |
| `BRUTALISM_PROXY_FILE` | Path file proxy (`host:port:user:pass`) | Tidak (pakai `data/proxies.txt`) |
| `BRUTALISM_CHROME_BIN` | Jalur browser Chrome/Chromium | Tidak (auto-deteksi) |

Buat file `data/proxies.txt` jika akan menggunakan proxy (format: satu proxy per baris, `host:port:user:pass`).

---

## Penggunaan

### TUI (mode interaktif, paling ramah)

```bash
source venv/bin/activate
python proton/tui.py
```

Menu:
1. **run** — buat akun (one-shot atau batch)
2. **proxy-pool** — kelola/uji daftar proxy
3. **registered** — lihat akun sukses (`results/registered.txt`)
4. **proton-checker** — cek status akun (via refresh token atau login browser)
5. **settings** — atur API key (disimpan ke `data/settings.json`)
6. **statistik** — metrik run (rata2 waktu/akun, byte proxy, dll.)
7. **kebersihan** — perawatan (bebaskan RAM Chrome, hapus sisa profil)
8. **prewarm-cache** — panaskan cache aset router
9. **keluar**

### Mode non-interaktif (otomatisasi)

```bash
# Jalankan pembuatan akun
python proton/tui.py --auto run --accounts 5 --workers 2 --mode oneshot

# Cek semua akun terdaftar
python proton/tui.py --auto check

# Cek dengan login browser (lebih lambat, deteksi suspend)
python proton/tui.py --auto checklogin --workers 2

# Lihat statistik
python proton/tui.py --auto stats

# Perawatan membersihkan sisa
python proton/tui.py --auto clean

# Panaskan cache aset
python proton/tui.py --auto prewarm
```

### Anti-suspend mitigasi (penting)

Proton menggunakan ML clustering untuk mendeteksi bulk/scripted signup. Akun
yang terdeteksi akan **ditangguhkan (suspended)**. Untuk mengurangi risiko:

| Opsi | Keterangan | Disarankan |
|------|-----------|------------|
| `--anti-suspend-delay N` | Jeda N menit antar akun dalam satu batch | 5-15 menit |
| `--batch-pause N` | Jeda N menit antar batch | 30-60 menit |
| Satu proxy residential per akun | Jangan reuse proxy untuk banyak akun | Wajib |
| `ANTI_SUSPEND_DELAY_MIN=N` | Env var alternatif ke `--anti-suspend-delay` | 5-15 |
| `NAIKKAN_SESI=1` | Upgrade scope sesi (berisiko, bisa buang akun) | 0 (mati) |
| Proxy kualitas tinggi | Hindari proxy publik/datacenter yang sudah kena flag | Wajib |

Contoh aman (1 akun, jeda 10 menit kalau banyak):

```bash
# 1 akun, tanpa jeda (test)
python proton/tui.py --auto run --accounts 1 --workers 1

# 3 akun, jeda 10 menit antar akun, batch 1 per batch
python proton/tui.py --auto run --accounts 3 --workers 1 --mode batch \
  --per-batch 1 --batch-pause 30 --anti-suspend-delay 10
```

**Peringatan:** Pembuatan akun massal melanggar Ketentuan Layanan Proton.
Akun yang kena suspend dapat di-appeal via
https://proton.me/support/appeal-abuse tapi tidak dijamin dibuka.

### Jalur signup inti

`proton/core.py` (orkestrasi) → `full_fidelity2.py` (persona + mutasi blob)
→ `signup.py` (form) → `recovery_email.py` (opsional) → API
(`proton_api.py`) untuk verifikasi/OTP.

Data keluaran (daftar akun, dsb.) ditulis ke folder `results/` dan `data/`.

---

## Dependensi

### Python (pip / sistem)

- Python 3.10+ dengan: `playwright`, `patchright`, `curl_cffi`, `requests`,
  `bcrypt`, `numpy`, `Pillow`, `mitmproxy` (opsional, untuk router cache).
- Chrome terpasang (dipakai `cdp_gate` + full_fidelity) + Xvfb
  (`DISPLAY=:99`).
- Node.js diperlukan untuk `make_auth.js` (SRP proof di sisi klien).

### Struktur folder

Struktur folder dipertahankan seperti aslinya karena `konfig.py`
menghitung `AKAR` dari lokasi file (`parent.parent`) — letakkan paket
ini utuh, jangan tukar posisi subfoldernya.

API key layanan pihak ketiga (mis. layanan mailbox alias) DIBACA dari
environment variable atau `data/settings.json` (lihat
`cb/alias_otp.py` fungsi `_keys()`) — **tidak dibundel di sini**; sediakan
sendiri bila memakai fitur recovery-email.

---

## Peringatan

Kode ini dibuat untuk keperluan **riset internal** terhadap alur
pendaftaran Proton. Perhatikan ketentuan layanan pihak yang bersangkutan
saat menggunakannya.
