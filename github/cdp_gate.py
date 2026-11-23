#!/usr/bin/env python3
"""cdp_gate.py — SOLVER GERBANG GitHub di atas raw-CDP (TANPA Playwright) +
KOLAM BROWSER BERSAMA utk semua tahap ber-browser lainnya.

Gerbang (dipertahankan apa adanya — resep lolos DataDome "kasar-nogl"):
    raw-CDP + profil chrome sendiri (_ambil_profil) + tanpa flag GL
    (_args_tanpa_gl) -> homepage-warm -> /signup -> loop deteksi -> solve slider
    bila perlu -> panen signup form (authenticity_token) + cookies.
Kontrak sama dgn `gate.ambil_gate`: `(signup_html, cookies)` — cookies list dict
{name,value,domain,path} utk `sesi.s.cookies.set(...)`.

REVISI HEMAT-RAM 2026-10-06 (satu run = 1 profile, banyak TAB/konteks):
  * KOLAM (`sesi_browser`/`pinjam_browser`): SATU Chrome global (Xvfb :99,
    kasar-nogl + batas proses wajib) dibagi ke SEMUA thread lewat koneksi
    Playwright per-thread (sync API tidak boleh lintas-thread/bersarang — bug
    lama "Playwright Sync API inside the asyncio loop"). Tiap akun memakai
    KONTEKS BARU (isolasi cookie + proxy per konteks — 1 exit-IP per akun);
    konteks ditutup setelah akun, Chrome hanya ditutup di akhir run
    (`tutup_semua`). Kebocoran konteks dijaga `jaga_bocor` (paksa tutup >N).
  * Gerbang TETAP di Chrome proses TERPISAH (raw-CDP, profil pool sendiri):
    koneksi Playwright yang hidup = auto-attach Target + Runtime.enable +
    addScriptToEvaluateOnNewDocument ke SEMUA target = jejak rule 878430;
    jendela gate tak boleh tersentuh Playwright. Chrome gate singkat (buka-
    pakai-tutup) & profil dipinjam/dikembalikan di SEMUA jalur (dulu slot bocor
    saat start gagal -> profil menumpuk / 2 Chrome 1 profil = start gagal).
  * Args wajib RAM: --renderer-process-limit=3, --disable-dev-shm-usage,
    --disable-gpu, --no-first-run, --disable-extensions + ramping proses
    --no-zygote --no-sandbox --in-process-gpu (jumlah --type 9-10 -> 4-6 per
    Chrome; BUKAN --single-process yang tidak stabil).
  * siapkan_display(): Xvfb :99 diuji hidup-nya dulu (socket basi Xvfb mati =
    Chrome "Missing X server"); DISPLAY selalu :99 -> tak nongol di layar user.

Antarmuka utk proton_inbox (lihat instruksi 3 baris di komentar atas stok_cb.py):
    b = cdp_gate.pinjam_browser(proxy=_px or None, headless=...)  # konteks baru
    ... b.new_context(...) seperti biasa ...
    b.close()   # konteks dikembalikan; Chrome bersama TETAP hidup
Tanpa integrasi / bila kolam gagal -> fallback otomatis buka Chrome sendiri.
"""
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
import random
import socket
import threading
import time
from urllib.parse import urlsplit

import cdp_lite
import fp_lite
import px_forward

# ---- kolam profil PERSISTEN gerbang (resep bedah §8.3: "simpan cookie datadome
# lulus per-profil"): (a) cache aset HTTP = repeat-run ~0 byte proxy (target
# 1,5MB); (b) cookie datadome yg lolos kebawa = skor kepercayaan naik. Slot
# tumbuh sesuai kebutuhan (N worker boleh paralel) — Chrome mengunci 1 profil =
# 1 instance, jadi 2 Chrome jangan berbagi direktori.
_PROFIL_POOL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "results", "cdp-profil")
PROFIL_KOLAM = os.path.join(_PROFIL_POOL, "kolam")   # profil Chrome bersama
_kunci_pool = threading.Lock()
_siap_slot = []
_slot_berikut = [4]
byte_terakhir = (0, 0)   # (up, down) tunnel gerbang terakhir — utk meter run.py

# ---- batas proses WAJIB (spec 2026-10-06) + peramping proses (--type 9-10 ->
# 4-6 per Chrome; --no-zygote butuh --no-sandbox; --single-process DILARANG;
# --disable-breakpad = tanpa 2 crashpad-handler per pohon)
PROSES_HEMAT = [
    "--renderer-process-limit=3", "--disable-dev-shm-usage", "--disable-gpu",
    "--no-first-run", "--disable-extensions",
    "--no-sandbox", "--disable-breakpad",
    # [fix stabilitas 2026-10-06] --no-zygote & --in-process-gpu = hemat proses
    # TAPI bikin Chrome kolam CRASH di beban 3 konteks (CdpError ws recv /
    # TargetClosedError — bukti Tahap-1). Dilepas: stabil > ramping.
]


def _sync_playwright_impl():
    """[misi CB 2026-11-12, lead rebrowser-patches] pemasok sync_playwright.
    REBROWSER=1 = driver rebrowser-playwright (drop-in, patch anti-bocor
    Runtime.enable + jejak 'UtilityScript' evaluate — README rebrowser:
    deteksi Runtime.enable DIPAKAI anti-bot besar; patchright batal karena
    mematikan WebGL di setup ini, lihat docstring fp_profile.sync_playwright).
    Default = playwright asli (perilaku lama; kill-switch)."""
    if os.environ.get("REBROWSER") == "1":
        try:
            from rebrowser_playwright.sync_api import (
                sync_playwright as _sw)
            return _sw
        except Exception:
            pass
    from playwright.sync_api import sync_playwright as _sw
    return _sw


def siapkan_display(n="99"):
    """Xvfb :99 AMAN utk browser HEADED (headless = sinyal sensor; jendela tetap
    tak nongol di layar user karena ada di layar virtual :99).
    fp_lite.ensure_display() hanya mengecek keberadaan FILE socket — socket
    basi (Xvfb mati tak bersih) bikin Chrome gagal "Missing X server or
    $DISPLAY". Di sini socket diuji hidup; mati -> buang -> biarkan
    ensure_display() start Xvfb baru."""
    disp = f":{n}"
    sock = f"/tmp/.X11-unix/X{n}"

    def _hidup():
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        try:
            s.connect(sock)
            return True
        except Exception:
            return False
        finally:
            s.close()

    if os.path.exists(sock) and not _hidup():
        try:
            os.remove(sock)              # socket basi tanpa listener
        except OSError:
            pass
    fp_lite.ensure_display()
    for _ in range(8):
        if _hidup():
            break
        time.sleep(0.5)
    os.environ["DISPLAY"] = disp
    return disp


