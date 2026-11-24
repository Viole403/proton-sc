#!/usr/bin/env python3
"""github/github_register.py — pendaftaran akun GitHub dengan browser asli + stealth.

Alur anti-deteksi (berdasarkan penelitian DataDome 3-layer detection):

  Layer 1 — Network (TLS/IP/ASN): curl_cffi impersonate chrome150 + header lengkap
  Layer 2 — Device/Behavior (JS fingerprint + behavior): Camoufox/Playwright stealth,
      mouse movement, typing per karakter, jeda acak
  Layer 3 — Session Memory (cookie): profil persisten + warm-up di homepage dulu

Flow:
  1. Buka github.com homepage (warm-up session + cookie)
  2. Klik "Sign up" → /signup
  3. Isi email/password/username secara manusiawi
  4. Klik "Create account" → verifikasi email
  5. Ambil kode launch 8-digit dari inbox Proton
  6. POST verifikasi → akun aktif
  7. Simpan ke results/github_registered.txt

REQUIRED ENV:
  * Email Proton sudah ada di results/registered.txt (untuk kode launch)
  * vendor/captcha_solver/arkose/models/*.onnx (untuk CAPTCHA Arkose/FunCaptcha)
  * Proxy residential (opsional — IP Telkomsel biasanya cukup)

OUTPUT: results/github_registered.txt (email|pass|username|totp_secret|recovery_codes)
"""
from __future__ import annotations

import asyncio
import json as _json
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

_UA_CHROME_150 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/150.0.0.0 Safari/537.36")

_CHROME_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8,"
              "application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Not_A Brand";v="99", "Chromium";v="150", "Google Chrome";v="150"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Ch-Ua-Arch": '"x86"',
    "Sec-Ch-Ua-Bitness": '"64"',
    "Sec-Ch-Ua-Full-Version": '"150.0.7434.0"',
    "Sec-Ch-Ua-Full-Version-List": '"Not_A Brand";v="99.0.0.0", "Chromium";v="150.0.7434.0", "Google Chrome";v="150.0.7434.0"',
    "Sec-Ch-Prefers-Color-Scheme": "light",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": _UA_CHROME_150,
}

_ARKOSE_PUBKEY_RE = re.compile(
    r'data-publickey="([^"]+)"|public.?key.?[:=]\s*["\']([^"\']+)["\']|'
    r'arkose[_-]?key[=:]\s*["\']([^"\']+)["\']|'
    r'funCaptcha[_-]?key[=:]\s*["\']([^"\']+)["\']',
    re.I,
)

_NAMA_DEPAN = ["Adi", "Budi", "Citra", "Dedi", "Eka", "Fajar", "Gita", "Hana",
               "Indah", "Joko", "Kirana", "Luki", "Maya", "Nina", "Oki", "Putri",
               "Rama", "Sari", "Tono", "Uma", "Vina", "Wawan", "Yoga", "Zara",
               "Arif", "Bella", "Cahy", "Dewi", "Fika", "Gede", "Hari", "Ika"]
_NAMA_BELAKANG = ["Pratama", "Saputra", "Wijaya", "Kusuma", "Putra", "Lestari",
                  "Astuti", "Hidayat", "Ningrum", "Setiawan", "Rahayu", "Mahendra",
                  "Suharto", "Permana", "Susanti", "Manurung", "Sapto", "Kurnia",
                  "Hidayanto", "Wijaya", "Firmansyah", "Rahardjo", "Muliawan",
                  "Nugroho", "Hidayat", "Suryono", "Darajat", "Efendi", "Gunawan"]


def _username_dari_email(mail):
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


def _nama_tampilkan():
    return f"{secrets.choice(_NAMA_DEPAN)} {secrets.choice(_NAMA_BELAKANG)}"


def _email_proton_tersedia():
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


def _cek_arkose_solver():
    if not HAS_CAMOUFOX:
        return False
    models_dir = Path(__file__).parent.parent / "vendor" / "captcha_solver" / "arkose" / "models"
    return models_dir.is_dir() and any(models_dir.glob("*.onnx"))


