#!/usr/bin/env python3
"""cek_aman.py — jalankan cek_semua TANPA menyentuh stok asli:
input = salinan registered.txt, semua keluaran dialihkan ke /tmp.
(amankan dari tabrakan dengan run stok_cb yang sedang klaim registered.txt,
dan refresh|client token TAK dihapus seperti perilaku cek_semua bawaan)."""
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
import shutil
import sys
import time

import cek_semua as c

ts = int(time.time())
shutil.copyfile(c.REG, f"/tmp/cek-input-{ts}.txt")
c.REG = f"/tmp/cek-input-{ts}.txt"          # baca salinan saja
c.HASIL = f"/tmp/cek-kesehatan-{ts}.txt"
c.ARSIP = f"/tmp/cek-suspend-{ts}.txt"
print(f"cek aman: input salinan /tmp/cek-input-{ts}.txt | stok asli TIDAK diubah", flush=True)
c.utama(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