def _matikan_kunci_profil(d):
    for nm in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
        try:
            os.remove(os.path.join(d, nm))
        except OSError:
            pass


def _tombstone_exit(px):
    """[resep I-P0-4] catat exit yang kena hard-block ke daftar hitam permanen —
    jangan pernah dikirim ke akun lain (bukti: hard-block 20x vs 1 sukses saat
    slot exit dipakai ulang). Penyaringan dilakukan pembuat pool (ops/buat-pool)."""
    try:
        u = (px or {}).get("username") or ""
        if u:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                             "ops", "exit-tombstone.txt")
            with open(p, "a") as f:
                f.write(u + "\n")
    except Exception:
        pass


def _preflight_exit(px):
    """[resep I-P0-4] probe CONNECT 3 dtk ke github.com via exit SEBELUM Chrome
    dibuka — exit sekarat langsung diganti tanpa membakar waktu & byte tunnel
    (bukti riset: 8 kasus 'form tak muncul 130 dtk' dgn byte 22-250KB = exit mati,
    30x ConnectionError per run). Return True = sehat."""
    try:
        import base64 as _b64
        hp = (px or {}).get("server") or ""
        hp = hp.split("//")[-1]
        hs, _, prt = hp.partition(":")
        if not hs:
            return True
        sk = socket.create_connection((hs, int(prt or 80)), 3)
        ua = px.get("username") or ""
        pw = px.get("password") or ""
        auth = ""
        if ua:
            auth = ("Proxy-Authorization: Basic "
                    + _b64.b64encode(f"{ua}:{pw}".encode()).decode() + "\r\n")
        sk.sendall(("CONNECT github.com:443 HTTP/1.1\r\nHost: github.com:443\r\n"
                    + auth + "\r\n").encode())
        sk.settimeout(3)
        hd = sk.recv(160) or b""
        sk.close()
        return b"200" in hd.split(b"\r\n", 1)[0]
    except Exception:
        return False


def _ambil_profil():
    """Pinjam direktori profil (tumbuh sesuai kebutuhan — anti '2 Chrome 1
    profil' yang bikin start gagal = numpuk proses retry).
    [resep-A 2026-10-14] PROFIL_FRESH=1 = profil BARU tiap attempt (temuan bedah
    ghreg: "DataDome trusts fresh profiles"; data kita: cookie lintas slot =
    hard-block 20x vs 1 sukses). UNDO: set PROFIL_FRESH=0 -> perilaku lama.
    Cache aset tetap DISALIN dari profil template (identitas baru, byte hemat)."""
    if os.environ.get("PROFIL_FRESH") == "1":
        import shutil, tempfile
        d = tempfile.mkdtemp(dir=_PROFIL_POOL, prefix="fresh-")
        tmpl_root = os.path.join(_PROFIL_POOL, "p0", "Default")
        for sub in ("Cache", "Code Cache"):
            s = os.path.join(tmpl_root, sub)   # [fix hemat-byte 2026-10-14]
            # AKAR: cache asli ada di p0/Default/Cache (28MB) — dulu path p0/Cache
            # yang tidak ada -> cache dingin tiap pass -> githubassets ~1,2MB/pass
            # diunduh ulang. HANYA Cache yang disalin (bukan Cookies/Local State)
            # = identitas profil tetap fresh, hanya byte penyajian yang hangat.
            if os.path.isdir(s):
                try:
                    os.makedirs(os.path.join(d, "Default"), exist_ok=True)
                    shutil.copytree(s, os.path.join(d, "Default", sub), dirs_exist_ok=True)
                except Exception:
                    pass
        _matikan_kunci_profil(d)
        return d
    with _kunci_pool:
        if not _siap_slot:
            for i in range(4):
                d = os.path.join(_PROFIL_POOL, f"p{i}")
                os.makedirs(d, exist_ok=True)
                _siap_slot.append(d)
        if _siap_slot:
            d = _siap_slot.pop()
        else:
            i = _slot_berikut[0]
            _slot_berikut[0] += 1
            d = os.path.join(_PROFIL_POOL, f"p{i}")
            os.makedirs(d, exist_ok=True)
    _matikan_kunci_profil(d)     # sisa kunci dr chrome zombie -> start gagal
    return d


def _kosongkan_profil(d):
    """Lap STATUS SESI run gagal — TAPI pertahankan Cache/Code Cache (bedah byte
    R1: rmtree dulu menghapus cache -> retry = unduh ulang 2,4-52 MB; 33/59 gate
    call >2MB = 96% byte tunnel semuanya karenanya). Cookie/ets tetap dibersihkan
    ('profil dicurigai'); cache HTTP = jejak manusiawi, bukan sinyal."""
    import shutil
    if os.path.basename(d).startswith("fresh-"):
        shutil.rmtree(d, ignore_errors=True)   # [resep-A] profil fresh = buang total
        return
    for nama in ("Cookies", "Cookies-journal", "Login Data", "Login Data-journal",
                 "Web Data", "Web Data-journal", "Local State", "History",
                 "History-journal", "Visited Links", "Network Persistent State"):
        for base in (os.path.join(d, "Default", nama), os.path.join(d, nama)):
            try:
                os.remove(base)
                break
            except OSError:
                pass
    for sub in ("Session Storage", "Local Storage", "IndexedDB", "Service Worker"):
        shutil.rmtree(os.path.join(d, "Default", sub), ignore_errors=True)
    try:
        os.remove(os.path.join(d, "datadome.json"))
    except OSError:
        pass


def _kembali_profil(d):
    with _kunci_pool:
        if d not in _siap_slot:
            _siap_slot.append(d)


def _bunuh_proses_profil(pdir):
    """Bunuh sisa proses Chrome yg memakai `--user-data-dir=pdir` (anti-numpuk
    proses) — pid dibaca dari /proc (aman: pola unik profil, diri sendiri tak
    ikut, tanpa shell/pkill)."""
    saya = os.getpid()
    pola = f"--user-data-dir={pdir}"
    try:
        daftar = os.listdir("/proc")
    except OSError:
        return 0
    n = 0
    for pid in daftar:
        if not pid.isdigit() or int(pid) == saya:
            continue
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        if pola in cmd and "chrome" in cmd:
            try:
                os.kill(int(pid), 9)
                n += 1
            except Exception:
                pass
    return n


# [GL_BACKEND 2026-11-12] variasi backend render per akun (misri user: field
# 29/57-seg1 blob turingfraud IDENTIK lintas akun karena SEMUA meraster lewat
# SwiftShader yang sama). Backend = PER-PROSES -> dipilih tiap Chrome baru.
_GL_TRIO_LAMA = ("--use-angle=swiftshader", "--use-gl=angle",
                 "--enable-unsafe-swiftshader")
_GL_SAH = ("swiftshader", "llvmpipe", "lvp")


