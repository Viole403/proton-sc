#!/usr/bin/env python3
"""proton_api.py — pembaca OTP inbox Proton API-MURNI (tanpa browser):
login SRP (vendor/srp.py) -> bahan kunci -> dekripsi PGP lokal (vendor/dekrip.py,
keyring RAM /dev/shm) -> ekstrak kode. Target 2-5 detik (vs 40-60 dtk jalur
browser). Pola diadaptasi dari vendor/baca_inbox.py + vendor/daftar_headless.py
(karya teman pengguna, terverifikasi vs dump asli)."""
from __future__ import annotations

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
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import dekrip  # noqa: E402
import srp  # noqa: E402

from curl_cffi import requests as crq  # noqa: E402

ACC = "https://account.proton.me"
APPV = "web-account@5.0.433.0"  # [fix 2026-11-05] samakan bundle HIDUP — dicek
# langsung dari bundle: curl halaman signup -> /assets/static/public-index.*.js
# -> grep 'APP_VERSION:"..."'. Emas 20 Okt = 429.0 (bundle saat itu); 5 Nov =
# 5.0.433.0. Versi basi = 'berbohong soal versi' (RISET-DEEP-10003 poin 3-4).
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")
# [fix 2026-11-05 hipotesis user: 'login dgn persona beda = trigger'] klien-2
# HARUS identik dgn browser signup kita (Chrome/155 asli, UA_SPOOF=0 -> UA
# native di atas). curl_cffi 0.16.3 BELUM punya target chrome155 (tertinggi
# chrome150 — JA3/JA4 identik dgn 155 selama cipher set sama).
KLIEN_TLS = "chrome150"


def _hdr():
    # [fix 2026-11-05 konteks emas] replika PERSIS header klien browser emas
    # (HAR idx=152/415/423): cookie-auth + origin + referer + sec-ch-ua +
    # accept-language. 'Bentuk polos' (Bearer tanpa origin/referer/hints =
    # klien API) = kandidat pemicu 'login kelihatan bot' (hipotesis user).
    # x-pm-product: emas hanya utk /users & keys/setup, TIDAK utk auth/settings.
    return {"content-type": "application/json",
            "accept": "application/vnd.protonmail.v1+json",
            "accept-language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Google Chrome";v="157", "Chromium";v="157", "Not(A:Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "origin": "https://account.proton.me",
            "referer": "https://account.proton.me/mail/signup?plan=free&noPromo=1&ref=mail_plus_intro-2",
            "x-pm-appversion": APPV, "x-pm-locale": "en_US",
            "user-agent": UA}


