#!/usr/bin/env python3
"""gh_api.py — lapisan HTTP murni utk form GitHub (pola gasak: C5-C8) dengan
curl_cffi impersonate=chrome150 (sidik TLS/HTTP2 persis Chrome -> aman utk
DataDome, hemat byte). Form-fill diberi pace() manusiawi; token dipanen dari
HTML hasil gate (authenticity/timestamp/honeypot dinamis).

REVISI (RE D4-sub/D7/D8/D11):
  * entry pintu 1 homepage-inline (`source=form-home-signup`) — fallback pintu 2.
  * kalibrasi_sesi: log ringkas rt per sesi + berhenti awal saat >=3 rt:'c' beruntun.
  * vonis_tambahan()/varian_gate()/alasan_ip() — klasifikasi varian gate (robot-block
    vs slider t=bv vs device-check t=fe) + panen reasonIP (playbook B.1(7)/B.3).
  * C9/C11/login-ulang (D11): port pola terbukti fase_C_* stage1_http — submit OTP
    `launch_code[]`x8 dgn header verified-fetch + x-fetch-nonce, lalu POST /session
    tanpa pace() (fast-lane; target <8 dtk). Varian HTTP opsional — pipeline default
    tetap browser (keputusan pemilik, RE "Keputusan yang tetap milik pemilik")."""
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
import re
import time
from urllib.parse import urlencode, quote

from curl_cffi import requests as creq

IMP = "chrome150"

# ---- [jaringan 2026-10-06] pool geonode + jaminan SESI TIDAK PERNAH direct ----
# Meter jujur = byte NIC wlp3s0 (hotspot HP) — tiap request WAJIB lewat proxy.
# JANGAN pakai SIGNUP_PROXY utk modul ini: router cache mengarahkan host
# non-proton (github.com) ke jalur "direct"-nya sendiri (desain router_cache:
# "host lain -> langsung"); hanya trafik *.proton.me yang layak lewat sana.
JALUR_POOL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "proxies.txt")


def muat_pool(jalur=None):
    """Semua exit geonode `host:port:user:pass` -> list dict standar
    {"server": "http://host:port", "username", "password"} (bentuk run.muat_proxy)."""
    out = []
    try:
        for ln in open(jalur or JALUR_POOL):
            ln = ln.strip()
            if not ln or ln.startswith("#") or ln.count(":") < 3:
                continue
            host, port, user, pw = ln.split(":", 3)
            out.append({"server": f"http://{host}:{port}",
                        "username": user, "password": pw})
    except Exception:
        pass
    return out


_PUTARAN_PERTAMA = 0


def proksi_pertama(jalur=None):
    """Satu exit geonode dari pool — jaring pengaman utk pemanggil yang datang
    tanpa proxy. [fix 2026-10-09] BERPUTAR melingkar: dulu selalu pool[0] =
    SATU exit dipakai semua pemanggil tanpa proxy -> 429 Code 2028 'abusive
    traffic' (tebakan user: 'proxy satu biji dibagi ke semua' — TEPAT).
    None hanya bila pool kosong/file hilang."""
    global _PUTARAN_PERTAMA
    pool = muat_pool(jalur)
    if not pool:
        return None
    _PUTARAN_PERTAMA += 1
    return pool[_PUTARAN_PERTAMA % max(1, len(pool))]


def ke_url(px):
    """dict proxy / URL -> URL `http://[user:pass@]host:port` utk curl_cffi
    (tanpa bagian auth bila memang tak ber-auth, mis. router cache lokal)."""
    if not px:
        return None
    if isinstance(px, str):
        return px if "://" in px else "http://" + px
    srv = (px.get("server") or "").split("//", 1)[-1]
    u, p = px.get("username"), px.get("password")
    return f"http://{u}:{p or ''}@{srv}" if u else f"http://{srv}"


def pace(a, b=None):
    time.sleep(random.uniform(a, b if b is not None else a))


def _b():
    return "----WebKitFormBoundary" + "".join(random.choices("0123456789ABCDEF", k=16))


def multipart_encode(fields, boundary):
    out = b""
    for k, v in fields:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    return out + f"--{boundary}--\r\n".encode()


HDR_DASAR = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document", "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none", "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


