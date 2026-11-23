#!/usr/bin/env python3
"""recovery_email.py — LANGKAH 2 rantai (INSTRUKSI 2026-10-07, pintu resmi
/safety-review/email): tambah + VERIFIKASI alamat recovery Proton sebelum akun
dipakai daftar GitHub. Inilah yang TERBUKTI mengangkat pembatasan
'Automated Abuse Detection' (surat eksternal dikarantina -> "OTP inbox tak
datang" selamanya): setelah recovery terverifikasi, akun BISA menerima email
eksternal lagi — TIDAK perlu balapan jendela 5-25 menit.

Urutan API = capture manual-flows-8089.mitm (langkah telemetry resmi:
passwordVerification -> setRecoveryEmail -> verifyRecoveryEmail) — SEMUA
terverifikasi live 2026-10-07:
  1. login SRP (proton_api.login)
  2. PUT  /api/core/v4/settings/email {"Email": alias, "PersistPasswordScope": true}
     (sesi tanpa scope "password" -> 403 Code 9102; balas dgn bukti SRP digabung
      ke body: auth/info {ReauthScope:"password"} + ClientProof dst — TERUJI 200)
  3. POST /api/core/v4/verify/email (body kosong) -> 422 Code 9001
     Details.HumanVerificationToken + Methods ["ownership-email"]  <-- TOKEN
  4. GET  /api/core/v4/verification/ownership-email/<T>   (status)
     POST /api/core/v4/verification/ownership-email/<T>   (kirim kode ke alias)
  5. kode 6 digit dari alias (alias_otp.ambil_kode)
  6. POST /api/core/v4/verification/ownership-email/<T>/<kode>
  7. TUNAS: POST /api/core/v4/verify/email + header
     x-pm-human-verification-token(-type)  -> 200, Email.Status terverifikasi

Alamat recovery = alias headmail ROUND-ROBIN domain non-gmail (2026-10-07:
evilmail sedang bermasalah -> headmail dulu; saat pulih kumpul="semua").
Idempoten: Email sudah terverifikasi -> TUNAS langsak; Email pending (Status 0)
-> lanjut verifikasi dari langkah 3 dgn alamat YANG SAMA. Galat -> dump bukti
di results/dump/.

Pakai:  python3 recovery_email.py <mail> <pass> [--proxy host:port:user:pass]
Keluar JSON ringkas di stdout (token TIDAK pernah dicetak)."""
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
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import alias_otp                                   # noqa: E402
import proton_api                                  # noqa: E402

DUMP = os.path.join(HERE, "results", "dump")
ACC = proton_api.ACC
VERIF = "/api/core/v4/verification/ownership-email"


def _dump(nama, obj):
    try:
        os.makedirs(DUMP, exist_ok=True)
        with open(os.path.join(DUMP, f"recovery-{nama}.json"), "w") as f:
            json.dump(obj, f, indent=1, default=str, ensure_ascii=False)
    except Exception:
        pass


def _api(s, H3, method, path, body=None, hdr=None):
    h = dict(H3)
    if hdr:
        h.update(hdr)
    r = s.request(method, f"{ACC}{path}", headers=h,
                  json=body if body is not None else None, timeout=30)
    try:
        j = r.json()
    except Exception:
        j = {}
    return r.status_code, j, r.text


def status_recovery(s, H3):
    """(email, status_minta, mentah) dari GET /api/core/v4/settings.
    Status: 0 = pending/belum verifikasi, >=1 = terverifikasi."""
    try:
        _, j, _ = _api(s, H3, "GET", "/api/core/v4/settings")
        us = (j or {}).get("UserSettings") or (j or {}).get("Settings") or {}
        em = us.get("Email") or {}
    except Exception:
        return "", -1, {}
    return (em.get("Value") or ""), int(em.get("Status") or 0), us


def cek_flag(s, H3):
    """'FLAG' bila inbox memuat surat 'Automated Abuse Detection' (sesi yg sama)."""
    try:
        _, j, _ = _api(s, H3, "GET",
                       "/api/mail/v4/messages?Location=5&Page=0&PageSize=25"
                       "&Sort=Time&Desc=1")
        for m in (j or {}).get("Messages") or []:
            if "abuse detection" in (m.get("Subject") or "").lower():
                return "FLAG"
        return "BERSIH"
    except Exception:
        return "?"


