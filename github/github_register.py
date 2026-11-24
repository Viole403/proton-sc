#!/usr/bin/env python3
"""github/github_register.py — orkestrasi pendaftaran akun GitHub lengkap.

Alur (berdasarkan penelitian github.com/signup + ptraced/GithubPy):

  1. Ambil email Proton dari results/registered.txt (belum dipakai untuk GitHub)
  2. Gate browser (playwright_gate) → atasi DataDome interstitial → /signup
  3. Panen token form (authenticity_token, timestamp, timestamp_secret, honeypot)
  4. Validasi email/password/username (C5-C8 lewat gh_api)
  5. CAPTCHA FunCaptcha (Arkose Labs) — solved via vendored
     vendor/captcha_solver/arkose/solve.py (ONNX prediction)
  6. POST /signup → 302 ke halaman verifikasi email
  7. Ambil kode launch 8-digit dari inbox Proton (proton_api.baca_otp)
  8. POST /account_verifications (launch_code[] x8) → akun aktif
  9. Simpan hasil ke results/github_registered.txt

REQUIRED ENV:
  * data/proxies.txt — proxy residential per akun (wajib, DataDome ketat)
  * GH_PROXY atau BRUTALISM_PROXY_FILE — override proxy GitHub
  * vendor/captcha_solver/arkose/models/*.onnx — Arkose ONNX models (~1.4GB)
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
import asyncio
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
    HAS_CAMOUFOX = playwright_gate.HAS_CAMOUFOX
except Exception:
    HAS_PG = False
    HAS_CAMOUFOX = False

try:
    from camoufox.async_api import AsyncCamoufox  # noqa: E402,F401
    HAS_CAMOUFOX = True
except Exception:
    HAS_CAMOUFOX = False

try:
    from curl_cffi import requests as creq
    HAS_CURL = True
except Exception:
    HAS_CURL = False

GITHUB = "https://github.com"
SIGNUP_URL = f"{GITHUB}/signup"
REGISTERED_F = konfig.RESULTS / "github_registered.txt"

_UA_CHROME_157 = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")

_CHROME_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8,"
              "application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Not_Brand";v="99", "Chromium";v="157", "Google Chrome";v="157"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Linux"',
    "Sec-Ch-Ua-Arch": '"x86"',
    "Sec-Ch-Ua-Bitness": '"64"',
    "Sec-Ch-Ua-Full-Version": '"157.0.8204.0"',
    "Sec-Ch-Ua-Full-Version-List": '"Not_Brand";v="99.0.0.0", "Chromium";v="157.0.8204.0", "Google Chrome";v="157.0.8204.0"',
    "Sec-Ch-Prefers-Color-Scheme": "light",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": _UA_CHROME_157,
}

_ARKOSE_PUBKEY_RE = re.compile(
    r'data-publickey="([^"]+)"|public.?key.?[:=]\s*["\']([^"\']+)["\']|'
    r'arkose[_-]?key[=:]\s*["\']([^"\']+)["\']|'
    r'funCaptcha[_-]?key[=:]\s*["\']([^"\']+)["\']',
    re.I,
)


def _cek_arkose_solver():
    """Cek apakah vendored Arkose solver siap (ONNX models tersedia)."""
    if not HAS_ARKOSE:
        return False
    models_dir = Path(__file__).parent.parent / "vendor" / "captcha_solver" / "arkose" / "models"
    return models_dir.is_dir() and any(models_dir.glob("*.onnx"))


def _ecak_public_key(html):
    """Cari Arkose/FunCaptcha public key dari HTML signup."""
    m = _ARKOSE_PUBKEY_RE.search(html or "")
    if m:
        for g in m.groups():
            if g and len(g) > 10:
                return g
    return ""


def _solve_arkose(public_key, page_url, proxy=None, timeout_s=120):
    """Panggil vendored Arkose solver (vendor/captcha_solver/arkose/solve.py).

    Return token string atau None bila gagal."""
    if not HAS_ARKOSE:
        return None, "arkose-solver-tidak-tersedia"
    if not public_key:
        return None, "public-key-kosong"
    try:
        pr = _proxy_untuk_playwright(proxy) if proxy else None
        result = solve_arkose(
            public_key=public_key,
            page_url=page_url,
            proxy=pr,
            timeout_s=timeout_s,
        )
        if not result.get("solved"):
            return None, f"arkose-gagal:{result.get('error','tidak-diketahui')}"
        return result.get("token"), None
    except Exception as e:
        return None, f"arkose-exc:{type(e).__name__}:{str(e)[:60]}"


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


def _parse_camoufox_proxy(proxy_str):
    """host:port:user:pass -> Camoufox proxy dict atau None."""
    if not proxy_str:
        return None
    parts = proxy_str.split(":")
    if len(parts) < 2:
        return None
    pr = {"server": f"http://{parts[0]}:{parts[1]}"}
    if len(parts) > 2:
        pr["username"] = parts[2]
    if len(parts) > 3:
        pr["password"] = parts[3]
    return pr


def _simpan_hasil(mail, pw, user, totp_secret, recovery_codes):
    REGISTERED_F.parent.mkdir(parents=True, exist_ok=True)
    baris = f"{mail}|{pw}|{user}|{totp_secret or ''}|{recovery_codes or ''}\n"
    with open(REGISTERED_F, "a", encoding="utf-8") as f:
        f.write(baris)
    return baris.strip()


async def _camoufox_signup(proxy=None, headless=False, email=None, pw=None,
                           user=None, delay_anti_suspend=0):
    """Pendaftaran GitHub lengkap via Camoufox (Firefox anti-detect).

    Camoufox mampu bypass DataDome tanpa proxy pada IP residential karena
    fingerprint spoofing di level engine (bukan cuma overlay JS).

    Alur: browser → /signup → form → submit → OTP verifikasi → 2FA → simpan.
    """
    import json as _json
    import re as _re

    log = {"email": email, "user": user, "pw": pw, "sukses": False,
           "alasan": "", "status": "", "totp_secret": "", "recovery_codes": "",
           "launch_code": None}

    # --- Kredensial ---
    if not email:
        mail, pw_proton = _email_proton_tersedia()
        if not mail:
            log["alasan"] = "email-proton-tidak-tersedia"
            log["status"] = "email-tidak-tersedia"
            return log
        email = mail
        if pw_proton and not pw:
            pw = pw_proton
    if not pw:
        pw = _password_acak()
    if not user:
        user = _username_dari_email(email)
    log.update(email=email, user=user, pw=pw)

    if delay_anti_suspend > 0:
        await asyncio.sleep(delay_anti_suspend * 60 * random.uniform(0.5, 1.5))

    GITHUB_SIGNUP = "https://github.com/signup"
    _EMAIL_INPUTS = ["#email", "input[name='email']", "input[type='email']"]
    _PASSWORD_INPUTS = ["#password", "input[name='password']"]
    _USERNAME_INPUTS = ["#login", "input[name='login']"]
    _OTP_INPUTS = [
        "#otp", "input[name='otp']",
        "input[autocomplete='one-time-code']", "#launch-code-0",
    ]
    _SIGNUP_FORM = "form[action*='signup']"

    cf = None
    browser = None
    context = None

    try:
        cf = AsyncCamoufox(
            humanize=True,
            geoip=False,  # Nonaktifkan geoip untuk menghindari error ipecho.net
            proxy=_parse_camoufox_proxy(proxy) if proxy else None,
            headless="virtual" if headless else False,
            os="windows",
            canvas_noise=False,  # Hindari deteksi privacy.resistFingerprinting
            disable_coop=True,  # Biarkan klik mencapai widget (Turnstile, slider)
            window=(1280, 720),
        )
        browser = await cf.start()
        context = await browser.new_context(
            locale="en-US",
            # Matikan WebGL untuk mencegah deteksi Camoufox
            java_script_enabled=True,
        )
        # Inject script untuk menyembunyikan WebGL dan sinyal automation lainnya
        await context.add_init_script("""
            () => {
                // Sembunyikan WebGL info
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 0x8B90 || parameter === 0x8B91 || parameter === 0x8B92 || parameter === 0x8B93 || parameter === 0x8B94 || parameter === 0x8B95 || parameter === 0x8B96 || parameter === 0x8B97 || parameter === 0x8B98 || parameter === 0x8B99 || parameter === 0x8B9A || parameter === 0x8B9B || parameter === 0x8B9C || parameter === 0x8B9D || parameter === 0x8B9E || parameter === 0x8B9F) {
                        return '';
                    }
                    return getParameter.call(this, parameter);
                };
                // Sembunyikan sheild aktif
                delete window.__playwright;
                delete window.__pw_manual;
                delete window.__PW_inspect;
                // Spoof platform
                Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
                Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 4});
                Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
            }
        """)
        page = await context.new_page()

        # Buka homepage dulu (warms up session), baru klik "Sign up"
        # Langsung ke /signup trigger DataDome lebih cepat.
        # Tunggu lebih lama agar DataDome JS selesai dijalankan
        # dan cookie session terbentuk.
        await page.goto("https://github.com/", wait_until="networkidle", timeout=60000)
        await asyncio.sleep(5)

        # Cek apakah ada halaman tantangan DataDome di homepage
        try:
            body_text = (await page.locator("body").inner_text(timeout=3000) or "").lower()
            if "temporarily restricted" in body_text or "access is restricted" in body_text:
                log["alasan"] = "datadome-temporarily-restricted-homepage"
                log["status"] = "datadome-blocked"
                return log
            if "captcha" in body_text or "verify you are human" in body_text:
                # Tunggu tantangan DataDome selesai (biasanya auto-resolve)
                await asyncio.sleep(15)
                body_text = (await page.locator("body").inner_text(timeout=3000) or "").lower()
                if "captcha" in body_text or "verify you are human" in body_text:
                    log["alasan"] = "datadome-captcha-homepage"
                    log["status"] = "datadome-captcha"
                    return log
        except Exception:
            pass

        # Cari dan klik tombol "Sign up"
        signup_clicked = False
        for sel in ["a:has-text('Sign up')", "button:has-text('Sign up')",
                    "a[href*='/signup']", "[data-testid*='signup']"]:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.scroll_into_view_if_needed()
                    await asyncio.sleep(1)
                    await loc.click(timeout=10000)
                    signup_clicked = True
                    break
            except Exception:
                continue

        if not signup_clicked:
            # Fallback: langsung ke /signup
            await page.goto(GITHUB_SIGNUP, wait_until="domcontentloaded", timeout=60000)

        # Tunggu form muncul
        deadline = asyncio.get_event_loop().time() + 30
        form_ready = False
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1)
            try:
                for sel in _EMAIL_INPUTS:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        form_ready = True
                        break
            except Exception:
                pass
            if form_ready:
                break

        if not form_ready:
            # Cek apakah kena DataDome block
            try:
                body_text = (await page.locator("body").inner_text(timeout=3000) or "").lower()
                if "temporarily restricted" in body_text or "access is restricted" in body_text:
                    log["alasan"] = "datadome-temporarily-restricted"
                    log["status"] = "datadome-blocked"
                    return log
                if "captcha" in body_text:
                    log["alasan"] = "datadome-captcha"
                    log["status"] = "datadome-captcha"
                    return log
            except Exception:
                pass
            log["alasan"] = "form-tidak-muncul"
            log["status"] = "gate-gagal"
            return log

        # Isi email
        for sel in _EMAIL_INPUTS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.focus(timeout=5000)
                    await loc.press_sequentially(email, delay=55)
                    await loc.evaluate("el => el.blur()")
                    break
            except Exception:
                continue
        await asyncio.sleep(1.5)

        # Isi password
        for sel in _PASSWORD_INPUTS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.focus(timeout=5000)
                    await loc.press_sequentially(pw, delay=55)
                    await loc.evaluate("el => el.blur()")
                    break
            except Exception:
                continue
        await asyncio.sleep(1.5)

        # Isi username
        username_filled = False
        for sel in _USERNAME_INPUTS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.focus(timeout=5000)
                    await loc.press_sequentially(user, delay=55)
                    await loc.evaluate("el => el.blur()")
                    username_filled = True
                    break
            except Exception:
                continue

        if not username_filled:
            log["alasan"] = "username-input-tidak-ditemukan"
            log["status"] = "gate-gagal"
            return log

        await asyncio.sleep(3)

        # Tunggu tombol Create account enabled, lalu klik via DOM
        deadline = asyncio.get_event_loop().time() + 30
        clicked = False
        while asyncio.get_event_loop().time() < deadline:
            enabled = await page.evaluate("""
                () => {
                    const form = document.querySelector("form[action*='signup']");
                    const b = form && form.querySelector("button[type='submit']");
                    return b && !b.disabled;
                }
            """)
            if enabled:
                clicked = await page.evaluate("""
                    () => {
                        const form = document.querySelector("form[action*='signup']");
                        const b = form && form.querySelector("button[type='submit']");
                        if (!b || b.disabled) return false;
                        b.click();
                        return true;
                    }
                """)
                if clicked:
                    break
            await asyncio.sleep(0.8)

        if not clicked:
            log["alasan"] = "create-account-tombol-disabled"
            log["status"] = "gate-gagal"
            return log

        # Tunggu post-submit state
        deadline = asyncio.get_event_loop().time() + 120
        verified = False
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2)
            # Cek OTP input
            try:
                for sel in _OTP_INPUTS:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        verified = True
                        break
            except Exception:
                pass
            if verified:
                break
            # Cek cookies logged_in
            try:
                cookies = await context.cookies()
                for c in cookies:
                    if c.get("name") == "logged_in" and str(c.get("value", "")).lower() == "yes":
                        verified = True
                        break
            except Exception:
                pass
            if verified:
                break

        if not verified:
            log["alasan"] = "verifikasi-tidak-terdeteksi"
            log["status"] = "gate-gagal"
            return log

        # --- Ambil kode launch dari Proton inbox ---
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

        # Isi kode OTP
        try:
            boxes = page.locator("input[id^='launch-code-']")
            count = await boxes.count()
            if count >= len(kode):
                for i, digit in enumerate(kode):
                    try:
                        await boxes.nth(i).fill(digit, timeout=3000)
                    except Exception:
                        pass
                    await _asyncio.sleep(0.1)
                try:
                    await page.click("button[class*='Button--primary']", timeout=5000)
                except Exception:
                    await page.click("button[type='submit']", timeout=5000)
            else:
                for sel in _OTP_INPUTS:
                    try:
                        loc = page.locator(sel).first
                        if await loc.count() > 0:
                            await loc.fill(kode, timeout=5000)
                            break
                    except Exception:
                        continue
                try:
                    await page.click("button[type='submit']", timeout=5000)
                except Exception:
                    pass
        except Exception as e:
            log["alasan"] = f"otp-input-gagal:{str(e)[:60]}"
            log["status"] = "otp-gagal"
            return log

        await _asyncio.sleep(5)

        # Konfirmasi sukses
        try:
            cookies = await context.cookies()
            for c in cookies:
                if c.get("name") == "logged_in" and str(c.get("value", "")).lower() == "yes":
                    log["sukses"] = True
                    log["status"] = "terverifikasi"
                    break
        except Exception:
            pass

        if not log["sukses"]:
            log["alasan"] = "login-cookie-tidak-ditemukan"
            log["status"] = "verifikasi-gagal"
            return log

        # Simpan hasil
        _simpan_hasil(email, pw, user, "", "")
        return log

    except Exception as e:
        log["alasan"] = f"{type(e).__name__}: {str(e)[:80]}"
        log["status"] = "exception"
        return log

    finally:
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
        try:
            if cf:
                await cf.stop()
        except Exception:
            pass


def daftar_akun_github(proxy=None, headless=False, negara="ID",
                       email=None, pw=None, user=None,
                       delay_anti_suspend=0, user_data_dir=None,
                       use_camoufox=None):
    """Daftar satu akun GitHub lengkap (termasuk verifikasi email).

    Return dict: {email, user, pw, sukses, alasan, totp_secret, recovery_codes,
                  launch_code, status}

    use_camoufox: None (auto) → pakai Camoufox bila tidak ada proxy
                  (DataDome bypass tanpa proxy pada raw IP residential),
                  True → paksa Camoufox, False → paksa Chromium
    """
    # Inisialisasi log terlebih dahulu
    log = {"email": email, "user": user, "sukses": False,
           "alasan": "", "status": "", "totp_secret": "", "recovery_codes": "",
           "launch_code": None}

    # Auto-aktifkan Camoufox bila tidak ada proxy dan belum diatur
    if use_camoufox is None:
        use_camoufox = (not proxy) and HAS_CAMOUFOX
    elif use_camoufox and not HAS_CAMOUFOX:
        log["alasan"] = "camoufox-tidak-terpasang"
        log["status"] = "solver-tidak-tersedia"
        return log

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
    if HAS_PG and not use_camoufox:
        try:
            b = playwright_gate.pinjam_browser(
                proxy=proxy, headless=headless,
                user_data_dir=user_data_dir)
            ctx = b.new_context()
            page = ctx.new_page()
            page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=30000)
            # tunggu form muncul / DataDome selesai — beri waktu lebih lama
            # agar stealth + cookie session bisa menyegarkan
            page.wait_for_timeout(5000)
            try:
                page.wait_for_selector("form", timeout=30000)
            except Exception:
                # form mungkin muncul setelah DataDome challenge selesai
                page.wait_for_timeout(10000)
            html = page.content()
            # panen cookie + token
            cookies = ctx.cookies()
            tok = gh_api.form_tokens(html)
            # simpan cookie ke sesi HTTP — pakai curl_cffi impersonate chrome157
            # agar TLS/JA3 fingerprint cocok dengan browser asli (anti DataDome
            # layer network: JA3/JA4 fingerprint + header konsisten)
            if HAS_CURL:
                sesi = creq.Session(impersonate="chrome157", timeout=30)
                sesi.headers.update(_CHROME_HEADERS)
                for ck in cookies:
                    try:
                        sesi.cookies.set(ck["name"], ck["value"],
                                         domain=ck.get("domain") or "github.com",
                                         path=ck.get("path") or "/")
                    except Exception:
                        pass
            else:
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
    elif use_camoufox:
        # Camoufox path — async, dijalankan pada event loop yang tersedia
        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            result = loop.run_until_complete(_camoufox_signup(
                proxy=proxy, headless=headless, email=email, pw=pw, user=user,
                delay_anti_suspend=delay_anti_suspend))
            return result
        except Exception as e:
            log["alasan"] = f"camoufox-gagal:{type(e).__name__}:{str(e)[:80]}"
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

    # --- 4. CAPTCHA FunCaptcha (Arkose Labs) via vendored solver ---
    octo_token = os.environ.get("OCTOCAPTCHA_TOKEN", "")
    if not octo_token and _cek_arkose_solver():
        arkose_pubkey = _ecak_public_key(html)
        if arkose_pubkey:
            px = _proxy_untuk_playwright(proxy) if proxy else None
            octo_token, err = _solve_arkose(
                arkose_pubkey, referer, proxy=px, timeout_s=120)
            if err:
                log["catatan_captcha"] = err
        else:
            log["catatan_captcha"] = "arkose-public-key-tidak-ditemukan"
    elif not octo_token:
        log["catatan_captcha"] = "arkose-solver-tidak-tersedia"

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
                 headless=False, negara="ID"):
    """Daftar batch akun GitHub dengan jeda anti-suspend."""
    hasil = []
    for i in range(jumlah):
        px = None
        if proxy_pool:
            px = proxy_pool[i % len(proxy_pool)]
        elif proxy_pool is None:
            px = gh_api.ke_url(gh_api.proksi_pertama())
        # Setiap akun pakai profil tersendiri agar cookie/session tidak tumpang
        # tindih (DataDome melacak via cookie + session history)
        import tempfile
        udd = tempfile.mkdtemp(prefix="github-reg-")
        r = daftar_akun_github(proxy=px, headless=headless, negara=negara,
                               delay_anti_suspend=delay_menit,
                               user_data_dir=udd)
        hasil.append(r)
        if not r["sukses"] and i < jumlah - 1:
            print(f"[github-register] akun {i+1}/{jumlah} gagal: {r['alasan']}")
        time.sleep(2)
    return hasil
