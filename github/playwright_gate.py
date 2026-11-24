#!/usr/bin/env python3
"""github/playwright_gate.py — pengganti cdp_gate.pinjam_browser() yang tidak
butuh cdp_lite. Pakai Playwright sync API dengan stealth (playwright-stealth)
dan profil persisten agar terlihat seperti browser asli.

Opsional: Camoufox (Firefox anti-detect) untuk bypass DataDome pada
IP residential tanpa proxy. Camoufox adalah async-only, jadi dipanggil lewat
asyncio.run() dari wrapper sync.

Interface yang sama seperti cdp_gate:
  b = pinjam_browser(proxy="host:port:user:pass", headless=False,
                     user_data_dir="/tmp/proton-sc-profile",
                     use_camoufox=True)
  ctx = b.new_context()
  page = ctx.new_page()
  page.goto(url, ...)
  page.wait_for_selector(...)
  page.content()
  page.evaluate(...)
  page.click(...)
  cookies = ctx.cookies()
  ctx.close()
  b.close()

Proxy format: "host:port:user:pass" (sama dengan data/proxies.txt).

Fitur anti-deteksi:
  * playwright-stealth (navigator.webdriver = false, HeadlessChrome dihilangkan)
  * Profil persisten (user_data_dir) — cookie & session history tersimpan
  * Headful mode (headless=False) — browser terlihat nyata
  * Human-like typing & klik dengan jeda acak
  * Chrome args untuk menyembunyikan sinyal automation
  * Camoufox mode — Firefox fingerprint spoofing, cocok untuk DataDome
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from pathlib import Path

# brutalism: jalur lintas-folder
import os as _os_b, sys as _sys_b
for _d_b in ("inti", "proton", "github", "cb", "cek", "vendor"):
    _p_b = _os_b.path.join(
        _os_b.path.dirname(_os_b.path.dirname(_os_b.path.abspath(__file__))), _d_b)
    if _p_b not in _sys_b.path:
        _sys_b.path.insert(0, _p_b)

import konfig  # noqa: E402,F401

from playwright.sync_api import sync_playwright
try:
    from playwright_stealth import Stealth  # noqa: E402,F401
    HAS_STEALTH = True
except Exception:
    HAS_STEALTH = False

try:
    from camoufox.async_api import AsyncCamoufox  # noqa: E402,F401
    HAS_CAMOUFOX = True
except Exception:
    HAS_CAMOUFOX = False


# ---------------------------------------------------------------- util acak
def _jeda(min_d=0.3, max_d=1.2):
    """Jeda acak seperti jeda manusia."""
    time.sleep(random.uniform(min_d, max_d))


def _gerak_mouse(page, x=None, y=None):
    """Gerak mouse ke koordinasi acak atau target — mencegah deteksi
    pola klik tanpa gerakan mouse (behavioral biometrics)."""
    try:
        page.mouse.move(
            x or random.randint(100, 800),
            y or random.randint(100, 600),
            steps=random.randint(5, 15),
        )
        _jeda(0.1, 0.5)
    except Exception:
        pass


def _tulis_manusia(locator, teks, min_d=0.05, max_d=0.25):
    """Ketik teks karakter per karakter dengan jeda acak seperti manusia."""
    for ch in teks:
        locator.type(ch, delay=random.randint(int(min_d * 1000), int(max_d * 1000)))
        _jeda(0.02, 0.1)


# ---------------------------------------------------------------- wrapper class
class _Browser:
    """Wrapper agar Playwright Browser punya antarmuka seperti cdp_gate."""
    def __init__(self, browser, p):
        self._browser = browser
        self._p = p

    def new_context(self, **kw):
        return _Context(self._browser.new_context(**kw))

    def close(self):
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._p.stop()
        except Exception:
            pass


class _Context:
    """Wrapper agar Playwright BrowserContext punya antarmuka seperti cdp_gate."""
    def __init__(self, ctx):
        self._ctx = ctx
        self._stealth_applied = False

    def new_page(self):
        page = _Page(self._ctx.new_page())
        if not self._stealth_applied and HAS_STEALTH:
            try:
                Stealth().apply_stealth_sync(page)
                self._stealth_applied = True
            except Exception:
                pass
        return page

    def cookies(self):
        return self._ctx.cookies()

    def close(self):
        try:
            self._ctx.close()
        except Exception:
            pass


class _Page:
    """Wrapper agar Playwright Page punya antarmuka seperti cdp_gate."""
    def __init__(self, page):
        self._page = page

    def goto(self, url, **kw):
        return self._page.goto(url, **kw)

    def wait_for_selector(self, selector, **kw):
        return self._page.wait_for_selector(selector, **kw)

    def wait_for_timeout(self, ms):
        return self._page.wait_for_timeout(ms)

    def content(self):
        return self._page.content()

    def evaluate(self, script, *args):
        return self._page.evaluate(script, *args)

    def click(self, selector, **kw):
        _gerak_mouse(self._page)
        return self._page.click(selector, **kw)

    def locator(self, selector):
        return _Locator(self._page.locator(selector))


class _Locator:
    def __init__(self, locator):
        self._locator = locator

    def click(self, **kw):
        _gerak_mouse(self._locator)
        return self._locator.click(**kw)

    def type(self, text, **kw):
        _tulis_manusia(self._locator, text)
        return self

    def scroll_into_view_if_needed(self, **kw):
        return self._locator.scroll_into_view_if_needed(**kw)


# ---------------------------------------------------------------- proxy parser
def _parse_proxy(proxy_str):
    """host:port:user:pass -> dict(server, username, password) atau None."""
    if not proxy_str:
        return None
    parts = proxy_str.split(":")
    if len(parts) < 2:
        return None
    host, port = parts[0], parts[1]
    user = parts[2] if len(parts) > 2 else None
    pw = parts[3] if len(parts) > 3 else None
    pr = {"server": f"http://{host}:{port}"}
    if user:
        pr["username"] = user
    if pw:
        pr["password"] = pw
    return pr


# ---------------------------------------------------------------- entry point
def pinjam_browser(proxy=None, headless=False, executable_path=None,
                   user_data_dir=None, channel="chrome", use_camoufox=False):
    """Launch Chrome/Chromium via Playwright dan return _Browser wrapper.

    proxy: "host:port:user:pass" atau None (direct)
    headless: bool (disarankan False untuk menghindari deteksi headless)
    executable_path: path ke Chrome biner, default dari konfig.CHROME_BIN
    user_data_dir: path ke profil persisten (cookie/session tersimpan)
    channel: "chrome" atau "chromium"
    use_camoufox: pakai Camoufox (Firefox anti-detect) untuk DataDome bypass.
                  Camoufox bekerja tanpa proxy pada raw IP residential.
    """
    if use_camoufox:
        if not HAS_CAMOUFOX:
            raise RuntimeError("Camoufox tidak terpasang: pip install camoufox")
        return _CamoufoxBrowser(headless=headless, proxy=proxy)

    if executable_path is None:
        executable_path = str(konfig.CHROME_BIN)

    proxy_dict = _parse_proxy(proxy) if proxy else None

    p = sync_playwright().start()

    # Chrome args untuk menyembunyikan sinyal automation
    launch_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-site-isolation-trials",
    ]

    launch_kw = dict(
        executable_path=executable_path,
        headless=headless,
        args=launch_args,
        channel=channel,
    )
    if user_data_dir:
        launch_kw["user_data_dir"] = user_data_dir
    if proxy_dict:
        launch_kw["proxy"] = proxy_dict

    browser = p.chromium.launch(**launch_kw)
    return _Browser(browser, p)


# ---------------------------------------------------------------- Camoufox wrapper
class _CamoufoxPage:
    """Wrapper Camoufox Page agar memiliki antarmuka yang sama dengan _Page."""
    def __init__(self, page, loop):
        self._page = page
        self._loop = loop

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def goto(self, url, **kw):
        return self._run(self._page.goto(url, **kw))

    def wait_for_selector(self, selector, **kw):
        return self._run(self._page.wait_for_selector(selector, **kw))

    def wait_for_timeout(self, ms):
        return self._run(self._page.wait_for_timeout(ms))

    def content(self):
        return self._run(self._page.content())

    def evaluate(self, script, *args):
        return self._run(self._page.evaluate(script, *args))

    def click(self, selector, **kw):
        return self._run(self._page.click(selector, **kw))

    def locator(self, selector):
        return _CamoufoxLocator(self._page.locator(selector), self._loop)


class _CamoufoxLocator:
    def __init__(self, locator, loop):
        self._locator = locator
        self._loop = loop

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def click(self, **kw):
        return self._run(self._locator.click(**kw))

    def type(self, text, **kw):
        return self._run(self._locator.type(text, delay=random.randint(30, 80)))

    def scroll_into_view_if_needed(self, **kw):
        return self._run(self._locator.scroll_into_view_if_needed(**kw))


class _CamoufoxContext:
    def __init__(self, context, loop):
        self._ctx = context
        self._loop = loop

    def new_page(self):
        return _CamoufoxPage(self._run(self._ctx.new_page()), self._loop)

    def cookies(self):
        return self._run(self._ctx.cookies())

    def close(self):
        self._run(self._ctx.close())


class _CamoufoxBrowser:
    """Wrapper Camoufox Browser agar memiliki antarmuka yang sama dengan _Browser."""
    def __init__(self, headless=False, proxy=None):
        self._headless = headless
        self._proxy = proxy
        self._camoufox = None
        self._browser = None
        self._context = None
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def new_context(self, **kw):
        if self._browser is None:
            self._camoufox = self._loop.run_until_complete(AsyncCamoufox(
                humanize=True,
                geoip=bool(self._proxy),
                proxy=self._parse_camoufox_proxy(self._proxy),
                headless="virtual" if self._headless else False,
                os="windows",
            ))
            self._browser = self._loop.run_until_complete(self._camoufox.start())
            self._context = self._loop.run_until_complete(self._browser.new_context(
                locale="en-US"))
        return _CamoufoxContext(self._context, self._loop)

    def close(self):
        try:
            self._loop.run_until_complete(self._context.close())
        except Exception:
            pass
        try:
            self._loop.run_until_complete(self._browser.close())
        except Exception:
            pass
        try:
            self._loop.run_until_complete(self._camoufox.stop())
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass

    @staticmethod
    def _parse_camoufox_proxy(proxy_str):
        """host:port:user:pass -> Camoufox proxy dict atau None."""
        if not proxy_str:
            return None
        parts = proxy_str.split(":")
        if len(parts) < 2:
            return None
        pr = {"server": f"http://{parts[0]}:{parts[1]}"}
        if len(parts) > 2:
            pr["username"] = parts[2]
        if len(parts) > 3:
            pr["password"] = parts[3]
        return pr
