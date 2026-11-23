#!/usr/bin/env python3
"""run.py — runner batch GitHub e2e (mandiri).
Disiplin (permintaan user): RAM sehat (1 Chrome/worker, context/akun, close wajib),
byte proxy/direct terhitung per akun, 1 proxy = 1 akun, jeda antar akun.
  python run.py --accounts 5 --workers 3 [--proxy-file data/proxies.txt] [--live]
Data akun: "nyedot" proton-e2e/results/registered.txt (email|pass|refresh|client).
Keluaran: results/github-combo.txt  =  mail|pass|totp_secret

OPTIMASI (patch performa & stabilitas — prinsip: TINNGALKAN kecepatan bila
mengancam keberhasilan; tiap jalur cepat punya fallback ke perilaku lama):
  [P1] PIPELINE GATE — gate gasak (140-190 dtk/akun) di-pra-produksi di latar
       belakang oleh gasak_bridge (lihat docstring-nya) dan tumpang tindih dgn
       OTP/2FA akun sebelumnya -> gate ~0 dtk di jalur kritis. Antre kosong /
       artefak basi -> otomatis dry-run sinkron seperti dulu.
  [P2] INBOX PROTON PRA-HANGAT — login Proton (±40-60 dtk/akun) dikerjakan
       saat akun masih di fase gate/C5-C8; saat kode OTP diminta, tinggal
       baca pesan. Fase ekstraksi = salinan persis loop proton_inbox.ambil_otp.
       Gagal kapan pun -> fallback ke proton_inbox.ambil_otp ASLI (sesi baru).
  [P3] RAM-GUARD — port pola terbukti batch_parallel.py (komparator): tunda
       klaim akun bila MemAvailable < --min-ram (default 1500 MB) — cegah N
       Chrome lahir serempak di atas swap penuh (penyebab crash CDP).
  [P4] RETRY INFRA (maks --ulang-infra, default 1) dgn jendela proxy BARU —
       pola klasifikasi INFRA/REAL batch_parallel.py: gate/init_failed/OTP tak
       datang/timeout/eksepsi/robot-block = ganti IP + ulang, BUKAN buang
       akun (di sana 4 crash salah klasifikasi jadi REAL tanpa retry).
       Kegagalan REAL (drift token/TOTP) tetap tidak diulang.
  [P5] Jendela proxy per akun kini WRAP-AROUND (dulu slice i*12 bisa KOSONG
       utk akun ke->83 -> ZeroDivisionError di kalibrasi_sesi), dan tiap retry
       memakai jendela berbeda (jaminan "1 proxy = 1 akun" tetap).
  [P6] KLASIFIKASI RETRYABLE DIPERBAIKI (cetak biru P3): `C8 4xx`, `submit OTP
       gagal` (nonce basi), `login-ulang gagal` = INFRA (retry), BUKAN REAL
       (3 kasus itu yg men-ggugurkan 5/9 akun run-e2e10). Retry INFRA 1-2x
       KERAS (pulih 36,6% utk retry 1-2; ke-3 nihil [terukur]) + IDEMPOTENSI:
       pool.tandai_dipakai SEBELUM retry (dulu sesudah loop -> konsumsi akun
       lolos ditandai saat attempt-2 gagal) + dedupe results/*-combo.txt.
  [P7] METERAN BYTE PER TAHAP (cetak biru P4): bandwidth.BandwidthMonitor
       (label `stage`: masuk+gate <=150 KB, signup <=800 KB, otp+lanjut
       <=550 KB — total <=1,5 MB/akun) + meter ambang 80% + alarm tren 3-run +
       laporan ringkas per run. Label tahap otomatis (pasang_label_tahap —
       balut batas tahap, tanpa menyentuh github_signup/gh_api); byte browser
       lewat asset_cache (P3: cache aset statis + blokir chrome-system/ML).
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
import glob
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bandwidth
import fp_lite
import gh_api            # [jaringan] pool geonode + jaminan anti-direct
import cdp_gate as gate   # raw-CDP (tanpa Playwright/flag GL) — label tahap menempel ke sini
import github_signup
import proton_inbox


def muat_proxy(path):
    """[edit brutalism] validasi format ketat + LAPORKAN baris rusak (dulu senyap:
    file salah format = "0 proxy" diam-diam, meledak belakangan di jendela_proxy)."""
    hasil = konfig.validasi_proksi(path)
    for no, baris, alasan in hasil["tolak"]:
        print(f"[proxy] TOLAK baris {no}: {alasan}", flush=True)
    out = []
    for baris in hasil["ok"]:
        host, port, user, pw = baris.split(":", 3)
        out.append({"server": f"http://{host}:{port}", "username": user, "password": pw})
    muat_proxy.terakhir = hasil
    return out


# --------------------------------------------------------------- RAM-guard
def mem_tersedia_mb():
    try:
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) / 1024
    except Exception:
        pass
    return None


def jaga_ram(min_mb=1500.0, maks_tunggu=8, log=print):
    """P3 — tunda klaim akun selama RAM sesak (identik doktrin batch_parallel:
    klaim DITUNDA dulu, bukan diambil lalu menganggur). False = menyerah
    (lanjut saja — RAM tak terbaca bukan alasan mematikan batch)."""
    for _ in range(maks_tunggu):
        m = mem_tersedia_mb()
        if m is None or m >= min_mb:
            return True
        t = random.uniform(15, 30)
        log(f"[RAM-GUARD] MemAvailable {m:.0f} MB < {min_mb:.0f} MB — tunda {t:.0f}dtk")
        time.sleep(t)
    return False


# ------------------------------------------------- klasifikasi INFRA vs REAL
# Semangat identik batch_parallel.py: kegagalan INFRASTRUKTUR (ganti IP/proses
# mati/OTP tak datang) layak retry; kegagalan NYATA (drift form/TOTP) tidak.
# P3 cetak biru (berbukti run-e2e10: 5/9 akun gugur di 3 kasus ini): `C8 4xx`,
# `submit OTP gagal` (x-fetch-nonce basi -> reload halaman ulang = khas
# infrastruktur), `login-ulang gagal` = RETRYABLE/INFRA, bukan REAL.
_RE_INFRA = re.compile(
    r"gate gasak|init_failed|tak menyimpan artefak|token form|tak ada sesi proxy|"
    r"terbakar|robot-block|geo .*mismatch|octocaptcha|OTP inbox tak datang|"
    r"C8 5\d\d|C8 4\d\d|signed in with another tab|submit OTP gagal|login ulang pasca-OTP gagal|login-ulang|"
    r"timeout|timed out|eksepsi", re.I)
_RE_REAL = re.compile(r"TOTP secret|konfirmasi TOTP|kolom OTP|\[DRY-RUN\]", re.I)


def klasifikasi(sebab):
    if _RE_REAL.search(sebab or ""):
        return "REAL"
    return "INFRA" if _RE_INFRA.search(sebab or "") else "REAL"


_POOL_DARURAT = [None]          # cache pool geonode utk jaring pengaman anti-direct


def _pool_darurat():
    """data/proxies.txt (cache sekali baca) -> list dict exit geonode."""
    if _POOL_DARURAT[0] is None:
        _POOL_DARURAT[0] = gh_api.muat_pool(os.path.join(HERE, "data", "proxies.txt"))
    return _POOL_DARURAT[0]


def jendela_proxy(proksi, i, percobaan=0, lebar=12):
    """P5 — 12 proxy/akun utk kalibrasi + browser (pola lama proksi[i*12:]),
    kini wrap-around (tak akan pernah kosong) dan BERBEDA tiap retry (rotasi
    IP utk kegagalan INFRA). [jaringan 2026-10-06] TANPA proksi TIDAK lagi
    jatuh direct: fallback pool geonode data/proxies.txt; pool pun kosong ->
    RuntimeError (direct hanya utk kasus yang memang disengaja)."""
    if not proksi:
        proksi = _pool_darurat()
        if not proksi:
            raise RuntimeError("jendela_proxy: pool geonode kosong — menolak "
                               "egress direct (isi --proxy-file / data/proxies.txt)")
    n = len(proksi)
    mulai = (i + percobaan * 977) * lebar % n      # 977: lompatan ganjil antar pool
    jendela = [proksi[(mulai + k) % n] for k in range(min(lebar, n))]
    try:   # [exit hijau] dahulukan exit yang gerbangnya pernah tembus cepat
        import os as _os
        hj = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                           "data", "exit-hijau.nonaktif")
        if _os.path.exists(hj):
            if _os.environ.get("EXPERIMEN_KERAS") == "1":
                # [EXPERIMEN 2026-10-07] bobot FREKUENSI + 8 slot — uji cepat
                hitung = {}
                for l in open(hj):
                    if "|" in l:
                        s = l.split("|", 1)[1].strip()
                        hitung[s] = hitung.get(s, 0) + 1
                isi = [s for s, _n in sorted(hitung.items(), key=lambda x: -x[1])]
                slot = 8
            else:
                isi = [l.split("|", 1)[1].strip() for l in open(hj)][-40:]
                slot = 4                       # METODE LAMA (terbukti 80% uji-20)
            dahulu = [p for p in proksi if str(p) in isi]
            if dahulu:
                jendela = dahulu[:slot] + [p for p in jendela
                                          if p not in set(map(str, dahulu))]
    except Exception:
        pass
    return jendela


# ------------------------------------------------- P3: idempotensi *-combo.txt
_KUNCI_BERKAS = threading.Lock()


def _baris_ada(jalur, kunci):
    """Cek baris dgn FIELD-1 == kunci (bukan substring — mail pendek bisa
    nyangkut di mail lain) pada berkas berformat pipe."""
    try:
        return any(l.split("|")[0].strip() == kunci for l in open(jalur) if l.strip())
    except Exception:
        return False


def tulis_combo_unik(jalur, baris, kunci):
    """P3 IDEMPOTENSI: 1 baris per `kunci` (mail) di *-combo.txt — retry/re-run
    tak boleh menggandakan (dulu append buta / cek substring tanpa kunci dan
    tanpa kunci-thread -> rebutan antar worker). Return True bila baris baru."""
    with _KUNCI_BERKAS:
        if _baris_ada(jalur, kunci):
            return False
        os.makedirs(os.path.dirname(jalur), exist_ok=True)
        with open(jalur, "a") as f:
            f.write(baris + "\n")
        return True


def dedupe_combo():
    """P3: rapikan results/*-combo.txt dari baris dobel mail (PERTAMA menang —
    jejak pertama = yang diverifikasi) sekali di akhir run. Return jml dibuang."""
    buang = 0
    for jalur in glob.glob(os.path.join(HERE, "results", "*-combo.txt")):
        with _KUNCI_BERKAS:
            lihat, unik = set(), []
            for l in open(jalur).read().splitlines():
                if not l.strip():
                    continue
                k = l.split("|")[0].strip()
                if k in lihat:
                    buang += 1
                    continue
                lihat.add(k)
                unik.append(l)
            if buang:
                with open(jalur, "w") as f:
                    f.write("\n".join(unik) + ("\n" if unik else ""))
    return buang


# ------------------------------- P4: label tahap otomatis + meter codebuddy
_RUN_GATE_TERCATAT = set()          # run gasak yang byte-nya sudah masuk T1
_KUNCI_GATE = threading.Lock()


def _balut_metode(cls, nama, sebelum=None, sesudah=None):
    """Balut method kelas dgn pindah label tahap (delegasi murni — hasil & galat
    persis asli). Dipasang sekali; aman dipanggil berulang."""
    asli = getattr(cls, nama, None)
    if asli is None or getattr(asli, "_tahap_terbalut", False):
        return

    def _bungkus(self, *a, **k):
        if sebelum:
            sebelum()
        r = asli(self, *a, **k)
        if sesudah:
            sesudah()
        return r

    _bungkus.__name__ = nama
    _bungkus._tahap_terbalut = True
    setattr(cls, nama, _bungkus)


def _balut_fungsi(modul, nama, sebelum=None, sesudah=None):
    asli = getattr(modul, nama, None)
    if asli is None or getattr(asli, "_tahap_terbalut", False):
        return

    def _bungkus(*a, **k):
        if sebelum:
            sebelum()
        r = asli(*a, **k)
        if sesudah:
            sesudah()
        return r

    _bungkus.__name__ = nama
    _bungkus._tahap_terbalut = True
    setattr(modul, nama, _bungkus)


def pasang_label_tahap():
    """P4 — label tahap OTOMATIS tanpa menyentuh github_signup/gh_api: balut
    batas tahap yang memang sudah dipanggil akun_baru. Urutan byte:
      masuk+gate (kalibrasi+entry+gate, default) -> signup (C7/C5/C6) ->
      otp+lanjut (setelah C8; halaman OTP+Proton+login-ulang+codebuddy) ->
      balik masuk+gate (panen token ulang saat C8 422) -> dst.
    Byte gate dry-run gasak (proses terpisah, ~140 KB) diambil dari
    runs/*/bandwidth.json-nya (terbaik-upaya, ditandai '~')."""
    import gh_api
    import proton_api

    t_signup = bandwidth.T_SIGNUP
    _balut_metode(gh_api.GhSesi, "c7_username", sebelum=lambda: bandwidth.tahap(t_signup))
    _balut_metode(gh_api.GhSesi, "c5_email", sebelum=lambda: bandwidth.tahap(t_signup))
    _balut_metode(gh_api.GhSesi, "c6_password", sebelum=lambda: bandwidth.tahap(t_signup))

    t_otp = bandwidth.T_OTP_LANJUT
    _balut_metode(gh_api.GhSesi, "c8_signup", sesudah=lambda: bandwidth.tahap(t_otp))
    _balut_fungsi(proton_api, "baca_otp", sebelum=lambda: bandwidth.tahap(t_otp))

    _ambil_gate_asli = gate.ambil_gate   # TANGKAP dulu — jangan rekursif

    def _ambil_gate_tahap(username, *a, **k):
        bandwidth.tahap(bandwidth.T_MASUK_GATE)
        if not k.get("proxy") and os.environ.get("IZINKAN_DIRECT") != "1":
            # [jaringan] github_signup bisa meneruskan proxy=None (akun_baru
            # dipanggil tanpa jendela) -> browser gate nyungsep direct. Isi exit
            # geonode darurat (dict run.muat_proxy — diterima px_forward.Forwarder)
            # sebelum masuk; argumen lain TIDAK disentuh.
            k["proxy"] = gh_api.proksi_pertama()
        hasil = _ambil_gate_asli(username, *a, **k)
        try:                      # [meter jujur] byte browser tunnel ikut dihitung
            up, dn = gate.byte_terakhir
            if up or dn:
                bandwidth.catat(up + dn, host="~browser-gate")
        except Exception:
            pass
        return hasil

    if not getattr(gate.ambil_gate, "_tahap_terbalut", False):
        _ambil_gate_tahap._tahap_terbalut = True
        gate.ambil_gate = _ambil_gate_tahap


def pasang_meter_codebuddy():
    """P4/P3 — cache + meter browser CodeBuddy TANPA menyentuh codebuddy_phase.py
    (sedang diedit paralel): subclass TokenMint (delegasi murni) yang menempel
    asset_cache (cache aset statis + blokir chrome-system/ML + meteran byte
    per tahap) begitu browser lahir; dipasang dgn mengganti nama kelas modul
    (pola sama inbox_pasang). CSS/JS codebuddy TIDAK diblokir (risk-context
    400) — hanya di-cache; media sampah cukup sekali lewat proxy. Gagal ->
    meteran codebuddy nihil, perilaku 100% lama."""
    try:
        import codebuddy_phase as cb
        if getattr(cb.TokenMint, "_meter_terpasang", False):
            return True
        mint_asli = cb.TokenMint

        class MintMeter(mint_asli):
            _meter_terpasang = True

            def _pastikan_browser(self):
                ok = super()._pastikan_browser()
                try:
                    if ok and self._pg is not None and not getattr(self, "_meter_ok", False):
                        import asset_cache
                        asset_cache.pasang(self._pg)
                        self._meter_ok = True
                except Exception:
                    pass
                return ok

        cb.TokenMint = MintMeter
        return True
    except Exception:
        return False


# --------------------------------------------- P2: inbox Proton pra-hangat
_AMBIL_OTP_ASLI = proton_inbox.ambil_otp
_INBOX = {}
_KUNCI_INBOX = threading.Lock()


class InboxHangat:
    """Login Proton + muat inbox di LATAR BELAKANG (fase 1, saat akun masih di
    gate/C5-C8); ekstraksi kode (fase 2) dijalankan SAAT github_signup meminta
    OTP. Fase 2 = salinan persis loop proton_inbox.ambil_otp (urutan klik +
    regex identik) supaya hasil sama; yang diubah hanya: jeda polling adaptif
    (4/8/15 dtk, bukan 15 dtk tetap) + timeout klik lebih pendek dgn iterasi
    LEBIH BANYAK (30 vs 22) -> deteksi ~2x cepat pada poll kosong dan total
    kesabaran ±12,5 menit TETAP/SAMA dari asli (22 iter x ±34 dtk). APA PUN
    yang gagal -> hasil None -> pemanggil fallback ke ambil_otp ASLI (tak
    pernah lebih buruk)."""

    MINTA_TIMEOUT = 780          # batas tunggu fase 1 -> tutup browser sendiri

    def __init__(self, mail, pw, mon=None):
        self.mail, self.pw, self.mon = mail, pw, mon
        self.dari, self.pola, self.jeda = "github", r"\b(\d{6,8})\b", 15
        self.siap = threading.Event()
        self.minta = threading.Event()
        self.batal = False
        self.fase2 = False
        self.hasil = []
        self.utama = threading.Thread(target=self._jalan, daemon=True,
                                      name=f"inbox-{mail.split('@')[0][:12]}")
        self.utama.start()

    def _jalan(self):
        try:
            if not jaga_ram(2200.0, maks_tunggu=3,
                            log=lambda s: print(f"[inbox-hangat] {s}", flush=True)):
                raise RuntimeError("RAM sesak — biarkan jalur lama")
            from playwright.sync_api import sync_playwright
            prof = fp_lite.build_persona()
            # [jaringan 2026-10-06] WAJIB rute sadar-proxy: env SIGNUP_PROXY
            # (router cache) -> INBOX_PROXY -> pool geonode (dulu context ini
            # dibuat TANPA proxy = egress direct membakar kuota hotspot).
            px = proton_inbox.tentukan_proxy()
            with sync_playwright() as p:
                b = p.chromium.launch(executable_path="/usr/bin/google-chrome",
                                      headless=True, args=fp_lite.launch_args(prof)[1:])
                try:
                    ctx = b.new_context(locale=prof["locale"],
                                        timezone_id="Europe/Berlin",
                                        viewport=prof["viewport"], user_agent=prof["ua"],
                                        proxy=px)
                    pg = ctx.new_page()
                    if self.mon is not None:
                        # P4: meter PASIF byte Proton — selalu ke tahap
                        # otp+lanjut (alur paralel: stage akun sedang berjalan
                        # di thread lain). JANGAN intercept/blokir di sini —
                        # larangan berbukti: telemetri Proton terblokir = menu
                        # HV anjlok.
                        pg.on("response", lambda resp: self.mon.pengukur_response(
                            resp, tahap=bandwidth.T_OTP_LANJUT))
                    if not proton_inbox._login(pg, self.mail, self.pw):
                        raise RuntimeError("login proton gagal")
                    # prolog IDENTIK proton_inbox.ambil_otp (muat inbox + filter)
                    pg.goto("https://mail.proton.me/inbox", wait_until="domcontentloaded",
                            timeout=45000)
                    if "mail" not in pg.url:
                        try:
                            pg.get_by_text("Proton Mail", exact=False).first.click(timeout=5000)
                            time.sleep(4)
                        except Exception:
                            pass
                    time.sleep(7)
                    try:
                        cari = pg.locator("input[type=search], input[placeholder*='search' i]").first
                        if cari.count():
                            cari.fill(self.dari)
                            cari.press("Enter")
                            time.sleep(5)
                    except Exception:
                        pass
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
            print(f"[inbox-hangat] pra-hangat gagal ({type(e).__name__}: "
                  f"{str(e)[:50]}) -> fallback sesi baru", flush=True)
        finally:
            self.siap.set()
            self.minta.set()

    def _ekstrak(self, pg):
        """Salinan persis loop proton_inbox.ambil_otp (baris 75-91) dgn polling
        adaptif; regex & pola klik TIDAK diubah (anti perubahan perilaku)."""
        self.fase2 = True
        jadwal = [4] * 4 + [8] * 6 + [self.jeda] * 20      # 30 iter, kesabaran ±12,5 mnt
        for tunggu in jadwal:
            try:
                pg.get_by_text(self.dari, exact=False).first.click(timeout=4000)
                time.sleep(4)
            except Exception:
                pass
            try:   # jalur cadangan: klik pesan TERBARU apa pun (identik asli)
                pg.locator("li[class*=item], [role=row], [class*=message]").first.click(timeout=2500)
                time.sleep(3)
            except Exception:
                pass
            isi = pg.evaluate(
                "() => (document.querySelector('[role=main]')||document.body).innerText") or ""
            m = re.search(self.pola, isi)
            if m:
                return [m.group(1)]
            time.sleep(tunggu)
        return []

    def ambil(self, dari, pola, ulang, jeda):
        self.dari, self.pola, self.jeda = dari, pola, jeda
        self.minta.set()
        # batas tunggu = kesabaran terburuk fase 2 (30 iter x ±26 dtk) + margin
        self.utama.join(timeout=820)
        return self.hasil[0] if self.hasil else None


def inbox_mulai(mail, pw, mon=None):
    with _KUNCI_INBOX:
        _INBOX[mail] = InboxHangat(mail, pw, mon=mon)


def inbox_batal(mail):
    """Bersihkan pra-hangat yang tak sempat dipakai (akun gagal sebelum OTP) —
    browser ditutup cepat, bukan menganggur menunggu timeout."""
    with _KUNCI_INBOX:
        rec = _INBOX.pop(mail, None)
    if rec is not None:
        rec.batal = True
        rec.minta.set()


def _ambil_otp_hangat(mail, pw, dari="github", pola=r"\b(\d{6})\b", ulang=8,
                      jeda=12, headless=True):
    """Pengganti proton_inbox.ambil_otp (dipasang oleh inbox_pasang): pakai
    sesi pra-hangat bila ADA; selalu punya jalan kembali ke fungsi asli."""
    bandwidth.tahap(bandwidth.T_OTP_LANJUT)   # P4: ambang OTP masuk T3
    with _KUNCI_INBOX:
        rec = _INBOX.pop(mail, None)
    if rec is not None:
        kode = rec.ambil(dari, pola, ulang, jeda)
        if kode:
            print("[inbox-hangat] kode dapat dari sesi pra-hangat", flush=True)
            return kode
        print("[inbox-hangat] pra-hangat nihil -> sesi baru (fallback aman)", flush=True)
        # bila fase-ekstraksi sempat jalan, kotak mungkin telat terisi -> satu
        # sesi ulang dgn budget SEDANG; bila pra-hangat gagal awal -> budget penuh
        return _AMBIL_OTP_ASLI(mail, pw, dari=dari, pola=pola,
                               ulang=(min(ulang, 8) if rec.fase2 else ulang),
                               jeda=jeda, headless=headless)
    return _AMBIL_OTP_ASLI(mail, pw, dari=dari, pola=pola, ulang=ulang,
                           jeda=jeda, headless=headless)


def inbox_pasang():
    proton_inbox.ambil_otp = _ambil_otp_hangat


# ------------------------------------- [jaringan] kawal rute proton_api (eksternal)
def kawal_proton_api():
    """proton_api (berkas DI LUAR hak ubah) memanggil login(mail, pw) TANPA proxy
    -> egress DIRECT ke account.proton.me (jalur fallback OTP github_signup).
    Bungkus proton_api.login dari sini: argumen proxy yang kosong diisi rantai
    rute Proton (SIGNUP_PROXY router cache -> INBOX_PROXY -> geonode pool).
    DELEGASI MURNI: argumen lain, alur SRP, dan hasil TIDAK berubah — hanya
    rute yang dipaksa. Kontrak login dipertahankan: gagal = (None, None) +
    `login.alasan` terisi (tanpa melempar)."""
    try:
        import proton_api
    except Exception:
        return False
    if getattr(proton_api.login, "_kawal_rute", False):
        return True
    _login_asli = proton_api.login

    def _login_proxy(mail, pw, proxy=None, **k):
        if not proxy and os.environ.get("IZINKAN_DIRECT") != "1":
            try:
                proxy = proton_inbox.url_proxy()   # rantai: env -> param -> pool
            except Exception as e:
                _login_proxy.alasan = f"tanpa proxy rute ({type(e).__name__})"
                return None, None
        return _login_asli(mail, pw, proxy=proxy, **k)

    _login_proxy._kawal_rute = True
    _login_proxy.__name__ = "login"
    proton_api.login = _login_proxy

    # [fix 2026-11-08] kelas bocor SAMA: _sesi_dari_refresh(proxy=None) ->
    # crq.Session(proxies=None) = request ber-sesi (cookie AUTH signup) DIRECT
    # dari IP VPS (BEDAH-2028-JARINGAN; pemanggil lupa: oneshot_pipeline.worker
    # 341 — sudah diberi proxy, ini jaring pengaman pemanggil lain). Kontrak
    # dipertahankan: tanpa rute -> (None, None), jangan lempar.
    if not getattr(proton_api._sesi_dari_refresh, "_kawal_rute", False):
        _sdr_asli = proton_api._sesi_dari_refresh

        def _sdr_proxy(mail, proxy=None):
            if not proxy and os.environ.get("IZINKAN_DIRECT") != "1":
                try:
                    proxy = proton_inbox.url_proxy()
                except Exception:
                    return None, None
            return _sdr_asli(mail, proxy=proxy)

        _sdr_proxy._kawal_rute = True
        proton_api._sesi_dari_refresh = _sdr_proxy
    return True


def pasang_patch():
    """[edit brutalism] monkeypatch EKSPLISIT — dipanggil tiap entry point.
    Dulu `kawal_proton_api()` jalan otomatis saat import (efek samping global)."""
    return kawal_proton_api()


def utama():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", type=int, default=5)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--proxy-file", default=os.path.join(HERE, "data", "proxies.txt"))
    ap.add_argument("--live", action="store_true",
                    help="eksekusi POST /signup sungguhan (default dry-run, aman)")
    ap.add_argument("--pra-gate", type=int, default=-1,
                    help="produsen gate latar belakang (0=mati/perilaku lama; "
                         "default -1 = auto min(workers,3))")
    ap.add_argument("--ulang-infra", type=int, default=2,
                    help="retry kegagalan INFRA dgn proxy baru (0=tanpa retry; "
                         "dibatasi 2 — retry ke-3 nihil [terukur cetak biru C])")
    ap.add_argument("--jeda-min", type=float, default=30.0,
                    help="jeda antar akun minimum (disiplin user; komparator "
                         "menjalankan 0 atas keputusan pemilik)")
    ap.add_argument("--jeda-max", type=float, default=60.0)
    ap.add_argument("--min-ram", type=float, default=1500.0,
                    help="MemAvailable min (MB) sebelum klaim akun (RAM-guard)")
    ap.add_argument("--tanpa-inbox-hangat", action="store_true",
                    help="matikan pra-hangat inbox Proton (pakai jalur lama)")
    a = ap.parse_args()

    pasang_patch()                      # [edit brutalism] patch eksplisit
    akun = proton_inbox.baca_akun()[:a.accounts]
    proksi = muat_proxy(a.proxy_file)
    random.shuffle(proksi)   # hindari kluster sesi mati berurutan
    kunci = threading.Lock()
    selesai = [0]
    byte_total = [0]
    hasil = []
    if not a.tanpa_inbox_hangat:
        inbox_pasang()
    bandwidth.pasang_meter_http()   # P4: takar byte HTTP pipeline (curl) per tahap
    pasang_label_tahap()            # P4: label tahap otomatis (masuk+gate/signup/otp+lanjut)
    pasang_meter_codebuddy()        # P4/P3: cache+meter browser codebuddy (tanpa sentuh berkasnya)
    pra_gate = a.pra_gate if a.pra_gate >= 0 else max(1, min(a.workers, 3))
    # [fix: gasak DIBUANG] gate kini mandiri (gate.py) — antrean pra-produksi
    # gasak (biang spam sesi evilmail + IP-agnostik) TIDAK dipakai lagi.

    def kerjakan(i, item):
        prof = fp_lite.build_persona()
        t0 = time.time()
        h = None
        # P3: retry INFRA 1-2x KERAS — pulih 36,6% utk retry ke-1/ke-2; retry
        # ke-3 nihil [terukur cetak biru C] -> jangan lebih.
        ulang = min(2, max(0, a.ulang_infra))
        maks_attempt = 1 + ulang
        for attempt in range(1, maks_attempt + 1):
            bandwidth.mulai_akun(item["mail"])    # P4: meter byte per attempt
            jaga_ram(a.min_ram)                       # P3
            if not a.tanpa_inbox_hangat:
                # P2: login duluan, paralel — P4: byte inbox dihitung T3
                inbox_mulai(item["mail"], item["pw"], bandwidth.monitor())
            try:
                h = github_signup.akun_baru(item["mail"], item["pw"], prof,
                                            proxy=jendela_proxy(proksi, i, attempt - 1),
                                            live=a.live)
            except Exception as e:               # jangan mati senyap (pelajaran 0/0)
                h = {"sukses": False, "mail": item["mail"],
                     "sebab": f"eksepsi {type(e).__name__}: {str(e)[:60]}"}
            inbox_batal(item["mail"])            # browser pra-hangat tak terpakai -> tutup
            # P3 IDEMPOTENSI: begitu POST /signup diterima (terpakai), akun
            # Proton ditandai SEBELUM retry — dulu setelah loop, sehingga
            # attempt-2 gagal menimpa h -> konsumsi akun lolos ditandai dan
            # antrean fresh bisa dikonsumsi dobel oleh run berikut.
            if h.get("terpakai"):
                import pool
                pool.tandai_dipakai(h["mail"])   # idempoten (sudah pindah -> False)
            if h.get("sukses") or attempt >= maks_attempt or \
                    klasifikasi(h.get("sebab", "")) != "INFRA":
                break                            # P4: hanya INFRA yang diulang
            with kunci:
                print(f"  [retry] {h['mail']} — {h['sebab'][:40]} -> attempt "
                      f"{attempt + 1}/{maks_attempt} dgn proxy baru", flush=True)
            time.sleep(random.uniform(3, 8))
        # P3 idempotensi: simpan() asli tetap — tapi jangan tulis baris dobel
        # saat retry/re-run (mail sudah tercatat -> lewati; dedupe_combo di akhir
        # merapikan sisa jejak lama)
        if not (h.get("sukses") and _baris_ada(github_signup.HASIL, h["mail"])):
            github_signup.simpan(h)
        if h.get("sukses") and a.live:
            # ---- FASE CODEBUDDY: target akhir = JWT access token ----
            bandwidth.tahap(bandwidth.T_OTP_LANJUT)   # P4: byte cb masuk T3
            try:
                import codebuddy_phase
                # [jaringan] JANGAN pernah proxy=None (dulu: proksi kosong ->
                # browser+curl codebuddy direct): fallback exit geonode pool.
                px_cb = proksi[i % max(1, len(proksi))] if proksi else gh_api.proksi_pertama()
                if not px_cb:
                    raise RuntimeError("tanpa exit proxy — menolak sesi codebuddy direct")
                res = codebuddy_phase.proses(
                    {"mail": h["mail"], "pass": h["pass"],
                     "gh_cookies": h.get("gh_cookies") or {}},
                    proxy=px_cb,   # 1 identitas = 1 exit-IP (wajib, anti-direct)
                    log=print)
                if res.get("token"):
                    jalur_cb = os.path.join(HERE, "results", "codebuddy-combo.txt")
                    tulis_combo_unik(       # P3: anti dobel (dulu cek substring
                        jalur_cb,           #     tanpa kunci-thread -> rebutan)
                        f"{h['mail']}|{h['pass']}|{res['token']}", h["mail"])
                    h["sebab"] += f" | JWT OK ({res.get('timing', 0):.0f}dtk)"
                    h["jwt"] = res["token"]
                else:
                    h["sebab"] += f" | cb: {str(res.get('sebab', ''))[:36]}"
            except Exception as e:
                h["sebab"] += f" | cb galat {type(e).__name__}: {str(e)[:30]}"
        if h.get("terpakai"):
            import pool
            pool.tandai_dipakai(h["mail"])   # pindah ke bekas-github.txt (idempoten)
        with kunci:
            selesai[0] += 1
            hasil.append(h)
            tanda = "✓" if h["sukses"] else "✗"
            print(f"  [{selesai[0]}/{len(akun)}] {tanda} {h['mail']} — {h['sebab']} "
                  f"({time.time()-t0:.0f}dtk)", flush=True)

    print(f"rencana: {len(akun)} akun | {a.workers} worker | proxy: {len(proksi) or 'langsung'}"
          f" | pra-gate: {pra_gate or 'mati'} | ulang-infra: {a.ulang_infra}")
    awal = time.time()
    bagi = [akun[i::a.workers] for i in range(a.workers)]

    def worker(chunk, ofs):
        for j, item in enumerate(chunk):
            kerjakan(ofs + j * a.workers, item)
            time.sleep(random.uniform(a.jeda_min, a.jeda_max))   # 1 akun/IP + jeda anti-rate-limit

    try:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            for i, chunk in enumerate(bagi):
                if chunk:
                    ex.submit(worker, chunk, i)
    finally:
        pass                     # gate.py mandiri — tanpa antrean/produksi gasak

    buang = dedupe_combo()          # P3: rapikan *-combo.txt dari baris dobel
    menit = (time.time() - awal) / 60
    ok = sum(1 for h in hasil if h["sukses"])
    bp = sum(h.get("byte_proxy", 0) for h in hasil)   # KB (sudah //1024 di github_signup)
    import asset_cache
    print(f"\n=== RINGKASAN ===")
    print(f"sukses        : {ok}/{len(hasil)} akun ({100*ok/max(1,len(hasil)):.0f}%)")
    print(f"waktu         : {menit:.1f} menit total | {menit/max(1,ok):.1f} mnt/akun jadi")
    print(f"byte          : proxy {bp} KB ({bp//max(1,ok)} KB/akun jadi) "
          f"| aset-lokal {asset_cache.BYTE_LOKAL[0]//1024//1024} MB (0 byte proxy) "
          f"| chrome-system diblokir {asset_cache.N_BLOKIR[0]} req")
    # P4: laporan ringkas byte per TAHAP vs budget (150/800/550 KB; 1,5 MB/akun)
    teks_mtr, ring_mtr = bandwidth.laporan_run(n_akun=len(akun) or len(hasil))
    print(teks_mtr)
    riwayat = bandwidth.tren_catat(ring_mtr)          # P4: riwayat utk alarm 3-run
    for pes in bandwidth.tren_alarm(riwayat):
        print(f"tren 3-run    : {pes}")
    print(f"output        : {github_signup.HASIL}  (mail|pass|totp_secret)")
    if buang:
        print(f"dedupe combo  : {buang} baris dobel dibuang (P3 idempotensi)")
    if any(not h["sukses"] for h in hasil):
        print("gagal         : " + ", ".join(f"{h['mail'].split('@')[0]}({h['sebab'][:18]})"
                                            for h in hasil if not h["sukses"]))


if __name__ == "__main__":
    utama()