def _sesi_dari_refresh(mail, proxy=None):
    """[fix 10003] Build (sesi, H3) dari refresh-token SESSION SIGNUP — TANPA SRP.
    POST /api/auth/v4/sessions (GrantType=refresh_token) -> Code 1000 + AccessToken.
    Re-login SRP memicu 10003 ("akun pindah IP / login mencurigakan") — hindari.
    Sumber refresh+UID: cookie REFRESH-<uid> di browser-state/<mail>.json, atau
    registered.txt kolom 3-4 (refresh|uid). Return (s, H3) atau (None, None)."""
    import json as _j
    import urllib.parse as _up
    try:
        bs = os.path.join(str(konfig.RESULTS), "browser-state", f"{mail}.json")
        d = _j.load(open(bs, encoding="utf-8"))
    except Exception:
        d = None
    # 1) SESSION SIGNUP: cookie AUTH = token login user (BISA re-auth _srp_bukti).
    #    Pakai langsung sbg Bearer + muat cookie browser-state. TANPA SRP (10003)
    #    & TANPA refresh (token hasil refresh = 'logged-out', re-auth ditolak).
    # [fix 2026-10-22 bedah HAR+dump 48/48] blob 'eyJ' (JWE) = token SESI ANONIM
    #    Scopes:[] dari POST /auth/v4/sessions pra-signup (HAR idx=72) — BUKAN
    #    token login; server membalas 2001 'Cannot re-authenticate while being
    #    logged out'. Token USER = opaque non-eyJ pasca-POST /auth. Dulu eyJ
    #    didahulukan -> step-up 2001/401 (8 akun/48 jam). Kini: kandidat opaque
    #    DULU, probe read-only GET /settings — pakai yang membalas 200.
    auth = uid = None
    if d:
        kandidat = []
        for ck in d.get("cookies", []):
            n = str(ck.get("name", ""))
            v = ck.get("value") or ""
            if n.startswith("AUTH-") and v:
                kandidat.append((0 if not v.startswith("eyJ") else 1,
                                 v, n.split("-", 1)[1]))
        kandidat.sort(key=lambda x: x[0])
        for _prio, _v, _uid in kandidat:
            try:
                pr = None
                u = _url_proxy(proxy)
                if u:
                    pr = {"https": u, "http": u}
                s = crq.Session(impersonate=KLIEN_TLS, timeout=15, proxies=pr)
                for ck in d.get("cookies", []):
                    try:
                        s.cookies.set(ck["name"], ck["value"])
                    except Exception:
                        pass
                H = _hdr()
                H3 = dict(H, **{"x-pm-uid": _uid, "authorization": "Bearer " + _v})
                r = s.get(f"{ACC}/api/core/v4/settings", headers=H3, timeout=15)
                if r.status_code == 200:
                    return s, H3
                # 401/403 = token mati/anonim -> coba kandidat berikutnya
            except Exception:
                continue
        if kandidat:
            # semua probe gagal (jaringan/sesuatu) -> pakai kandidat terbaik
            # (opaque dulu) — jangan menyerah; perilaku lama tetap mengembalikan sesi
            try:
                pr = None
                u = _url_proxy(proxy)
                if u:
                    pr = {"https": u, "http": u}
                s = crq.Session(impersonate=KLIEN_TLS, timeout=30, proxies=pr)
                for ck in d.get("cookies", []):
                    try:
                        s.cookies.set(ck["name"], ck["value"])
                    except Exception:
                        pass
                H = _hdr()
                H3 = dict(H, **{"x-pm-uid": kandidat[0][2],
                                "authorization": "Bearer " + kandidat[0][1]})
                return s, H3
            except Exception:
                pass
    # 2) fallback: refresh-token -> /api/auth/v4/sessions (logged-out, tanpa re-auth)
    rt = uid = None
    try:
        if d:
            for ck in d.get("cookies", []):
                if str(ck.get("name", "")).startswith("REFRESH-"):
                    p = _j.loads(_up.unquote(ck["value"]))
                    rt = p.get("RefreshToken") or rt
                    uid = p.get("UID") or uid
                    break
    except Exception:
        pass
    if not rt:
        try:
            for l in open(str(konfig.REGISTERED), encoding="utf-8", errors="replace"):
                if l.split("|")[0].strip() == mail:
                    p = l.strip().split("|")
                    if len(p) >= 4:
                        rt = (p[2].strip() or None)
                        uid = (p[3].strip() or None)
                    break
        except Exception:
            pass
    if not rt:
        return None, None
    try:
        pr = None
        u = _url_proxy(proxy)
        if u:
            pr = {"https": u, "http": u}
        s = crq.Session(impersonate=KLIEN_TLS, timeout=30, proxies=pr)
        H = _hdr()
        H2 = dict(H, **{"x-pm-uid": uid or ""})
        r = s.post(f"{ACC}/api/auth/v4/sessions", headers=H2, json={
            "ResponseType": "token", "ClientID": "WebAccount",
            "GrantType": "refresh_token", "RefreshToken": rt, "UID": uid or ""})
        t = r.json() or {}
        at = t.get("AccessToken")
        if not at:
            return None, None
        H3 = dict(H, **{"x-pm-uid": t.get("UID") or uid or "",
                        "authorization": "Bearer " + at})
        return s, H3
    except Exception:
        return None, None


def _buka_modulus(blob_asc: str) -> bytes:
    """Modulus dikirim ter-encrypt PGP; buka dgn gpg (keyring temporer).
    [fix beban 2026-10-07] gpg subproses FLAKE saat beban (timeout/fork) —
    dulu 1 kegagalan = login mati senyap ("OTP inbox tak datang"). Kini 3x
    ulang; galat terakhir dilempar utk label jujur `login.alasan`."""
    galat = RuntimeError("gpg modulus: tak jalan")
    for ulang in range(3):
        home = tempfile.mkdtemp(dir="/dev/shm", prefix="mod-")
        try:
            p = subprocess.run(["gpg", "--homedir", home, "--batch", "--decrypt"],
                               input=blob_asc.encode(), capture_output=True,
                               timeout=30)
            if p.stdout.strip():
                return base64.b64decode(p.stdout.decode().strip())
            galat = RuntimeError(f"gpg modulus kosong (rc={p.returncode})")
        except (subprocess.TimeoutExpired, OSError, ValueError) as e:
            galat = e
        finally:
            shutil.rmtree(home, ignore_errors=True)
        time.sleep(0.6 * (ulang + 1))
    raise galat


