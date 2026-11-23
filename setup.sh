#!/usr/bin/env bash
# =============================================================================
# proton-sc — setup script (Indonesian/English)
# Instalasi dependensi, venv, browser Playwright, dan konfigurasi awal.
# =============================================================================
set -euo pipefail

AKAR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$AKAR"

echo "==> [1/6] Deteksi Python..."
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 tidak ditemukan / not found. Install Python 3.10+ dulu."
  exit 1
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "   Python $PY_VER terdeteksi"

echo "==> [2/6] Buat venv & install dependensi Python..."
if [ ! -d "$AKAR/venv" ]; then
  python3 -m venv "$AKAR/venv"
fi
# shellcheck source=/dev/null
. "$AKAR/venv/bin/activate"
pip install --upgrade pip
pip install -r "$AKAR/requirements.txt"

echo "==> [3/6] Install browser Playwright..."
playwright install chromium --with-deps 2>/dev/null || true

echo "==> [4/6] Deteksi Chrome sistem..."
if command -v google-chrome-stable &>/dev/null; then
  CHROME_BIN=$(command -v google-chrome-stable)
elif command -v google-chrome &>/dev/null; then
  CHROME_BIN=$(command -v google-chrome)
elif command -v chromium &>/dev/null; then
  CHROME_BIN=$(command -v chromium)
elif command -v chromium-browser &>/dev/null; then
  CHROME_BIN=$(command -v chromium-browser)
else
  CHROME_BIN=""
  echo "   PERINGATAN: Chrome/Chromium sistem tidak ditemukan. Beberapa modul butuh browser native."
fi
if [ -n "$CHROME_BIN" ]; then
  echo "   Chrome: $CHROME_BIN ($($CHROME_BIN --version 2>/dev/null || echo '?'))"
fi

echo "==> [5/6] Buat file .env dari contoh..."
if [ ! -f "$AKAR/.env" ]; then
  cp "$AKAR/.env.example" "$AKAR/.env"
  echo "   .env dibuat. Isi API key (HEADMAIL_KEY, EVILMAIL_KEY, dst.) sebelum menjalankan."
else
  echo "   .env sudah ada, dilewati."
fi

echo "==> [6/6] Buat direktori data/ hasil..."
mkdir -p "$AKAR/data" "$AKAR/results" "$AKAR/results/attempts" \
         "$AKAR/results/shots" "$AKAR/results/dump" "$AKAR/results/logs" \
         "$AKAR/cache" "$AKAR/cache/fc" "$AKAR/analysis/dbg-captcha/live"

echo ""
echo "==> SETUP SELESAI"
echo ""
echo "Langkah berikutnya:"
echo "  1. Edit $AKAR/.env — isi HEADMAIL_KEY / EVILMAIL_KEY (dan opsi TWOCAPTCHA_KEY)"
echo "  2. Buat $AKAR/data/proxies.txt (format: host:port:user:pass, satu per baris)"
echo "  3. Jalankan:  python proton/tui.py --auto run --accounts 1 --workers 1"
echo ""
echo "Catatan: mitmproxy (router cache) dipakai untuk hemat bandwidth proxy."
echo "         Jika tidak terpasang, install:  pip install mitmproxy"
