#!/usr/bin/env python3
"""lengkapi_token.py (v2 — jalur BROWSER terbukti) — isi refresh|client utk
baris registered.txt yang kosong, dengan LOGIN BROWSER (headless, Xvfb) lalu
PANEN cookie REFRESH-<uid>/SessionId. login_srp HTTP rusak (bukti: akun
ber-token valid pun ditolak 'password not correct') — jangan dipakai.
Paralel N worker (1 browser/worker), resume-safe (baris ber-token dilewati),
jeda manusiawi antar login (anti rate-limit 2028)."""
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
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

# [edit brutalism] jalur dari bootstrap+konfig (dulu absolut ke proton-e2e)
import fp_profile

from playwright.sync_api import sync_playwright

REG = str(konfig.REGISTERED)  # [edit brutalism] — dulu menulis ke POHON SUMBER
PX = str(konfig.PROXIES)  # [edit brutalism]


def muat_proxy():
    out = []
    try:
        for ln in open(PX):
            ln = ln.strip()
            if not ln or ln.startswith("#") or ln.count(":") < 3:
                continue
            host, port, user, pw = ln.split(":", 3)
            out.append({"server": f"http://{host}:{port}", "username": user, "password": pw})
    except Exception:
        pass
    return out


def baca_baris(line):
    """mail|pass|rt|uid — rsplit 2 dari KANAN (rt/uid tak ber-pipe) supaya
    password yang mengandung '|' tidak membingungkan parser."""
    bagian = line.rsplit("|", 2)
    if len(bagian) == 3:
        kepala, rt, uid = bagian
    else:
        kepala, rt, uid = line, "", ""
    mail, pw = (kepala.split("|", 1) + [""])[:2]
    return mail, pw, rt, uid


def panen_kuki(ctx):
    """(refresh, uid) dari kuki login Proton."""
    rt = uid = ""
    for k in ctx.cookies():
        n = k.get("name", "")
        if n.startswith("REFRESH-"):
            uid = n.split("REFRESH-", 1)[1]
            v = urllib.parse.unquote(k.get("value", ""))
            if v.startswith("{"):
                try:
                    import json
                    v = json.loads(v).get("RefreshToken", v)
                except Exception:
                    pass
            rt = v
        if n.startswith("SessionId") and not uid:
            uid = n.split("SessionId-", 1)[1] if "-" in n else ""
    return rt, uid


def utama(workers=5):
    baris = [l.rstrip("\n") for l in open(REG) if l.strip()]
    perlu = []
    for i, l in enumerate(baris):
        mail, pw, rt, uid = baca_baris(l)
        if mail and pw and not rt.strip():
            perlu.append(i)
    print(f"total {len(baris)} | perlu token: {len(perlu)} | worker: {workers}", flush=True)
    fp_profile.ensure_display()
    kunci = threading.Lock()
    n = [0]
    proksi = muat_proxy()          # 1 akun = 1 exit-IP (anti throttle login 2028)

    def kerja(chunk):
        with sync_playwright() as p:
            # [fix persona 2026-10-13] persona dipakai KOHEREN (dulu build_profile
            # inline di args -> context/init beda persona dgn browser signup = risiko
            # 10003 'pindah device'; pola proton_otp.py / core.cek_login_banyak)
            prof = fp_profile.build_profile()
            b = p.chromium.launch(executable_path="/usr/bin/google-chrome",
                                  headless=False,
                                  args=fp_profile.chrome_args(prof))
            try:
                for i in chunk:
                    mail, pw, _, _ = baca_baris(baris[i])
                    tanda = ""
                    try:
                        px = proksi[n[0] % len(proksi)] if proksi else None
                        ctx = b.new_context(locale=prof["locale"], timezone_id="Europe/Berlin",
                                            viewport=prof["viewport"], user_agent=prof["ua"],
                                            ignore_https_errors=True,
                                            **({"proxy": px} if px else {}))
                        pg = ctx.new_page()
                        # urutan SAMA dgn browser signup (full_fidelity2.py:304,307):
                        # apply_ua_cdp dulu (mutate brands GREASE) -> init_script
                        # meng-inline persona final (urutan terbalik = brands basi)
                        fp_profile.apply_ua_cdp(ctx, pg, prof)
                        pg.add_init_script(fp_profile.init_script(prof))
                        pg.goto("https://account.proton.me/login", wait_until="domcontentloaded",
                                timeout=45000)
                        time.sleep(3)
                        pg.locator("#username, input[type=email], input[type=text]").first.fill(mail)
                        pg.locator("#password, input[type=password]").first.fill(pw)
                        pg.locator("button[type=submit]").first.click()
                        ok = False
                        lihat_galat = ""
                        for _ in range(25):
                            time.sleep(2)
                            if any(s in pg.url for s in ("/mail", "/inbox", "/apps", "/u/")):
                                ok = True
                                break
                            if "suspend" in pg.url or False:
                                break
                            t = (pg.evaluate("() => (document.body.innerText||'').slice(0,400)")
                                 or "").lower()
                            if "not correct" in t or "incorrect password" in t or "incorrect" in t:
                                lihat_galat = "password?"   # jangan putus dini
                            if "too many" in t:
                                lihat_galat = "throttle(IP)"
                        if not ok and lihat_galat:
                            tanda = "✗ " + lihat_galat
                        if ok:
                            rt, uid = panen_kuki(ctx)
                            if rt:
                                baris[i] = f"{mail.split('|')[0]}|{pw}|{rt}|{uid}"
                                tanda = "✓"
                            else:
                                tanda = "✗ kuki REFRESH tak ada"
                        elif not tanda:
                            tanda = "✗ timeout login"
                        ctx.close()
                    except Exception as e:
                        tanda = f"✗ {type(e).__name__}"[:14]
                    with kunci:
                        n[0] += 1
                        print(f"  [{n[0]}/{len(perlu)}] {tanda} {mail}", flush=True)
                        if n[0] % 20 == 0:          # simpan berkala (resume-safe)
                            open(REG, "w").write("\n".join(baris) + "\n")
                    time.sleep(3 + (n[0] % 5))       # jeda anti rate-limit
            finally:
                try:
                    b.close()
                except Exception:
                    pass

    bagi = [perlu[i::workers] for i in range(workers)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for chunk in bagi:
            if chunk:
                ex.submit(kerja, chunk)
    open(REG, "w").write("\n".join(baris) + "\n")
    lengkap = sum(1 for l in baris if baca_baris(l)[2].strip())
    print(f"\nSELESAI — {lengkap}/{len(baris)} ber-token", flush=True)


if __name__ == "__main__":
    utama(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