def hdr_doc(referer=None):
    h = dict(HDR_DASAR)
    if referer:
        h["referer"] = referer
        h["sec-fetch-site"] = "same-origin"
    return h


def hdr_xhr(referer):
    return {"accept": "*/*", "accept-language": "en-US,en;q=0.9", "referer": referer,
            "sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin", "x-requested-with": "XMLHttpRequest"}


def hdr_form(referer):
    h = hdr_doc(referer)
    h["content-type"] = "application/x-www-form-urlencoded"
    h["origin"] = "https://github.com"
    return h


def form_tokens(html):
    """Panen token form signup: authenticity_token/timestamp/timestamp_secret +
    honeypot (nama field dinamis). Kosong = drift/gerbang belum tembus."""
    def satu(n):
        m = re.search(rf'name="{n}"\s+value="([^"]*)"', html) or \
            re.search(rf'value="([^"]*)"\s+name="{n}"', html)
        return m.group(1) if m else ""
    tok = {"authenticity_token": satu("authenticity_token"),
           "timestamp": satu("timestamp"),
           "timestamp_secret": satu("timestamp_secret"),
           "honeypot": ""}
    known = {"authenticity_token", "timestamp", "timestamp_secret", "commit",
             "user[email]", "user[password]", "user[login]", "return_to",
             "utf8", "flow", "input_signature", "invitation_token",
             "repo_invitation_token", "filter", "user_signup[country]",
             "user_signup[copilot_opt_in]", "user_signup[marketing_consent]",
             "octocaptcha-token"}
    for m in re.finditer(r'<input[^>]*type="(?:text|hidden)"[^>]*>', html):
        tag = m.group(0)
        nm = re.search(r'name="([^"]+)"', tag)
        val = re.search(r'value="([^"]*)"', tag)
        if nm and (val is None or val.group(1) == "") and nm.group(1) not in known:
            n = nm.group(1)
            if n.startswith("required") or "_field_" in n:
                tok["honeypot"] = n
                break
            if not tok["honeypot"] and len(n) > 12:
                tok["honeypot"] = n
    return tok


SIGNUP_PINTU2 = "https://github.com/signup?ref_cta=Sign+up&ref_loc=header&ref_page=%2F"


def url_pintu1(mail):
    """URL tujuan form inline homepage (pintu 1, D8): jejak funnel
    `source=form-home-signup&user_email=...` — persis jejak manusia sukses di
    capture [547] (bedah-github-signup.md §B)."""
    return ("https://github.com/signup?source=form-home-signup&user_email="
            + quote(mail or ""))


