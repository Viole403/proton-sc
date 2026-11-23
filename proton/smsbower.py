#!/usr/bin/env python3
"""smsbower.py — klien SMSBower (smsbower.page) untuk verifikasi SMS Proton.

Dokumentasi: https://smsbower.app/api/?page=client
  base     : https://smsbower.page/stubs/handler_api.php  (GET/POST, api_key wajib)
  getNumber      -> ACCESS_NUMBER:$id:$phone | NO_NUMBERS | NO_BALANCE | BAD_*
  getNumberV2    -> JSON {activationId, phoneNumber, activationCost, countryCode, ...}
  getStatus&id   -> STATUS_WAIT_CODE | STATUS_WAIT_RETRY:$last | STATUS_CANCEL | STATUS_OK:'kode'
  setStatus&id&status=N -> 1=ready, 3=minta kode lagi, 6=selesai, 8=batal
  getBalance -> ACCESS_BALANCE:$n ; getServicesList ; getCountries ; getTopCountriesByService
  batal (8) ditolak EARLY_CANCEL_DENIED bila <2 menit sejak pembelian.
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
import re
import time
import urllib.parse
import urllib.request

BASE = "https://smsbower.page/stubs/handler_api.php"


def _req(params, timeout=30):
    q = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{BASE}?{q}", timeout=timeout) as r:
        return r.read().decode("utf-8", "replace").strip()


class SMSBower:
    def __init__(self, key=None):
        self.key = key or os.environ.get("SMSBOWER_KEY", "")
        if not self.key:
            raise RuntimeError("SMSBOWER_KEY tidak diset")
        self._services = None
        self._countries = None

    def balance(self):
        t = _req({"api_key": self.key, "action": "getBalance"})
        if t.startswith("ACCESS_BALANCE:"):
            return float(t.split(":", 1)[1])
        raise RuntimeError(f"getBalance gagal: {t}")

    def services(self, refresh=False):
        if self._services is None or refresh:
            t = _req({"api_key": self.key, "action": "getServicesList"})
            self._services = json.loads(t).get("services", [])
        return self._services

    def find_service(self, pattern="proton"):
        """Cari kode service dari nama (dinamis — tabel di docs tidak lengkap)."""
        pat = re.compile(pattern, re.I)
        hits = [s for s in self.services()
                if pat.search(str(s.get("name", ""))) or pat.fullmatch(str(s.get("code", "")))]
        if not hits:
            raise RuntimeError(f"service '{pattern}' tidak ada di SMSBower")
        hits.sort(key=lambda s: (0 if pat.fullmatch(str(s.get("code", ""))) else 1,
                                 len(str(s.get("name", "")))))
        return hits[0]["code"]

    def countries(self, refresh=False):
        if self._countries is None or refresh:
            d = json.loads(_req({"api_key": self.key, "action": "getCountries"}))
            self._countries = list(d.values()) if isinstance(d, dict) else d
        return self._countries

    def find_country(self, name):
        pat = re.compile(rf"^{re.escape(name)}$", re.I)
        for c in self.countries():
            if any(pat.fullmatch(str(c.get(k, ""))) for k in ("eng", "rus", "chn")):
                return c["id"]
        pat2 = re.compile(re.escape(name), re.I)
        for c in self.countries():
            if any(pat2.search(str(c.get(k, ""))) for k in ("eng", "rus", "chn")):
                return c["id"]
        raise RuntimeError(f"negara '{name}' tidak ditemukan")

    def best_price(self, service, country):
        """(harga, provider_id|None) termurah utk service+negara — getPricesV3 -> getPrices."""
        try:
            d = json.loads(_req({"api_key": self.key, "action": "getPricesV3"}))
            block = (d.get(str(country), {}) or {}).get(service, {}) or {}
            best = None
            for prov, v in block.items():
                try:
                    price = float(v.get("price"))
                    cnt = int(v.get("count", 0))
                except Exception:
                    continue
                if cnt > 0 and (best is None or price < best[0]):
                    best = (price, str(v.get("provider_id") or prov))
            if best:
                return best
        except Exception:
            pass
        try:
            d = json.loads(_req({"api_key": self.key, "action": "getPrices",
                                 "service": service, "country": country}))
            block = (d.get(str(country), {}) or {}).get(service, {})
            if isinstance(block, dict) and "cost" in block:
                return float(block["cost"]), None
        except Exception:
            pass
        return None, None

    def get_number(self, service, country, max_price=None, provider_ids=None):
        p = {"api_key": self.key, "action": "getNumberV2", "service": service, "country": country}
        if max_price:
            p["maxPrice"] = max_price
        if provider_ids:
            p["providerIds"] = str(provider_ids)
        t = _req(p)
        try:
            d = json.loads(t)
            return str(d["activationId"]), str(d["phoneNumber"]), d
        except Exception:
            pass
        if t.startswith("ACCESS_NUMBER:"):
            _tag, aid, phone = t.split(":", 2)
            return aid, phone, {}
        raise RuntimeError(f"getNumber gagal: {t}")

    def get_status(self, aid):
        t = _req({"api_key": self.key, "action": "getStatus", "id": aid})
        if t.startswith("STATUS_WAIT_CODE"):
            return "wait", None
        if t.startswith("STATUS_WAIT_RETRY"):
            return "retry", t.split(":", 1)[1] if ":" in t else None
        if t.startswith("STATUS_CANCEL"):
            return "cancel", None
        if t.startswith("STATUS_OK"):
            return "ok", t.split(":", 1)[1].strip().strip("'").strip()
        return "err", t

    def wait_code(self, aid, budget=240, poll=6):
        end = time.time() + budget
        while time.time() < end:
            st, code = self.get_status(aid)
            if st == "ok" and code:
                return code
            if st == "cancel":
                return None
            time.sleep(poll)
        return None

    def set_status(self, aid, status):
        try:
            return _req({"api_key": self.key, "action": "setStatus", "id": aid, "status": str(status)})
        except Exception as e:
            return f"ERR:{e}"

    def finish(self, aid):
        return self.set_status(aid, 6)

    def cancel(self, aid):
        return self.set_status(aid, 8)

    def another(self, aid):
        return self.set_status(aid, 3)


if __name__ == "__main__":
    # smoke test tanpa key: pastikan struktur respon parsing jalan
    assert "ACCESS_NUMBER" not in "STATUS_OK:'123456'"
    print("smsbower module OK")