def _gl_pilih():
    """Nama backend GL utk Chrome BERIKUTNYA dari env GL_BACKEND:
    swiftshader|llvmpipe|lvp|acak. Kosong/nilai tak dikenal = 'swiftshader'
    (perilaku lama). 'acak' = undian per pembuatan Chrome."""
    m = (os.environ.get("GL_BACKEND") or "").strip().lower()
    if m == "acak":
        return random.choice(_GL_SAH)
    return m if m in _GL_SAH else "swiftshader"


def _gl_susun(nama):
    """(args flag Chrome, env proses) utk backend GL `nama`.
    [riset A/B 2026-11-12 Xvfb, Chrome 155.0.8059.39] bukti renderer ASLI +
    raster (uji /tmp/riset-gl.py):
    - swiftshader: 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)))'
      — trio args lama, tanpa env;
    - llvmpipe   : 'ANGLE (Mesa, llvmpipe (LLVM 20.1.2 256 bits), OpenGL 4.5)'
      — ANGLE-OpenGL over Mesa llvmpipe; TANPA --ignore-gpu-blocklist ->
      webgl NULL (feature opengl/webgl = disabled_off = tell lama);
    - lvp        : 'ANGLE (Mesa, Vulkan 1.4.318 (llvmpipe), llvmpipe)'
      — ANGLE-Vulkan over lavapipe (VK_ICD_FILENAMES paksa ICD lvp).
    zink GAGAL di box ini: ANGLE Display::initialize 12289 'Could not create
    GL context' (zink tak punya pbuffer surface utk display init ANGLE) —
    sudah dicoba GALLIUM_DRIVER=zink + MESA_LOADER_DRIVER_OVERRIDE +
    ZINK_DESCRIPTORS=lazy -> webgl NULL semua. Jangan ditawarkan."""
    if nama == "llvmpipe":
        return (["--use-gl=angle", "--use-angle=gl", "--ignore-gpu-blocklist"],
                {"LIBGL_ALWAYS_SOFTWARE": "1"})
    if nama == "lvp":
        return (["--use-gl=angle", "--use-angle=vulkan", "--ignore-gpu-blocklist"],
                {"VK_ICD_FILENAMES": "/usr/share/vulkan/icd.d/lvp_icd.json"})
    return list(_GL_TRIO_LAMA), {}


def _gl_rotasi():
    """True bila GL_BACKEND=acak -> 1 akun = 1 Chrome BARU (undian backend
    per akun). Dibaca MALAS: env diset pemanggil SETELAH import module
    (module-level _KOLAM dibuat saat import)."""
    return (os.environ.get("GL_BACKEND") or "").strip().lower() == "acak"


def _gl_taskset(cores):
    """Masker CPU per Chrome bila GL_BACKEND diset (mode identitas per akun):
    navigator.hardwareConcurrency ASLI bervariasi 4/8 antar akun (dulu semua
    8 = kunci klaster 'mesin sama'); klaim persona <=4 -> 4 thread nyata,
    selain itu 8 (maks CPU VPS; klaim 12/16 tetap lewat spoof JS seperti
    perilaku lama). Pola inti/device_mask.py."""
    if not (os.environ.get("GL_BACKEND") or "").strip():
        return None
    try:
        c = int(cores or 8)
    except Exception:
        c = 8
    return "0-3" if c <= 4 else "0-7"


def _args_tanpa_gl(prof, gl=""):
    """Resep kasar-nogl + batas proses wajib. Buang trio SwiftShader
    (`--use-angle=swiftshader`,`--use-gl=angle`,`--enable-unsafe-swiftshader`) =
    pemicu rule 876626. Jejak lolos `kasar-nogl` = Chrome TANPA flag GL (WebGL
    null = 'laptop tanpa GPU' di Xvfb, wajar). Dulu Playwright memaksa flag ini;
    raw-CDP membersihkannya. Argumen `prof` dipertahankan utk kompatibilitas
    pemanggil lama (persona UA diterapkan per konteks, bukan per proses).
    [A/B CB 2026-11-10] PAKAI_GL=1 = pertahankan trio GL DAN buang
    --disable-gpu -> webgl != NULL (resep 'WebGL HIDUP' fp_lite ARGS_DASAR).
    Motif: kandidat akar 'Account Access Restricted' CodeBuddy = webgl NULL
    (sidik jari mustahil kelas AGENTS #12, klaster identik semua akun).
    Default TETAP kasar-nogl — produksi tak tersentuh; saklar hanya utk A/B.
    [GL_BACKEND 2026-11-12] bila GL_BACKEND diset: trio SwiftShader diganti
    args backend pilihan (`gl` = nama hasil _gl_pilih utk Chrome ini — args DAN
    env harus dari undian YANG SAMA). GL_BACKEND kosong = perilaku lama UTUH."""
    if os.environ.get("PAKAI_GL") == "1":
        a = [x for x in fp_lite.launch_args(prof)[1:] + PROSES_HEMAT
             if x != "--disable-gpu"]
        if not (os.environ.get("GL_BACKEND") or "").strip():
            return a                   # perilaku lama utuh (trio swiftshader)
        args_gl, _ = _gl_susun(gl or _gl_pilih())
        return [x for x in a if x not in _GL_TRIO_LAMA] + args_gl
    dasar = [a for a in fp_lite.launch_args(prof)[1:]
             if "swiftshader" not in a.lower() and "use-gl=angle" not in a.lower()]
    return dasar + PROSES_HEMAT


def _proxy_dict(p):
    """proxy (dict run.py / URL 'user:pass@host:port' / None) -> dict Playwright."""
    if not p:
        return None
    if isinstance(p, dict):
        return p
    u = urlsplit(p if "://" in p else "http://" + p)
    return {"server": f"{u.scheme}://{u.hostname}:{u.port}",
            "username": u.username, "password": u.password}


def _jumlah_proses_tipe():
    """Jumlah proses chrome ber-`--type=` (anak browser) — utk laporan/uji."""
    n = 0
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "replace")
            except Exception:
                continue
            if "--type=" in cmd and "chrome" in cmd and "brave" not in cmd:
                n += 1
    except OSError:
        pass
    return n


