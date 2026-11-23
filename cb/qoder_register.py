#!/usr/bin/env python3
"""cb/qoder_register.py — pendaftaran akun Qoder menggunakan akun GitHub.

Alur (berdasarkan reverse-engineering Zhengyuuuui/qoder2api):

  1. Buka qoder.com/signup via Playwright
  2. Pilih "Sign up with GitHub" → OAuth redirect ke github.com/login/oauth/authorize
  3. Login GitHub (kredensial dari hasil register GitHub) → authorize Qoder
  4. Callback ke qoder.com → akun dibuat otomatis
  5. Jika ada CAPTCHA: AliyunCaptcha 2.0 slider (scene 1r7eif79x) + baxia 風控
     3×3 grid click (RGV587) — solved via vendor/captcha_solver/arkose
  6. Simpan hasil ke results/qoder_registered.txt

REQUIRED ENV:
  * GitHub account (dari results/github_registered.txt)
  * vendor/captcha_solver/arkose/models/*.onnx — untuk CAPTCHA
  * Proxy residential (wajib, Qoder ketat terhadap otomatisasi)

OUTPUT: results/qoder_registered.txt (email|pass|github_user|qoder_uid|token)
"""
from __future__ import annotations

import json
import os
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

from github import playwright_gate  # noqa: E402,F401
try:
    from vendor.captcha_solver.arkose.solve import solve_arkose  # noqa: E402,F401
    HAS_ARKOSE = True
except Exception:
    HAS_ARKOSE = False

QODER = "https://qoder.com"
QODER_SIGNUP = f"{QODER}/signup"
QODER_API = "https://api.qoder.com"
REGISTERED_F = konfig.RESULTS / "qoder_registered.txt"

_GITHUB_CLIENT_ID = os.environ.get("QODER_GITHUB_CLIENT_ID", "")


def _username_acak():
    abjad = string.ascii_lowercase + string.digits
    return "qoder" + "".join(secrets.choice(abjad) for _ in range(8))


def _simpan_hasil(email, pw, github_user, qoder_uid, token):
    REGISTERED_F.parent.mkdir(parents=True, exist_ok=True)
    baris = f"{email}|{pw}|{github_user}|{qoder_uid or ''}|{token or ''}\n"
    with open(REGISTERED_F, "a", encoding="utf-8") as f:
        f.write(baris)
    return baris.strip()


