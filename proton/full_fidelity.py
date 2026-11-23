#!/usr/bin/env python3
"""Runner full-fidelity: client asli Proton yang menyusun SEMUA request.
Kita hanya: ketik form seperti manusia + bantu verifikasi email via headmail.
Tanpa lapisan sintetis — TLS/headers/telemetri semuanya milik Chrome sungguhan.
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
import ipaddress
import json
import os
import secrets
import socket
import string
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

HOST = "account.proton.me"
TARGET = f"https://{HOST}/mail/signup?plan=free&noPromo=1&ref=mail_plus_intro-2"
HEADMAIL = "https://headmail.flowjob-lab.workers.dev"
HEADMAIL_KEY = os.environ.get("HEADMAIL_KEY", "")  # kunci API headmail dari env, bukan source
HM_DOMAIN = "hellowntas.fun"
HERE = Path(__file__).resolve().parent
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)

assert HOST.endswith("proton.me")
for _f, _t, _p, _c, sa in socket.getaddrinfo(HOST, 443):
    assert ipaddress.ip_address(sa[0]).is_global

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"


def shot(page, label):
    path = SHOTS / f"run-{int(time.time()) % 100000}-{label}.png"
    try:
        page.screenshot(path=str(path))
    except Exception:
        pass
    print(f"   [shot] {path.name}")


def human_type(page, handle, text):
    handle.focus()
    time.sleep(secrets.randbelow(400) / 1000 + 0.25)
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(secrets.randbelow(70) / 1000 + 0.03)
        if secrets.randbelow(12) == 0:
            time.sleep(secrets.randbelow(500) / 1000 + 0.15)


def state_of(page):
    try:
        return page.evaluate(
            "() => ({url: location.href, text: (document.body.innerText || '').slice(0, 1500)})")
    except Exception as e:
        return {"url": "?", "text": f"<err {e}>"}


# ---------- headmail ----------
def hm_create_alias():
    r = requests.post(f"{HEADMAIL}/api/v1/aliases",
                      headers={"authorization": "Bearer " + HEADMAIL_KEY, "user-agent": UA,
                               "content-type": "application/json"},
                      json={"source": "domain", "total": 1, "domains": [HM_DOMAIN]}, timeout=25)
    a = r.json()["created"][0]["email"]
    print(f">> alias headmail: {a}")
    return a


def hm_otp(alias, t0, budget=240):
    end = time.time() + budget
    while time.time() < end:
        q = urllib.parse.urlencode({"email": alias, "wait": 25, "since": t0, "any": 1})
        d = requests.get(f"{HEADMAIL}/api/v1/otp?{q}",
                         headers={"authorization": "Bearer " + HEADMAIL_KEY, "user-agent": UA},
                         timeout=40).json()
        if d.get("found") and d.get("otp"):
            print(f">> OTP masuk: {d['otp']} ({d.get('subject')})")
            return d["otp"]
        print("   ...menunggu OTP")
    return None


# ---------- persiapan identitas ----------
username = "rudiansayh" + "".join(secrets.choice(string.digits) for _ in range(3))
password = "T3st." + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10)) + "a1"
t0 = int(time.time() * 1000)
alias = hm_create_alias()

log = {"username": username, "password": password, "alias": alias, "steps": []}


def note(msg):
    print(">>", msg)
    log["steps"].append({"t": int(time.time()), "m": msg})


with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=str(konfig.CHROME_BIN), headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context(
        locale="en-US", timezone_id="Asia/Jakarta", viewport={"width": 1280, "height": 900},
        user_agent=UA,
    )
    page = ctx.new_page()
    note("muat halaman signup")
    page.goto(TARGET, wait_until="domcontentloaded", timeout=90000)
    time.sleep(6)

    # --- isi form: username ---
    uname = page.locator("#username")
    try:
        uname.scroll_into_view_if_needed(timeout=3000)
        uname.click(timeout=3000)
    except Exception:
        note("klik #username gagal -> fokus via JS")
        page.evaluate("document.querySelector('#username').focus()")
    human_type(page, page.locator("#username"), username)
    note(f"username diketik: {username}")
    time.sleep(2.5)  # jeda alami + biarkan cek availability jalan

    # --- password ---
    try:
        pw = page.locator("#password")
        pw.click(timeout=3000)
    except Exception:
        page.evaluate("document.querySelector('#password').focus()")
    human_type(page, page.locator("#password"), password)
    note("password diketik")
    time.sleep(2.0)

    # --- konfirmasi password bila ada ---
    pw2 = page.locator("input[type=password]").nth(1)
    try:
        if pw2.count() and pw2.is_visible():
            pw2.click(timeout=2000)
            human_type(page, pw2, password)
            note("konfirmasi password diketik")
            time.sleep(1.5)
    except Exception:
        pass

    shot(page, "sebelum-submit")
    st = state_of(page)
    note(f"teks sebelum submit: {st['text'][:120]!r}")

    # --- SUBMIT (client asli yang membangun semua request) ---
    btn = page.locator("button[type=submit]").first
    btn.click()
    note("submit diklik — client asli mulai bekerja")

    # --- amati hasil: verifikasi / sukses / error ---
    otp_done = False
    final = {"url": "", "text": ""}
    for i in range(60):
        time.sleep(4)
        st = state_of(page)
        final = st
        low = st["text"].lower()
        if "2028" in low or "abusive traffic" in low:
            note(f"TERDETEKSI blokir abuse:\n{st['text'][:300]}")
            shot(page, "blokir")
            break
        if ("welcome" in low or "inbox" in st["url"] or "/u/1/" in st["url"]
                or "account created" in low or "successfully" in low):
            note(f"INDIKASI SUKSES: {st['url']}")
            shot(page, "sukses")
            break
        need_verif = ("verification" in low or "verify" in low or "captcha" in low
                      or "confirm" in low and "code" in low)
        if need_verif and not otp_done:
            note(f"tahap verifikasi muncul (iterasi {i})")
            shot(page, "verifikasi")
            # pilih metode email bila tersedia
            for sel in ["text=Email", "button:has-text('Email')", "[role=button]:has-text('Email')",
                        "label:has-text('Email')", "text=your email"]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=3000)
                        note(f"metode email diklik via {sel}")
                        time.sleep(2)
                        break
                except Exception:
                    continue
            else:
                if "email" not in low:
                    note("TIDAK ada opsi email — hanya captcha. Tidak saya lanjutkan (captcha bukan ranah saya).")
                    shot(page, "hanya-captcha")
                    break
            # isi alamat email
            for sel in ["input[type=email]", "input[placeholder*='mail' i]", "input[name*='email' i]",
                        "input[type=text]"]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=3000)
                        human_type(page, loc, alias)
                        note(f"alias diketik via {sel}")
                        time.sleep(1.5)
                        break
                except Exception:
                    continue
            # tombol kirim kode
            for sel in ["button:has-text('Send')", "button:has-text('Get')", "button:has-text('Continue')",
                        "button:has-text('code' i)", "button[type=submit]"]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=3000)
                        note(f"tombol kirim kode diklik via {sel}")
                        break
                except Exception:
                    continue
            shot(page, "setelah-kirim-kode")
            otp = hm_otp(alias, t0)
            if not otp:
                note("OTP tidak masuk dalam budget")
                break
            # isi kode
            for sel in ["input[autocomplete=one-time-code]", "input[inputmode=numeric]",
                        "input[maxlength='6']", "input[type=text]"]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=3000)
                        human_type(page, loc, otp)
                        note("OTP diketik")
                        time.sleep(1)
                        break
                except Exception:
                    continue
            for sel in ["button:has-text('Verify')", "button:has-text('Confirm')",
                        "button:has-text('Submit')", "button[type=submit]"]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=3000)
                        note(f"tombol verifikasi diklik via {sel}")
                        break
                except Exception:
                    continue
            otp_done = True
            shot(page, "setelah-verifikasi")
        else:
            note(f"menunggu... ({i}) url={st['url'][:70]}")

    shot(page, "final")
    log["final"] = {"url": final.get("url", ""), "text": final.get("text", "")[:1500]}
    (HERE / "run-full-result.json").write_text(json.dumps(log, indent=1))
    browser.close()

print("\n== RINGKASAN ==")
print("akun :", f"{username}@proton.me")
print("alias:", alias)
print("akhir:", log["final"].get("url", ""))
print("teks :", log["final"].get("text", "")[:400])