# ===========================================================================
# KOLAM BROWSER BERSAMA — 1 Chrome global + N konteks (Playwright per-thread)
# ===========================================================================
class SesiBrowser:
    """Facade mirip `Browser` Playwright utk SATU thread: `new_context(**kw)`
    = konteks baru di Chrome BERSAMA (proxy per konteks = 1 exit-IP per akun);
    `close()` = tutup semua konteksnya SAJA (Chrome tetap hidup). Antarmuka
    sengaja cocok dgn pemakaian lama (`b = p.chromium.launch(...)` ... `b.close()`)
    supaya call-site cukup ditukar, logika bisnis tak tersentuh."""

    def __init__(self, proxy=None, label="", lepas_koneksi=False):
        self._proxy = proxy
        self.label = label
        self._lepas = lepas_koneksi      # True utk thread pinjaman (mis. inbox)
        self._ctxs = []
        self._b = None

    def siap(self):
        """Hubungkan sekarang (deteksi dini) — opsional; new_context() juga
        menghubungkan otomatis."""
        if self._b is None:
            self._b = _KOLAM.koneksi()
        return self

    def new_context(self, **kw):
        if self._b is None:
            self._b = _KOLAM.koneksi()
        kw2 = dict(kw)
        if self._proxy is not None and "proxy" not in kw2:
            kw2["proxy"] = _proxy_dict(self._proxy)
        with _KOLAM._kunci_buat:               # diff id konteks = anti bocor
            sebelum = _KOLAM._daftar_konteks_id()
            try:
                ctx = self._b.new_context(**kw2)
            except Exception:   # [self-heal 2026-10-07] Chrome kolam tumbang
                # (TargetClosed berantai 20/43 run!) -> konteks dari Chrome
                # mandiri, rantai tetap jalan; dibersihkan bersihkan_chrome_nakal
                mb = _BrowserMandiri(proxy=self._proxy, label=self.label,
                                     headless=False).siap()
                ctx = mb.new_context(**kw2)
                try:
                    ctx._mandiri = mb
                except Exception:
                    pass
            bid = _KOLAM._daftar_konteks_id() - sebelum
        _KOLAM._catat(ctx, bid, self.label)
        self._ctxs.append(ctx)
        return ctx

    def close(self):
        for ctx in self._ctxs:
            try:
                ctx.close()
            except Exception:
                pass
            _KOLAM._lepas(ctx)
        self._ctxs.clear()
        if self._lepas:
            _KOLAM.tutup_koneksi()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class _BrowserMandiri:
    """FALLBACK `pinjam_browser`: buka Chrome SENDIRI (perilaku lama pemanggil,
    mis. proton_inbox standalone / kolam tak tersedia) — antarmuka sama persis
    (new_context/close), jadi integrasi tak pernah lebih buruk dari aslinya."""

    def __init__(self, proxy=None, label="", headless=False):
        self._proxy, self.label, self._headless = proxy, label, headless
        self._ctxs = []
        self._pw = self._b = None

    def siap(self):
        sync_playwright = _sync_playwright_impl()
        siapkan_display()
        self._pw = sync_playwright().start()
        # [GL_BACKEND 2026-11-12] undian backend GL per Chrome mandiri (args +
        # env satu paket; env=Playwright.launch = env anak, bukan global).
        nama_gl = _gl_pilih()
        _, env_gl = _gl_susun(nama_gl)
        # catatan: env=Playwright.launch = env LENGKAP anak (bukan merge) ->
        # gabung dgn os.environ; None = wariskan (perilaku lama). taskset tak
        # berlaku di jalur fallback Playwright ini (kolam raw-CDP = jalur utama).
        self._b = self._pw.chromium.launch(
            executable_path="/usr/bin/google-chrome", headless=self._headless,
            args=_args_tanpa_gl(fp_lite.build_persona(), gl=nama_gl),
            env=({**os.environ, **env_gl} if env_gl else None))
        return self

    def new_context(self, **kw):
        if self._b is None:
            self.siap()
        kw2 = dict(kw)
        if self._proxy is not None and "proxy" not in kw2:
            kw2["proxy"] = _proxy_dict(self._proxy)
        ctx = self._b.new_context(**kw2)
        self._ctxs.append(ctx)
        return ctx

    def close(self):
        for ctx in self._ctxs:
            try:
                ctx.close()
            except Exception:
                pass
        self._ctxs.clear()
        try:
            if self._b is not None:
                self._b.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._b = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Kolam:
    """Siklus hidup SATU Chrome bersama + koneksi Playwright per-thread.

    Kenapa koneksi per-thread: objek sync Playwright terikat greenlet thread
    pembuat (dipanggil dari thread lain = galat / bug "Sync API inside the
    asyncio loop" bila 1 thread punya 2 sync_playwright hidup). Tiap thread
    ambil koneksi sendiri via `connect_over_cdp` ke Chrome yang SAMA — chrome
    tetap 1 proses, konteks = isolasi cookie/proxy.

    Manajemen konteks (spec 3): konteks ditutup pemilik per akun; `jaga_bocor`
    memaksa tutup konteks bila >N terbuka (via raw-CDP Target.disposeBrowser
    Context — aman lintas-thread); `tutup_semua` di akhir run."""

    def __init__(self, indeks=0):
        self.indeks = indeks
        self._kunci_hidup = threading.Lock()
        self._kunci_cdp = threading.Lock()      # serialisasi raw-CDP call
        self._kunci_buat = threading.Lock()     # serialisasi create-context
        self._kunci_catat = threading.Lock()
        self._tls = threading.local()
        self._c = None                          # cdp_lite.Cdp = chrome + ws raw
        self._pdir = None
        self._http_ep = None                    # http://127.0.0.1:PORT (connect_over_cdp)
        self._aktif = {}                        # id(ctx) -> {ctx, waktu, label, bid}

    # ---- siklus hidup Chrome bersama
    def hidupkan(self, paksa=False):
        with self._kunci_hidup:
            siapkan_display()
            if (not paksa) and self._c is not None:
                try:
                    if self._c.proc is None or self._c.proc.poll() is None:
                        return self._http_ep
                except Exception:
                    pass
            self._matikan_lama()
            pdir = f"{PROFIL_KOLAM}-{self.indeks}"   # [bedah 2026-10-12] profil per-chrome
            os.makedirs(pdir, exist_ok=True)
            _matikan_kunci_profil(pdir)
            galat = None
            for _ in range(2):
                # [bedah 2026-10-12] persona PER CHROME = tiap Chrome seakan
                # laptop berbeda (anti-spoof ala pabrik Proton) — dulu None =
                # semua Chrome identik & 1 profil dipakai berjamaah.
                # [GL_BACKEND 2026-11-12] undian backend GL SEKALI per Chrome
                # (args + env WAJIB dari undian yang sama; GL_BACKEND kosong =
                # perilaku lama utuh).
                nama_gl = _gl_pilih()
                _, env_gl = _gl_susun(nama_gl)
                prof_gl = fp_lite.build_persona()
                c = cdp_lite.Cdp(args=_args_tanpa_gl(prof_gl, gl=nama_gl),
                                 profil_dir=pdir, env=env_gl or None,
                                 taskset=_gl_taskset(prof_gl.get("cores")))
                try:
                    c.start()
                except Exception as e:
                    galat = e
                    try:
                        c.close()
                    except Exception:
                        pass
                    _bunuh_proses_profil(pdir)
                    _matikan_kunci_profil(pdir)
                    continue
                self._c, self._pdir = c, pdir
                self._http_ep = _endpoint_dari(c)
                return self._http_ep
            raise RuntimeError(f"chrome kolam gagal start: {galat}")

    def _matikan_lama(self):
        c, self._c = self._c, None
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        if self._pdir:
            _bunuh_proses_profil(self._pdir)
        self._http_ep = None

    # ---- koneksi Playwright per-thread (lazy) — dipakai objek thread ini saja
    def _sambung(self):
        """sync_playwright BARU + connect_over_cdp — 1 instance per thread
        (2 instance hidup di 1 thread = bug lama "Sync API inside the asyncio
        loop"); instance gagal WAJIB distop sebelum retry."""
        sync_playwright = _sync_playwright_impl()
        pw = None
        try:
            pw = sync_playwright().start()
            return pw, pw.chromium.connect_over_cdp(self._http_ep)
        except Exception:
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
            raise

    def koneksi(self):
        b = getattr(self._tls, "browser", None)
        if b is not None:
            return b
        galat = None
        for percobaan in range(2):   # percobaan 2 = chrome paksa-baru
            try:
                self.hidupkan(paksa=(percobaan > 0))
                pw, b = self._sambung()
                self._tls.pw, self._tls.browser = pw, b
                return b
            except Exception as e:
                galat = e
                # [waras-retry 2026-10-08] doktrin _sambung: instance lama thread
                # ini WAJIB distop sebelum retry — dulu tak dilakukan di lapisan
                # ini -> racun permanen "Sync API inside the asyncio loop" per
                # thread (bukti: sukses dulu, gagal berantai setelah N siklus).
                self.tutup_koneksi()
        raise galat

    def tutup_koneksi(self):
        """Tutup koneksi Playwright thread INI (dipanggil dari thread pemilik)."""
        pw = getattr(self._tls, "pw", None)
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        self._tls.pw = None
        self._tls.browser = None

    # ---- buku konteks (anti-bocor)
    def _daftar_konteks_id(self):
        with self._kunci_cdp:
            try:
                return set(self._c.call("Target.getBrowserContexts", {})
                           .get("browserContextIds", []))
            except Exception:
                return set()

    def _catat(self, ctx, bid, label):
        with self._kunci_catat:
            self._aktif[id(ctx)] = {"ctx": ctx, "waktu": time.time(),
                                   "label": label, "bid": set(bid or ())}
        tls = getattr(self._tls, "ctxs", None)
        if tls is None:
            tls = self._tls.ctxs = []
        tls.append(ctx)

    def _lepas(self, ctx):
        with self._kunci_catat:
            self._aktif.pop(id(ctx), None)
        tls = getattr(self._tls, "ctxs", None)
        if tls:
            try:
                tls.remove(ctx)
            except ValueError:
                pass

    def jaga_bocor(self, maks=80):   # [skala worker 2026-10-09] 48 -> 80: 10 worker x 5-6
        # konteks = 50+ > 48 = eviktor membunuh konteks HIDUP (badai TargetClosed).
        """Spec 3: kalau ada kebocoran konteks (lebih dari `maks` terbuka),
        tutup paksa yang PALING TUA via raw-CDP (aman lintas-thread).
        [fix klaster-TargetClosed 2026-10-08] maks 8 -> 32: dgn 8 worker tiap
        thread membuka 2-4 konteks -> total >8 hampir selalu -> konteks HIDUP
        lintas-thread dibantai tiap akun (26 kegagalan TargetClosed/ws per jam).
        32 = 4 konteks/worker: kebocoran tetap terjaga, sesama worker tak saling
        bunuh (bedah: self-heal kolam 0x = kolam sehat, pelakunya evictor ini)."""
        with self._kunci_catat:
            daftar = sorted(self._aktif.values(), key=lambda r: r["waktu"])
            korban = daftar[:max(0, len(daftar) - maks)]
            for r in korban:
                self._aktif.pop(id(r["ctx"]), None)
        n = 0
        with self._kunci_cdp:
            for r in korban:
                for bid in r["bid"]:
                    try:
                        self._c.call("Target.disposeBrowserContext",
                                     {"browserContextId": bid})
                        n += 1
                    except Exception:
                        pass
        return n

    def bersihkan_thread(self):
        """Setelah 1 akun selesai: tutup konteks yg bocor milik thread ini +
        lepas koneksi Playwright thread ini (hemat RAM antar-akun)."""
        sisa = []
        ctxs = getattr(self._tls, "ctxs", None)
        if ctxs:
            sisa = list(ctxs)
            ctxs.clear()
        for ctx in sisa:
            try:
                ctx.close()
            except Exception:
                pass
            self._lepas(ctx)
        self.jaga_bocor()
        self.tutup_koneksi()

    def tutup_semua(self):
        """Akhir run: tutup SEMUA konteks (raw-CDP) lalu Chrome-nya + profil."""
        with self._kunci_hidup:
            try:
                with self._kunci_cdp:
                    if self._c is not None:
                        for bid in list(self._c.call("Target.getBrowserContexts", {})
                                        .get("browserContextIds", [])):
                            try:
                                self._c.call("Target.disposeBrowserContext",
                                             {"browserContextId": bid})
                            except Exception:
                                pass
            except Exception:
                pass
            self._matikan_lama()
        with self._kunci_catat:
            self._aktif.clear()


