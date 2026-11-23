#!/usr/bin/env python3
"""Diagnosa modal verifikasi: sampai POST /users -> 422, lalu SEMUA di-dump tanpa dipotong.

Yang ditangkap:
  - body respons POST /users (daftar HumanVerificationMethods resmi dari server)
  - DOM modal verifikasi UTUH + semua teks visible + keberadaan kata 'email' di tiap frame
  - daftar elemen kandidat (button/radio/card) di dalam modal
Berhenti SEBELUM mengirim kode verifikasi apa pun (tidak ada email terkirim).
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
import re
import socket
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag-modal"
HOST = "account.proton.me"
TARGET = f"https://{HOST}/mail/signup?plan=free&noPromo=1&ref=mail_plus_intro-2"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36"

for _f, _t, _p, _c, sa in socket.getaddrinfo(HOST, 443):
    assert ipaddress.ip_address(sa[0]).is_global
OUT.mkdir(parents=True, exist_ok=True)

# proxy: ambil baris pertama dari data/proxies.txt (format host:port:user:pass)
baris = [l.strip() for l in (HERE / "data" / "proxies.txt").read_text().splitlines() if l.strip()][0]
h, p, u, pw = baris.split(":", 3)
PROXY = {"server": f"http://{h}:{p}", "username": u, "password": pw}

hasil = {"users422": [], "modal_dom": "", "frames": []}

with sync_playwright() as pw_:
    b = pw_.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True,
                            args=["--no-sandbox", "--disable-dev-shm-usage"], proxy=PROXY)
    ctx = b.new_context(locale="en-US", timezone_id="Europe/Berlin",
                        viewport={"width": 1280, "height": 900}, user_agent=UA,
                        ignore_https_errors=True)
    page = ctx.new_page()

    def on_response(r):
        if "/api/core/v4/users" in r.url and r.request.method == "POST":
            try:
                hasil["users422"].append({"status": r.status, "body": r.text()[:2000]})
            except Exception:
                pass
    page.on("response", on_response)

    print(">> muat halaman + isi form (username di iframe, password + konfirmasi) ...")
    page.goto(TARGET, wait_until="domcontentloaded", timeout=90000)
    time.sleep(8)
    ef = next((f for f in page.frames if "Name=email" in f.url), None)
    uname = "rudianqa" + str(int(time.time()) % 100000)
    if ef:
        ef.locator("input").first.click()
        for ch in uname:
            page.keyboard.type(ch)
            time.sleep(0.04)
    page.locator("#password").click()
    for ch in "T3st.diag123a1":
        page.keyboard.type(ch)
        time.sleep(0.03)
    try:
        c = page.locator("#password-confirm")
        c.wait_for(state="visible", timeout=5000)
        c.click()
        for ch in "T3st.diag123a1":
            page.keyboard.type(ch)
            time.sleep(0.03)
    except Exception:
        pass
    time.sleep(2)
    page.locator("button[type=submit]").filter(has_text="Start").first.click()
    print(">> submit diklik, menunggu modal verifikasi ...")

    for i in range(20):
        time.sleep(3)
        ada = page.evaluate("!!document.querySelector('[role=dialog], dialog, .modal-two-dialog')")
        if ada:
            print(f">> modal muncul (iterasi {i}) — dump semua ...")
            break
    time.sleep(3)  # beri waktu isi modal termuat penuh

    hasil["modal_dom"] = page.evaluate(
        "() => { const d = document.querySelector('[role=dialog], dialog, .modal-two-dialog');"
        " return d ? d.outerHTML : '(dialog tidak ditemukan)'; }")
    hasil["body_text"] = page.evaluate("() => (document.body.innerText || '').slice(0, 3000)")
    hasil["kandidat"] = page.evaluate("""
    (() => {
      const d = document.querySelector('[role=dialog], dialog, .modal-two-dialog');
      const root = d || document;
      const out = [];
      for (const el of root.querySelectorAll('*')) {
        const t = (el.innerText || '').trim();
        const r = el.getBoundingClientRect();
        if (t && t.length < 60 && r.width > 0 && r.height > 0 &&
            ['BUTTON','A','INPUT','LABEL','DIV','SPAN','H1','H2','H3','P','LI'].includes(el.tagName)) {
          out.push({tag: el.tagName.toLowerCase(), cls: (el.className||'').toString().slice(0,60),
                    role: el.getAttribute('role')||'', text: t.slice(0,50)});
        }
      }
      return out;
    })()""")
    # cari kata 'email' di semua frame
    per_frame = []
    for f in page.frames:
        try:
            t = f.evaluate("() => (document.body ? document.body.innerText : '').toLowerCase()")
            per_frame.append({"url": f.url[:90], "ada_email": "email" in t,
                              "ada_captcha": "captcha" in t, "panjang": len(t)})
        except Exception as e:
            per_frame.append({"url": f.url[:90], "err": str(e)[:60]})
    hasil["frames"] = per_frame
    page.screenshot(path=str(OUT / "modal.png"), full_page=True)
    b.close()

(OUT / "hasil.json").write_text(json.dumps(hasil, indent=1))
print("\n=== body POST /users (metode resmi server):")
for x in hasil["users422"]:
    print(f"  [{x['status']}] {x['body'][:400]}")
print("\n=== pencarian 'email' per frame:")
for f in hasil["frames"]:
    print("  ", f)
print("\n=== teks visible halaman (500 char):")
print(hasil["body_text"][:500])
print(f"\ntersimpan: {OUT}/hasil.json + modal.png (DOM {len(hasil['modal_dom'])} char utuh)")
