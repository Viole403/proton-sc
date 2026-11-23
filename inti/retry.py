#!/usr/bin/env python3
"""retry.py — kebijakan retry brutalism TERPUSAT (RISET-MIGRASI §4):
  * klasifikasi kegagalan INFRA (layak ulang) vs REAL (jangan diulang) —
    pola regex disalin dari run.klasifikasi (historis teruji).
  * backoff eksponensial + jitter (pengganti sleep keras 2/20 dtk)."""
from __future__ import annotations

import random
import re
import time

RE_INFRA = re.compile(
    r"gate gasak|init_failed|tak menyimpan artefak|token form|tak ada sesi proxy|exit-stabil|"
    r"terbakar|robot-block|geo .*mismatch|octocaptcha|OTP inbox tak datang|"
    r"C8 5\d\d|C8 4\d\d|signed in with another tab|submit OTP gagal|login ulang pasca-OTP gagal|login-ulang|"
    r"timeout|timed out|eksepsi|connection|curl: \(\d+\)|device-token|proxy|"
    r"Connect|Timeout|SSLError|OSError", re.I)
RE_REAL = re.compile(r"TOTP secret|konfirmasi TOTP|kolom OTP|\[DRY-RUN\]", re.I)


def klasifikasi(sebab: str) -> str:
    """'INFRA' = layak retry (ganti exit/proses mati/sesi putus) | 'REAL' = jangan."""
    if RE_REAL.search(sebab or ""):
        return "REAL"
    return "INFRA" if RE_INFRA.search(sebab or "") else "REAL"


def backoff(coba_ke: int, basis: float = 1.0, faktor: float = 2.0,
            maks: float = 60.0, jitter: float = 0.3) -> float:
    """Jeda eksponensial + jitter acak 0..jitter*nilai.
    coba_ke mulai dari 1. Return detik — pemanggil `time.sleep(backoff(...))`."""
    nilai = min(maks, basis * (faktor ** max(0, coba_ke - 1)))
    return nilai + random.uniform(0, jitter * nilai)


def tidur(coba_ke: int, **kw) -> float:
    """backoff() + langsung tidur. Return detik yang ditidurkan (utk log)."""
    d = backoff(coba_ke, **kw)
    time.sleep(d)
    return d


def ulang(fn, maks: int = 3, hanya: str = "INFRA", log=None):
    """Ulang `fn()` maks `maks`x; berhenti awal bila hasil sukses ATAU vonis
    kegagalan != `hanya` ('INFRA' = hanya infra yang diulang; None = ulang semua).
    Return (hasil, percobaan_ke). Eksepsi TERAKHIR dilempar bila semua gagal."""
    galat = None
    for i in range(1, maks + 1):
        try:
            return fn(), i
        except Exception as e:
            galat = e
            if hanya and klasifikasi(str(e)) != hanya:
                raise
            if i < maks:
                if log:
                    log(f"[retry] {type(e).__name__}: {str(e)[:60]} -> ulang {i + 1}/{maks}")
                tidur(i)
    raise galat