def _endpoint_dari(c):
    """http://127.0.0.1:PORT dari koneksi ws CDP (utk connect_over_cdp)."""
    ep = getattr(c.ws, "url", "") or ""
    if "://" in ep:
        u = urlsplit(ep)
        return f"http://{u.hostname}:{u.port}"
    try:
        host, port = c.ws.sock.getpeername()[:2]
        return f"http://{host}:{port}"
    except Exception:
        raise RuntimeError("endpoint CDP kolam tak terbaca")


class RakitKolam:
    """[bedah 2026-10-12] rakitan MULTI-CHROME: tiap Chrome = 1 "laptop"
    (persona + profil sendiri = anti-spoof ala pabrik Proton) dengan MAKS
    8 tab/konteks per Chrome — 15 signup paralel dibagi ke beberapa Chrome,
    bukan 15 tab dalam 1 Chrome (jejak bot yang dibaca DataDome)."""
    MAKS_THREAD = 2                     # 2 thread x <=4 konteks = <=8 tab/chrome

    def __init__(self):
        self._kunci = threading.Lock()
        self._slots = []                # [{kolam, thread}]
        self._tls = threading.local()

    def slot(self):
        s = getattr(self._tls, "slot", None)
        if s is not None:
            return s
        with self._kunci:
            pilih = None
            # [GL_BACKEND 2026-11-12] mode acak = 1 thread per Chrome (rotasi
            # Chrome per akun — Chrome berbagi = korban thread saudara).
            maks = 1 if _gl_rotasi() else self.MAKS_THREAD
            for d in self._slots:
                if len(d["thread"]) < maks:
                    pilih = d
                    break
            if pilih is None:
                k = Kolam(indeks=len(self._slots))
                pilih = {"kolam": k, "thread": set()}
                self._slots.append(pilih)
            pilih["thread"].add(threading.get_ident())
            self._tls.slot = pilih["kolam"]
            return pilih["kolam"]

    # --- permukaan yang dipanggil SesiBrowser (per-thread -> slotnya)
    @property
    def _kunci_buat(self):
        return self.slot()._kunci_buat

    def _daftar_konteks_id(self):
        return self.slot()._daftar_konteks_id()

    def _catat(self, ctx, bid, label):
        return self.slot()._catat(ctx, bid, label)

    def _lepas(self, ctx):
        return self.slot()._lepas(ctx)

    def koneksi(self):
        return self.slot().koneksi()

    def tutup_koneksi(self):
        return self.slot().tutup_koneksi()

    # --- operasi seluruh rakitan
    def jaga_bocor(self, maks=8):
        n = 0
        for d in list(self._slots):
            n += d["kolam"].jaga_bocor(maks)   # 8 tab per chrome = batas user
        return n

    def bersihkan_thread(self):
        s = getattr(self._tls, "slot", None)
        if s is not None:
            s.bersihkan_thread()
            if _gl_rotasi():
                # [GL_BACKEND acak 2026-11-12] 1 akun = 1 Chrome BARU: backend
                # GL = per-proses, Chrome kolam yang hidup lintas akun membuat
                # semua akun berkumpul di backend yang sama (klaster baru).
                # Rotasi di akhir akun = undian backend segar utk akun berikut.
                s._matikan_lama()

    def tutup_semua(self):
        with self._kunci:
            for d in self._slots:
                d["kolam"].tutup_semua()
            self._slots.clear()


