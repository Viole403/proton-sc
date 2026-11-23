#!/usr/bin/env python3
"""cek_semua.py — cek kesehatan SEMUA akun registered.txt (browser login,
deteksi banner 'Account suspended', proxy rotasi anti-throttle).
Keluaran: results/kesehatan-akun.txt (mail|AKTIF/SUSPEND/GAGAL) +
registered.txt disaring jadi AKTIF saja; SUSPEND otomatis ke
registered-suspended.txt (pola yang sudah disepakati)."""
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
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# [edit brutalism] jalur dari bootstrap+konfig (dulu absolut ke proton-e2e)
import fp_profile
from playwright.sync_api import sync_playwright

P = str(konfig.AKAR)  # [edit brutalism] — dulu menulis ke POHON SUMBER
REG = P + "/results/registered.txt"
ARSIP = P + "/results/registered-suspended.txt"
HASIL = P + "/results/kesehatan-akun.txt"
PX = P + "/data/proxies.txt"


def baca_baris(line):
    bagian = line.rsplit("|", 2)
    kepala = bagian[0] if len(bagian) == 3 else line
    mail, pw = (kepala.split("|", 1) + [""])[:2]
    return mail, pw


def muat_proxy():
    out = []
    try:
        for ln in open(PX):
            ln = ln.strip()
            if ln and not ln.startswith("#") and ln.count(":") >= 3:
                h, pt, u, pw = ln.split(":", 3)
                out.append({"server": f"http://{h}:{pt}", "username": u, "password": pw})
    except Exception:
        pass
    return out


def utama(workers=3):
    baris = [l.rstrip("\n") for l in open(REG) if l.strip()]
    print(f"cek {len(baris)} akun | worker {workers} | jalur LANGSUNG (proxy Decodo flaky)",
          flush=True)
    fp_profile.ensure_display()
    hasil = [None] * len(baris)
    kunci = threading.Lock()
    n = [0]

    def kerja(chunk):
        with sync_playwright() as p:
            # [fix persona 2026-10-13] persona koheren dgn browser signup (dulu
            # build_profile inline di args, context tanpa init_script -> sidik jari
            # beda -> risiko 10003 'pindah device'; pola proton_otp.py:58-75)
            prof = fp_profile.build_profile()
            b = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=False,
                                  args=fp_profile.chrome_args(prof))
            try:
                for i in chunk:
                    mail, pw = baca_baris(baris[i])
                    stt = "GAGAL"
                    try:
                        ctx = b.new_context(locale=prof["locale"], timezone_id="Europe/Berlin",
                                            viewport=prof["viewport"], user_agent=prof["ua"],
                                            ignore_https_errors=True)
                        pg = ctx.new_page()
                        # urutan SAMA dgn browser signup (full_fidelity2.py:304,307):
                        # apply_ua_cdp dulu (mutate brands GREASE) -> init_script
                        # meng-inline persona final (urutan terbalik = brands basi)
                        fp_profile.apply_ua_cdp(ctx, pg, prof)
                        pg.add_init_script(fp_profile.init_script(prof))
                        pg.goto("https://account.proton.me/login", wait_until="domcontentloaded",
                                timeout=45000)
                        time.sleep(2.5)
                        pg.locator("#username, input[type=email], input[type=text]").first.fill(mail)
                        pg.locator("#password, input[type=password]").first.fill(pw)
                        pg.locator("button[type=submit]").first.click()
                        for _ in range(12):            # maks ±30 dtk
                            time.sleep(2.5)
                            u = pg.url
                            if any(s in u for s in ("/mail", "/inbox", "/apps", "/u/")):
                                stt = "AKTIF"
                                break
                            t = (pg.evaluate("() => (document.body.innerText||'').slice(0,600)")
                                 or "").lower()
                            if "suspended" in t:
                                stt = "SUSPEND"
                                break
                            if "too many" in t:
                                time.sleep(90)          # throttle login -> jeda panjang
                        ctx.close()
                    except Exception as e:
                        stt = f"GAGAL({type(e).__name__}: {str(e)[:40]})"
                    hasil[i] = (mail, pw, stt)
                    with kunci:
                        n[0] += 1
                        if n[0] % 10 == 0 or stt != "AKTIF":
                            print(f"  [{n[0]}/{len(baris)}] {stt} {mail}", flush=True)
                    time.sleep(2)
            finally:
                try:
                    b.close()
                except Exception:
                    pass

    bagi = [list(range(len(baris)))[i::workers] for i in range(workers)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for chunk in bagi:
            if chunk:
                ex.submit(kerja, chunk)

    # rapikan: kesehatan-akun.txt + filter registered.txt (AKTIF saja)
    aktif, susp, gagal = [], [], []
    for r in hasil:
        if r is None:
            continue
        (aktif if r[2] == "AKTIF" else susp if r[2] == "SUSPEND" else gagal).append(r)
    with open(HASIL, "w") as f:
        for mail, pw, stt in sorted(hasil, key=lambda x: (x[2] if x else "", x[0] if x else "")):
            if mail:
                f.write(f"{mail}|{stt}\n")
    if susp:
        with open(ARSIP, "a") as f:
            f.write("\n".join(f"{m}|{p}" for m, p, _ in susp) + "\n")
    with open(REG, "w") as f:
        f.write("\n".join(f"{m}|{p}||" for m, p, _ in aktif) + "\n")
    print(f"\n=== KESEHATAN: AKTIF {len(aktif)} | SUSPEND {len(susp)} | GAGAL-CEK {len(gagal)} ===",
          flush=True)
    print(f"detail: {HASIL} | arsip: {ARSIP}", flush=True)


if __name__ == "__main__":
    utama(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
