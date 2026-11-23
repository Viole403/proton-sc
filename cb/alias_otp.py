#!/usr/bin/env python3
"""alias_otp.py — klien alias headmail/evilmail utk rantai bebas-Proton:
  buat_alias()          -> (mail, meta) alias domain custom HEADMAIL (LOLOS CB;
                           evilmail = fallback; pool evilmail restricted di CB)
  buat_alias(kumpul="semua") -> alias ROUND-ROBIN seluruh domain non-gmail
                           headmail+evilmail (KEBIJAKAN alamat RECOVERY 2026-10-07)
  ambil_kode(mail)      -> kode OTP dari kotak masuk alias (headmail long-poll API)
  daftar_domain()       -> domain headmail; daftar_semua_domain() = gabungan
Kunci via env HEADMAIL_KEY / EVILMAIL_KEY (diisi driver dari data/settings.json
proton-e2e — hanya DIBACA, tool proton tak dieksekusi: pemisahan proyek).

KEBIJAKAN DOMAIN (2026-10-07): HANYA domain non-gmail; rotasi ROUND-ROBIN merata
across kumpul (JANGAN pres satu domain terus-menerus). Kursor persist di
data/rr-<kumpul>.ctr — antar worker & antar run tetap merata."""
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
import threading
import time
import urllib.parse

import requests

HEADMAIL = "https://headmail.flowjob-lab.workers.dev"
EVILMAIL = "https://evilmail.pro"
UA = "Mozilla/5.0"
# [2026-10-07 user] website evilmail.pro bermasalah — pakai HEADMAIL dulu sampai
# kunci/domain evilmail baru (~70 domain) turun. Set EVILMAIL_AKTIF=1 saat pulih.
# [2026-11-03] kebalikan: HEADMAIL yang mati (500 outage), evilmail SEHAT
# (uji API random-email sukses). User putuskan pakai evilmail -> default NYALA.
# [2026-11-03 malam] headmail PULIH (200) -> user: kembali ke versi utama
# headmail (domain evilmail ditolak Proton: 422 2011 'Email domain not
# allowed'). Evilmail hanya cadangan via env EVILMAIL_AKTIF=1.
EVILMAIL_AKTIF = os.environ.get("EVILMAIL_AKTIF", "0") == "1"
HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(HERE, "data")
_DOM_EVIL_CACHE = os.path.join(_DATA, "evilmail-domains.txt")
_kunci_rr = threading.Lock()
_kunci_dom = threading.Lock()
_cache_evil = {"doms": [], "t": 0.0}


def _keys():
    hk = os.environ.get("HEADMAIL_KEY", "")
    ek = os.environ.get("EVILMAIL_KEY", "")
    if not hk or not ek:   # [fix] fallback baca data/settings.json (dulu hanya env —
        try:               #   jalur langsung tanpa brutalism.sh = key kosong = alias mati)
            import json as _j
            sj = _j.load(open(os.path.join(str(konfig.DATA), "settings.json")))
            hk = hk or sj.get("HEADMAIL_KEY", "")
            ek = ek or sj.get("EVILMAIL_KEY", "")
        except Exception:
            pass
    return hk, ek


def daftar_domain():
    hk, ek = _keys()
    out = []
    if hk:
        try:
            d = requests.get(f"{HEADMAIL}/api/v1/domains",
                             headers={"authorization": "Bearer " + hk, "user-agent": UA},
                             timeout=25).json()
            out += [x.get("domain") for x in (d.get("domains") or []) if x.get("domain")]
        except Exception:
            pass
    # [fix domain 2026-10-14] Hostinger PEMILIK DITANGGUHKAN: 16 dari 21 domain
    # MATI -> OTP recovery tak pernah datang -> rantai menggantung menit-menit
    # per pass (biang kelambatan 5akun/16mnt). Whitelist hanya domain hidup
    # (pemilik menonaktifkan sisanya di dashboard). Override via DOMAIN_HIDUP.
    hidup = [x.strip().lower() for x in os.environ.get(
        "DOMAIN_HIDUP",
        "mailenvol.com,hopefulluk.net,ukdealershipdeal.com,kilatku.net,ruginsaykah.com"
    ).split(",") if x.strip()]
    return [d for d in out if str(d).lower() in hidup] or hidup


