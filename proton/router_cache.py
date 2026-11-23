# Addon mitmproxy: router + cache untuk hemat bandwidth proxy.
# - host *.proton.me (identitas penting) -> via upstream (Geonode/Decodo/dll) [server.via]
# - host lain -> langsung (via tetap None), tidak menyentuh proxy berbayar
# - asset immutable proton (nama ber-hash) -> dilayani dari cache disk, 0 byte jaringan
# - asset berat tak perlu utk fase signup (PDF template, font TTF fallback) -> DIBLOKIR
#   (0 byte ke proxy maupun direct) — atur BLOCK_HEAVY=1 utk ikut blokir recovery-kit chunk
# Perbaikan v2 (2026-10-01):
#   * respons sintetis cache-hit tidak lagi dihitung sbg byte upstream / re-store
#     (dulu dobel: cache_stored & bytes_upstream ikut naik saat hit -> akunting "boros")
#   * byte dihitung WIRE (raw_content terkompresi) = biaya proxy nyata
#   * blocklist asset berat non-esensial
# Pakai: GEONODE_PROXY=host:port GEONODE_AUTH=user:pass mitmdump -s router_cache.py
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
import json
import os
import re
import time
from pathlib import Path

from mitmproxy import http

GEONODE_PROXY = os.environ.get("GEONODE_PROXY", "")   # host:port
GEONODE_AUTH = os.environ.get("GEONODE_AUTH", "")      # user:pass
UPSTREAM_F = Path(os.environ.get("UPSTREAM_FILE", str(Path(__file__).resolve().parent / "upstream.json")))
PROXY_HOSTS = re.compile(r"(^|\.)proton\.me$", re.I)


def load_upstream():
    """[fix 2026-10-12] upstream STICKY per attempt (ditulis core.py pasang_exit
    ke upstream.json) — dulu kubuat round-robin per koneksi = tiap request
    browser loncat IP = 'pembuatan mencurigakan' bagi Proton (sumber blokir
    abusive). Fallback: env, lalu pool bila file tak ada."""
    try:
        d = json.loads(UPSTREAM_F.read_text())
        hp, auth = d.get("proxy", ""), d.get("auth", "")
        if isinstance(hp, str) and ":" in hp:
            return hp, auth if isinstance(auth, str) else ""
    except Exception:
        pass
    if GEONODE_PROXY:
        return GEONODE_PROXY, GEONODE_AUTH
    pool = []
    try:
        pf = Path(__file__).resolve().parent.parent / "data" / "proxies.txt"
        for l in pf.read_text().splitlines():
            l = l.strip()
            if l and l.count(":") >= 3:
                h, po, u, pw = l.split(":", 3)
                pool.append((f"{h}:{po}", f"{u}:{pw}"))
    except Exception:
        pass
    return pool[0] if pool else ("", "")


def auth_header():
    _hp, auth = load_upstream()
    return ("Basic " + base64.b64encode(auth.encode()).decode()) if auth else None


IMMUTABLE = re.compile(
    r"\.?[0-9a-f]{6,}(?:\.[a-z0-9]+)*\.(?:js|mjs|css|wasm|woff2?|ttf|png|svg|jpe?g|gif|webp)(?:\?|$)", re.I)

_BLOCK_PATS = []
if os.environ.get("BLOCK_HEAVY") == "1":
    # [fix BEDAH-RISK-CONTROL-BARU 2026-11-08] empty_template + Inter ttf
    # DIPINDAH ke balik gerbang BLOCK_HEAVY: blokir tanpa gerbang = font
    # fallback 404 sintetis di 1017/1017 run gelombang (emas: 200/304) — jejak
    # "MITM" terbaca server + halaman render dgn font salah. JANGAN blokir
    # resource yang halaman butuh utk render normal (pelajaran blokir-selektif).
    _BLOCK_PATS.append(r"empty_template\.[0-9a-f]{6,}[^?]*\.pdf")      # template PDF recovery (1MB)
    _BLOCK_PATS.append(r"Inter-(?:Regular|Bold|SemiBold|Light|Mono|Italic|ExtraBold|Thin)\.[0-9a-f]{6,}[^?]*\.ttf")  # font fallback (0.7MB)
    _BLOCK_PATS.append(r"recovery-kit\.[0-9a-f]{6,}[^?]*\.chunk\.js")  # generator PDF recovery (1.4MB)
BLOCK = re.compile("|".join(f"(?:{p})" for p in _BLOCK_PATS), re.I) if _BLOCK_PATS else re.compile(r"(?!)")