class GhSesi:
    """Sesi HTTP GitHub impersonate-Chrome; cookie bisa dioper ke browser utk
    melanjutkan OTP/2FA dgn identitas yang sama.
    [jaringan] proxy BUKAN opsional: kosong -> env GH_PROXY -> exit geonode pool
    (data/proxies.txt). Tidak pernah direct tanpa eksplisit (izinkan_direct=True
    / env IZINKAN_DIRECT=1) — hotspot kuota terbatas, meter = byte wlp3s0."""

    def __init__(self, proxy=None, timeout=30, izinkan_direct=False):
        direct_ok = izinkan_direct or os.environ.get("IZINKAN_DIRECT") == "1"
        if not proxy and not direct_ok:
            proxy = os.environ.get("GH_PROXY") or ke_url(proksi_pertama())
        if not proxy and not direct_ok:
            raise RuntimeError("GhSesi tanpa proxy — menolak egress direct "
                               "(isi argumen proxy / GH_PROXY / data/proxies.txt)")
        pr = None
        if proxy:
            u = proxy if "://" in proxy else "http://" + proxy
            pr = {"https": u, "http": u}
        self.s = creq.Session(impersonate=IMP, proxies=pr, timeout=timeout)

    # ---- entry sesuai aturan user: github.com DULU (jejak header-link) ----
    def entry_dan_gate(self, email=None):
        """Entry organik PINTU 1 (D8): GET github.com/ -> isi #hero_user_email ->
        /signup?source=form-home-signup&user_email=<mail>. Fallback pintu 2
        (ref_cta=header) bila form inline tak ada / pintu 1 gagal dimuat."""
        r = self.s.get("https://github.com/", headers=hdr_doc())
        pace(0.7, 1.5)   # [hemat-waktu] dulu 1.5-3 — jejak jeda antar-GET tetap
        html = r.text or ""
        if email and ('id="hero_user_email"' in html or "form-home-signup" in html):
            r = self.s.get(url_pintu1(email), headers=hdr_doc("https://github.com/"))
            if r.status_code == 200 and (r.text or ""):
                return r
        r = self.s.get(SIGNUP_PINTU2, headers=hdr_doc("https://github.com/"))
        return r

    # ---- C5-C8 (pola gasak) ----
    def c5_email(self, email, tok, referer):
        pace(0.4, 0.9)   # [speed 2026-10-14] pacing form = tidur siang utk request API (pemilik: buat apa); dulu 2.0-4.5
        b = _b()
        return self.s.post("https://github.com/email_validity_checks",
                           headers={**hdr_xhr(referer),
                                    "content-type": f"multipart/form-data; boundary={b}"},
                           data=multipart_encode([("authenticity_token", tok["authenticity_token"]),
                                                  ("value", email)], b))

    def c6_password(self, pw, tok, referer):
        pace(0.5, 1.0)   # [speed 2026-10-14] dulu 2.5-5.5
        b = _b()
        return self.s.post("https://github.com/password_validity_checks"
                           "?hide_password_validity_pills=true&hide_strength_sentence=true",
                           headers={**hdr_xhr(referer),
                                    "content-type": f"multipart/form-data; boundary={b}"},
                           data=multipart_encode([("authenticity_token", tok["authenticity_token"]),
                                                  ("value", pw)], b))

    def c7_username(self, user, referer):
        pace(0.3, 0.7)   # [speed 2026-10-14] dulu 1.0-2.5
        return self.s.get(f"https://github.com/signup_check_new/username?value={quote(user)}",
                          headers=hdr_xhr(referer))

    def c8_signup(self, email, user, pw, tok, referer, country="DE",
                  octo=""):
        # [bedah 2026-10-12] jeda baca-form manusiawi sebelum submit kritis
        # (usia token wajar; submit seketika = pola mesin)
        pace(1.0, 2.0)   # [speed 2026-10-14] dulu 8-20 DTK murni percuma
        body = urlencode([
            ("authenticity_token", tok["authenticity_token"]),
            ("return_to", ""), ("invitation_token", ""), ("repo_invitation_token", ""),
            ("user[email]", email), ("user[password]", pw), ("user[login]", user),
            ("user_signup[country]", country), ("filter", ""),
            ("user_signup[copilot_opt_in]", "1"), ("user_signup[marketing_consent]", "0"),
            ("octocaptcha-token", octo),
            (tok["honeypot"], ""),
            ("timestamp", tok["timestamp"]), ("timestamp_secret", tok["timestamp_secret"]),
        ])
        return self.s.post(referer, headers=hdr_form(referer), data=body,
                           allow_redirects=False)

    def kuki_untuk_browser(self):
        """Cookie sesi (termasuk `datadome` CHIPS) utk context Playwright."""
        out = []
        for c in self.s.cookies.jar:
            out.append({"name": c.name, "value": c.value, "domain": c.domain,
                        "path": c.path or "/", "secure": bool(c.secure),
                        "httpOnly": bool(c.has_nonstandard_attr("HttpOnly") or False)})
        return out

    def ukuran_byte(self):
        try:
            return int(self.s.bytes_recv or 0) + int(self.s.bytes_sent or 0)
        except Exception:
            return 0

    # ---- C9/C11/login-ulang (D11 — port fase_C_* stage1_http; fast-lane tanpa pace) ----
    def c9_otp_page(self, otp_url, referer="https://github.com/signup"):
        """C9 GET /account_verifications -> panen meta fetch-nonce +
        github-client-version (pola terbukti stage1_http.gh_fetch_nonce)."""
        r = self.s.get(otp_url, headers=hdr_doc(referer))
        meta = gh_fetch_nonce(r.text or "")
        return {"html": r.text, "url": otp_url, "status": r.status_code, **meta}

    def c11_submit_otp(self, otp, meta, otp_url, return_to=""):
        """C11 POST /account_verifications PERSIS bentuk capture [626]: kode per
        digit sebagai array launch_code[] (8 field) + 7 hidden kosong, TANPA
        authenticity_token/timestamp; penggantinya header github-verified-fetch +
        x-fetch-nonce (meta halaman OTP, sekali pakai per load) +
        x-github-client-version. Tanpa pace() — bagian fast-lane."""
        hdrs = hdr_xhr(otp_url)
        hdrs.update({
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://github.com",
            "github-verified-fetch": "true",
            "x-fetch-nonce": (meta or {}).get("nonce") or "",
        })
        if (meta or {}).get("client_version"):
            hdrs["x-github-client-version"] = meta["client_version"]
        body = urlencode(
            [("return_to", return_to), ("invitation_token", ""), ("repo_invitation_token", ""),
             ("plan", ""), ("verification", ""), ("setup_organization", ""),
             ("trial_acquisition_channel", "")]
            + [("launch_code[]", d) for d in str(otp)])
        return self.s.post("https://github.com/account_verifications", headers=hdrs,
                           data=body, allow_redirects=False)

    def login_ulang(self, email, pw, return_to="https://github.com/dashboard",
                    referer="https://github.com/account_verifications"):
        """Login ulang DIPAKSA pasca-verifikasi (RE §A.4): GET /login (panen
        authenticity_token + timestamp/timestamp_secret + honeypot SEGAR dari
        HTML) -> POST /session tanpa pace(). Bagian login fast-lane pola gasak
        (C13-C14 fase_C_fast_lane_login); rantai OAuth C12/C15/C16 sengaja tidak
        ikut — jalur lanjutan pipeline kita = 2FA, bukan OAuth. Return response
        POST /session (sukses = 302)."""
        url_login = "https://github.com/login"
        if return_to:
            url_login += "?return_to=" + quote(return_to, safe="")
        r = self.s.get(url_login, headers=hdr_doc(referer))
        tok = form_tokens(r.text or "")
        if not (tok["authenticity_token"] and tok["timestamp"]):
            return r          # drift halaman /login — biarkan pemanggil memutuskan
        body = urlencode([
            ("commit", "Sign in"),
            ("authenticity_token", tok["authenticity_token"]),
            ("add_account", ""),
            ("login", email),
            ("password", pw),
            ("webauthn-conditional", "undefined"),
            ("javascript-support", "true"),
            ("webauthn-support", "supported"),
            ("webauthn-iuvpaa-support", "unsupported"),
            ("return_to", return_to or ""),
            ("allow_signup", ""),
            (tok["honeypot"] or "required_field_none", ""),
            ("timestamp", tok["timestamp"]),
            ("timestamp_secret", tok["timestamp_secret"]),
        ])
        return self.s.post("https://github.com/session", headers=hdr_form(url_login),
                           data=body, allow_redirects=False)


