#!/usr/bin/env python3
"""proton_otp.py — ambil kode OTP dari kotak masuk Proton utk pendaftaran layanan
lain (mis. GitHub). Strategi: **browser = dekriptor** — login resmi Proton, masuk
kotak masuk, buka email dari pengirim, baca kode dari teks yang sudah didekripsi
aplikasi. Tanpa mengimplementasikan PGP sendiri.

Format data yang didukung: email|password (combo.txt) atau baris registered.txt
(email|password|refresh_token|client_id|access_token — password kolom 2).

Pakai:
  venv/bin/python proton_otp.py --mail kamu@proton.me --pass 'xxx' --dari github
  venv/bin/python proton_otp.py --mail kamu@proton.me --pass 'xxx' --dari github --ulang 8
"""
# brutalism: jalur lintas-folder (inti/proton/github/cb/cek/vendor) + helper inti
import os as _os_b, sys as _sys_b
for _d_b in ("inti", "proton", "github", "cb", "cek", "vendor"):
    _p_b = _os_b.path.join(
        _os_b.path.dirname(_os_b.path.dirname(_os_b.path.abspath(__file__))), _d_b)
    if _p_b not in _sys_b.path:
        _sys_b.path.insert(0, _p_b)
import konfig  # noqa: E402,F401
import retry  # noqa: E402,F401
import tulis  # noqa: E402,F401
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core
import fp_profile


def _login(pg, mail, pw, timeout=45):
    """Login resmi via UI — True bila berhasil masuk (mail/apps)."""
    pg.goto("https://account.proton.me/login", wait_until="domcontentloaded", timeout=45000)
    time.sleep(3)
    pg.locator("#username, input[type=email], input[type=text]").first.fill(mail)
    pg.locator("#password, input[type=password]").first.fill(pw)
    pg.locator("button[type=submit]").first.click()
    akhir = time.time() + timeout
    while time.time() < akhir:
        time.sleep(2)
        u = pg.url
        if any(s in u for s in ("/mail", "/inbox", "/apps", "/u/")):
            return True
        t = (pg.evaluate("() => (document.body.innerText||'').slice(0,300)") or "").lower()
        if "suspend" in t or "not correct" in t:
            return False
    return "/apps" in pg.url or "/mail" in pg.url


def ambil_otp(mail, pw, dari="github", pola=r"\b(\d{6})\b", ulang=6, jeda=15):
    """Kembalikan kode OTP (string) dari email terbaru pengirim `dari`; None bila
    tidak ketemu. Polling `ulang` kali tiap `jeda` detik."""
    core.pembersihan() if False else None
    fp_profile.ensure_display()
    prof = fp_profile.build_profile()
    launch = dict(executable_path="/usr/bin/google-chrome", headless=False,
                  args=fp_profile.chrome_args(prof))
    ruter = core.start_router(8089, {"proxy": "", "auth": ""})
    for _ in range(20):
        if core.router_alive(8089):
            break
        time.sleep(0.5)
    launch["proxy"] = {"server": "http://127.0.0.1:8089"}
    with fp_profile.sync_playwright() as p:
        b = p.chromium.launch(**launch)
        try:
            ctx = b.new_context(locale=prof["locale"], timezone_id="Europe/Berlin",
                                viewport=prof["viewport"], user_agent=prof["ua"],
                                ignore_https_errors=True)
            pg = ctx.new_page()
            pg.add_init_script(fp_profile.init_script(prof))
            if not _login(pg, mail, pw):
                print("[otp] login gagal (suspend/salah password?)")
                return None
            pg.goto("https://account.proton.me/mail/inbox", wait_until="domcontentloaded",
                    timeout=45000)
            time.sleep(7)
            try:  # cari pengirim lewat search bila tersedia
                cari = pg.locator("input[type=search], input[placeholder*='search' i]").first
                if cari.count():
                    cari.fill(dari)
                    cari.press("Enter")
                    time.sleep(6)
            except Exception:
                pass
            for percobaan in range(max(1, int(ulang))):
                try:  # buka pesan teratas yang menyebut pengirim
                    baris = pg.get_by_text(dari, exact=False).first
                    baris.click(timeout=8000)
                    time.sleep(5)
                except Exception:
                    pass
                try:
                    isi = pg.evaluate("() => (document.querySelector('[role=main]')||document.body).innerText")
                except Exception:
                    isi = ""
                m = re.search(pola, isi)
                if m:
                    print(f"[otp] kode ketemu: {m.group(1)}")
                    return m.group(1)
                print(f"[otp] percobaan {percobaan + 1}: belum ada kode ({dari})")
                try:  # kembali ke list utk refresh
                    pg.goto("https://account.proton.me/mail/inbox", wait_until="domcontentloaded",
                            timeout=45000)
                    time.sleep(5)
                except Exception:
                    pass
                time.sleep(jeda)
            return None
        finally:
            try:
                b.close()
            except Exception:
                pass
            try:
                ruter.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mail", required=True)
    ap.add_argument("--pass", dest="pw", required=True)
    ap.add_argument("--dari", default="github")
    ap.add_argument("--pola", default=r"\b(\d{6})\b")
    ap.add_argument("--ulang", type=int, default=6)
    ap.add_argument("--jeda", type=int, default=15)
    a = ap.parse_args()
    kode = ambil_otp(a.mail, a.pw, dari=a.dari, pola=a.pola, ulang=a.ulang, jeda=a.jeda)
    print("HASIL:", kode)
