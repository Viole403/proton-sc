#!/usr/bin/env python3
"""github/github_register.py — orkestrasi pendaftaran akun GitHub lengkap.

Alur (berdasarkan penelitian github.com/signup + ptraced/GithubPy):

  1. Ambil email Proton dari results/registered.txt (belum dipakai untuk GitHub)
  2. Gate browser (cdp_gate) → atasi DataDome interstitial → /signup
  3. Panen token form (authenticity_token, timestamp, timestamp_secret, honeypot)
  4. Validasi email/password/username (C5-C8 lewat gh_api)
  5. CAPTCHA FunCaptcha (Arkose Labs) — solved via cf-solver HTTP API
  6. POST /signup → 302 ke halaman verifikasi email
  7. Ambil kode launch 8-digit dari inbox Proton (proton_api.baca_otp)
  8. POST /account_verifications (launch_code[] x8) → akun aktif
  9. Simpan hasil ke results/github_registered.txt

REQUIRED ENV:
  * data/proxies.txt — proxy residential per akun (wajib, DataDome ketat)
  * GH_PROXY atau BRUTALISM_PROXY_FILE — override proxy GitHub
  * CF_SOLVER_URL — URL cf-solver FastAPI (default http://localhost:8877)
  * Proton account sudah terdaftar di results/registered.txt

OUTPUT: results/github_registered.txt (email|pass|username|totp_secret|recovery_codes)
"""
from __future__ import annotations

import os
import random
import re
import secrets
import string
import time
from pathlib import Path

# brutalism: jalur lintas-folder
import os as _os_b, sys as _sys_b
for _d_b in ("inti", "proton", "github", "cb", "cek", "vendor"):
    _p_b = _os_b.path.join(
        _os_b.path.dirname(_os_b.path.dirname(_os_b.path.abspath(__file__))), _d_b)
    if _p_b not in _sys_b.path:
        _sys_b.path.insert(0, _p_b)

import konfig  # noqa: E402,F401
import retry  # noqa: E402,F401
import tulis  # noqa: E402,F401

from github import gh_api  # noqa: E402,F401
try:
    from github import playwright_gate  # noqa: E402,F401
    HAS_PG = True
except Exception:
    HAS_PG = False

try:
    from curl_cffi import requests as creq
    HAS_CURL = True
except Exception:
    HAS_CURL = False

GITHUB = "https://github.com"
SIGNUP_URL = f"{GITHUB}/signup"
REGISTERED_F = konfig.RESULTS / "github_registered.txt"
CF_SOLVER_URL = os.environ.get("CF_SOLVER_URL", "http://localhost:8877")

_ARKOSE_PUBKEY_RE = re.compile(
    r'data-publickey="([^"]+)"|public.?key.?[:=]\s*["\']([^"\']+)["\']|'
    r'arkose[_-]?key[=:]\s*["\']([^"\']+)["\']|'
    r'funCaptcha[_-]?key[=:]\s*["\']([^"\']+)["\']',
    re.I,
)


def _cek_cf_solver():
    """Cek apakah cf-solver FastAPI siap (GET /health)."""
    if not HAS_CURL:
        return False
    try:
        r = creq.get(f"{CF_SOLVER_URL}/health", timeout=5, impersonate="chrome157")
        return r.status_code == 200
    except Exception:
        return False


def _ecak_public_key(html):
    """Cari Arkose/FunCaptcha public key dari HTML signup."""
    m = _ARKOSE_PUBKEY_RE.search(html or "")
    if m:
        for g in m.groups():
            if g and len(g) > 10:
                return g
    return ""


def _solve_arkose(public_key, page_url, proxy=None, timeout_s=120):
    """Panggil cf-solver POST /solve dengan type=arkose.

    Return token string atau None bila gagal."""
    if not HAS_CURL:
        return None, "curl-cffi-tidak-tersedia"
    if not public_key:
        return None, "public-key-kosong"
    try:
        pr = None
        if proxy:
            pr = gh_api.ke_url(proxy)
            if pr:
                pr = {"https": pr, "http": pr}
        r = creq.post(
            f"{CF_SOLVER_URL}/solve",
            json={
                "type": "arkose",
                "public_key": public_key,
                "url": page_url,
                "proxy": proxy,
                "game_type": "4",
                "timeout_s": timeout_s,
            },
            headers={"Content-Type": "application/json"},
            timeout=timeout_s + 10,
            impersonate="chrome157",
            proxies=pr,
        )
        if r.status_code != 200:
            return None, f"cf-solver-http-{r.status_code}"
        data = r.json() or {}
        if not data.get("solved"):
            return None, f"cf-solver-gagal:{data.get('error','tidak-diketahui')}"
        return data.get("token"), None
    except Exception as e:
        return None, f"cf-solver-exc:{type(e).__name__}:{str(e)[:60]}"


