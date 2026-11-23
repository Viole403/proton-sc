#!/usr/bin/env python3
"""github/playwright_gate.py — pengganti cdp_gate.pinjam_browser() yang tidak
butuh cdp_lite. Pakai Playwright sync API langsung dengan Chrome/Chromium.

Interface yang sama seperti cdp_gate:
  b = pinjam_browser(proxy="host:port:user:pass", headless=True)
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
"""
from __future__ import annotations

import os
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

    def new_page(self):
        return _Page(self._ctx.new_page())

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

    def content(self):
        return self._page.content()

    def evaluate(self, script, *args):
        return self._page.evaluate(script, *args)

    def click(self, selector, **kw):
        return self._page.click(selector, **kw)

    def locator(self, selector):
        return _Locator(self._page.locator(selector))


class _Locator:
    def __init__(self, locator):
        self._locator = locator

    def click(self, **kw):
        return self._locator.click(**kw)

    def scroll_into_view_if_needed(self, **kw):
        return self._locator.scroll_into_view_if_needed(**kw)


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


def pinjam_browser(proxy=None, headless=True, executable_path=None):
    """Launch Chrome/Chromium via Playwright dan return _Browser wrapper.

    proxy: "host:port:user:pass" atau None (direct)
    headless: bool
    executable_path: path ke Chrome biner, default dari konfig.CHROME_BIN
    """
    if executable_path is None:
        executable_path = str(konfig.CHROME_BIN)

    proxy_dict = _parse_proxy(proxy) if proxy else None

    p = sync_playwright().start()
    launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
    browser = p.chromium.launch(
        executable_path=executable_path,
        headless=headless,
        args=launch_args,
    )
    return _Browser(browser, p)