def _url_proxy(proxy):
    """Terima URL `http://user:pass@host:port`, baris `host:port:user:pass`, atau
    dict {server, username, password} (bentuk run.muat_proxy) -> URL proxy."""
    if not proxy:
        return None
    if isinstance(proxy, dict):
        s = proxy.get("server") or ""
        s = s if "://" in s else "http://" + s
        u, pw = proxy.get("username") or "", proxy.get("password") or ""
        if u:
            s = s.replace("://", f"://{u}:{pw}@", 1)
        return s
    p = str(proxy)
    if "://" in p:
        return p
    if p.count(":") >= 3:
        h, port, u, pw = p.split(":", 3)
        return f"http://{u}:{pw}@{h}:{port}"
    return "http://" + p


def login(mail: str, pw: str, proxy: str = None):
    """Login SRP penuh. Return (sesi curl, headers ber-token) atau (None, None).
    `proxy` = URL proxy (`http://user:pass@host:port`), baris `host:port:user:pass`,
    atau dict {server,username,password} — rotasi klasifikasi (throttle login
    Proton per-IP, ±7-19 attempt/jendela). Galat terakhir dibukakan di
    `login.alasan` ("HTTP <st> Code <n>" / tipe eksepsi) supaya pemanggil bisa
    memberi label JUJUR: throttle vs kredensial vs jaringan."""
    login.alasan = ""
    # [fix 2026-10-13] KEMBALI ke SRP langsung — kode asli yang terbukti jalan.
    # AUTH cookie dari browser-state = 401/9106 (expired/scope kurang).
    # tarusaugor296 SUKSES via SRP = SRP aman, yang rusak itu AUTH cookie path.
    # _sesi_dari_refresh() tetap ada sbg utility, tapi TIDAK dipanggil dari login().
    pr = None
    u = _url_proxy(proxy)
    if u:
        pr = {"https": u, "http": u}
    s = crq.Session(impersonate=KLIEN_TLS, timeout=30, proxies=pr)
    H = _hdr()

    def _gagal(r):
        try:
            j = r.json() or {}
            login.alasan = f"HTTP {r.status_code} Code {j.get('Code')}"
        except Exception:
            login.alasan = f"HTTP {r.status_code}"
        return None, None

    try:
        r = s.post(f"{ACC}/api/auth/v4/sessions", headers=H, json={})
        if r.status_code != 200:
            return _gagal(r)
        t = r.json()
        # [fix 2026-11-05] replika PERSIS bootstrap browser emas (HAR idx=77):
        # sessions -> auth/cookies (Bearer -> server Set-Cookie AUTH/REFRESH)
        # -> semua request berikutnya COOKIE-ONLY + x-pm-uid. Bearer di request
        # login/recovery = bentuk klien API yang tak pernah dipakai app web.
        H_ac = dict(H, **{"x-pm-uid": t["UID"],
                          "authorization": "Bearer " + t["AccessToken"]})
        s.post(f"{ACC}/api/core/v4/auth/cookies", headers=H_ac)   # REFRESH via Set-Cookie (idx=77)
        s.cookies.set(f"AUTH-{t['UID']}", t["AccessToken"],
                      domain="account.proton.me")                 # sprti JS app menulis AUTH
        H2 = dict(H, **{"x-pm-uid": t["UID"]})
        r = s.post(f"{ACC}/api/core/v4/auth/info", headers=H2,
                   json={"Username": mail, "Intent": "Proton"})
        info = r.json()
        if "Modulus" not in info:
            return _gagal(r)
        mod = _buka_modulus(info["Modulus"])
        b = srp.bukti_login(mail, pw, mod, info["Salt"],
                            base64.b64decode(info["ServerEphemeral"]))
        r = s.post(f"{ACC}/api/core/v4/auth", headers=H2,
                   json={"ClientProof": b["clientProof"],
                         "ClientEphemeral": b["clientEphemeral"],
                         "SRPSession": info["SRPSession"],
                         "Username": mail, "PersistentCookies": 0})
        if r.status_code != 200:
            return _gagal(r)
        sesi = r.json() or {}
        # mode cookie (balasan EMAS, HAR idx=152): token TIDAK di body — AUTH
        # tiba via Set-Cookie (jar otomatis) + JS app; mode lama: token di body.
        if sesi.get("AccessToken"):
            s.cookies.set(f"AUTH-{sesi['UID']}", sesi["AccessToken"],
                          domain="account.proton.me")
        H3 = dict(H, **{"x-pm-uid": sesi["UID"]})
        return s, H3
    except Exception as e:
        login.alasan = f"{type(e).__name__}: {str(e)[:80]}"
        return None, None