def _ecak_public_key(html):
    m = _ARKOSE_PUBKEY_RE.search(html or "")
    if m:
        for g in m.groups():
            if g and len(g) > 10:
                return g
    return ""


def _solve_arkose(public_key, page_url, proxy=None, timeout_s=120):
    if not HAS_CAMOUFOX:
        return None, "arkose-solver-tidak-tersedia"
    if not public_key:
        return None, "public-key-kosong"
    try:
        from vendor.captcha_solver.arkose.solve import solve_arkose as _solve
        result = _solve(
            public_key=public_key,
            page_url=page_url,
            proxy=proxy,
            timeout_s=timeout_s,
        )
        if not result.get("solved"):
            return None, f"arkose-gagal:{result.get('error','tidak-diketahui')}"
        return result.get("token"), None
    except Exception as e:
        return None, f"arkose-exc:{type(e).__name__}:{str(e)[:60]}"


def _proxy_untuk_cf_solver(px):
    if not px:
        return None
    if px.startswith("http://"):
        return px[7:]
    return px


def _parse_camoufox_proxy(proxy_str):
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


async def _solve_datadome_slider(page):
    """Solve DataDome slider CAPTCHA by dragging handle across track.

    DataDome slider typically has:
    - A track (background bar)
    - A handle (draggable circle/rectangle)
    - Text like "Slide right to secure your access"

    Returns True if slider was dragged successfully.
    """
    try:
        # Cari slider handle dan track
        slider_selectors = [
            # Handle selectors
            "[class*='slider-handle']", "[class*='slider-button']",
            "[class*='captcha-slider']", "[class*='datadome-slider']",
            "[class*='slide-button']", "[role='slider']",
            "[class*='verify-slider']",
            # Track selectors
            "[class*='slider-track']", "[class*='captcha-track']",
            "[class*='verify-track']",
        ]

        handle = None
        track = None

        for sel in slider_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    if handle is None:
                        handle = loc
                    elif track is None:
                        track = loc
                        break
            except Exception:
                continue

        if not handle:
            # Coba cari elemen dengan teks "slide" atau "drag"
            try:
                loc = page.locator("text=/slide|drag|verification/i").first
                if await loc.count() > 0 and await loc.is_visible():
                    handle = loc
            except Exception:
                pass

        if not handle:
            return False

        # Dapatkan posisi handle dan track
        handle_box = await handle.bounding_box()
        if not handle_box:
            return False

        # Jika ada track, dapatkan ujung kanan track
        if track:
            track_box = await track.bounding_box()
            if track_box:
                target_x = track_box["x"] + track_box["width"] - handle_box["width"] / 2
                target_y = track_box["y"] + track_box["height"] / 2
            else:
                target_x = handle_box["x"] + 200  # Default drag distance
                target_y = handle_box["y"]
        else:
            target_x = handle_box["x"] + 200
            target_y = handle_box["y"]

        # Drag handle dari posisi awal ke target dengan gerakan manusiawi
        start_x = handle_box["x"] + handle_box["width"] / 2
        start_y = handle_box["y"] + handle_box["height"] / 2

        # Gerak mouse ke handle dulu
        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(0.2)

        # Tekan mouse
        await page.mouse.down()
        await asyncio.sleep(0.1)

        # Drag ke target dengan beberapa langkah (simulasi manusia)
        steps = 20
        for i in range(1, steps + 1):
            t = i / steps
            # Easing function (ease-out)
            ease_t = 1 - (1 - t) ** 3
            cur_x = start_x + (target_x - start_x) * ease_t
            cur_y = start_y + (target_y - start_y) * ease_t
            # Tambah noise acak
            cur_x += random.uniform(-2, 2)
            cur_y += random.uniform(-1, 1)
            await page.mouse.move(cur_x, cur_y)
            await asyncio.sleep(random.uniform(0.01, 0.03))

        # Lepas mouse
        await page.mouse.up()
        await asyncio.sleep(0.5)

        return True

    except Exception as e:
        return False


