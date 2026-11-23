#!/usr/bin/env python3
"""pabrik-murni.py — PABRIK TANPA-CDP (koreksi user 2026-10-13):
Browser Chrome MURNI (tanpa Playwright, tanpa CDP) + xdotool untuk
injeksi keyboard/mouse. Tidak ada Runtime.enable, tidak ada DevTools
session = Proton TIDAK BISA mendeteksi otomasi (bukti: signup manual
user sukses via proxy yang sama).

Alur:
  1. Launch Chrome langsung (subprocess) dengan proxy + user-data-dir unik
  2. TANGANI dialog proxy auth (Chrome minta user:pass proxy)
  3. Tutup popup "Save password" Chrome
  4. Isi form signup via xdotool (ketik + Tab + klik)
  5. Submit, pantau hasil via screenshot + OCR

Env:
  PABRIK_PROXY   = host:port:user:pass
  DISPLAY        = :99 (default)
"""
import os
import sys
import time
import random
import subprocess

AKAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISPLAY = os.environ.get("DISPLAY", ":99")
XDOTOOL = "xdotool"
CHROME = "/usr/bin/google-chrome"
PROFILE_TMPL = "/tmp/pabrik-profile-{seed}"


def jeda(a, b):
    time.sleep(random.uniform(a, b))


def screenshot(path):
    """Tangkap layar Xvfb via ImageMagick import."""
    try:
        subprocess.run(["import", "-window", "root", path],
                       timeout=15, capture_output=True)
    except Exception:
        pass


def baca_teks(path):
    """OCR screenshot via tesseract."""
    try:
        r = subprocess.run(["tesseract", path, "stdout"],
                           timeout=15, capture_output=True, text=True)
        return r.stdout
    except Exception:
        return ""


def ketik_lengkap(text, wid=None):
    """Ketik teks via xdotool type (global, tanpa window ID)."""
    try:
        subprocess.run([XDOTOOL, "type", "--clearmodifiers", "--delay",
                        str(random.randint(30, 80)), text],
                       timeout=30, capture_output=True)
    except Exception:
        pass


def klik_global(x, y):
    """Klik mouse di koordinat layar global."""
    try:
        subprocess.run([XDOTOOL, "mousemove", str(x), str(y)], timeout=5)
        jeda(0.1, 0.3)
        subprocess.run([XDOTOOL, "click", "1"], timeout=5)
    except Exception:
        pass


def gerak_mouse_global(x, y):
    """Gerakkan mouse ke koordinat (tanpa klik)."""
    try:
        subprocess.run([XDOTOOL, "mousemove", str(x), str(y)], timeout=5)
    except Exception:
        pass


def nama_acak():
    awal = ["gai", "cor", "vin", "mar", "fla", "aug", "luc", "jul", "pom", "sel", "ter", "ner", "val", "cam", "dom"]
    akhir = ["us", "ian", "ella", "ius", "anus", "or", "ius", "ian", "ella", "on", "en", "ar"]
    n = random.choice(awal) + random.choice(akhir) + str(random.randint(100, 999))
    return n


def password_acak():
    """Password kuat: wajib ada upper, lower, digit, symbol, min 14 char."""
    a = "abcdefghijkmnpqrstuvwxyz"
    A = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    d = "23456789"
    sp = "!@#$%&*"
    pw = [random.choice(a), random.choice(A), random.choice(d), random.choice(sp)]
    semua = a + A + d + sp
    sisa = random.randint(10, 14)
    pw += random.choices(semua, k=sisa)
    random.shuffle(pw)
    return "".join(pw)


