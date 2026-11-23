#!/usr/bin/env python3
"""konfig.py — SATU sumber semua path/port brutalism (RISET-MIGRASI §4).
Modul ringan (stdlib saja) — aman diimpor di mana pun, termasuk mode uji-format
yang TANPA jaringan. Override venv: env BRUTALISM_VENV."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# ---------------------------------------------------------------- struktur
AKAR = Path(__file__).resolve().parent.parent      # .../brutalism/
INTI = AKAR / "inti"
PROTON = AKAR / "proton"
GITHUB = AKAR / "github"
CB = AKAR / "cb"
CEK = AKAR / "cek"
VENDOR = AKAR / "vendor"
ARSIP = AKAR / "arsip"

DATA = AKAR / "data"
RESULTS = AKAR / "proton" / "results"

# alias kompatibel (dipakai modul yang sudah diedit; = folder kode domain)
PROTON_DIR = PROTON
GITHUB_DIR = GITHUB
CB_DIR = CB
CEK_DIR = CEK

# ---------------------------------------------------------------- venv/eksternal
# venv hidup = milik proton-e2e (instruksi user) — dirujuk, TIDAK disalin.
# Default: venv di root paket (./venv/bin/python), fallback python3 sistem.
_VENV_DEFAULT = AKAR / "venv" / "bin" / "python"
_VENV_PY = str(_VENV_DEFAULT) if _VENV_DEFAULT.exists() else "python3"
VENV_PY = Path(os.environ.get("BRUTALISM_VENV", _VENV_PY))
MITMDUMP = Path(os.environ.get("BRUTALISM_MITMDUMP",
    str(AKAR / "venv" / "bin" / "mitmdump") if (AKAR / "venv" / "bin" / "mitmdump").exists() else "mitmdump"))
SUMBER_BEKAS = Path(os.environ.get(
    "BRUTALISM_SUMBER_BEKAS",
    str(AKAR / "data" / "proton-bekas-github.txt")))   # data user, baca-saja

# ---------------------------------------------------------------- browser biner
_CHROME_CANDIDATES = [
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]
CHROME_BIN = Path(os.environ.get("BRUTALISM_CHROME_BIN",
    next((c for c in _CHROME_CANDIDATES if Path(c).exists()), "google-chrome")))

# ---------------------------------------------------------------- port
ROUTER_PORT = int(os.environ.get("BRUTALISM_ROUTER_PORT", "8095"))
ROUTER_PORT_BASE = 8080          # pabrik proton: port = BASE + wid (producer)
DISPLAY = ":99"                  # Xvfb (cdp_gate/fp_lite)
BATAS_WAKTU_AKUN = 180.0         # batas keras per akun (stok_cb)

# ---------------------------------------------------------------- file akun
REGISTERED = RESULTS / "registered.txt"        # mail|pass[|refresh|client]
BEKAS = RESULTS / "bekas-github.txt"
GITHUB_COMBO = RESULTS / "github-combo.txt"    # mail|pass|totp
CB_COMBO = RESULTS / "codebuddy-combo.txt"     # mail|pass|token
SIDECAR = RESULTS / "codebuddy"                # sidecar <mail>.json (credit dst)
GH_COOKIES = RESULTS / "gh-cookies"            # cookie sesi GitHub per akun
BYTE_AKUN = RESULTS / "byte-akun"
DUMP = RESULTS / "dump"
STAT_JSONL = RESULTS / "oneshot-statistik.jsonl"
STAT_MD = RESULTS / "oneshot-statistik.md"
ONESHOOT_HASIL = RESULTS / "oneshoot-hasil.jsonl"
ONESHOOT_TOTP = RESULTS / "oneshoot-totp.txt"
ONESHOOT_TOKEN = RESULTS / "oneshoot-token.txt"

# ---------------------------------------------------------------- file data
PROXIES = Path(os.environ.get("BRUTALISM_PROXY_FILE", str(DATA / "proxies.txt")))                 # format host:port:user:pass
SETTINGS = DATA / "settings.json"              # HEADMAIL_KEY/EVILMAIL_KEY/dst
EXIT_HIJAU = DATA / "exit-hijau.txt"
DEVICE_MASK_JS = INTI / "device_mask.js"

# ---------------------------------------------------------------- pidfile
PID_RUN = RESULTS / "run.pid"
PID_ROUTER = RESULTS / "router.pid"
PID_PRODUCER = RESULTS / "producer.pid"
PID_SWEEP = RESULTS / "sweep.pid"

for _d in (DATA, RESULTS, SIDECAR, GH_COOKIES, BYTE_AKUN, DUMP, RESULTS / "logs"):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def upstream_json(port: int = None) -> Path:
    """router_cache upstream per port (ditulis core.start_router / stok_cb.rotasi_upstream)."""
    return DATA / f"upstream-{port or ROUTER_PORT}.json"


# ---------------------------------------------------------------- validasi proxy
_POROK = re.compile(r"^[A-Za-z0-9._~%+-]+$")   # token host/user (tanpa ':')


def validasi_proksi(path) -> dict:
    """Validasi format file proxy `host:port:user:pass` — TANPA jaringan.
    Return {"ok": [baris_sah], "tolak": [(no, baris, alasan)], "lewati": n_komentar_kosong}.
    Baris rusak TIDAK pernah senyap (bug run.muat_proxy lama: `except: pass`)."""
    ok, tolak, lewati = [], [], 0
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": [], "tolak": [(0, "-", f"file tak terbaca: {e}")], "lewati": 0}
    with fh:
        for no, ln in enumerate(fh, 1):
            baris = ln.strip()
            if not baris or baris.startswith("#"):
                lewati += 1
                continue
            alasan = None
            if baris.count(":") < 3:
                alasan = "bukan host:port:user:pass (titik dua kurang dari 3)"
            else:
                host, port, _user, _pw = baris.split(":", 3)
                if not host:
                    alasan = "host kosong"
                elif not port.isdigit() or not (1 <= int(port) <= 65535):
                    alasan = f"port bukan angka 1-65535 (terbaca {port!r})"
            if alasan:
                tolak.append((no, baris[:40] + ("…" if len(baris) > 40 else ""), alasan))
            else:
                ok.append(baris)
    return {"ok": ok, "tolak": tolak, "lewati": lewati}


def validasi_kunci_alias() -> dict:
    """Status kunci alias dari data/settings.json — nilai TIDAK pernah dikembalikan."""
    try:
        s = json.loads(SETTINGS.read_text())
    except Exception as e:
        return {"HEADMAIL_KEY": False, "EVILMAIL_KEY": False, "galat": str(e)[:60]}
    return {"HEADMAIL_KEY": bool(s.get("HEADMAIL_KEY")),
            "EVILMAIL_KEY": bool(s.get("EVILMAIL_KEY"))}


if __name__ == "__main__":
    print("AKAR     :", AKAR)
    print("VENV_PY  :", VENV_PY, "(ada)" if VENV_PY.exists() else "(TIDAK ADA)")
    print("PROXIES  :", PROXIES)
    print("REGISTERED:", REGISTERED)
    print("ROUTER   :", ROUTER_PORT)
    print("kunci alias:", validasi_kunci_alias())