def _simpan_hasil(mail, pw, user, totp_secret, recovery_codes):
    REGISTERED_F.parent.mkdir(parents=True, exist_ok=True)
    baris = f"{mail}|{pw}|{user}|{totp_secret or ''}|{recovery_codes or ''}\n"
    with open(REGISTERED_F, "a", encoding="utf-8") as f:
        f.write(baris)
    return baris.strip()


async def _camoufox_signup(proxy=None, headless=False, email=None, pw=None,
                           user=None, delay_anti_suspend=0):
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

    GITHUB_HOME = "https://github.com"
    GITHUB_SIGNUP = f"{GITHUB}/signup"
    _EMAIL_INPUTS = ["#email", "input[name='email']", "input[type='email']"]
    _PASSWORD_INPUTS = ["#password", "input[name='password']"]
    _USERNAME_INPUTS = ["#login", "input[name='login']"]
    _OTP_INPUTS = ["#otp", "input[name='otp']",
                   "input[autocomplete='one-time-code']", "#launch-code-0"]

    cf = None
    browser = None
    context = None

    try:
        cf = AsyncCamoufox(
            humanize=True,
            geoip=False,
            proxy=_parse_camoufox_proxy(proxy) if proxy else None,
            headless="virtual" if headless else False,
            os="windows",
            canvas_noise=False,
            disable_coop=True,
            window=(1280, 720),
        )
        browser = await cf.start()
        context = await browser.new_context(
            locale="en-US",
            java_script_enabled=True,
        )
        await context.add_init_script("""
            () => {
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    blocked = [0x8B90, 0x8B91, 0x8B92, 0x8B93, 0x8B94, 0x8B95,
                               0x8B96, 0x8B97, 0x8B98, 0x8B99, 0x8B9A, 0x8B9B,
                               0x8B9C, 0x8B9D, 0x8B9E, 0x8B9F];
                    if (blocked.includes(parameter)) {
                        return '';
                    }
                    return getParameter.call(this, parameter);
                };
                delete window.__playwright;
                delete window.__pw_manual;
                delete window.__PW_inspect;
                Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
                Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 4});
                Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            }
        """)
        page = await context.new_page()

        # --- 1. Buka homepage dulu (warm-up session) ---
        await page.goto(GITHUB_HOME, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(5)

        # Cek DataDome di homepage
        try:
            body_text = (await page.locator("body").inner_text(timeout=3000) or "").lower()
            if "temporarily restricted" in body_text or "access is restricted" in body_text:
                log["alasan"] = "datadome-temporarily-restricted-homepage"
                log["status"] = "datadome-blocked"
                return log
            if "captcha" in body_text or "verify you are human" in body_text:
                await asyncio.sleep(15)
                body_text = (await page.locator("body").inner_text(timeout=3000) or "").lower()
                if "captcha" in body_text or "verify you are human" in body_text:
                    log["alasan"] = "datadome-captcha-homepage"
                    log["status"] = "datadome-captcha"
                    return log
        except Exception:
            pass

        # --- 2. Klik "Sign up" ---
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
            await page.goto(GITHUB_SIGNUP, wait_until="domcontentloaded", timeout=60000)

        # --- 3. Tunggu form muncul ---
        deadline = asyncio.get_event_loop().time() + 30
        form_ready = False
        captcha_detected = False
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
            # Cek CAPTCHA
            try:
                body_text = (await page.locator("body").inner_text(timeout=1000) or "").lower()
                if "verification required" in body_text or "visual verification" in body_text or "captcha" in body_text:
                    captcha_detected = True
                    break
            except Exception:
                pass
            if form_ready:
                break

        if captcha_detected:
            # --- CAPTCHA handling ---
            log["alasan"] = "datadome-captcha-detected"
            log["status"] = "captcha-detected"
            
            # Coba solve slider CAPTCHA dengan mouse drag simulasi
            slider_solved = await _solve_datadome_slider(page)
            if slider_solved:
                log["catatan_captcha"] = "slider-solved"
                await asyncio.sleep(3)
                # Cek apakah form muncul setelah slider solve
                deadline2 = asyncio.get_event_loop().time() + 30
                while asyncio.get_event_loop().time() < deadline2:
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
            
            # Fallback: coba Arkose solver jika slider tidak muncul/form masih tidak ada
            if not form_ready and _cek_arkose_solver():
                try:
                    html = await page.content()
                    pubkey = _ecak_public_key(html)
                    if pubkey:
                        octo_token, err = _solve_arkose(
                            pubkey, page.url, proxy=proxy, timeout_s=120)
                        if octo_token:
                            log["catatan_captcha"] = "arkose-token-diterima"
                            try:
                                await page.evaluate(f"""
                                    () => {{
                                        const inp = document.querySelector('input[name=\"captcha\"]') ||
                                                   document.querySelector('input[name=\"octocaptcha-token\"]') ||
                                                   document.querySelector('input[name=\"datadome-token\"]');
                                        if (inp) {{ inp.value = `{octo_token}`; inp.dispatchEvent(new Event('change')); }}
                                    }}
                                """)
                            except Exception:
                                pass
                            try:
                                btn = page.locator("button[type='submit'], input[type='submit']").first
                                if await btn.count() > 0:
                                    await btn.click(timeout=5000)
                                    await asyncio.sleep(5)
                            except Exception:
                                pass
                        else:
                            log["catatan_captcha"] = f"arkose-gagal:{err}"
                except Exception as e:
                    log["catatan_captcha"] = f"solve-error:{str(e)[:60]}"
                
                # Cek lagi form
                deadline3 = asyncio.get_event_loop().time() + 30
                while asyncio.get_event_loop().time() < deadline3:
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
            log["alasan"] = "form-tidak-muncul"
            log["status"] = "gate-gagal"
            return log

        # --- 4. Isi email ---
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

        # --- 5. Isi password ---
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

        # --- 6. Isi username ---
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

        # --- 7. Klik "Create account" ---
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

        # --- 8. Tunggu OTP / verifikasi ---
        deadline = asyncio.get_event_loop().time() + 120
        verified = False
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2)
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

        # --- 9. Ambil kode launch dari Proton inbox ---
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

        # --- 10. Isi kode OTP ---
        try:
            boxes = page.locator("input[id^='launch-code-']")
            count = await boxes.count()
            if count >= len(kode):
                for i, digit in enumerate(kode):
                    try:
                        await boxes.nth(i).fill(digit, timeout=3000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.1)
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

        await asyncio.sleep(5)

        # --- 11. Konfirmasi sukses ---
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

        # --- 12. Simpan hasil ---
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