_KOLAM = RakitKolam()


def sesi_browser(proxy=None, label="", lepas_koneksi=False):
    """Ambil 'browser' pinjaman utk thread ini (konteks per new_context()).
    Dipanggil github_signup / codebuddy_phase — antarmuka sengaja sama dgn
    `p.chromium.launch()` lama supaya logika bisnis tak berubah."""
    return SesiBrowser(proxy=proxy, label=label, lepas_koneksi=lepas_koneksi)


def pinjam_browser(proxy=None, label="pinjam", headless=False):
    """API utk proton_inbox (thread lain) — lihat instruksi 3 baris di atas
    stok_cb.py. Return objek mirip Browser Playwright (`new_context`/`close`)
    dgn KONTEKS BARU dari Chrome bersama; `close()` mengembalikan konteks (Chrome
    tetap hidup). Bila kolam gagal / tanpa integrasi -> FALLBACK buka Chrome
    sendiri (perilaku lama) — aman dipanggil dari mana saja."""
    try:
        s = SesiBrowser(proxy=proxy, label=label, lepas_koneksi=True)
        s.siap()                                # gagal awal -> fallback
        return s
    except Exception:
        return _BrowserMandiri(proxy=proxy, label=label, headless=headless).siap()


def tutup_semua():
    """Akhir run: tutup SEMUA konteks + Chrome bersama (spec 3)."""
    _KOLAM.tutup_semua()


def bersihkan_thread():
    """Setelah 1 akun per thread: tutup konteks sisa + koneksi Playwright thread."""
    _KOLAM.bersihkan_thread()


def jaga_bocor(maks=8):
    """Paksa tutup konteks bila lebih dari `maks` terbuka (anti-kebocoran)."""
    return _KOLAM.jaga_bocor(min(maks, 8))


def uji_bersama(tutup=True):
    """Self-check kolam TANPA jaringan (about:blank): validasi Xvfb + args +
    connect_over_cdp + konteks; return (ok, jumlah proses --type). `tutup`=True:
    Chrome uji DITUTUP lagi setelah dicek — kolam produksi start LAZY saat akun
    pertama benar-benar butuh (jangan biarkan Chrome nganggur di jendela gate)."""
    n_sebelum = _jumlah_proses_tipe()
    s = SesiBrowser(label="uji-kolam", lepas_koneksi=True)
    try:
        s.siap()
        ctx = s.new_context()
        pg = ctx.new_page()
        pg.goto("about:blank", timeout=15000)
        n = _jumlah_proses_tipe()
        return True, max(0, n - n_sebelum)
    finally:
        s.close()
        if tutup:
            _KOLAM.tutup_semua()


def uji_gerbang():
    """Self-check Chrome gerbang raw-CDP TANPA jaringan (about:blank): validasi
    resep kasar-nogl + pool profil (pinjam/kembali); return (ok, jumlah proses)."""
    siapkan_display()
    n_sebelum = _jumlah_proses_tipe()
    pdir = _ambil_profil()
    c = None
    try:
        c = cdp_lite.Cdp(args=_args_tanpa_gl(None), profil_dir=pdir)
        c.start()
        c.new_tab("about:blank")
        time.sleep(0.6)
        return True, max(0, _jumlah_proses_tipe() - n_sebelum)
    except Exception:
        return False, 0
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        _bunuh_proses_profil(pdir)
        _kembali_profil(pdir)


# ===========================================================================
# GATE raw-CDP (Chrome TERPISAH dari kolam — jangan sentuh Playwright)
# ===========================================================================
HOMEPAGE = "https://github.com/"
# [bedah 2026-10-12] ref_path dulu salah ketik (tanpa '=') + entry langsung
# tanpa Referer = tanda tangan bot; kini funnel asli homepage -> klik Sign up.
SIGNUP = ("https://github.com/signup?source=form-home-signup&ref_cta=Sign+up"
          "&ref_loc=hero&ref_page=%2F&ref_path=%2F")
_JS_KLIK_DAFTAR = """(() => {
  const k = document.querySelector('a[data-ga-click*="Sign up"], header a[href^="/signup"]');
  if (k) { k.click(); return 'klik'; }
  const b = document.querySelector('form[action*="signup"] button');
  if (b) { b.click(); return 'form'; }
  return '';})()"""

# deteksi status halaman (dalam 1 evaluate)
_DET = """(() => {
  const h = document.documentElement.outerHTML || '';
  const t = (document.body && document.body.innerText) || '';
  const q = (s) => { for (const e of document.querySelectorAll(s)) {
      const r = e.getBoundingClientRect(); if (r.width>4 && r.height>4)
        return {x:r.x,y:r.y,w:r.width,h:r.height}; } return null; };
  const handle = q('.slider,[class*=slider] [class*=handle],.puzzle__button');
  const track = q('.sliderTarget,.sliderText,[class*=track]');
  return JSON.stringify({
    form: h.includes('authenticity_token') || !!document.querySelector('input[name="user[email]"]'),
    robot: /temporarily restricted|unusual activity/i.test(t),
    cls: (document.body && document.body.className || '').slice(0, 40),
    rt: (h.match(/dd=\\{'rt':'([icb])'/) || [])[1] || null,
    slider: !!(handle && track),
    handle, track, path: location.pathname
  });})()"""