def _srp_bukti(s, H3, mail, pw, scope="password"):
    """Bukti SRP utk re-auth scope (auth/info {ReauthScope:scope}) -> proof.
    scope="password" utk 9102, "full" utk 9106. Return dict auth ATAU None (gagal)."""
    import base64
    sys.path.insert(0, os.path.join(HERE, "vendor"))
    import srp
    st, j, raw = _api(s, H3, "POST", "/api/core/v4/auth/info",
                      {"Username": mail, "Intent": "Proton", "ReauthScope": scope})
    if st != 200 or "Modulus" not in j:
        _dump(f"{mail}-authinfo", {"status": st, "json": j, "raw": raw[:300]})
        return None
    mod = proton_api._buka_modulus(j["Modulus"])
    b = srp.bukti_login(mail, pw, mod, j["Salt"],
                        base64.b64decode(j["ServerEphemeral"]))
    return {"Username": mail, "SRPSession": j["SRPSession"],
            "ClientEphemeral": b["clientEphemeral"], "ClientProof": b["clientProof"]}


def pasang(mail, pw, proxy=None, log=lambda *a, **k: None, budget_kode=25,
           kumpul_alias="semua", proxy_lain=None):
    """LANGKAH 2 utk 1 akun. Return dict {ok, sebab, flag_awal, alias, dt}.
    Idempoten: recovery terverifikasi -> ok=True tanpa menyentuh apa pun.
    Self-healing: kode tak datang ke alias -> timpa dgn alias BARU (maks 2 siklus).
    `proxy_lain` = callable -> proxy baru (login proxy mati = coba lagi, jangan
    langsung vonis akun mati — bukti uji-5B "login gagal CONNECT tunnel failed")."""
    t0 = time.time()
    out = {"ok": False, "sebab": "", "flag_awal": "?", "alias": "", "dt": 0.0}
    s, H3 = proton_api.login(mail, pw, proxy=proxy)
    for _ in range(3 if (s is None and proxy_lain) else 0):
        proxy = proxy_lain()
        if not proxy:
            break
        s, H3 = proton_api.login(mail, pw, proxy=proxy)
    if s is None:
        out["sebab"] = f"login gagal ({getattr(proton_api.login, 'alasan', '')})"
        out["dt"] = time.time() - t0
        return out
    try:
        out["flag_awal"] = cek_flag(s, H3)
        alias, stat, _us = status_recovery(s, H3)
        if alias and stat >= 1:
            out.update(ok=True, alias=alias, dt=time.time() - t0,
                       sebab=f"TUNAS (sudah terverifikasi: {alias})")
            return out

        alias_ditolak = False
        for kesempatan in range(2):
            # [fix 2026-10-15] ganti alias HANYA bila alias lama ditolak
            # validator (12006). Dulu tiap retry memaksa alias baru -> vtoken
            # & kode terikat alias lama -> 12087 'Verification address mismatch'
            # (artefak retry kita sendiri, terlihat jelas di dump audit).
            if not alias or alias_ditolak:
                # --- setRecoveryEmail: alias ROUND-ROBIN + PUT settings/email
                alias, meta = alias_otp.buat_alias(kumpul=kumpul_alias)
                out["alias"] = alias
                body = {"Email": alias, "PersistPasswordScope": True}
                st, j, raw = _api(s, H3, "PUT", "/api/core/v4/settings/email", body)
                if st == 403 and ("9102" in str(j.get("Code"))
                                  or "9106" in str(j.get("Code"))):
                    # [fix 2026-10-13] SELALU pakai scope "password" dulu (TERUJI 200).
                    # scope "full" = _srp_bukti GAGAL (auth/info tidak terima "full").
                    # MissingScopes ["full","password"] tetap bisa diatasi "password"
                    # karena PUT settings/email butuh "password" scope, bukan "full".
                    auth = _srp_bukti(s, H3, mail, pw, "password")  # re-auth step-up
                    if not auth:
                        out["sebab"] = "scope password: auth/info+SRP gagal"
                        out["dt"] = time.time() - t0
                        return out
                    st, j, raw = _api(s, H3, "PUT", "/api/core/v4/settings/email",
                                      dict(body, **auth))
                if st >= 400:
                    # [fix gerbang-2] 422 Code 12006 "Email address failed
                    # validation" = alias DITOLAK validator Proton -> coba alias
                    # RR berikutnya (dulu langsung menyerah = 1 akun terbuang)
                    _dump(f"{mail}-put", {"status": st, "json": j, "raw": raw[:400],
                                          "alias": alias})
                    log(f"[recovery] PUT ditolak (st {st} {str(j)[:50]}) alias "
                        f"{alias} -> ganti alias")
                    alias_ditolak = True
                    if kesempatan == 0:
                        continue
                    out["sebab"] = f"PUT settings/email gagal 2 alias (st {st})"
                    out["dt"] = time.time() - t0
                    return out
                log(f"[recovery] alias={alias} (domain {meta.get('domain')}) terpasang")
            else:
                out["alias"] = alias
                meta = {"provider": alias_otp._prov_domain(alias.split("@")[-1])}
                log(f"[recovery] lanjut verifikasi alias pending {alias}")

            # --- verifyRecoveryEmail: minta token HumanVerification
            st, j, raw = _api(s, H3, "POST", "/api/core/v4/verify/email", {})
            vtoken = (((j or {}).get("Details") or {}).get("HumanVerificationToken")) \
                if st in (200, 422) else None
            if not vtoken:
                _dump(f"{mail}-verify-email", {"status": st, "json": j,
                                               "raw": raw[:400]})
                out["sebab"] = f"verify/email tanpa token HV (st {st} {str(j)[:80]})"
                out["dt"] = time.time() - t0
                return out

            # --- kode 6 digit: CEK INBOX DULU [fix 2026-10-15 — akar blokir abuse]
            # Kode sering SUDAH terkirim (permintaan otomatis sebelumnya). Minta
            # kirim HANYA bila inbox kosong; saat 2028 JANGAN minta ulang — itulah
            # pemicu eskalasi "stopped sending new codes" dari Proton. Cukup
            # tunggu kode yang sudah terkirim masuk inbox.
            kode = alias_otp.ambil_kode(alias, provider=meta.get("provider"),
                                        sejak=t0 - 60, budget=2)
            if not kode:
                _api(s, H3, "GET", f"{VERIF}/{vtoken}")
                st2, j2, raw2 = _api(s, H3, "POST", f"{VERIF}/{vtoken}")
                if st2 >= 400 and str((j2 or {}).get("Code")) != "2028":
                    _dump(f"{mail}-kirim", {"status": st2, "json": j2,
                                            "raw": raw2[:400]})
                    out["sebab"] = f"kirim kode gagal (st {st2} {str(j2)[:80]})"
                    out["dt"] = time.time() - t0
                    return out
                if st2 >= 400:
                    log("[recovery] 2028 — kode mungkin sudah terkirim; "
                        "TIDAK minta ulang, tunggu di inbox")
                kode = alias_otp.ambil_kode(alias, provider=meta.get("provider"),
                                            sejak=t0 - 60, budget=budget_kode)
            st3, j3, raw3 = 0, {}, ""
            for _ in range(3):
                if not kode:
                    break
                st3, j3, raw3 = _api(s, H3, "POST", f"{VERIF}/{vtoken}/{kode}")
                if st3 < 400 and not (j3 or {}).get("Error"):
                    break
                log(f"[recovery] submit kode gagal st {st3} {str(j3)[:60]} -> kode baru")
                _api(s, H3, "POST", f"{VERIF}/{vtoken}")
                kode = alias_otp.ambil_kode(alias, provider=meta.get("provider"),
                                            sejak=time.time() - 30,
                                            budget=budget_kode)
            if not kode:
                if kesempatan == 0:
                    log(f"[recovery] kode tak datang ke {alias} -> ganti alias baru")
                    alias = ""        # paksa alias BARU di siklus berikutnya
                    continue
                out["sebab"] = "kode verifikasi tak datang ke alias (2 alias)"
                out["dt"] = time.time() - t0
                return out
            if st3 >= 400 or (j3 or {}).get("Error"):
                _dump(f"{mail}-kode", {"status": st3, "json": j3, "raw": raw3[:400]})
                out["sebab"] = f"submit kode gagal (st {st3} {str(j3)[:80]})"
                out["dt"] = time.time() - t0
                return out

            # --- TUNAS: finalize verify/email dgn header token HV
            hdr = {"x-pm-human-verification-token": vtoken,
                   "x-pm-human-verification-token-type": "ownership-email"}
            st4, j4, raw4 = _api(s, H3, "POST", "/api/core/v4/verify/email", {},
                                 hdr=hdr)
            mail2, stat2, _ = status_recovery(s, H3)
            if st4 >= 400 or stat2 < 1:
                _dump(f"{mail}-tunas", {"st4": st4, "j4": j4, "raw4": raw4[:400],
                                       "email": mail2, "status": stat2})
                out["sebab"] = f"finalisasi tak TUNAS (st {st4}, status {stat2})"
                out["dt"] = time.time() - t0
                return out
            out.update(ok=True, dt=time.time() - t0,
                       sebab=f"TUNAS (recovery {mail2} status={stat2}, "
                             f"flag_awal {out['flag_awal']})")
            return out
    except Exception as e:
        _dump(f"{mail}-eksepsi", {"galat": f"{type(e).__name__}: {e}"})
        out["sebab"] = f"eksepsi {type(e).__name__}: {str(e)[:80]}"
        out["dt"] = time.time() - t0
        return out
    out["dt"] = time.time() - t0
    return out