def _ambil_kode_launch(mail, pw, t0, budget=240):
    try:
        import proton_api  # noqa: F401
    except Exception:
        return None, "proton-api-tidak-tersedia"
    try:
        kode = proton_api.baca_otp(mail, pw, dari="github",
                                   pola=r"\b(\d{8})\b",
                                   ulang=8, jeda=6.0, sejak=t0,
                                   proxy=None)
        return kode, None
    except Exception as e:
        return None, f"otp-gagal:{type(e).__name__}:{str(e)[:60]}"


def daftar_akun_github(proxy=None, headless=False, negara="ID",
                       email=None, pw=None, user=None,
                       delay_anti_suspend=0, user_data_dir=None,
                       use_camoufox=None):
    if use_camoufox is None:
        use_camoufox = (not proxy) and HAS_CAMOUFOX
    elif use_camoufox and not HAS_CAMOUFOX:
        return {"email": email or "", "user": user or "", "pw": pw or "",
                "sukses": False, "alasan": "camoufox-tidak-terpasang",
                "status": "solver-tidak-tersedia", "totp_secret": "",
                "recovery_codes": "", "launch_code": None}

    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_camoufox_signup(
        proxy=proxy, headless=headless, email=email, pw=pw, user=user,
        delay_anti_suspend=delay_anti_suspend))
    return result


def daftar_batch(jumlah=1, proxy_pool=None, delay_menit=5,
                 headless=False, negara="ID"):
    hasil = []
    for i in range(jumlah):
        px = None
        if proxy_pool:
            px = proxy_pool[i % len(proxy_pool)]
        elif proxy_pool is None:
            px = gh_api.ke_url(gh_api.proksi_pertama())
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