_DET_FRAME = """(() => {
  const t = (document.body && document.body.innerText) || '';
  const q = (s) => { for (const e of document.querySelectorAll(s)) {
      const r = e.getBoundingClientRect(); if (r.width>4 && r.height>4)
        return {x:r.x,y:r.y,w:r.width,h:r.height}; } return null; };
  const handle = q('.slider,[class*=slider] [class*=handle],.puzzle__button');
  const track = q('.sliderTarget,.sliderText,[class*=track]');
  return JSON.stringify({
    robot: /temporarily restricted|unusual activity/i.test(t),
    cls: (document.body && document.body.className || '').slice(0, 40),
    soft: /verification required|slide right/i.test(t),
    slider: !!(handle && track), handle, track
  });})()"""


def _sinyal(c, sid):
    """Deteksi gabungan: konteks utama + SEMUA target (page + OOPIF challenge).
    OOPIF (iframe lintas-origin) = target CDP terpisah -> session_id sendiri
    (fsid); Input.mouse di sesi itu = koordinat viewport iframe tsb."""
    out = {"form": False, "robot": False, "slider": False, "soft": False,
           "handle": None, "track": None, "fsid": None, "rt": None}
    try:
        main = json.loads(c.eval(_DET, sid))
    except Exception:
        main = {}
    out["rt"] = main.get("rt")
    out["form"] = main.get("form", False)
    out["robot"] = out["robot"] or main.get("robot", False)
    if main.get("slider"):
        out.update(slider=True, handle=main.get("handle"),
                   track=main.get("track"), fsid=sid)
    for url, val, tsid in c.frames_eval(_DET_FRAME, sid,
                                        hanya_cocok="captcha-delivery"):
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except Exception:
                val = None
        if not val:
            continue
        out["robot"] = out["robot"] or val.get("robot", False)
        out["soft"] = out["soft"] or val.get("soft", False)
        if val.get("slider") and not out["slider"]:
            out.update(slider=True, handle=val.get("handle"),
                       track=val.get("track"), fsid=tsid)
    return out


KUNCI_GERBANG = threading.Semaphore(8)   # [stabilitas 2026-10-08] konsesi gerbang
#   maksimal 3 serentak: 7 worker x chrome gerbang + 7 chrome signup = puncak 14
#   chrome -> konsesi timeout "tak menyimpan artefak" + hard-block beruntun
#   (bukti gerbang-B: rasio jeblok 2/9 saat semua konsesi serentak). Sisanya
#   antre rapi; alur tak diubah.


def ambil_gate(username=None, timeout=360, proxy=None, log=print):
    """Raw-CDP gate (dibungkus antrean konsesi maks 3 serentak)."""
    with KUNCI_GERBANG:
        return _ambil_gate_asli(username=username, timeout=timeout, proxy=proxy, log=log)