def _username_dari_email(mail):
    """Username dari local-part email + digit acak (anti-tabrak)."""
    local = mail.split("@")[0]
    clean = "".join(c for c in local if c.isalnum() or c == "-")
    return clean[:20] + "".join(secrets.choice(string.digits) for _ in range(2))


def _password_acak():
    abjad = string.ascii_letters + string.digits + "!@#$%^&*_-+="
    while True:
        pw = "".join(secrets.choice(abjad) for _ in range(16 + secrets.randbelow(8)))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and sum(c.isdigit() for c in pw) >= 2):
            return pw


_NAMA_DEPAN = ["Adi", "Budi", "Citra", "Dedi", "Eka", "Fajar", "Gita", "Hana",
               "Indah", "Joko", "Kirana", "Luki", "Maya", "Nina", "Oki", "Putri",
               "Rama", "Sari", "Tono", "Uma", "Vina", "Wawan", "Yoga", "Zara",
               "Arif", "Bella", "Cahy", "Dewi", "Fika", "Gede", "Hari", "Ika"]
_NAMA_BELAKANG = ["Pratama", "Saputra", "Wijaya", "Kusuma", "Putra", "Lestari",
                  "Astuti", "Hidayat", "Ningrum", "Setiawan", "Rahayu", "Mahendra",
                  "Suharto", "Permana", "Susanti", "Manurung", "Sapto", "Kurnia",
                  "Hidayanto", "Wijaya", "Firmansyah", "Rahardjo", "Muliawan",
                  "Nugroho", "Hidayat", "Suryono", "Darajat", "Efendi", "Gunawan"]


def _nama_tampilkan():
    return f"{secrets.choice(_NAMA_DEPAN)} {secrets.choice(_NAMA_BELAKANG)}"


