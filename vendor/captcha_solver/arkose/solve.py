"""Arkose FunCaptcha solver — vendored from cf-solver (VeyraAgent/cf-solver).

Ported into vendor/captcha_solver/ so the repo has no external runtime dep on
cf-solver. Uses Playwright sync API (via github/playwright_gate) for browser
driving and vendored ONNX models for challenge prediction.

Flow: navigate to page_url → trigger Arkose → intercept /fc/gfct →
download challenge image → predict via ONNX → encrypt answer (CryptoJS AES-CBC)
→ submit to /fc/ca/ → return token.

Answer submission format:
  POST /fc/ca/ with form-urlencoded:
    session_token, game_token, sid, guess, render_type, analytics_tier,
    bio, is_compatibility_mode, ecdata

  guess = CryptoJS.AES.encrypt(JSON.stringify([{index: N}]), session_token)
  bio = base64 encoded mouse motion data
  ecdata = base64('{"height": 450, "width": 400}')
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import tempfile
import time
import urllib.parse
from pathlib import Path

# brutalism: jalur lintas-folder
import os as _os_b, sys as _sys_b
for _d_b in ("inti", "proton", "github", "cb", "cek", "vendor"):
    _p_b = _os_b.path.join(
        _os_b.path.dirname(_os_b.path.dirname(_os_b.path.abspath(__file__))), _d_b)
    if _p_b not in _sys_b.path:
        _sys_b.path.insert(0, _p_b)

import konfig  # noqa: E402,F401

from github import playwright_gate  # noqa: E402,F401
from .predict import predict as onnx_predict, cryptojs_encrypt  # noqa: E402,F401

log = logging.getLogger("arkose")

_ECDATA = base64.b64encode(b'{"height": 450, "width": 400}').decode()


def _generate_bio() -> str:
    """Generate minimal mouse motion bio data (base64 encoded)."""
    motion_parts = []
    ts = random.randint(5, 100)
    ts += random.randint(120, 420)
    x, y = random.randint(100, 300), random.randint(100, 350)
    motion_parts.append(f"{ts},0,{x},{y};")
    for _ in range(random.randint(8, 15)):
        ts += random.randint(6, 55)
        x += random.randint(-20, 20)
        y += random.randint(-20, 20)
        x = max(20, min(380, x))
        y = max(36, min(414, y))
        motion_parts.append(f"{ts},0,{x},{y};")
    ts += random.randint(30, 110)
    motion_parts.append(f"{ts},0,{x},{y};")
    ts += random.randint(60, 180)
    motion_parts.append(f"{ts},1,{x},{y};")
    ts += random.randint(60, 180)
    motion_parts.append(f"{ts},2,{x},{y};")

    motion_str = "".join(motion_parts)
    bio_json = json.dumps({"mbio": motion_str, "tbio": "", "kbio": ""})
    return base64.b64encode(bio_json.encode()).decode()


def _extract_challenge(gfct: dict) -> dict:
    game_data = gfct.get("game_data", {})
    custom_gui = game_data.get("customGUI", {})
    return {
        "instruction": game_data.get("instruction_string", ""),
        "challenge_imgs": custom_gui.get("_challenge_imgs", []),
        "session_token": gfct.get("session_token", ""),
        "challenge_id": gfct.get("challengeID", ""),
        "sec": gfct.get("sec", ""),
        "sid": gfct.get("sid", ""),
        "waves": game_data.get("waves", 1),
        "game_type": game_data.get("gameType", 4),
    }


def solve_arkose(public_key: str, page_url: str | None = None,
                 game_type: str = "4", proxy: str | None = None,
                 timeout_s: int = 120, max_attempts: int = 10) -> dict:
    """Solve Arkose FunCaptcha using sync Playwright + ONNX prediction.

    Returns dict with 'solved', 'token', 'method', 'waves', 'elapsed' on
    success, or 'solved': False with 'error' on failure.
    """
    if not public_key:
        return {"solved": False, "error": "public_key is required"}
    if not page_url:
        return {"solved": False, "error": "page_url is required"}

    models_dir = Path(__file__).parent / "models"
    if not models_dir.is_dir() or not any(models_dir.glob("*.onnx")):
        return {
            "solved": False,
            "error": (
                "arkose models missing: place ONNX files in "
                "vendor/captcha_solver/arkose/models/ (~1.4GB). "
                "Not shipped by the repo."
            ),
            "elapsed": 0.0,
        }

    t_start = time.monotonic()
    gfct_data: dict = {}
    ca_result: dict = {}
    arkose_domain: str = ""
    gt2_denied = False
    gt2_locked = False
    gt2_status = None
    net_hits: list[str] = []

    try:
        b = playwright_gate.pinjam_browser(
            proxy=proxy, headless=os.environ.get("ARKOSE_HEADLESS", "1") == "1")
        ctx = b.new_context()
        page = ctx.new_page()

        captured = {"gfct": None, "ca": None, "domain": ""}

        def _on_response(response):
            try:
                url = response.url
                low = url.lower()
                if any(x in low for x in ("/fc/gfct", "gfct", "/fc/gt2",
                                          "arkoselabs", "funcaptcha")):
                    if len(net_hits) < 40:
                        net_hits.append(f"{response.status} {url[:160]}")
                if "/fc/gfct" in low or low.rstrip("/").endswith("gfct"):
                    try:
                        body = response.body()
                        if body:
                            data = json.loads(body.decode("utf-8", errors="replace"))
                            if isinstance(data, dict) and (
                                    data.get("session_token")
                                    or data.get("challengeID")
                                    or data.get("game_data")):
                                captured["gfct"] = data
                                from urllib.parse import urlparse
                                captured["domain"] = (
                                    urlparse(url).netloc or captured["domain"])
                    except Exception:
                        pass
                elif "/fc/ca/" in low:
                    try:
                        body = response.body()
                        if body:
                            captured["ca"] = json.loads(
                                body.decode("utf-8", errors="replace"))
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", _on_response)

        # gt2 route interception
        def _gt2_route(route):
            nonlocal gt2_denied, gt2_locked, gt2_status
            try:
                r = route.fetch()
                body = r.body().decode("utf-8", errors="replace")
                gt2_status = r.status
                if "DENIED ACCESS" in body:
                    gt2_denied = True
                elif r.status >= 400 and (
                        "lock" in body.lower()
                        or ("site" in body.lower() and "invalid" in body.lower())):
                    gt2_locked = True
                route.fulfill(response=r)
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass

        try:
            page.route("**/fc/gt2/**", _gt2_route)
        except Exception:
            pass

        log.info("arkose: navigating to %s", page_url)

        stub = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>arkose-local</title>
<script>
window.__arkose_token='';
window.__arkose_err='';
window.__arkose_ready=false;
window.__arkose_shown=false;
window.setupEnforcement=function(myEnforcement){{
  try{{
    myEnforcement.setConfig({{
      publicKey: '{public_key}',
      language: 'en',
      selector: '#arkose-host',
      onCompleted: function(r){{
        try{{ window.__arkose_token=(r&&r.token)||''; }}catch(e){{}}
      }},
      onSuppress: function(r){{
        try{{ window.__arkose_token=(r&&r.token)||window.__arkose_token||''; }}catch(e){{}}
      }},
      onReady: function(){{
        window.__arkose_ready=true;
        try{{ myEnforcement.run(); }}catch(e){{ window.__arkose_err=String(e); }}
      }},
      onShown: function(){{ window.__arkose_shown=true; }},
      onError: function(r){{
        try{{ window.__arkose_err=JSON.stringify(r||{{}}); }}catch(e){{ window.__arkose_err=String(r); }}
      }},
      onFailed: function(r){{
        try{{ window.__arkose_err='failed:'+JSON.stringify(r||{{}}); }}catch(e){{}}
      }}
    }});
  }}catch(e){{ window.__arkose_err=String(e); }}
}};
</script>
<script src="https://client-api.arkoselabs.com/v2/{public_key}/api.js"
  data-callback="setupEnforcement" async defer
  onerror="window.__arkose_err='api.js load failed'"></script>
</head><body style="margin:0;background:#f5f5f5">
<button id="arkose-host" type="button" style="margin:40px;padding:16px 28px;font-size:18px">
  Start Challenge
</button>
</body></html>"""

        from urllib.parse import urlsplit
        parts = urlsplit(page_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        mode = "real"

        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=35000)
            has_ark = page.evaluate(
                """() => !!(window.arkoseLabsClientApi || document.querySelector(
                  'script[src*="arkoselabs"],script[src*="funcaptcha"]'))""")
            if not has_ark:
                page.evaluate(
                    """(pk) => {
                      if (window.__arkose_injected) return;
                      window.__arkose_injected = true;
                      window.__arkose_token = '';
                      window.__arkose_err = '';
                      window.__arkose_ready = false;
                      window.setupEnforcement = function (myEnforcement) {
                        try {
                          myEnforcement.setConfig({
                            publicKey: pk,
                            language: 'en',
                            selector: '#arkose-host-inject',
                            onCompleted: function (r) {
                              window.__arkose_token = (r && r.token) || '';
                            },
                            onSuppress: function (r) {
                              window.__arkose_token = (r && r.token) || window.__arkose_token || '';
                            },
                            onReady: function () {
                              window.__arkose_ready = true;
                              try { myEnforcement.run(); } catch (e) { window.__arkose_err = String(e); }
                            },
                            onError: function (r) {
                              try { window.__arkose_err = JSON.stringify(r || {}); } catch (e) {}
                            }
                          });
                        } catch (e) { window.__arkose_err = String(e); }
                      };
                      var host = document.getElementById('arkose-host-inject');
                      if (!host) {
                        host = document.createElement('button');
                        host.id = 'arkose-host-inject';
                        host.textContent = 'Start Challenge';
                        host.style.cssText = 'position:fixed;top:12px;left:12px;z-index:2147483646;padding:12px';
                        document.body.appendChild(host);
                      }
                      var s = document.createElement('script');
                      s.src = 'https://client-api.arkoselabs.com/v2/' + pk + '/api.js';
                      s.setAttribute('data-callback', 'setupEnforcement');
                      s.async = true;
                      document.head.appendChild(s);
                    }""",
                    public_key)
                mode = "real+inject"
        except Exception as e:
            log.warning("arkose real nav failed (%s); stub", e)
            mode = "stub"

            def _origin_doc(route):
                try:
                    if route.request.resource_type == "document":
                        route.fulfill(
                            body=stub, status=200,
                            content_type="text/html; charset=utf-8")
                    else:
                        route.continue_()
                except Exception:
                    try:
                        route.continue_()
                    except Exception:
                        pass

            try:
                page.route(f"{origin}/**", _origin_doc)
                page.goto(origin + "/", wait_until="domcontentloaded",
                          timeout=30000)
            except Exception as e2:
                return {"solved": False,
                        "error": f"navigation failed: {e2}",
                        "elapsed": round(time.monotonic() - t_start, 1)}

        log.info("arkose: mode=%s", mode)

        # Wait for gfct / onCompleted
        wait_n = max(15, min(int(timeout_s) - 35, 50))
        last_err = ""
        last_ready = False
        arkose_token_js = ""

        for i in range(wait_n):
            if captured["gfct"] or arkose_token_js:
                break
            if gt2_denied or gt2_locked:
                break
            if time.monotonic() - t_start > max(20, int(timeout_s) - 30):
                break
            try:
                st = page.evaluate(
                    """() => ({
                      token: window.__arkose_token || '',
                      err: window.__arkose_err || '',
                      ready: !!window.__arkose_ready,
                      shown: !!window.__arkose_shown,
                    })""")
                last_ready = bool(st.get("ready"))
                if st.get("token") and len(st["token"]) > 40:
                    arkose_token_js = st["token"]
                    break
                if st.get("err"):
                    last_err = str(st["err"])[:300]
                    if ("API_REQUEST_ERROR" in last_err
                            or '"status":400' in last_err):
                        if i > 10:
                            break
            except Exception:
                pass
            if i in (1, 4, 8, 15):
                for sel in ("#arkose-host", "#arkose-host-inject", "button",
                            "[data-callback]", "iframe"):
                    try:
                        page.click(sel, timeout=800)
                        break
                    except Exception:
                        pass
            time.sleep(1)

        if arkose_token_js and not captured["gfct"]:
            return {
                "solved": True,
                "token": arkose_token_js,
                "method": "enforcement-onCompleted",
                "elapsed": round(time.monotonic() - t_start, 1),
            }

        if not captured["gfct"]:
            if gt2_denied:
                err = ("arkose gt2 DENIED ACCESS — IP-reputation gate "
                       "(datacenter/WARP ranges blocked); retry via "
                       "residential proxy")
            elif gt2_locked:
                err = ("arkose gt2 rejected public_key (site-locked) — run "
                       "on the real site origin for this key (page_url)")
            else:
                err = "no gfct response"
            return {
                "solved": False, "error": err,
                "gt2_status": gt2_status, "gt2_denied": gt2_denied,
                "arkose_err": last_err or None, "ready": last_ready,
                "mode": mode, "net_hits": net_hits[:15],
                "elapsed": round(time.monotonic() - t_start, 1),
            }

        gfct_data = captured["gfct"]
        ca_result = captured["ca"] or {}
        arkose_domain = captured["domain"]

        guesses = []

        for wave in range(max_attempts):
            if time.monotonic() - t_start > timeout_s:
                break

            info = _extract_challenge(gfct_data)
            instruction = info["instruction"]
            challenge_imgs = info["challenge_imgs"]
            session_token = info["session_token"]
            challenge_id = info["challenge_id"]
            sid = info["sid"]

            log.info("arkose wave %d: %s imgs=%d sid=%s",
                     wave, instruction, len(challenge_imgs), sid)

            if not challenge_imgs:
                break

            img_path = None
            try:
                img_bytes = page.evaluate(
                    """async (url) => {
                        const r = await fetch(url);
                        return Array.from(new Uint8Array(await r.arrayBuffer()));
                    }""", challenge_imgs[0])

                if len(img_bytes) < 100:
                    continue

                with tempfile.NamedTemporaryFile(suffix=".png",
                                                 delete=False) as tmp:
                    tmp.write(bytes(img_bytes))
                    img_path = tmp.name

                from PIL import Image
                img = Image.open(img_path)
                answer = onnx_predict(img, instruction)
                if answer is None:
                    log.warning("arkose wave %d: prediction failed", wave)
                    continue

                log.info("arkose wave %d: answer=%d", wave, answer)

                # Find an Arkose-origin frame
                game_frame = None
                for frame in page.frames:
                    if "game-core" in (frame.url or ""):
                        game_frame = frame
                        break
                if not game_frame:
                    for frame in page.frames:
                        if "arkoselabs" in (frame.url or ""):
                            game_frame = frame
                            break
                if not game_frame:
                    log.warning("arkose wave %d: no Arkose frame", wave)
                    continue

                game_type_str = str(info.get("game_type", 4))
                if game_type_str == "0":
                    game_type_str = "4"

                if wave == 0:
                    enforcement_url = gfct_data.get("challengeURL", "")
                    base_payload = urllib.parse.urlencode({
                        "sid": sid, "session_token": session_token,
                        "analytics_tier": "15", "disableCookies": "false",
                        "render_type": "canvas",
                        "is_compatibility_mode": "false",
                    })
                    a1 = (base_payload
                          + "&category=Site+URL&action="
                          + urllib.parse.quote(enforcement_url))
                    a2 = (base_payload
                          + f"&game_token={urllib.parse.quote(challenge_id)}"
                          + f"&game_type={game_type_str}"
                          + "&category=loaded&action=game+loaded")
                    a3 = (base_payload
                          + f"&game_token={urllib.parse.quote(challenge_id)}"
                          + f"&game_type={game_type_str}"
                          + "&category=begin+app&action=user+clicked+verify")
                    a_url = f"https://{arkose_domain}/fc/a/"
                    for label, body_str in [("Site URL", a1),
                                            ("game loaded", a2),
                                            ("user clicked verify", a3)]:
                        game_frame.evaluate(
                            """async ({url, body}) => {
                                await fetch(url, {method:'POST',
                                    headers:{'Content-Type':
                                    'application/x-www-form-urlencoded;
                                    charset=UTF-8',
                                    'X-Requested-With':'XMLHttpRequest'},
                                    body:body, credentials:'include'});
                            }""",
                            {"url": a_url, "body": body_str})
                    log.info("arkose: sent3 action updates to /fc/a/")

                guesses.append({"index": answer})
                guess_json = json.dumps(guesses, separators=(",", ":"))

                bio = _generate_bio()
                analytics_tier = "15"
                ca_url = f"https://{arkose_domain}/fc/ca/"
                encrypted_guess = cryptojs_encrypt(guess_json, session_token)

                result = game_frame.evaluate(
                    """async ({guess, sessionToken, gameToken, sid,
                              gameType, analyticsTier, bio, ecdata, caUrl}) => {
                        const params = new URLSearchParams();
                        params.set('session_token', sessionToken);
                        params.set('game_token', gameToken);
                        params.set('sid', sid);
                        params.set('guess', guess);
                        params.set('render_type', 'canvas');
                        params.set('analytics_tier', analyticsTier);
                        params.set('bio', bio);
                        params.set('is_compatibility_mode', 'false');
                        params.set('ecdata', ecdata);

                        const r = await fetch(caUrl, {
                            method: 'POST',
                            headers: {
                                'Content-Type':
                                'application/x-www-form-urlencoded;
                                charset=UTF-8',
                                'X-Requested-With':'XMLHttpRequest'
                            },
                            body: params.toString(),
                            credentials: 'include'
                        });
                        return {status: r.status, response: await r.text()};
                    }""", {
                        "guess": encrypted_guess,
                        "sessionToken": session_token,
                        "gameToken": challenge_id,
                        "sid": sid,
                        "gameType": game_type_str,
                        "analyticsTier": analytics_tier,
                        "bio": bio,
                        "ecdata": _ECDATA,
                        "caUrl": ca_url,
                    })

                log.info("arkose wave %d: ca result: %s",
                         wave, str(result)[:300])

                if result.get("error"):
                    log.warning("arkose wave %d: error: %s",
                                wave, result["error"])
                    guesses.pop()
                    continue

                try:
                    resp_data = json.loads(result.get("response", "{}"))
                except (json.JSONDecodeError, TypeError):
                    resp_data = {}

                solved = resp_data.get("solved", False)
                solved_token = resp_data.get("token", "")
                response_str = resp_data.get("response", "")

                if solved and solved_token:
                    elapsed = round(time.monotonic() - t_start, 1)
                    log.info("arkose: SOLVED in %.1fs (wave %d)",
                             elapsed, wave + 1)
                    return {
                        "solved": True, "token": solved_token,
                        "method": "onnx-predict",
                        "waves": wave + 1, "elapsed": elapsed,
                    }

                if response_str == "answered":
                    if solved_token:
                        elapsed = round(time.monotonic() - t_start, 1)
                        return {
                            "solved": True, "token": solved_token,
                            "method": "onnx-predict",
                            "waves": wave + 1, "elapsed": elapsed,
                        }

                if response_str == "not answered":
                    log.info("arkose wave %d: correct, next wave", wave)
                    next_imgs = resp_data.get("_challenge_imgs", [])
                    next_instruction = resp_data.get(
                        "instruction_string", instruction)
                    if next_imgs:
                        gfct_data.clear()
                        gfct_data.update({
                            "session_token": session_token,
                            "challengeID": challenge_id,
                            "sid": sid,
                            "game_data": {
                                "customGUI": {"_challenge_imgs": next_imgs},
                                "instruction_string": next_instruction,
                                "gameType": info.get("game_type", 4),
                            }
                        })
                        continue
                    gfct_data.clear()
                    ca_result.clear()
                    for _ in range(30):
                        if captured["gfct"]:
                            break
                        time.sleep(1)
                    if captured["gfct"]:
                        gfct_data = captured["gfct"]
                        continue
                    log.warning("arkose: no next challenge after correct "
                                "answer")
                    break

                log.info("arkose wave %d: unexpected response: %s",
                         wave, str(resp_data)[:200])
                guesses.pop()

            finally:
                if img_path:
                    try:
                        os.unlink(img_path)
                    except OSError:
                        pass

            gfct_data.clear()
            ca_result.clear()
            try:
                page.click("[data-theme='try-again']", timeout=3000)
            except Exception:
                pass
            time.sleep(2)
            for _ in range(15):
                if captured["gfct"]:
                    break
                time.sleep(1)
            if captured["gfct"]:
                gfct_data = captured["gfct"]
            else:
                break

        elapsed = round(time.monotonic() - t_start, 1)
        return {"solved": False,
                "error": f"failed after {max_attempts} waves",
                "elapsed": elapsed}
    finally:
        try:
            b.close()
        except Exception:
            pass
