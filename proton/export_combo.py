#!/usr/bin/env python3
"""Ekspor combo `mail|pass|refresh_token|client_id` dari capture mitm + file hasil run.

Format baris:  mail|pass|refresh_token|client_id
Mapping Proton:  mail = email akun, pass = password akun,
                 refresh_token = RefreshToken sesi (POST /auth atau /auth/cookies),
                 client_id = UID sesi Proton (pasangan wajib refresh_token).

Pakai:  python3 export_combo.py [capture.jsonl] [--run run-v2-result.json ...]
Output: combo.txt (baris penuh) + ringkasan ter-mask di terminal.
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
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_http(path):
    out = []
    for line in Path(path).read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "http":
            out.append(e)
    return sorted(out, key=lambda x: x.get("t", 0))


def body_json(e, where="res_body"):
    try:
        return json.loads((e.get(where) or {}).get("preview") or "{}")
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", nargs="?", help="file .jsonl capture (default: terbaru)")
    ap.add_argument("--run", action="append", default=[], help="file run-*.json (sumber password)")
    args = ap.parse_args()

    cap = Path(args.capture) if args.capture else sorted(
        (HERE.parent / "pipeline-capture" / "out").glob("sesi-*.jsonl"))[-1]
    entries = load_http(cap)

    # 1) akun dari POST /users yang berstatus 200
    accounts = []
    for e in entries:
        if e["path"].rstrip("/").endswith("/api/core/v4/users") and e.get("method") == "POST" \
                and e.get("status") == 200:
            u = body_json(e).get("User") or {}
            req = body_json(e, "req_body")
            if u.get("Email"):
                accounts.append({"email": u["Email"], "username": req.get("Username") or u.get("Name")})

    # 2) refresh_token + UID dari response auth terakhir
    token = uid = None
    for e in entries:
        if not any(k in e["path"] for k in ("/auth/v4/sessions", "/core/v4/auth", "/auth/cookies")):
            continue
        b = body_json(e)
        if b.get("RefreshToken"):
            token = b["RefreshToken"]
            uid = b.get("UID") or uid
    if not uid:  # fallback: UID dari nama cookie AUTH-<uid> / REFRESH-<uid>
        for e in entries:
            for h in e.get("res_headers", []):
                if h["name"].lower() == "set-cookie" and h["value"].split("=")[0].startswith(("AUTH-", "REFRESH-")):
                    uid = h["value"].split("=")[0].split("-", 1)[1]

    # 3) password dari file hasil run (cocokkan per username)
    passwords = {}
    for rp in args.run:
        d = json.loads(Path(rp).read_text())
        if d.get("username") and d.get("password"):
            passwords[d["username"]] = d["password"]

    lines = []
    for a in accounts:
        pw = passwords.get(a["username"], "")
        lines.append(f"{a['email']}|{pw}|{token or ''}|{uid or ''}")

    out = HERE / "combo.txt"
    out.write_text("\n".join(lines) + "\n")
    print(f"combo ditulis: {out} ({len(lines)} baris)")
    for ln in lines:
        mail, pw, rt, cid = (ln.split("|") + [""] * 4)[:4]
        print(f"  {mail}|{'<ada>' if pw else '<kosong>'}|{rt[:10]}…|{cid[:10]}…")


if __name__ == "__main__":
    main()
