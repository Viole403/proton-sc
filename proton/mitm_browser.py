#!/usr/bin/env python3
"""mitm_browser.py — hidupkan ROUTER MITM (cache+capture) + Chrome manual
di atasnya. Dipakai utk merancang flow lanjutan: apa pun yang dilakukan di
browser ini terekam di router (asset immutable masuk cache, API tercatat).
Chrome persisten (profil sendiri) + remote debugging :9222 utk otomasi nanti.
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
import json
import os
import subprocess
import sys
import time

HERE = str(konfig.PROTON_DIR)  # [edit brutalism] path terpusat
sys.path.insert(0, HERE)
from pathlib import Path
import core
import fp_profile

PORT = 8089
URL = "https://account.proton.me/mail/signup?plan=free&noPromo=1&ref=mail_plus_intro-2"
PROFIL = "/tmp/mitm-browser-profile"

# desktop ASLI lebih diutamakan daripada Xvfb otomasi (:99) — kalau tidak,
# jendela mendarat di layar virtual dan tak terlihat user
if not os.environ.get("DISPLAY") or not os.path.exists(f"/tmp/.X11-unix/X{os.environ.get('DISPLAY', ':99').lstrip(':')}"):
    fp_profile.ensure_display()
print("DISPLAY =", os.environ.get("DISPLAY"), flush=True)

# 1) router mitm (direct upstream — tanpa proxy berbayar utk kerja manual)
(core.DATA / f"upstream-{PORT}.json").write_text(json.dumps({"proxy": "", "auth": ""}))
if not core.router_alive(PORT):
    log = open(core.LOGS / f"router-{PORT}.log", "ab")
    subprocess.Popen(
        [str(konfig.MITMDUMP), "--listen-host", "127.0.0.1",
         "--listen-port", str(PORT), "-s", str(Path(HERE) / "router_cache.py"),
         # capture PENUH (header+body tiap flow) utk bedah mendalam sesi manual
         "--save-stream-file", str(core.LOGS / f"manual-flows-{PORT}.mitm"),
         "--set", "termlog_verbosity=warn"],
        cwd=HERE, shell=False, start_new_session=True,
        stdout=log, stderr=subprocess.STDOUT,
        env=dict(os.environ, UPSTREAM_FILE=str(core.DATA / f"upstream-{PORT}.json"),
                 STATS_FILE=str(core.router_stats_file(PORT))))
    for _ in range(20):
        if core.router_alive(PORT):
            break
        time.sleep(0.5)
print(f"router mitm: {'hidup' if core.router_alive(PORT) else 'GAGAL'} di 127.0.0.1:{PORT}", flush=True)

# 2) Chrome manual lewat router (persisten + remote debugging)
prof = fp_profile.build_profile()
os.environ["FONTCONFIG_FILE"] = fp_profile.write_fonts_conf(prof)
args = ["/usr/bin/google-chrome"] + fp_profile.chrome_args(prof) + [
    f"--user-data-dir={PROFIL}",
    f"--proxy-server=http://127.0.0.1:{PORT}",
    "--remote-debugging-port=9222",
    "--ignore-certificate-errors",   # CA mitmproxy ditelan utk browser riset ini
    "--no-first-run", "--no-default-browser-check",
    URL,
]
subprocess.Popen(args, cwd=HERE, shell=False, start_new_session=True,
                 stdout=open("/dev/null", "wb"), stderr=subprocess.STDOUT)
print(f"chrome: dibuka di {URL}", flush=True)
print("debug otomasi: http://127.0.0.1:9222 | profil: " + PROFIL, flush=True)
print("hentikan dgn: kill <pid-mitm-browser> (pidfile results/router.pid utk mitmdump;  # [edit brutalism] dulu saran pkill -f", flush=True)