def _ambil_json(s, url, H3, percobaan=3):
    """GET + parse JSON dgn ulang — throttling/timeout Proton di bawah beban
    (8 worker) bikin .json() melempar / balasan kosong; dulu 1 kegagalan =
    _tukang None = "OTP inbox tak datang" instan."""
    galat = ""
    for ulang in range(percobaan):
        try:
            j = s.get(url, headers=H3, timeout=25).json()
            if isinstance(j, dict):
                return j
            galat = f"bentuk aneh {type(j).__name__}"
        except Exception as e:
            galat = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(0.7 * (ulang + 1))
    _ambil_json.alasan = galat
    return {}


def _tukang(s, H3, mail, pw, percobaan=3):
    """Kumpulkan bahan kunci -> dekrip.Tukang siap pakai.
    [fix beban 2026-10-07] retry per-GET + retry bangun Tukang penuh (kelas
    gagal '_tukang' dulu membunuh baca_otp SEBELUM polling — surat ADA tapi
    tak pernah dibaca)."""
    for ulang in range(max(1, int(percobaan))):
        user = (_ambil_json(s, f"{ACC}/api/core/v4/users", H3) or {}).get("User") or {}
        salts = (_ambil_json(s, f"{ACC}/api/core/v4/keys/salts", H3)
                 or {}).get("KeySalts") or []
        addrs = (_ambil_json(s, f"{ACC}/api/core/v4/addresses", H3)
                 or {}).get("Addresses") or []
        if not (user.get("Keys") and salts and addrs and addrs[0].get("Keys")):
            time.sleep(0.8 * (ulang + 1))
            continue
        try:
            kunci_user = user["Keys"][0]
            salt = next((base64.b64decode(x["KeySalt"]) for x in salts
                         if x["ID"] == kunci_user["ID"]), b"")
            kunci_alamat = addrs[0]["Keys"][0]
            t = dekrip.Tukang(kunci_user["PrivateKey"], kunci_alamat["PrivateKey"],
                              kunci_alamat["Token"], salt, pw)
            if t.siap():
                return t
            t.tutup()                     # keyring bocor -> tutup sebelum ulang
        except Exception:
            pass
        time.sleep(0.8 * (ulang + 1))
    return None


def cek_flag_abuse(mail: str, pw: str, proxy: str = None):
    """Saring cepat (API, ~6-10 dtk, KB-an): 'FLAG' bila kotak masuk memuat surat
    "Automated Abuse Detection" (Proton mengkarantina surat eksternal -> kode
    GitHub tak akan pernah sampai — akar 'OTP inbox tak datang' 2026-10-06).
    'BERSIH' bila aman; None bila cek sendiri gagal (jangan buang akun)."""
    try:
        s, H3 = login(mail, pw, proxy=proxy)
        if s is None:
            return None
        msgs = (s.get(f"{ACC}/api/mail/v4/messages",
                      headers=H3, params={"Location": 5, "Page": 0,
                                          "PageSize": 15, "Sort": "Time",
                                          "Desc": 1}, timeout=20).json()
                or {}).get("Messages") or []
        for m in msgs:
            subj = (m.get("Subject") or "").lower()
            if "abuse detection" in subj:
                return "FLAG"
        return "BERSIH"
    except Exception:
        return None


