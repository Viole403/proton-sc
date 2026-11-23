#!/usr/bin/env python3
"""px_forward.py — forwarder proxy lokal utk raw-CDP + proxy ber-auth.

Masalah: Chrome `--proxy-server` TIDAK menerima auth inline (`user:pass@host`);
proxy geonode butuh `Proxy-Authorization`. Solusi (pola gasak "forwarder"):
browser -> proxy lokal ini (tanpa auth) -> upstream geonode (disuntik header
`Proxy-Authorization: Basic ...`). Hanya CONNECT (HTTPS) + GET/POST polos.

Pemakaian:
    f = Forwarder("127.0.0.1", 0, upstream="http://user:pass@host:port")
    f.start()          # f.local = "127.0.0.1:xxxxx"
    ... --proxy-server=http://{f.local} ...
    f.stop()
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
import base64
import select
import socket
import socketserver
import threading
from urllib.parse import urlsplit


def _parse(upstream):
    """Dukung 3 bentuk: dict `{server,username,password}` (format run.py),
    geonode `host:port:user:pass`, ATAU URL `user:pass@host:port`.
    Return (host, port, user, pass)."""
    if isinstance(upstream, dict):
        srv = (upstream.get("server") or "").replace("https://", "").replace("http://", "")
        host, _, port = srv.partition(":")
        return (host, int(port or 800), upstream.get("username") or "",
                upstream.get("password") or "")
    s = upstream.strip()
    if "://" in s:
        u = urlsplit(s)
        return (u.hostname, u.port or 800, u.username or "", u.password or "")
    p = s.split(":")
    if len(p) >= 4:                       # host:port:user:pass
        return (p[0], int(p[1]), p[2], ":".join(p[3:]))
    if len(p) == 2:
        return (p[0], int(p[1]), "", "")
    return (s, 800, "", "")


def _auth_header(upstream: str) -> str:
    host, port, user, pw = _parse(upstream)
    if not user:
        return ""
    tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return f"Proxy-Authorization: Basic {tok}\r\n"


def _upstream_addr(upstream: str):
    host, port, _u, _p = _parse(upstream)
    return (host, port)


BATAS_BYTE = 10_000_000  # [rem byte user 2026-10-07; naik 4->10MB 2026-10-16
                         # arahan user: rem TETAP ada utk jaga-jaga, tapi 4MB
                         # memotong tunnel HTTP/2 sesi signup (HTML+chunk+wasm+
                         # challenge+font ~4MB) = request penting ikut mati /
                         # menggantung (jejak GAGAL-JARINGAN)] PER KONEKSI (tunnel)
# diputus bila >4 MB. [fix 2026-10-14] AKAR BUG: meter ini DULU dihitung
# server-wide (semua koneksi gabung) => homepage Chrome (~3,9MB aset) menghabiskan
# seluruh budget, lalu navigasi /signup dibunuh di byte pertama = ERR_CONNECTION_CLOSED.
# Bukti meter driver adopsi: 4/4 mint mati tepat di total ~4.000.000 byte.
# Aturan asli 'tunnel gerbang putus di 4MB' memang per-tunnel; koreksi 2026-10-07
# (2,2MB memotong konsesi sehat 0,1-1,8MB) tetap berlaku utk nilai per-koneksi.
# Rem total sesi tetap ada sbg jaring pengaman liar (default 40MB, override env
# PX_BATAS_TOTAL; 0 = tanpa rem total).
BATAS_TOTAL = int(_os_b.environ.get("PX_BATAS_TOTAL", "40000000"))

# host BUKANG-FUNGSI (chrome-system/ML + unduhan/media codebuddy) — ditolak
# langsung di CONNECT tunnel (berlaku utk SEMUA browser, termasuk raw-CDP gate
# yang tak tersentuh asset_cache). Bukti: akun sukses penuh = 0 byte host ini.
HOST_TOLAK = (
    "googleapis.com", "gvt1.com", "clients2.google.com",
    "clients2.googleusercontent.com", "connectivitycheck.gstatic.com",
    "android.clients.google.com", "mtalk.google.com",
    "browser.events.data.microsoft.com",
    "download.codebuddy.ai", "dscache-intl.cloudcachetci.com",
)

# [resep lab3 transport.py — kunci '0,5MB/akun', saklar PX_SPLIT=1] SPLIT
# ROUTING: hanya host penanda sesi yang lewat proxy BERBAYAR; aset/CDN keluar
# LANGSUNG dari VPS (gratis, hemat kuota); telemetri diblokir. Aturan lab3:
# "vendor anti-bot baru / endpoint verifikasi baru WAJIB didaftarkan BERBAYAR —
# jangan dibiarkan jatuh ke tak-dikenal" (tak-dikenal memang sengaja LANGSUNG).
# Mode ini HANYA utk jalur adopsi lab3; default (PX_SPLIT=0) = perilaku lama
# semua lewat upstream — jangan mengubah jalur produksi lama tanpa uji A/B.
BERBAYAR_SUFFIX = ("github.com", "ipify.org",
                   "captcha-delivery.com", "datadome.co", "octocaptcha.com")
LANGSUNG_SUFFIX = ("githubassets.com", "githubusercontent.com", "gstatic.com",
                   "gravatar.com", "cdn.", "static.", "assets.", "fonts.",
                   "images.")
HOST_PROTON = ("proton.me",)
BLOK_TELEMETRI = ("google-analytics.com", "googletagmanager.com",
                  "doubleclick.net", "sentry.io", "segment.io", "segment.com",
                  "hotjar.com", "fullstory.com", "mixpanel.com",
                  "amplitude.com", "clarity.ms", "facebook.net")
# PENTING (A/B lab5 lab3): telemetri Proton SENDIRI (reports.proton.me dsb)
# jangan diblok — memblokirnya menurunkan menu HV yang ditawarkan.


def _cocok(host, daftar):
    """Subdomain persis untuk domain; longgar utk awalan berakhiran titik."""
    for s in daftar:
        if s.endswith("."):
            if s in host + ".":
                return True
        elif host == s or host.endswith("." + s):
            return True
    return False


def _split_diblokir(host):
    h = (host or "").lower().split(":")[0]
    return _cocok(h, BLOK_TELEMETRI)


def _split_langsung(host):
    """True = keluar langsung (hemat kuota); False = lewat upstream berbayar."""
    h = (host or "").lower().split(":")[0]
    if _cocok(h, LANGSUNG_SUFFIX):
        return True
    if _cocok(h, BERBAYAR_SUFFIX) or _cocok(h, HOST_PROTON):
        return False
    return True


class _Handler(socketserver.StreamRequestHandler):
    # rfile TANPA buffer: cegah kasus klasik — BufferedReader menelan byte
    # TLS ClientHello yang sudah ikut segmen CONNECT, lalu tunnel tak pernah
    # meneruskannya (koneksi menggantung acak / halaman termuat sebagian).
    rbufsize = 0

    def handle(self):
        try:
            self._buf = b""
            line = self._readline()
            if not line:
                return
            met, target, _ = line.decode("latin-1", "replace").split(" ", 2)
            while True:                       # telan header browser
                h = self._readline()
                if h in (b"\r\n", b"\n", b""):
                    break
            if met == "CONNECT":
                self._connect(target)
            else:
                self._http(met, target, line)
        except Exception:
            pass

    def _readline(self) -> bytes:
        """baca 1 baris dari socket MENTAH (tanpa buffer mencuri byte tunnel)."""
        while b"\n" not in self._buf:
            d = self.connection.recv(4096)
            if not d:
                return b""
            self._buf += d
        i = self._buf.index(b"\n") + 1
        line, self._buf = self._buf[:i], self._buf[i:]
        return line

    def _connect(self, target):
        host = target.rsplit(":", 1)[0].lower()
        try:
            port = int(target.rsplit(":", 1)[1] or 443)
        except Exception:
            port = 443
        split = _os_b.environ.get("PX_SPLIT", "0") == "1"
        if split:
            # [resep lab3 transport.py] blokir telemetri, sisanya split routing
            if _split_diblokir(host):
                try:
                    self.connection.sendall(b"HTTP/1.1 403 Blocked\r\n\r\n")
                except Exception:
                    pass
                return
            langsung = _split_langsung(host)
        else:
            if any(host == h or host.endswith("." + h) for h in HOST_TOLAK):
                try:                    # tolak CONNECT host buang-buang (0 byte)
                    self.connection.sendall(b"HTTP/1.1 403 Blocked\r\n\r\n")
                except Exception:
                    pass
                return
            langsung = False
        if langsung:
            try:
                up = socket.create_connection((host, port), 15)
            except Exception:
                try:
                    self.connection.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                except Exception:
                    pass
                return
            self.connection.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            up = socket.create_connection(_upstream_addr(self.server.upstream), 15)
            auth = _auth_header(self.server.upstream)
            up.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{auth}\r\n".encode())
            buf = b""
            while b"\r\n\r\n" not in buf:     # balasan CONNECT upstream
                ch = up.recv(1)
                if not ch:
                    break
                buf += ch
            if b"200" not in buf.split(b"\r\n", 1)[0]:
                self.connection.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                up.close()
                return
            self.connection.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        if self._buf:                         # KRITIS: sisa byte (TLS hello) ikut
            up.sendall(self._buf)
            self._buf = b""
        socks = [self.connection, up]
        n_up = n_dn = 0                    # [fix 2026-10-14] meter PER koneksi
        try:
            while True:
                r, _, _ = select.select(socks, [], [], 60)
                if not r:
                    break
                for s in r:
                    d = s.recv(65536)
                    if not d:
                        # [fix 2026-10-22 bedah subagent] HALF-CLOSE, jangan
                        # bunuh dua arah! Exit memutus koneksi di umur ~50-61s
                        # bahkan saat unduhan berjalan (bukti px-tunnel.log:
                        # dn=718KB terpotong) — perilaku lama (return = bunuh
                        # semua) membuat response-in-flight HILANG: font
                        # ERR_FAILED x2 tiap attempt, POST nyangkut -> spinner
                        # 'Dein Konto wird erstellt' macet -> SPA reboot ->
                        # kematian sesi. Kini: arah yang EOF cukup shutdown(WR),
                        # arah lain TERUS dialirkan sampai tuntas.
                        try:
                            if s is self.connection:
                                up.shutdown(socket.SHUT_WR)
                            else:
                                self.connection.shutdown(socket.SHUT_WR)
                        except Exception:
                            pass
                        socks = [x for x in socks if x is not s]
                        if not socks:
                            return
                        break
                    if s is self.connection:
                        n_up += len(d)
                        with self.server._kunci_meter:
                            if langsung:
                                self.server.byte_direct += len(d)
                            else:
                                self.server.byte_up += len(d)
                            _t = (self.server.byte_up + self.server.byte_down
                                  + self.server.byte_direct)
                        if (not langsung and n_up + n_dn > BATAS_BYTE) or (
                                BATAS_TOTAL and _t > BATAS_TOTAL):
                            return
                        up.sendall(d)
                    else:
                        n_dn += len(d)
                        with self.server._kunci_meter:
                            if langsung:
                                self.server.byte_direct += len(d)
                            else:
                                self.server.byte_down += len(d)
                            _t = (self.server.byte_up + self.server.byte_down
                                  + self.server.byte_direct)
                        if (not langsung and n_up + n_dn > BATAS_BYTE) or (
                                BATAS_TOTAL and _t > BATAS_TOTAL):
                            return
                        self.connection.sendall(d)
        finally:
            up.close()

    def _http(self, met, target, line):
        u = urlsplit(target)
        host = (u.hostname or "").lower()
        split = _os_b.environ.get("PX_SPLIT", "0") == "1"
        if split and _split_diblokir(host):
            try:
                self.connection.sendall(b"HTTP/1.1 403 Blocked\r\n\r\n")
            except Exception:
                pass
            return
        langsung = split and _split_langsung(host)
        if langsung:
            try:
                up = socket.create_connection((host, u.port or 80), 15)
            except Exception:
                self.connection.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            auth = ""
        else:
            up = socket.create_connection(_upstream_addr(self.server.upstream), 15)
            auth = _auth_header(self.server.upstream)
        head = line.rstrip() + "\r\nHost: " + urlsplit(target).netloc + "\r\n" + auth + "\r\n"
        up.sendall(head.encode())
        if self._buf:                         # body/byte lanjutan yg sudah terbaca
            up.sendall(self._buf)
            self._buf = b""
        try:
            n_dn = 0
            while True:
                d = up.recv(65536)
                if not d:
                    break
                n_dn += len(d)
                with self.server._kunci_meter:
                    if langsung:
                        self.server.byte_direct += len(d)
                    else:
                        self.server.byte_down += len(d)
                    _t = (self.server.byte_up + self.server.byte_down
                          + self.server.byte_direct)
                if (not langsung and n_dn > BATAS_BYTE) or (
                        BATAS_TOTAL and _t > BATAS_TOTAL):
                    break
                self.connection.sendall(d)
        finally:
            up.close()


class Forwarder:
    def __init__(self, host="127.0.0.1", port=0, upstream=""):
        class _Srv(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True
        self.srv = _Srv((host, port), _Handler)
        self.srv.upstream = upstream
        self.srv.byte_up = 0            # meteran byte BERBAYAR (utk budget)
        self.srv.byte_down = 0
        self.srv.byte_direct = 0        # [PX_SPLIT] byte keluar langsung (gratis)
        self.srv._kunci_meter = threading.Lock()
        self.local = f"{host}:{self.srv.server_address[1]}"
        self._t = None

    @property
    def byte(self) -> tuple[int, int]:
        s = self.srv
        with s._kunci_meter:
            return s.byte_up, s.byte_down

    @property
    def byte_langsung(self) -> int:
        s = self.srv
        with s._kunci_meter:
            return s.byte_direct

    def start(self):
        self._t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._t.start()
        return self

    def stop(self):
        try:
            self.srv.shutdown()
        except Exception:
            pass
