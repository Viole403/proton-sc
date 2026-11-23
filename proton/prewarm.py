#!/usr/bin/env python3
"""Prewarm cache: muat halaman signup sekali (tanpa isi form, tanpa persona —
identitas tidak relevan utk pemanasan) supaya asset immutable masuk cache router.
Dipanggil core.prewarm_cache(). SIGNUP_PROXY = alamat router lokal."""
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
import os
import time
import urllib.parse

from playwright.sync_api import sync_playwright

proxy = os.environ.get("SIGNUP_PROXY", "")
with sync_playwright() as p:
    kw = dict(executable_path="/usr/bin/google-chrome", headless=True,
              args=["--no-sandbox", "--disable-dev-shm-usage"])
    if proxy:
        pr = urllib.parse.urlparse(proxy if "//" in proxy else "http://" + proxy)
        pk = {"server": f"http://{pr.hostname}:{pr.port or 80}"}
        if pr.username:
            pk["username"] = pr.username
            pk["password"] = pr.password or ""
        kw["proxy"] = pk
    b = p.chromium.launch(**kw)
    ctx = b.new_context(ignore_https_errors=bool(proxy))
    pg = ctx.new_page()
    pg.goto("https://account.proton.me/mail/signup?plan=free&noPromo=1&ref=mail_plus_intro-2",
            wait_until="networkidle", timeout=60000)
    time.sleep(2)
    b.close()
print("prewarm selesai")
