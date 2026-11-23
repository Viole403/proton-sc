"""SRP Proton Auth v4 — verifikasi (POST /users) + bukti login (auth/info + auth).

Sumber: fungsi I/N/k/z/U/M/F/V di bundel `public-index` app account (2026-10).
Konvensi terbukti (uji prima modulus): semua bilangan besar **little-endian**.

- hash password v4:  bcrypt(password, "$2y$10$" + b64_crypt(utf8(saltB64)[:16]))
                     lalu stretch 4×SHA512(x ‖ [i]) ‖ modulus → 256 byte
- verifier:          v = 2^x mod n  → 256 byte LE
- bukti login:       SRP-6a gaya Proton (multiplier = k-stretch mod n, u = k(A‖B))
"""
from __future__ import annotations

import base64
import hashlib
import secrets

import bcrypt

_G = 2
_KODE = "./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _b64_crypt(data: bytes, n: int) -> str:
    out: list[str] = []
    off, ln = 0, min(n, len(data))
    while off < ln:
        c1 = data[off]; off += 1
        out.append(_KODE[(c1 >> 2) & 0x3F]); c1 = (c1 & 3) << 4
        if off >= ln:
            out.append(_KODE[c1 & 0x3F]); break
        c2 = data[off]; off += 1
        c1 |= (c2 >> 4) & 15; out.append(_KODE[c1 & 0x3F]); c1 = (c2 & 15) << 2
        if off >= ln:
            out.append(_KODE[c1 & 0x3F]); break
        c2 = data[off]; off += 1
        c1 |= (c2 >> 6) & 3; out.append(_KODE[c1 & 0x3F]); out.append(_KODE[c2 & 0x3F])
    return "".join(out)


def _stretch(data: bytes) -> bytes:
    """k() bundel: concat 4×SHA512(data ‖ [i]) untuk i=0..3 → 256 byte."""
    return b"".join(hashlib.sha512(data + bytes([i])).digest() for i in range(4))


def hash_password(password: str, salt: bytes, modulus: bytes) -> bytes:
    """I(version 4) → 256 byte stretched hash.

    Garam bcrypt = b64_crypt(salt_10byte ‖ "proton", 16) — dari bundel:
    `A(FY(\`${Dj(saltBytes)}proton\`), 16)` = 10 byte garam + literal "proton"
    (16 byte pas, itulah fungsi sufiks "proton").
    """
    garam = ("$2y$10$" + _b64_crypt(salt + b"proton", 16)).encode()
    bh = bcrypt.hashpw(password.encode(), garam)          # 60-char bcrypt
    return _stretch(bh + modulus)                          # k(FY(bcrypt) ‖ modulus)


def _le(b: bytes) -> int:
    return int.from_bytes(b, "little")


def _ile(v: int, panjang: int) -> bytes:
    return v.to_bytes(panjang, "little")


def buat_auth(password: str, modulus: bytes, salt: bytes | None = None) -> dict:
    """V() → {ModulusID diisi pemanggil, Version, Salt, Verifier}."""
    salt = salt or secrets.token_bytes(10)
    salt_b64 = base64.b64encode(salt).decode()
    x = _le(hash_password(password, salt, modulus))
    n = _le(modulus)
    v = pow(_G, x, n)
    return {"Version": 4, "Salt": salt_b64,
            "Verifier": base64.b64encode(_ile(v, 256)).decode()}


def bukti_login(username: str, password: str, modulus: bytes,
                salt_b64: str, server_ephemeral: bytes, byte_length: int = 256) -> dict:
    """F()+U()+M() → {clientEphemeral, clientProof, expectedServerProof, sharedSession}."""
    n = _le(modulus)
    x = _le(hash_password(password, base64.b64decode(salt_b64), modulus))
    b_srv = _le(server_ephemeral)
    if b_srv == 0:
        raise ValueError("server ephemeral nol")
    # multiplier k = stretch(g_le ‖ modulus) mod n
    k_hash = _le(_stretch(_ile(_G, byte_length) + modulus)) % n
    while True:
        a = _le(secrets.token_bytes(byte_length))
        a_pub = pow(_G, a, n)
        a_le = _ile(a_pub, byte_length)
        u = _le(_stretch(a_le + server_ephemeral))
        if u != 0 and a_pub != 0:
            break
    pangkal = (pow(_G, x, n) * k_hash) % n
    pangkat = (u * x + a) % (n - 1)
    s = pow((b_srv - pangkal) % n, pangkat, n)
    s_le = _ile(s, byte_length)
    bukti = _stretch(a_le + server_ephemeral + s_le)
    balas = _stretch(a_le + bukti + s_le)
    return {"clientEphemeral": base64.b64encode(a_le).decode(),
            "clientProof": base64.b64encode(bukti).decode(),
            "expectedServerProof": base64.b64encode(balas).decode(),
            "sharedSession": base64.b64encode(s_le).decode()}