# host telepon-pulang Chrome + path telemetri Proton (analytics murni, aman diblokir)
BLOCK_HOSTS = re.compile(r"(google-analytics\.com|googletagmanager\.com|doubleclick\.net"
                         r"|safebrowsing\.googleapis\.com|safebrowsings\.googleapis\.com"
                         r"|gvt1\.com|clients[0-9]?\.google\.com|update\.googleapis\.com"
                         r"|optimizationguide-pa\.googleapis\.com)", re.I)
# PENTING: telemetri Proton JANGAN diblokir — terbukti (uji 2026-10-02) menjadi
# sinyal risiko: run tanpa telemetri -> menu anjlok ke [sms,invite,coupon];
# run dgn telemetri mengalir -> [captcha,email]. Blokir hanya utk host non-proton.
BLOCK_PATHS = re.compile(r"(?!)")  # kosongkan: semua path proton diizinkan

CACHE_DIR = Path(os.environ.get("CACHE_DIR", str(Path(__file__).resolve().parent / "cache")))
STATS_F = Path(os.environ.get("STATS_FILE", str(Path(__file__).resolve().parent / "cache-stats.json")))
CT = {"js": "application/javascript", "mjs": "application/javascript", "css": "text/css",
      "wasm": "application/wasm", "woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf",
      "png": "image/png", "svg": "image/svg+xml", "jpg": "image/jpeg", "jpeg": "image/jpeg",
      "gif": "image/gif", "webp": "image/webp"}

stats = {"cache_hit": 0, "cache_stored": 0, "cache_bytes_served": 0,
         "via_upstream": 0, "direct": 0, "bytes_upstream": 0, "bytes_direct": 0,
         "blocked": 0, "bytes_blocked_est": 0, "t0": time.time()}


def key_for(host, path):
    if not IMMUTABLE.search(path):
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", path.split("?")[0])
    return CACHE_DIR / host / safe


def wire_len(resp):
    """Ukuran di kawat (terkompresi) = biaya proxy nyata."""
    if resp is None:
        return 0
    raw = resp.raw_content
    if raw is not None:
        return len(raw)
    return len(resp.content or b"")


