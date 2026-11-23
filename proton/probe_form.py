#!/usr/bin/env python3
"""Dry-run: petakan struktur form signup Proton (semua frame, input, tombol) TANPA submit.
Tidak mengirim request signup apa pun — aman dipakai kapan saja.
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
from pathlib import Path

from playwright.sync_api import sync_playwright

HOST = "account.proton.me"
TARGET = f"https://{HOST}/mail/signup?plan=free&noPromo=1&ref=mail_plus_intro-2"
HERE = Path(__file__).resolve().parent
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)

assert HOST.endswith("proton.me")
for _f, _t, _p, _c, sa in socket.getaddrinfo(HOST, 443):
    assert ipaddress.ip_address(sa[0]).is_global

INVENTORY = """
(() => {
  const out = { url: location.href, title: document.title, items: [] };
  const desc = (el) => {
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(),
      type: el.type || '', name: el.name || '', id: el.id || '',
      placeholder: el.placeholder || '', aria: el.getAttribute('aria-label') || '',
      value: (el.type === 'password') ? (el.value ? '<terisi>' : '') : (el.value || '').slice(0, 40),
      text: (el.innerText || '').trim().slice(0, 60),
      visible: r.width > 0 && r.height > 0,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    };
  };
  for (const el of document.querySelectorAll('input, button, select, [role=button], [role=radio], textarea')) {
    out.items.push(desc(el));
  }
  out.bodyText = (document.body ? document.body.innerText : '').slice(0, 600);
  return out;
})()
"""

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/google-chrome", headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context(
        locale="en-US", timezone_id="Asia/Jakarta", viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36",
    )
    page = ctx.new_page()
    print(">> muat halaman signup...")
    page.goto(TARGET, wait_until="domcontentloaded", timeout=90000)
    time.sleep(10)  # biarkan SPA + iframe challenge matang

    report = []
    for i, frame in enumerate(page.frames):
        try:
            inv = frame.evaluate(INVENTORY)
            inv["frame_index"] = i
            inv["frame_url"] = frame.url
            report.append(inv)
        except Exception as e:
            report.append({"frame_index": i, "frame_url": frame.url, "error": str(e)})

    page.screenshot(path=str(SHOTS / "probe-full.png"), full_page=True)
    (HERE / "form-inventory.json").write_text(json.dumps(report, indent=1))
    browser.close()

for fr in report:
    print(f"\n== FRAME #{fr.get('frame_index')} | {fr.get('frame_url', '')[:100]}")
    if fr.get("error"):
        print("   error:", fr["error"][:120])
        continue
    vis = [x for x in fr["items"] if x["visible"]]
    print(f"   item: {len(fr['items'])} (visible {len(vis)}) | judul: {fr.get('title', '')[:60]}")
    for it in vis[:14]:
        print(f"   - <{it['tag']} type={it['type']}> name={it['name']!r} id={it['id']!r} "
              f"ph={it['placeholder']!r} aria={it['aria']!r} text={it['text'][:30]!r} rect={it['rect']}")
print("\ntersimpan: form-inventory.json + shots/probe-full.png")