import re as _re
def rt_dari(body):
    """'i' (auto-solve) | 'c' (challenge/rotasi!) | 'b' (blokir) | None (bersih)."""
    m = _re.search(r"dd=\{'rt':'([icb])'", body or "")
    return m.group(1) if m else None


RE_ROBOT = _re.compile(r"temporarily restricted|captcha__robot|unusual activity", _re.I)


def gh_fetch_nonce(html):
    """Meta x-fetch-nonce ('v2:<uuid>', sekali pakai per load) + client-version
    (halaman OTP & login — pola terbukti stage1_http.gh_fetch_nonce)."""
    html = html or ""

    def satu(p):
        m = _re.search(p, html)
        return m.group(1) if m else ""

    nonce = satu(r'<meta name="fetch-nonce" content="([^"]+)"')
    cv = (satu(r'<meta name="github-client-version" content="([^"]+)"')
          or satu(r'"clientVersion":"([^"]+)"'))
    return {"nonce": nonce, "client_version": cv}


def alasan_ip(body):
    """reasonIP=<exit-IP> dari halaman blokir (kertas kerja 87/87) — utk log forensik."""
    m = _re.search(r"reasonIP=([0-9a-fA-F.:]+)", body or "")
    return m.group(1) if m else ""


def varian_gate(body):
    """'robot' | 'slider' | 'device-check' | None (B.1(7): JANGAN vonis robot-block
    hanya dari rt='c'). robot = teks blokir; slider = dd t:'bv' / iframe captcha
    (bisa diselesaikan — jangan bakar IP!); device-check = t:'fe' (diamkan)."""
    body = body or ""
    if RE_ROBOT.search(body):
        return "robot"
    if rt_dari(body) != "c":
        return None
    if (_re.search(r"['\"]t['\"]\s*:\s*['\"]bv['\"]|[?&]t=bv\b", body)
            or "geo.captcha-delivery.com/captcha" in body):
        return "slider"
    if _re.search(r"['\"]t['\"]\s*:\s*['\"]fe['\"]|[?&]t=fe\b", body):
        return "device-check"
    return None