def _email_proton_tersedia():
    """Ambil satu email Proton dari results/registered.txt yang belum dipakai
    untuk GitHub (cek results/github_registered.txt)."""
    try:
        daftar_proton = []
        with open(konfig.REGISTERED, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or "|" not in ln:
                    continue
                parts = ln.split("|")
                if len(parts) >= 2:
                    daftar_proton.append(parts[0])
    except Exception:
        daftar_proton = []

    try:
        dipakai = set()
        with open(REGISTERED_F, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or "|" not in ln:
                    continue
                dipakai.add(ln.split("|")[0])
    except Exception:
        dipakai = set()

    tersedia = [m for m in daftar_proton if m not in dipakai]
    if not tersedia:
        return None, None
    mail = secrets.choice(tersedia)
    # cari password dari registered.txt (kolom 2)
    pw = None
    try:
        with open(konfig.REGISTERED, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.strip().startswith(mail + "|"):
                    pw = ln.strip().split("|")[1]
                    break
    except Exception:
        pass
    return mail, pw


def _ambil_kode_launch(mail, pw, t0, budget=240):
    """Poll kode launch 8-digit dari inbox Proton (via proton_api.baca_otp)."""
    try:
        import proton_api  # noqa: F401
    except Exception:
        return None, "proton-api-tidak-tersedia"
    try:
        # pola kode launch GitHub: 8 digit
        kode = proton_api.baca_otp(mail, pw, dari="github",
                                   pola=r"\b(\d{8})\b",
                                   ulang=8, jeda=6.0, sejak=t0,
                                   proxy=None)
        return kode, None
    except Exception as e:
        return None, f"otp-gagal:{type(e).__name__}:{str(e)[:60]}"


def _proxy_untuk_playwright(px):
    """http://user:pass@host:port -> host:port:user:pass untuk playwright_gate."""
    if not px:
        return None
    if px.startswith("http://"):
        px = px[7:]
    if "@" in px:
        auth, hostport = px.split("@", 1)
        parts = auth.split(":", 1)
        user = parts[0] if len(parts) > 0 else ""
        pw = parts[1] if len(parts) > 1 else ""
        return f"{hostport}:{user}:{pw}"
    return px


def _proxy_untuk_cf_solver(px):
    """http://user:pass@host:port -> user:pass@host:port untuk cf-solver."""
    if not px:
        return None
    if px.startswith("http://"):
        return px[7:]
    return px


def _simpan_hasil(mail, pw, user, totp_secret, recovery_codes):
    REGISTERED_F.parent.mkdir(parents=True, exist_ok=True)
    baris = f"{mail}|{pw}|{user}|{totp_secret or ''}|{recovery_codes or ''}\n"
    with open(REGISTERED_F, "a", encoding="utf-8") as f:
        f.write(baris)
    return baris.strip()


def daftar_akun_github(proxy=None, headless=True, negara="ID",
                       email=None, pw=None, user=None,
                       delay_anti_suspend=0):
    """Daftar satu akun GitHub lengkap (termasuk verifikasi email).

    Return dict: {email, user, pw, sukses, alasan, totp_secret, recovery_codes,
                  launch_code, status}
    """
    log = {"email": email, "user": user, "sukses": False,
           "alasan": "", "status": "", "totp_secret": "", "recovery_codes": "",
           "launch_code": None}

    # --- 1. Persiapan kredensial ---
    if not email:
        mail, pw_proton = _email_proton_tersedia()
        if not mail:
            log["alasan"] = "email-proton-tidak-tersedia"
            log["status"] = "email-tidak-tersedia"
            return log
        email = mail
        # pakai password Proton dari registered.txt jika ada
        if pw_proton and not pw:
            pw = pw_proton
    if not pw:
        pw = _password_acak()
    if not user:
        user = _username_dari_email(email)
    log.update(email=email, user=user, pw=pw)

    if delay_anti_suspend > 0:
        time.sleep(delay_anti_suspend * 60 * random.uniform(0.5, 1.5))

    # --- 2. Gate browser (DataDome + FunCaptcha) via Playwright ---
    sesi = None
    r = None
    tok = None
    if HAS_PG:
        try:
            b = playwright_gate.pinjam_browser(proxy=proxy, headless=headless)
            ctx = b.new_context()
            page = ctx.new_page()
            page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=30000)
            # tunggu form muncul / DataDome selesai
            page.wait_for_selector("form", timeout=20000)
            html = page.content()
            # panen cookie + token
            cookies = ctx.cookies()
            tok = gh_api.form_tokens(html)
            # simpan cookie ke sesi HTTP
            sesi = gh_api.GhSesi(proxy=proxy, izinkan_direct=True)
            for ck in cookies:
                try:
                    sesi.s.cookies.set(ck["name"], ck["value"],
                                       domain=ck.get("domain") or "github.com",
                                       path=ck.get("path") or "/")
                except Exception:
                    pass
            r = type("R", (), {"text": html, "status_code": 200})()
            ctx.close()
            b.close()
        except Exception as e:
            log["alasan"] = f"gate-gagal:{type(e).__name__}:{str(e)[:80]}"
            log["status"] = "gate-gagal"
            return log
    else:
        # fallback: HTTP-only via gh_api kalibrasi
        pool = [gh_api._sederhana(proxy)] if proxy else []
        sesi, r, _ = gh_api.kalibrasi_sesi(pool, maks=5, email=email)
        if not sesi:
            log["alasan"] = "gate-http-gagal"
            log["status"] = "gate-gagal"
            return log
        tok = gh_api.form_tokens(r.text or "")

    if not tok or not tok.get("authenticity_token"):
        log["alasan"] = "token-form-tidak-ditemukan"
        log["status"] = "token-gagal"
        return log

    referer = SIGNUP_URL

    # --- 3. Validasi C5-C8 ---
    try:
        r1 = sesi.c5_email(email, tok, referer)
        gh_api.pace(0.3, 0.7)
        r2 = sesi.c6_password(pw, tok, referer)
        gh_api.pace(0.3, 0.7)
        r3 = sesi.c7_username(user, referer)
    except Exception as e:
        log["alasan"] = f"validasi-gagal:{type(e).__name__}:{str(e)[:60]}"
        log["status"] = "validasi-gagal"
        return log

    # --- 4. CAPTCHA FunCaptcha (Arkose Labs) via cf-solver ---
    octo_token = os.environ.get("OCTOCAPTCHA_TOKEN", "")
    if not octo_token and _cek_cf_solver():
        arkose_pubkey = _ecak_public_key(html)
        if arkose_pubkey:
            px_cf = _proxy_untuk_cf_solver(proxy)
            octo_token, err = _solve_arkose(
                arkose_pubkey, referer, proxy=px_cf, timeout_s=120)
            if err:
                log["catatan_captcha"] = err
        else:
            log["catatan_captcha"] = "arkose-public-key-tidak-ditemukan"
    elif not octo_token:
        log["catatan_captcha"] = "cf-solver-tidak-tersedia"

    # --- 5. POST /signup ---
    try:
        rs = sesi.c8_signup(email, user, pw, tok, referer, country=negara,
                            octo=octo_token)
    except Exception as e:
        log["alasan"] = f"submit-gagal:{type(e).__name__}:{str(e)[:60]}"
        log["status"] = "submit-gagal"
        return log

    if rs.status_code not in (301, 302):
        log["alasan"] = f"signup-http-{rs.status_code}"
        log["status"] = "signup-gagal"
        body = rs.text or ""
        if "captcha" in body.lower() or "octocaptcha" in body.lower():
            log["alasan"] += "-captcha-dibutuhkan"
        return log

    # --- 6. Verifikasi email (kode launch 8-digit dari Proton) ---
    otp_url = None
    loc = rs.headers.get("location") or ""
    if loc.startswith("/"):
        otp_url = GITHUB + loc
    elif loc.startswith("http"):
        otp_url = loc

    kode = None
    if otp_url:
        t0 = time.time()
        kode, err = _ambil_kode_launch(email, pw, t0, budget=180)
        log["launch_code"] = kode
        if err:
            log["alasan"] = f"otp:{err}"
            log["status"] = "otp-gagal"
            return log

    if not kode:
        log["alasan"] = "kode-launch-tidak-diterima"
        log["status"] = "otp-gagal"
        return log

    # --- 7. POST /account_verifications ---
    try:
        meta_otp = gh_api.gh_fetch_nonce(r.text or "") if r else {}
        rs2 = sesi.c11_submit_otp(kode, meta_otp, otp_url or referer,
                                  return_to="https://github.com/dashboard")
    except Exception as e:
        log["alasan"] = f"verifikasi-gagal:{type(e).__name__}:{str(e)[:60]}"
        log["status"] = "verifikasi-gagal"
        return log

    if rs2.status_code not in (200, 302):
        log["alasan"] = f"verifikasi-http-{rs2.status_code}"
        log["status"] = "verifikasi-gagal"
        return log

    log["sukses"] = True
    log["status"] = "terverifikasi"
    log["totp_secret"] = ""
    log["recovery_codes"] = ""

    # --- 8. Login ulang + TOTP 2FA (opsional, via browser-state cookie) ---
    # TOTP setup membutuhkan interaksi browser penuh — ditangani secara terpisah
    # atau dilewati bila tidak diperlukan. Sesi cookie dari `sesi` bisa dioper
    # ke Playwright untuk melanjutkan 2FA.

    # --- 9. Simpan hasil ---
    _simpan_hasil(email, pw, user, log["totp_secret"], log["recovery_codes"])
    return log


def daftar_batch(jumlah=1, proxy_pool=None, delay_menit=5,
                 headless=True, negara="ID"):
    """Daftar batch akun GitHub dengan jeda anti-suspend."""
    hasil = []
    for i in range(jumlah):
        px = None
        if proxy_pool:
            px = proxy_pool[i % len(proxy_pool)]
        elif proxy_pool is None:
            px = gh_api.ke_url(gh_api.proksi_pertama())
        r = daftar_akun_github(proxy=px, headless=headless, negara=negara,
                               delay_anti_suspend=delay_menit)
        hasil.append(r)
        if not r["sukses"] and i < jumlah - 1:
            print(f"[github-register] akun {i+1}/{jumlah} gagal: {r['alasan']}")
        time.sleep(2)
    return hasil
