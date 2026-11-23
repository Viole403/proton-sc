#!/usr/bin/env python3
"""tulis.py — helper tulis brutalism (RISET-MIGRASI §4):
  * kunci_file()  : fcntl.flock lintas-PROSES (pengganti kunci threading yang
                    bocor antar proses — bug #8).
  * tulis_unik()  : append + dedupe per kunci (field-1) — resume/paralel tidak
                    pernah menulis baris dobel (bug #5).
  * catat_pid/bunuh_pidfile(): pidfile per-PID — pengganti `pkill -f` yang
                    tembak lintas-run / self-match (bug (b)). CLI:
                      python3 tulis.py bunuh results/run.pid [pidfile ...]"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import time
from contextlib import contextmanager


@contextmanager
def kunci_file(jalur):
    """flock EKSKLUSIF lintas-proses pada berkas kunci sampingan <jalur>.kunci."""
    jalur = str(jalur)
    os.makedirs(os.path.dirname(os.path.abspath(jalur)) or ".", exist_ok=True)
    fh = open(jalur + ".kunci", "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def baca(jalur) -> list:
    try:
        with open(jalur, encoding="utf-8", errors="replace") as f:
            return [l.rstrip("\n") for l in f]
    except OSError:
        return []


def tambah(jalur, baris: str) -> None:
    """Append 1 baris terkunci (lintas-proses)."""
    with kunci_file(jalur):
        with open(jalur, "a", encoding="utf-8") as f:
            f.write(baris.rstrip("\n") + "\n")


def tulis_unik(jalur, baris: str, kunci: str = None) -> bool:
    """Append `baris` bila kunci (field-1, atau baris utuh) BELUM ada. Return True
    bila baris baru ditulis. Terkunci lintas-proses + dedupe isi lama."""
    baris = baris.rstrip("\n")
    kunci = (kunci if kunci is not None else baris).strip()
    with kunci_file(jalur):
        sudah = False
        try:
            with open(jalur, encoding="utf-8", errors="replace") as f:
                for l in f:
                    l = l.strip()
                    if not l:
                        continue
                    if (l.split("|", 1)[0].strip() if kunci != baris else l) == kunci:
                        sudah = True
                        break
        except OSError:
            pass
        if sudah:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(str(jalur))) or ".", exist_ok=True)
        with open(jalur, "a", encoding="utf-8") as f:
            f.write(baris + "\n")
        return True


def tulis_json(jalur, obj) -> None:
    """Tulis JSON (sidecar) terkunci lintas-proses."""
    with kunci_file(jalur):
        os.makedirs(os.path.dirname(os.path.abspath(str(jalur))) or ".", exist_ok=True)
        with open(jalur, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1, default=str)


def dedupe_file(jalur) -> int:
    """Buang baris dobel per field-1 (PERTAMA menang). Return jumlah dibuang."""
    with kunci_file(jalur):
        lihat, unik, buang = set(), [], 0
        for l in baca(jalur):
            if not l.strip():
                continue
            k = l.split("|", 1)[0].strip()
            if k in lihat:
                buang += 1
                continue
            lihat.add(k)
            unik.append(l)
        if buang:
            with open(jalur, "w", encoding="utf-8") as f:
                f.write("\n".join(unik) + ("\n" if unik else ""))
    return buang


# ---------------------------------------------------------------- pidfile
def catat_pid(pidfile, pid: int) -> None:
    with kunci_file(pidfile):
        with open(str(pidfile), "a") as f:
            f.write(f"{int(pid)}\n")


def daftar_pid(pidfile) -> list:
    out = []
    for l in baca(pidfile):
        l = l.strip()
        if l.isdigit() and int(l) > 1:
            out.append(int(l))
    return out


def hidup(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def bunuh_pidfile(*pidfiles, jeda: float = 3.0) -> int:
    """TERM -> tunggu -> KILL per PID di pidfile (TANPA pkill pattern — aman
    lintas-run & tanpa self-match). PID mati/diri sendiri dilewati. Return jumlah
    proses yang dikirim sinyal."""
    saya = os.getpid()
    korban = []
    for pf in pidfiles:
        for pid in daftar_pid(pf):
            if pid != saya and hidup(pid):
                korban.append(pid)
    for pid in korban:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    t0 = time.time()
    while time.time() - t0 < jeda and any(hidup(p) for p in korban):
        time.sleep(0.2)
    for pid in korban:
        if hidup(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    for pf in pidfiles:
        try:
            os.remove(str(pf))
        except OSError:
            pass
        try:
            os.remove(str(pf) + ".kunci")
        except OSError:
            pass
    return len(korban)


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "bunuh":
        n = bunuh_pidfile(*sys.argv[2:])
        print(f"[tulis] {n} proses dihentikan per-PID (pidfile dibersihkan)")
    else:
        print("pakai: python3 tulis.py bunuh <pidfile> [pidfile ...]")