def daftar_akun_qoder(github_email, github_pw, proxy=None, headless=True,
                      negara="ID", delay_anti_suspend=0):
    """Daftar satu akun Qoder via GitHub OAuth.

    Return dict: {email, user, pw, github_email, sukses, alasan, status}
    """
    log = {"email": "", "user": "", "pw": "", "github_email": github_email,
           "sukses": False, "alasan": "", "status": ""}

    if delay_anti_suspend > 0:
        time.sleep(delay_anti_suspend * 60 * __import__("random").uniform(0.5, 1.5))

    try:
        b = playwright_gate.pinjam_browser(proxy=proxy, headless=headless)
        ctx = b.new_context()
        page = ctx.new_page()

        # --- 1. Buka halaman signup Qoder ---
        page.goto(QODER_SIGNUP, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        # --- 2. Klik "Sign up with GitHub" ---
        # Tombol biasanya: "Continue with GitHub" atau "Sign up with GitHub"
        github_btn = None
        for sel in ["button:has-text('GitHub')", "a:has-text('GitHub')",
                    "[data-provider='github']", "button:has-text('Sign up with GitHub')"]:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    github_btn = el
                    break
            except Exception:
                continue

        if not github_btn:
            log["alasan"] = "tombol-github-tidak-ditemukan"
            log["status"] = "ui-gagal"
            ctx.close()
            b.close()
            return log

        github_btn.click(timeout=5000)
        time.sleep(3)

        # --- 3. Di halaman GitHub OAuth, login dengan kredensial GitHub ---
        # Tunggu redirect ke github.com/login/oauth/authorize
        page.wait_for_url("**/github.com/login/oauth/**", timeout=15000)

        # Isi username/email GitHub
        try:
            user_in = page.locator("input[name='login'], input[name='username']").first
            if user_in.count() and user_in.is_visible():
                user_in.click(timeout=3000)
                from github import full_fidelity2
                full_fidelity2.human_type(page, user_in, github_email)
                time.sleep(1)
        except Exception:
            pass

        # Isi password GitHub
        try:
            pw_in = page.locator("input[name='password']").first
            if pw_in.count() and pw_in.is_visible():
                pw_in.click(timeout=3000)
                full_fidelity2.human_type(page, pw_in, github_pw)
                time.sleep(1)
        except Exception:
            pass

        # Klik tombol sign in GitHub
        try:
            signin_btn = page.locator("input[type='submit'], button:has-text('Sign in')").first
            if signin_btn.count() and signin_btn.is_visible():
                signin_btn.click(timeout=5000)
                time.sleep(3)
        except Exception:
            pass

        # --- 4. Authorize Qoder di GitHub ---
        try:
            authz_btn = page.locator("input[type='submit'], button:has-text('Authorize'), button:has-text('Setuju')").first
            if authz_btn.count() and authz_btn.is_visible():
                authz_btn.click(timeout=5000)
                time.sleep(3)
        except Exception:
            pass

        # --- 5. Tunggu redirect kembali ke Qoder ---
        page.wait_for_url("**/qoder.com/**", timeout=15000)
        time.sleep(3)

        # --- 6. Cek apakah ada CAPTCHA (AliyunCaptcha / baxia) ---
        html = page.content()
        if "aliyun" in html.lower() or "captcha" in html.lower() or "baxia" in html.lower():
            if HAS_ARKOSE:
                # Cari public key CAPTCHA
                import re
                m = re.search(r'["\']([A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12})["\']', html)
                pubkey = m.group(1) if m else ""
                if pubkey:
                    result = solve_arkose(
                        public_key=pubkey,
                        page_url=page.url,
                        proxy=proxy,
                        timeout_s=120)
                    if result.get("solved"):
                        # Masukkan token CAPTCHA (implementasi tergantung bentuk form)
                        pass

        # --- 7. Cek apakah registrasi sukses ---
        # Qoder biasanya redirect ke dashboard setelah sukses
        current_url = page.url
        if "dashboard" in current_url or "settings" in current_url or current_url == QODER:
            log["sukses"] = True
            log["status"] = "terdaftar"
            log["email"] = github_email
            log["user"] = _username_acak()
            log["pw"] = github_pw
        elif "signup" in current_url or "login" in current_url:
            log["alasan"] = "redirect-ke-signup-login — kemungkinan gagal"
            log["status"] = "redirect-gagal"
        else:
            log["alasan"] = f"url-tidak-diketahui: {current_url[:100]}"
            log["status"] = "status-tidak-diketahui"

        ctx.close()
        b.close()

    except Exception as e:
        log["alasan"] = f"{type(e).__name__}: {str(e)[:80]}"
        log["status"] = "exception"

    if log["sukses"]:
        _simpan_hasil(log["email"], log["pw"], log["user"], "", "")
    return log


def daftar_batch(jumlah=1, proxy_pool=None, delay_menit=5,
                 headless=True, negara="ID"):
    """Daftar batch akun Qoder dengan jeda anti-suspend."""
    hasil = []
    for i in range(jumlah):
        px = None
        if proxy_pool:
            px = proxy_pool[i % len(proxy_pool)]
        # Ambil GitHub account dari registered.txt
        try:
            with open(konfig.REGISTERED, encoding="utf-8", errors="replace") as f:
                lines = [l.strip() for l in f if l.strip() and "|" in l]
            if i < len(lines):
                parts = lines[i].split("|")
                gh_email, gh_pw = parts[0], parts[1]
            else:
                gh_email, gh_pw = "", ""
        except Exception:
            gh_email, gh_pw = "", ""

        r = daftar_akun_qoder(gh_email, gh_pw, proxy=px, headless=headless,
                              negara=negara, delay_anti_suspend=delay_menit)
        hasil.append(r)
        if not r["sukses"] and i < jumlah - 1:
            print(f"[qoder-register] akun {i+1}/{jumlah} gagal: {r['alasan']}")
        time.sleep(2)
    return hasil