def main():
    proxy_raw = os.environ.get("PABRIK_PROXY", "")
    if not proxy_raw or proxy_raw.count(":") < 3:
        print("PABRIK_PROXY tidak disetel (host:port:user:pass)", flush=True)
        return 1

    h, pt, u, pw_px = proxy_raw.split(":", 3)
    seed = str(random.randint(10000, 99999))
    profile = PROFILE_TMPL.format(seed=seed)
    os.makedirs(profile, exist_ok=True)

    username = nama_acak()
    password = password_acak()
    mail = f"{username}@proton.me"

    print(f"[pabrik-murni] akun: {mail}", flush=True)
    print(f"[pabrik-murni] exit: {h}:{pt}", flush=True)

    env = dict(os.environ)
    env["DISPLAY"] = DISPLAY
    proc = subprocess.Popen([
        CHROME,
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        f"--proxy-server=http://{h}:{pt}",
        f"--user-data-dir={profile}",
        "--window-size=1280,900",
        "--disable-dev-shm-usage",
        "https://account.proton.me/mail/signup?plan=free&noPromo=1",
    ], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # tanpa perlu window ID — Chrome = window fokus di Xvfb
    jeda(8, 12)
    wid = 999999  # dummy (tidak dipakai untuk interaksi global)
    print(f"[pabrik-murni] window: {wid}", flush=True)

    # ── 1b) TANGANI DIALOG PROXY AUTH ──────────────────────────────
    # Chrome munculkan dialog "The proxy ... requires a username and
    # password" — tanpa ini, semua keystroke masuk ke dialog, bukan form!
    screenshot(f"/tmp/pabrik-{seed}-0-proxy.png")
    print(f"[pabrik-murni] menangani dialog proxy auth...", flush=True)
    klik_global(640, 400)       # fokus ke field Username dialog
    jeda(0.5, 1)
    ketik_lengkap(u)            # username proxy
    jeda(0.3, 0.7)
    subprocess.run([XDOTOOL, "key", "Tab"], timeout=5)   # ke Password
    jeda(0.3, 0.7)
    ketik_lengkap(pw_px)        # password proxy
    jeda(0.3, 0.7)
    subprocess.run([XDOTOOL, "key", "Return"], timeout=5)  # submit
    jeda(5, 8)                  # tunggu halaman termuat

    # ── 1c) TUTUP POPUP "Save password" Chrome ─────────────────────
    print(f"[pabrik-murni] tutup popup save-password...", flush=True)
    screenshot(f"/tmp/pabrik-{seed}-0.5-setelah-proxy.png")
    klik_global(1043, 385)      # tombol "No thanks"
    jeda(1, 2)
    subprocess.run([XDOTOOL, "key", "Escape"], timeout=5)
    jeda(1, 2)

    # ── 2) ISI FORM (koordinat asli yang sudah terbukti jalan) ─────
    jeda(3, 6)
    screenshot(f"/tmp/pabrik-{seed}-1-form.png")
    print(f"[pabrik-murni] form screenshot", flush=True)

    # Klik area form (640, 380) → fokus ke field pertama
    klik_global(640, 380)
    jeda(0.5, 1.5)
    ketik_lengkap(mail)
    jeda(0.5, 1.5)

    # Tab ke Password
    subprocess.run([XDOTOOL, "key", "Tab"], timeout=5)
    jeda(0.5, 1.5)
    ketik_lengkap(password)
    jeda(1, 2)

    # Tab ke Confirm Password
    subprocess.run([XDOTOOL, "key", "Tab"], timeout=5)
    jeda(0.5, 1.5)
    ketik_lengkap(password)
    jeda(1, 2)

    # Tab ke Username
    subprocess.run([XDOTOOL, "key", "Tab"], timeout=5)
    jeda(0.5, 1.5)
    ketik_lengkap(username)
    jeda(2, 4)

    screenshot(f"/tmp/pabrik-{seed}-2-isi.png")

    # Klik tombol Submit (640, 580)
    klik_global(640, 580)
    jeda(3, 6)

    screenshot(f"/tmp/pabrik-{seed}-3-submit.png")

    # ── 3) PANTAU HASIL ────────────────────────────────────────────
    print(f"[pabrik-murni] menunggu captcha/verifikasi...", flush=True)
    t0 = time.time()
    while time.time() - t0 < 300:
        jeda(8, 15)
        shot = f"/tmp/pabrik-{seed}-monitor.png"
        screenshot(shot)
        teks = baca_teks(shot)
        if "abusive" in teks.lower():
            print("[pabrik-murni] DIBLOKIR (abusive traffic)", flush=True)
            break
        if "display name" in teks.lower() or "onboarding" in teks.lower():
            print("[pabrik-murni] SUKSES! (onboarding)", flush=True)
            break
        if "captcha" in teks.lower() or "verify" in teks.lower():
            print(f"[pabrik-murni] captcha/verify terdeteksi", flush=True)

    print(f"[pabrik-murni] selesai: {mail} | {password}", flush=True)
    print(f"MAIL={mail}", flush=True)
    print(f"PASS={password}", flush=True)

    # 4) Catat ke registered.txt (format 4 kolom: mail|pass||)
    reg_file = os.path.join(AKAR, "results", "registered.txt")
    with open(reg_file, "a") as f:
        f.write(f"{mail}|{password}||\n")

    # Tutup browser
    jeda(2, 4)
    proc.terminate()

    return 0


if __name__ == "__main__":
    sys.exit(main())