def _ambil_gate_asli(username=None, timeout=360, proxy=None, log=print):
    """Raw-CDP gate. Return (signup_html, cookies) atau (None, None)."""
    prof = fp_lite.build_persona()
    siapkan_display()
    # [resep I-P0-4] preflight: exit sekarat diganti TANPA buka Chrome
    if proxy and not _preflight_exit(proxy):
        log("[cdp-gate] preflight: exit tak sehat -> ganti exit tanpa buka browser")
        return None, None
    fwd = px_forward.Forwarder(upstream=proxy).start() if proxy else None
    t0 = time.time()
    sukses = False
    c = pdir = None                 # start tangguh: profil terkunci/zombie -> slot lain
    for _ in range(3):
        pdir = _ambil_profil()      # profil persisten: cache aset + cookie
        c = cdp_lite.Cdp(args=_args_tanpa_gl(prof), profil_dir=pdir,
                         proxy=(f"http://{fwd.local}" if fwd else None))
        try:
            c.start()
            break
        except Exception:
            try:
                c.close()
            except Exception:
                pass
            _bunuh_proses_profil(pdir)     # anti-numpuk: sisa proses ikut mati
            _kosongkan_profil(pdir)
            _kembali_profil(pdir)          # [fix] slot dulu bocor di sini
            c = None
    if c is None:
        if fwd:
            fwd.stop()
        log("[cdp-gate] chrome tak bisa start (3 slot) -> anggap gagal", flush=True)
        return None, None
    # ---- kebersihan cookie antar-akun (RACUN profil persisten): cookie sesi lama
    # (logged_in/_gh_sess) ikut terbawa -> C8 POST ditolak GitHub ("already ...").
    # Sisakan HANYA datadome (kepercayaan L1, TTL 1 thn — resep §8.3).
    try:
        c.call("Storage.clearCookies")
        jalur_dd = os.path.join(pdir, "datadome.json")
        if not os.path.exists(jalur_dd) and os.environ.get("EXPERIMEN_KERAS") == "1":
            # [EXPERIMEN 2026-10-07] cookie DataDome HANGAT lintas IP — TERBUKTI
            # MEMICU hard-block instan (uji-5H: 7x hard-block tunnel 30-44KB).
            # Hanya utk eksperimen; jalur produksi TIDAK memakai ini (metode lama).
            try:
                kolam = json.load(open(os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "data", "datadome-hangat.json")))
                if kolam:
                    json.dump(kolam[-1], open(jalur_dd, "w"))
            except Exception:
                pass
        # [bedah 2026-10-12] JANGAN muat cookie DataDome lama lintas IP —
        # exit kita round-robin tiap akun, cookie bekas exit lain = hard-block
        # instan (terbukti: 20x hard-block vs 1 sukses saat slot dipakai ulang).
        # Cookie baru dihasilkan sendiri per attempt dengan exit-nya.
        if False and os.path.exists(jalur_dd):
            c.call("Storage.setCookies",
                   {"cookies": [json.load(open(jalur_dd))]})
    except Exception:
        pass
    try:
        sid = c.new_tab(HOMEPAGE)
        # homepage-warm: diam + gerak acak (jejak manusia). [hemat-waktu 2026-10-07]
        # dipadatkan 5-9 -> 2.5-4.5 dtk: jejak kursor TETAP ada (gerak acak tiap
        # 0.3-0.9 dtk), yg dipotong cuma waktu diam — deteksi konsesi gerbang
        # (62-88 dtk) tak menentukan hasil, terbukti TAKLUK juga pada warm cepat
        # (sampel TAKLUK 10-24 dtk di run yg sama).
        _diam_gerak(c, sid, random.uniform(5.0, 9.0))   # dwell manusiawi (dulu dipadatkan 2.5-4.5)
        # [bedah 2026-10-12] masuk lewat KLIK tautan Sign up (Referer alami),
        # navigate langsung hanya fallback.
        masuk = ""
        try:
            masuk = (c.eval(_JS_KLIK_DAFTAR, sid, timeout=20) or "").strip()
        except Exception:
            masuk = ""
        if masuk:
            time.sleep(random.uniform(1.4, 3.0))
        else:
            c.navigate(SIGNUP, sid, timeout=45)
        putaran = 0
        while time.time() - t0 < timeout:
            putaran += 1
            st = _sinyal(c, sid)
            if st.get("form"):
                break
            if st.get("robot"):
                log("[cdp-gate] hard-block -> rotasi IP")
                _tombstone_exit(proxy)   # [resep I-P0-4] exit kena hard-block = JANGAN dipakai lagi
                return None, None
            if st.get("slider"):
                log(f"[cdp-gate] slider terdeteksi -> solve (putaran {putaran})")
                _solve_slider(c, sid, st)
                time.sleep(random.uniform(0.6, 1.2))
            else:
                # rt:'i' interstitial / tak-ada -> DIAMKAN (auto-solve), tanpa reload
                # [hemat-waktu] jeda antar-cek 3-6 -> 1.5-2.8 dtk: konsesi yang
                # datang lebih cepat KETEMU lebih cepat (latensi deteksi turun
                # ~2 dtk/putaran); jejak kursor tetap, logika vonis tak berubah.
                _diam_gerak(c, sid, random.uniform(3.0, 6.0) if random.random() > 0.1 else random.uniform(8.0, 15.0))
            # [fix jendela tunggu] putaran 25 dengan jeda 1.5-2.8 habis ~70 dtk <
            # timeout 130 -> konsesi telat (88-126 dtk) jadi keburu break. Dinaikkan
            # ke 60 supaya BATAS 130 dtk tunggu konsesi tetap terpakai penuh.
            if putaran > 60:
                break
        st = _sinyal(c, sid)
        if not st.get("form"):
            log(f"[cdp-gate] form tak muncul s/d {timeout:.0f}dtk (rt={st.get('rt')})")
            return None, None
        html = c.dom_content(sid)
        kuki = c.call("Storage.getCookies", {}, sid).get("cookies", [])
        norm = [{"name": k.get("name"), "value": k.get("value"),
                 "domain": k.get("domain"), "path": k.get("path", "/")} for k in kuki]
        log(f"[cdp-gate] TAKLUK: html={len(html)}B cookies={len(norm)} "
            f"({time.time()-t0:.0f}dtk)")
        sukses = True
        try:   # [exit hijau] exit dgn gate tembus = jendela flag 25 mnt menang
            if proxy:
                import os as _os
                _p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                   "data", "exit-hijau.txt")
                with open(_p, "a") as fh:
                    fh.write(f"{time.time():.0f}|{str(proxy)}\n")
        except Exception:
            pass
        try:                       # simpan datadome utk kepercayaan run berikut
            for k in norm:
                if k.get("name") == "datadome":
                    dd = {"name": k["name"], "value": k["value"],
                          "domain": k.get("domain") or ".github.com",
                          "path": k.get("path") or "/"}
                    json.dump(dd, open(os.path.join(pdir, "datadome.json"), "w"))
                    try:      # kolam hangat global (dipakai profil lain/run berikut)
                        jalur_kolam = os.path.join(
                            os.path.dirname(os.path.abspath(__file__)),
                            "data", "datadome-hangat.json")
                        kolam = []
                        try:
                            kolam = json.load(open(jalur_kolam))
                        except Exception:
                            pass
                        kolam.append(dd)
                        json.dump(kolam[-30:], open(jalur_kolam, "w"))
                    except Exception:
                        pass
                    break
        except Exception:
            pass
        return html, norm
    finally:
        if fwd:
            try:
                up, dn = fwd.byte
                global byte_terakhir
                byte_terakhir = (up, dn)
                log(f"[cdp-gate] byte tunnel: up={up/1024:.0f}KB down={dn/1024:.0f}KB "
                    f"total={(up + dn) / 1024:.0f}KB (budget 1500KB)", flush=True)
            except Exception:
                pass
            fwd.stop()
        try:
            c.close()
        except Exception:
            pass
        _bunuh_proses_profil(pdir)         # pastikan tak ada chrome numpuk
        if not sukses:
            _kosongkan_profil(pdir)  # gagal/hard-block -> buang akumulasi mencurigakan
        _kembali_profil(pdir)
    return None, None


def _diam_gerak(c, sid, dur):
    """[bedah 2026-10-12] gerak manusiawi: kursor berinterpolasi smoothstep
    (bukan teleport), micro-jitter, sesekali scroll — jejak pointer untuk
    challenge JS dibaca sebagai manusia."""
    t0 = time.time()
    x, y = random.uniform(400, 900), random.uniform(250, 550)
    while time.time() - t0 < dur:
        tx, ty = random.uniform(150, 1450), random.uniform(120, 820)
        langkah = random.randint(8, 22)
        for i in range(langkah):
            f = (i + 1) / langkah
            e = f * f * (3 - 2 * f)                     # smoothstep
            x2 = x + (tx - x) * e + random.uniform(-2.5, 2.5)
            y2 = y + (ty - y) * e + random.uniform(-2.5, 2.5)
            c.mouse("mouseMoved", x2, y2, sid)
            time.sleep(random.uniform(0.03, 0.12))
        x, y = tx, ty
        if random.random() < 0.3:
            try:
                c.call("Input.dispatchMouseEvent",
                       {"type": "mouseWheel", "x": int(x), "y": int(y),
                        "deltaX": 0,
                        "deltaY": random.choice([-1, 1]) * random.uniform(60, 320)},
                       sid=sid)
            except Exception:
                pass
        time.sleep(random.uniform(0.25, 1.1))


def _solve_slider(c, sid, st):
    """Drag slider di SESI target-nya (OOPIF: koordinat viewport iframe sendiri)."""
    h, tb = st.get("handle"), st.get("track")
    fsid = st.get("fsid") or sid
    if not h or not tb:
        return False
    x0 = h["x"] + h["w"] / 2
    y0 = h["y"] + h["h"] / 2
    x1 = tb["x"] + tb["w"] - h["w"] / 2 - 2
    y1 = tb["y"] + tb["h"] / 2
    c.drag(x0, y0, x1, y1, fsid)
    return True


if __name__ == "__main__":
    import sys
    px = sys.argv[1] if len(sys.argv) > 1 else None
    h, k = ambil_gate(proxy=px)
    print("signup_html:", len(h or ""), "| cookies:", len(k or []))
    tutup_semua()