def daftar_domain_evilmail():
    """Domain evilmail yang PERNAH dipakai (riwayat /api/accounts) — dipakai sbagai
    katalog domain round-robin; cache berkas (data/evilmail-domains.txt) + TTL."""
    hk, ek = _keys()
    now = time.time()
    if _cache_evil["doms"] and now - _cache_evil["t"] < 300:
        return list(_cache_evil["doms"])
    doms = set()
    if ek:
        try:
            d = requests.get(f"{EVILMAIL}/api/accounts",
                             headers={"x-api-key": ek, "user-agent": UA}, timeout=25).json()
            for a in (d.get("data") or []):
                if a.get("domain"):
                    doms.add(a["domain"])
        except Exception:
            pass
        # [fix 2026-11-03] katalog jangan hanya riwayat akun (basi/kosong utk
        # key baru) — gabungkan daftar domain tersedia resmi (free+premium).
        try:
            d2 = requests.get(f"{EVILMAIL}/api/domains/available",
                              headers={"x-api-key": ek, "user-agent": UA}, timeout=25).json().get("data", {})
            for x in (d2.get("free") or []) + (d2.get("premium") or []):
                doms.add(str(x).lower())
        except Exception:
            pass
    if not doms:
        try:
            for l in open(_DOM_EVIL_CACHE):
                if l.strip():
                    doms.add(l.strip())
        except Exception:
            pass
    if doms:
        try:
            os.makedirs(_DATA, exist_ok=True)
            with open(_DOM_EVIL_CACHE, "w") as f:
                f.write("\n".join(sorted(doms)) + "\n")
        except Exception:
            pass
    _cache_evil["doms"] = sorted(doms)
    _cache_evil["t"] = now
    return list(_cache_evil["doms"])


def daftar_semua_domain():
    """Kumpulan RECOVERY: seluruh domain non-gmail headmail+evilmail (urut stabil).
    evilmail ikut HANYA bila EVILMAIL_AKTIF (2026-10-07: website bermasalah)."""
    gabung = daftar_domain() + (daftar_domain_evilmail() if EVILMAIL_AKTIF else [])
    hidup = {x.strip().lower() for x in os.environ.get(
        "DOMAIN_HIDUP",
        "mailenvol.com,hopefulluk.net,ukdealershipdeal.com,kilatku.net,ruginsaykah.com"
    ).split(",") if x.strip()}   # [fix domain 2026-10-14] whitelist juga utk gabungan
    # [fix 2026-11-03] whitelist 'hidup' basi utk evilmail: domain baru
    # (evilbx.com dkk) dibuang semua -> sisa domain malah diarahkan ke headmail
    # yang mati -> 'alias: semua domain gagal'. Katalog evilmail kini diizinkan
    # lewat tanpa whitelist (daftarnya dari API evilmail sendiri, bukan tebakan).
    evil_set = set(daftar_domain_evilmail()) if EVILMAIL_AKTIF else set()
    # [2026-11-03] DOMAIN_HIDUP eksplisit = MODE KETAT: hanya domain tsb (utk
    # A/B uji domain mana yang lolos filter Proton 'Email domain not allowed').
    if os.environ.get("DOMAIN_HIDUP"):
        return sorted({d.lower() for d in gabung
                       if d and "gmail" not in d.lower() and d.lower() in hidup})
    return sorted({d.lower() for d in gabung
                   if d and "gmail" not in d.lower()
                   and (d.lower() in hidup or d.lower() in evil_set)})


def domain_berikut(kumpul="semua"):
    """ROUND-ROBIN merata: kursor persisten per kumpul -> 1 siklus penuh domain
    sebelum domain berulang (JANGAN pres satu domain)."""
    pool = daftar_semua_domain() if kumpul == "semua" else daftar_domain()
    if not pool:
        raise RuntimeError("alias: tak ada domain non-gmail (headmail/evilmail mati)")
    ctr_file = os.path.join(_DATA, f"rr-{kumpul}.ctr")
    with _kunci_rr:
        try:
            n = int(open(ctr_file).read().strip() or "0")
        except Exception:
            n = 0
        try:
            os.makedirs(_DATA, exist_ok=True)
            with open(ctr_file, "w") as f:
                f.write(str(n + 1))
        except Exception:
            pass
    return pool[n % max(1, len(pool))]


def _prov_domain(dom):
    dom = (dom or "").lower()
    # [fix 2026-11-03] jangan klasifikasi via daftar_domain(): saat headmail
    # mati fungsi itu melempar balik whitelist (DOMAIN_HIDUP) -> SEMUA domain
    # dianggap headmail -> dikirim ke API mati -> 'semua domain gagal'.
    # Katalog evilmail = otoritatif utk domainnya sendiri.
    if dom in {d.lower() for d in daftar_domain_evilmail()}:
        return "evilmail"
    return "headmail"


