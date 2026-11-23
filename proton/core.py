#!/usr/bin/env python3
"""Core: konfigurasi, proxy pool, antrean email, orkestrator (worker/batch/retry/stop darurat),
metrik, dan proton-checker. Dipakai oleh tui.py — bukan entry point mandiri.
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
import http.cookiejar
import ipaddress
import json
import os
import random
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
ATTEMPTS = RESULTS / "attempts"
LOGS = HERE / "logs"
SETTINGS_F = DATA / "settings.json"
STATE_F = DATA / "state.json"
METRICS_F = RESULTS / "metrics.json"
REGISTERED_F = RESULTS / "registered.txt"
HEADMAIL = "https://headmail.flowjob-lab.workers.dev"
EVILMAIL = "https://evilmail.pro"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
ALLOWED_HOSTS = {"headmail.flowjob-lab.workers.dev", "evilmail.pro", "account.proton.me"}
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
PROXY_RE = re.compile(r"^[A-Za-z0-9._~%+-]+(:[A-Za-z0-9._~%+-]+)?@")

for _d in (DATA, RESULTS, ATTEMPTS, LOGS):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- pengaturan
def load_settings():
    if SETTINGS_F.exists():
        return json.loads(SETTINGS_F.read_text())
    return {"HEADMAIL_KEY": "", "EVILMAIL_KEY": ""}


def save_settings(d):
    SETTINGS_F.write_text(json.dumps(d, indent=1))
    os.chmod(SETTINGS_F, 0o600)


# ---------------------------------------------------------------- guard jaringan
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        raise RuntimeError("redirect tidak diizinkan")


def check_url_public(url):
    """Hanya http/https + host allowlist + IP publik (tolak privat/loopback/reserved)."""
    u = urllib.parse.urlparse(url)
    assert u.scheme in ("http", "https"), f"skema tidak diizinkan: {u.scheme}"
    assert u.hostname in ALLOWED_HOSTS, f"host tidak diizinkan: {u.hostname}"
    for _f, _t, _p, _c, sa in socket.getaddrinfo(u.hostname, u.port or 443):
        assert ipaddress.ip_address(sa[0]).is_global, f"IP tidak global: {sa[0]}"
    return u


def api_get(url, headers):
    check_url_public(url)
    req = urllib.request.Request(url, headers={"user-agent": UA, **headers})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as r:
        return json.loads(r.read().decode())


def api_post(url, headers, body):
    check_url_public(url)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"user-agent": UA, "content-type": "application/json", **headers})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------- proxy pool
_DNS_CACHE = {}


def _ip_global(hostname, port):
    """getaddrinfo dimemoisasi — pool geonode 1000 baris memakai 1 hostname."""
    k = (hostname, port)
    if k not in _DNS_CACHE:
        _DNS_CACHE[k] = [sa[0] for _f, _t, _p, _c, sa in socket.getaddrinfo(hostname, port)]
    return _DNS_CACHE[k]


def parse_proxy(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith(("http://", "https://")):
        url = line
    else:
        host, port, user, pw = line.split(":", 3)
        url = f"http://{user}:{pw}@{host}:{port}"
    u = urllib.parse.urlparse(url)
    assert u.scheme in ("http", "https") and u.hostname and u.port, f"format proxy tidak valid: {line[:40]}"
    assert PROXY_RE.match(f"{u.username}:{u.password}@x"), f"kredensial proxy tidak valid: {line[:40]}"
    for ip in _ip_global(u.hostname, u.port):
        assert ipaddress.ip_address(ip).is_global, f"IP proxy tidak global: {u.hostname}"
    return {"url": url, "host": u.hostname, "port": u.port,
            "auth": f"{u.username}:{u.password}",
            "label": f"{u.hostname}:{u.port}#{(u.username or '_')[-6:]}"}


def load_proxies(path=None):
    # [fix 2026-10-13] BRUTALISM_PROXY_FILE = 1 proxy per-akun dari satu-alur.py.
    # Dulu env ini DIABAIKAN -> factory pakai 2500 proxy random, bukan proxy akun
    # -> signup di IP-B, recovery di IP-A = beda IP = 10003 "pindah IP".
    import os as _os
    if path is None:
        path = _os.environ.get("BRUTALISM_PROXY_FILE") or None
    p = Path(path) if path else DATA / "proxies.txt"
    out = [x for x in (parse_proxy(l) for l in p.read_text().splitlines()) if x]
    if not path:  # hanya shuffle kalau pakai pool besar (bukan proxy per-akun)
        random.shuffle(out)
    return out


# ---------------------------------------------------------------- antrean email
def valid_domain(dom):
    assert isinstance(dom, str) and DOMAIN_RE.fullmatch(dom), f"domain tidak valid: {dom!r}"
    return dom


def _aktif(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def build_queue():
    """headmail dulu (1x pakai per domain), lalu evilmail (free -> premium).
    [fix 2026-11-03] provider MATI/TIDAK TERJANGKAU tidak boleh membunuh
    seluruh antrean: headmail sempat balas 500 (outage) -> pabrik crash 0 dtk
    sebelum evilmail sempat dipanggil. Tiap provider kini berdiri sendiri."""
    s = load_settings()
    q = []
    if s.get("HEADMAIL_KEY"):
        try:
            doms = api_get(f"{HEADMAIL}/api/v1/domains",
                           {"authorization": "Bearer " + s["HEADMAIL_KEY"]}).get("domains", [])
            q += [{"provider": "headmail", "domain": valid_domain(d["domain"])}
                  for d in doms if _aktif(d.get("enabled"))]
        except Exception as e:
            print(f"[queue] headmail tak terjangkau ({str(e)[:60]}) — lewati, pakai evilmail")
    if s.get("EVILMAIL_KEY"):
        try:
            d = api_get(f"{EVILMAIL}/api/domains/available", {"x-api-key": s["EVILMAIL_KEY"]}).get("data", {})
            q += [{"provider": "evilmail", "domain": valid_domain(x)} for x in d.get("free", [])]
            q += [{"provider": "evilmail", "domain": valid_domain(x)} for x in d.get("premium", [])]
        except Exception as e:
            print(f"[queue] evilmail tak terjangkau ({str(e)[:60]})")
    return q


def load_state():
    st = json.loads(STATE_F.read_text()) if STATE_F.exists() else {"used_emails": [], "attempts": []}
    st.setdefault("frozen", {})  # domain -> timestamp beku (freeze 24 jam saat blokir domain 85102)
    return st


def save_state(st):
    STATE_F.write_text(json.dumps(st, indent=1))


# ---------------------------------------------------------------- metrik
def load_metrics():
    return json.loads(METRICS_F.read_text()) if METRICS_F.exists() else {
        "attempts": [], "batches": [], "ringkasan": {}}


def save_metrics(m):
    METRICS_F.write_text(json.dumps(m, indent=1))


def metrics_update(m, rec, batch_no=None):
    m["attempts"].append(rec)
    if batch_no is not None:
        b = next((x for x in m["batches"] if x["no"] == batch_no), None)
        if b is None:
            b = {"no": batch_no, "attempts": [], "sukses": 0, "menit_total": 0.0,
                 "bytes_proxy": 0, "bytes_direct": 0, "bytes_cache": 0}
            m["batches"].append(b)
        b["attempts"].append(rec["id"])
        if rec.get("sukses"):
            b["sukses"] += 1
        b["menit_total"] += rec.get("durasi_dtk", 0) / 60
        b["bytes_proxy"] += rec.get("bytes_proxy", 0)
        b["bytes_direct"] += rec.get("bytes_direct", 0)
        b["bytes_cache"] += rec.get("bytes_cache", 0)
    ok = [a for a in m["attempts"] if a.get("sukses")]
    m["ringkasan"] = {
        "total_attempt": len(m["attempts"]),
        "total_sukses": len(ok),
        "total_gagal": len(m["attempts"]) - len(ok),
        "rata2_menit_per_akun": round(sum(a.get("durasi_dtk", 0) for a in ok) / 60 / len(ok), 2) if ok else None,
        "total_bytes_proxy": sum(a.get("bytes_proxy", 0) for a in m["attempts"]),
        "total_bytes_direct": sum(a.get("bytes_direct", 0) for a in m["attempts"]),
        "total_bytes_cache": sum(a.get("bytes_cache", 0) for a in m["attempts"]),
    }
    save_metrics(m)


# ---------------------------------------------------------------- router per worker
def router_stats_file(port):
    return HERE / f"cache-stats-{port}.json"


def start_router(port, upstream):
    (DATA / f"upstream-{port}.json").write_text(json.dumps(upstream))
    log = open(LOGS / f"router-{port}.log", "ab")
    proc = subprocess.Popen(
        [str(konfig.MITMDUMP), "--listen-host", "127.0.0.1", "--listen-port", str(port),
         "-s", str(HERE / "router_cache.py"), "--set", "termlog_verbosity=warn",
         # [fix akar 2026-10-13] WAJIB lazy: default connection_strategy=eager
         # (addons/proxyserver.py ~155) membuka koneksi LANGSUNG ke tujuan saat
         # CONNECT (http layers handle_connect_regular ~789) lalu dipakai ulang
         # utk request = bocor ke IP VPS tanpa lewat upstream. lazy = koneksi baru
         # dibuka saat request, saat via sudah terpasang (router_cache._atur_via).
         "--set", "connection_strategy=lazy"],
        cwd=str(HERE), shell=False, start_new_session=True,
        stdout=log, stderr=subprocess.STDOUT,
        env=dict(os.environ, UPSTREAM_FILE=str(DATA / f"upstream-{port}.json"),
                 STATS_FILE=str(router_stats_file(port))),
    )
    tulis.catat_pid(konfig.PID_ROUTER, proc.pid)  # [edit brutalism] pidfile per-PID
    return proc                                     # (pengganti pola pkill -f)


def router_alive(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        return True
    except OSError:
        return False


def stats_snapshot(port):
    try:
        return json.loads(router_stats_file(port).read_text())
    except Exception:
        return {"cache_bytes_served": 0, "via_upstream": 0, "direct": 0,
                "bytes_upstream": 0, "bytes_direct": 0}


# ---------------------------------------------------------------- eksekusi 1 attempt
_PUTARAN_EXIT = 0


def _pool_satu_sesi():
    """True bila BRUTALISM_PROXY_FILE berisi <=1 sesi (sticky per-akun, dipakai
    satu-alur). Pool multi-sesi (ops/buat-pool.sh) berarti rotasi exit per attempt
    (_exit_baru) — jadi 2028 cukup GANTI SESI berikutnya, bukan mematikan slot."""
    try:
        pf = os.environ.get("BRUTALISM_PROXY_FILE")
        if not pf:
            return False
        return sum(1 for l in Path(pf).read_text().splitlines()
                   if l.strip() and not l.strip().startswith("#")) <= 1
    except Exception:
        return False


def _exit_baru(lama=None):
    """Satu exit SEGAR tiap attempt (dipakai pasang_exit per attempt+retry).
    Dahulukan exit-hijau (gate pernah tembus cepat — tulisan: <epoch>|{dict
    proxy}), fallback pool peluru (host:port:user:pass), rotasi melingkar.
    [fix audit 2026-10-09] dulu retry menempel exit lama -> 2028 beruntun."""
    global _PUTARAN_EXIT
    _PUTARAN_EXIT += 1
    # [fix 2026-10-13] BRUTALISM_PROXY_FILE = exit STICKY per-akun (satu-alur):
    # signup WAJIB di exit yang sama dgn rantai (recovery/OTP) — beda IP =
    # 10003 'pindah IP'. Dulu env ini hanya dibaca load_proxies(), tapi
    # pasang_exit selalu menimpa slot dgn rotasi exit-hijau/proxies.txt di sini
    # -> signup di IP-B, login di IP-A (bukti: login gagal HTTP 422 Code 10003).
    try:
        pf = os.environ.get("BRUTALISM_PROXY_FILE")
        if pf:
            kandidat = [x for x in (parse_proxy(l) for l in Path(pf).read_text().splitlines()) if x]
            if kandidat:
                p = kandidat[_PUTARAN_EXIT % len(kandidat)]
                return {"host": p["host"], "port": p["port"],
                        "auth": p["auth"], "label": p["label"]}
    except Exception:
        pass
    try:
        hj = DATA / "exit-hijau.txt"
        if hj.exists():
            import ast as _ast
            kandidat = []
            for l in open(hj):
                if "|" not in l:
                    continue
                ts, blob = l.split("|", 1)
                try:
                    kandidat.append((float(ts), _ast.literal_eval(blob.strip())))
                except Exception:
                    continue
            kandidat = [x for x in kandidat if __import__('time').time() - x[0] < 7200]  # [fix] sesi hijau >2 jam = kedaluwarsa -> 'muat halaman signup'
            kandidat.sort(key=lambda x: x[0])
            # [fix kritis malam] ROTASI melingkar dlm daftar hijau — dulu selalu
            # kandidat valid PERTAMA = semua attempt menumpang SATU exit ('satu
            # biji dibagi semua' lagi!) -> "muat halaman signup" gagal + 10003 massal.
            jendela = kandidat[-400:]
            mulai = _PUTARAN_EXIT % max(1, len(jendela))
            jendela = jendela[mulai:] + jendela[:mulai]
            for _ts, d in jendela:
                srv = (d.get("server") or "").replace("https://", "").replace("http://", "")
                if not srv or not d.get("username"):
                    continue   # [fix kritis v2] baris TANPA auth = busuk utk
                    #   gateway ber-otentikasi (CONNECT ditolak -> 'muat halaman
                    #   signup'); dulu dipakai fallback. Dibuang total.
                host, _, port = srv.partition(":")
                return {"host": host, "port": port or "10000",
                        "auth": f"{d.get('username', '')}:{d.get('password', '')}",
                        "label": f"{host}:{port}"}
    except Exception:
        pass
    try:
        pool = []
        for l in open(DATA / "proxies.txt"):
            l = l.strip()
            if l and not l.startswith("#") and l.count(":") >= 3:
                h, po, u, p = l.split(":", 3)
                pool.append({"host": h, "port": po, "auth": f"{u}:{p}",
                             "label": f"{h}:{po}"})
        if pool:
            return pool[_PUTARAN_EXIT % len(pool)]
    except Exception:
        pass
    return lama or {"host": "127.0.0.1", "port": "10000", "auth": "", "label": "fallback"}


def run_attempt(slot, port, retry_ke=1, no=0):
    """Jalankan satu signup. slot: {provider, domain, proxy}. Hasil dict lengkap + metrik."""
    # [speed 2026-10-14] rem velocity 15-40 -> 5-10 dtk (riset F-bedah-waktu:
    # sleep tetap = 55% durasi/akun). Bukti aman: era kini 1 sesi = 1 akun
    # (per-IP cuma 1 signup — asumsi lama "3-4 signup bersih/jam" untuk era
    # IP-berbagi sudah tidak berlaku); laju 27-30 signup/jam tetap 20/20 &
    # 10/10 bersih, nol 10003. Jitter tetap ada utk anti-velocity.
    time.sleep(random.uniform(5, 10))
    awal = time.time()
    snap = stats_snapshot(port)
    env = dict(os.environ)
    if os.environ.get("PROTON_TLS_ASLI") != "0":
        # [fix BEDAH-RISK-CONTROL-BARU 2026-11-08] DEFAULT kini TLS Chrome ASLI
        # (tunnel CONNECT — ClientHello Chrome tembus utuh ke exit). Bukti:
        # 1017/1017 run gelombang 5-8 Nov memakai MODE LAMA (env tak disetel ->
        # MITM router_cache) = Proton melihat JA3/H2 mitmproxy vs header Chrome
        # 155 -> pengangkat skor baseline 'automated anti-abuse'. Laptop user
        # (TLS asli end-to-end) selalu aman. Mode router (MITM, hemat byte) kini
        # hanya bila EKSPLISIT PROTON_TLS_ASLI=0 (utk A/B bobot, aturan #11).
        env["SIGNUP_PROXY"] = f"http://{slot['proxy']['auth']}@{slot['proxy']['host']}:{slot['proxy']['port']}"
        env["CHROME_DISK_CACHE"] = "1"   # ganti rugi cache router yg tak lagi dilalui
    else:
        # [fix 2026-10-13] default: lewat ROUTER cache — aset immutable 0 byte
        # (cache 20MB), sampah/analytics diblokir/dialihkan direct, host non-proton
        # direct, host proton -> upstream exit akun (upstream-<port>.json, sticky).
        # Bukti era router (10-11): 1104/1131 signup sukses + 16GB dari cache.
        env["SIGNUP_PROXY"] = f"http://127.0.0.1:{port}"
    if slot["provider"] == "headmail":
        env.update({"MAIL_PROVIDER": "headmail", "HM_DOMAIN": slot["domain"]})
    else:
        env.update({"MAIL_PROVIDER": "evilmail", "EVIL_DOMAIN": slot["domain"]})
    s = load_settings()
    env.setdefault("HEADMAIL_KEY", s.get("HEADMAIL_KEY", ""))
    env.setdefault("EVILMAIL_KEY", s.get("EVILMAIL_KEY", ""))
    env.setdefault("SMSBOWER_KEY", s.get("SMSBOWER_KEY", ""))
    env.setdefault("SMSBOWER_COUNTRY", s.get("SMSBOWER_COUNTRY", ""))
    # negara SMS mengikuti IP proxy (de.decodo.com -> germany, dst)
    host0 = slot["proxy"]["host"].split(".")[0].lower()
    env["PROXY_COUNTRY"] = {"de": "germany", "jp": "japan", "us": "united states", "sg": "singapore",
                            "gb": "united kingdom", "uk": "united kingdom",
                            "id": "indonesia"}.get(host0, "germany")
    attempt_file = ATTEMPTS / f"run-{int(awal)}-{slot['provider']}-{re.sub(r'[^a-z0-9]', '_', slot['domain'])}.json"
    env["RUN_RESULT_FILE"] = str(attempt_file)
    env["REGISTERED_FILE"] = str(REGISTERED_F)
    env["SHOTS_DIR"] = str(RESULTS / "shots")
    res = {"ok": False, "timeout": False}
    try:
        r = subprocess.run(
            [str(konfig.VENV_PY), str(konfig.PROTON_DIR / "full_fidelity2.py")],
            cwd=str(HERE), timeout=1200, capture_output=True, text=True, shell=False, env=env)
        res["tail"] = [l for l in r.stdout.splitlines() if not l.startswith(">> NET GET")][-8:]
        if r.stderr and r.stderr.strip():
            # [debug 2026-10-16] traceback attempt dulu dibuang percuma (hanya
            # stdout yang disimpan) -> kasus 'proses mati sebelum tulis run-json'
            # mustahil dibedah. Simpan apa adanya utk diagnosa.
            try:
                (ATTEMPTS / f"run-{int(awal)}-stderr.log").write_text(r.stderr[-12000:])
            except Exception:
                pass
    except subprocess.TimeoutExpired:
        res.update({"timeout": True, "tail": ["(timeout 20 menit)"]})
    durasi = time.time() - awal
    snap2 = stats_snapshot(port)
    d = {}
    if attempt_file.exists():
        try:
            d = json.loads(attempt_file.read_text())
        except Exception:
            pass
    # vonis bedrock: POST /users 200 = akun SUDAH dibuat (tahan-bahasa).
    # Cek "recovery phrase" lama meleset: layar Jerman & alur yang sudah lanjut
    # ke inbox justru tak memuat frasa itu -> 9 akun jadi sempat tercatat gagal.
    _net = d.get("net") or []
    sukses = (any(str(x.get("u", "")).rstrip("/").endswith("/api/core/v4/users")
                  and x.get("s") == 200 for x in _net)
              or bool(d.get("recovery_ok"))
              or "recovery phrase" in (d.get("final", {}).get("text", "").lower()))
    kode_terkirim = any("users/code" in x.get("u", "") for x in d.get("net", []))
    blokir_domain = any("85102" in (x.get("body") or "") or "temporarily disabled" in (x.get("body") or "").lower()
                        for x in d.get("net", []))
    # [fix 2026-10-13] 2028 = blokir signup per-JARINGAN (exit) — vonis definitif
    abuse_2028 = any("2028" in (x.get("body") or "") or "abusive traffic" in (x.get("body") or "").lower()
                     for x in _net)
    return {
        "id": int(awal), "ts": time.time(), "provider": slot["provider"], "domain": slot["domain"],
        "domain_blocked": blokir_domain,
        "abuse_2028": abuse_2028,
        "proxy": slot["proxy"]["label"], "port": port, "retry_ke": retry_ke,
        "durasi_dtk": round(durasi, 1), "sukses": sukses, "kode_terkirim": kode_terkirim,
        "username": d.get("username"), "alias": d.get("alias"), "timeout": res["timeout"],
        "hv_methods": d.get("hv_methods"),
        "jalur": d.get("jalur"),
        "no": no,
        "no_method": bool(d.get("no_method")),
        "solver_gagal": d.get("solver_gagal"),   # [fix 2026-10-14] sinyal retry solver
        "fp_seed": (d.get("fp_profile") or {}).get("seed") if isinstance(d.get("fp_profile"), dict) else None,
        "bytes_cache": max(0, snap2.get("cache_bytes_served", 0) - snap.get("cache_bytes_served", 0)),
        "via_upstream": max(0, snap2.get("via_upstream", 0) - snap.get("via_upstream", 0)),
        "direct": max(0, snap2.get("direct", 0) - snap.get("direct", 0)),
        "bytes_proxy": max(0, snap2.get("bytes_upstream", 0) - snap.get("bytes_upstream", 0)),
        "bytes_direct": max(0, snap2.get("bytes_direct", 0) - snap.get("bytes_direct", 0)),
        "tail": res.get("tail", []),
    }


# ---------------------------------------------------------------- orkestrator
class Orchestrator:
    """Worker paralel + batch + retry + stop darurat."""

    def __init__(self, workers=1, accounts=1, mode="oneshot", per_batch=0, batch_pause_min=0):
        self.workers = max(1, int(workers))
        self.accounts = max(1, int(accounts))
        self.mode = mode
        self.per_batch = int(per_batch) or self.accounts
        self.batch_pause = float(batch_pause_min)
        self.lock = threading.Lock()
        self.stop_flag = False
        self.consec_fail = 0
        self.consec_nomethod = 0
        self.first_strike_used = False
        self.no = 0
        self.total = 0
        self.queue = build_queue()
        self.proxies = load_proxies()
        self.metrics = load_metrics()
        self.state = load_state()
        self.routers = {}

    def _sisa_domain(self):
        return max(0, len(self.queue) - len({tuple(x) for x in self.state.get("used_emails", [])}))

    def _ts(self):
        return time.strftime("%H:%M:%S")

    def _log(self, msg):
        print(f"[{self._ts()}] {msg}", flush=True)

    def _slot_iter(self):
        """Slot: domain DIPUTAR ULANG sampai target (jalur captcha tak butuh
        verifikasi email — domain cukup jadi identitas alias). Dilewati: domain
        yang FREEZE atau yang sudah dipakai verifikasi email (used_emails)."""
        used = set(map(tuple, self.state["used_emails"]))
        frozen = self.state.get("frozen", {})
        n = 0
        for _ronde in range(50):
            for slot in self.queue:
                if n >= self.accounts:
                    return
                key = (slot["provider"], slot["domain"])
                if key in used or frozen.get(slot["domain"], 0) > time.time():
                    continue
                yield {"provider": slot["provider"], "domain": slot["domain"],
                       "proxy": self.proxies[n % len(self.proxies)]}
                n += 1

    def _ensure_router(self, wid):
        # [fix akar 2026-10-08] port unik per PROSES tui, bukan 8080+wid:
        # banyak instance tui paralel dulu semua wid=1 -> SEMUA rebutan 8081 ->
        # semua signup Proton lewat SATU exit -> blokir "abusive traffic".
        # Basis PID = dijamin beda antar instance; +wid membedakan antar worker.
        port = 12000 + (os.getpid() % 20000) + wid
        if port not in self.routers or not router_alive(port):
            self.routers[port] = start_router(port, {"proxy": "", "auth": ""})
            for _ in range(20):
                if router_alive(port):
                    break
                time.sleep(0.5)
        return port

    def _execute(self, slot, wid, batch_no=None, no=0):
        if self.stop_flag:  # slot tersisa di antrean dilewati setelah stop trigger
            return {"id": int(time.time()), "ts": time.time(), "provider": slot["provider"],
                    "domain": slot["domain"], "proxy": slot["proxy"]["label"], "port": 0,
                    "retry_ke": 0, "durasi_dtk": 0.0, "sukses": False, "kode_terkirim": False,
                    "timeout": False, "hv_methods": None, "no_method": False,
                    "domain_blocked": False, "username": None, "alias": None, "fp_seed": None,
                    "tail": ["(dilewati — stop trigger aktif)"]}
        port = self._ensure_router(wid)
        # upstream proxy diarahkan per-attempt lewat file upstream-<port>.json
        # [mode DIRECT] PROTON_DIRECT=1 -> upstream kosong (router_cache.server_connect
        # otomatis direct). Dipakai saat jaringan proxy kena blok signup (422 Code 2028
        # "blocked any further signups") sementara jalur sendiri masih bersih —
        # 0 byte proxy & tanpa hop tambahan.
        def pasang_exit():
            # [fix audit 2026-10-09] exit SEGAR tiap attempt + dahulukan exit hijau
            # (dulu: retry pakai exit YANG SAMA -> 8/8 attempt-1 kena 2028 'abusive
            # traffic', attempt-2 tanpa ganti exit = undian). Prefilter exit-hijau
            # = +25-35 poin success rate (resep audit langkah 2).
            slot["proxy"] = _exit_baru(slot.get("proxy"))
            if os.environ.get("PROTON_DIRECT") == "1":
                (DATA / f"upstream-{port}.json").write_text(json.dumps({"proxy": "", "auth": ""}))
            else:
                (DATA / f"upstream-{port}.json").write_text(json.dumps(
                    {"proxy": f"{slot['proxy']['host']}:{slot['proxy']['port']}",
                     "auth": slot["proxy"]["auth"]}))
        pasang_exit()
        with self.lock:
            self.no += 1
            no = self.no
            self._log(f"→ {no}/{self.total} · w{wid} · {slot['proxy']['label'].split(':')[0]}")
        hasil = run_attempt(slot, port, no=no)
        # [2026-11-06 arahan user: RETRY DIMATIKAN TOTAL — 2028/gagal = stop, jangan
        # diulang; slot berikut = akun berikut. Alasan: loop retry membuat uji
        # 5 akun 5 worker makan 1 jam (3 slot terjebak '↻ retry' berulang).
        while (not hasil["sukses"] and not hasil["timeout"] and hasil["retry_ke"] < 1
                and not hasil.get("domain_blocked")
                # [fix 2026-10-14] solver gagal (widget mati / kalah CV) BUKAN vonis
                # "menu tanpa captcha" — boleh retry: attempt baru = exit+profil+
                # puzzle baru. Bukti baseline run-capt-base 2026-10-14 (6/10): 3 slot
                # mati sia-sia karena no_method memblokir retry padahal keluarga
                # gagalnya transien (2x widget-stuck, 1x outcome menggantung).
                and (not hasil.get("no_method") or hasil.get("solver_gagal"))
                # [fix 2026-10-13] mode sticky (BRUTALISM_PROXY_FILE 1 sesi/akun):
                # retry dengan exit YANG SAMA setelah 2028 = pasti gagal lagi (bukti
                # '8/8 attempt-1 kena 2028') — stop cepat, hemat byte proxy; slot
                # baru = exit baru. [fix 2026-10-14] POOL multi-sesi = rotasi exit
                # per attempt -> 2028 cukup ganti sesi, jangan bunuh slot (bukti
                # baseline slot#5: kena 2028 sekali -> slot mati tanpa retry).
                and not (os.environ.get("BRUTALISM_PROXY_FILE") and hasil.get("abuse_2028")
                         and _pool_satu_sesi())
                and not self.stop_flag):
            with self.lock:
                self._log(f"↻ retry slot#{no} (percobaan {hasil['retry_ke'] + 1}/3, exit baru)")
            pasang_exit()
            hasil = run_attempt(slot, port, retry_ke=hasil["retry_ke"] + 1, no=no)
        if hasil.get("sukses"):
            try:   # [fix malam v2] exit pemenang -> daftar hijau SEGAR (prefilter
                a = slot["proxy"].get("auth") or ""     #   berikutnya makin waras)
                u, _, p = a.partition(":")
                with open(DATA / "exit-hijau.txt", "a") as f:
                    f.write(f"{int(time.time())}|{repr({'server': 'http://' + slot['proxy']['host'] + ':' + str(slot['proxy']['port']), 'username': u, 'password': p})}\n")
            except Exception:
                pass
        self._record(hasil, batch_no)  # catat SELESAI langsung (stop trigger jadi tepat waktu)
        return hasil

    def _record(self, hasil, batch_no=None):
        with self.lock:
            metrics_update(self.metrics, hasil, batch_no)
            self.state["attempts"].append({k: hasil[k] for k in
                                           ("id", "provider", "domain", "proxy", "sukses", "durasi_dtk",
                                            "retry_ke", "hv_methods")})
            if hasil["kode_terkirim"]:
                # domain hanya "dipakai" bila verifikasi EMAIL benar terkirim —
                # jalur captcha tidak butuh alias, jadi domain tidak dibakar
                # (kapasitas antrean jauh lebih besar utk run captcha-heavy)
                self.state["used_emails"].append([hasil["provider"], hasil["domain"]])
            if hasil.get("domain_blocked"):  # mode freeze: blokir domain (85102) -> beku 24 jam
                self.state["frozen"][hasil["domain"]] = time.time() + 24 * 3600
                self._log(f"❄ freeze {hasil['domain']} 24 jam (blokir domain)")
            save_state(self.state)
            progres = f"{hasil.get('no', '?')}/{self.total}"
            kapas = f"domain sisa {self._sisa_domain()} · freeze {len(self.state.get('frozen', {}))}"
            if hasil["sukses"]:
                self.consec_fail = 0
                self.consec_nomethod = 0
                self._log(f"✓ {hasil['username']} · jalur {hasil.get('jalur') or '?'} · "
                          f"{hasil['durasi_dtk'] / 60:.1f} mnt · hv={hasil.get('hv_methods')} · {progres} · {kapas}")
            elif hasil.get("no_method"):
                self.consec_nomethod += 1
                # [fix label 2026-10-13] AUDIT-TOTAL T27: no_method + hv_methods
                # terisi BUKAN selalu "solver meleset" — menu tanpa captcha =
                # linkage lintas-attempt (AGENTS.md #9). Bedakan via 'jalur':
                # captcha = solver meleset; sms = jalur sms tak terpakai;
                # selain itu = menu server tanpa captcha (linkage?).
                jalur = hasil.get("jalur") or ""
                if not hasil.get("hv_methods"):
                    ket = "tanpa vektor"
                elif jalur == "captcha":
                    ket = "solver meleset"
                elif jalur == "sms":
                    ket = "sms tak terpakai (kunci/kuota)"
                else:
                    ket = "menu tanpa captcha (linkage?)"
                self._log(f"○ slot#{hasil.get('no', '?')} · {ket} · hv={hasil.get('hv_methods')} · "
                          f"{progres} · streak {self.consec_nomethod}/5")
                if self.consec_nomethod >= 5:
                    self._log("! STOP TRIGGER: 5x berturut tanpa captcha/email — batch dihentikan")
                    self.stop_flag = True
            else:
                self.consec_fail += 1
                alasan = (hasil.get("tail") or ["?"])[-1][:40]
                self._log(f"✗ slot#{hasil.get('no', '?')} · {alasan} · hv={hasil.get('hv_methods')} · "
                          f"{progres} · gagal beruntun {self.consec_fail}")
                if self.consec_fail >= 5:
                    if not self.first_strike_used:
                        self.first_strike_used = True
                        self.consec_fail = 0
                        self._log("  !! STOP DARURAT: 5 gagal beruntun — jeda 30 menit lalu lanjut")
                        threading.Thread(target=self._nap, daemon=True).start()
                    else:
                        self.stop_flag = True
                        self._log("  !! STOP TOTAL: 5 gagal beruntun lagi — penghentian beneran")

    def _nap(self):
        self.stop_flag = True
        time.sleep(30 * 60)
        self.stop_flag = False
        self._log("  >> jeda darurat selesai — melanjutkan...")

    def run(self):
        total_batch = 1 if self.mode == "oneshot" else max(1, -(-self.accounts // self.per_batch))
        _rb = self.metrics.get("ringkasan", {})
        self._byte_awal = {k: _rb.get(k, 0) for k in
                           ("total_bytes_proxy", "total_bytes_direct", "total_bytes_cache")}
        bersih = pembersihan()  # bebaskan RAM/sisa sebelum kerja (perawatan)
        if any(bersih.values()):
            ringkas = " ".join(f"{k}={v}" for k, v in bersih.items() if v)
            self._log(f"pembersihan awal: {ringkas}")
        sisa_slot = list(self._slot_iter())
        idx = 0
        self.total = len(sisa_slot)
        self._log(f"=== {self.total} slot · {self.workers} worker · mode {self.mode} ===")
        if self.total < self.accounts:
            self._log(f"! target {self.accounts} tapi antrean hanya {self.total} slot — run berhenti di {self.total} akun")
        try:
            self._log("prewarm cache: " + str(prewarm_cache(8081)))
        except Exception as e:
            self._log(f"prewarm dilewati: {e}")
        for b in range(1, total_batch + 1):
            if self.stop_flag:
                self._log("dihentikan (stop darurat aktif)")
                break
            ambil = self.per_batch if self.mode == "batch" else self.accounts
            batch_slots = sisa_slot[idx:idx + ambil]
            idx += len(batch_slots)
            if not batch_slots:
                self._log("antrean habis")
                break
            self._log(f"batch {b}/{total_batch}: {len(batch_slots)} slot")
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futs = [ex.submit(self._execute, s, i % self.workers + 1, b) for i, s in enumerate(batch_slots)]
                for f in futs:
                    f.result()  # pencatatan sudah dilakukan tiap worker secara realtime
            if b < total_batch and self.batch_pause:
                self._log(f"jeda antar batch: {self.batch_pause} menit ...")
                time.sleep(self.batch_pause * 60)
        self._ringkasan()
        return self.metrics

    def _ringkasan(self):
        r = self.metrics.get("ringkasan", {})
        aw = getattr(self, "_byte_awal", {})
        self._log("\n=== RINGKASAN ===")
        self._log(f"sukses            : {r.get('total_sukses', 0)} / {r.get('total_attempt', 0)} attempt")
        if r.get("rata2_menit_per_akun"):
            self._log(f"rata2 per akun    : {r['rata2_menit_per_akun']} menit")
        dp = r.get('total_bytes_proxy', 0) - aw.get('total_bytes_proxy', 0)
        dd = r.get('total_bytes_direct', 0) - aw.get('total_bytes_direct', 0)
        dc = r.get('total_bytes_cache', 0) - aw.get('total_bytes_cache', 0)
        # delta run ini dulu (yg ditanya user), total kumulatif sbg pembanding —
        # angka kumulatif mentah dulu menyesatkan ("kok gede amat")
        self._log(f"byte run ini      : proxy {dp:,} ({dp / max(1, self.no) / 1024:.0f} KB/akun) "
                  f"| langsung {dd:,} | cache-lokal {dc:,}")
        self._log(f"byte kumulatif    : proxy {r.get('total_bytes_proxy', 0):,} | langsung: {r.get('total_bytes_direct', 0):,} | cache: {r.get('total_bytes_cache', 0):,}")
        self._log(f"detail            : {METRICS_F} | akun: {REGISTERED_F}")

    def stop_routers(self):
        for p in list(self.routers):
            try:
                self.routers[p].send_signal(2)
            except Exception:
                pass


# ---------------------------------------------------------------- proton checker
def login_srp(user, pw):
    """Login SRP penuh (utk akun tanpa access token): sesi anonim -> auth/info ->
    make_auth.js (bukti SRP) -> /core/v4/auth. Return {ok, status, refresh, access, uid}.
    Status error mengungkap kasus nyata: salah password / SUSPEND / dsb."""
    hdr = {"user-agent": UA, "content-type": "application/json",
           "accept": "application/vnd.protonmail.v1+json",
           "x-pm-appversion": "web-account@5.0.422.3", "x-pm-locale": "en_US",
           "origin": "https://account.proton.me",
           "referer": "https://account.proton.me/mail/signup?plan=free&noPromo=1"}

    bearer = [None]

    def post(path, body, uid=None):
        h = dict(hdr)
        if uid:
            h["x-pm-uid"] = uid
        if bearer[0]:
            h["authorization"] = "Bearer " + bearer[0]
        req = urllib.request.Request(f"https://account.proton.me{path}", method="POST",
                                     data=json.dumps(body).encode() if body is not None else b"",
                                     headers=h)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"Code": f"HTTP {e.code}"}

    st = post("/api/auth/v4/sessions", None)
    uid = st.get("UID")
    bearer[0] = st.get("AccessToken")  # token sesi -> dipakai utk semua panggilan
    if not uid:
        return {"ok": False, "status": f"sesi-anonim gagal ({st.get('Code')})"}
    info = post("/api/core/v4/auth/info", {"Username": user, "Intent": "Proton"}, uid)
    if info.get("Code") != 1000:
        return {"ok": False,
                "status": f"info {info.get('Code')} {str(info.get('Error', ''))[:36]}"}
    proof = subprocess.run(
        ["node", str(HERE / "make_auth.js"), "login", json.dumps({
            "modulusArmored": info.get("Modulus", ""), "password": pw,
            "saltB64": info.get("Salt", ""), "serverEphemeralB64": info.get("ServerEphemeral", "")})],
        cwd=str(HERE), capture_output=True, text=True, timeout=60)
    if proof.returncode != 0:
        return {"ok": False, "status": f"srp gagal: {(proof.stderr or '')[:50]}"}
    srp = json.loads(proof.stdout)
    auth = post("/api/core/v4/auth", {
        "ClientEphemeral": srp["ClientEphemeral"], "ClientProof": srp["ClientProof"],
        "SRPSession": info.get("SRPSession"), "Username": user,
        "PersistentCookies": 0}, uid)
    if auth.get("Code") != 1000:
        err = str(auth.get("Error", ""))[:40]
        rendah = err.lower()
        label = ("SUSPEND" if any(w in rendah for w in ("suspend", "disabled", "deactivat"))
                 else "LOGIN-DITOLAK")
        return {"ok": False, "status": f"{label} {err}".strip()}
    return {"ok": True, "status": "AKTIF (login)",
            "refresh": auth.get("RefreshToken"), "access": auth.get("AccessToken"),
            "uid": auth.get("UID") or uid}


def _sesi_anonim():
    """Ambil token sesi anonim SEKALI -> dipakai (Bearer) utk semua uji akun."""
    try:
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        req = urllib.request.Request("https://account.proton.me/api/auth/v4/sessions",
                                     method="POST", data=b"",
                                     headers={"user-agent": UA, "content-type": "application/json",
                                              "accept": "application/vnd.protonmail.v1+json",
                                              "x-pm-appversion": "web-account@5.0.422.3",
                                              "x-pm-locale": "en_US",
                                              "origin": "https://account.proton.me"})
        with op.open(req, timeout=25) as r:
            st = json.loads(r.read().decode())
        return st.get("AccessToken"), st.get("UID"), op
    except Exception:
        return None, None, None


def _vonis_login(pg, mail, pw, timeout_dtk=45):
    """Isi form login + baca hasil: SUSPEND / SALAH-PASSWORD / AKTIF."""
    try:
        pg.goto("https://account.proton.me/login", wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)
        pg.locator("#username, input[type=email], input[type=text]").first.fill(mail)
        pg.locator("#password, input[type=password]").first.fill(pw)
        pg.locator("button[type=submit]").first.click()
        akhir = time.time() + timeout_dtk
        while time.time() < akhir:
            time.sleep(2)
            t = (pg.evaluate("() => (document.body.innerText||'').slice(0,800)") or "").lower()
            url = pg.url
            if "suspend" in t or "anti-abuse" in t:
                return "SUSPEND (anti-abuse)"
            if "not correct" in t or "incorrect" in t or "wrong password" in t:
                return "SALAH-PASSWORD"
            if "two-factor" in t or "2fa" in t or "authentication code" in t:
                return "AKTIF (login, 2FA)"
            if any(s in url for s in ("/inbox", "/mail", "/apps", "/u/")) or "welcome" in t or "recovery phrase" in t:
                return "AKTIF (login)"
        potong = " ".join((pg.evaluate("() => (document.body.innerText||'').slice(0,80)") or "").split())
        return f"TIMEOUT ({url[:35]}) [{potong}]"
    except Exception as e:
        return f"error {type(e).__name__}: {str(e)[:50]}"


def cek_login_banyak(pairs, hasil, workers=1):
    """Vonis login utk banyak akun: SATU browser per worker, context baru tiap akun
    (stabil & hemat vs launch browser tiap akun)."""
    import fp_profile
    fp_profile.ensure_display()
    prof = fp_profile.build_profile()
    # checker: tanpa proxy BERBAYAR, tapi lewat ROUTER LOKAL -> asset utk cache
    # disk (0 byte berulang), hanya HTML/API kecil yang langsung (hemat kuota ~10x)
    launch = dict(executable_path=str(konfig.CHROME_BIN), headless=False,
                  args=fp_profile.chrome_args(prof))
    ruter = start_router(8089, {"proxy": "", "auth": ""})
    for _ in range(20):
        if router_alive(8089):
            break
        time.sleep(0.5)
    launch["proxy"] = {"server": "http://127.0.0.1:8089"}
    n = max(1, int(workers))
    bagi = [[] for _ in range(n)]
    for idx, item in enumerate(pairs):
        bagi[idx % n].append(item)
    _total, _selesai, _kunci = len(pairs), [0], threading.Lock()

    def kerjakan(chunk):
        with fp_profile.sync_playwright() as p:
            b = p.chromium.launch(**launch)
            try:
                for i, mail, pw in chunk:
                    time.sleep(random.uniform(1, 5))
                    try:
                        ctx = b.new_context(locale=prof["locale"], timezone_id="Europe/Berlin",
                                            viewport=prof["viewport"], user_agent=prof["ua"],
                                            ignore_https_errors=True)
                        pg = ctx.new_page()
                        pg.add_init_script(fp_profile.init_script(prof))
                        hasil[i] = _vonis_login(pg, mail, pw)
                        ctx.close()
                    except Exception as e:
                        hasil[i] = f"error {type(e).__name__}: {str(e)[:50]}"
                    stt = hasil.get(i, "?")
                    tanda = "✓" if stt.startswith("AKTIF") else (
                        "⚠" if stt.startswith(("SUSPEND", "TIMEOUT")) else "✗")
                    with _kunci:
                        _selesai[0] += 1
                        print(f"  [{_selesai[0]}/{_total}] {tanda} {mail} — {stt[:45]}",
                              flush=True)
            finally:
                try:
                    b.close()
                except Exception:
                    pass

    try:
        with ThreadPoolExecutor(max_workers=n) as ex:
            for chunk in bagi:
                if chunk:
                    ex.submit(kerjakan, chunk)
    finally:
        try:
            ruter.terminate()
        except Exception:
            pass


def check_accounts(cek_login=False, workers=1):
    """Cek semua akun dgn SATU sesi anonim (Bearer) utk semua panggilan.
    cek_login=True -> vonis akhir lewat LOGIN BROWSER sungguhan (lambat tapi
    satu-satunya cara membaca status 'Account suspended' anti-abuse), dikerjakan
    `worker` paralel. Akun berstatus SUSPEND otomatis DIKELUARKAN dari
    registered.txt (diarsipkan dulu ke registered-suspended.txt)."""
    if not REGISTERED_F.exists():
        return []
    if cek_login:
        # ---- vonis login browser (paralel) + auto-bersih suspend ----
        lines = [l for l in REGISTERED_F.read_text().splitlines() if l.strip()]
        hasil = {}

        pairs = []
        for i, line in enumerate(lines):
            cols = (line.split("|") + [""] * 5)[:5]
            if cols[1]:
                pairs.append((i, cols[0], cols[1]))
            else:
                hasil[i] = "TANPA-PASSWORD (lewati)"
        cek_login_banyak(pairs, hasil, workers=workers)
        out, simpan, buang = [], [], []
        for i, line in enumerate(lines):
            mail = line.split("|")[0]
            stt = hasil.get(i, "?")
            out.append({"email": mail, "status": stt})
            (buang if "SUSPEND" in stt else simpan).append(line)
        if buang:
            arsip = RESULTS / "registered-suspended.txt"
            with open(arsip, "a") as f:
                f.write("\n".join(buang) + "\n")
            # merge AMAN (pelajaran: rewrite dari hasil baca lama menimpa baris
            # yg di-append batch berjalan -> 327 akun sempat hilang). Baca ULANG
            # saat menulis, buang HANYA baris suspend yang terdeteksi.
            buang_mail = {l.split("|")[0].strip() for l in buang}
            with tulis.kunci_file(REGISTERED_F):   # [fix race 2026-11-08]
                # merge read-modify-write TANPA kunci = baris baru yg di-append
                # pabrik di tengah jendela baca->tulis ikut TERHAPUS (korban:
                # cor0050or 8 Nov & clauscla5015 6 Nov — 'akun ditambahkan' ada
                # di run-json tapi baris lenyap; komentar '327 akun sempat hilang'
                # di atas = leluhur bug yg sama). Kini terkunci lintas-proses
                # dgn tulis.tambah pabrik (satu mekanisme kunci_file).
                kini = [l for l in REGISTERED_F.read_text().splitlines() if l.strip()]
                REGISTERED_F.write_text(
                    "\n".join(l for l in kini if l.split("|")[0].strip() not in buang_mail)
                    + ("\n" if kini else ""))
        return out
    bearer, suid, op = _sesi_anonim()
    if op is None:
        import urllib.request as _u
        op = _u.build_opener()
    out = []
    _baris = [l for l in REGISTERED_F.read_text().splitlines() if l.strip()]
    _n = [0]
    for line in _baris:
        if not line.strip():
            continue
        mail, pw, rt, cid, at = (line.split("|") + [""] * 5)[:5]
        status = "MATI"
        try:
            check_url_public("https://account.proton.me/api/core/v4/auth/cookies")
            hdr = {"user-agent": UA, "content-type": "application/json",
                   "accept": "application/vnd.protonmail.v1+json",
                   "x-pm-uid": suid or cid, "x-pm-appversion": "web-account@5.0.422.3",
                   "x-pm-locale": "en_US"}
            if bearer:
                hdr["authorization"] = "Bearer " + bearer
            req = urllib.request.Request(
                "https://account.proton.me/api/auth/v4/sessions", method="POST",   # [fix] endpoint benar (dulu /auth/cookies -> 401)
                data=json.dumps({"UID": cid, "ResponseType": "token", "GrantType": "refresh_token",
                                 "ClientID": "WebAccount",
                                 "RefreshToken": rt, "RedirectURI": "https://protonmail.com",
                                 "Persistent": 0, "State": "chk"}).encode(), headers=hdr)
            try:
                with op.open(req, timeout=25) as r:
                    st = json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                try:
                    st = json.loads(e.read().decode())
                except Exception:
                    st = {"Code": f"HTTP {e.code}"}
            if cek_login and pw:
                status = cek_login_browser(mail, pw)  # vonis login sesungguhnya
                out.append({"email": mail, "status": status})
                time.sleep(random.uniform(3, 8))  # jeda manusiawi antar login
                continue
            if st.get("Code") == 1000:
                status = "TOKEN AKTIF (belum tentu bebas suspend — pakai mode login)"
            else:
                err = str(st.get("Error", "")).lower()
                if any(w in err for w in ("suspend", "disabled", "deactivat")):
                    status = f"SUSPEND {st.get('Error', '')[:40]}"
                elif pw:
                    lg = login_srp(mail, pw)  # vonis kedua via password
                    status = lg["status"]
                else:
                    status = f"refresh {st.get('Code')} (tanpa password — tak bisa login ulang)"
        except Exception as e:
            status = f"error {type(e).__name__}"
        _n[0] += 1
        _t = "✓" if status.startswith(("AKTIF", "TOKEN AKTIF")) else (
            "⚠" if status.startswith(("SUSPEND", "TIMEOUT", "refresh")) else "✗")
        print(f"  [{_n[0]}/{len(_baris)}] {_t} {mail} — {status[:45]}", flush=True)
        out.append({"email": mail, "status": status})
    return out


# ---------------------------------------------------------------- kebersihan & prewarm
KEKAL = ("modal", "pilihan-", "pasca-recovery", "pasca-verifikasi", "sukses",
         "blokir", "tanpa-vektor")  # screenshot bukti riset: jangan dipangkas


def pembersihan(dry=False):
    """Perawatan (tanpa menyentuh logic): bebaskan RAM dari Chrome toolkit yang
    nyangkut, hapus profil /tmp bekas crash, pangkas screenshot & conf font lama.
    dry=True = hanya hitung, tidak menghapus. Return ringkasan dict."""
    import shutil
    hasil = {"chrome_dibunuh": 0, "profil_tmp": 0, "screenshot": 0, "fc": 0, "bebas_byte": 0}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
        except Exception:
            continue
        if "/opt/google/chrome" in cmd and ("--ozone-platform=x11" in cmd
                                            or "--disable-blink-features=AutomationControlled" in cmd):
            # [FIX silang-tembak 2026-10-09] dulu SEMUA chrome ber-flag ini dibunuh
            # -- termasuk milik worker lain yang SEDANG jalan (tiap tui start =
            # sapuan -> TargetClosed/CdpError berantai -> 10 worker kolaps).
            # Kini hanya YATIM (ppid=1 / induk sudah mati) yang dibersihkan.
            try:
                _stat = Path(f"/proc/{pid}/stat").read_text()
                _ppid = int(_stat[_stat.rfind(")") + 1:].split()[1])
            except Exception:
                _ppid = -1
            if _ppid not in (0, 1):
                continue
            hasil["chrome_dibunuh"] += 1
            if not dry:
                try:
                    os.kill(int(pid), 15)
                except Exception:
                    pass
    for pola in ("playwright*", ".com.google.Chrome.*", ".org.chromium.Chromium*"):
        for d in Path("/tmp").glob(pola):
            try:
                sz = (sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                      if d.is_dir() else d.stat().st_size)
                hasil["profil_tmp"] += 1
                hasil["bebas_byte"] += sz
                if not dry:
                    (shutil.rmtree(d, ignore_errors=True) if d.is_dir()
                     else d.unlink(missing_ok=True))
            except Exception:
                pass
    # [fix 2026-10-13] key stat() anti-balapan: worker paralel saling hapus
    # saat trim -> FileNotFoundError tak terbukti (rc=1 massal di GATE-A).
    def _mtim(f):
        try:
            return f.stat().st_mtime
        except OSError:
            return 0.0

    shots_dir = RESULTS / "shots"
    if shots_dir.exists():
        shots = sorted((f for f in shots_dir.iterdir() if f.is_file()),
                       key=_mtim, reverse=True)
        bukti = {f for f in shots if any(k in f.name for k in KEKAL)}
        sisanya = [f for f in shots if f not in bukti]
        for f in sisanya[100:]:  # selain bukti: sisakan 100 terbaru
            try:
                hasil["bebas_byte"] += f.stat().st_size
                hasil["screenshot"] += 1
                if not dry:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
    fc_dir = HERE / "cache" / "fc"
    if fc_dir.exists():
        fcs = sorted((f for f in fc_dir.glob("fc-*.conf") if f.is_file()),
                     key=_mtim, reverse=True)   # [fix 2026-10-13] anti-balapan
        for f in fcs[50:]:
            try:
                hasil["bebas_byte"] += f.stat().st_size
                hasil["fc"] += 1
                if not dry:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
    return hasil


def prewarm_cache(port=None):
    """Tarik 1x halaman signup (tanpa isi form) lewat router supaya semua asset
    immutable masuk cache — worker paralel berikutnya 100% dari cache (menghapus
    dobel-fetch dingin x N worker). Dipanggil otomatis di awal batch."""
    cache_dir = HERE / "cache" / "account.proton.me"
    n_cache = len(list(cache_dir.iterdir())) if cache_dir.exists() else 0
    if n_cache >= 25:
        return f"cache sudah hangat ({n_cache} file) — dilewati"
    proc = None
    if port is None or not router_alive(port):
        port = 8089
        proc = start_router(port, {"proxy": "", "auth": ""})
        for _ in range(20):
            if router_alive(port):
                break
            time.sleep(0.5)
    env = dict(os.environ)
    # [fix 2026-10-13] prewarm WAJIB lewat router (cache diisi). Dulu baris
    # [kerak] disalin ke sini dgn merujuk `slot` yang tak ada -> NameError
    # tersembunyi (caller hanya "prewarm dilewati") -> cache tak pernah hangat.
    env["SIGNUP_PROXY"] = f"http://127.0.0.1:{port}"
    try:
        r = subprocess.run([str(konfig.VENV_PY), str(HERE / "prewarm.py")],
                           cwd=str(HERE), env=env, timeout=150, capture_output=True, text=True)
        msg = "selesai" if r.returncode == 0 else f"rc={r.returncode}"
    except Exception as e:
        msg = f"gagal {type(e).__name__}"
    finally:
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
    n_cache = len(list(cache_dir.iterdir())) if cache_dir.exists() else 0
    return f"{msg} — cache kini {n_cache} file"
