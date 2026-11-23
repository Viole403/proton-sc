#!/usr/bin/env python3
"""Uji end-to-end signup Proton via HTTP + fingerprint hasil panen + OTP headmail (mode domain).
Alur: (opsi bootstrap sesi) -> users/available -> auth/modulus -> verifier SRP ->
POST /users -> [9001] users/code -> OTP headmail -> retry POST /users dengan header verifikasi.
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
import json
import os
import secrets
import string
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import requests

BASE = "https://account.proton.me"
HEADMAIL = "https://headmail.flowjob-lab.workers.dev"
HEADMAIL_KEY = os.environ.get("HEADMAIL_KEY", "")
assert HEADMAIL_KEY, "setel env HEADMAIL_KEY (kunci API headmail) sebelum menjalankan"
HM_DOMAIN = "hellowntas.fun"  # mode domain mail (bukan alias gmail)
HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36"

harvest = json.loads((HERE / (sys.argv[1] if len(sys.argv) > 1 else "harvest.json")).read_text())
pairs = harvest["pairs"]
fp_unauth = next((x for x in pairs if "unauth" in x["tag"]), None)
fp_email = next((x for x in pairs if x is not fp_unauth), None)
print(f">> fingerprint: unauth={'ADA' if fp_unauth else 'TIDAK'} | kedua={'ADA' if fp_email else 'TIDAK'}")

username = "rudiansayh" + "".join(secrets.choice(string.digits) for _ in range(3))
password = "T3st." + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10)) + "a1"

s = requests.Session()
s.headers.update({
    "user-agent": UA,
    "x-pm-appversion": "web-account@5.0.422.3",
    "x-pm-locale": "en_US",
    "x-pm-product": "mail",
    "origin": BASE,
    "referer": f"{BASE}/mail/signup?plan=free&noPromo=1&ref=mail_plus_intro-2",
    "content-type": "application/json",
})

uid = None
for c in harvest["cookies"]:
    if c.get("name", "").startswith(("AUTH-", "REFRESH-")):
        uid = c["name"].split("-", 1)[1]
    s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
if uid:
    s.headers["x-pm-uid"] = uid
print(f">> sesi browser dipakai: UID={uid}, {len(harvest['cookies'])} cookie")


def node(mode, args):
    r = subprocess.run(
        ["node", str(HERE / "make_auth.js"), mode, json.dumps(args)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


# --- alias headmail mode domain ---
t0 = int(time.time() * 1000)
r = s.post(f"{HEADMAIL}/api/v1/aliases",
           headers={"authorization": "Bearer " + HEADMAIL_KEY, "user-agent": UA, "content-type": "application/json"},
           json={"source": "domain", "total": 1, "domains": [HM_DOMAIN]})
alias = r.json()["created"][0]["email"]
print(f">> alias headmail (domain): {alias} [{r.status_code}]")

# --- preflight ---
r = s.get(f"{BASE}/api/core/v4/users/available?Name={urllib.parse.quote(username)}&ParseDomain=0")
print(f">> users/available({username}): {r.status_code} {r.text[:80]}")
r = s.get(f"{BASE}/api/core/v4/auth/modulus")
modulus = r.json()
print(f">> auth/modulus: {r.status_code} ModulusID={modulus.get('ModulusID', '')[:24]}...")

auth = node("verifier", {"modulusArmored": modulus["Modulus"], "password": password})
print(f">> verifier SRP dibuat: Salt={auth['Salt']} Verifier={auth['Verifier'][:24]}...")

body = {
    "Type": 1,
    "Username": username,
    "Payload": {fp_email["id"]: fp_email["fingerprint"]} if fp_email else {},
    "Domain": "proton.me",
    "Auth": {"ModulusID": modulus["ModulusID"], "Version": auth["Version"],
             "Salt": auth["Salt"], "Verifier": auth["Verifier"]},
}

# --- percobaan pertama ---
r = s.post(f"{BASE}/api/core/v4/users", json=body)
print(f">> POST /users #1: {r.status_code} {r.text[:200]}")

hv_headers = None
if r.json().get("Code") == 9001:
    details = r.json().get("Details", {})
    print(f">> human verification diminta, metode: {details.get('HumanVerificationMethods')}")
    r2 = s.post(f"{BASE}/api/core/v4/users/code",
                json={"Type": "email", "Destination": {"Address": alias}})
    print(f">> users/code ke {alias}: {r2.status_code} {r2.text[:80]}")

    otp = None
    for i in range(20):
        q = urllib.parse.urlencode({"email": alias, "wait": 25, "since": t0, "any": 1})
        r3 = s.get(f"{HEADMAIL}/api/v1/otp?{q}",
                   headers={"authorization": "Bearer " + HEADMAIL_KEY, "user-agent": UA})
        d = r3.json()
        if d.get("found") and d.get("otp"):
            otp = d["otp"]
            print(f">> OTP headmail masuk: {otp} (subjek: {d.get('subject')})")
            break
        print(f">> ...menunggu OTP ({i + 1}) {d.get('message', '')}")
    if not otp:
        sys.exit("OTP tidak masuk — uji dihentikan")

    hv_headers = {
        "x-pm-human-verification-token": f"{alias}:{otp}",
        "x-pm-human-verification-token-type": "email",
    }
    r = s.post(f"{BASE}/api/core/v4/users", json=body, headers=hv_headers)
    print(f">> POST /users #2 (dengan verifikasi email): {r.status_code} {r.text[:300]}")

result = r.json()
out = {
    "username": username,
    "password": password,
    "email": f"{username}@proton.me",
    "headmail_alias": alias,
    "final_status": r.status_code,
    "final_body": result,
    "hv_headers_used": bool(hv_headers),
}
(HERE / "run-result.json").write_text(json.dumps(out, indent=1))
print(f"\n>> SELESAI. Code={result.get('Code')} | akun: {out['email']} | detail: run-result.json")