def _buat_headmail(dom):
    hk, ek = _keys()
    r = requests.post(f"{HEADMAIL}/api/v1/aliases",
                      headers={"authorization": "Bearer " + hk, "user-agent": UA,
                               "content-type": "application/json"},
                      json={"source": "domain", "total": 1, "domains": [dom]},
                      timeout=25).json()
    mail = (r.get("created") or [{}])[0].get("email")
    return mail


def _buat_evilmail(dom):
    hk, ek = _keys()
    r = requests.post(f"{EVILMAIL}/api/random-email",
                      headers={"x-api-key": ek, "user-agent": UA,
                               "content-type": "application/json"},
                      json={"domain": dom, "count": 1, "passwordLength": 20},
                      timeout=25).json()
    e = (r.get("data", {}).get("emails") or [{}])[0]
    return (e.get("email"), e.get("password", "")) if e.get("email") else (None, "")


def buat_alias(domain=None, kumpul="headmail"):
    """(mail, meta) — `kumpul="headmail"` (kompatibel alias_cb: aman utk CodeBuddy)
    atau `kumpul="semua"` (RECOVERY: round-robin gabungan headmail+evilmail).
    `domain` memaksa satu domain; gagal = coba domain berikutnya (maks 5)."""
    pool = daftar_semua_domain() if kumpul == "semua" else daftar_domain()
    urut = [domain] if domain else []
    for _ in range(5):
        urut.append(domain_berikut(kumpul))
    dicoba = set()
    for dom in urut:
        if not dom or dom in dicoba:
            continue
        dicoba.add(dom)
        prov = _prov_domain(dom)
        if prov == "evilmail" and not EVILMAIL_AKTIF:
            continue                      # evilmail bermasalah (2026-10-07)
        try:
            if prov == "headmail":
                mail = _buat_headmail(dom)
                if mail:
                    return mail, {"provider": "headmail", "domain": dom}
            else:
                mail, pw = _buat_evilmail(dom)
                if mail:
                    return mail, {"provider": "evilmail", "domain": dom,
                                  "acct_pass": pw}
        except Exception:
            continue
    raise RuntimeError(f"alias: semua domain gagal ({len(dicoba)} dicoba)")


def ambil_kode(mail, sejak=None, budget=240, provider=None):
    """Kode OTP dari kotak masuk alias (poll API sampai `budget` dtk).
    `provider` = "headmail"/"evilmail"; bila kosong diketahui dari domain alias."""
    hk, ek = _keys()
    prov = provider or _prov_domain(mail.split("@")[-1])
    sejak = sejak or time.time()
    akhir = time.time() + budget
    if prov == "headmail":
        while time.time() < akhir:
            q = urllib.parse.urlencode({"email": mail, "wait": 25,
                                        "since": sejak - 30, "any": 1})
            try:
                d = requests.get(f"{HEADMAIL}/api/v1/otp?{q}",
                                 headers={"authorization": "Bearer " + hk,
                                          "user-agent": UA}, timeout=40).json()
            except Exception:
                time.sleep(4)
                continue
            if d.get("found") and d.get("otp"):
                return str(d["otp"])
        return None
    import re
    while time.time() < akhir:
        q = urllib.parse.urlencode({"email": mail})
        try:
            msgs = (requests.get(f"{EVILMAIL}/api/accounts/inbox?{q}",
                                 headers={"x-api-key": ek, "user-agent": UA},
                                 timeout=25).json() or {}).get("data") or []
        except Exception:
            time.sleep(6)
            continue
        for m in msgs:
            teks = f"{m.get('subject', '')} {m.get('preview') or m.get('snippet') or ''}"
            mm = re.search(r"(?<!\d)(\d{6})(?!\d)", teks)
            if not mm:
                uid = m.get("id") or m.get("uid")
                if uid is not None:
                    try:
                        dd = (requests.get(f"{EVILMAIL}/api/accounts/message/{uid}?{q}",
                                           headers={"x-api-key": ek, "user-agent": UA},
                                           timeout=25).json() or {}).get("data") or {}
                        mm = re.search(r"(?<!\d)(\d{6})(?!\d)",
                                       f"{dd.get('text', '')} {dd.get('html', '')}")
                    except Exception:
                        pass
            if mm:
                return mm.group(1)
        time.sleep(8)
    return None


if __name__ == "__main__":
    print("domain headmail:", daftar_domain()[:4])
    print("domain gabungan:", len(daftar_semua_domain()), daftar_semua_domain()[:4])
    print("alias:", buat_alias())
