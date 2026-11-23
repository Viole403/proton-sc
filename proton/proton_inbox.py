#!/usr/bin/env python3
"""proton_inbox.py — ambil kode OTP dari kotak masuk Proton (MANDIRI, tanpa
import proyek proton). Pola terbukti: browser = DEKRIPTOR (login resmi -> teks
terdekripsi aplikasi; badan email Proton PGP-encrypted utk API telanjang).
Dipakai github_signup utk kode verifikasi GitHub.

REVISI (RE D3 + D14):
  * Sesi DIPISAH: SesiInbox.mulai() = login + muat inbox SEKALI di awal akun
    (sebelum POST /signup); SesiInbox.cari_kode() = poll RINGAN (dulu tiap
    ambil_otp login ulang penuh 40-60 dtk di dalam jendela kritis).
  * Klik pesan diurutkan dari SUBJEK "Your GitHub launch code" (D14) — dulu
    tebak 'github' lalu pesan terbaru apa pun.
  * Kode = 8 digit numerik (r"\\b(\\d{8})\\b") — bukti capture: 13013442
    (RE §A.2), bukan \\d{6,8} yang rawan salah tangkap.
  * Polling AGRESIF di awal (3-4 dtk) karena email terbukti tiba ±1 dtk
    setelah write akun (RE §A.2) — pola lama jeda=15 dtk menambah 30-90 dtk
    percuma (RE §A.5).

Sesi berjalan di THREAD SENDIRI (playwright sync API tidak boleh bersarang
dalam satu thread) — thread pemanggil bebas memegang browser GitHub.

Pakai langsung:
  python proton_inbox.py --mail a@proton.me --pass 'xxx' --dari github
Sumbu data: baris registered.txt (email|pass|refresh|client) — "nyedot" file
hasil proton-e2e/results/registered.txt.
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
import argparse
import os
import re
import sys
import threading
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

import fp_lite
import gh_api

REGISTERED = str(konfig.REGISTERED)  # [edit brutalism] path terpusat
SUBJEK_GITHUB = "Your GitHub launch code"      # subjek email OTP (RE §A.2)
POLA_KODE = r"\b(\d{6}|\d{8}|\d{3,4}-\d{3,4})\b"   # [fix final] kode device-verify BERSTRIP (12 surat nyangkut tak terbaca!)               # [fix 2026-10-06] launch code kini
                                               # 6 digit (bukti otp-halaman.txt:
                                               # "Digit 1..6") — pola 8-digit lama
                                               # tak pernah cocok => "OTP tak datang"


# ---- [jaringan 2026-10-06] rantai rute inbox: TIDAK PERNAH direct diam-diam ----
# Urutan sumber (sesuai permintaan: env dulu, lalu parameter):
#   1. env SIGNUP_PROXY  = router cache lokal proton-e2e (aset dari cache, hemat
#      kuota; ditulis stok_cb: http://127.0.0.1:8081) -> trafik non-cache diteruskan
#      ke upstream geonode (bukan direct utk host *.proton.me).
#   2. env INBOX_PROXY   = override rute inbox.
#   3. param `proxy=`    = proxy geonode yang diberikan pemanggil (dict/URL/baris
#      host:port:user:pass).
#   4. pool data/proxies.txt (gh_api.proksi_pertama) = jaring pengaman terakhir.
# Semua kosong -> RuntimeError (TOLAK egress direct) — kecuali eksplisit:
# izinkan_direct=True / env IZINKAN_DIRECT=1. Meter jujur = byte wlp3s0.


def _ke_dict_px(sumber):
    """Sumber (dict proxy / URL http://user:pass@host:port / baris geonode
    host:port:user:pass / host:port polos) -> dict Playwright {server,username,password}."""
    if isinstance(sumber, dict):
        srv = sumber.get("server") or ""
        return {"server": srv if "://" in srv else "http://" + srv,
                "username": sumber.get("username"),
                "password": sumber.get("password")}
    s = (sumber or "").strip()
    if not s:
        return None
    if "://" not in s and s.count(":") >= 3:          # baris geonode mentah
        host, port, u, pw = s.split(":", 3)
        return {"server": f"http://{host}:{port}", "username": u, "password": pw}
    u = urlsplit(s if "://" in s else "http://" + s)
    return {"server": f"{u.scheme}://{u.hostname}:{u.port}",
            "username": u.username, "password": u.password}


def tentukan_proxy(proxy=None, izinkan_direct=False):
    """Rantai rute inbox (lihat blok komentar di atas) -> dict proxy Playwright.
    Raise RuntimeError bila nihil & direct tak diizinkan — pemanggil TIDAK boleh
    membuat context browser tanpa hasil fungsi ini."""
    if os.environ.get("IZINKAN_DIRECT") == "1":
        izinkan_direct = True
    for sumber in (os.environ.get("SIGNUP_PROXY"), os.environ.get("INBOX_PROXY"),
                   proxy, gh_api.proksi_pertama()):
        px = _ke_dict_px(sumber)
        if px and px.get("server"):
            return px
    if izinkan_direct:
        return None
    raise RuntimeError("tidak ada proxy (SIGNUP_PROXY/INBOX_PROXY/param proxy=/"
                       " pool geonode) — menolak egress direct; isi env atau proxy=")


def url_proxy(proxy=None, izinkan_direct=False):
    """Rantai sama, bentuk URL `http://[user:pass@]host:port` (utk curl_cffi)."""
    px = tentukan_proxy(proxy, izinkan_direct=izinkan_direct)
    return gh_api.ke_url(px) if px else None


def _login(pg, mail, pw, timeout=120):
    pg.goto("https://account.proton.me/login", wait_until="domcontentloaded", timeout=120000)
    pg.wait_for_selector("#username, input[type=email], input[type=text]", timeout=60000)   # [fix] SPA lambat via proxy — dulu sleep 3 lalu fill = timeout
    pg.locator("#username, input[type=email], input[type=text]").first.fill(mail)
    pg.locator("#password, input[type=password]").first.fill(pw)
    pg.locator("button[type=submit]").first.click()
    akhir = time.time() + timeout
    t = ""
    while time.time() < akhir:
        time.sleep(2)
        if any(s in pg.url for s in ("/mail", "/inbox", "/apps", "/u/")):
            return True
        t = (pg.evaluate("() => (document.body.innerText||'').slice(0,300)") or "").lower()
        if "suspend" in t or "not correct" in t:
            print(f"[inbox] login DITOLAK: url={pg.url[:90]} | {t[:120]}", flush=True)
            return False
    print(f"[inbox] login TIMEOUT 120dtk: url={pg.url[:90]} | {t[:150]}", flush=True)
    return False


def _muat_inbox(pg, dari):
    """Prolog SEKALI saja (identik perilaku lama): muat inbox + filter pencarian."""
    pg.goto("https://mail.proton.me/inbox", wait_until="domcontentloaded",
            timeout=120000)
    if "mail" not in pg.url:        # kadang mendarat di app-chooser/settings
        try:
            pg.get_by_text("Proton Mail", exact=False).first.click(timeout=5000)
            time.sleep(4)
        except Exception:
            pass
    time.sleep(7)
    try:
        cari = pg.locator("input[type=search], input[placeholder*='search' i]").first
        if cari.count():
            cari.fill(dari)
            cari.press("Enter")
            time.sleep(5)
    except Exception:
        pass


def _buka_pesan(pg, dari, subjek=SUBJEK_GITHUB):
    """Buka pesan OTP dgn urutan klik berbasis SUBJEK (D14):
    'Your GitHub launch code' -> pengirim -> pesan terbaru (jalur cadangan lama).
    Timeout pendek: ini loop poll ringan, bukan startup."""
    if subjek:
        try:
            pg.get_by_text(subjek, exact=False).first.click(timeout=3000)
            time.sleep(2.5)
            return
        except Exception:
            pass
    try:
        pg.get_by_text(dari, exact=False).first.click(timeout=3000)
        time.sleep(2.5)
        return
    except Exception:
        pass
    try:   # jalur cadangan: klik pesan TERBARU apa pun
        pg.locator("li[class*=item], [role=row], [class*=message]").first.click(timeout=2500)
        time.sleep(2)
    except Exception:
        pass


def _ekstrak_kode(pg, pola):
    isi = pg.evaluate(
        "() => (document.querySelector('[role=main]')||document.body).innerText") or ""
    m = re.search(pola, isi)
    return m.group(1) if m else None


class SesiInbox:
    """Sesi inbox Proton yang dipisah (D14):
      mulai()     -> login + muat inbox (sekali di awal akun; jalan di thread khusus)
      cari_kode() -> poll ringan sampai kode ketemu (jadwal agresif di awal)
      tutup()     -> matikan browser (dipanggil aman berkali-kali)
    Thread khusus = main thread bebas memegang browser GitHub tanpa playwright
    bersarang (bug lama yang dicatat github_signup)."""

    MINTA_TIMEOUT = 780          # batas tunggu perintah cari_kode -> tutup sendiri
    TUNGGU_SIAP = 300            # budget login + muat inbox (link throttle: 45s sering kurang)

    def __init__(self, mail, pw, dari="github", subjek=SUBJEK_GITHUB, headless=True,
                 proxy=None):
        self.mail, self.pw, self.dari, self.subjek = mail, pw, dari, subjek
        self.headless = headless
        self.proxy = proxy        # [jaringan] opsional; fallback: env SIGNUP_PROXY
                                  # -> INBOX_PROXY -> pool geonode (anti-direct)
        self.pola = POLA_KODE
        self.jadwal = []
        self.siap = threading.Event()
        self.minta = threading.Event()
        self.selesai = threading.Event()
        self.batal = False
        self.siap_ok = False
        self.hasil = None
        self._t = threading.Thread(target=self._jalan, daemon=True,
                                   name=f"inbox-{mail.split('@')[0][:12]}")

    def mulai(self):
        self._t.start()
        return self

    def _jalan(self):
        try:
            px = tentukan_proxy(self.proxy)   # [jaringan] WAJIB dapat rute — env
            prof = fp_lite.build_persona()    # (router cache) / param / pool geonode
            import cdp_gate   # [integrasi kolam] 1 Chrome bersama + N konteks
            b = cdp_gate.pinjam_browser(proxy=px, headless=self.headless)
            if True:          # jaga struktur indent blok lama
                try:
                    ctx = b.new_context(locale=prof["locale"],
                                        timezone_id="Europe/Berlin",
                                        viewport=prof["viewport"],
                                        user_agent=prof["ua"],
                                        proxy=px)
                    pg = ctx.new_page()
                    if not _login(pg, self.mail, self.pw):
                        print("[inbox] login proton gagal", flush=True)
                        return
                    _muat_inbox(pg, self.dari)
                    self.siap_ok = True
                    self.siap.set()
                    diminta = self.minta.wait(self.MINTA_TIMEOUT)
                    if self.batal or not diminta:
                        return
                    self.hasil = self._ekstrak(pg)
                finally:
                    try:
                        b.close()
                    except Exception:
                        pass
        except Exception as e:
            print(f"[inbox] sesi gagal ({type(e).__name__}: {str(e)[:60]})", flush=True)
        finally:
            self.siap.set()
            self.selesai.set()

    def _ekstrak(self, pg):
        for tunggu in self.jadwal:
            if self.batal:
                return None
            _buka_pesan(pg, self.dari, self.subjek)
            kode = _ekstrak_kode(pg, self.pola)
            if kode:
                return kode
            time.sleep(tunggu)
        return None

    def tunggu_siap(self, timeout=None):
        self.siap.wait(self.TUNGGU_SIAP if timeout is None else timeout)
        return self.siap_ok

    def cari_kode(self, pola=POLA_KODE, ulang=36, jeda=10):
        """Poll ringan sampai kode ketemu; None = tak datang / sesi tutup.
        Jadwal: 3-4 dtk di AWAL (email tiba ±1 dtk pasca-signup) lalu `jeda`."""
        if not self.tunggu_siap():
            return None
        self.pola = pola
        n = max(1, int(ulang))
        self.jadwal = ([3] * min(4, n) + [4] * min(4, max(0, n - 4))
                       + [jeda] * max(0, n - 8))
        self.minta.set()
        budget = sum(self.jadwal) + 18.0 * len(self.jadwal)   # + overhead klik/gagal
        self.selesai.wait(max(60.0, budget))
        return self.hasil

    def tutup(self):
        self.batal = True
        self.minta.set()
        try:
            self._t.join(timeout=10)
        except Exception:
            pass


def ambil_otp(mail, pw, dari="github", pola=POLA_KODE, ulang=36, jeda=10,
              headless=True, proxy=None, subjek=None):
    """Kembalikan kode OTP 8-digit dari email terbaru pengirim `dari`; None bila
    gagal. Antarmuka TETAP kompatibel pemanggil lama (run.py memasang ulang nama
    ini); kini di dalamnya = SesiInbox + poll agresif. Param `proxy=` opsional
    (dict/URL/baris geonode) — fallback: env SIGNUP_PROXY -> INBOX_PROXY ->
    pool geonode; tidak pernah direct tanpa eksplisit (IZINKAN_DIRECT=1).
    Param `subjek=` opsional (default SUBJEK_GITHUB) — utk saksi 2FA
    'just enabled two-factor' (resep lab3 02-resep-teknis §3.3)."""
    s = SesiInbox(mail, pw, dari=dari, subjek=subjek or SUBJEK_GITHUB,
                  headless=headless, proxy=proxy).mulai()
    if not s.tunggu_siap():
        s.tutup()
        return None
    try:
        return s.cari_kode(pola=pola, ulang=ulang, jeda=jeda)
    finally:
        s.tutup()


_AMBIL_OTP_ASLI = ambil_otp
# Penanda utk github_signup: bila proton_inbox.ambil_otp BUKAN objek ini, berarti
# run.py sudah memasang penghangat eksternal (InboxHangat) — jangan login dua kali.


def baca_akun(register_path=REGISTERED):
    """Semua baris registered.txt -> list dict {mail, pw, rt, cid} (nyedot hasil
    proton; kolom refresh/client disimpan utk cek-token cepat & jalur API nanti)."""
    out = []
    for ln in open(register_path):
        ln = ln.strip()
        if not ln:
            continue
        mail, pw, rt, cid = (ln.split("|") + [""] * 4)[:4]
        if mail and pw:
            out.append({"mail": mail, "pw": pw, "rt": rt, "cid": cid})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mail"), ap.add_argument("--pass", dest="pw")
    ap.add_argument("--dari", default="github")
    ap.add_argument("--ulang", type=int, default=25)
    ap.add_argument("--jeda", type=float, default=5)
    ap.add_argument("--proxy", default=None,
                    help="URL/baris geonode utk rute inbox (fallback: env "
                         "SIGNUP_PROXY -> INBOX_PROXY -> data/proxies.txt)")
    a = ap.parse_args()
    if not a.mail:
        akun = baca_akun()
        print(f"{len(akun)} akun tersedia dari {REGISTERED}")
        ap.print_help()
    else:
        print("OTP:", ambil_otp(a.mail, a.pw, dari=a.dari, ulang=a.ulang, jeda=a.jeda,
                               proxy=a.proxy))