class RouterCache:
    def request(self, flow: http.HTTPFlow):
        # [kuota endpoint] users/available = 429 malam ini (600+ uji membakar kuota);
        # jawab sintetis dari router (app tetap jalan; POST /users tetap validasi
        # nama di server) — sekaligus hemat kuota & byte.
        # [fix 2026-11-05 HAR user] sintetis DIMATIKAN default: alur user asli
        # 3x cek users/available NYATA ke server sebelum POST /users; kita nol
        # (server tak pernah lihat cek nama = pola beda + header versi basi
        # 5.0.423.0 di balasan sintetis). Aktifkan lagi hanya utk uji kuota:
        # AVAILABLE_SINTETIS=1.
        if 'users/available' in flow.request.path and \
                os.environ.get("AVAILABLE_SINTETIS", "0") == "1":   # [aktif] sintetis SELALU dipakai (bukti: dibalas 13b HTTP/1.1)
            flow.response = http.Response.make(200, b'{"Code":1000}', {
                "Content-Type": "application/json",
                "x-pm-application-version": "5.0.423.0",
                "x-pm-request-id": "0",
                "cache-control": "no-cache, no-store",
                "date": __import__('email').utils.formatdate(usegmt=True)})
            stats["blocked"] += 1
            save_stats()
            return
        host = flow.request.pretty_host
        if BLOCK_HOSTS.search(host):
            flow.response = http.Response.make(204, b"", {"X-Disk-Cache": "blocked-host"})
            flow.metadata["from_cache"] = True
            stats["blocked"] += 1
            save_stats()
            return
        if PROXY_HOSTS.search(host) and BLOCK_PATHS.search(flow.request.path.split("?")[0]):
            flow.response = http.Response.make(204, b"", {"X-Disk-Cache": "blocked-telemetry"})
            flow.metadata["from_cache"] = True
            stats["blocked"] += 1
            save_stats()
            return
        if not PROXY_HOSTS.search(host):
            return
        path = flow.request.path
        if BLOCK.search(path.split("?")[0]):
            flow.response = http.Response.make(404, b"", {"X-Disk-Cache": "blocked"})
            flow.metadata["from_cache"] = True  # nol byte upstream & jangan dihitung ulang
            stats["blocked"] += 1
            save_stats()
            return
        k = key_for(host, path)
        if k and k.exists():
            body = k.read_bytes()
            ext = k.suffix.lstrip(".").lower()
            flow.response = http.Response.make(200, body, {
                "Content-Type": CT.get(ext, "application/octet-stream"),
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Disk-Cache": "hit",
            })
            flow.metadata["from_cache"] = True
            stats["cache_hit"] += 1
            stats["cache_bytes_served"] += len(body)
            save_stats()

    def _atur_via(self, flow: http.HTTPFlow):
        # [fix akar 2026-10-13] via HARUS dipasang di hook ALIRAN (http_connect /
        # requestheaders), BUKAN di server_connect. Bukti kode mitmproxy 12.2.3:
        # server_connect dijalankan di proxy/server.py open_connection (~195) tapi
        # koneksi dibuka asyncio.open_connection(*address) (~212) — atribut
        # server.via DIABAIKAN total. via hanya dibaca layers/http get_connection
        # (~1125) dari event.via = flow.server_conn.via saat make_server_connection
        # (~761). Dulu via di-set di server_connect -> keburu terlambat/sia-sia;
        # ditambah koneksi EAGER (default connection_strategy=eager,
        # addons/proxyserver.py ~155) dibuka langsung saat CONNECT
        # (handle_connect_regular ~789) lalu DIPAKAI ULANG utk request
        # (get_connection can_use_context_connection) = semua byte bocor ke IP VPS.
        # Bukti tcpdump 2026-10-13 (curl -x router https://account.proton.me):
        # 28 paket direct ke proton, 0 paket ke upstream. Dengan via awal +
        # connection_strategy=lazy, get_connection membangun HttpUpstreamProxy ->
        # CONNECT ber-auth lewat upstream (http_connect_upstream isi
        # Proxy-Authorization), byte hijau di dashboard Geonode.
        if flow.server_conn.via:
            return  # sudah diputuskan utk koneksi ini (via tak boleh berubah saat open)
        host = flow.request.pretty_host
        hp, _auth = load_upstream()
        if PROXY_HOSTS.search(host) and hp:
            h, p = hp.rsplit(":", 1)
            flow.server_conn.via = ("http", (h, int(p)))  # proton -> upstream (IP harus konsisten)

    def http_connect(self, flow: http.HTTPFlow):
        # CONNECT (browser HTTPS) — hook ini jalan SEBELUM koneksi server dibuka
        self._atur_via(flow)

    def requestheaders(self, flow: http.HTTPFlow):
        # plain HTTP tanpa CONNECT — via wajib siap sebelum make_server_connection
        self._atur_via(flow)

    def server_connect(self, data):
        # koneksi NYATA yang dibuka: hitung leg berbayar (tunnel ke endpoint
        # upstream) vs langsung — via routing sendiri diatur di _atur_via.
        server = data.server
        addr = getattr(server, "address", None) or ("", 0)
        hp, _auth = load_upstream()
        if hp and f"{addr[0]}:{addr[1]}" == hp:
            stats["via_upstream"] += 1  # leg tunnel ke upstream (berbayar)
        else:
            stats["direct"] += 1  # non-proton: langsung, jangan boroskan proxy
        save_stats()

    def http_connect_upstream(self, flow: http.HTTPFlow):
        hdr = auth_header()
        if hdr:
            flow.request.headers["Proxy-Authorization"] = hdr

    def response(self, flow: http.HTTPFlow):
        if flow.metadata.get("from_cache"):
            return  # respons sintetis (cache-hit/blok): bukan byte upstream
        host = flow.request.pretty_host
        n = wire_len(flow.response)
        if PROXY_HOSTS.search(host):
            stats["bytes_upstream"] += n  # wire bytes lewat proxy berbayar
        else:
            stats["bytes_direct"] += n
        if not PROXY_HOSTS.search(host) or not flow.response or flow.response.status_code != 200:
            save_stats()
            return
        k = key_for(host, flow.request.path)
        content = flow.response.content  # disimpan ter-decode, disajikan sbg identity
        if k and content:
            k.parent.mkdir(parents=True, exist_ok=True)
            k.write_bytes(content)
            stats["cache_stored"] += 1
        save_stats()

    def done(self):
        save_stats()
        print(f"[router_cache] {stats}")


def save_stats():
    try:
        STATS_F.write_text(json.dumps(stats, indent=1))
    except Exception:
        pass


addons = [RouterCache()]