def vonis_tambahan(v, body):
    """Perbaiki vonis watchdog utk varian gate (playbook B.3 — D4):
    robot-block -> fail-cepat + re-roll IP (tanpa tunggu/slider); t=bv ->
    'slider' (selesaikan; JANGAN jatuh ke vonis 'interstitial' yang menyuruh
    tunggu); selain itu pertahankan vonis awal."""
    var = varian_gate(body)
    if var == "robot":
        return "robot-block"
    if var == "slider":
        return "slider"
    return v


def kalibrasi_sesi(daftar_px, maks=3, email=None):  # R3: dulu 8-12 = 300-1300 KB/akun terbakar
    """Putar proxy sampai menemukan sesi LAYAK (200 atau rt='i').
    rt='c' = kelas challenge yang dibakar percuma -> langsung rotasi (vonis
    kertas kerja gasak). Log ringkas rt per sesi + BERHENTI AWAL bila >=3 sesi
    berturut rt:'c' (pool sedang buruk -> ganti pool, jangan habis-habisan) (D7).
    Return (sesi, r, px_ke)."""
    catatan, beruntun_c = [], 0
    for n in range(min(maks, len(daftar_px) or 1)):
        # [jaringan] daftar_px kosong/berisi None -> jatuh ke pool geonode, BUKAN
        # GhSesi(None) yang dulu nembus direct saat proxies.txt gagal dimuat.
        px = (daftar_px[n % len(daftar_px)] if daftar_px else None) or proksi_pertama()
        try:
            s = GhSesi(_sederhana(px))
            r = s.entry_dan_gate(email=email)
            tok = form_tokens(r.text or "")
            rt = rt_dari(r.text or "")
            if tok.get("authenticity_token"):
                catatan.append(f"{n}:rt={rt or '-'}/token")
                print(f"[kalibrasi] sesi ke-{n} layak (rt:{rt or '-'}); "
                      f"ringkas: {' '.join(catatan)}", flush=True)
                return s, r, n
            if rt == "i" or (rt is None and r.status_code == 200):
                catatan.append(f"{n}:rt={rt or '-'}/{r.status_code}")
                print(f"[kalibrasi] sesi ke-{n} layak (rt:{rt or '-'}); "
                      f"ringkas: {' '.join(catatan)}", flush=True)
                return s, r, n
            if rt is None:
                print(f"[kalibrasi] sesi aneh (status {r.status_code}) -> ganti", flush=True)
                catatan.append(f"{n}:aneh/{r.status_code}")
            else:
                catatan.append(f"{n}:rt={rt}/{r.status_code}")
                print(f"[kalibrasi] sesi rt:'{rt}' -> ganti proxy ({n+1})", flush=True)
            beruntun_c = beruntun_c + 1 if rt == "c" else 0
            if beruntun_c >= 3:
                print(f"[kalibrasi] >=3 sesi berturut rt:'c' — pool sedang buruk, "
                      f"STOP lebih awal ({n+1} sesi): {' '.join(catatan)}", flush=True)
                break
        except Exception as e:
            catatan.append(f"{n}:galat")
            print(f"[kalibrasi] sesi galat {type(e).__name__} -> ganti", flush=True)
    return None, None, -1


def _sederhana(px):
    if not px:
        return None
    if isinstance(px, str):
        return px
    return f"http://{px['username']}:{px['password']}@{px['server'].split('//', 1)[-1]}"