# ---------------------------------------------------------------------------
# [v2 2026-10-16 — RESEP HAR BROWSER ASLI (01-asep.har), disetujui user] ------
# Alur yang DILAKUKAN manusia: PUT settings/email (PersistPasswordScope=False)
# -> 403 scope WAJAR -> POST auth/info {"Intent":"Proton"} (step-up) -> PUT
# ulang dgn SRP proof -> 200 -> POST verify/send {"Type":"recovery_email"} ->
# email berisi LINK (JWT di fragmen #) masuk ke alias -> KLIK LINK ->
# POST verify/validate {"JWT":...} -> TUNAS. TANPA login-ulang SRP (anti 10003:
# utamakan sesi signup), TANPA kode 6 digit, TANPA minta kode berulang.
# ---------------------------------------------------------------------------

def _cari_jwt(teks):
    m = re.search(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}",
                  teks or "")
    return m.group(0) if m else None


def ambil_jwt(mail, sejak=None, budget=240, provider=None):
    """JWT link verifikasi dari kotak masuk alias (poll sampai `budget` dtk).
    evilmail: inbox -> body pesan; headmail: pesan utuh via /api/v1/messages.
    [fix 2026-11-05 — akar 'link verifikasi tak datang ke alias'] /api/v1/otp
    HANYA balas found/otp/subject/from/date TANPA body pesan (dokumentasi resmi
    /docs headmail) -> JWT di link mustahil terbaca dari sana walau surat SUDAH
    masuk; param 'since' = epoch MS (dulu dikirim detik). Sumber pesan utuh =
    /api/v1/messages (+ /api/v1/aliases/{email} = 50 pesan terakhir) — JWT
    dicari di seluruh JSON (body/html/preview)."""
    import requests
    import urllib.parse
    hk, ek = alias_otp._keys()
    prov = provider or alias_otp._prov_domain(mail.split("@")[-1])
    sejak = sejak or time.time()
    akhir = time.time() + budget
    q = urllib.parse.urlencode({"email": mail})
    while time.time() < akhir:
        try:
            if prov == "headmail":
                H = {"authorization": "Bearer " + hk, "user-agent": alias_otp.UA}
                for path, par in (
                        ("/api/v1/messages",
                         {"email": mail, "limit": 20,
                          "since": int((sejak - 30) * 1000)}),
                        (f"/api/v1/aliases/{urllib.parse.quote(mail)}", {}),
                        ("/api/v1/messages/latest", {"email": mail})):
                    try:
                        d = requests.get(f"{alias_otp.HEADMAIL}{path}",
                                         headers=H, params=par,
                                         timeout=40).json()
                    except Exception:
                        continue
                    jwt = _cari_jwt(json.dumps(d))
                    if jwt:
                        return jwt
                # long-poll lama = PEMICU tunggu server-side (balasan tanpa body)
                qq = urllib.parse.urlencode({"email": mail, "wait": 25,
                                             "since": int((sejak - 30) * 1000),
                                             "any": 1})
                d = requests.get(f"{alias_otp.HEADMAIL}/api/v1/otp?{qq}",
                                 headers=H, timeout=40).json()
                jwt = _cari_jwt(json.dumps(d))
                if jwt:
                    return jwt
            else:
                msgs = (requests.get(
                    f"{alias_otp.EVILMAIL}/api/accounts/inbox?{q}",
                    headers={"x-api-key": ek, "user-agent": alias_otp.UA},
                    timeout=25).json() or {}).get("data") or []
                for m in msgs:
                    uid = m.get("id") or m.get("uid")
                    jwt = _cari_jwt(f"{m.get('subject', '')} "
                                    f"{m.get('preview') or m.get('snippet') or ''}")
                    if not jwt and uid is not None:
                        try:
                            dd = (requests.get(
                                f"{alias_otp.EVILMAIL}/api/accounts/message/{uid}?{q}",
                                headers={"x-api-key": ek, "user-agent": alias_otp.UA},
                                timeout=25).json() or {}).get("data") or {}
                            jwt = _cari_jwt(f"{dd.get('text', '')} {dd.get('html', '')}")
                        except Exception:
                            pass
                    if jwt:
                        return jwt
        except Exception:
            pass
        time.sleep(8)
    return None


