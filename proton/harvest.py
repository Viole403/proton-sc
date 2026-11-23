#!/usr/bin/env python3
"""Panen {id, fingerprint} dari iframe challenge Proton pada halaman signup asli.
Tanpa interaksi form — hanya memicu protokol postMessage (env.loaded + submit.broadcast)
persis seperti ChallengeFrame.tsx, lalu menyadap balasan child.message.data.
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
import socket
import sys
import time

from playwright.sync_api import sync_playwright

HOST = "account.proton.me"
TARGET = f"https://{HOST}/mail/signup?plan=free&noPromo=1&ref=mail_plus_intro-2"
OUT = sys.argv[1] if len(sys.argv) > 1 else "harvest.json"

assert HOST.endswith("proton.me"), "host tidak diizinkan"
for _fam, _t, _p, _c, sa in socket.getaddrinfo(HOST, 443):
    assert ipaddress.ip_address(sa[0]).is_global, f"IP tidak global: {sa[0]}"

SPY = """
(() => {
  window.__fp = { messages: [], pairs: [] };
  const tagOf = (src) => {
    try {
      const u = new URL(src, location.href);
      if (u.pathname.indexOf('/challenge/') >= 0) return u.searchParams.get('Name') || u.pathname;
      return u.pathname;
    } catch (e) { return '?'; }
  };
  window.addEventListener('message', (e) => {
    try {
      let tag = 'unknown';
      for (const f of document.querySelectorAll('iframe')) {
        if (f.contentWindow === e.source) { tag = tagOf(f.src); break; }
      }
      const d = e.data;
      window.__fp.messages.push({ t: Date.now(), tag, origin: e.origin, type: d && d.type });
      if (d && d.type === 'child.message.data' && d.data && d.data.id && d.data.fingerprint) {
        window.__fp.pairs.push({ tag, id: d.data.id, fingerprint: d.data.fingerprint });
      }
    } catch (err) {}
  }, true);
})();
"""

TRIGGER = """
(() => {
  const out = [];
  for (const f of document.querySelectorAll("iframe[src*='challenge']")) {
    try {
      const target = new URL(f.src).origin;
      f.contentWindow.postMessage({ type: 'env.loaded', data: { targetOrigin: location.origin } }, target);
      f.contentWindow.postMessage({ type: 'submit.broadcast' }, target);
      out.push(f.src.slice(0, 120));
    } catch (e) { out.push('ERR:' + e.message); }
  }
  return out;
})()
"""

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/google-chrome",
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context(
        locale="en-US",
        timezone_id="Asia/Jakarta",
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36",
    )
    page = ctx.new_page()
    page.add_init_script(SPY)
    print(">> buka halaman signup...")
    page.goto(TARGET, wait_until="domcontentloaded", timeout=90000)

    for i in range(40):
        n_ifr = page.evaluate("document.querySelectorAll(\"iframe[src*='challenge']\").length")
        n_loaded = page.evaluate("(window.__fp && window.__fp.messages.filter(m => m.type === 'onload').length) || 0")
        if n_ifr >= 1 and n_loaded >= n_ifr:
            print(f">> iframe challenge siap: {n_ifr} ({n_loaded} onload)")
            break
        time.sleep(1)

    time.sleep(2)
    triggered = page.evaluate(TRIGGER)
    print(">> trigger terkirim ke:", triggered)

    pairs = []
    for i in range(20):
        pairs = page.evaluate("window.__fp.pairs")
        tags = {x["tag"] for x in pairs}
        if len(tags) >= 2 or (pairs and i >= 5):
            break
        time.sleep(1)
    time.sleep(1)

    result = page.evaluate("window.__fp")
    cookies = ctx.cookies()
    browser.close()

proton_cookies = [c for c in cookies if "proton.me" in (c.get("domain") or "")]
out = {
    "pairs": result["pairs"],
    "messages": result["messages"][:100],
    "triggered": triggered,
    "cookies": proton_cookies,
}
with open(OUT, "w") as f:
    json.dump(out, f, indent=1)

print("\n== PAIRS TERPANEN ==")
print(json.dumps(result["pairs"], indent=1)[:1200])
names = [c["name"] for c in proton_cookies]
print("\n== COOKIES proton.me ==", len(proton_cookies))
print([n for n in names if n.startswith(("AUTH", "REFRESH", "Session", "Tag", "Domain"))])
print(f"\ntersimpan: {OUT}")
