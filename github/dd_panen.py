"""dd_panen.py — pemetik cookie datadome (adaptasi lab3-ref/01-panen_dd.py).

ATURAN EMAS asli (dibayar mahal di produksi — JANGAN dilanggar):
1. Chrome HARUS headed di Xvfb — JANGAN --headless (DataDome mendeteksi headless).
2. CDP hanya meng-enable `Page` — `Network.enable`/`Runtime.enable` MEMBUNUH
   interstitial; Runtime.evaluate per-ekspresi tanpa Runtime.enable.
3. TIDAK ada slider yang digeser — panaskan homepage -> klik Sign up -> biarkan
   interstitial selesai SENDIRI. Slider visual = gagal apa adanya (tidak ada solver).
4. TIDAK ada refresh sama sekali (435x refresh semalam = nihil).
Urutan: precheck IP dulu (tak terbaca = gagal TANPA buka Chrome) -> profil
pakai-buang -> Xvfb -> Chrome -> homepage -> klik /signup -> poll -> cookies.

Shim lab3 diganti milik kita: DISPLAY :99 (Xvfb sudah jalan), port_bebas socket,
profil di results/dd-profil (hapus setelah selesai).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

AKAR = Path(__file__).resolve().parent.parent / "results" / "dd-profil"
CHROME = os.getenv("DD_CHROME", "/usr/bin/google-chrome")
IP_TIMEOUT = 15.0
DD_URL = os.getenv("DD_URL", "https://github.com/")


class Mint(dict):
    """dict hasil: dd_cookies, dd_ua {'user_agent':..}, exit_ip, reason, error."""

    @property
    def dd_ok(self):
        return any(c.get("name") == "datadome" for c in self.get("dd_cookies") or [])


def _gagal(reason, error=""):
    return Mint(dd_cookies=[], dd_ua={}, exit_ip="", reason=reason, error=error[:180])


def _arsip(nama, isi):
    """[bedah 2026-10-14] simpan cuplikan halaman saat mint GAGAL —
    tanpa bukti visual kita hanya menebak penyebab chrome-error/link hilang."""
    try:
        d = AKAR.parent / "dd-debug"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{int(time.time())}-{nama}.txt"
        p.write_text(str(isi or "")[:400000])
        return str(p)
    except Exception:
        return ""


def cek_exit_ip(listener_url, timeout=IP_TIMEOUT):
    """Precheck ipify lewat proxy sebelum browser lahir. Kosong = tak sehat."""
    if not listener_url:
        return "direct"
    buka = urllib.request.build_opener(urllib.request.ProxyHandler(
        {"http": listener_url, "https": listener_url}))
    try:
        r = buka.open(urllib.request.Request(
            "https://api.ipify.org", headers={"User-Agent": "curl/8.0"}),
            timeout=timeout)
        ip = (r.read().decode() or "").strip()
        return ip if r.status == 200 and ip else ""
    except Exception:
        return ""


def profil_sekali_pakai(akar: Path = AKAR):
    (akar / "dd").mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=akar / "dd", prefix="profil-run-"))
    # [fix hemat-byte 2026-10-14] salin cache hangat dari template p0 — identitas
    # profil tetap BARU (hukum lab3/ghreg: fresh profile), yang disalin hanya
    # byte cache statis. Bukti: mint polos = ~3,9MB aset/mint (4/4 mati kena
    # BATAS_BYTE px_forward); resep lab3 pun cache aset (02-resep-operasional §8).
    # Saklar: DD_CACHE_WARM=0 -> perilaku lama (profil dingin).
    if os.environ.get("DD_CACHE_WARM", "1") == "1":
        try:
            (d / "Default").mkdir(exist_ok=True)
            for sub in ("Cache", "Code Cache"):
                s = akar / "p0" / "Default" / sub
                if s.is_dir():
                    shutil.copytree(s, d / "Default" / sub, dirs_exist_ok=True)
        except Exception:
            pass
    return d


def bendera_chrome(user_data_dir, cdp_port, proxy_url=""):
    return [
        # [adaptasi] --no-sandbox & --disable-dev-shm-usage WAJIB utk root/Xvfb
        # kita (bug uji-1: chrome_exit_early — Chrome menolak start sbg root
        # tanpa --no-sandbox; di mesin asal lab3 bukan root).
        "--no-sandbox", "--disable-dev-shm-usage",
        "--no-first-run", "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        # [fix 2026-10-15 — akar access_restricted] kontrak env gasak: WebGL
        # WAJIB hidup generik (jangan null). Terukur di Xvfb ini: tanpa trio
        # berikut getContext('webgl') = null (lab fase-1); dengan trio =
        # renderer 'ANGLE (Google, Vulkan ... SwiftShader)' seperti laptop
        # tanpa GPU diskrit — lingkungan seluruh jalur terbukti user
        # (Camoufox WebGL hidup / ARGS_DASAR gasak). DataDome menghitung
        # sinyal WebGL dalam payload plv2.
        "--use-angle=swiftshader", "--use-gl=angle", "--enable-unsafe-swiftshader",
        "--ozone-platform=x11", "--disable-crash-reporter",
        "--disable-crashpad", "--disable-background-networking",
        "--disable-component-update",
        "--disable-component-extensions-with-background-pages",
        "--disable-extensions", "--disable-sync", "--disable-quic",
        "--disable-domain-reliability",
        "--disable-features=DnsOverHttps,MediaRouter",
        "--variations-server-url=", "--variations-disable-limited-entropy",
        "--password-store=basic", "--metrics-recording-only",
        "--window-size=1400,900", "--window-position=60,60",
        f"--user-data-dir={user_data_dir}",
        f"--remote-debugging-port={cdp_port}",
        *(["--proxy-server=" + proxy_url] if proxy_url else []),
        "about:blank",
    ]


def _port_bebas():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


STATE_JS = """
(() => {
  const html = document.documentElement ? document.documentElement.outerHTML : "";
  const txt = (document.body && document.body.innerText) || "";
  return JSON.stringify({
    url: location.href, title: document.title, bodyLen: html.length,
    rate_limit: (document.title || "").toLowerCase().includes("too many requests"),
    form: !!document.querySelector('form input[name="authenticity_token"]')
       || !!document.querySelector('form[action*="signup"]'),
    dd: html.includes("var dd") ||
        !!document.querySelector('script[src*="captcha-delivery"]'),
    slide: txt.includes("Slide right") || txt.includes("Verification Required"),
    // [RE DataDome 2026-10-15] ddst = status POST /interstitial/ milik widget:
    // null = POST belum balik (macet di koleksi sinyal), 200 = putusan datang
    // tapi macet setelahnya, non-200 = server menolak. ifr = URL widget —
    // berubah ke /captcha/ berarti kalah putusan (bukan macet).
    ddst: (typeof window._ddst === "undefined" ? null : window._ddst),
    ifr: (document.querySelector('iframe') ? document.querySelector('iframe').src : "")
  });
})()
"""

# [port ghregv2 2026-10-15, kode milik user — terbukti "aman-aman saja"] scan
# geometri slider DI DALAM iframe captcha: handle = kotak 25-90px paling kiri,
# track = batang 200-420px lebar.
GEOMETRI_SLIDER_JS = """
(() => {
  const els = Array.from(document.querySelectorAll('*'));
  const boxes = els.map(el => { const r = el.getBoundingClientRect();
      return {x: r.x, y: r.y, w: r.width, h: r.height}; })
    .filter(b => b.w > 25 && b.w < 90 && b.h > 25 && b.h < 90);
  boxes.sort((a, b) => a.x - b.x);
  const track = els.map(el => { const r = el.getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height}; })
      .filter(b => b.w > 200 && b.w < 420 && b.h > 30 && b.h < 90)[0];
  return JSON.stringify({handle: boxes[0] || null, track: track || null});
})()
"""


async def _coba_slider(cdp, sesi, catat, percobaan=2):
    """Drag slider DataDome dgn gerak manusiawi — port ghregv2 user:
    event mouse LEVEL CDP (Input.dispatchMouseEvent = TRUSTED, mekanisme yang
    sama dgn Playwright; event sintetis JS justru terdeteksi), easing kuadrat
    + jitter + jeda acak. Geometri dibaca lewat attach OOPIF iframe captcha +
    Runtime.evaluate per-panggilan (tanpa enable domain — stealth terjaga).
    Return True bila halaman captcha hilang dari daftar target."""
    for ke in range(percobaan):
        luar = await cdp.eval_js(
            "(() => { const el = document.querySelector('iframe[src*=\"captcha-delivery\"]');"
            " if (!el) return null; const r = el.getBoundingClientRect();"
            " return JSON.stringify({x: r.x, y: r.y, w: r.width, h: r.height}); })()", sesi)
        # [fix 2026-10-15] halaman captcha butuh waktu RENDER setelah redirect —
        # ghregv2 memanggil solver setelah halaman termuat penuh; kita sebelumnya
        # scan instan -> "geometri tidak ditemukan". Ulang scan s/d 5x @1 dtk.
        geo = None
        for _tunggu in range(5):
            try:
                infos = (await cdp.send("Target.getTargets", {})).get("targetInfos", [])
                tid = next((t.get("targetId") for t in infos
                            if "captcha-delivery.com/captcha" in (t.get("url") or "")), None)
                if tid:
                    a = await cdp.send("Target.attachToTarget",
                                       {"targetId": tid, "flatten": True})
                    geo = json.loads(await cdp.eval_js(GEOMETRI_SLIDER_JS,
                                                       a.get("sessionId")) or "null")
            except Exception as exc:
                catat(f"slider: geometri iframe gagal ({type(exc).__name__})")
                break
            if geo and geo.get("handle") and geo.get("track"):
                break
            await asyncio.sleep(1.0)
        if not luar or not geo or not geo.get("handle") or not geo.get("track"):
            cuplik = ""
            try:
                cuplik = ((await cdp.eval_js(
                    "document.body ? document.body.innerText.slice(0, 200) : ''",
                    a.get("sessionId"))) or "").replace("\n", " | ")
            except Exception:
                pass
            catat(f"slider: geometri handle/track tidak ditemukan "
                  f"(tunggu 5x) — isi iframe: {cuplik[:160]}")
            # [klasifikasi 2026-10-15] bukan slider melainkan BLOKIR keras
            # GitHub/DataDome ("Access is temporarily restricted" — state
            # 'restricted' ala ghregv2 = BLOCKED): tak terselamatkan, ganti
            # identitas. Beda dari slider asli yang tetap layak disolve.
            if "temporarily restricted" in cuplik.lower():
                return "restricted"
            return False
        luar = json.loads(luar)
        h, tr = geo["handle"], geo["track"]
        sx = luar["x"] + h["x"] + h["w"] / 2
        sy = luar["y"] + h["y"] + h["h"] / 2
        ex = luar["x"] + tr["x"] + tr["w"] - h["w"] / 2 - 4
        catat(f"slider: drag #{ke + 1} x={sx:.0f} -> {ex:.0f}")

        async def gerak(tipe, x, y, tombol=None):
            p = {"type": tipe, "x": x, "y": y}
            if tombol:
                p.update(tombol)
            await cdp.send("Input.dispatchMouseEvent", p, session_id=sesi)

        await gerak("mouseMoved", sx, sy)
        await asyncio.sleep(random.uniform(0.2, 0.4))
        await gerak("mousePressed", sx, sy, {"button": "left", "clickCount": 1})
        await asyncio.sleep(random.uniform(0.08, 0.2))
        langkah = random.randint(26, 36)
        for i in range(1, langkah + 1):
            t = i / langkah
            ease = 2 * t * t if t < 0.5 else -1 + (4 - 2 * t) * t
            await gerak("mouseMoved",
                        sx + (ex - sx) * ease + random.uniform(-1.5, 1.5),
                        sy + random.uniform(-2, 2),
                        {"button": "left", "buttons": 1})
            await asyncio.sleep(0.03 if random.random() < 0.12
                                else random.uniform(0.006, 0.016))
        await gerak("mouseMoved", ex, sy, {"button": "left", "buttons": 1})
        await asyncio.sleep(random.uniform(0.06, 0.14))
        await gerak("mouseReleased", ex, sy, {"button": "left", "clickCount": 1})
        catat(f"slider: drag #{ke + 1} selesai — tunggu 6 dtk")
        await asyncio.sleep(6)
        try:
            infos = (await cdp.send("Target.getTargets", {})).get("targetInfos", [])
            if not any("captcha-delivery.com/captcha" in (t.get("url") or "")
                       for t in infos):
                return True
        except Exception:
            pass
    return False


class _CDP:
    """WebSocket CDP minimal — hanya Page.enable; Runtime.evaluate per panggilan."""

    def __init__(self, ws_url):
        self._url, self._ws, self._id, self._pending = ws_url, None, 0, {}

    async def __aenter__(self):
        import websockets
        self._ws = await websockets.connect(self._url, max_size=64 * 1024 * 1024)
        asyncio.get_running_loop().create_task(self._pompa())
        return self

    async def __aexit__(self, *a):
        try:
            await self._ws.close()
        except Exception:
            pass

    async def _pompa(self):
        try:
            async for mentah in self._ws:
                msg = json.loads(mentah)
                fut = self._pending.pop(msg.get("id"), None)
                if fut is not None and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(str(msg["error"])[:160]))
                    else:
                        fut.set_result(msg.get("result", {}))
        except asyncio.CancelledError:
            raise
        except Exception:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("CDP closed"))
            self._pending.clear()

    async def send(self, method, params=None, session_id=None, timeout=30.0):
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._id] = fut
        await self._ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, timeout)

    async def eval_js(self, expr, session_id, await_promise=False):
        r = await self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True,
            "awaitPromise": await_promise}, session_id=session_id)
        if r.get("exceptionDetails"):
            return None
        return (r.get("result") or {}).get("value")


def panen(listener_url="", timeout=None, log=None):
    """Mint dd_cookie: precheck IP -> Chrome headed Xvfb -> interstitial sendiri.
    [fix 2026-10-15] ADOPT_MINT_TIMEOUT (default 20, aturan user): interstitial
    sehat selesai 3-10 dtk (data uji); yang mentok TIDAK akan sembuh (68 arsip
    stuck, nol pulih) — bail makin cepat = sesi ke-2 dicoba lebih awal.
    Tombstone (ADOPT_TOMBSTONE=1 di driver) + ganti sesi = obatnya."""
    if timeout is None:
        timeout = float(os.environ.get("ADOPT_MINT_TIMEOUT", "20"))
    catat = log or (lambda s: None)

    exit_ip = cek_exit_ip(listener_url)
    if not exit_ip:
        return _gagal("proxy_unhealthy",
                      "exit IP tak terbaca lewat proxy — browser tidak dibuka")
    catat(f"exit IP sehat: {exit_ip}" if exit_ip != "direct" else "jalur direct")

    user_data_dir = profil_sekali_pakai()
    catat(f"profil pakai-buang {user_data_dir.name}")
    chrome = None
    hasil = None
    try:
        # Xvfb: pastikan :99 hidup dulu (adaptasi — lab3 pakai mulai_xvfb sendiri)
        try:
            import fp_lite
            fp_lite.ensure_display()
        except Exception:
            pass
        display = os.environ.get("DD_DISPLAY", "99")
        env = {k: v for k, v in os.environ.items()
               if k not in ("WAYLAND_DISPLAY",)}
        env["DISPLAY"] = f":{display}"
        port = _port_bebas()
        chrome = subprocess.Popen(
            [CHROME, *bendera_chrome(user_data_dir, port, listener_url)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            hasil = asyncio.run(_alur(chrome, port, timeout, exit_ip, catat))
        except Exception as exc:
            hasil = _gagal("mint_exception", f"{type(exc).__name__}: {exc}")
        finally:
            try:
                chrome.terminate()
                chrome.wait(timeout=5)
            except Exception:
                try:
                    chrome.kill()
                except Exception:
                    pass
    finally:
        # [fix hemat-byte 2026-10-14] panaskan template p0 dari profil yang
        # baru SUKSES — mint berikutnya mulai hangat (muat pertama saja mahal).
        try:
            if (os.environ.get("DD_CACHE_WARM", "1") == "1"
                    and hasil is not None and getattr(hasil, "dd_ok", False)):
                for sub in ("Cache", "Code Cache"):
                    s = user_data_dir / "Default" / sub
                    if s.is_dir():
                        shutil.copytree(s, AKAR / "p0" / "Default" / sub,
                                        dirs_exist_ok=True)
        except Exception:
            pass
        shutil.rmtree(user_data_dir, ignore_errors=True)
    return hasil


async def _alur(chrome, cdp_port, timeout, exit_ip, catat):
    """Homepage -> klik /signup -> tunggu interstitial selesai SENDIRI."""
    versi = None
    batas = time.monotonic() + 20
    while time.monotonic() < batas:
        if chrome.poll() is not None:
            return _gagal("chrome_exit_early", "chrome keluar saat startup")
        try:
            versi = json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{cdp_port}/json/version", timeout=3).read())
            break
        except Exception:
            await asyncio.sleep(0.4)
    if not versi:
        return _gagal("chrome_devtools_timeout", "/json/version tak menjawab")

    async with _CDP(versi["webSocketDebuggerUrl"]) as cdp:
        target = await cdp.send("Target.createTarget", {"url": "about:blank"})
        sesi = (await cdp.send("Target.attachToTarget",
                               {"targetId": target["targetId"],
                                "flatten": True}))["sessionId"]
        await cdp.send("Page.enable", session_id=sesi)

        await cdp.send("Page.navigate", {"url": DD_URL}, session_id=sesi)
        for _ in range(60):
            if await cdp.eval_js("document.readyState", sesi) == "complete":
                break
            await asyncio.sleep(0.5)
        judul = await cdp.eval_js("document.title", sesi) or ""
        catat(f"homepage ok ({judul[:40]})")
        # [target user 2026-10-14] jeda dipadatkan 4.0 -> 2.5 (ghreg-A mengukur
        # 2dtk aman: "cookie dd tidak pernah muncul di homepage 10/10").
        # Saklar: DD_JEDA_HOMEPAGE=4 utk nilai asli.
        await asyncio.sleep(float(os.environ.get("DD_JEDA_HOMEPAGE", "2.5")))

        if not await cdp.eval_js(
                '!!document.querySelector(\'a[href^="/signup"]\')', sesi):
            ar = _arsip("no-signup-link",
                        (await cdp.eval_js("document.documentElement.outerHTML", sesi)
                         or "") + "\n---INNERTEXT---\n"
                        + (await cdp.eval_js("document.body.innerText", sesi) or ""))
            return _gagal("signup_link_not_found",
                          f"link signup tidak ada (arsip: {ar})")
        await cdp.eval_js(
            'document.querySelector(\'a[href^="/signup"]\').click()', sesi)
        catat("klik Sign up")

        batas = time.monotonic() + timeout
        terakhir = None
        hitung = 0

        async def _captcha_senyap():
            """[RISET 2026-10-15] location.replace di dalam iframe TIDAK mengubah
            atribut src (deteksi lama buta); baca URL NYATA target OOPIF lewat
            Target.getTargets — pasif, tanpa injeksi/tanpa enable domain baru."""
            try:
                infos = (await cdp.send("Target.getTargets", {})).get("targetInfos", [])
            except Exception:
                return False
            for t in infos:
                if "captcha-delivery.com/captcha" in (t.get("url") or ""):
                    return True
            return False

        while time.monotonic() < batas:
            hitung += 1
            mentah = await cdp.eval_js(STATE_JS, sesi)
            state = json.loads(mentah) if mentah else None
            if state != terakhir:
                terakhir = state
                catat(f"state: {state}")
            if not state:
                await asyncio.sleep(0.8)
                continue
            if hitung % 3 == 0 and await _captcha_senyap():
                # [port ghregv2 user 2026-10-15] kalah putusan bukan akhir:
                # coba selesaikan slider-nya (drag trusted + easing manusiawi).
                # Saklar DD_SLIDER=0 utk matikan (kembali langsung gagal).
                if os.environ.get("DD_SLIDER", "1") == "1":
                    hasil_slider = await _coba_slider(cdp, sesi, catat)
                    if hasil_slider is True:
                        catat("slider TERPECAHKAN — tunggu form muncul")
                        batas = time.monotonic() + 15      # waktu ekstra form
                        terakhir = None
                        hitung = 0
                        continue
                    if hasil_slider == "restricted":
                        return _gagal(
                            "access_restricted",
                            "GitHub blokir keras 'temporarily restricted' "
                            "(identitas/exit ditandai — ganti, tak tersolve)")
                return _gagal("visual_captcha_redirect",
                              "DataDome kalah putusan -> halaman captcha "
                              "(URL iframe nyata via Target.getTargets)")
            # TIDAK ADA refresh — interstitial dibiarkan selesai sendiri.
            if str(state.get("url") or "").startswith("chrome-error://"):
                ar = _arsip("nav-error",
                            (await cdp.eval_js(
                                "document.body ? document.body.innerText : ''", sesi)
                             or "") + "\n---HTML---\n"
                            + (await cdp.eval_js("document.documentElement.outerHTML", sesi)
                               or ""))
                return _gagal("nav_error",
                              f"navigasi terputus (halaman error Chrome) (arsip: {ar})")
            judul = (state.get("title") or "").lower()
            if "too many requests" in judul or state.get("rate_limit"):
                return _gagal("rate_limit", "GitHub membatasi (Too many requests)")
            if state.get("slide"):
                # [fix 2026-10-21 poin-5, arahan user] slider visual BUKAN
                # akhir — solver _coba_slider SUDAH ADA (dipakai jalur
                # _captcha_senyap); pola lama langsung gagal "memang tidak ada
                # solver" = keliru & membuang sesi yang masih layak (resep
                # user: geser slider -> form -> lanjut).
                if os.environ.get("DD_SLIDER", "1") == "1":
                    hasil_slider = await _coba_slider(cdp, sesi, catat)
                    if hasil_slider is True:
                        catat("slider (state.slide) TERPECAHKAN — tunggu form")
                        batas = time.monotonic() + 15
                        terakhir = None
                        hitung = 0
                        continue
                    if hasil_slider == "restricted":
                        return _gagal(
                            "access_restricted",
                            "GitHub blokir keras 'temporarily restricted' "
                            "(identitas/exit ditandai — ganti, tak tersolve)")
                return _gagal("visual_slider_rt_c",
                              "slider visual gagal diselesaikan solver")
            if state.get("form") and not state.get("dd") and not state.get("slide"):
                break
            await asyncio.sleep(0.8)
        else:
            ar = _arsip("stuck",
                        json.dumps(terakhir) + "\n---HTML---\n"
                        + (await cdp.eval_js("document.documentElement.outerHTML", sesi)
                           or ""))
            return _gagal("interstitial_stuck_timeout",
                          f"interstitial tidak selesai sendiri dalam {timeout:.0f} dtk "
                          f"(arsip: {ar})")

        catat(f"form signup muncul; sisa {batas - time.monotonic():.0f} dtk")
        # [fix 2026-10-21 poin-5, bedah HAR] browser asli mengirim beacon
        # perilaku (POST api-js.datadome.co/js/) + gerak mouse sebelum cookie
        # dipakai; pola lama panen setelah 1,2 dtk = sering lebih cepat dari
        # beacon -> jar cookie 'tak pernah dilihat halaman'. Gerak mouse CDP
        # (trusted) + tunggu beacon maks DD_BEACON_MAKS dtk, lalu settle.
        try:
            for mx, my in ((320, 240), (380, 300), (300, 260)):
                await cdp.send("Input.dispatchMouseEvent",
                               {"type": "mouseMoved", "x": mx, "y": my},
                               session_id=sesi)
                await asyncio.sleep(random.uniform(0.15, 0.35))
        except Exception:
            pass
        beacon_ok = False
        for _ke in range(int(float(os.environ.get("DD_BEACON_MAKS", "6")))):
            await asyncio.sleep(1.0)
            try:
                beacon_ok = bool(await cdp.eval_js(
                    "performance.getEntriesByType('resource')"
                    ".some(e => e.name.includes('api-js.datadome'))", sesi))
            except Exception:
                beacon_ok = False
            if beacon_ok:
                break
        catat(f"beacon datadome: {'terkirim' if beacon_ok else 'belum (lanjut)'}")
        await asyncio.sleep(float(os.environ.get("DD_JEDA_SETTLE", "1.2")))
        mentah = (await cdp.send("Storage.getCookies", {}))["cookies"]
        cookies = [{k: c[k] for k in ("name", "value", "domain", "path",
                                      "expires", "secure", "httpOnly",
                                      "sameSite") if c.get(k) is not None}
                   for c in mentah if "github.com" in (c.get("domain") or "")]
        if not any(c["name"] == "datadome" for c in cookies):
            return _gagal("datadome_cookie_missing", "cookie datadome tidak terpanen")

        catat(f"{len(cookies)} cookie github terpanen")
        ua = await cdp.eval_js("navigator.userAgent", sesi) or ""
        return Mint(dd_cookies=cookies, dd_ua={"user_agent": ua},
                    exit_ip="" if exit_ip == "direct" else exit_ip,
                    masuk=time.time())