def pasang_v2(mail, pw, proxy=None, log=lambda *a, **k: None, budget_kode=25,
              kumpul_alias="semua", proxy_lain=None):
    """Recovery mail versi 2 — replika persis alur browser asli (lihat blok
    komentar v2 di atas). Kontrak keluaran sama dgn pasang(): dict
    {ok, sebab, flag_awal, alias, dt}."""
    t0 = time.time()
    out = {"ok": False, "sebab": "", "flag_awal": "?", "alias": "", "dt": 0.0}
    try:
        # --- SESI: utamakan SESI SIGNUP (tanpa login SRP — anti 10003)
        s, H3 = proton_api._sesi_dari_refresh(mail, proxy=proxy)
        for _ in range(2 if (s is None and proxy_lain) else 0):
            proxy = proxy_lain()
            if not proxy:
                break
            s, H3 = proton_api._sesi_dari_refresh(mail, proxy=proxy)
        if s is None:
            s, H3 = proton_api.login(mail, pw, proxy=proxy)   # fallback
        if s is None:
            out["sebab"] = f"sesi tak terbentuk ({getattr(proton_api.login, 'alasan', '')})"
            out["dt"] = time.time() - t0
            return out

        out["flag_awal"] = cek_flag(s, H3)
        alias_lama, stat, _us = status_recovery(s, H3)
        if alias_lama and stat >= 1:
            out.update(ok=True, alias=alias_lama, dt=time.time() - t0,
                       sebab=f"TUNAS (sudah terverifikasi: {alias_lama})")
            return out
        if alias_lama:
            alias = alias_lama
            meta = {"provider": alias_otp._prov_domain(alias.split("@")[-1])}
        else:
            alias, meta = alias_otp.buat_alias(kumpul=kumpul_alias)
        out["alias"] = alias

        # --- PUT #1 (tanpa proof): 403 scope = WAJAR, seperti app asli
        body0 = {"Email": alias, "PersistPasswordScope": False}
        st, j, raw = _api(s, H3, "PUT", "/api/core/v4/settings/email", body0)
        if st >= 400 and "9102" not in str((j or {}).get("Code")) \
                and "9106" not in str((j or {}).get("Code")):
            # alamat ditolak validator -> ganti alias SEKALI (bukan karena retry)
            alias, meta = alias_otp.buat_alias(kumpul=kumpul_alias)
            out["alias"] = alias
            body0 = {"Email": alias, "PersistPasswordScope": False}
            st, j, raw = _api(s, H3, "PUT", "/api/core/v4/settings/email", body0)

        # --- step-up SRP persis HAR: auth/info {"Intent":"Proton"} -> PUT+proof
        if st >= 400:
            st2, j2, raw2 = _api(s, H3, "POST", "/api/core/v4/auth/info",
                                 {"Intent": "Proton"})
            if st2 != 200 or not (j2 or {}).get("Salt"):
                # [fix 2026-10-21] auth/info {"Intent":"Proton"} MENOLAK sesi
                # refresh (Code 2001 'Invalid input' — bukti A/B 2026-10-20:
                # sesi refresh -> 400, sesi SRP login -> 200). Fallback: login
                # SRP segar lalu ulang step-up SEKALI; baru menyerah bila tetap
                # gagal. (korban: octaia4450, tarellaon5092 — akun lahir mati
                # di preflight padahal kredensial sehat.)
                log(f"[recovery-v2] step-up st {st2} di sesi awal -> fallback login SRP")
                s2, H3b = proton_api.login(mail, pw, proxy=proxy)
                if s2 is not None:
                    s, H3 = s2, H3b
                    st2, j2, raw2 = _api(s, H3, "POST", "/api/core/v4/auth/info",
                                         {"Intent": "Proton"})
            if st2 != 200 or not (j2 or {}).get("Salt"):
                _dump(f"{mail}-stepup", {"status": st2, "json": j2,
                                         "raw": raw2[:300]})
                out["sebab"] = f"step-up auth/info gagal (st {st2})"
                out["dt"] = time.time() - t0
                return out
            import base64
            sys.path.insert(0, os.path.join(HERE, "vendor"))
            import srp
            mod = proton_api._buka_modulus(j2["Modulus"])
            b = srp.bukti_login(mail, pw, mod, j2["Salt"],
                                base64.b64decode(j2["ServerEphemeral"]))
            body = {"ClientProof": b["clientProof"],
                    "ClientEphemeral": b["clientEphemeral"],
                    "SRPSession": j2["SRPSession"],
                    "Email": alias, "PersistPasswordScope": False}
            st, j, raw = _api(s, H3, "PUT", "/api/core/v4/settings/email", body)
        if st >= 400:
            _dump(f"{mail}-put-v2", {"status": st, "json": j, "raw": raw[:400],
                                     "alias": alias})
            out["sebab"] = f"PUT settings/email gagal (st {st} {str(j)[:60]})"
            out["dt"] = time.time() - t0
            return out
        log(f"[recovery-v2] alias={alias} terpasang")

        # --- kirim email verifikasi (isi LINK)
        st, j, raw = _api(s, H3, "POST", "/api/core/v4/verify/send",
                          {"Type": "recovery_email"})
        if st >= 400:
            out["sebab"] = f"verify/send gagal (st {st} {str(j)[:60]})"
            out["dt"] = time.time() - t0
            return out
        log("[recovery-v2] verify/send OK — tunggu link di inbox alias")

        # --- ambil JWT dari link (fragmen #) di inbox, lalu validate
        jwt = ambil_jwt(alias, sejak=t0 - 60, budget=budget_kode,
                        provider=meta.get("provider"))
        if not jwt:
            out["sebab"] = "link verifikasi tak datang ke alias"
            out["dt"] = time.time() - t0
            return out
        st, j, raw = _api(s, H3, "POST", "/api/core/v4/verify/validate",
                          {"JWT": jwt})
        if st < 400 and (j or {}).get("Code") == 1000:
            out.update(ok=True, dt=time.time() - t0,
                       sebab=f"TUNAS (link verifikasi, flag_awal {out['flag_awal']})")
            return out
        _dump(f"{mail}-validate", {"status": st, "json": j, "raw": raw[:300]})
        out["sebab"] = f"verify/validate gagal (st {st} {str(j)[:60]})"
        out["dt"] = time.time() - t0
        return out
    except Exception as e:
        _dump(f"{mail}-eksepsi-v2", {"galat": f"{type(e).__name__}: {e}"})
        out["sebab"] = f"eksepsi {type(e).__name__}: {str(e)[:80]}"
        out["dt"] = time.time() - t0
        return out


