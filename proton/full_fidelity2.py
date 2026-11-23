#!/usr/bin/env python3
"""Runner full-fidelity v2: ketik DI DALAM iframe challenge (form asli), client asli submit.
Perbaikan v2: gate "challenge siap", ketik lewat frame Name=email, logging jaringan,
dump DOM modal verifikasi, deteksi POST /users dari sisi jaringan.
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
import ipaddress
import hashlib
import json
import os
import re
import secrets
import random
import socket
import string
import time
import urllib.parse
from pathlib import Path

import requests
from fp_profile import sync_playwright  # patchright bila ada (stealth)

import fp_profile
import captcha_slider
import smsbower

# override konfigurasi dari batch-env.json (ditulis run_batch.py) bila ada
_benv = Path(__file__).resolve().parent / "batch-env.json"
if _benv.exists():
    for _k, _v in json.loads(_benv.read_text()).items():
        os.environ.setdefault(_k, str(_v))
# brutalism: kunci alias dari data/settings.json (dulu diwariskan shell/orkestrator
# — banking t1 tidak mengisinya sehingga assert di bawah membunuh SEMUA slot, 0 byte)
_setf = Path(__file__).resolve().parent / "data" / "settings.json"
if _setf.exists():
    try:
        _s = json.loads(_setf.read_text())
        for _k in ("HEADMAIL_KEY", "EVILMAIL_KEY", "HM_DOMAIN", "EVIL_DOMAIN", "MAIL_PROVIDER"):
            if _s.get(_k):
                os.environ.setdefault(_k, str(_s[_k]))
    except Exception:
        pass

HOST = "account.proton.me"
# [domain 2026-10-20 — arahan user, bukti HAR idx 84/122/148] akun dibuat di
# protonmail.com (proton.me sudah terlalu sering dipakai -> kena restricted).
# SWITCH: env PROTON_MAIL_DOMAIN=proton.me utk kembali, tanpa edit kode.
# [2026-10-26 — arahan user] KEMBALI ke proton.me (default); env masih bisa
# override ke protonmail.com bila perlu.
# [RE-PROTON-2028 2026-11-08] default protonmail.com — blob emas 20-Okt
# (body Domain + interaksi #select-domain) memakai protonmail.com, bukan proton.me.
MAIL_DOMAIN = os.environ.get("PROTON_MAIL_DOMAIN", "protonmail.com")
TARGET = f"https://{HOST}/mail/signup?plan=free&noPromo=1&ref=mail_plus_intro-2"
HEADMAIL = "https://headmail.flowjob-lab.workers.dev"
HEADMAIL_KEY = os.environ.get("HEADMAIL_KEY", "")
HM_DOMAIN = os.environ.get("HM_DOMAIN", "hellowntas.fun")
EVILMAIL = "https://evilmail.pro"
EVILMAIL_KEY = os.environ.get("EVILMAIL_KEY", "")
EVIL_DOMAIN = os.environ.get("EVIL_DOMAIN", "evilmail.pro")
MAIL_PROVIDER = os.environ.get("MAIL_PROVIDER", "headmail")  # headmail | evilmail
if MAIL_PROVIDER == "evilmail":
    assert EVILMAIL_KEY, "setel env EVILMAIL_KEY untuk provider evilmail"
else:
    assert HEADMAIL_KEY, "setel env HEADMAIL_KEY untuk provider headmail"
HERE = Path(__file__).resolve().parent


def _safe_path(nilai, default):
    """Path dari env: dinormalkan, tolak '..'/'~', wajib di dalam folder tool/results."""
    root = HERE.resolve()
    root_hsl = (HERE / "results").resolve()   # brutalism: results = symlink ke luar tree
    mentah = str(nilai or default)
    assert "~" not in mentah and ".." not in Path(mentah).parts, f"path tidak valid (traversal): {mentah}"
    fp = Path(os.path.normpath(os.path.expanduser(mentah))).resolve()
    oke = fp in (root, root_hsl) or root in fp.parents or root_hsl in fp.parents
    assert oke, f"path di luar folder tool ditolak: {fp}"
    return fp


SHOTS = _safe_path(os.environ.get("SHOTS_DIR"), HERE / "results" / "shots")
RESULT_F = _safe_path(os.environ.get("RUN_RESULT_FILE"), HERE / "results" / "run-last.json")
REGISTERED_F = _safe_path(os.environ.get("REGISTERED_FILE"), HERE / "results" / "registered.txt")
for _d in (SHOTS, RESULT_F.parent, REGISTERED_F.parent):
    _d.mkdir(parents=True, exist_ok=True)

assert HOST.endswith("proton.me")
for _f, _t, _p, _c, sa in socket.getaddrinfo(HOST, 443):
    assert ipaddress.ip_address(sa[0]).is_global

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
log = {"steps": [], "net": []}

# [debug-hv 2026-10-16] saklar env DEBUG_HV=1: tangkap RESPONS JARINGAN UTUH
# (status+headers+body) + DOM halaman tepat setelah submit — bukti mentah kasus
# hv=None (menu verifikasi hilang). Tanpa flag: perilaku & byte TIDAK berubah.
DEBUG_HV = os.environ.get("DEBUG_HV") == "1"
DUMP_HV = _safe_path(os.environ.get("RUN_RESULT_FILE"),
                     HERE / "results" / "run-last.json").parent / "hv-dump"
if DEBUG_HV:
    DUMP_HV.mkdir(parents=True, exist_ok=True)
_ID_HV = f"{int(time.time()) % 1000000}"


def note(msg):
    if msg.startswith("NET ") and os.environ.get("VERBOSE") != "1":
        log["steps"].append({"t": int(time.time()), "m": msg})  # derau: simpan saja
        return
    print(">>", msg, flush=True)   # [edit brutalism] flush (bug #4: redirect = buffer)
    log["steps"].append({"t": int(time.time()), "m": msg})


def shot(page, label):
    path = SHOTS / f"v2-{int(time.time()) % 100000}-{label}.png"
    try:
        page.screenshot(path=str(path))
    except Exception:
        pass
    note(f"shot: {path.name}")


def dump_hv(page, label):
    """[debug-hv] simpan DOM utuh + URL saat momen kunci (hanya DEBUG_HV=1)."""
    if not DEBUG_HV:
        return
    try:
        html = page.content()
        st = page.evaluate("() => location.href")
        p = DUMP_HV / f"{_ID_HV}-{label}.html"
        p.write_text(f"<!-- url: {st} -->\n" + html)
        note(f"dump-hv: {p.name} ({len(html)} byte)")
    except Exception as e:
        note(f"dump-hv gagal: {e}")


def _snap_cookie(page, label):
    """[diag sesi-mati 2026-11-05 — hipotesis user] snapshot cookie jar ringkas
    (nama AUTH-/REFRESH- + uid + 12 char awal nilai token) di momen kunci:
    setelah POST /users 200, setelah /auth 200, 401/403 pertama, wizard-reset,
    akhir run. Pembeda 'token user' vs 'token anonim pra-login': nilai mulai
    'eyJpdi' = blob ter-enkripsi sesi anonim; opaque = AccessToken user.
    Aman dipanggil dari handler (read-only CDP)."""
    try:
        rows = []
        ctx = page.context   # [fix] patchright: context PROPERTY (bukan method)
        for c in (ctx() if callable(ctx) else ctx).cookies():
            nm = str(c.get("name", ""))
            if not nm.startswith(("AUTH-", "REFRESH-", "AccessToken-")):
                continue
            val = urllib.parse.unquote(str(c.get("value", "")))
            tok = val
            try:  # cookie json (AccessToken-/REFRESH-) -> ambil tokennya
                j = json.loads(val)
                tok = str(j.get("AccessToken") or j.get("RefreshToken") or val)
            except Exception:
                pass
            rows.append({"n": nm, "v12": tok[:12], "anon": tok.startswith("eyJpdi"),
                         "len": len(tok)})
        log.setdefault("cookie_snap", []).append(
            {"t": int(time.time()), "label": label,
             "n_auth": sum(1 for r in rows if r["n"].startswith("AUTH-")),
             "rows": rows})
        note(f"COOKIE-SNAP {label} (n_auth={sum(1 for r in rows if r['n'].startswith('AUTH-'))}): "
             + "; ".join(f"{r['n']}={'ANON-ey' if r['anon'] else r['v12']}" for r in rows))
    except Exception as e:
        note(f"COOKIE-SNAP {label} gagal: {type(e).__name__}: {str(e)[:60]}")


def _probe_token_mati(page, log, note):
    """[diag sesi-mati 2026-11-05] ORAKEL 'token dicabut server' vs 'request
    korup di wire': replay GET /users dgn Bearer AccessToken user DARI COOKIE
    JAR, via halaman (exit proxy sticky YANG SAMA — anti 10003 pindah-IP).
    200 = token user MASIH valid -> 401 tadi = masalah request/klien;
    401/403 = server benar2 menolak token. Read-only, TANPA login ulang."""
    try:
        toks = {}
        ctx = page.context   # [fix] patchright: context PROPERTY (bukan method)
        for c in (ctx() if callable(ctx) else ctx).cookies():
            nm = str(c.get("name", ""))
            if nm.startswith("AccessToken-"):
                try:
                    v = json.loads(urllib.parse.unquote(str(c.get("value", ""))))
                    toks[nm.split("-", 1)[1]] = str(v.get("AccessToken") or "")
                except Exception:
                    pass
            elif nm.startswith("AUTH-"):
                uid = nm.split("-", 1)[1]
                val = str(c.get("value", ""))
                if not val.startswith("eyJpdi") and len(val) > 20:
                    toks.setdefault(uid, val)
        hasil = []
        for uid, tok in toks.items():
            if not tok:
                continue
            r = page.evaluate("""async ([uid, tok]) => {
                const r = await fetch('/api/core/v4/users', {credentials: 'include',
                    headers: {'authorization': 'Bearer ' + tok, 'x-pm-uid': uid,
                              'accept': 'application/vnd.protonmail.v1+json'}});
                return {s: r.status, b: (await r.text()).slice(0, 140)};
            }""", [uid, tok])
            hasil.append({"uid12": uid[:12], "tok12": tok[:12], "s": r.get("s"),
                          "b": str(r.get("b", ""))[:140]})
            note(f"PROBE-TOKEN {uid[:12]} tok={tok[:12]}… -> {r.get('s')} {str(r.get('b',''))[:80]}")
        log["probe_token_mati"] = hasil or "tak ada token user di jar"
    except Exception as e:
        log["probe_token_mati"] = f"gagal: {type(e).__name__}: {str(e)[:80]}"
        note(f"PROBE-TOKEN gagal: {log['probe_token_mati']}")


def _status_captcha_respons(log):
    """[keputusan RESPONS 2026-10-16, arahan user] vonis puzzle DARI respons
    server — bukan dari tebakan solver, bukan nunggu buta.
    Tanda SUKSES (HAR asli 01-nihliat): validate & finalize balas body "OK".
    Tanda GAGAL nyata: body terbaca tapi bukan "OK" (mis. kosong/error).
    'tak-terbaca': body tak terbaca -> keputusan jatuh ke bukti resubmit /users."""
    terbaca = []
    for n in log.get("net", []):
        u = str(n.get("u", ""))
        if "captcha/v1/api/validate" in u or "captcha/v1/api/finalize" in u:
            if n.get("s") is not None and "body" in n:
                terbaca.append((n.get("s"), (n.get("body") or "").strip()))
    if not terbaca:
        return "tak-terbaca"
    ada_ok = False
    for _s, b in terbaca:
        if b == "OK":
            ada_ok = True
        elif "did not pass" in b or '"status":"failed"' in b or '"status":"error"' in b:
            return "gagal"
        elif b.startswith("{") and ("powPhase" in b or '"status":"check"' in b):
            # [fix 2026-10-23 vinonia71788] 202 {"status":"check","powPhase":
            # "secondary",...} = fase PoW LANJUTAN yang dikerjakan widget
            # sendiri — BUKAN penolakan. Dulu 'bukan OK = gagal' memutus detik
            # itu juga, padahal widget akan menyelesaikan PoW lanjut lalu
            # finalize 'OK' beberapa detik kemudian.
            pass
    return "OK" if ada_ok else "tak-terbaca"


def human_type(page, locator_or_handle, text):
    """[humanis 2026-10-12] ketik MANUSIA: mouse digerakkan ke kolom dulu,
    ritme log-normal (banyak cepat, sesekali lambat), melambat di batas kata,
    dan 6% salah-ketik lalu Backspace (jejak paling khas manusia)."""
    try:
        box = locator_or_handle.bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] * random.uniform(0.2, 0.8),
                            box["y"] + box["height"] * random.uniform(0.3, 0.7),
                            steps=random.randint(8, 22))
            time.sleep(random.uniform(0.05, 0.25))
    except Exception:
        pass
    locator_or_handle.click()
    time.sleep(random.uniform(0.15, 0.7))
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isalnum() and random.random() < 0.06 and i > 0:
            page.keyboard.type(random.choice("abcdefghijklmnopqrstuvwxyz0123456789"))
            time.sleep(random.uniform(0.12, 0.5))
            page.keyboard.press("Backspace")
            time.sleep(random.uniform(0.08, 0.35))
        page.keyboard.type(ch)
        i += 1
        t = random.lognormvariate(-2.5, 0.55)   # median ~80ms, ekor panjang
        if ch in " @._-":
            t += random.uniform(0.08, 0.45)     # manusia melambat di batas kata
        time.sleep(min(t, 1.8))


def jeda_baca(page, berat=False):
    """[humanis 2026-10-12] jeda 'membaca' + scroll sesekali sebelum bertindak."""
    if random.random() < 0.55:
        try:
            page.mouse.wheel(0, random.uniform(80, 420))
            time.sleep(random.uniform(0.3, 1.1))
            if random.random() < 0.4:
                page.mouse.wheel(0, -random.uniform(40, 220))
        except Exception:
            pass
    time.sleep(random.uniform(1.2, 4.0) if berat else random.uniform(0.4, 1.5))  # [speed 2026-10-14] dipadatkan (riset F)


def gerak_mouse_manusia(page, fokus, putar=None):
    """[fix 2026-10-23 bunuh-diri-sesi] gerakkan mouse SUNGGUHAN di sekitar
    elemen form sebelum interaksi. Bukti blob (log.blob_dump) vs rekaman emas:
    kita mousemove: 0 (tak pernah gerak = pola bot), user asli mousemove: 128.
    Kit menghitung event mousemove jendela challenge; gerak di area form =
    event masuk ke hitungan. putar = jumlah lingkaran (default 2-4)."""
    try:
        box = fokus.bounding_box()
    except Exception:
        box = None
    if not box:
        box = {"x": 400, "y": 400, "width": 300, "height": 40}
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    try:
        page.mouse.move(cx + random.uniform(-260, 260), cy + random.uniform(-160, 160),
                        steps=random.randint(6, 14))
    except Exception:
        return
    for _ in range(putar if putar is not None else random.randint(2, 4)):
        try:
            tx = cx + random.uniform(-180, 180)
            ty = cy + random.uniform(-90, 90)
            page.mouse.move(tx, ty, steps=random.randint(4, 12))
            time.sleep(random.uniform(0.05, 0.22))
        except Exception:
            break
    try:   # akhiri tepat di atas elemen (spt kursor manusia yang menuju target)
        page.mouse.move(cx + random.uniform(-6, 6), cy + random.uniform(-4, 4),
                        steps=random.randint(3, 8))
    except Exception:
        pass
    time.sleep(random.uniform(0.05, 0.2))


def ketik_pacing(page, text, lo, hi):
    """[humanisasi 2026-10-16, arahan user] ketik per-karakter dgn TOTAL durasi
    target uniform(lo,hi) dtk. Dulu: 15 char ~0,5 dtk (isi instan/robotik);
    kini pola ngetik manusiawi — jeda tak seragam, melambat di batas kata.
    Target user: username 3-5 dtk, isi password (2 kotak) total 3-6 dtk."""
    total = random.uniform(lo, hi)
    bobot = [random.lognormvariate(0, 0.5) + (0.5 if ch in " @._-" else 0.0)
             for ch in text] or [1.0]
    s = sum(bobot)
    for ch, b in zip(text, bobot):
        page.keyboard.type(ch)
        time.sleep(max(0.02, total * b / s * random.uniform(0.7, 1.3)))


def ketik_manusia(page, text):
    """[humanisasi 2026-10-16 — pola ASLI user dari blob ter-dekripsi] ketik
    username: BURST 4-6 char + jeda 0,35-1,75 dtk + SATU kali salah-ketik
    (1 char nyasar -> Backspace). Bukti blob user: keydown ['g','u','r','a',
    'n','a','Backspace',...] + ritme users/available jeda 0,35-1,75 dtk.
    Kita dulu: metronom per karakter tanpa pernah salah = jejak robot."""
    i = 0
    salah = False
    while i < len(text):
        n = min(len(text) - i, random.randint(4, 6))
        for ch in text[i:i + n]:
            page.keyboard.type(ch)
            time.sleep(random.uniform(0.04, 0.12))
        i += n
        if (not salah and i < len(text) - 2 and random.random() < 0.85):
            page.keyboard.type(random.choice("abcdefghijklmnopqrstuvwxyz"))
            time.sleep(random.uniform(0.15, 0.4))
            page.keyboard.press("Backspace")
            salah = True
            time.sleep(random.uniform(0.2, 0.5))
        if i < len(text):
            time.sleep(random.uniform(0.35, 1.75))


# ---------------------------------------------------------------------------
# [state-machine 2026-11-05 — arahan user + koordinator, bukti screenshot]
# Layar pasca-signup BERSIFAT KONDISIONAL (display name kadang ada kadang
# tidak). Deteksi 'layar display name masih' dari teks body = FALSE POSITIF
# (teks "Preparing your recovery phrase" di kartu Thank-you pernah menyesatkan;
# 6x klik buta menembak tombol yang tak lagi relevan = pola robot + percuma
# saat sesi sudah anon). Kini: klasifikasi layar AKTUAL dari DOM+URL+bukti
# jaringan, SATU aksi per layar, klik HANYA bila tombolnya benar-benar ada,
# verifikasi screenshot. Target = MASUK MAIL (bukti sessions/forks 200 /
# mail.proton.me / URL /u/1/) — BARU pasang recovery (alur HAR user 5 Nov).
# ---------------------------------------------------------------------------

def _login_ulang_browser(page, mail_penuh, pw, note):
    """[arahan user 2026-11-05 malam] wizard-reset/sesi-mati/signin SETELAH akun
    lahir = gangguan infra (dugaan user: warmup browser) — pemulihan yang BENAR
    (kata user: 'langsung aja ke halaman login dan masuk lagi dan ke halaman
    setting'): login ulang VIA BROWSER YANG SAMA, lalu lanjut alur.
    Prinsip tunggu BERBASIS KONDISI (pagar maksimal = rem, bukan tidur buta) —
    page.wait_for_timeout memompa dispatcher Playwright (time.sleep membekukan
    log net — lihat AGENTS.md pelajaran 2026-11-05). Return True bila masuk."""
    try:
        note("[login-ulang] pulihkan sesi via halaman login (browser yang sama)")
        try:
            page.goto("https://account.proton.me/login",
                      wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass

        def _cari(sel):
            try:
                l = page.locator(sel).first
                return l if (l.count() and l.is_visible()) else None
            except Exception:
                return None

        # kolom username (bentuk UI 5.0.433.0: 'Email or username' + Continue)
        kol = None
        akhir = time.time() + 30
        while time.time() < akhir:
            for sel in ('input[autocomplete="username"]', 'input[name="username"]',
                        'input[placeholder*="Email or username"]',
                        '[data-testid="input-input-element"]:not([type="password"])'):
                kol = _cari(sel)
                if kol is not None:
                    break
            if kol is not None:
                break
            page.wait_for_timeout(500)
        if kol is None:
            note("[login-ulang] kolom username tak ditemukan")
            return False
        kol.click()
        kol.fill(mail_penuh)
        page.wait_for_timeout(500)
        try:
            b = page.locator('button:has-text("Continue")').first
            if b.count() and b.is_visible():
                b.click(timeout=5000)
            else:
                kol.press("Enter")
        except Exception:
            pass
        # kolom password (layar ke-2 login)
        pwf = None
        akhir = time.time() + 30
        while time.time() < akhir:
            pwf = _cari('input[type="password"]')
            if pwf is not None:
                break
            page.wait_for_timeout(500)
        if pwf is None:
            note("[login-ulang] kolom password tak muncul")
            return False
        pwf.click()
        pwf.fill(pw)
        page.wait_for_timeout(500)
        try:
            b = page.locator('button:has-text("Sign in"), button:has-text("Continue")').first
            if b.count() and b.is_visible():
                b.click(timeout=5000)
            else:
                pwf.press("Enter")
        except Exception:
            pass
        # BERHASIL = masuk aplikasi (mail/inbox) — poll berbasis kondisi

        def _masuk():
            try:
                u = page.url or ""
                if ("inbox" in u or "/mail" in u or "/u/1/" in u) and "login" not in u:
                    return True
                t = (page.evaluate("() => (document.body.innerText||'').slice(0,3000)") or "")
                return ("Inbox" in t) or ("Get started" in t) or ("Secure your account" in t)
            except Exception:
                return False

        akhir = time.time() + 90
        while time.time() < akhir:
            if _masuk():
                shot(page, "login-ulang-berhasil")
                note("[login-ulang] BERHASIL masuk lagi — lanjut alur (mail -> settings/recovery)")
                return True
            page.wait_for_timeout(1000)
        shot(page, "login-ulang-gagal")
        note("[login-ulang] gagal masuk dalam pagar 90 dtk")
        return False
    except Exception as e:
        note(f"[login-ulang] galat: {type(e).__name__}: {str(e)[:60]}")
        return False


def _deteksi_layar(page, log):
    """Klasifikasi layar AKTUAL. Return: 'mail' | 'signin' | 'display_name' |
    'passphrase' | 'tour' | 'sesi_mati' | 'signup_ulang' | 'lain'. Bukti
    JARINGAN (forks/labels) mengalahkan tampilan UI — layar bisa telat render
    tapi sesi mail sudah lahir."""
    for n in log.get("net", []):
        u = str(n.get("u", ""))
        if n.get("s") == 200 and ("/sessions/forks" in u or "mail.proton.me/" in u):
            return "mail"
    try:
        st = page.evaluate("() => ({url: location.href, "
                           "text: (document.body.innerText||'').slice(0,2500)})")
    except Exception:
        return "lain"
    url = (st.get("url") or "")
    low = (st.get("text") or "").lower()
    if "/u/1/" in url or "inbox" in url:
        return "mail"
    # Sign in = alur SALAH urutan (sesi mail belum dibuat). Kenali dari FORM
    # login asli — teks 'Sign in' juga ada sebagai link di halaman signup.
    try:
        lf = page.locator('[data-testid="login-form"]')
        if lf.count() and lf.first.is_visible():
            return "signin"
    except Exception:
        pass
    if "email or username" in low and "create your proton account" not in low:
        return "signin"
    # [bukti run-1793873433 galorgai380, v2-73658-final.png] token dicabut ->
    # SPA MEMBUANG sesinya sendiri -> wizard RESET ke form signup KOSONG
    # padahal akun SUDAH lahir. UI kembali ke nol = onboarding MATI; jangan
    # tunggu 3 menit percuma (dulu 55x 'menunggu').
    if "create your proton account" in low and (log.get("users_ok")
                                               or log.get("terbukti_hidup")):
        return "signup_ulang"
    # display name: heading/kolom + kolom input + tombol Continue TERLIHAT
    if ("display name" in low or "anzeigenamen" in low or "nama tampilan" in low
            or "nom d'affichage" in low or "nome visualizzato" in low):
        try:
            inp = page.locator('input[type="text"], input:not([type])')
            btn = page.locator("button:has-text('Continue'), button:has-text('Weiter'),"
                               " button:has-text('Next'), button[type=submit]")
            if inp.count() and btn.count() and btn.first.is_visible():
                tolak = [n for n in log.get("net", [])
                         if "/addresses/" in str(n.get("u", "")) and n.get("m") == "PUT"
                         and n.get("s") in (401, 403, 502)]
                if len(tolak) >= 3:
                    return "sesi_mati"   # token dicabut server (bukti 5 Nov: 403 x6)
                return "display_name"
        except Exception:
            pass
    # passphrase/recovery phrase ASLI punya checkbox persetujuan — kartu
    # Thank-you ("Preparing your recovery phrase") TIDAK punya = transisi, tunggu.
    if ("recovery phrase" in low or "wiederherstellungsphrase" in low
            or "phrase de récupération" in low or "frase di recupero" in low
            or "frase de recuperación" in low):
        try:
            if page.locator('input[type="checkbox"]').count():
                return "passphrase"
        except Exception:
            pass
    for sel in ('button:has-text("Skip")', 'button:has-text("Later")',
                'button:has-text("Überspringen")', 'button:has-text("Später")'):
        try:
            b = page.locator(sel).first
            if b.count() and b.is_visible():
                return "tour"
        except Exception:
            continue
    return "lain"


def _aksi_onboarding(page, layar, note, username, log):
    """SATU aksi utk layar yang TERBUKTI tampil (screenshot sebelum & sesudah).
    Return 'mati' bila sesi terbukti dicabut server — pemanggil wajib STOP
    (jangan klik buta; klik berulang ke sesi anon = pola robot, tak ada gunanya)."""
    if layar == "display_name":
        if int(log.get("display_name_klik", 0)) >= 3:
            # [user 2026-11-05] jangan dipaksa berulang — 3 klik tak maju =
            # bukan masalah klik; stop jujur, jangan 6x buta.
            return "habis"
        try:
            inp = page.locator('input[type="text"], input:not([type])').first
            btn = page.locator("button:has-text('Continue'), button:has-text('Weiter'),"
                               " button:has-text('Next'), button[type=submit]").first
            if not (btn.count() and btn.is_visible()):
                note("display name: tombol tak terlihat — layar sudah pindah/belum siap, lewati")
                return None
            # [bukti screenshot v2-3568/34044/34079 + HAR user idx 28] kolom
            # SUDAH terisi nama akun UTUH oleh app (DisplayName=user asli) —
            # JANGAN dikosongkan/diisi potongan acak (dulu username[:3..8] +
            # fill("") merusak isi awal = beda dari perilaku manusia).
            # [anti-suspend 2026-11-23] isi nama manusia realistis (depan+belakang)
            # bukan username 'flav5942ianus' yang terdeteksi pola bot.
            isi = ""
            if inp.count() and inp.is_visible():
                try:
                    isi = (inp.input_value(timeout=1500) or "").strip()
                except Exception:
                    isi = ""
            if not isi:
                nama_tampil = _nama_tampilkan()
                log["display_name"] = nama_tampil
                human_type(page, inp, nama_tampil)
                time.sleep(random.uniform(0.5, 1.5))
            else:
                log["display_name"] = isi
            n_klik = int(log.get("display_name_klik", 0)) + 1
            log["display_name_klik"] = n_klik
            shot(page, f"display-name-klik-{n_klik}")
            btn.click(timeout=4000)
            note(f"display name: Continue diklik ({n_klik}/3) isi='{isi or log.get('display_name','')}'")
            time.sleep(random.uniform(3.0, 5.0))
        except Exception as e:
            note(f"display name aksi gagal: {type(e).__name__}: {str(e)[:60]}")
            return None
        tolak = [n for n in log.get("net", [])
                 if "/addresses/" in str(n.get("u", "")) and n.get("m") == "PUT"
                 and n.get("s") in (401, 403, 502)]
        if len(tolak) >= 3:
            return "mati"
        return None
    if layar == "passphrase":
        try:
            cb = page.locator('input[type="checkbox"]').first
            if cb.count() and cb.is_visible():
                try:
                    if not cb.is_checked():
                        cb.check(timeout=3000)
                        note("passphrase: ceklis persetujuan")
                except Exception:
                    pass
                time.sleep(random.uniform(1.0, 2.5))
            btn = page.locator("button:has-text('Continue'), button:has-text('Next'),"
                               " button:has-text('Weiter'), button[type=submit]").first
            if btn.count() and btn.is_visible():
                shot(page, "passphrase-sebelum-klik")
                btn.click(timeout=4000)
                note("passphrase: Next/Continue diklik")
                time.sleep(random.uniform(2.5, 4.5))
            else:
                note("passphrase: tombol Next/Continue tak terlihat — tunggu render")
        except Exception as e:
            note(f"passphrase aksi gagal: {type(e).__name__}: {str(e)[:60]}")
        return None
    if layar == "tour":
        for sel in ('button:has-text("Skip")', 'button:has-text("Later")',
                    'button:has-text("Überspringen")', 'button:has-text("Später")',
                    'button:has-text("Next")'):
            try:
                b = page.locator(sel).first
                if b.count() and b.is_visible():
                    shot(page, "tour-sebelum-klik")
                    b.click(timeout=3000)
                    note(f"tour: tombol diklik ({sel})")
                    time.sleep(random.uniform(1.5, 3.0))
                    break
            except Exception:
                continue
        return None
    return None


def _guard_public(host):
    for _f, _t, _p, _c, sa in socket.getaddrinfo(host, 443):
        assert ipaddress.ip_address(sa[0]).is_global, f"IP tidak global: {sa[0]}"


def naikkan_sesi(page, mail, pw):
    """[bedah HAR 01-asep 2026-10-16 — inti masalah 'kurang scope'] SETELAH
    /users 200, browser asli melakukan POST auth/info + POST /auth (SRP dgn
    password) -> balasan Scopes:['full',...] = SESI LENGKAP ("kartu karyawan").
    Tanpa langkah ini sesi tetap izin rendah -> 401/403 'insufficient scope' di
    mana-mana (display name, recovery, /users) -> akun setengah-onboarding.
    Dijalankan via fetch DI HALAMAN (origin+cookie = persis app asli)."""
    import base64
    import sys  # [fix kritis 2026-10-16] 'sys' TIDAK pernah di-import di file ini
    # (hanya _sys_b) -> NameError di luar try = proses CRASH tepat saat akun
    # lahir (users_ok -> naikkan_sesi) -> run-json & pencatatan akun hilang
    # (akar 'akun yatim' + run A/B tanpa artefak; bukti stderr run-1792182421)
    sys.path.insert(0, os.path.join(HERE, "vendor"))
    import srp as _srp
    # [bedah HAR 2026-10-16] persis browser asli: Username = email LENGKAP
    # (user@protonmail.com), x-pm-appversion = versi bundle hidup (428.1, bukan
    # 428.0), + x-pm-locale. Beda di sini = app "asli" tiba-tiba menyimpang.
    mail_penuh = mail if "@" in mail else f"{mail}@{MAIL_DOMAIN}"
    pm_locale = str(FP.get("locale", "en-US")).replace("-", "_")
    pm_appver = "web-account@5.0.433.0"  # [fix 2026-11-05] versi bundle HIDUP dicek
    try:
        ambil_uid = "() => ((document.cookie.match(/(?:AUTH|REFRESH)-([a-z0-9]+)/) || [])[1] || '')"
        # [fix 401 2026-10-16] cookie AUTH-/REFRESH- berflag httponly (bukti HAR
        # golden: '; secure; httponly') -> TIDAK terbaca document.cookie -> uid
        # kosong -> x-pm-uid '' -> 401 'Invalid access token'. Ambil dari cookie
        # API Playwright (melihat HttpOnly); regex JS tetap sbg cadangan.
        uid_pm = ""
        try:
            for _c in page.context().cookies():
                _nm = _c.get("name", "")
                if _nm.startswith("AUTH-") or _nm.startswith("REFRESH-"):
                    uid_pm = _nm.split("-", 1)[1]
                    break
        except Exception:
            pass
        info = page.evaluate("""async ([mail, uidVal, uidFn, appver, locale]) => {
            const uid = uidVal || eval(uidFn)();
            const r = await fetch('/api/core/v4/auth/info', {
                method: 'POST', credentials: 'include',
                headers: {'content-type': 'application/json',
                          'accept': 'application/vnd.protonmail.v1+json',
                          'x-pm-appversion': appver,
                          'x-pm-locale': locale,
                          'x-pm-uid': uid},
                body: JSON.stringify({Username: mail, Intent: 'Proton'})});
            return await r.json();
        }""", [mail_penuh, uid_pm, ambil_uid, pm_appver, pm_locale])
        if (info or {}).get("Code") != 1000 or not info.get("Salt"):
            return {"ok": False, "info": str(info)[:100]}
        import proton_api
        mod = proton_api._buka_modulus(info["Modulus"])
        b = _srp.bukti_login(mail, pw, mod, info["Salt"],
                             base64.b64decode(info["ServerEphemeral"]))
        out = page.evaluate("""async ([payload, uidVal, uidFn, appver, locale]) => {
            const uid = uidVal || eval(uidFn)();
            const r = await fetch('/api/core/v4/auth', {
                method: 'POST', credentials: 'include',
                headers: {'content-type': 'application/json',
                          'accept': 'application/vnd.protonmail.v1+json',
                          'x-pm-appversion': appver,
                          'x-pm-locale': locale,
                          'x-pm-uid': uid},
                body: JSON.stringify(payload)});
            return await r.json();
        }""", [{"ClientProof": b["clientProof"],
                "ClientEphemeral": b["clientEphemeral"],
                "SRPSession": info["SRPSession"], "Username": mail_penuh,
                "PersistentCookies": 0}, uid_pm, ambil_uid, pm_appver, pm_locale])
        sc = (out or {}).get("Scopes") or []
        return {"ok": "full" in sc, "scopes": sc[:6]}
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {str(e)[:80]}"}


def pasang_recovery_browser(page, mail_penuh, pw, note):
    """[bedah DOM live 2026-10-22] Pasang email pemulihan lewat UI BROWSER —
    jalur yang sama dengan mitm user. KUNCI dari user: SEMUA langkah sampai
    klik link verifikasi harus di SATU browser/sesi yang sama ('kalau beda
    pasti gagal'). Peta PRESISI hasil bedah DOM live (bukan tebakan):
      sidebar settings         : a[href="/u/1/mail/recovery"] (item 'Recovery')
      kartu 'Email verification': a[href="/u/1/mail/recovery/email"]
      form halaman itu         : input[data-testid="input-input-element"][type=text]
                                 input[data-testid="input-input-element"][type=password]
                                 button[type=submit] — email+password SATU halaman
      [aturan user 2026-11-05] TIDAK PERNAH login ulang: kalau yang tampil
      halaman Sign in = urutan SALAH (sesi mail belum dibuat) -> batal +
      screenshot, JANGAN di-login (login ulang = pemicu 422 10003 suspended).
    TIDAK ada API mentah (SRP curl = pemicu 'Account locked'). Return
    {ok, alias, sebab}; gagal tidak fatal (preflight API jadi pemeriksa)."""
    try:
        alias, _meta = create_mailbox()
    except Exception as e:
        return {"ok": False, "alias": "", "sebab": f"alias gagal: {type(e).__name__}"}
    t0 = time.time()

    def _form_login_tampil():
        try:
            lf = page.locator('[data-testid="login-form"]')
            return bool(lf.count() and lf.first.is_visible())
        except Exception:
            return False

    def _kolom_email():
        """[fix 2026-11-05 uji live run-1793873946] kolom email recovery UI
        5.0.433.0 = input placeholder 'example@domain.com' TANPA [type=text]
        eksplisit -> selektor bedah 22 Okt '[data-testid=input-input-element]
        [type=text]' TIDAK match (bukti screenshot v2-74067: form ADA di layar
        tapi 'kolom email tak ditemukan'). Kandidat berurutan; jangan tertukar
        kotak 'Search settings' di header settings."""
        for sel in ('input[placeholder*="domain.com"]',
                    '[data-testid="input-input-element"][type="text"]',
                    '[data-testid="input-input-element"]', 'input[type="text"]'):
            try:
                loc = page.locator(sel)
                for i in range(loc.count()):
                    c = loc.nth(i)
                    if not c.is_visible():
                        continue
                    if "search" in (c.get_attribute("placeholder") or "").lower():
                        continue
                    return c
            except Exception:
                continue
        return None

    def _kolom_password():
        """[fix 2026-11-05] password TIDAK selalu satu halaman dgn email (UI
        baru): bukti HAR emas 5 Nov idx 146-148 — klik 'Add and verify' ->
        PUT /settings/email 403 MissingScopes:['password'] -> MUNCUL modal
        konfirmasi password -> auth/info -> PUT ulang + ClientProof SRP.
        Cari kolom password kapan pun: halaman lama (satu halaman) maupun
        modal pasca-klik."""
        for sel in ('[data-testid="input-input-element"][type="password"]',
                    'input[type="password"]'):
            try:
                loc = page.locator(sel)
                for i in range(loc.count()):
                    c = loc.nth(i)
                    if c.is_visible():
                        return c
            except Exception:
                continue
        return None

    def _tunggu_kondisi(fn, budget, poll=0.5):
        """[prinsip user 2026-11-05: TUNGGU BERBASIS KONDISI/RESPON] poll
        interval pendek sampai fn() truthy; page.wait_for_timeout = POMPA
        dispatcher Playwright (time.sleep TIDAK — lihat _tunggu_net). Pagar
        budget hanya bila kondisi tak pernah terpenuhi. Return fn() truthy
        pertama atau None."""
        akhir = time.time() + budget
        while True:
            try:
                v = fn()
            except Exception:
                v = None
            if v:
                return v
            if time.time() >= akhir:
                return None
            try:
                page.wait_for_timeout(int(poll * 1000))
            except Exception:
                time.sleep(poll)

    def _tunggu_net(pred, budget, awal=0, poll=0.5):
        """[fix balapan waktu run-1793890022 tibianaia5547 — prinsip user:
        TUNGGU BERBASIS RESPONS] Tunggu ENTRI NET (event-based): polling daftar
        log net interval pendek sambil MEMOMPA dispatcher Playwright.
        AKAR bug 'server diam (PUT/verify tak terlihat)': penantian lama pakai
        time.sleep — selama main thread tidur, handler on_response TIDAK
        dieksekusi (playwright sync_api _sync_base: 'while not task.done():
        dispatcher_fiber.switch()') -> log net MEMBEKU + vonis keburu jatuh
        padahal PUT /settings/email 200 (SRP proof di browser ~49 dtk) baru
        mendarat (bukti run: klik Authenticate t=1793890147 -> PUT 200
        t=1793890196, vonis palsu 'server diam' di detik yang sama).
        Pagar budget = REM bila respons benar2 tak datang, BUKAN penantian.
        Return entri net pertama cocok pred (hanya entri ber-status), atau
        None bila pagar habis."""
        akhir = time.time() + budget
        while True:
            for n in log.get("net", [])[awal:]:
                try:
                    if isinstance(n.get("s"), int) and pred(n):
                        return n
                except Exception:
                    continue
            if time.time() >= akhir:
                return None
            try:
                page.wait_for_timeout(int(poll * 1000))
            except Exception:
                time.sleep(poll)

    def _kode_body(n):
        """Code JSON dari body entri net (1000=OK, 9102=MissingScopes,
        12006/2011/2001=alias ditolak validator)."""
        try:
            return str((json.loads(n.get("body") or "{}") or {}).get("Code", ""))
        except Exception:
            return ""

    def _setelah(n):
        """Indeks entri BERIKUTNYA sesudah n (IDENTITAS objek, bukan == —
        entri kembar bisa tertukar) — respons cepat berikutnya tak boleh
        terlewat saat scan berikutnya."""
        try:
            for i, x in enumerate(log["net"]):
                if x is n:
                    return i + 1
        except Exception:
            pass
        return len(log.get("net", []))

    try:
        note(f"[recovery-ui] buka halaman recovery (jalur aplikasi), alias={alias}")
        try:
            page.goto("https://account.proton.me/u/1/mail/recovery",
                      wait_until="domcontentloaded", timeout=45000)
            _tunggu_kondisi(lambda: _kolom_email() is not None
                            or _form_login_tampil(), 20)
            if _form_login_tampil():
                shot(page, "recovery-terhalang-signin")
                return {"ok": False, "alias": alias,
                        "sebab": "halaman Sign in — sesi mail belum dibuat; TIDAK login ulang (aturan user 2026-11-05)"}
            kartu = page.locator('a[href="/u/1/mail/recovery/email"]').first
            if kartu.count() and kartu.is_visible():
                kartu.click(timeout=5000)
                _tunggu_kondisi(lambda: _kolom_email() is not None, 15)
            else:
                page.goto("https://account.proton.me/u/1/mail/recovery/email",
                          wait_until="domcontentloaded", timeout=45000)
                _tunggu_kondisi(lambda: _kolom_email() is not None
                                or _form_login_tampil(), 20)
        except Exception as e:
            note(f"[recovery-ui] navigasi recovery: {type(e).__name__} — paksa rute")
            page.goto("https://account.proton.me/u/1/mail/recovery/email",
                      wait_until="domcontentloaded", timeout=45000)
            _tunggu_kondisi(lambda: _kolom_email() is not None
                            or _form_login_tampil(), 20)
        # [fix 2026-10-22] halaman kadang me-redirect kita pergi dari recovery —
        # WAJIB kembali ke halaman recovery, kalau tidak form tidak akan pernah
        # ketemu ('kolom email tak ditemukan' di 2 korban 3x3). [2026-11-05]
        # _login_inline DIHAPUS (aturan user: login ulang = 10003); kalau form
        # login yang tampil = urutan salah -> batal + screenshot.
        for _putar in range(3):
            if _form_login_tampil():
                shot(page, "recovery-terhalang-signin")
                return {"ok": False, "alias": alias,
                        "sebab": "halaman Sign in saat recovery — TIDAK login ulang (aturan user 2026-11-05)"}
            _tunggu_kondisi(lambda: _kolom_email() is not None
                            or _form_login_tampil(), 8)
            kol = _kolom_email()
            if kol is not None:
                break
            note("[recovery-ui] form belum tampil — kembali ke halaman recovery")
            try:
                page.goto("https://account.proton.me/u/1/mail/recovery/email",
                          wait_until="domcontentloaded", timeout=45000)
                _tunggu_kondisi(lambda: _kolom_email() is not None
                                or _form_login_tampil(), 20)
            except Exception:
                pass
        time.sleep(random.uniform(1.0, 2.0))
        # --- form: alamat (+ password bila satu halaman; UI baru = modal) ---
        kol = _kolom_email()
        if kol is None:
            shot(page, "recovery-form-tak-ada")   # [wajib 2026-11-05] diagnosa VISUAL
            try:
                _st = page.evaluate("() => ({url: location.href, "
                                    "text: (document.body.innerText||'').slice(0,400)})")
            except Exception:
                _st = {"url": "?", "text": "?"}
            note(f"[recovery-ui] kolom email tak ditemukan — url={_st['url'][:80]} "
                 f"teks={_st['text'][:120]!r}")
            return {"ok": False, "alias": alias,
                    "sebab": f"kolom email tak ditemukan (url={_st['url'][:70]})"}
        def _ketik_verified(loc, teks, label):
            """[aturan #12 2026-11-05] kolom WAJIB benar terisi sebelum submit —
            ketikan hilang = proof SRP salah -> PUT 422 (jejak run-1793878569:
            modal terisi tapi PUT +proof 422 2x, verify/send tak pernah terkirim).
            Return True bila kolom PASTI terisi `teks` (dicek ULANG pasca-refill
            — jangan percaya fill() buta). Pemanggil WAJIB batal bila False;
            berlaku juga utk kolom password SEBELUM klik Authenticate."""
            loc.click()
            human_type(page, loc, teks)
            time.sleep(random.uniform(0.8, 1.6))
            try:
                kini = (loc.input_value() or "").strip()
            except Exception:
                kini = None
            if kini is not None and kini != teks.strip():
                note(f"[recovery-ui] kolom {label} tak cocok pasca-ketik "
                     f"({len(kini)} vs {len(teks.strip())} char) — isi ulang")
                try:
                    loc.fill(teks)
                    time.sleep(random.uniform(0.8, 1.5))
                except Exception:
                    pass
                try:
                    kini = (loc.input_value() or "").strip()
                except Exception:
                    kini = None
            return kini is None or kini == teks.strip()

        def _konfirmasi_password():
            """modal 'Enter your password' UI 5.0.433.0: tombol 'Authenticate'
            (bukti shot v2-78667-recovery-modal-password.png) — dulu selektor
            Confirm/Verify/Unlock/OK tak pernah match -> Enter buta.
            [fix 2026-11-05] return True bila Authenticate TERKLIK: modal render
            asinkron pasca-403 -> kolom password ditunggu berbasis KONDISI, dan
            kolom WAJIB terverifikasi terisi SEBELUM klik (ketikan hilang =
            proof SRP salah -> PUT 422). Hasil klik (PUT +proof SRP ~49 dtk)
            ditunggu berbasis EVENT NET di alur utama — di sini TIDAK ada sleep
            penantian hasil."""
            pwf2 = _tunggu_kondisi(_kolom_password, 12)
            if pwf2 is None:
                note("[recovery-ui] modal password tak terlihat setelah 9102/9106")
                return False
            shot(page, "recovery-modal-password")
            try:
                if not _ketik_verified(pwf2, pw, "password"):
                    note("[recovery-ui] kolom password tak terisi benar — BATAL klik Authenticate")
                    return False
                dikonfirmasi = False
                for sel in ("[role=dialog] button:has-text('Authenticate')",
                            "button:has-text('Authenticate')",
                            "[role=dialog] button:has-text('Confirm')",
                            "[role=dialog] button:has-text('Verify')",
                            "[role=dialog] button:has-text('Unlock')",
                            "[role=dialog] button:has-text('OK')",
                            "[role=dialog] button[type=submit]",
                            "button:has-text('Confirm')", "button:has-text('Unlock')",
                            "button[type=submit]"):
                    try:
                        b = page.locator(sel).first
                        if b.count() and b.is_visible():
                            b.click(timeout=4000)
                            note(f"[recovery-ui] konfirmasi password diklik: {sel}")
                            dikonfirmasi = True
                            break
                    except Exception:
                        continue
                if not dikonfirmasi:
                    pwf2.press("Enter")   # modal tanpa tombol dikenal: Enter = submit
                    note("[recovery-ui] konfirmasi password via Enter")
                    dikonfirmasi = True
                try:  # jeda pasca-klik = humanisasi modal menutup, BUKAN
                    # penantian hasil (hasil = respons PUT; ditunggu di alur)
                    page.wait_for_timeout(random.uniform(1.2, 2.5) * 1000)
                except Exception:
                    pass
                return dikonfirmasi
            except Exception as e:
                note(f"[recovery-ui] modal password gagal: {type(e).__name__}: {str(e)[:60]}")
                return False

        def _klik_verify_with_email():
            for sel in ("[role=dialog] button:has-text('Verify with email')",
                        "button:has-text('Verify with email')"):
                try:
                    b = page.locator(sel).first
                    if b.count() and b.is_visible():
                        b.click(timeout=4000)
                        note(f"[recovery-ui] tombol kirim verifikasi diklik: {sel}")
                        return True
                except Exception:
                    continue
            return False

        def _kirim_verifikasi():
            """[bukti screenshot v2-90196-final.png run-1793890022] pasca-PUT
            200 UI menampilkan dialog 'Verify recovery email?' dgn tombol
            'Verify with email' — klik itu = pemicu POST verify/send (HAR emas
            idx 150 = klik user, BUKAN auto-kirim). Bila dialog belum terbuka:
            link 'Verify' di baris 'Unverified' MEMBUKANYA. Teks PERSIS
            'Verify' (bukan has-text) = jangan salah pukul 'Add and verify'
            (submit ulang)."""
            if _klik_verify_with_email():
                return True
            for sel in ('text="Verify"', 'a:has-text("Verify")'):
                try:
                    b = page.locator(sel).first
                    if b.count() and b.is_visible():
                        b.click(timeout=4000)
                        note(f"[recovery-ui] link Verify baris diklik: {sel}")
                        _tunggu_kondisi(_klik_verify_with_email, 8)  # dialog render
                        return True
                except Exception:
                    continue
            note("[recovery-ui] tombol kirim verifikasi tak terlihat")
            return False

        for kesempatan in range(2):
            if kesempatan:
                # [fix 2026-11-05] alias DITOLAK validator Proton (kode server
                # 12006/2011/2001) -> alias BARU + submit ulang — pola terbukti
                # pasang() v1 'fix gerbang-2' (dulu menyerah = 1 akun terbuang).
                try:
                    alias, _m2 = create_mailbox()
                except Exception as e:
                    return {"ok": False, "alias": alias,
                            "sebab": f"alias pengganti gagal: {type(e).__name__}"}
                note(f"[recovery-ui] alias ditolak server -> ganti {alias}")
                kol = _kolom_email()
                if kol is None:
                    return {"ok": False, "alias": alias,
                            "sebab": "kolom email hilang saat ganti alias"}
            if not _ketik_verified(kol, alias, "email"):
                return {"ok": False, "alias": alias,
                        "sebab": "kolom email tak terisi benar pasca-ketik"}
            pwf = _kolom_password()
            if pwf is not None:        # UI lama: email+password satu halaman
                if not _ketik_verified(pwf, pw, "password"):
                    return {"ok": False, "alias": alias,
                            "sebab": "kolom password tak terisi benar pasca-ketik"}
            diklik = False
            awal = len(log.get("net", []))   # [kunci] SEBELUM klik — respons
            # cepat (PUT 403 9102 ~3 dtk pasca-klik) tak boleh terlewat
            for sel in ("button:has-text('Verify')", "button:has-text('Save')",
                        "button:has-text('Confirm')", "button[type=submit]"):
                try:
                    b = page.locator(sel).first
                    if b.count() and b.is_visible():
                        b.click(timeout=4000)
                        note(f"[recovery-ui] tombol verify diklik: {sel}")
                        diklik = True
                        break
                except Exception:
                    continue
            if not diklik:
                return {"ok": False, "alias": alias, "sebab": "tombol verify tak ditemukan"}
            # [fix 2026-11-05 balapan waktu run-1793890022 tibianaia5547 —
            # prinsip user: TUNGGU BERBASIS RESPONS, pagar hanya bila respons
            # benar2 tak datang] Rantai bukti HAR emas 5 Nov idx 146-156 +
            # screenshot v2-90141/90196: klik 'Add and verify' -> PUT
            # /settings/email 403 9102 MissingScopes:['password'] -> modal
            # password -> auth/info -> PUT ulang + ClientProof SRP (~49 dtk di
            # browser! bukti run: klik Authenticate t=1793890147 -> PUT 200
            # t=1793890196) -> dialog 'Verify recovery email?' -> klik 'Verify
            # with email' -> POST verify/send -> link -> verify/validate.
            # Dulu _vonis_server (time.sleep 3 dtk + budget 45 dtk) vonis
            # 'server diam' TEPAT saat PUT 200 mendarat — padahal email
            # recovery SUDAH terpasang; langkah verify tak pernah jalan.
            put_ok, konfirmasi, idx = None, 0, awal
            while True:
                n_put = _tunggu_net(
                    lambda n: ("/settings/email" in str(n.get("u", ""))
                               and str(n.get("m", "")).upper() == "PUT"),
                    90, idx)   # pagar 90 dtk: SRP proof browser ~49 dtk (bukti)
                if n_put is None:
                    shot(page, "recovery-put-diam")
                    return {"ok": False, "alias": alias,
                            "sebab": "tak ada respons PUT settings/email dalam "
                                     "90 dtk setelah submit (server benar2 diam)"}
                s, kode = n_put.get("s"), _kode_body(n_put)
                note(f"[recovery-ui] PUT settings/email st={s} code={kode or '-'}")
                if s == 200 and kode in ("", "1000"):
                    put_ok = n_put
                    break
                if s == 200:
                    return {"ok": False, "alias": alias,
                            "sebab": f"PUT settings/email 200 tapi Code {kode or '?'}"}
                if kode in ("9102", "9106"):
                    # MissingScopes:['password'] = modal konfirmasi password
                    # (wajar, BUKAN vonis gagal) — tangani: isi + Authenticate
                    idx = _setelah(n_put)
                    if konfirmasi >= 2:
                        return {"ok": False, "alias": alias,
                                "sebab": f"PUT settings/email tetap 403 {kode} "
                                         f"setelah {konfirmasi}x konfirmasi password"}
                    konfirmasi += 1
                    if not _konfirmasi_password():
                        return {"ok": False, "alias": alias,
                                "sebab": "modal password tak tertangani (kolom "
                                         "tak terisi/tombol tak ditemukan)"}
                    continue   # tunggu PUT berikutnya (pasca-Authenticate)
                if kode in ("12006", "2011", "2001") and kesempatan == 0:
                    note(f"[recovery-ui] alias ditolak validator (code {kode}) — ganti alias")
                    break      # -> loop luar: alias baru + submit ulang
                return {"ok": False, "alias": alias,
                        "sebab": f"link tak terkirim — PUT settings/email "
                                 f"st={s} code={kode or '-'}"}
            if put_ok is None:
                continue       # alias ditolak -> kesempatan ke-2 dgn alias baru
            # --- kirim verifikasi: dialog 'Verify recovery email?' pasca-PUT 200
            # tombol 'Verify with email' (bukti shot v2-90196-final.png) ->
            # POST verify/send — tunggu RESPONS-nya, bukan timer ---
            _kirim_verifikasi()
            n_send = _tunggu_net(lambda n: "verify/send" in str(n.get("u", "")),
                                 50, awal)   # pagar ~50 dtk (arahan user)
            if n_send is None:
                shot(page, "recovery-verify-send-diam")
                return {"ok": False, "alias": alias,
                        "sebab": "verify/send tak terlihat dalam 50 dtk pasca-PUT 200"}
            if n_send.get("s") != 200 or _kode_body(n_send) not in ("", "1000"):
                return {"ok": False, "alias": alias,
                        "sebab": f"verify/send st={n_send.get('s')} "
                                 f"code={_kode_body(n_send) or '-'}"}
            note("[recovery-ui] verify/send 200 — tunggu link di alias")
            break
        # --- link verifikasi: ambil dari alias, KLIK di browser YANG SAMA ---
        import recovery_email
        jwt = recovery_email.ambil_jwt(alias, sejak=t0, budget=240)
        if not jwt:
            return {"ok": False, "alias": alias, "sebab": "link verifikasi tak datang ke alias"}
        # [HAR user 5 Nov idx 155-156] link emas = /verify-email?email=<akun
        # proton>#<jwt> (referer verify/validate = verify-email?email=akun,
        # BUKAN email alias) — samakan bentuk persis.
        awal_val = len(log.get("net", []))
        page.goto("https://account.proton.me/verify-email?email="
                  + urllib.parse.quote(mail_penuh) + "#" + jwt,
                  wait_until="domcontentloaded", timeout=45000)
        n_val = _tunggu_net(lambda n: "verify/validate" in str(n.get("u", "")),
                            50, awal_val)   # pagar ~50 dtk (arahan user)
        shot(page, "recovery-ui-tuntas")
        # [arahan user 2026-11-05] vonis AKHIR dari bukti NET (PUT/verify-send/
        # verify-validate), bukan dari timer.
        bukti = {"put_settings_email": put_ok.get("s"),
                 "verify_send": n_send.get("s"),
                 "verify_validate": n_val.get("s") if n_val else None}
        note(f"[recovery-ui] bukti net: {bukti}")
        if n_val is None:
            return {"ok": False, "alias": alias, "bukti": bukti,
                    "sebab": "verify/validate tak terlihat dalam 50 dtk "
                             "setelah link dibuka"}
        if n_val.get("s") != 200 or _kode_body(n_val) not in ("", "1000"):
            return {"ok": False, "alias": alias, "bukti": bukti,
                    "sebab": f"verify/validate st={n_val.get('s')} "
                             f"code={_kode_body(n_val) or '-'}"}
        note(f"[recovery-ui] TUNAS via browser (link diklik): {alias}")
        return {"ok": True, "alias": alias, "sebab": "tuntas via UI browser",
                "bukti": bukti}
    except Exception as e:
        return {"ok": False, "alias": alias,
                "sebab": f"{type(e).__name__}: {str(e)[:80]}"}


def create_mailbox():
    """(email, meta) sesuai MAIL_PROVIDER — headmail (mode domain) atau evilmail."""
    if MAIL_PROVIDER == "evilmail":
        # jalur terbukti: random-email ACCOUNT di domain (bukan temp-session yang buntu)
        _guard_public("evilmail.pro")
        r = requests.post(f"{EVILMAIL}/api/random-email",
                          headers={"x-api-key": EVILMAIL_KEY, "user-agent": UA,
                                   "content-type": "application/json"},
                          json={"domain": EVIL_DOMAIN, "count": 1, "passwordLength": 20}, timeout=25)
        d = r.json()
        assert d.get("status") == "success", f"evilmail gagal: {d}"
        e = d["data"]["emails"][0]
        return e["email"], {"acct_pass": e.get("password", "")}
    r = requests.post(f"{HEADMAIL}/api/v1/aliases",
                      headers={"authorization": "Bearer " + HEADMAIL_KEY, "user-agent": UA,
                               "content-type": "application/json"},
                      json={"source": "domain", "total": 1, "domains": [HM_DOMAIN]}, timeout=25)
    return r.json()["created"][0]["email"], {"provider": "headmail"}


def wait_otp(alias, meta, t0, budget=240):
    if MAIL_PROVIDER == "evilmail":
        H = {"x-api-key": EVILMAIL_KEY, "user-agent": UA}
        end = time.time() + budget
        dumped = False
        while time.time() < end:
            q = urllib.parse.urlencode({"email": alias})
            d = requests.get(f"{EVILMAIL}/api/accounts/inbox?{q}", headers=H, timeout=25).json()
            msgs = d.get("data") or []
            if msgs and not dumped:
                note(f"bentuk pesan evilmail: {json.dumps(msgs[0])[:300]}")
                dumped = True
            for m in msgs:
                text = f"{m.get('subject', '')} {m.get('preview') or m.get('snippet') or ''}"
                mm = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
                if not mm:
                    uid = m.get("id") or m.get("uid")
                    if uid is not None:
                        try:
                            md = requests.get(f"{EVILMAIL}/api/accounts/message/{uid}?{q}",
                                              headers=H, timeout=25).json()
                            dd = md.get("data") or {}
                            pool = f"{dd.get('text', '')} {dd.get('html', '')} {json.dumps(dd)}"
                            mm = re.search(r"(?<!\d)(\d{6})(?!\d)", pool)
                        except Exception:
                            pass
                if mm:
                    note(f"OTP masuk (evilmail): {mm.group(1)} ({m.get('subject')})")
                    return mm.group(1)
            note(f"...menunggu OTP (evilmail, {len(msgs)} pesan)")
            time.sleep(8)
        return None
    end = time.time() + budget
    while time.time() < end:
        q = urllib.parse.urlencode({"email": alias, "wait": 25, "since": t0, "any": 1})
        d = requests.get(f"{HEADMAIL}/api/v1/otp?{q}",
                         headers={"authorization": "Bearer " + HEADMAIL_KEY, "user-agent": UA},
                         timeout=40).json()
        if d.get("found") and d.get("otp"):
            note(f"OTP masuk: {d['otp']} ({d.get('subject')})")
            return d["otp"]
        note("...menunggu OTP")
    return None


_NAMA_AWAL = ["mar", "jul", "aug", "luc", "fel", "cor", "vin", "cla", "sei", "tar",
              "gal", "cam", "oct", "pomp", "cass", "flav", "sext", "tib", "gai", "lucr"]
_NAMA_AKHIR = ["ius", "ia", "us", "a", "um", "inus", "ella", "ianus", "iana", "io", "or", "on"]

# Nama depan & belakang realistis untuk display name Proton (anti-suspend:
# nama acak 'flav5942ianus' terdeteksi pola bot; nama manusia lebih natural)
_NAMA_DEPAN = ["Adi", "Budi", "Citra", "Dedi", "Eka", "Fajar", "Gita", "Hana",
               "Indah", "Joko", "Kirana", "Luki", "Maya", "Nina", "Oki", "Putri",
               "Rama", "Sari", "Tono", "Uma", "Vina", "Wawan", "Yoga", "Zara",
               "Arif", "Bella", "Cahy", "Dewi", "Fika", "Gede", "Hari", "Ika"]
_NAMA_BELAKANG = ["Pratama", "Saputra", "Wijaya", "Kusuma", "Putra", "Lestari",
                  "Astuti", "Hidayat", "Ningrum", "Setiawan", "Rahayu", "Mahendra",
                  "Suharto", "Permana", "Susanti", "Manurung", "Sapto", "Kurnia",
                  "Hidayanto", "Wijaya", "Firmansyah", "Rahardjo", "Muliawan",
                  "Nugroho", "Hidayat", "Suryono", "Darajat", "Efendi", "Gunawan"]


def _nama_tampilkan():
    """Display name realistis (depan + belakang, spasi di antaranya) — anti-suspend:
    Proton ML clustering mendeteksi pola nama acak 'flav5942ianus' sebagai bot.
    Nama manusia depan+belakang lebih natural dan sulit diklasifikasikan bulk."""
    return f"{secrets.choice(_NAMA_DEPAN)} {secrets.choice(_NAMA_BELAKANG)}"


_PID_UNIK = f"{os.getpid() % 100:02d}"   # [anti-tabrak 2026-10-16] akhiran unik


def _nama_acak():
    """Username bergaya nama Latin acak — variasi 2026-10-15 (permintaan user):
    pola lebih beragam (digit 2-5, kadang digit di tengah, kadang 3 silabel)
    supaya tidak menabrak pola nama akun lama (2x kejadian email_rejected).
    [anti-tabrak 2026-10-16] 2 digit terakhir = unik-per-proses (temuan riset:
    17 run kena 409:2011 'username keburu dipakai' = balapan nama antar worker
    paralel). Tampilan tetap 'silabel+digit' seperti nama manusia."""
    awal = secrets.choice(_NAMA_AWAL)
    akhir = secrets.choice(_NAMA_AKHIR)
    angka = lambda k: "".join(secrets.choice(string.digits) for _ in range(k))
    pola = secrets.randbelow(4)
    if pola == 0:            # silabel-silabel + digit 3-5 (2 digit = unik/proses)
        return awal + akhir + angka(1 + secrets.randbelow(3)) + _PID_UNIK
    if pola == 1:            # silabel-digit-silabel (digit di tengah)
        return awal + angka(2) + _PID_UNIK + akhir
    if pola == 2:            # tiga silabel + digit 3-4
        return awal + akhir + secrets.choice(_NAMA_AWAL) + angka(1 + secrets.randbelow(2)) + _PID_UNIK
    return awal + secrets.choice(_NAMA_AKHIR) + akhir + angka(1 + secrets.randbelow(3)) + _PID_UNIK


def _password_acak():
    """Password kuat acak penuh (tanpa pola tetap): 14-22 char, huruf besar/kecil,
    angka, simbol — tanpa '|' (pemisah kolom registered.txt)."""
    abjad = string.ascii_letters + string.digits + "!@#$%^&*_-+="
    while True:
        pw = "".join(secrets.choice(abjad) for _ in range(14 + secrets.randbelow(9)))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and sum(c.isdigit() for c in pw) >= 2):
            return pw


def _akun_terbukti_hidup(mail, pw):
    """[fix 2026-10-21 bug '409 salah-baca'] 409 'username sudah dipakai' TIDAK
    berarti gagal: pola nyata (octaia4450, gal7180us) = resubmit pertama yang
    ter-ABORTED di sisi klien justru DIPROSES server -> akun LAHIR, resubmit
    berikutnya dapat 409, lalu kode menyerah padahal akun ADA. Buktikan dulu
    via login SRP dari exit YANG SAMA (SIGNUP_PROXY — anti 10003):
    sukses = akun hidup & milik kita."""
    try:
        import proton_api
        s, _h = proton_api.login(mail, pw, proxy=os.environ.get("SIGNUP_PROXY") or None)
        return s is not None
    except Exception:
        return False


username = _nama_acak()
password = _password_acak()
t0 = int(time.time() * 1000)
alias, mail_meta = None, {}  # alias dibuat ON-DEMAND hanya bila perlu verifikasi email
log["username"], log["password"] = username, password
note(f"akun: {username}@{MAIL_DOMAIN} (alias dibuat hanya jika menu butuh email)")

INVENTORY = """
(() => {
  const out = [];
  for (const el of document.querySelectorAll('input, button, select, [role=button], textarea')) {
    const r = el.getBoundingClientRect();
    out.push({tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '', id: el.id || '',
              placeholder: el.placeholder || '', aria: el.getAttribute('aria-label') || '',
              text: (el.innerText || '').trim().slice(0, 40),
              rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]});
  }
  return out;
})()
"""

def _perakit_flag_profil_dd(profil_tmp, cdp_port, proxy_arg, FP):
    """Susun flag Chrome mentah dari bendera_chrome dd_panen (terbukti DevTools
    hidup + DataDome lolos) — buang duplikat window/blank, tambah persona."""
    import dd_panen
    dasar = [a for a in dd_panen.bendera_chrome(str(profil_tmp), cdp_port,
                                                proxy_arg.replace("--proxy-server=", "")
                                                or "")
             if not a.startswith("--window-size=") and a != "about:blank"]
    return [str(konfig.CHROME_BIN), *dasar,
            f"--lang={FP['locale']}",
            f"--window-size={FP['viewport']['width']},{FP['viewport']['height']}",
            "about:blank"]


fp_profile.ensure_display()  # Xvfb -> Chrome HEADED (bukan HeadlessChrome yang bocor)
FP = fp_profile.build_profile()
# [fix 2026-10-22 — arahan user: 'tinggal duplikat aja' mitm] Template emas =
# HAR manual user (flows-proton.jsonl): challenge SELALU Lang=en-US, TANPA
# ganti bahasa; accept-language 'en-US,en;q=0.9'. Persona non-EN membuat
# challenge load en-US lalu SWAP ke de-DE (reload ganda -> yang en-US
# ERR_ABORTED -> frame form sempat hilang -> submit tak terkirim, pola
# pompinus755). Kunci locale di EN-US = persis jalur mitm yang terbukti.
FP["locale"] = "en-US"
log["fp_profile"] = FP
os.environ["FONTCONFIG_FILE"] = fp_profile.write_fonts_conf(FP)  # set font persona (klaster "fonts")
with sync_playwright() as p:
    launch_kw = dict(executable_path=str(konfig.CHROME_BIN), headless=False,
                     args=fp_profile.chrome_args(FP),
                     # [fix 2026-11-05] LANG persona -> Chrome membentuk
                     # navigator.languages/Accept-Language 2-entri emas dari
                     # env (jangan opsi locale — lihat _kw_ctx). Deterministik
                     # walau shell pemanggil tanpa LANG.
                     env=dict(os.environ,
                              LANG=str(FP.get("locale", "en-US")).replace("-", "_") + ".UTF-8"))
    signup_proxy = os.environ.get("SIGNUP_PROXY", "")
    if signup_proxy:
        pr = urllib.parse.urlparse(signup_proxy if "//" in signup_proxy else "http://" + signup_proxy)
        pk = {"server": f"http://{pr.hostname}:{pr.port or 80}"}
        if pr.username:  # Chromium tidak mengautentikasi kredensial dari URL — pisahkan
            pk["username"] = pr.username
            pk["password"] = pr.password or ""
        # [fix kebocoran 2026-10-14] DAFTAR BYPASS DIHAPUS total: host bypass
        # keluar DIRECT dari IP VPS = identitas sesi tercampur (exit proxy +
        # datacenter) = sinyal anti-spoofing. Semua lalu lintas kini SATU jalur
        # (proxy sesi). Lalu lintas internal Chrome dimatikan di _BASE_ARGS
        # fp_profile (0 request, 0 bocor, 0 byte) — bukan direct, bukan cache
        # (endpoint dinamis: safebrowsing/autofill tidak bisa di-cache).
        launch_kw["proxy"] = pk
    # timezone mengikuti geolokasi IP (DE -> Europe/Berlin; JP -> set TZ_OVERRIDE=Asia/Tokyo)
    # [fix 2026-11-05 inkoherensi TZ-vs-IP] hardcode Europe/Berlin saat proxy
    # aktif = persona klaim zona Jerman padahal exit pool ops/proxy-gh-3000.txt
    # = fr/nl/ie/se/fi/lu (nol DE) -> TZ salah-benua vs IP = jejak anti-abuse
    # (aturan persona koheren #12). TZ kini ikut NEGARA EXIT dari token
    # 'country-xx' di username proxy: langsung di SIGNUP_PROXY (TLS asli) atau
    # data/upstream-<port>.json (mode router: SIGNUP_PROXY = 127.0.0.1:port).
    # Fallback tetap seperti sekarang (Berlin bila ada proxy, Jakarta tanpa).
    _tz_negara = {"fr": "Europe/Paris", "nl": "Europe/Amsterdam",
                  "ie": "Europe/Dublin", "se": "Europe/Stockholm",
                  "fi": "Europe/Helsinki", "lu": "Europe/Luxembourg",
                  "de": "Europe/Berlin", "gb": "Europe/London",
                  # [2026-11-08] 5 negara gudang gh-3000-baru: TZ ikut exit
                  "at": "Europe/Vienna", "be": "Europe/Brussels",
                  "ch": "Europe/Zurich", "dk": "Europe/Copenhagen",
                  "no": "Europe/Oslo"}
    _m_cc = re.search(r"country-([a-z]{2})", signup_proxy or "")
    if not _m_cc:
        try:   # mode router: exit ada di upstream-<port>.json, bukan di URL
            _pu = urllib.parse.urlparse(signup_proxy if "//" in signup_proxy
                                        else "http://" + signup_proxy)
            if _pu.hostname in ("127.0.0.1", "localhost", "::1") and _pu.port:
                _m_cc = re.search(r"country-([a-z]{2})",
                                  (HERE / "data" / f"upstream-{_pu.port}.json").read_text())
        except Exception:
            pass
    tz = (os.environ.get("TZ_OVERRIDE") or os.environ.get("PROXY_TZ")
          or _tz_negara.get(_m_cc.group(1) if _m_cc else "")
          or ("Europe/Berlin" if signup_proxy else "Asia/Jakarta"))
    # [fix 2026-10-22 bedah bot-leak] Accept-Language format ASLI Chrome
    # (dulu 2-entri 'de-DE,de;q=0.9' rakitan sendiri = format yang TIDAK PERNAH
    # dikirim Chrome asli; asli 4-entri utk non-EN: de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7).
    # user_agent persona DIBUKA (dulu klaim 148-151 vs TLS/Accept asli 155 =
    # kontradiksi versi) — native = konsisten.
    _loc = FP["locale"]
    _base = _loc.split("-")[0]
    _al = (f"{_loc},{_base};q=0.9" if _base == "en"
           else f"{_loc},{_base};q=0.9,en-US;q=0.8,en;q=0.7")
    # [fix 2026-11-05 kandidat #1 vonis 10003] JANGAN pakai opsi locale
    # new_context: Emulation.setLocaleOverride memaksa navigator.languages
    # 1-entri + Accept-Language 'en-US' polos (MENANG atas extra_http_headers)
    # — blob languages 1-entri vs emas 2-entri ['en-US','en'] + header beda
    # dari Chrome asli 'en-US,en;q=0.9' = kontradiksi server-visible tiap
    # request. Bukti A/B 5 varian (qoder/riset/alat/uji-bahasa.py): tanpa
    # locale (LANG env / --lang / C.UTF-8 apa pun) = bentuk emas persis.
    _kw_ctx = dict(timezone_id=tz,
                   viewport=FP["viewport"],
                   extra_http_headers={"accept-language": _al},
                   ignore_https_errors=bool(signup_proxy))
    if os.environ.get("CHROME_DISK_CACHE", "0") == "1":
        # [solusi hemat-byte 2026-10-14, vonis debug] Chrome MENTAH + attach
        # Playwright via CDP (pola dd_panen/cdp_gate). Bukti: Playwright
        # launch_persistent_context MENGABAIKAN cache disk (3 eksperimen: profil
        # sama 3,28 vs 3,33MB; salinan 3,23MB; aset cacheable tetap unduh ulang)
        # sementara Chrome mentah MEMAKAINYA (dd_panen 38->5,4MB). TLS Chrome
        # asli utuh (anti-10003), tanpa MITM. Proxy auth via px_forward
        # (Chrome tak bisa bawa kredensial proxy). Saklar: 0 = perilaku lama.
        import shutil as _sh, tempfile as _tf, socket as _sock
        import subprocess as _sp, json as _js, urllib.request as _ur
        import px_forward as _pxf
        (HERE / "profil").mkdir(parents=True, exist_ok=True)
        profil_tmp = Path(_tf.mkdtemp(dir=str(HERE / "profil")))
        # [fix 2026-10-22] HANYA HTTP Cache yang dibagikan. Code Cache (hasil
        # kompilasi V8) yang terpotong = RACUN: JS challenge gagal jalan diam2 ->
        # frame tak pernah terpasang (43/51 attempt 'frame tak lengkap' walau
        # html-nya 200) -> submit tak terkirim + SPA macet -> sesi ditandai.
        for _sub in ("Cache",):
            _s = HERE / "proton-warm" / "Default" / _sub
            if _s.is_dir():
                try:
                    _sh.copytree(_s, profil_tmp / "Default" / _sub,
                                 dirs_exist_ok=True)
                except Exception:
                    pass
        _fwd = None
        _proxy_arg = ""
        if signup_proxy:
            pr2 = urllib.parse.urlparse(signup_proxy if "//" in signup_proxy
                                        else "http://" + signup_proxy)
            _fwd = _pxf.Forwarder(upstream={
                "server": f"http://{pr2.hostname}:{pr2.port or 80}",
                "username": pr2.username or "", "password": pr2.password or ""}).start()
            _proxy_arg = f"--proxy-server=http://{_fwd.local}"
        with _sock.socket() as _sk:
            _sk.bind(("127.0.0.1", 0)); _cdp_port = _sk.getsockname()[1]
        _env = dict(os.environ)
        _env["TZ"] = {"Europe/London": "Europe/London", "Europe/Berlin": "Europe/Berlin",
                      "Asia/Tokyo": "Asia/Tokyo", "Asia/Jakarta": "Asia/Jakarta"}.get(tz, tz)
        # [fix 2026-10-22 bedah bot-leak] Accept-Language dibentuk Chrome dari
        # ENV LANG (bukan flag --lang) — dulu semua attempt mengirim
        # 'en-US,en;q=0.9' (LANG=C.UTF-8 VPS) sementara persona/x-pm-locale
        # mengaku de-DE = kontradiksi per-request. Kini LANG = locale persona,
        # Chrome asli membentuk sendiri format 4-entri miliknya.
        _env["LANG"] = str(FP.get("locale", "en-US")).replace("-", "_") + ".UTF-8"
        chrome_proc = _sp.Popen(
            # [fix 2026-10-14] flag HARUS dari bendera_chrome (TERBUKTI DevTools
            # hidup, dipakai dd_panen tiap hari) — daftar fp_profile.chrome_args
            # terbukti MEMATIKAN endpoint DevTools (uji: CDP tak menjawab vs OK).
            [*_perakit_flag_profil_dd(profil_tmp, _cdp_port, _proxy_arg, FP)],
            env=_env, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        _ws = None
        for _ in range(40):
            try:
                _info = _js.loads(_ur.urlopen(
                    f"http://127.0.0.1:{_cdp_port}/json/version", timeout=2).read())
                _ws = _info["webSocketDebuggerUrl"]; break
            except Exception:
                time.sleep(0.5)
        if not _ws:
            raise RuntimeError("chrome mentah tidak menjawab CDP")
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{_cdp_port}")
        ctx = browser.contexts[0]
    else:
        browser = p.chromium.launch(**launch_kw)
        ctx = browser.new_context(**_kw_ctx)
    note(f"proxy: {'aktif (' + tz + ')' if signup_proxy else 'langsung (Asia/Jakarta)'}"
         f" | laptop={FP['os']}:{FP['seed']} {FP['gl_renderer'][:36]} vp={FP['viewport']['width']}x{FP['viewport']['height']}")
    page = ctx.new_page()
    # [hemat-byte 2026-10-14] CACHE ASET di level route: aset statis (js/css/
    # font/gambar) dilayani dari disk lokal — TIDAK lewat proxy, TANPA menyentuh
    # TLS (Chrome tetap TLS asli = fix 10003 aman). Byte proxy turun drastis
    # setelah warmup. Saklar: ASSET_CACHE_PROTON=0 -> mati (undo seketika).
    # [fix sukses 2026-10-14] CACHE ASET route: DEFAULT MATI. Bukti A/B 5 slot
    # paralel: cache ON = 1/5 (hv=None 3x); cache OFF = 5/5 SEMPURNA (4mnt 1dtk).
    # Serve-from-disk = respons 0-detik + header sintetis = pola yang dibaca JS
    # risiko Proton -> menu verifikasi ditahan (hv=None). Konsekuensi: byte proxy
    # tahap proton naik — success rate lebih penting. Nyalakan manual:
    # ASSET_CACHE_PROTON=1 (hanya utk eksperimen cache 'pintar' di masa depan).
    if os.environ.get("ASSET_CACHE_PROTON", "0") == "1":
        _CACHE_RUTE = HERE / "cache" / "route"

        def _penjaga_cache(route):
            try:
                req = route.request
                u = req.url.split('#')[0]
                # [fix hemat-byte 2026-10-14] AKAR: r.body adalah METODE playwright
                # (APIResponse.body()) — dipakai sbg bytes -> TypeError ditelan except
                # -> fulfill gagal -> continue_() -> cache 0 file, semua byte lewat
                # proxy. API/XHR + Authorization TIDAK pernah di-cache (pola
                # asset_cache.py yang terbukti aman utk token/OTP).
                if (req.method != "GET" or "/api/" in u
                        or "authorization" in {k.lower() for k in req.headers}
                        or not re.search(
                            r'\.(?:js|css|woff2?|ttf|otf|png|jpe?g|svg|ico|gif|webp)(?:\?|$)', u)):
                    return route.continue_()
                import hashlib as _hl
                import random as _rd
                # [resep capt-v2 §6.1] kunci cache TANPA query — bukti: 97 duplikat
                # font md5 sama 352KB (URL beda query per attempt) = 97x fetch
                # percuma + paparan hang. Aset ber-ekstensi = konten terikat nama.
                k = _hl.sha1(u.split("?")[0].encode()).hexdigest()
                fb = _CACHE_RUTE / (k[:2] + "-" + k[2:])
                fm = _CACHE_RUTE / (k[:2] + "-" + k[2:] + ".hdr")
                _potong = ("content-encoding", "content-length", "transfer-encoding")

                def _hdr_json(h):
                    # [cache pintar 2026-10-14] respons HARUS tampak jaringan asli:
                    # header LENGKAP (date/etag/cache-control/dsb) — bukan cuma
                    # content-type (itu pola sintetis yang dibaca JS risiko Proton
                    # = biang hv=None; A/B: cache polos 1/5 vs tanpa cache 5/5).
                    try:
                        import json as _js
                        d = _js.loads(fm.read_text() or "{}")
                        if isinstance(d, dict) and d.get("headers"):
                            return int(d.get("status") or 200), \
                                {k2: v for k2, v in d["headers"].items()
                                 if k2.lower() not in _potong}
                    except Exception:
                        pass
                    try:      # file .hdr lama (content-type polos)
                        ct = fm.read_text()[:100] or "application/octet-stream"
                    except Exception:
                        ct = "application/octet-stream"
                    return 200, {"content-type": ct}

                if fb.exists():
                    st, hd = _hdr_json(fm)
                    etag = next((v for k2, v in hd.items()
                                 if k2.lower() == "etag"), "")
                    inm = (req.headers or {}).get("if-none-match") \
                        or (req.headers or {}).get("If-None-Match") or ""
                    time.sleep(_rd.uniform(0.05, 0.30))   # latensi realistis
                    if etag and inm and etag in inm:
                        return route.fulfill(status=304, headers={
                            k2: v for k2, v in hd.items()
                            if k2.lower() in ("etag", "cache-control", "date", "expires")})
                    return route.fulfill(status=st, headers=hd,
                                         body=fb.read_bytes())
                # [resep capt-v2 §6.1] fetch TANPA timeout = menahan event load
                # iframe widget s/d 30dtk -> spinner 'Loading' abadi -> 'frame
                # widget tak ditemukan' / hv=None. Timeout pendek -> except ->
                # continue_() = muat biasa, load tuntas. INI biang utama.
                r = route.fetch(timeout=8000)
                isi = r.body()                      # <== PERBAIKAN UTAMA: panggil metode
                hd = {k2: v for k2, v in (r.headers or {}).items()
                      if k2.lower() not in _potong}
                try:
                    import json as _js
                    _CACHE_RUTE.mkdir(parents=True, exist_ok=True)
                    fb.write_bytes(isi)
                    fm.write_text(_js.dumps({"status": r.status, "headers": hd}))
                except Exception:
                    pass
                return route.fulfill(status=r.status, headers=hd, body=isi)
            except Exception:
                try:
                    return route.continue_()
                except Exception:
                    return None
        try:
            page.route("**/*", _penjaga_cache)
        except Exception:
            pass
    fp_profile.apply_ua_cdp(ctx, page, FP)  # UA + userAgentData + sec-ch-ua konsisten (CDP metadata)
    if os.environ.get("FP_DEBUG") == "1":
        page.add_init_script("window.__FP_DEBUG = 1;")
    # [fix 2026-10-22 uji A/B atas arahan user] FP_INIT=0 = Chrome POLOS tanpa
    # injeksi persona (persis laptop user) — utk memastikan apakah injeksi kita
    # yang memicu 'bunuh diri sesi' pasca-keys/setup. Default tetap 1.
    if os.environ.get("FP_INIT", "1") == "1":
        page.add_init_script(fp_profile.init_script(FP))  # persona device lengkap (platform/plugins/media/fonts)
    # [fix 2026-10-22 bedah bot-leak] window.__msg = GLOBAL BUATAN yang bisa
    # dipindai skrip halaman ('__msg' in window -> otomasi terdeteksi; browser
    # asli: false). Kini lewat console bridge: listener menutup rapat tanpa
    # menyentuh window, Python mengumpulkan dari page.on('console') ke msg_kit.
    # [fix klaster-2 2026-10-23] hash canvas2d & WebGL IDENTIK di SEMUA attempt
    # (software stack sama) = 'satu mesin' lintas attempt (kunci linkage sisa).
    # [upgrade 2026-11-08 — BEDAH-RISK-CONTROL-BARU #1] noise kini menutup
    # SEMUA kanal baca render: toDataURL + toBlob (salin + usik), getImageData,
    # WebGL readPixels (1/2). Dulu cuma toDataURL + posisi piksel dihitung dari
    # kanvas PERTAMA (kanvas beda ukuran -> noise jatuh di luar batas -> NO-OP
    # -> hash tetap konstan). Kini posisi deterministik per (seed sesi, dimensi,
    # kanal): konsisten dlm 1 sesi (blob vs resubmit identik), beda antar akun.
    # Render asli TIDAK diubah utk kanal salin; kanal baca hanya 2 piksel LSB
    # (tak terlihat mata). Bukan noise sintetis fp_forge (toxin era 2028).
    # toString/name/length tiap wrapper = native (audit T2). Saklar: CANVAS_NOISE=0.
    if os.environ.get("CANVAS_NOISE", "1") == "1":
        page.add_init_script("""
            (() => {
              const _ts = Function.prototype.toString;
              const _SRC = new WeakMap();
              Function.prototype.toString = function () {
                if (_SRC.has(this)) return _SRC.get(this);
                return _ts.call(this);
              };
              const _native = (fn, nama) => {
                _SRC.set(fn, 'function ' + nama + '() { [native code] }');
                try { Object.defineProperty(fn, 'name', {value: nama, configurable: true}); } catch (e) {}
                try { Object.defineProperty(fn, 'length', {value: 0, configurable: true}); } catch (e) {}
                return fn;
              };
              _native(Function.prototype.toString, 'toString');
              const seed = Math.floor(Math.random() * 1e9);
              const _pos = (w, h, kanal, n) => {
                let x = (seed ^ Math.imul(w, 374761393) ^ Math.imul(h, 668265263)
                         ^ Math.imul(kanal, 2246822519) ^ Math.imul(n, 3266489917)) >>> 0;
                x = Math.imul(x ^ (x >>> 13), 1274126177) >>> 0;
                const y = Math.imul(x ^ (x >>> 7), 2654435761) >>> 0;
                return [x % Math.max(1, w), y % Math.max(1, h)];
              };
              const _usik = (data, w, h, kanal) => {
                for (let n = 1; n <= 2; n++) {
                  const p = _pos(w, h, kanal, n);
                  const i = (p[1] * w + p[0]) * 4;
                  if (i >= 0 && i + 2 < data.length) data[i] = (data[i] ^ 1) & 255;
                }
              };
              const gid = CanvasRenderingContext2D.prototype.getImageData;
              const _salinNoise = (src, kanal) => {
                const c = document.createElement('canvas');
                c.width = src.width; c.height = src.height;
                const x = c.getContext('2d');
                x.drawImage(src, 0, 0);
                const d = gid.call(x, 0, 0, c.width, c.height);
                _usik(d.data, c.width, c.height, kanal);
                x.putImageData(d, 0, 0);
                return c;
              };
              const td = HTMLCanvasElement.prototype.toDataURL;
              HTMLCanvasElement.prototype.toDataURL = _native(function (...a) {
                try { return td.call(_salinNoise(this, 11), ...a); }
                catch (e) { return td.call(this, ...a); }
              }, 'toDataURL');
              const tb = HTMLCanvasElement.prototype.toBlob;
              if (tb) HTMLCanvasElement.prototype.toBlob = _native(function (...a) {
                try { return tb.call(_salinNoise(this, 29), ...a); }
                catch (e) { return tb.call(this, ...a); }
              }, 'toBlob');
              CanvasRenderingContext2D.prototype.getImageData = _native(function (sx, sy, sw, sh) {
                const d = gid.call(this, sx, sy, sw, sh);
                try { _usik(d.data, sw, sh, 47); } catch (e) {}
                return d;
              }, 'getImageData');
              for (const K of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
                if (!K) continue;
                const rp = K.prototype.readPixels;
                K.prototype.readPixels = _native(function (x, y, w, h, f, t, buf) {
                  const r = rp.call(this, x, y, w, h, f, t, buf);
                  try { if (buf instanceof Uint8Array) _usik(buf, w, h, 83); } catch (e) {}
                  return r;
                }, 'readPixels');
              }
            })();
        """)
    page.add_init_script(
        "if (window.top === window) { window.addEventListener('message', e => { try { "
        "console.debug('__MSG__' + JSON.stringify({t:(e.data&&e.data.type)||'?', d:e.data})); "
        "} catch(x){} }, true); }")
    # [bedah bunuh-diri-sesi 2026-10-23] dump ringkas objek fingerprint SEBELUM
    # di-encrypt kit (JSON.stringify hook terpisah — fp_forge TIDAK disentuh).
    # Bukti struktur emas: blob b64 3520 (2640 B, 47 field) vs kita 4120 —
    # pemicu revokasi = kandidat field mustahil/kelebihan di blob.
    page.add_init_script("""
        (() => {
          const _sj = JSON.stringify;
          // [fix tamper-T1 2026-11-06 — audit fingerprint Fase 1] hook HARUS
          // tampil NATIVE: dulu toString() membocorkan source penuh (berisi
          // literal '__BLOB__'), length 1 (asli 3), name '' (asli 'stringify')
          // = sinyal tamper terkuat, dan kit risiko SENDIRI memanggil
          // JSON.stringify utk enkripsi blob -> tiap signup melewati fungsi
          // yang ketahuan palsu. Kini: rantai Function#toString berlapis
          // (delegasi ke patch fp_profile bila ada) + name/length native.
          const _ts = Function.prototype.toString;
          const _SRC = new WeakMap();
          Function.prototype.toString = function () {
            if (_SRC.has(this)) return _SRC.get(this);
            return _ts.call(this);
          };
          _SRC.set(Function.prototype.toString, 'function toString() { [native code] }');
          const _wrap = function (v) {
            try {
              if (v && typeof v === 'object' && 'visitorId' in v && 'fonts' in v && 'platform' in v) {
                const s = {};
                for (const k of Object.keys(v)) {
                  const val = v[k];
                  if (Array.isArray(val)) {
                    s[k] = 'arr(' + val.length + ')=' + _sj(val).slice(0, 220);
                  } else if (val && typeof val === 'object') {
                    s[k] = 'obj=' + _sj(val).slice(0, 220);
                  } else {
                    s[k] = String(val).slice(0, 90);
                  }
                }
                console.debug('__BLOB__' + _sj(s));
              }
            } catch (e) {}
            return _sj.apply(JSON, arguments);
          };
          _SRC.set(_wrap, 'function stringify() { [native code] }');
          try { Object.defineProperty(_wrap, 'name', {value: 'stringify', configurable: true}); } catch (e) {}
          try { Object.defineProperty(_wrap, 'length', {value: 3, configurable: true}); } catch (e) {}
          JSON.stringify = _wrap;
        })();
    """)
    msg_kit = []

    def _kumpul_console(m):
        try:
            t = m.text
            if t.startswith("__MSG__") and len(msg_kit) < 300:
                # [fix 2026-10-22] '__MSG__' = 7 karakter — dulu dipotong [8:]
                # (off-by-one) -> json.loads gagal senyap -> msg_kit selalu kosong
                msg_kit.append(json.loads(t[len("__MSG__"):]))
            elif t.startswith("__BLOB__"):
                log.setdefault("blob_dump", []).append(json.loads(t[len("__BLOB__"):]))
        except Exception:
            pass
    page.on("console", _kumpul_console)

    def on_response(r):
        u = r.url
        # [riset aset 2026-10-16] jejak kelengkapan aset kunci (wasm SRP, font
        # Inter, chunk kt-certificates) — resep verifikasi: wajib 200; kalau
        # hilang/gagal termuat = kandidat sinyal kurang vs browser asli.
        if u.endswith((".wasm", ".ttf", ".woff2")) or "kt-certificates" in u:
            if len(log.setdefault("aset", [])) < 40:
                log["aset"].append({"u": u[:120], "s": r.status})
        # [debug-hv] RESPONS UTUH (status+headers+body) ke file mentah — terutama
        # respons setelah submit saat hv=None. Hanya saat DEBUG_HV=1.
        if DEBUG_HV and ("/api/" in u or "challenge" in u
                         or r.request.resource_type == "document"):
            try:
                body = ""
                try:
                    body = r.text()
                except Exception:
                    try:
                        body = (r.body() or b"").decode("utf-8", "replace")
                    except Exception:
                        pass
                nama = re.sub(r"[^a-zA-Z0-9]+", "_", u.split("//", 1)[-1])[:70]
                fn = DUMP_HV / f"{_ID_HV}-net-{int(time.time() * 1000) % 1000000}-{nama}.txt"
                fn.write_text(
                    f"{r.request.method} {u}\nSTATUS {r.status}\n"
                    + "".join(f"{k}: {v}\n" for k, v in (r.headers or {}).items())
                    + "\n--- BODY ---\n" + body[:20000])
                note(f"dump-hv-net: {fn.name} status={r.status} body={len(body)}b")
            except Exception as e:
                note(f"dump-hv-net gagal: {e}")
        if "/api/" in u or "challenge" in u:
            entry = {"t": int(time.time()), "s": r.status,
                     "m": r.request.method, "u": u[:130]}
            try:  # [bedah bunuh-diri-sesi 2026-10-23] set-cookie = sumber cookie
                # AUTH-/REFRESH- (bukti emas: /core/v4/auth men-set keduanya).
                # Kalau hilang/rusak di jalan -> browser pakai cookie lama -> 401.
                if any(x in u for x in ("/auth", "/users", "sessions", "keys/")):
                    sc = []
                    for h in (r.headers_array() or []):
                        nm = h.get("name") if isinstance(h, dict) else h[0]
                        vl = h.get("value") if isinstance(h, dict) else h[1]
                        if str(nm).lower() == "set-cookie":
                            _sv = str(vl)
                            # [diag sesi-mati 2026-11-05] AUTH-/REFRESH- UTUH
                            # (dulu [:130]) — bahan A-B byte vs cookie request
                            # yang kena 401 (korupsi wire vs token dicabut).
                            sc.append(_sv[:700] if _sv.startswith(("AUTH-", "REFRESH-"))
                                      else _sv[:130])
                    if sc:
                        entry["setcookie"] = sc
            except Exception:
                pass
            if r.status in (401, 403):  # [diag sesi-mati 2026-11-05] simpan
                # header request UTUH (cookie+authorization, tanpa potong) +
                # body vonis 401/403 utk bedah 'token salah di wire' vs
                # 'token dicabut server' (bandingkan byte vs Set-Cookie /auth).
                try:
                    rqh = r.request.all_headers()
                    entry["req_hdr"] = {
                        "cookie": str(rqh.get("cookie", ""))[:2000],
                        "authorization": str(rqh.get("authorization", ""))[:700],
                        "uid": str(rqh.get("x-pm-uid", ""))[:64]}
                except Exception:
                    pass
                try:
                    entry["body"] = r.text()[:400]
                except Exception:
                    try:
                        entry["body"] = (r.body() or b"").decode("utf-8", "replace")[:400]
                    except Exception:
                        pass
            # [diagnosa 2026-10-13] captcha validate/finalize: body = vonis PoW
            # (manual: 'OK'). Kalau bukan OK = proof busuk -> resubmit 2028.
            if "captcha/v1/api/validate" in u or "captcha/v1/api/finalize" in u:
                try:
                    entry["body"] = r.text()[:200]
                except Exception:
                    try:  # fallback: r.text() kadang melempar utk respons iframe
                        entry["body"] = (r.body() or b"").decode("utf-8", "replace")[:200]
                    except Exception:
                        pass
                note(f"CAPTCHA-STEP {r.status} {u.split('/api/')[-1][:60]} "
                     f"body={entry.get('body','?')[:80]}")
            if "/users/code" in u:
                try:
                    entry["body"] = r.text()[:300]
                except Exception:
                    pass
            if "/settings/email" in u or "/verify/" in u:
                try:  # [diagnosa 2026-11-05 vonis akar 'link tak datang ke alias']
                    # body request app vs balasan server utk A/B vs HAR emas
                    # 5 Nov idx 146-156 (PUT +proof harus 200 -> verify/send auto
                    # 200). Tanpa ini 422 misterius tak teridentifikasi kodenya.
                    pd = r.request.post_data
                    if pd:
                        entry["req_body"] = str(pd)[:600]
                    try:
                        entry["body"] = r.text()[:300]
                    except Exception:
                        entry["body"] = (r.body() or b"").decode(
                            "utf-8", "replace")[:300]
                    note(f"RECOVERY-NET {r.request.method} "
                         f"{u.split('proton.me')[-1][:60]} st={r.status} "
                         f"body={str(entry.get('body', ''))[:130]}")
                except Exception:
                    pass
            if "/api/core/v4/users" in u:
                try:  # body = sumber kebenaran (9001/2028/409/200) — semua status
                    j = r.json()
                    det = j.get("Details") or {}
                    if "HumanVerificationMethods" in det:  # hanya saat ada (422) — jangan timpa oleh 200 sukses
                        log["hv_methods"] = det["HumanVerificationMethods"]
                        log["hv_token"] = det.get("HumanVerificationToken", "")
                        note(f"HV methods dari server: {log['hv_methods']}")
                    entry["body"] = json.dumps(j)[:300]
                except Exception:
                    pass
            if u.endswith("/core/v4/auth") or u.endswith("/auth/refresh"):
                try:  # [bedah bunuh-diri-sesi] body /auth = ExpiresIn/Scope token
                    # sesi baru — bandingkan vs emas (ExpiresIn 86400 scope penuh)
                    j = r.json()
                    safe = {k: (str(v)[:12] + "…" if k in ("AccessToken", "RefreshToken") else v)
                            for k, v in j.items() if k != "ServerProof"}
                    entry["body"] = json.dumps(safe)[:400]
                except Exception:
                    pass
            log["net"].append(entry)
            if "/api/core/v4/users" in u or "/auth/v4/" in u:
                note(f"NET {r.status} {u[:110]}" + (f" | {entry['body'][:150]}" if entry.get("body") else ""))
        # [diag sesi-mati 2026-11-05] snapshot cookie jar di momen kunci
        try:
            if ("/api/core/v4/users" in u and r.status == 200
                    and r.request.method == "POST" and not log.get("snap_users")):
                log["snap_users"] = True
                _snap_cookie(page, "setelah-users-200")
            if (u.split("?")[0].rstrip("/").endswith("/api/core/v4/auth")
                    and r.status == 200 and not log.get("snap_auth")):
                log["snap_auth"] = True
                _snap_cookie(page, "setelah-auth-200")
            if r.status in (401, 403) and "/api/" in u:
                if not log.get("snap_4xx"):
                    log["snap_4xx"] = True
                    _snap_cookie(page, f"4xx-pertama-{r.status}")
                if "/api/core/v4/" in u and not log.get("snap_4xx_core"):
                    log["snap_4xx_core"] = True
                    _snap_cookie(page, f"4xx-core-pertama-{r.status}")
        except Exception:
            pass
    page.on("response", on_response)
    def on_request(r):
        u = r.url
        if "reports/sentry" in u and r.method == "POST":
            # [diag sesi-mati 2026-11-05 — hipotesis user] envelope SENTRY ~12KB
            # (laporan crash JS) tak pernah ada di HAR emas; TANGKAP ISI PENUH:
            # exception + stack trace -> apakah di area hook/script penyuntik
            # kita, kode aplikasi Proton, atau resource gagal muat?
            try:
                pd = str(r.post_data or "")
                fn = RESULT_F.parent / (
                    f"crash-{int(time.time())}-{len(log.get('sentry_dump', []))}.json")
                fn.write_text(pd)
                log.setdefault("sentry_dump", []).append({
                    "t": int(time.time()), "u": u[:200], "blen": len(pd),
                    "file": fn.name, "envelope": pd[:20000]})
                note(f"SENTRY-ENVELOPE {len(pd)}b -> {fn.name}")
            except Exception as e2s:
                note(f"SENTRY tangkap gagal: {e2s}")
        # [diagnosa 2026-10-13] GET validate membawa header pcaptcha (posisi drop
        # + jawaban PoW 15-zero) — tangkap utk buktikan answers terisi/hampa
        if "captcha/v1/api/validate" in u or "captcha/v1/api/finalize" in u:
            hd = r.headers or {}
            pc = ""
            for k2, v2 in hd.items():
                if k2.lower() == "pcaptcha":
                    pc = str(v2)
            e2 = {"t": int(time.time()), "s": None, "m": r.method, "u": u[:300],
                  "pcaptcha": pc}  # UTUH tanpa potong — clientData+timing wajib utk bedah
            log["net"].append(e2)
            note(f"CAPTCHA-REQ {r.method} {u.split('/api/')[-1][:60]} "
                 f"pcaptcha={'ADA ' + pc[:120] if pc else 'TIDAK ADA'}")
        if "/api/" in u:
            try:  # [bedah bunuh-diri-sesi 2026-10-23] sidik jari AUTH per request:
                # pembeda 'token dicabut server' vs 'cookie basi/tak terkirim' =
                # nilai AUTH-/REFRESH- cookie yang DIBAWA request saat 401 pertama
                # (bukti header emas: app account memakai cookie AUTH-<uid>, BUKAN
                # Authorization; refresh body-kosong = desain, dibantu cookie
                # REFRESH-<uid> ber-path /api/auth/refresh).
                # [fix] r.headers MENAHAN header cookie (kebijakan Playwright) ->
                # ck selalu kosong; all_headers() = lengkap termasuk cookie.
                try:
                    hd = r.all_headers()
                except Exception:
                    hd = r.headers or {}
                ck = hd.get("cookie") or ""
                kline = []
                for part in ck.split(";"):
                    nm, _, val = part.partition("=")
                    nm = nm.strip()
                    if nm.startswith(("AUTH-", "REFRESH-")):
                        # [diag sesi-mati 2026-11-05] nama+uid UTUH + nilai token
                        # UTUH (tugas user: 'pastikan tidak terpotong saat
                        # kegagalan') — dulu nm[:14] memotong uid & val[:22] tak
                        # cukup utk A-B byte 'token salah di wire'.
                        kline.append(f"{nm}={val[:12]}…{val[-4:]}" if len(val) > 18
                                     else f"{nm}={val}")
                log.setdefault("hdr", []).append({
                    "t": int(time.time()),
                    "m": r.method, "u": u.split("proton.me")[-1][:70],
                    "autz": (hd.get("authorization") or "")[:600],
                    "uid": (hd.get("x-pm-uid") or "")[:64],
                    "ck": "|".join(kline)[:1200],
                })
            except Exception:
                pass
        if r.method == "POST" and "/api/" in u:
            e = {"t": int(time.time()), "s": None, "m": "POST", "u": u[:130]}
            try:  # sidik jari body + header verifikasi: POST-1 vs resubmit
                pd = r.post_data
                if pd:
                    e["blen"] = len(pd)
                    e["bhash"] = hashlib.md5(pd.encode() if isinstance(pd, str) else pd).hexdigest()[:12]
                    # [riset blob 2026-10-16] simpan body UTUH pembawa blob
                    # (Payload ter-enkripsi) utk bedah field-per-field vs blob
                    # user ter-dekripsi (/tmp/riset/user-blobs-plain.json)
                    if ("sessions/payload" in u
                            or ("/api/core/v4/users" in u and "available" not in u)):
                        e["req_body"] = pd if len(pd) < 9000 else pd[:9000]
                hd = r.headers or {}
                e["hvhdr"] = "|".join(f"{k}={str(v)[:400]}" for k, v in hd.items()
                                      if "verification" in k.lower() or "token" in k.lower())
                # [diagnosa 2026-10-13] header pcaptcha (posisi drop + jawaban PoW)
                # dikirim JS captcha ke validate — buktikan answers terisi/hampa
                if "pcaptcha" in (k.lower() for k in hd):
                    e["pcaptcha"] = str(hd.get("pcaptcha") or hd.get("Pcaptcha") or "")[:300]
                if e["hvhdr"]:
                    note(f"HV-HDR {u[:60]} {e['hvhdr'][:150]}")
            except Exception:
                pass
            log["net"].append(e)
            note(f"NET POST {u[:110]}" + (f" #{e['bhash']}({e['blen']})" if "bhash" in e else ""))
    page.on("request", on_request)

    def on_gagal(r):
        """[fix 2026-10-16] request GAGAL di transport (tunnel putus/timeout)
        dulu tak tercatat sama sekali -> kasus 'validate menggantung' tak bisa
        dibedakan 'server lambat' vs 'koneksi diputus' (kandidat: batas 4MB
        px_forward per koneksi). Catat apa adanya."""
        try:
            log["net"].append({"t": int(time.time()), "s": "GAGAL", "m": r.method,
                               "u": (r.url or "")[:130],
                               "err": str(r.failure or "")[:120]})
            note(f"GAGAL-JARINGAN {r.method} {str(r.url)[:80]} :: {str(r.failure or '')[:60]}")
        except Exception:
            pass
    page.on("requestfailed", on_gagal)

    note("muat halaman signup")
    page.goto(TARGET, wait_until="domcontentloaded", timeout=90000)
    jeda_baca(page)

    # ---- GATE: frame challenge (form) harus benar-benar ADA ----
    # [fix 2026-10-22 duplikat mitm] sinyal 'siap' yang SEBENARNYA = frame
    # Name=email (form username) & Name=unauth ADA di daftar frame — itu yang
    # dibutuhkan submit. Dulu gate menghitung pesan kit; saat boot lambat
    # (>30 dtk, koneksi lambat) gate menyerah -> fallback kolom cermin yang
    # TIDAK BISA submit (POST /users tak pernah terkirim: 11/11 attempt satu
    # run). Kini tunggu frame NYATA maks 60 dtk. (Bandingkan HAR: kedua frame
    # selalu ada sebelum user mengetik.)
    ready = False
    _reload = 0
    t0c = time.time()
    while time.time() - t0c < 120:
        time.sleep(1)
        _fr = [f.url for f in page.frames]
        if any("Name=email" in u for u in _fr) and any("Name=unauth" in u for u in _fr):
            ready = True
            note(f"challenge SIAP (frame email+unauth ada, {time.time() - t0c:.0f} dtk)")
            break
        # [fix 2026-10-22] halaman punya 2 VARIAN: form dlm iframe challenge ATAU
        # form polos #username/#password langsung (bukti screenshot: 43 attempt
        # menunggu iframe yang tak pernah ada padahal form polos sudah tampil).
        # Form polos = siap pakai.
        try:
            if page.locator("#username").count():
                ready = True
                note(f"challenge SIAP (varian form polos #username, {time.time() - t0c:.0f} dtk)")
                break
        except Exception:
            pass
        # [fix 2026-10-22] boot kadang sangat lambat (jaringan lelet) — manusia
        # akan MENYEGARKAN halaman yang macet. Reload sekali di detik 50.
        if _reload < 1 and time.time() - t0c > 50:
            _reload = 1
            note("frame belum ada 50 dtk — segarkan halaman (spt manusia)")
            try:
                page.reload(wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
    if not ready:
        note(f"frame challenge tak lengkap setelah 120 dtk: "
             f"{[u[-55:] for u in [f.url for f in page.frames]]}")

    # ---- temukan frame Name=email (form username asli) ----
    # [fix 2026-10-23 gai7795ius/seiiusinus1074] varian 'form polos' sering =
    # iframe challenge TELAT termuat (gate mendeteksi #username top-page lebih
    # dulu). Input top-page terbukti BISA mati (mirror tak ber-kabel: ketik 3x
    # + click nyata tak nempel — screenshot 'This field is required'). TUNGGU
    # iframe email + inputnya (maks 25 dtk) — jalur yang terbukti — sebelum
    # jatuh ke form polos.
    email_frame = None
    uname_in = None
    inv = []
    t0f = time.time()
    while time.time() - t0f < 25:
        email_frame = next((f for f in page.frames if "Name=email" in f.url), None)
        if email_frame is not None:
            try:
                inv = email_frame.evaluate(INVENTORY)
            except Exception:
                inv = []
            uname_in = next((x for x in inv if x["tag"] == "input"), None)
            if uname_in:
                break
        time.sleep(1)
    if uname_in:
        note(f"inventory frame email: {json.dumps(inv)[:400]}")
    else:
        note("iframe email tak siap 25 dtk — fallback form polos")

    # [domain 2026-10-20 — arahan user] pilih domain di dropdown #select-domain
    # sesuai MAIL_DOMAIN (bukti HAR idx 84/122/148: body /users dibangun JS form
    # dari dropdown ini, jadi cukup pilih -> payload otomatis benar). Cek teks
    # tombol setelah pilih = jaminan domain benar2 berubah sebelum submit.
    # [fix perilaku 2026-11-06 — bedah HAR user vs pabrik] JANGAN sentuh dropdown
    # bila sudah menampilkan MAIL_DOMAIN (default @proton.me): pengguna ASLI
    # (HAR user 5 Nov + emas 20 Okt) TIDAK PERNAH menyentuh tombol ini — cukup
    # ketik username. Interaksi hanya bila domain TIDAK cocok (defensif).
    # [fix 2026-11-08 BEDAH-URUTAN-REQUEST-EMAS] DIPINDAH ke SEBELUM ketik:
    # emas E20 selalu 'pilih domain dulu baru ketik' (3 cek available SEMUA di
    # domain final) sedangkan kita dulu 'ketik dulu ganti domain kemudian' ->
    # app cek ulang nama SAMA di 2 domain = PEMBERISH satu-satunya yang bersih
    # (3/3 MATI vs 0/2 emas vs 0/1 SELAMAT). Urutan emas = tanpa cek ganda.
    try:
        ctx_dom = email_frame if email_frame is not None else page
        tombol_dom = ctx_dom.locator("#select-domain")
        teks_dom = ""
        try:
            teks_dom = tombol_dom.inner_text(timeout=2000).strip()
        except Exception:
            pass
        if teks_dom.lstrip("@") == MAIL_DOMAIN.lstrip("@"):
            note(f"domain dropdown: default sudah '{MAIL_DOMAIN}' — TIDAK disentuh "
                 "(perilaku emas: user cukup ketik username)")
        else:
            tombol_dom.scroll_into_view_if_needed()
            # [fix 2026-11-08 BEDAH-SKOR-RISKCONTROL §6] timing klik dropdown
            # 15-35 ms = tell di blob ('MUSTAHIL manusia' — diakui komentar di
            # atas); emas E20 (20 Okt, HIDUP, blob 3928B) = ~103 ms. Humanize:
            # hover-pause + delay mousedown->mouseup kelas emas.
            page.wait_for_timeout(130)
            tombol_dom.click(timeout=4000, delay=95)
            page.wait_for_timeout(700)
            dipilih = False
            for konteks in (([email_frame] if email_frame is not None else []) + [page]):
                try:
                    opsi = konteks.locator(f"text={MAIL_DOMAIN}").first
                    if opsi.count() > 0:
                        page.wait_for_timeout(110)
                        opsi.click(timeout=2500, delay=85)
                        dipilih = True
                        break
                except Exception:
                    continue
            try:
                teks_dom = tombol_dom.inner_text(timeout=2000).strip()
            except Exception:
                pass
            note(f"domain dropdown: dipilih={dipilih} teks tombol='{teks_dom}'")
            time.sleep(0.8)
    except Exception as e:
        note(f"pilih domain {MAIL_DOMAIN} gagal: {str(e)[:70]}")

    if email_frame is not None and uname_in:
        loc = email_frame.locator("input").first
        # [humanisasi 2026-10-16] ketik 3-5 dtk (bukan fill instan) — pola
        # manusiawi; keping availability wajar 1-3x (HAR asli juga 3x cek)
        gerak_mouse_manusia(page, loc)
        loc.click()
        ketik_manusia(page, username)
        note(f"username diketik DI IFRAME: {username}")
        time.sleep(1.5)  # [speed 2026-10-14] jeda manusiawi + cek availability (3->1.5)
    else:
        # [fix 2026-10-22] form polos — ketik via LOCATOR + verifikasi terisi.
        # Dulu: fokus via JS + keyboard tanpa cek = username kadang tidak masuk
        # (screenshot bukti: kolom kosong 'This field is required' sementara
        # password terisi -> submit pasti ditolak form).
        note("form polos (tanpa iframe) — isi #username langsung")
        kol = page.locator("#username")
        try:
            kol.scroll_into_view_if_needed()
            gerak_mouse_manusia(page, kol)
            kol.click(timeout=8000)
        except Exception as e:
            # [fix 2026-11-05 run-1793873361 & run-1793875284] #username ADA tapi
            # TIDAK terlihat (state transisi/overlay) — click default 30dtk
            # melempar TimeoutError yang MEMBUNUH proses TANPA run-json (core.py
            # memvonis dari file itu -> jejak hangus; 2 crash identik 11:10 &
            # 11:42). Fallback = pola blok mirror (click pendek -> fokus JS ->
            # ketik) + screenshot diagnosa — jangan crash.
            note(f"#username tak terklik ({type(e).__name__}) — fokus JS + ketik")
            shot(page, "username-form-tak-terlihat")
            try:
                kol.evaluate("el => el.focus()")
            except Exception:
                pass
        ketik_manusia(page, username)
        if not (kol.input_value() or "").strip():
            note("username belum terisi — ulangi ketik")
            try:
                kol.click(timeout=3000)
            except Exception:
                try:
                    kol.evaluate("el => el.focus()")
                except Exception:
                    pass
            ketik_manusia(page, username)
        note(f"username terisi: '{kol.input_value()}'")

    # [fix 2026-10-23 bedah screenshot gagal-submit] form yang DISUBMIT = form
    # halaman-utama; ketik di IFRAME challenge hanya KADANG termirror ke sana.
    # Bukti: v2-8263 username kosong + "This field is required" (net TANPA
    # users/available = app tak pernah menerima nama) vs v2-8976 terisi + centang
    # hijau. Kalau mirror kosong -> ketik LANGSUNG ke #username halaman utama
    # (yang dibaca submit); manusia pun mengetik di form yang terlihat.
    try:
        utama = page.locator("#username")
        for _cek in range(6):
            if (utama.input_value() or "").strip():
                break
            time.sleep(0.5)
        if not (utama.input_value() or "").strip():
            note("mirror username KOSONG di form utama — ketik langsung ke #username")
            # [fix 2026-10-23 seiiusinus1074] JS focus() TIDAK cukup (fokus
            # dirampas iframe overlay -> ketik tak mendarat, terbukti 2x).
            # Pakai CLICK NYATA (pola cabang form-polos yang bekerja).
            try:
                gerak_mouse_manusia(page, utama, putar=1)
                utama.click(timeout=3000)
            except Exception:
                utama.evaluate("el => el.focus()")
            try:
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
            except Exception:
                pass
            ketik_manusia(page, username)
            time.sleep(1.2)
        nilai_utama = (utama.input_value() or "").strip()
        note(f"verifikasi form utama: username='{nilai_utama}'")
        # [fix 2026-11-06 — temuan uji SG] dulu cuma 1x ulang lalu TETAP LANJUT
        # walau kolom masih kosong -> tombol submit disabled -> tak ada POST
        # /users (5/5 run SG gagal 'SUBMIT tak memicu POST /users'). Kini loop
        # terverifikasi maks 3x (beri waktu iframe di koneksi latensi tinggi);
        # gagal terisi = hentikan lebih awal, jangan buang 45 dtk guard.
        _isi_ok = (nilai_utama == username)
        for _coba in range(3):
            if _isi_ok:
                break
            note(f"username belum terisi — isi ulang via IFRAME ({_coba + 1}/3)")
            # [fix 2026-11-06 — bukti SCREENSHOT v2-42083] input asli ADA DI
            # IFRAME; elemen 'utama' = cermin (bukan input) -> ketikan hilang +
            # Ctrl+A malah menyeleksi SELURUH halaman (teks ungu tersorot).
            # Kini: targetkan input iframe, pastikan fokus BENAR di kolom
            # sebelum Ctrl+A/Delete, verifikasi nilai tiap putaran.
            _sasaran = None
            try:
                _fr = next((f for f in page.frames if "Name=email" in f.url), None)
                if _fr is not None:
                    _sasaran = _fr.locator("input").first
            except Exception:
                _sasaran = None
            if _sasaran is None:
                _sasaran = utama
            try:
                _sasaran.scroll_into_view_if_needed(timeout=3000)
                _sasaran.click(timeout=3000)
            except Exception:
                try:
                    _sasaran.evaluate("el => el.focus()")
                except Exception:
                    pass
            page.wait_for_timeout(300)
            try:
                _fokus = page.evaluate("() => (document.activeElement && document.activeElement.tagName) || ''")
            except Exception:
                _fokus = ""
            if _fokus in ("INPUT", "TEXTAREA"):
                try:
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Delete")
                except Exception:
                    pass
            ketik_manusia(page, username)
            page.wait_for_timeout(1200)   # pompa dispatcher (bukan sleep)
            try:
                nilai_utama = (_sasaran.input_value() or "").strip()
            except Exception:
                nilai_utama = ""
            if not nilai_utama:
                try:
                    nilai_utama = (utama.input_value() or "").strip()
                except Exception:
                    pass
            note(f"verifikasi ulang ({_coba + 1}/3): '{nilai_utama}' (fokus={_fokus})")
            _isi_ok = (nilai_utama == username)
        if not _isi_ok:
            note("username TIDAK terisi setelah 3x via iframe — tandai; guard "
                 "'SUBMIT tak memicu POST /users' akan menghentikan monitor "
                 "dgn aman (run-json tetap tertulis, AGENTS #11)")
            log["onboarding_gagal"] = "username-tak-terisi"
            try:
                shot(page, "username-tak-terisi")
            except Exception:
                pass
    except Exception as e:
        note(f"verifikasi mirror username gagal: {type(e).__name__}: {str(e)[:80]}")

    # (blok pilihan domain DIPINDAH ke sebelum ketik — lihat BEDAH-URUTAN-REQUEST-EMAS)

    # [guard 409 2026-10-16] username kebetulan SUDAH DIPAKAI -> form menolak
    # submit, attempt terbuang percuma (kasus julius23: users/available 409
    # 12106, tombol tak memicu POST /users). Baca balasan availability utk nama
    # penuh; 409 -> ganti nama + ketik ulang (maks 2x) SEBELUM isi password.
    for _ulang in range(2):
        taken = any(n.get("s") == 409 and f"{username}%40" in str(n.get("u", ""))
                    for n in log["net"])
        if not taken:
            break
        note(f"username '{username}' SUDAH DIPAKAI (409) — ganti nama + ketik ulang")
        username = f"{_nama_acak()}{secrets.randbelow(90) + 10}"
        # [fix 2026-10-23 octon994] dulu mengetik TANPA fokus (fokus masih di
        # dropdown domain setelah pilih domain) -> nama baru tak pernah masuk
        # -> form masih berisi nama TAKEN -> submit ditolak form = 'submit tak
        # kirim POST /users' (varian baru dari kelas yang sama). Fokuskan ke
        # input username (iframe bila ada, else form utama) + verifikasi.
        try:
            sasaran = (email_frame.locator("input").first
                       if (email_frame is not None and uname_in)
                       else page.locator("#username"))
            gerak_mouse_manusia(page, sasaran, putar=1)
            sasaran.click(timeout=3000)
        except Exception:
            pass
        try:
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
        except Exception:
            pass
        ketik_manusia(page, username)
        try:
            _cek_nama = (page.locator("#username").input_value() or "").strip()
            if _cek_nama != username:
                note(f"rename belum nempel di form utama: '{_cek_nama}'")
        except Exception:
            pass
        page.wait_for_timeout(2500)   # beri waktu cek availability nama baru

    # ---- password di kartu utama ----
    pw = page.locator("#password")
    gerak_mouse_manusia(page, pw)
    try:
        pw.click(timeout=4000)
    except Exception:
        page.evaluate("document.querySelector('#password').focus()")
    # [humanisasi 2026-10-16] isi password 2 kotak = TOTAL 3-6 dtk (arahan user)
    ketik_pacing(page, password, 1.5, 3.0)
    note("password diketik")
    time.sleep(0.8)   # [speed 2026-10-14] 1.2->0.8
    confirm = page.locator("#password-confirm")
    try:
        confirm.wait_for(state="visible", timeout=5000)
        confirm.click(timeout=3000)
        ketik_pacing(page, password, 1.5, 3.0)
        note("konfirmasi password diketik")
    except Exception as e:
        note(f"konfirmasi password gagal: {e}")
    time.sleep(1.2)   # [speed 2026-10-14] 2.5->1.2 sebelum submit
    shot(page, "sebelum-submit")

    # ---- submit: tombol visible dgn teks MULTI-BAHASA (bug lama: filter
    # has_text="Start" gagal di persona de-DE dll -> fallback .first mengklik
    # tombol salah -> POST /users tak pernah terpicu) + fallback Enter di form ----
    _CTA = re.compile(r"^(Start|Starten|Weiter|Loslegen|Commencer|Continuar|"
                      r"Iniciar|Créer|Erstellen|Konto erstellen|Get started|"
                      r"Create account|Continue|Lanjut|Mulai)\b", re.I)
    diklik = False
    try:
        btn = page.locator("button[type=submit]").filter(has_text=_CTA).first
        if btn.count() and btn.is_visible():
            gerak_mouse_manusia(page, btn, putar=1)
            btn.click(timeout=5000)
            diklik = True
    except Exception:
        pass
    if not diklik:
        try:   # fallback: semua submit terlihat di form (urut) — sampai ada yg mendarat
            for i in range(page.locator("button[type=submit]:visible").count()):
                try:
                    page.locator("button[type=submit]:visible").nth(i).click(timeout=3000)
                    diklik = True
                    time.sleep(0.5)
                    if any("/api/core/v4/users" in n.get("u", "") for n in log.get("net", [])):
                        break
                except Exception:
                    continue
        except Exception:
            pass
    if not diklik:
        try:   # fallback terakhir: Enter di field konfirmasi password (submit native form)
            page.locator("input[type=password]").last.press("Enter")
            diklik = True
        except Exception:
            pass
    note(f"SUBMIT diklik (cta-multibahasa={diklik})")
    shot(page, "setelah-submit")
    # [bedah gerbang submit] baca PERSIS state aplikasi saat klik — syarat mana
    # yang menahan POST /users (disabled button? status cek-username? pesan error?)
    try:
        diag = page.evaluate("""() => {
          const out = {};
          try {
            const btn = document.querySelector('button[type=submit]');
            out.btn = btn ? {text: (btn.innerText||'').slice(0,20), disabled: !!btn.disabled,
                             aria: btn.getAttribute('aria-disabled')} : null;
          } catch (e) { out.btn = 'err:' + e.message; }
          try {
            const t = (document.body.innerText||'');
            out.pesan = t.slice(0, 400).replace(/\s+/g, ' ');
          } catch (e) {}
          try {
            // status cek username: cari teks peringatan inline di form
            const warn = document.querySelector('[class*=error], [class*=alert], [data-target*=error]');
            out.warn = warn ? warn.innerText.slice(0, 200) : null;
          } catch (e) {}
          try {
            // rekonstruksi sinyal app: semua input terisi?
            out.inputs = [...document.querySelectorAll('input')].map(i =>
              ({name: i.name || i.id || i.type, len: (i.value||'').length}));
          } catch (e) {}
          return out;
        }""")
        note(f"DIAG-SEBELUM-MONITOR: {json.dumps(diag, ensure_ascii=False)[:600]}")
    except Exception as e:
        note(f"DIAG gagal: {e}")
    dump_hv(page, "setelah-submit")

    # ---- amati ----
    otp_done = False
    final = {"url": "", "text": ""}
    users_seen = [n for n in log["net"] if "/api/core/v4/users" in n["u"]]
    t_monitor = time.time()   # [speed 2026-10-14] dasar guard monitor-burn
    for i in range(60):
        time.sleep(3)
        st = page.evaluate("() => ({url: location.href, text: (document.body.innerText||'').slice(0,1500)})")
        final = st
        low = st["text"].lower()
        # [speed 2026-10-14] GUARD MONITOR-BURN (riset F-bedah-waktu: kasus
        # 463-796 dtk = attempt MATI menghabiskan loop monitor 300 dtk):
        # hentikan cepat dua kasus mati yang dulu tak terjaga.
        _us = [n for n in log["net"]
               if "/api/core/v4/users" in n["u"] and n.get("m") == "POST"]
        if not _us and time.time() - t_monitor > 45:
            note("guard: SUBMIT tak memicu POST /users >45 dtk — stop monitor (hemat 2-4 mnt)")
            dump_hv(page, "guard-tanpa-post-users")
            break
        if len(_us) >= 2 and not otp_done and time.time() - t_monitor > 60:
            note("guard: POST /users berulang tanpa modal verifikasi >60 dtk — stop monitor")
            dump_hv(page, "guard-tanpa-modal")
            break
        new_users = [n for n in log["net"] if "/api/core/v4/users" in n["u"]]
        if len(new_users) != len(users_seen):
            note(f"POST /users TERDETEKSI di jaringan: {new_users[-1]}")
            users_seen = new_users
        if any(n.get("s") == 200
               and n.get("u", "").split("?")[0].endswith("/api/core/v4/users")
               for n in new_users):
            log["users_ok"] = True   # [fix] bedrock: /users 200 = akun SAH dibuat
            # [fix 2026-10-22 bedah alur subagent] JANGAN GANGGU jendela kritis:
            # 0-3dtk pasca-/users 200 SPA melakukan transisi UID (auth/info ->
            # /auth -> keys/setup). Gangguan kita di jendela ini memicu request
            # tanpa Authorization -> 401 -> SPA memanggil auth/refresh LEGACY
            # ber-body kosong (dijamin 422) -> SPA MEMBUANG SESINYA SENDIRI ->
            # akun terbuang (bukti ts run: gal mati 0,11dtk stlh /auth, tarum
            # 0,15dtk stlh keys/setup). Tangan diangkat 3 dtk.
            if not log.get("jendela_kritis"):
                log["jendela_kritis"] = True
                note("jendela kritis 8dtk pasca-/users 200 — tangan diangkat, biarkan SPA transisi auth/keys/setup")
                time.sleep(8)
            # [fix 2026-10-22] naikkan_sesi DIMATIKAN: auth/info EXTRA tanpa
            # Authorization tiap kelahiran (HAR: tepat 1x) = noise mustahil
            # manusiawi & selalu 401; appversion hardcode 428.1 basi. SPA sudah
            # melakukan auth sendiri. NAIKKAN_SESI=1 utk perilaku lama.
            if os.environ.get("NAIKKAN_SESI", "0") == "1" and not log.get("sesi_naik"):
                log["sesi_naik"] = naikkan_sesi(page, username, password)
                note(f"naikkan sesi (POST /auth): {log['sesi_naik']}")
        # [fix 2026-10-22] jendela kritis ke-2: 2,5dtk pasca-keys/setup terlihat
        # (transisi signedkeylists/local/key — tempat kematian sesi bentuk-2).
        if not log.get("jendela_kunci") and any(
                "keys/setup" in str(n.get("u", "")) for n in log["net"]):
            log["jendela_kunci"] = True
            note("jendela kritis 2,5dtk pasca-keys/setup — tangan diangkat")
            time.sleep(2.5)
            # presisi: jangan menangkap /users/available (juga 200!) -> akun hantu
        if "abusive traffic" in low or "2028" in low:
            note(f"BLOKIR ABUSE tampil:\n{st['text'][:300]}")
            shot(page, "blokir")
            break
        # [state-machine 2026-11-05 — arahan user + koordinator] layar
        # pasca-signup KONDISIONAL (display name kadang ada kadang tidak);
        # deteksi layar AKTUAL dari DOM+URL+bukti jaringan tiap iterasi, SATU
        # aksi per layar, klik hanya bila tombolnya BENAR-ADA, verifikasi
        # screenshot — sampai MASUK MAIL (bukti forks/labels/inbox), BARU
        # pasang recovery (alur user HAR 5 Nov). [revisi user 2026-11-05 malam]
        # bila sesi reset (Sign in / form signup kosong / sesi mati) SETELAH
        # akun lahir -> login ulang VIA BROWSER (lihat _login_ulang_browser).
        if log.get("users_ok") and i % 5 == 2:
            _snap_cookie(page, f"monitor-{i}")   # [diag sesi-mati] snapshot berkala
        layar = _deteksi_layar(page, log)
        if layar == "mail":
            log["masuk_mail"] = ("forks/labels/mail.proton.me"
                                 if any("/sessions/forks" in str(n.get("u", ""))
                                        for n in log["net"]) else st["url"])
            note(f"MASUK MAIL — bukti: {log['masuk_mail']} (url={st['url'][:70]})")
            shot(page, "masuk-mail")
            log["recovery_ok"] = True
            if not log.get("recovery_ui"):
                log["recovery_ui"] = pasang_recovery_browser(
                    page, f"{username}@{MAIL_DOMAIN}", password, note)
                note(f"[recovery-ui] hasil: {log['recovery_ui']}")
            break
        if layar in ("signin", "signup_ulang", "sesi_mati"):
            # [arahan user 2026-11-05 malam — MENGUBAH aturan lama] kondisi ini
            # SETELAH akun lahir = gangguan infra (dugaan user: warmup browser),
            # pemulihan yang benar: 'langsung aja ke halaman login dan masuk lagi
            # dan ke halaman setting'. Login ulang VIA BROWSER DIPERBOLEHKAN
            # khusus pemulihan reset ini (maks 2x; gagal = stop jujur).
            _snap_cookie(page, f"reset-{layar}")
            shot(page, f"reset-{layar}")
            if log.get("users_ok") and log.get("login_ulang", 0) < 2:
                log["login_ulang"] = log.get("login_ulang", 0) + 1
                if _login_ulang_browser(page, f"{username}@{MAIL_DOMAIN}",
                                        password, note):
                    continue
            note(f"kondisi '{layar}' tak tertolong login-ulang — stop jujur; "
                 "akun tetap dicatat anti-yatim")
            log["onboarding_gagal"] = f"{layar}-sesi-dibuang"
            break
        if layar in ("display_name", "passphrase", "tour"):
            log["recovery_ok"] = True
            _hasil = _aksi_onboarding(page, layar, note, username, log)
            if _hasil == "mati":
                note("sesi MATI saat onboarding (PUT /addresses 403 beruntun) — stop")
                _snap_cookie(page, "sesi-mati-onboarding")
                shot(page, "sesi-mati-onboarding")
                log["onboarding_gagal"] = "sesi-mati"
                break
            if _hasil == "habis":
                note("display name tak maju setelah 3 klik terverifikasi — stop "
                     "jujur (bukan dipaksa 6x); sesi kemungkinan anon/mati")
                shot(page, "display-name-tak-maju")
                log["onboarding_gagal"] = "display-name-tak-maju"
                break
            continue
        # (cabang SUKSES lama DIHAPUS 2026-11-05 — tugasnya kini di state-machine
        # di atas: deteksi layar aktual + aksi terverifikasi screenshot; dulu
        # 'display name' di teks body = false-positive + 6x klik buta + isi
        # display name dipotong acak, lalu pasang_recovery_browser dipanggil
        # SEBELUM sesi mail lahir -> selalu kena halaman Sign in.)
        if ("verification" in low or "verify" in low or "captcha" in low) and not otp_done:
            note(f"modal verifikasi muncul (iterasi {i})")
            shot(page, "modal")
            dump_hv(page, "modal-muncul")
            dom = page.evaluate("() => { const d = document.querySelector('[role=dialog], dialog, .modal');"
                                " return (d ? d.outerHTML : document.body.innerHTML).slice(0, 4000); }")
            note(f"DOM modal: {dom[:1500]}")
            # [fix 2026-11-08 BEDAH-URUTAN-REQUEST-EMAS] ritme mikro: manusia
            # MEMBACA modal 2.5-3.9 dtk sebelum klik opsi (emas E5/E20); kita
            # dulu langsung klik <=1 dtk = tell. Jeda baca modal kelas emas.
            time.sleep(random.uniform(2.5, 3.9))

            def aksi(selectors, aksi_fn, label, tunggu=45):
                """Ulangi sampai elemen siap — isi modal ter-render asinkron."""
                akhir = time.time() + tunggu
                while time.time() < akhir:
                    for sel in selectors:
                        try:
                            loc = page.locator(sel).first
                            if loc.count() and loc.is_visible():
                                aksi_fn(loc, sel)
                                return True
                        except Exception:
                            continue
                    time.sleep(3)
                note(f"{label} TIDAK ditemukan dalam {tunggu} dtk")
                return False

            methods = log.get("hv_methods") or []
            force = os.environ.get("HV_FORCE", "captcha")   # [kebijakan user 2026-10-05] default HANYA captcha;
            # email/SMS DIHAPUS. Verifikasi via alias disposable = tier MATI (flag
            # 'Automated Abuse Detection' + surat eksternal dikarantina — pemisah
            # sempurna 18/18, bedah S2). Menu tanpa captcha -> jatuh ke no_method
            # (gagal cepat, JANGAN lahirkan akun yang sudah pasti diblokir).
            note(f"keputusan metode dari server: {methods} (mode={force})")
            klik_captcha = ("captcha" in methods or not methods)
            klik_email = klik_sms = False   # email & sms tidak pernah dipilih
            if force == "email" and not klik_captcha and ("email" in methods or not methods):
                klik_email = aksi(
                    ["[role=dialog] >> text=Email", "button:has-text('Email')",
                     "[role=button]:has-text('Email')", "text=Email address", "label:has-text('Email')"],
                    lambda loc, sel: (loc.click(timeout=3000),
                                      note(f"metode email diklik: {sel}"), time.sleep(2.5)),
                    "opsi metode email")
            if not klik_captcha and not klik_email and force == "sms" and ("sms" in methods or not methods):
                klik_sms = aksi(
                    ["[role=dialog] >> text=SMS", "button:has-text('SMS')",
                     "[role=tab]:has-text('SMS')", "text=SMS"],
                    lambda loc, sel: (loc.click(timeout=3000),
                                      note(f"metode SMS diklik: {sel}"), time.sleep(2.5)),
                    "opsi metode SMS", tunggu=20)
            if klik_captcha:
                log["jalur"] = "captcha"
                # ---- jalur captcha slider (PRIORITAS): solver CV ----
                note("jalur captcha slider — solver CV" + (" (DIPAKSA)" if force == "captcha" else ""))
                shot(page, "sebelum-solve-captcha")
                # [fix widget 2026-10-16] tunggu widget SIAP berbasis BUKTI
                # (arahan user: jangan tunggu buta 5-10 dtk — bisa juga langsung
                # nongol). Bukti hidupnya widget = bg+puzzle terunduh. Polling
                # tiap 1 dtk -> LANJUT SEGERA saat bukti muncul; tanpa bukti
                # sampai batas = gagal nyata, tanpa buang waktu.
                def _widget_siap():
                    return (any("captcha/v1/api/bg" in str(n.get("u", "")) and n.get("s") for n in log["net"])
                            and any("captcha/v1/api/puzzle" in str(n.get("u", "")) and n.get("s") for n in log["net"]))
                t0w = time.time()
                while not _widget_siap() and time.time() - t0w < 25:
                    page.wait_for_timeout(1000)
                solved = captcha_slider.solve(page, note=note)
                if not solved:
                    # [fix 2026-10-14] sebab detail utk core: solver_gagal = sinyal
                    # RETRY slot (exit/profil baru). no_method tetap utk label
                    # "solver meleset" + streak, tapi tak lagi MEMBUNUH retry.
                    log["solver_gagal"] = dict(getattr(captcha_slider.solve, "terakhir", {}) or {})
                    _sebab = str(log["solver_gagal"].get("sebab", "")).lower()
                    if "frame" in _sebab or "widget" in _sebab:
                        # kelas 'widget/frame' = hampir pasti masih di fase
                        # kosong-putih (bukti user 5-10 dtk) -> polling BUKTI lagi;
                        # begitu widget TERBUKTI hidup -> coba 1x lagi SEGERA
                        _sudah = _widget_siap()
                        t0w = time.time()
                        while not _widget_siap() and time.time() - t0w < 12:
                            page.wait_for_timeout(1000)
                        if _widget_siap():
                            if _sudah:
                                page.wait_for_timeout(3000)   # frame sempat rusak: reda sebentar
                            note("widget TERBUKTI hidup (bg+puzzle terunduh) — coba 1x lagi")
                            solved = captcha_slider.solve(page, note=note)
                            if not solved:
                                log["solver_gagal"] = dict(getattr(captcha_slider.solve, "terakhir", {}) or {})
                        else:
                            note("12 dtk tanpa bukti widget hidup — gagal nyata, TANPA tunggu buta")
                # [keputusan RESPONS 2026-10-16 — arahan user, revisi bukti run
                # 1792166007] vonis TUNGGU/TIDAK dari TANDA RESPONS, bukan tebakan:
                #   GAGAL CEPAT : validate/finalize balas body bukan "OK" (bisa
                #                 terbaca) -> lepas tanpa buang waktu
                #   BUKTI SUKSES: app RESUBMIT POST /users dgn header token HV
                #                 (on_request mencatat hvhdr) -> vonis akhir =
                #                 BALASAN resubmit: 200 = AKUN LAHIR, 422 = gagal
                # Bug versi lama (dibetulkan): resubmit sudah terkirim SEBELUM
                # solver balik, dan balasan server baru masuk ±20 dtk kemudian —
                # versi lama vonis 'gagal' keburu MEMBUNUH akun yang sedang lahir
                # (kasus nyata vin864iana & corum1736 lahir tapi yatim).
                vonis = _status_captcha_respons(log)
                if vonis == "gagal" and log.get("solver_gagal") and ("email" in methods or not methods):
                    # [fallback email 2026-11-23] captcha solver gagal berulang,
                    # tapi server menawarkan email — coba jalur email sebelum lepas
                    note("captcha solver gagal — fallback ke verifikasi email")
                    klik_email = aksi(
                        ["[role=dialog] >> text=Email", "button:has-text('Email')",
                         "[role=button]:has-text('Email')", "text=Email address", "label:has-text('Email')"],
                        lambda loc, sel: (loc.click(timeout=3000),
                                          note(f"metode email diklik (fallback): {sel}"), time.sleep(2.5)),
                        "opsi metode email (fallback)")
                    if klik_email:
                        log["jalur"] = "email_fallback"
                        if not alias:
                            alias, mail_meta = create_mailbox()
                            log["alias"], log["mail_meta"] = alias, {k: (v[:12] + "…" if isinstance(v, str) and len(v) > 16 else v) for k, v in mail_meta.items()}
                            note(f"alias on-demand (fallback): {alias}")
                        typed = aksi(
                            ["input[type=email]", "input[placeholder*='mail' i]", "input[type=text]"],
                            lambda loc, sel: (human_type(page, loc, alias),
                                              note(f"alias diketik via {sel} (fallback)"), time.sleep(1.5)),
                            "field email (fallback)")
                        if typed:
                            sent = aksi(
                                ["button:has-text('Send')", "button:has-text('Get')", "button:has-text('Continue')",
                                 "[role=button]:has-text('code')", "button[type=submit]"],
                                lambda loc, sel: (loc.click(timeout=3000), note(f"kirim kode diklik (fallback): {sel}")),
                                "tombol kirim kode (fallback)", tunggu=30)
                            if sent:
                                shot(page, "kirim-kode-fallback")
                                otp = None
                                for percobaan in range(2):
                                    if percobaan:
                                        note("jeda 2 menit lalu minta kode ulang (percobaan 2/2 fallback)")
                                        time.sleep(120)
                                        for sel in ["button:has-text('Get')", "button:has-text('Send')",
                                                    "button:has-text('Continue')", "button[type=submit]"]:
                                            try:
                                                loc = page.locator(sel).first
                                                if loc.count() and loc.is_visible():
                                                    loc.click(timeout=3000)
                                                    note(f"kirim kode ulang diklik (fallback): {sel}")
                                                    break
                                            except Exception:
                                                continue
                                    otp = wait_otp(alias, mail_meta, t0, budget=150)
                                    if otp:
                                        break
                                if otp:
                                    for sel in ["input[autocomplete=one-time-code]", "input[inputmode=numeric]",
                                                "input[maxlength='6']", "input[type=text]"]:
                                        try:
                                            loc = page.locator(sel).first
                                            if loc.count() and loc.is_visible():
                                                human_type(page, loc, otp)
                                                note("OTP diketik (fallback)")
                                                time.sleep(1)
                                                break
                                        except Exception:
                                            continue
                                    for sel in ["button:has-text('Verify')", "button:has-text('Confirm')",
                                                "button:has-text('Submit')", "button[type=submit]"]:
                                        try:
                                            loc = page.locator(sel).first
                                            if loc.count() and loc.is_visible():
                                                loc.click(timeout=3000)
                                                note(f"verifikasi diklik (fallback): {sel}")
                                                break
                                        except Exception:
                                            continue
                                    note("verifikasi email (fallback) terkirim — tunggu resubmit /users")
                                    # jatuh ke blok resubmit di bawah (tidak break)
                                else:
                                    note("OTP tidak masuk (fallback) — lepas cepat")
                                    log["no_method"] = methods
                                    break
                            else:
                                note("tombol kirim kode tak ditemukan (fallback) — lepas cepat")
                                log["no_method"] = methods
                                break
                        else:
                            note("tombol email tak ditemukan (fallback) — lepas cepat")
                            log["no_method"] = methods
                            break
                elif vonis == "gagal":
                    note("validate/finalize balas BUKAN 'OK' (bukti respons) — gagal nyata, lepas cepat")
                    log["no_method"] = methods
                    break
                _is_u = lambda n: (n.get("m") == "POST"
                                   and str(n.get("u", "")).split("?")[0].endswith("/api/core/v4/users"))
                t0b = time.time()
                u_hdr = idx_hdr = None
                while time.time() - t0b < 30:
                    for i2, n in enumerate(log["net"]):
                        if _is_u(n) and n.get("hvhdr"):
                            u_hdr, idx_hdr = n, i2
                            break
                    if u_hdr:
                        break
                    page.wait_for_timeout(2000)   # PUMP event (time.sleep menahan dispatch Playwright)
                if not u_hdr:
                    # [fix 2026-10-23 tepi-jendela pompor0837] event request yang
                    # ter-dispatch saat wait_for_timeout TERAKHIR tidak sempat
                    # discan loop (scan terjadi di awal iterasi; while sudah
                    # berakhir). Bukti: HV-HDR resubmit masuk detik ke-29, vonis
                    # 'tak ada resubmit' detik ke-30 = GAGAL PALSU. Scan ULANG.
                    for i2, n in enumerate(log["net"]):
                        if _is_u(n) and n.get("hvhdr"):
                            u_hdr, idx_hdr = n, i2
                            note("resubmit ber-token ketemu di scan ulang tepi jendela")
                            break
                if not u_hdr:
                    note("tak ada resubmit /users ber-token 30 dtk — gagal nyata, lepas cepat")
                    log["no_method"] = methods
                    break
                note("resubmit /users ber-token ADA — tunggu balasan server (maks 120 dtk)")
                t0b = time.time()
                balas = None
                while time.time() - t0b < 120:
                    page.wait_for_timeout(2000)   # PUMP event — tanpa ini balasan
                    # /users baru tercatat SETELAH loop selesai (bug 1792166833:
                    # akun lahir, balasan 200 terlambat masuk -> vonis 'tanpa
                    # balasan' palsu). time.sleep memblokir dispatch Playwright.
                    # Jendela 120 dtk: bukti nyata respons sukses datang s/d
                    # detik ke-46 saat VPS term-load (kasus 1792167145 lewat di 46).
                    for n in log["net"][idx_hdr + 1:]:
                        if _is_u(n) and isinstance(n.get("s"), int):
                            balas = n
                            break
                    if balas:
                        break
                if balas and balas.get("s") == 200:
                    note("resubmit /users = 200 — AKUN LAHIR (lanjut onboarding)")
                    log["users_ok"] = True
                elif (balas is None or balas.get("s") in (409, None)) and \
                        _akun_terbukti_hidup(f"{username}@{MAIL_DOMAIN}", password):
                    # [fix 2026-10-21] 409/'tanpa balasan' + akun TERBUKTI hidup
                    # (login SRP sukses) = submit pertama ternyata masuk server
                    # (request aborted di klien) -> akun SAH lahir. Jangan buang.
                    note(f"resubmit /users = {balas.get('s') if balas else 'tanpa balasan'}"
                         " tapi akun TERBUKTI hidup (login SRP OK) — AKUN LAHIR, lanjut onboarding")
                    log["users_ok"] = True
                    # [fix 2026-10-23 korban 8x8 cla2213iana] UI TIDAK akan ke
                    # onboarding (sesi SPA tak pernah terbentuk — keys/setup tak
                    # pernah jalan) -> jangan tunggu 55 dtk; kombinasi tetap
                    # dicatat (mail|pass||) supaya akun tak hilang sia-sia.
                    log["terbukti_hidup"] = True
                else:
                    note(f"resubmit /users = {balas.get('s') if balas else 'tanpa balasan 45 dtk'}"
                         " — gagal nyata, lepas cepat")
                    log["no_method"] = methods
                    break
            elif klik_email:
                log["jalur"] = "email"
                if not alias:  # permintaan user: buat mail HANYA saat email dipakai
                    alias, mail_meta = create_mailbox()
                    log["alias"], log["mail_meta"] = alias, {k: (v[:12] + "…" if isinstance(v, str) and len(v) > 16 else v) for k, v in mail_meta.items()}
                    note(f"alias on-demand: {alias}")
                typed = aksi(
                    ["input[type=email]", "input[placeholder*='mail' i]", "input[type=text]"],
                    lambda loc, sel: (human_type(page, loc, alias),
                                      note(f"alias diketik via {sel}"), time.sleep(1.5)),
                    "field email")
                if not typed:
                    break
                sent = aksi(
                    ["button:has-text('Send')", "button:has-text('Get')", "button:has-text('Continue')",
                     "[role=button]:has-text('code')", "button[type=submit]"],
                    lambda loc, sel: (loc.click(timeout=3000), note(f"kirim kode diklik: {sel}")),
                    "tombol kirim kode", tunggu=30)
                if not sent:
                    break
                shot(page, "kirim-kode")
                otp = None
                for percobaan in range(2):
                    if percobaan:
                        note("jeda 2 menit lalu minta kode ulang (percobaan 2/2)")
                        time.sleep(120)
                        for sel in ["button:has-text('Get')", "button:has-text('Send')",
                                    "button:has-text('Continue')", "button[type=submit]"]:
                            try:
                                loc = page.locator(sel).first
                                if loc.count() and loc.is_visible():
                                    loc.click(timeout=3000)
                                    note(f"kirim kode ulang diklik: {sel}")
                                    break
                            except Exception:
                                continue
                    otp = wait_otp(alias, mail_meta, t0, budget=150)
                    if otp:
                        break
                if not otp:
                    note("OTP tidak masuk setelah 2 percobaan kirim kode")
                    break
                for sel in ["input[autocomplete=one-time-code]", "input[inputmode=numeric]",
                            "input[maxlength='6']", "input[type=text]"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() and loc.is_visible():
                            human_type(page, loc, otp)
                            note("OTP diketik")
                            time.sleep(1)
                            break
                    except Exception:
                        continue
                for sel in ["button:has-text('Verify')", "button:has-text('Confirm')",
                            "button:has-text('Submit')", "button[type=submit]"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() and loc.is_visible():
                            loc.click(timeout=3000)
                            note(f"verifikasi diklik: {sel}")
                            break
                    except Exception:
                        continue
            elif klik_sms:
                log["jalur"] = "sms"
                # ---- jalur SMS via SMSBower: negara = IP, provider termurah ----
                if not os.environ.get("SMSBOWER_KEY"):
                    note("SMSBOWER_KEY belum diset — jalur SMS tidak bisa dipakai")
                    log["no_method"] = methods
                    try:  # foto semua pilihan/tab yang tampil utk dokumentasi
                        ntab = page.locator("button.tabs-list-link, [role=tab]").count()
                        for i in range(ntab):
                            try:
                                loc = page.locator("button.tabs-list-link, [role=tab]").nth(i)
                                nama = (loc.inner_text(timeout=2000) or f"tab{i}").strip()
                                loc.click(timeout=3000, force=True)
                                time.sleep(1.5)
                                shot(page, "pilihan-" + re.sub(r"[^a-z0-9]+", "-", nama.lower())[:16])
                            except Exception:
                                continue
                    except Exception as e:
                        note(f"foto pilihan gagal: {e}")
                    shot(page, "tanpa-vektor")
                    break
                sb = smsbower.SMSBower()
                try:
                    svc = sb.find_service(r"proton")
                    negara = os.environ.get("SMSBOWER_COUNTRY") or os.environ.get("PROXY_COUNTRY", "germany")
                    cid = sb.find_country(negara)
                    harga, prov = sb.best_price(svc, cid)
                    note(f"SMSBower: service={svc} negara={negara}({cid}) termurah={harga} prov={prov} saldo={sb.balance()}")
                    aid, phone, extra = sb.get_number(svc, cid, max_price=harga, provider_ids=prov)
                    note(f"nomor SMSBower: {phone} (id={aid}, harga={extra.get('activationCost', harga)})")
                except Exception as e:
                    note(f"SMSBower gagal ambil nomor: {e}")
                    shot(page, "sms-gagal")
                    break
                num = re.sub(r"\D", "", phone)
                typed = aksi(
                    ["input[type=tel]", "input[name*='phone' i]", "input[placeholder*='phone' i]",
                     "input[type=number]"],
                    lambda loc, sel: (human_type(page, loc, num),
                                      note(f"nomor diketik via {sel}"), time.sleep(1.5)),
                    "field nomor telepon")
                if not typed:
                    note("field telepon tidak ditemukan")
                    sb.cancel(aid)
                    break
                sent = aksi(
                    ["button:has-text('Send')", "button:has-text('Get')", "button:has-text('Continue')",
                     "[role=button]:has-text('code')", "button[type=submit]"],
                    lambda loc, sel: (loc.click(timeout=3000), note(f"kirim kode SMS diklik: {sel}")),
                    "tombol kirim kode SMS", tunggu=30)
                shot(page, "sms-kirim-kode")
                if not sent:
                    sb.cancel(aid)
                    break
                otp = sb.wait_code(aid, budget=240)
                if not otp:
                    note("SMS tidak masuk dari SMSBower")
                    sb.cancel(aid)
                    break
                note(f"kode SMS masuk: {otp}")
                for sel in ["input[autocomplete=one-time-code]", "input[inputmode=numeric]",
                            "input[maxlength='6']", "input[type=text]"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() and loc.is_visible():
                            human_type(page, loc, otp)
                            note("kode SMS diketik")
                            time.sleep(1)
                            break
                    except Exception:
                        continue
                for sel in ["button:has-text('Verify')", "button:has-text('Confirm')",
                            "button:has-text('Submit')", "button[type=submit]"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() and loc.is_visible():
                            loc.click(timeout=3000)
                            note("verifikasi SMS diklik")
                            break
                    except Exception:
                        continue
                note(f"aktivasi SMSBower ditandai selesai: {sb.finish(aid)}")
            else:
                # ---- tak ada captcha/email/sms: foto SEMUA pilihan yg tampil, lalu skip ----
                log["no_method"] = methods
                note(f"tidak ada captcha/email/sms ({methods}) — foto semua pilihan lalu skip")
                try:
                    ntab = page.locator("button.tabs-list-link, [role=tab]").count()
                    for i in range(ntab):
                        try:
                            loc = page.locator("button.tabs-list-link, [role=tab]").nth(i)
                            nama = (loc.inner_text(timeout=2000) or f"tab{i}").strip()
                            loc.click(timeout=3000, force=True)
                            time.sleep(1.5)
                            shot(page, "pilihan-" + re.sub(r"[^a-z0-9]+", "-", nama.lower())[:16])
                        except Exception:
                            continue
                except Exception as e:
                    note(f"foto pilihan gagal: {e}")
                shot(page, "tanpa-vektor")
                break
            otp_done = True
            shot(page, "pasca-verifikasi")
        elif i % 5 == 0:
            note(f"menunggu ({i}) url={st['url'][:60]}")
        # [fix audit 2026-10-09 malam v2] URL macet di signup >60 dtk SETELAH
        # recovery = tanda akun sedang dibunuh Proton (3 run terbukti: kelas 6
        # menit = 10003). Dulu syarat 'not users_ok' tidak menendang (akun-akun
        # ini justru punya /users 200) -> 280 dtk percuma + tercatat sukses
        # palsu sehingga slot tak pernah retry.
        if log.get("recovery_ok") and i >= 15 and "mail/signup" in st["url"]:
            note("URL macet di signup >60 dtk setelah recovery — akhiri cepat")
            if not log.get("users_ok"):
                log["gagal_coa"] = True
            break
        # [fix 2026-10-13] LIMBO cepat: /users 200 tapi GET /users balas 401/403
        # (9106 kehilangan scope) = balapan persist-sesi vs keys/setup KALAH —
        # akun terbukti MATI (uji login exit sama: 10003). Vonis awal hemat
        # ~250 dtk monitor percuma per attempt; biar worker lanjut exit baru.
        if (log.get("terbukti_hidup") and i >= 4 and "mail/signup" in st["url"]
                and not any(s2 in low for s2 in ("display name", "welcome",
                                                 "recovery phrase", "onboarding"))):
            # [fix 2026-10-23 cla2213iana] kasus 409-nyangkut: sesi SPA tak
            # pernah terbentuk (keys/setup tak jalan) -> UI PASTI tetap di form
            # signup. Tunggu 55 dtk = buang waktu worker; akun sudah terjamin
            # dicatat oleh blok anti-yatim 'terbukti_hidup'. Lepas cepat.
            note("terbukti-hidup + UI tak ke onboarding — lepas cepat (akun dicatat)")
            break
        if (log.get("users_ok") and i >= 6 and "mail/signup" in st["url"]
                and any(str(n.get("u", "")).split("?")[0].rstrip("/").endswith("/api/core/v4/users")
                        and n.get("s") in (401, 403) for n in log["net"])):
            # [user 2026-10-16] vonis LIMBO-CEPAT DIHENTIKAN: GET /users 403 =
            # artefak scope yang terbukti memvonis MATI akun-akun hidup (dibuang
            # percuma). Vonis hidup/mati diserahkan ke KONDISI UI (kriteria 9 Okt
            # yang terbukti: layar onboarding = hidup; form/loading = mati) lewat
            # cek 'berhasil' di akhir. Saklar LIMBO_CEPAT=1 utk perilaku lama.
            if os.environ.get("LIMBO_CEPAT", "0") == "1" or not (
                    "display name" in low or "anzeigenamen" in low
                    or "welcome" in low or "recovery phrase" in low
                    or "wiederherstellungsphrase" in low):
                # vonis BERBASIS UI (kriteria 9 Okt yang terbukti): tanpa layar
                # onboarding + users 401/403 = sesi kalah balapan -> lepas cepat.
                note("LIMBO: users 401/403 + UI tanpa onboarding (layar loading) — cepat")
                shot(page, "limbo")
                break
            note("catatan: users 401/403 (artefak scope) — layar onboarding tampil, lanjutkan")

    shot(page, "final")
    log["final"] = {"url": final.get("url", ""), "text": final.get("text", "")[:1500]}
    try:
        log["forge_dbg"] = page.evaluate("() => window.__forge_dbg || null")
    except Exception:
        pass
    try:  # ringkasan event challenge (keydown/input/init/...) utk bukti riset
        # [riset blob 2026-10-16] plaintext fingerprint dari kit (child.message
        # .data) SEBELUM dienkripsi ke Payload — kunci kit acak per-respons
        # jadi ciphertext tak bisa didekripsi dari artefak; simpan plaintextnya.
        # [fix 2026-10-22] dibaca dari msg_kit (console bridge) — window.__msg
        # dihapus: global buatan = jejak otomasi yang dipindai skrip halaman.
        log["msg_data"] = [m.get("d") for m in msg_kit
                           if m.get("t") == "child.message.data"][-4:]
        log["msg"] = [
            f"{m.get('t')}:{((m.get('d') or {}).get('payload') or {}).get('type') or ''}"
            for m in msg_kit[-80:]]
    except Exception:
        pass
    try:  # [fix 2026-10-16] meter byte proxy per attempt — dulu tak tercatat,
        # 'tunnel putus vs server lambat' tak bisa dibedakan dari data.
        # (byte & byte_langsung = PROPERTY, bukan fungsi — panggil tanpa () )
        if _fwd:
            _bu, _bd = _fwd.byte
            log["byte_fwd"] = {"up": _bu, "down": _bd,
                               "langsung": _fwd.byte_langsung}
    except Exception:
        pass
    _snap_cookie(page, "akhir")   # [diag sesi-mati] cookie jar akhir run
    if log.get("onboarding_gagal"):
        # [diag sesi-mati 2026-11-05] orakel penentu: token user masih valid di
        # server (200 = korupsi request klien) vs benar2 dicabut (401/403).
        _probe_token_mati(page, log, note)
    RESULT_F.write_text(json.dumps(log, indent=1, default=str))

    # ekspor combo mail|pass|refresh_token|client_id (client_id = UID sesi Proton)
    # hanya saat signup benar-benar sukses — token sesi anonim tidak berguna
    # [fix 2026-10-04] "Set a display name" = onboarding PASCA-signup = akun SUDAH
    # JADI (dulu dianggap gagal -> combo dilewati percuma, kasus flavus477).
    berhasil = ("/u/1/" in final.get("url", "") or "inbox" in final.get("url", "")
                or "recovery phrase" in final.get("text", "").lower()
                or "welcome" in final.get("text", "").lower()
                or "display name" in final.get("text", "").lower()
                or "wiederherstellungsphrase" in final.get("text", "").lower()
                # [state-machine 2026-11-05] bukti jaringan masuk mail
                # (sessions/forks 200 / mail.proton.me) = sukses TERKUAT
                or bool(log.get("masuk_mail")))
                # [fix kritis final malam 2026-10-09] 'or users_ok' DIBUANG:
                # /users 200 tanpa konfirmasi UI = kelas LIMBO (UI nyangkut di
                # form/'Creating your account') yang DIBUNUH Proton 10003 beberapa
                # menit kemudian — bukti 3-vs-3: semua MATI layarnya form/loading,
                # semua HIDUP layarnya onboarding (recovery-phrase/display-name).
    # [fix anti-yatim 2026-10-16 — temuan bedah: 100 akun lahir tak tercatat,
    # 76 di antaranya hari ini termasuk 5 akun uji kita] jaringan = bukti
    # kelahiran SAH bila rantai kripto SELESAI (keys/setup 200 / signedkeylists
    # 200) walau layar belum sempat ke onboarding (halaman diputus di tengah).
    # Akun TETAP dicatat (mail|pass||) supaya bisa dilengkapi recovery lewat
    # mekanisme stok — jangan hilang sia-sia. ATURAN LIMBO lama TETAP berlaku:
    # /users 200 TANPA keys/setup (layar form/loading) = TIDAK dicatat.
    if not berhasil and log.get("terbukti_hidup"):
        # [fix 2026-10-23] akun 409-nyangkut yang TERBUKTI hidup (login SRP OK)
        # = bukti kelahiran PALING KUAT (lebih kuat dari keys/setup 200) — wajib
        # dicatat (mail|pass||) sesuai konvensi anti-yatim, jangan dibuang hanya
        # karena UI tak sampai onboarding. (Kekurangan keys/setup tercatat di
        # run-json: akun begini perlu dilengkapi sebelum bisa terima mail.)
        berhasil = True
        note("anti-yatim: akun terbukti hidup (login SRP OK) — dicatat walau UI tanpa onboarding")
    if not berhasil:
        for n in log.get("net", []):
            u = str(n.get("u", "")).split("?")[0]
            if n.get("s") == 200 and (u.endswith("/api/core/v4/keys/setup")
                                      or u.endswith("/api/core/v4/keys/signedkeylists")):
                berhasil = True
                note("anti-yatim: jaringan buktikan akun LAHIR (keys/setup 200) — dicatat walau UI belum onboarding")
                break
    if not berhasil:
        # [fix 2026-11-05 anti-yatim-2 — bukti run-1793851701 aug0200a] /users 200
        # + auth/info 200 + /auth 200 (bootstrap SPA sendiri, Scopes penuh) = akun
        # LAHIR & password BENAR, tapi token dicabut server 8-20 dtk kemudian
        # (vonis 10003 saat signup) -> keys/setup 403 -> layar LIMBO -> akun
        # terbuang tanpa jejak (pabrik 'tak-menghasilkan' padahal akun ADA).
        # Beda dgn kasus LIMBO 2026-10-09 (hanya /users 200, /auth tak pernah 200
        # = akun hantu): di sini /auth 200 = server MENERIMA login kita. Catat
        # mail|pass|| — jangan hilang sia-sia (prinsip anti-yatim 2026-10-16).
        _auth_ok = any(str(n.get("u", "")).split("?")[0].rstrip("/").endswith("/api/core/v4/auth")
                       and n.get("s") == 200 for n in log.get("net", []))
        if _auth_ok and log.get("users_ok"):
            berhasil = True
            note("anti-yatim-2: /users 200 + /auth 200 (login diterima server) — dicatat walau token dicabut pre-keys/setup")
    if not berhasil:
        note("combo dilewati — signup belum sukses (bukan akun nyata)")
    try:
        if not berhasil:
            raise RuntimeError("skip")
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        uid = rt = at = None
        # [fix BEDAH-BUNUH-DIRI-SESI 2026-11-08] saat n_auth>1 (ada sesi anon
        # ke-2), loop lama mengambil REFRESH terakhir = kadang kredensial SESI
        # ANONIM bukan sesi akun (bukti julus32721: tersimpan 475is… sedangkan
        # UID body /auth 200 = 5i5kz…). Utamakan REFRESH dgn UID == UID /auth.
        uid_auth = ""
        for _n in log.get("net", []):
            if (str(_n.get("u", "")).split("?")[0].rstrip("/").endswith("/api/core/v4/auth")
                    and _n.get("s") == 200):
                _m = re.search(r'"UID":\s*"([A-Za-z0-9]+)', str(_n.get("body") or ""))
                if _m:
                    uid_auth = _m.group(1)[:12]
        for n, v in cookies.items():
            if n.startswith("REFRESH-"):
                u2 = n.split("-", 1)[1]
                if uid_auth and not u2.startswith(uid_auth):
                    continue          # sesi anon/bukan akun — jangan simpan
                uid = u2
                try:
                    rt = json.loads(urllib.parse.unquote(v)).get("RefreshToken") or urllib.parse.unquote(v)
                except Exception:
                    rt = v
            if n.startswith("AccessToken-"):
                try:
                    at = json.loads(urllib.parse.unquote(v)).get("AccessToken") or urllib.parse.unquote(v)
                except Exception:
                    at = v
        if uid and rt:
            # SATU file hasil (permintaan user): email|pass|refresh|client.
            # refresh+client = cek-token cepat & bahan OTP-reader (berguna nyata);
            # access_token sengaja dibuang (kedaluwarsa). combo.txt tidak lagi
            # ditulis — cukup registered.txt (ekspor kapan pun bila perlu).
            tulis.tambah(REGISTERED_F, f"{username}@{MAIL_DOMAIN}|{password}|{rt}|{uid}")
            note("akun ditambahkan ke registered.txt (email|pass|refresh|client)")
        else:
            # [edit brutalism] format TUNGGAL 4 kolom (bug #6): dulu 2 kolom di
            # sini vs 4 kolom di atas = parser ambigu (mail|pass|X).
            # [fix] cookie REFRESH belum ada di fase onboarding — akun tetap
            # BERGUNA penuh utk rantai (cukup mail|pass: pembaca OTP login SRP
            # mandiri) — jangan buang akun jadi gara-gara cookie telat.
            tulis.tambah(REGISTERED_F, f"{username}@{MAIL_DOMAIN}|{password}||")
            note("akun ditambahkan ke registered.txt (email|pass|| — cookie REFRESH belum ada)")
    except Exception as e:
        note(f"combo gagal: {e}")
    # [fix] simpan ULANG log setelah blok combo — dulu JSON dibuang sebelum
    # catatan combo sehingga jejak penulisan hilang dari arsip attempt.
    RESULT_F.write_text(json.dumps(log, indent=1, default=str))

    # [satu-browser 2026-10-12] SIMPAN state browser (cookies+localStorage)
    # supaya rantai bisa LANJUT tanpa login ulang — kunci arsitektur SATU ALUR.
    try:
        state_file = os.path.join(str(REGISTERED_F.parent), "browser-state", f"{username}@{MAIL_DOMAIN}.json")
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        ctx = page.context
        ctx.storage_state(path=state_file)
        note(f"browser state disimpan: {state_file}")
    except Exception as e:
        note(f"browser state gagal disimpan: {e}")

    if os.environ.get("CHROME_DISK_CACHE", "0") == "1":
        import shutil as _sh
        try:
            browser.close()
        except Exception:
            pass
        try:
            chrome_proc.terminate()
            chrome_proc.wait(timeout=5)
        except Exception:
            try:
                chrome_proc.kill()
            except Exception:
                pass
        try:
            if _fwd:
                _fwd.stop()
        except Exception:
            pass
        try:   # panaskan template dari profil attempt ini (cache Chrome ASLI)
            # [fix 2026-10-22] HANYA simpan cache dari attempt BERSIH (akun lahir).
            # Bukti: file terpotong dari attempt gagal (koneksi dibunuh di tengah
            # unduhan) ikut tersimpan -> diracuni ke SEMUA attempt berikutnya
            # (komponen challenge tak termuat -> frame form tak pernah ada ->
            # 11/11 + 13/13 attempt gagal; sembuh total setelah proton-warm
            # disingkirkan). Kalau perlu cache murah utk semua: perbaiki dulu
            # integritas entry-nya, jangan asal merge.
            if log.get("users_ok") and not log.get("no_method"):
                for _sub in ("Cache",):
                    _s = Path(profil_tmp) / "Default" / _sub
                    if _s.is_dir():
                        _sh.copytree(_s, HERE / "proton-warm" / "Default" / _sub,
                                     dirs_exist_ok=True)
        except Exception:
            pass
        _sh.rmtree(profil_tmp, ignore_errors=True)
    else:
        browser.close()

print("\n== RINGKASAN v2 ==")
print("akun :", f"{username}@{MAIL_DOMAIN}", "| alias:", alias)
print("akhir:", log["final"].get("url", ""))
print("teks :", log["final"].get("text", "")[:400])