def baca_otp(mail: str, pw: str, dari: str = "github",
             pola: str = r"\b(\d{6}|\d{8}|\d{3,4}-\d{3,4})\b", ulang: int = 8, jeda: float = 4.0,
             sejak: float = None, proxy=None):
    """Kembalikan kode OTP dari email terbaru pengirim `dari`; None bila gagal.
    API murni: login SRP + dekripsi lokal (2-5 dtk per siklus).
    `sejak` = epoch; pesan lebih TUA dari ini DILEWATI — [fix 422] dulu pembaca
    jatuh ke surat kode LAMA (kodelah basi -> GitHub 422 "Invalid launch code")
    saat surat baru gagal didekrip PGP (bedah-sisa-checkpoint §4 revisi).
    `proxy` = rotasi exit login (throttle login Proton per-IP). [fix 2026-10-21]
    TIDAK ADA lagi fallback direct — login ulang tetap lewat proxy."""
    s, H3 = _sesi_dari_refresh(mail, proxy=proxy)   # [fix 2026-10-23 otp_timeout]
    # sesi signup (token browser-state) — TANPA SRP (hemat risiko login ulang
    # akun segar) TAPI hanya bila benar2 bisa buka kunci (cek _tukang di bawah).
    tukang = _tukang(s, H3, mail, pw) if s is not None else None
    if tukang is None:
        # [fix 2026-11-08] AGENTS.md #14: sesi _sesi_dari_refresh = scope sempit
        # 'locked' -> keys/salts 403 -> _tukang None -> gejala 'surat ADA tapi
        # kode None' (bukti uji-ladder11: 'kode via API nihil' padahal OTP
        # GitHub terkirim). WAJIB proton_api.login (SRP, scope penuh) utk baca
        # OTP; sesi refresh hanya utk kasus kunci terbaca.
        s, H3 = login(mail, pw, proxy=proxy)
        if s is None and proxy:
            # [fix 2026-10-21 BOCOR JARINGAN] pola lama: login(mail, pw) = SRP
            # kredensial akun keluar DIRECT dari IP VPS -> Proton melihat akun
            # yang sama dibuka dari jaringan beda tanpa browser = trigger-suspend
            # 'bot-like' + sinyal 'abusive traffic from your network' (2028).
            # Sekarang: ulangi DENGAN proxy — TIDAK PERNAH login dari IP telanjang.
            s, H3 = login(mail, pw, proxy=proxy)
        if s is None:
            print("[inbox-api] login SRP gagal — sesi nihil", flush=True)
            return None
        tukang = _tukang(s, H3, mail, pw)
        if tukang is None:
            print("[inbox-api] tukang None pasca-SRP — keys/salts tak terbaca", flush=True)
            return None
    n_surat = 0
    try:
        for _ in range(max(1, int(ulang))):
            try:
                msgs = (s.get(f"{ACC}/api/mail/v4/messages",
                              headers=H3, params={"Location": 5, "Page": 0,
                                                  "PageSize": 15, "Sort": "Time",
                                                  "Desc": 1}, timeout=25).json()
                        or {}).get("Messages") or []
            except Exception:
                msgs = []
            n_surat = max(n_surat, len(msgs))
            for m in msgs:
                if sejak and float(m.get("Time") or 0) < sejak:
                    continue          # kode basi dari attempt sebelumnya — racun 422
                try:
                    det = (s.get(f"{ACC}/api/mail/v4/messages/{m['ID']}",
                                 headers=H3, timeout=25).json() or {}).get("Message") or {}
                except Exception:
                    continue
                ph = det.get("ParsedHeaders") or {}
                sumber = (ph.get("From", "") + " " + ph.get("Subject", "")).lower()
                if dari.lower() not in sumber:
                    continue
                badan = det.get("Body") or ""
                if "BEGIN PGP MESSAGE" in badan:
                    teks = tukang.buka_badan(badan) or ""
                else:
                    teks = dekrip.bersihkan_teks(badan)   # pesan polos (tanpa PGP)
                mm = re.search(pola, teks)
                if mm:
                    return mm.group(1)
            time.sleep(jeda)
        print(f"[inbox-api] nihil ({n_surat} surat terlihat, sejak={sejak})", flush=True)
        return None
    finally:
        tukang.tutup()


if __name__ == "__main__":
    print("OTP:", baca_otp(sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else "pakai: python proton_api.py <mail> <pw>")
