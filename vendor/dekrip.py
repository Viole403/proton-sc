"""Dekripsi isi pesan Proton murni lokal via gpg CLI — tanpa browser, tanpa kirim apa pun.

Rantai (pmcrypto): password + KeySalt → user key (PrimaryKey, terproteksi passphrase)
→ `Token` (PGP message: passphrase kunci alamat, dienkripsi ke user key)
→ address key → badan pesan (PGP MIME) → teks.

Kunci privat hanya dikeyring sementara di /dev/shm (RAM) dan dihapus setelah pakai
(aturan 7: rahasia keras tidak pernah menetap di file proyek/log).
"""
from __future__ import annotations

import email
import hashlib
import html.parser
import os
import re
import shutil
import subprocess
import time
import tempfile


class Dompet:
    """Keyring gpg sementara di RAM."""

    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(dir="/dev/shm", prefix="dekrip-")
        os.chmod(self.dir, 0o700)

    def hapus(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _gpg(self, args: list[str], data: bytes, passphrase: bytes | None):
        """[fix beban 2026-10-07] subproses gpg FLAKE di bawah beban CPU/RAM
        (TimeoutExpired/OSError: fork/resource) — dulu eksepsinya BOCOR ke
        baca_otp dan membunuh SELURUH pemindaian ("OTP inbox tak datang" padahal
        surat ada). Kini: sekali ulang otomatis, gagal lagi -> balas (rc!=0, b"",
        pesan) BUKAN melempar — pemanggil sudah memperlakukan itu sbg dekripsi
        gagal (retry turunan/lajur browser)."""
        jalur = None
        for percobaan in range(2):
            try:
                with tempfile.NamedTemporaryFile(dir=self.dir, delete=False,
                                                 suffix=".asc") as f:
                    f.write(data)
                    jalur = f.name
                cmd = ["gpg", "--homedir", self.dir, "--batch", "--yes",
                       "--pinentry-mode", "loopback"]
                inp = b""
                if passphrase is not None:
                    cmd += ["--passphrase-fd", "0"]  # harus SEBELUM nama file
                    inp = passphrase
                cmd += [*args, jalur]
                p = subprocess.run(cmd, input=inp, capture_output=True, timeout=60)
                return p.returncode, p.stdout, p.stderr
            except subprocess.TimeoutExpired:
                galat = b"gpg: timeout 60s"
            except OSError as e:
                galat = f"gpg: {type(e).__name__} {e}".encode()[:200]
            finally:
                if jalur:
                    try:
                        os.unlink(jalur)
                    except OSError:
                        pass
                    jalur = None
            time.sleep(0.5 * (percobaan + 1))
        return 1, b"", galat

    def impor(self, kunci_asc: str) -> tuple[bool, str]:
        for percobaan in range(2):
            try:
                p = subprocess.run(
                    ["gpg", "--homedir", self.dir, "--batch", "--yes", "--import"],
                    input=kunci_asc.encode(), capture_output=True, timeout=60)
                return p.returncode == 0, p.stderr.decode(errors="replace")[-200:]
            except (subprocess.TimeoutExpired, OSError) as e:
                galat = f"{type(e).__name__}: {e}"
            time.sleep(0.5 * (percobaan + 1))
        return False, galat[-200:]

    def dekripsi(self, blob_asc: str, passphrase: bytes) -> tuple[bytes | None, str]:
        """Buka pesan PGP; passphrase dipakai untuk membuka kunci privat di keyring."""
        code, out, err = self._gpg(["--decrypt"], blob_asc.encode(), passphrase)
        if out and (b"BEGIN PGP" not in out[:40]):
            return out, ""
        return None, err.decode(errors="replace")[-200:]


def _b64_crypt(data: bytes, n: int) -> str:
    """Base64 gaya crypt(3)/bcrypt (encodeBase64 bcryptjs) — untuk string garam."""
    kode = "./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    out: list[str] = []
    off, ln = 0, min(n, len(data))
    while off < ln:
        c1 = data[off]; off += 1
        out.append(kode[(c1 >> 2) & 0x3F]); c1 = (c1 & 3) << 4
        if off >= ln:
            out.append(kode[c1 & 0x3F]); break
        c2 = data[off]; off += 1
        c1 |= (c2 >> 4) & 15; out.append(kode[c1 & 0x3F]); c1 = (c2 & 15) << 2
        if off >= ln:
            out.append(kode[c1 & 0x3F]); break
        c2 = data[off]; off += 1
        c1 |= (c2 >> 6) & 3; out.append(kode[c1 & 0x3F]); out.append(kode[c2 & 0x3F])
    return "".join(out)


def kandidat_passphrase(salt: bytes, password: str) -> list[bytes]:
    """Passphrase kunci Proton — sumber: bundel account app fungsi K():

        K(password, saltB64) = bcrypt(password, "$2y$10$" + b64_crypt(salt)).slice(29)

    Hasil = digest bcrypt 31 char (TANPA "$2y$10$<garam>"), dipakai sebagai
    passphrase PGP key. Terverifikasi terhadap Token asli 2026-10-02.
    """
    import bcrypt  # lokal
    garam = ("$2y$10$" + _b64_crypt(salt, 16)).encode()
    h = bcrypt.hashpw(password.encode(), garam)
    return [h[29:]]


class Tukang:
    """Kumpulan kunci satu akun + pemakaian dekripsi."""

    def __init__(self, user_key_asc: str, addr_key_asc: str, token_asc: str,
                 salt: bytes, password: str):
        self.user_key_asc = user_key_asc
        self.addr_key_asc = addr_key_asc
        self.token_asc = token_asc
        self.salt = salt
        self.password = password
        self.dompet = Dompet()
        self.pass_user: bytes | None = None
        self.pass_addr: bytes | None = None
        self.catatan: list[str] = []

    def siap(self) -> bool:
        """Impor kunci + buka Token → dapat passphrase kunci alamat."""
        ok, err = self.dompet.impor(self.user_key_asc)
        if not ok:
            self.catatan.append(f"impor user key gagal: {err}")
            return False
        ok, err = self.dompet.impor(self.addr_key_asc)
        if not ok:
            self.catatan.append(f"impor addr key gagal: {err}")
            return False
        # 1) buka Token dulu: butuh passphrase user key
        for i, kand in enumerate(kandidat_passphrase(self.salt, self.password)):
            out, err = self.dompet.dekripsi(self.token_asc, kand)
            if out is not None:
                self.pass_user = kand
                self.pass_addr = out.strip() or b""
                self.catatan.append(f"Token terbuka (turunan #{i}, "
                                    f"pass_addr {len(self.pass_addr)} byte)")
                return True
        self.catatan.append("Token gagal dibuka — semua turunan passphrase salah")
        return False

    def buka_badan(self, body_asc: str) -> str | None:
        """Dekripsi badan pesan → teks (HTML di-strip).
        [fix 2026-10-07] retry 3x: subproses gpg FLAKE di bawah beban CPU/RAM
        (3 worker + Chrome) — terbukti 4/4 sukses saat sepi vs gagal senyap saat
        run penuh ("OTP inbox tak datang" padahal surat ADA di inbox).
        [fix beban 2026-10-07] dinaikkan ke 4 putaran dgn backoff 0.4->2.0 dtk +
        TIDAK PERNAH melempar (dekripsi kini aman-galat) — 1 eksepsi gpg dulu
        membunuh seluruh pemindaian OTP."""
        coba = [self.pass_addr or b"", self.pass_user or b""] + \
               kandidat_passphrase(self.salt, self.password)
        for ulang in range(4):
            for pas in coba:
                try:
                    out, _ = self.dompet.dekripsi(body_asc, pas)
                except Exception:
                    out = None
                if out is not None:
                    return bersihkan_teks(out.decode(errors="replace"))
            try:
                time.sleep(0.4 * (ulang + 1) + (0.6 if ulang >= 2 else 0))
            except Exception:
                pass
        return None

    def tutup(self) -> None:
        self.dompet.hapus()


class _AmbilTeks(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.potong: list[str] = []
        self.lewati = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.lewati += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.lewati:
            self.lewati -= 1

    def handle_data(self, data):
        if not self.lewati:
            self.potong.append(data)


def bersihkan_teks(mentah: str) -> str:
    """Hasil dekripsi (MIME/HTML/teks) → teks polos."""
    isi = mentah
    if "Content-Type:" in mentah[:600] or "MIME-Version:" in mentah[:600]:
        msg = email.message_from_string(mentah)
        bagian = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            payload = part.get_payload(decode=True) or b""
            teks = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if part.get_content_subtype() == "html":
                p = _AmbilTeks()
                p.feed(teks)
                teks = " ".join(p.potong)
            bagian.append(teks)
        isi = "\n".join(bagian)
    if "<" in isi and ">" in isi:
        p = _AmbilTeks()
        p.feed(isi)
        isi = " ".join(p.potong)
    return re.sub(r"\s+", " ", isi).strip()
