#!/usr/bin/env python3
"""fp_lite.py — persona ringan utk GitHub e2e: konsistensi UA/UA-CH/locale/layar
(sinyal L2 DataDome = kecocokan runtime vs header). Mandiri, tanpa import proyek lain."""
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
import random
import subprocess
import time

UA_SET = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    # HANYA Linux/X11 — varian "Windows" dulu = mismatch runtime-vs-header
    # (sinyal L2 sensor; pelajaran tim bedah: device-check jadi tak pernah lulus)
]
LOCALE_SET = ["en-US", "en-GB", "de-DE"]
VIEWPORT = ({"width": 1536, "height": 864}, {"width": 1440, "height": 900},
            {"width": 1366, "height": 768})


def build_persona(seed=None):
    rnd = random.Random(seed)
    return {
        "ua": rnd.choice(UA_SET),
        "locale": rnd.choice(LOCALE_SET),
        "viewport": rnd.choice(VIEWPORT),
    }


ARGS_DASAR = [
    "/usr/bin/google-chrome",
    "--ozone-platform=x11",
    # kontrak env gasak: WebGL generik HIDUP (jangan null/disable) + window konsisten
    "--use-angle=swiftshader", "--use-gl=angle", "--enable-unsafe-swiftshader",
    # DoH OFF: Chrome gagal resolve host challenge lewat DoH di jaringan ini
    # (curl/system-DNS sehat) — akar "c.js GAGAL dimuat" (temuan isolasi)
    "--dns-over-https-mode=off", "--disable-features=DnsOverHttps",
    "--process-per-site", "--disable-dev-shm-usage",
    "--window-size=1920,1080",
    "--disable-blink-features=AutomationControlled",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-background-networking", "--disable-component-update",
    "--disable-default-apps", "--no-first-run", "--no-default-browser-check",
]


def launch_args(prof, profil_dir=None, proxy=None, debug_port=None):
    """Susunan argumen Chrome utk Playwright/eksekusi langsung.
    [bedah 2026-10-12] persona DIPAKAI NYATA (dulu prof diabaikan = semua
    chrome kembar 'laptop sama'): window-size & bahasa ikut persona."""
    a = [x for x in ARGS_DASAR if not x.startswith("--window-size=")]
    prof = prof or build_persona()
    vp = prof.get("viewport") or {}
    w, h = vp.get("width") or 1366, vp.get("height") or 768
    a.append(f"--window-size={w},{h}")
    loc = (prof.get("locale") or "en-US").split("_")[0].replace("-", "_")
    a.append(f"--lang={prof.get('locale') or 'en-US'}")
    if profil_dir:
        a.append(f"--user-data-dir={profil_dir}")
    if proxy:
        a.append(f"--proxy-server={proxy}")
    if debug_port:
        a.append(f"--remote-debugging-port={debug_port}")
    return a


def apply_ua_cdp(ctx, pg, prof):
    """Sinkronkan UA + User-Agent-Client-Hints (konsistensi L1/L2 DataDome)."""
    try:
        pg.context.new_cdp_session(pg)
        cdp = ctx.new_cdp_session(pg)
        merek = "Chrome" if "Chrome/" in prof["ua"] else "Chromium"
        versi = prof["ua"].split("Chrome/")[1].split(".")[0] if "Chrome/" in prof["ua"] else "150"
        cdp.send("Emulation.setUserAgentOverride", {
            "userAgent": prof["ua"],
            "platform": "Linux" if "X11" in prof["ua"] else "Windows",
            "userAgentMetadata": {
                "brands": [{"brand": "Chromium", "version": versi},
                           {"brand": f"Not_A Brand", "version": "24"},
                           {"brand": merek, "version": versi}],
                "fullVersionList": [{"brand": "Chromium", "version": versi},
                                    {"brand": merek, "version": versi}],
                "platform": "Linux" if "X11" in prof["ua"] else "Windows",
                "platformVersion": "6.6.0", "architecture": "x86",
                "model": "", "mobile": False,
            }})
    except Exception:
        pass


def ensure_display(n="99"):
    """Xvfb :99 utk browser automation HEADED (headless terdeteksi sensor
    DataDome -> device-check menggantung). Sekalian: jendela tak muncul di
    desktop user (ganggu kerja) karena berada di layar virtual."""
    import os
    import subprocess
    disp = f":{n}"
    sock = f"/tmp/.X11-unix/X{n}"
    if not os.path.exists(sock):
        try:
            subprocess.Popen(["Xvfb", disp, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            import time
            for _ in range(20):
                if os.path.exists(sock):
                    break
                time.sleep(0.25)
        except Exception:
            pass
    os.environ["DISPLAY"] = disp
    return disp


def bersihkan_chrome_nakal():
    """RAM-sehat: bunuh chrome yatim toolkit (identik pola kerja yang terbukti)."""
    n = 0
    try:
        # [fix 2026-10-07] pola lama 'remote-debugging-port=' MEMBUNUH browser
        # manual user (mitm 9222)! Sekarang hanya profil otomasi toolkit.
        # [fix 444-zombie 2026-10-07] pola lama JUGA tidak mengenali profil
        # toolkit kita sendiri (results/cdp-profil/*) -> 444 proses chrome yatim
        # menumpuk, RAM 2GB, mint x-device-token mati massal. Ikut disapu kini;
        # browser manual (mitm/9222/brave) TIDAK memakai jalur cdp-profil.
        for pid in subprocess.run(["pgrep", "-f", "user-data-dir=/tmp/gh-|user-data-dir=/tmp/eb-|user-data-dir=/tmp/profil-oto|user-data-dir=[^ ]*cdp-profil"],
                                  capture_output=True, text=True).stdout.split():
            if pid.isdigit() and int(pid) != __import__("os").getpid():
                try:
                    import os
                    os.kill(int(pid), 15)
                    n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return n


def sapu_chrome_yatim():
    """[anti-zombie 2026-10-07] aman dipanggil DI TENGAH run: hanya membunuh
    chrome toolkit yang induknya SUDAH MATI (PPID=1 = yatim). Chrome milik run
    berjalan (PPID = proses python hidup) TIDAK tersentuh."""
    n = 0
    try:
        out = subprocess.run(["pgrep", "-f", "user-data-dir=[^ ]*cdp-profil"],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            if not pid.isdigit() or int(pid) == os.getpid():
                continue
            try:
                ppid = int(subprocess.run(["ps", "-o", "ppid=", "-p", pid],
                                          capture_output=True,
                                          text=True).stdout.strip() or 0)
            except Exception:
                continue
            if ppid <= 1:                 # yatim beneran
                try:
                    os.kill(int(pid), 15)
                    n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return n


def pasang_pembersih_sinyal():
    """[anti-zombie permanen 2026-10-07] pasang di awal main tiap alat:
    saat dihentikan paksa (SIGTERM/SIGINT), chrome toolkit ikut DIBERSIHKAN dulu
    sebelum keluar. Akar insiden 444-proses: run yang dibunuh tak sempat menutup
    browser (blok cleanup tak pernah jalan) -> tiap stop paksa menyisakan
    ~15-20 proses chrome; ~20 kali stop paksa = ratusan proses."""
    import signal

    def _bersih(*_):
        try:
            bersihkan_chrome_nakal()
        except Exception:
            pass
        os._exit(0)

    try:
        signal.signal(signal.SIGTERM, _bersih)
        signal.signal(signal.SIGINT, _bersih)
    except Exception:
        pass


if __name__ == "__main__":
    p = build_persona()
    print("fp_lite OK:", p["ua"][:60], "|", p["locale"], p["viewport"])
