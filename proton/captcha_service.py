#!/usr/bin/env python3
"""captcha_service.py — koordinat lubang puzzle via layanan solver (2Captcha / Capsolver).

Alur: kirim screenshot canvas + instruksi "balas koordinat X,Y lubang" ->
layanan (pekerja/AI) mengembalikan angka -> drag oleh captcha_slider.

2Captcha (skema terverifikasi: https://2captcha.com/2captcha-api/):
  in.php  : key, method=base64, body=<b64 png>, textinstructions=<140 char>, json=1
            -> {"status":1,"request":"<id>"}
  res.php : key, action=get, id, json=1 -> {"status":1,"request":"<teks jawaban>"}
            (belum siap: request="CAPHA_NOT_READY")

Capsolver (pola createTask standar: https://api.capsolver.com):
  POST /createTask  {"clientKey", task:{type, body:<b64>, ...}} -> {"taskId"}
  POST /getTaskResult {"clientKey","taskId"} -> {"status":"ready","solution":{...}}
  Tipe task koordinat diatur via env CAPSOLVER_TASK_TYPE (default ImageToText).

Pakai: solve_gap_x(png_bytes) -> (x, y) | (x,) | None
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
import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request

INSTRUCTION = ("Slider puzzle with dark missing-piece gap. Reply ONLY gap center "
               "coords as X,Y pixels, e.g. 184,216")  # <=140 char (batas 2captcha)


def _parse_xy(text):
    nums = re.findall(r"\d{2,4}", text or "")
    if not nums:
        return None
    if len(nums) >= 2:
        return (int(nums[0]), int(nums[1]))
    return (int(nums[0]),)


class TwoCaptcha:
    name = "2captcha"

    def __init__(self, key=None):
        self.key = key or os.environ.get("TWOCAPTCHA_KEY", "")
        if not self.key:
            raise RuntimeError("TWOCAPTCHA_KEY tidak diset")

    def solve_text(self, png, instruction=INSTRUCTION):
        data = {"key": self.key, "method": "base64", "body": base64.b64encode(png).decode(),
                "textinstructions": instruction[:140], "json": 1}
        req = urllib.request.Request("https://2captcha.com/in.php",
                                     data=urllib.parse.urlencode(data).encode())
        r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
        if not r.get("status"):
            raise RuntimeError(f"2captcha in.php: {r}")
        tid = str(r["request"])
        end = time.time() + 180
        while time.time() < end:
            time.sleep(5)
            q = urllib.parse.urlencode({"key": self.key, "action": "get", "id": tid, "json": 1})
            rr = json.loads(urllib.request.urlopen(
                f"https://2captcha.com/res.php?{q}", timeout=30).read().decode())
            if rr.get("status") == 1:
                return str(rr["request"])
            if "CAPCHA_NOT_READY" not in str(rr.get("request", "")):
                raise RuntimeError(f"2captcha res.php: {rr}")
        raise RuntimeError("2captcha timeout 180s")


class Capsolver:
    name = "capsolver"

    def __init__(self, key=None):
        self.key = key or os.environ.get("CAPSOLVER_KEY", "")
        if not self.key:
            raise RuntimeError("CAPSOLVER_KEY tidak diset")

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"https://api.capsolver.com{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())

    def solve_text(self, png, instruction=INSTRUCTION):
        task = {"type": os.environ.get("CAPSOLVER_TASK_TYPE", "ImageToText"),
                "body": base64.b64encode(png).decode(),
                "websiteURL": "https://account.proton.me",
                "imginstructions": instruction[:140]}
        r = self._post("/createTask", {"clientKey": self.key, "task": task})
        if not r.get("taskId"):
            raise RuntimeError(f"capsolver createTask: {r}")
        end = time.time() + 180
        while time.time() < end:
            time.sleep(5)
            rr = self._post("/getTaskResult", {"clientKey": self.key, "taskId": r["taskId"]})
            if rr.get("status") == "ready":
                sol = rr.get("solution") or {}
                return str(sol.get("text") or sol.get("answer") or sol)
            if rr.get("errorId"):
                raise RuntimeError(f"capsolver: {rr}")
        raise RuntimeError("capsolver timeout 180s")


def available():
    out = []
    for cls, env in ((TwoCaptcha, "TWOCAPTCHA_KEY"), (Capsolver, "CAPSOLVER_KEY")):
        if os.environ.get(env):
            try:
                out.append(cls())
            except Exception:
                pass
    return out


def solve_gap_x(png, note=print):
    """(x, y) | (x,) | None — coba semua layanan yang API key-nya tersedia."""
    for sv in available():
        try:
            t = sv.solve_text(png)
            xy = _parse_xy(t)
            if xy:
                note(f"[captcha-svc] {sv.name} -> {t!r} -> {xy}")
                return xy
            note(f"[captcha-svc] {sv.name} jawaban tak terbaca: {t!r}")
        except Exception as e:
            note(f"[captcha-svc] {sv.name} gagal: {e}")
    return None


if __name__ == "__main__":
    print("layanan tersedia:", [s.name for s in available()] or "(belum ada API key)")
    print("panjang instruksi:", len(INSTRUCTION))
