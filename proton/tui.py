#!/usr/bin/env python3
"""Menu utama PROTON SIGNUP TOOLKIT v3 — cukup jalankan:  ./venv/bin/python tui.py

Menu interaktif:
  1. run            — one-shot atau batch (akun/batch + jeda antar batch)
  2. proxy-pool     — lihat/tambah/uji daftar proxy (data/proxies.txt)
  3. registered     — lihat akun sukses (file results/registered.txt)
  4. proton-checker — cek akun masih aktif (via refresh token)
  5. settings       — API key: headmail / evilmail / smsbower (+ negara SMS)
  6. statistik      — rata2 menit per akun + pemakaian byte proxy/langsung/cache
  7. kebersihan     — bebaskan RAM (Chrome nyangkut), hapus sisa profil /tmp,
                      pangkas screenshot lama (bukti penting disimpan)
  8. prewarm-cache  — panaskan 1x asset ke cache router (hemat proxy saat batch)
  9. keluar

Mode non-interaktif (otomasi/pengujian):
  ./venv/bin/python tui.py --auto run   --accounts 6 --workers 2 --mode oneshot
  ./venv/bin/python tui.py --auto check                      # cek semua akun
  ./venv/bin/python tui.py --auto stats                       # ringkasan metrik
  ./venv/bin/python tui.py --auto clean                       # perawatan
  ./venv/bin/python tui.py --auto prewarm                     # panaskan cache
Lihat README.md untuk panduan lengkap.
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
import argparse
import subprocess
import sys
from pathlib import Path

import core

# [fix 2026-10-21 bocor jaringan Proton] proses ini berdiri sendiri (subprocess
# karyawan) -> penjaga harus dipasang DI SINI juga: paksa proton_api.login
# lewat proxy, jangan sampai fallback login SRP DIRECT dari IP VPS.
try:
    import run as _runmod
    _runmod.pasang_patch()
except Exception:
    pass

BANNER = r"""
╔══════════════════════════════════════════════════╗
║   PROTON SIGNUP TOOLKIT          v4 (Chrome 157)   ║
╚══════════════════════════════════════════════════╝"""


def menu_run():
    print("\n— RUN —")
    print("  1. one-shot   (sekali jalan, semua sekaligus)")
    print("  2. batch      (per batch + jeda antar batch, rotasi lanjut)")
    pilih = input("pilih mode [1/2]: ").strip() or "1"
    accounts = int(input("jumlah akun yang dibuat: ").strip() or "1")
    workers = int(input("jumlah worker (paralel): ").strip() or "1")
    if workers >= 8:
        print(f"  ! {workers} worker ~{workers * 0.9:.0f}GB RAM untuk Chrome — pastikan RAM lega")
    mode, per_batch, pause = "oneshot", accounts, 0.0
    if pilih == "2":
        mode = "batch"
        per_batch = int(input("akun per batch: ").strip() or "1")
        pause = float(input("jeda antar batch (menit): ").strip() or "1")
    jalankan(mode, accounts, workers, per_batch, pause)


def jalankan(mode, accounts, workers, per_batch, pause):
    s = core.load_settings()
    if not (s.get("HEADMAIL_KEY") or s.get("EVILMAIL_KEY")):
        print("API key belum diatur — buka menu 5 (settings) dulu.")
        return
    print(f"\nrencana: {accounts} akun | {workers} worker | mode {mode}"
          + (f" | {per_batch}/batch, jeda {pause} mnt" if mode == "batch" else ""))
    q = core.build_queue()
    px = core.load_proxies()
    print(f"slot identitas: {len(q)} | proxy: {len(px)} | rotasi terus sampai target tercapai")
    if input("lanjutkan? [Y/n]: ").strip().lower() == "n":
        return
    orc = core.Orchestrator(workers=workers, accounts=accounts, mode=mode,
                            per_batch=per_batch, batch_pause_min=pause)
    try:
        orc.run()
    finally:
        orc.stop_routers()


def menu_proxy():
    print("\n— PROXY POOL —")
    px = core.load_proxies()
    for i, p in enumerate(px, 1):
        print(f"  {i:2}. {p['label']}  ({p['url'].split('//')[0]}//…)")
    print(f"total: {len(px)} proxy | file: {core.DATA / 'proxies.txt'}")
    aksi = input("[t]ambah dari file lain / [u]ji semua / [Enter] kembali: ").strip().lower()
    if aksi == "t":
        path = input("path file proxy: ").strip()
        baru = [x for x in (core.parse_proxy(l) for l in Path(path).read_text().splitlines()) if x]
        with open(core.DATA / "proxies.txt", "a") as f:
            for p in baru:
                f.write(p["url"] + "\n")
        print(f"{len(baru)} proxy ditambahkan")
    elif aksi == "u":
        import json
        import urllib.request
        for p in px:
            try:
                r = urllib.request.Request("https://ipinfo.io/json",
                                           headers={"user-agent": core.UA})
                with urllib.request.build_opener(
                        urllib.request.ProxyHandler({"http": p["url"], "https": p["url"]})
                ).open(r, timeout=25) as resp:
                    d = json.loads(resp.read().decode())
                print(f"  OK  {p['label']} -> {d.get('ip')} ({d.get('org')})")
            except Exception as e:
                print(f"  ERR {p['label']} -> {type(e).__name__}")


def menu_registered():
    print("\n— REGISTERED (akun sukses) —")
    if not core.REGISTERED_F.exists():
        print("belum ada akun terdaftar.")
        return
    lines = [l for l in core.REGISTERED_F.read_text().splitlines() if l.strip()]
    for l in lines:
        mail, pw, rt, cid = (l.split("|") + [""] * 4)[:4]
        print(f"  {mail}  | pass {'✓' if pw else '✗'} | rt {rt[:10]}… | cid {cid[:10]}…")
    print(f"total: {len(lines)} akun | file: {core.REGISTERED_F}")
    if input("buka file .txt-nya? [Y/n]: ").strip().lower() != "n":
        try:
            subprocess.run(["xdg-open", str(core.REGISTERED_F)], shell=False, check=False)
            print("(file dibuka di aplikasi default)")
        except Exception as e:
            print(f"gagal membuka: {e}")


def menu_checker():
    print("\n— PROTON CHECKER —")
    login = input("sekadar cek token [Enter] / vonis login browser (lambat, tahu SUSPEND) [l]: ").strip().lower() == "l"
    w = 1
    if login:
        w = int(input("jumlah worker login paralel [2]: ").strip() or "2")
        if w >= 4:
            print(f"  ! {w} worker ~{w * 0.8:.0f}GB RAM (Chrome) — pastikan lega")
    print("memeriksa semua akun terdaftar " + (f"(LOGIN BROWSER, {w} worker) ..." if login else "(via refresh token) ..."))
    hasil = core.check_accounts(cek_login=login, workers=w)
    # ringkas: log live per-akun sudah tampil saat pemeriksaan; di sini cukup
    # sorot yang bermasalah + total (pencocokan lama '==' meleset utk
    # "AKTIF (login)" sehingga total selalu 0)
    ada_masalah = [r for r in hasil if not r["status"].startswith(("AKTIF", "TOKEN AKTIF"))]
    if ada_masalah:
        print("  -- yang perlu perhatian --")
        for r in ada_masalah:
            print(f"  {r['email']:42} {r['status']}")
    aktif = sum(1 for r in hasil if r["status"].startswith(("AKTIF", "TOKEN AKTIF")))
    print(f"total: {aktif}/{len(hasil)} aktif")


def menu_settings():
    print("\n— SETTINGS —")
    s = core.load_settings()
    for k in ("HEADMAIL_KEY", "EVILMAIL_KEY", "SMSBOWER_KEY", "SMSBOWER_COUNTRY"):
        v = s.get(k, "")
        tampil = ((v[:10] + "…") if len(v) > 10 and "KEY" in k else v) if v else "(kosong)"
        baru = input(f"{k} {tampil} -> baru (Enter = tetap): ").strip()
        if baru:
            s[k] = baru
    core.save_settings(s)
    print("tersimpan di data/settings.json (chmod 600)")


def menu_stats():
    print("\n— STATISTIK —")
    m = core.load_metrics()
    r = m.get("ringkasan", {})
    if not r:
        print("belum ada data.")
        return
    tampilkan_stats(m, r)


def tampilkan_stats(m, r):
    print(f"  attempt        : {r.get('total_attempt')} (sukses {r.get('total_sukses')}, gagal {r.get('total_gagal')})")
    if r.get("rata2_menit_per_akun"):
        print(f"  rata2 per akun : {r['rata2_menit_per_akun']} menit")
    print(f"  byte proxy     : {r.get('total_bytes_proxy', 0):,}")
    print(f"  byte langsung  : {r.get('total_bytes_direct', 0):,}")
    print(f"  byte cache     : {r.get('total_bytes_cache', 0):,}")
    for b in m.get("batches", []):
        ok = b.get("sukses", 0)
        n = len(b.get("attempts", []))
        avg = b["menit_total"] / ok if ok else 0
        print(f"  batch {b['no']}: {ok}/{n} sukses | rata2 {avg:.1f} mnt/akun"
              f" | proxy {b['bytes_proxy']:,} B | langsung {b['bytes_direct']:,} B")


def menu_kebersihan():
    print("\n— KEBERSIHAN (perawatan, logic tidak disentuh) —")
    ringkas = core.pembersihan(dry=True)
    mb = ringkas["bebas_byte"] / 1024 / 1024
    print(f"  rencana: matikan {ringkas['chrome_dibunuh']} Chrome nyangkut, "
          f"hapus {ringkas['profil_tmp']} profil /tmp, pangkas {ringkas['screenshot']} "
          f"screenshot + {ringkas['fc']} conf font (±{mb:.1f}MB)")
    print("  (screenshot bukti: modal/pilihan/pasca-*/sukses/blokir/tanpa-vektor — TIDAK disentuh)")
    if input("jalankan? [Y/n]: ").strip().lower() != "n":
        ringkas = core.pembersihan()
        mb = ringkas["bebas_byte"] / 1024 / 1024
        print(f"  selesai: {ringkas} (±{mb:.1f}MB bebas)")


def menu_prewarm():
    print("\n— PREWARM CACHE —")
    print(core.prewarm_cache())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", choices=["run", "check", "checklogin", "stats", "clean", "prewarm"],
                    help="mode non-interaktif")
    ap.add_argument("--accounts", type=int, default=1)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--mode", choices=["oneshot", "batch"], default="oneshot")
    ap.add_argument("--per-batch", type=int, default=0)
    ap.add_argument("--batch-pause", type=float, default=0)
    args = ap.parse_args()

    if args.auto == "run":
        orc = core.Orchestrator(workers=args.workers, accounts=args.accounts, mode=args.mode,
                                per_batch=args.per_batch or args.accounts,
                                batch_pause_min=args.batch_pause)
        try:
            orc.run()
        finally:
            orc.stop_routers()
        return
    if args.auto in ("check", "checklogin"):
        hasil = core.check_accounts(cek_login=args.auto == "checklogin", workers=args.workers)
        for r in hasil:
            print(f"{r['email']:42} {r['status']}")
        if args.auto == "checklogin":
            n = sum(1 for r in hasil if "SUSPEND" in r["status"])
            print(f"\n{n} akun suspend dikeluarkan dari registered.txt (arsip: results/registered-suspended.txt)")
        return
    if args.auto == "stats":
        m = core.load_metrics()
        tampilkan_stats(m, m.get("ringkasan", {}))
        return
    if args.auto == "clean":
        print(core.pembersihan())
        return
    if args.auto == "prewarm":
        print(core.prewarm_cache())
        return

    print(BANNER)
    while True:
        print("""
  1. run
  2. proxy-pool
  3. registered
  4. proton-checker
  5. settings
  6. statistik
  7. kebersihan
  8. prewarm-cache
  9. keluar""")
        pilih = input("pilih> ").strip()
        if pilih == "1":
            menu_run()
        elif pilih == "2":
            menu_proxy()
        elif pilih == "3":
            menu_registered()
        elif pilih == "4":
            menu_checker()
        elif pilih == "5":
            menu_settings()
        elif pilih == "6":
            menu_stats()
        elif pilih == "7":
            menu_kebersihan()
        elif pilih == "8":
            menu_prewarm()
        elif pilih in ("9", "q", "quit", "exit"):
            print("sampai jumpa!")
            return
        else:
            print("pilihan tidak dikenal")


if __name__ == "__main__":
    main()