if __name__ == "__main__":
    # [fix 2026-10-21 bocor jaringan Proton] proses terpisah -> pasang penjaga
    # paksa-proxy di sini (kawal_proton_api): jangan ada egress direct ke
    # account.proton.me dari IP VPS walau login proxy gagal.
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))), "inti"))
        import run as _runmod
        _runmod.pasang_patch()
    except Exception:
        pass
    if len(sys.argv) < 3:
        print("pakai: python3 recovery_email.py <mail> <pass> [--proxy h:p:u:pw]")
        sys.exit(2)
    px = None
    if "--proxy" in sys.argv:
        px = sys.argv[sys.argv.index("--proxy") + 1]
    # [fix 2026-10-22 — vonis rekaman emas user] sesi manual TIDAK PERNAH login
    # ulang: semua langkah recovery jalan di SESI SIGNUP (cookie auth). Login SRP
    # ulang utk akun baru = magnet 10003 (bukti: lucumaug196 lahir SEHAT, sampai
    # fork mail-app, tetap 10003 saat pasang() login ulang). Maka: utamakan
    # pasang_v2 (sesi signup, bentuk body persis HAR); bila gagal -> fallback
    # pasang() v1 (login SRP + kode 6-digit, terbukti utk akun tak bermasalah).
    r = pasang_v2(sys.argv[1], sys.argv[2], proxy=px, log=print)
    if not r.get("ok"):
        log2 = print
        log2(f"[recovery] jalur sesi signup gagal ({r.get('sebab')}) -> fallback alur v1")
        r = pasang(sys.argv[1], sys.argv[2], proxy=px, log=print)
    print(json.dumps(r, ensure_ascii=False))
