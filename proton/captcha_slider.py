#!/usr/bin/env python3
"""captcha_slider.py — solver CV utk slider puzzle Proton (captcha/v1/assets).

Anatomi puzzle (terverifikasi dari dump canvas asli, analysis/captcha-probe):
  * canvas 370x400 CSS; BACKING STORE mengikuti devicePixelRatio yang
    DIPALSUKAN persona fp_profile (1 / 1.25 / 1.5 / 2) -> citra toDataURL
    berukuran 370*dpr x 400*dpr sementara koordinat mouse Playwright di
    ruang CSS px. Semua koordinat CV dikonversi lewat k = lebar_citra/370.
    (Ini biang utama 15/20 gagal malam sebelumnya: dragger lama memakai
    piksel citra mentah sebagai offset mouse -> target meleset 1.5-2x.)
  * kepingan (jigsaw, knob di atas + tonjolan sisi) digambar di kiri-atas
    di atas area putih; isi kepingan = potongan foto yg harus menutup lubang.
  * lubang = blob gelap pipih (interior hampir rata, luma ~40-55) di area
    foto, di tepi dalam ada rim TERANG (stroke putih potongan) dan bayangan
    asimetris. Interior lubang TIDAK menyimpan isi foto (hancur jadi gelap
    rata) — jadi template-matching isi-vs-lubang TIDAK bekerja; yang bekerja
    adalah KORELASI BENTUK mask kepingan vs mask lubang + bukti rim/tepi.
  * tombol widget MENGIKUTI LOCALE (Next="Weiter" dsb) -> selektor teks
    Inggris rapuh. Tombol dikenali lewat KELAS (btn-solid-purple = aksi utama,
    btn-outline-grey = Reset) + snapshot baseline utk mendeteksi tombol BARU
    (Retry) tanpa peduli bahasa.
  * tombol Next SELALU enabled (cek dump DOM) — klik dengan posisi salah
    MEMBAKAR puzzle ("did not pass" -> Retry = puzzle baru). Maka verifikasi
    penempatan secara pixel WAJIB sebelum klik (closed-loop).

Metode penutupan (closed-loop) setelah drop:
  1. lokalisasi kepingan via masked-NCC isi (saat menutup lubang NCC ~1.0
     karena pikselnya identik dgn potongan asli);
  2. koreksi halus (nudge +-2..8 px, dua sumbu) sampai |error| <= 1 px;
  3. cek sisa: blob gap masih ada di tempat lain -> pindahkan kepingan ke
     sana (perbaikan kandidat tanpa membakar puzzle);
  4. BARU klik Next (poll visible+enabled) lalu baca outcome dari DALAM
     iframe (bukan body halaman): frame/canvas hilang = solved; teks/tombol
     berubah = fail -> klik Retry (tahan-bahasa) utk puzzle baru.

API produksi (dipakai full_fidelity2.py):  solve(pg, note=print) -> bool
Selftest:  venv/bin/python captcha_slider.py --selftest
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
import io
import math
import os
import random
import re
import sys
import time

import numpy as np
from PIL import Image

HERE = str(konfig.PROTON_DIR)  # [edit brutalism] path terpusat

# --- selektor widget tahan-bahasa (dari dump DOM analysis/captcha-probe) ---
SEL_NEXT = "button.btn-solid-purple"          # "Next"/"Weiter"/... : aksi utama
SEL_RESET = "button.btn-outline-grey"         # "Reset puzzle piece"/...
SEL_CANVAS = "canvas"
WIDGET_URL_PART = "captcha/v1/assets"

# pola multi-bahasa utk tombol/teks keadaan (fallback setelah cek struktur)
RE_RETRY = re.compile(r"retry|erneut|wiederhol|réess|riess|riprova|opnieuw|"
                      r"reintent|repetir|tentar ?novamente|повтор|إعادة|再試|다시|다시도", re.I)
RE_FAILTXT = re.compile(r"did\s?n[o'?]t\s+pass|not\s+pass|nicht\s+bestanden|"
                        r"pas\s+réussi|no\s+has\s+superado|non\s+hai\s+superato|"
                        r"não\s+pass|не\s+прош|niet\s+geslaagd|başarısız|"
                        r"验证失败|未通过", re.I)
RE_SUCCESSTXT = re.compile(r"success|erfolgreich|réussi|éxito|riuscito|gelukt|"
                           r"успех|başarılı|成功", re.I)


# ---------------------------------------------------------------- DOM widget

def widget_frame(pg, tries=8, pause=0.4):
    """Cari frame widget dgn toleransi reload ('Frame was detached' -> cari ulang)."""
    for _ in range(tries):
        try:
            fr = next((f for f in pg.frames if WIDGET_URL_PART in f.url), None)
            if fr is not None:
                try:
                    fr.locator(SEL_CANVAS).count()  # sentuh utk memastikan hidup
                    return fr
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(pause)
    return None


def grab(fr):
    b64 = fr.evaluate("() => document.querySelector('canvas').toDataURL('image/png')")
    return Image.open(io.BytesIO(base64.b64decode(b64.split(",", 1)[1]))).convert("RGB")


def canvas_box(fr):
    try:
        loc = fr.locator(SEL_CANVAS).first
        loc.wait_for(state="attached", timeout=3000)   # [fix audit] dulu tanpa
        return loc.bounding_box()                      #   timeout -> 30 dtk/round
    except Exception:
        return None


def widget_snapshot(fr):
    """Snapshot struktur widget (tahan-bahasa): teks+kelas tombol + teks bantuan.
    Dipakai utk deteksi perubahan keadaan (fail muncul = tombol/teks BARU)."""
    try:
        return fr.evaluate("""() => {
            const btns = [...document.querySelectorAll('button')].map(b =>
                ((b.innerText||'').trim().slice(0,40) + '|' + (b.className||'').toString().slice(0,60)));
            const ps = [...document.querySelectorAll('p, h1, h2, h3')].map(p =>
                (p.innerText||'').trim().slice(0,120));
            return {btns, ps};
        }""")
    except Exception:
        return None


def _btn(fr, sel):
    try:
        loc = fr.locator(sel).first
        return loc if loc.count() else None
    except Exception:
        return None


def click_retry(pg, note=print):
    """Keadaan gagal / canvas hilang -> klik tombol Retry (puzzle BARU).
    Tahan-bahasa: cari pola teks retry multi-bahasa, fallback tombol baru
    hasil diff dgn snapshot awal widget (apapun bahasanya)."""
    fr = widget_frame(pg, tries=4)
    if fr is None:
        return False
    try:
        # 1) pola teks retry lintas bahasa
        for b in fr.locator("button").all():
            try:
                if b.is_visible() and RE_RETRY.search(b.inner_text() or ""):
                    b.click(timeout=2500)
                    time.sleep(1.2)
                    return True
            except Exception:
                continue
        # 2) tombol aksi utama dgn teks berubah (bukan teks Next/Reset awal)
        b = _btn(fr, SEL_NEXT)
        if b is not None and b.is_visible():
            t = (b.inner_text() or "").strip()
            if t and not RE_RETRY.search(t):
                # tombol solid-purple biasanya jadi tombol "coba lagi" di layar gagal
                ps = fr.locator("p").all()
                txt = " ".join((p.inner_text() or "") for p in ps[:4])
                if RE_FAILTXT.search(txt):
                    b.click(timeout=2500)
                    time.sleep(1.2)
                    return True
    except Exception:
        pass
    return False


def widget_state(fr, base):
    """'solved' | 'fail' | 'busy' — jujur & tahan-bahasa.
    solved: frame/canvas hilang (widget menutup diri) / teks sukses.
    fail  : teks 'did not pass' (multi-bahasa) ATAU tombol Retry muncul.
    PENTING: SETELAH klik Next tombol LENYAP sesaat (state [] saat validasi
    server, terverifikasi di dump chal-1-setelah-next) — penyusutan tombol
    = BUSY, bukan gagal. Versi lama menghakimi 'fail' pada state transien
    ini sehingga vonis juri tak pernah terbaca (biang 0/20 palsu)."""
    if fr is None:
        return "solved"
    try:
        snap = widget_snapshot(fr)
    except Exception:
        return "solved"          # frame lepas = widget selesai/direload
    if snap is None:
        return "solved"
    try:
        n_canvas = fr.locator(SEL_CANVAS).count()
    except Exception:
        return "solved"
    txt = " ".join(snap.get("ps", []))
    if RE_FAILTXT.search(txt):
        return "fail"
    if RE_SUCCESSTXT.search(txt):
        return "solved"
    if not n_canvas:
        return "solved"
    if base:
        nb, bb = snap.get("btns", []), base.get("btns", [])
        if len(nb) < len(bb):
            return "busy"        # validasi berjalan: tombol sempat hilang
        if nb != bb and any(RE_RETRY.search(b) for b in nb):
            return "fail"        # tombol Retry = layar gagal (tahan-bahasa)
    return "busy"


# ------------------------------------------------------------------- CV murni

def photo_top(g):
    """Baris pertama area foto: sisi kanan (bebas kepingan) tak lagi putih."""
    for y in range(g.shape[0] // 2):
        if g[y, int(g.shape[1] * 0.7):].mean() < 235:
            return y
    return g.shape[0] // 3


def _blobs(mask, min_px, step=2):
    """Komponen terhubung 4-arah dari mask biner -> list (x0,y0,x1,y1,n)."""
    m = (mask > 0).astype(np.uint8)
    H, W = m.shape
    seen = np.zeros_like(m)
    out = []
    for y0 in range(0, H, step):
        for x0 in range(0, W, step):
            if m[y0, x0] and not seen[y0, x0]:
                st = [(y0, x0)]
                seen[y0, x0] = 1
                ys, xs = [], []
                while st:
                    y, x = st.pop()
                    ys.append(y)
                    xs.append(x)
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < H and 0 <= nx < W and m[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = 1
                            st.append((ny, nx))
                if len(xs) >= min_px:
                    out.append((min(xs), min(ys), max(xs), max(ys), len(xs)))
    return out


def piece_guess(img, k=1.0):
    """Kepingan kasar dari tray putih kiri-atas (utk titik genggam awal).
    Kolom diskala-k: pada dpr 2 kepingan ~114px citra — potong 120px lama
    memotong mask & menggeser titik genggam ke knob tepi atas."""
    g = np.asarray(img.convert("L")).astype(int)
    pt = photo_top(g)
    sub = g[:pt, :int(118 * k)] < 245
    bs = _blobs(sub, 200, step=1)
    if not bs:
        return None
    x0, y0, x1, y1, n = max(bs, key=lambda b: b[4])
    mask = _shrink(sub[y0:y1 + 1, x0:x1 + 1], max(1, int(round(2 * k))))   # jauhi tepi utk genggam aman
    return {"mask": mask, "bbox": (x0, y0, x1, y1), "photo_top": pt, "g": g}


def densest_window(mask, w=9, h=9):
    """Titik dlm mask dgn jendela wxh paling padat -> aman utk mousedown."""
    m = (mask > 0).astype(float)
    H, W = m.shape
    best, pos = -1, (W // 2, H // 2)
    for y in range(0, max(1, H - h + 1)):
        for x in range(0, max(1, W - w + 1)):
            s = m[y:y + h, x:x + w].sum()
            if s > best:
                best, pos = s, (x + w // 2, y + h // 2)
    return pos


def piece_exact(img0, img1, travel):
    """Mask kepingan PERSIS dari union-diff dua posisi (travel >= lebar/2).
    Return (mask P relatif bbox, bbox kepingan di img0) | (None, None).
    Kolom old-only (T px kiri) dan new-only (T px kanan) menutupi SELURUH
    lebar kepingan -> tak ada bagian tak teramati."""
    a0 = np.asarray(img0).astype(int)
    a1 = np.asarray(img1).astype(int)
    d = np.abs(a0 - a1).sum(axis=2)
    ch = d > 40
    ys, xs = np.where(ch)
    if len(xs) < 150:
        return None, None
    ux0, uy0, ux1, uy1 = xs.min(), ys.min(), xs.max(), ys.max()
    w = ux1 - ux0 + 1 - travel
    h = uy1 - uy0 + 1
    if not (20 <= w <= 200 and 20 <= h <= 200):
        return None, None
    P = np.zeros((h, w), bool)
    # old-only: kolom rel 0..travel-1  (hanya posisi lama)
    P[:, :min(travel, w)] |= ch[uy0:uy0 + h, ux0:ux0 + min(travel, w)]
    # new-only: kolom rel w-travel..w-1 (hanya posisi baru, geser balik)
    if w > travel:
        P[:, w - travel:] |= ch[uy0:uy0 + h, ux0 + w:ux0 + w + travel]
    # sisa tengah (bila travel < w/2): ambil dari threshold tray utk baris atas
    if w > 2 * travel:
        mid = ch[uy0:uy0 + h, ux0 + travel:ux0 + w]
        P[:, travel:w] |= mid
    return P, (ux0, uy0, ux0 + w - 1, uy0 + h - 1)


def seam_score(img0, mask, bbox, cx, cy, lompat=1):
    """...lompat: jarak banding ke luar (3 = MELOMPAPI rim terang kepingan —
    banding +1px salah kaprah: isi-foto vs rim-putih di lubang ASLI justru
    berskor tinggi, sementara area halus nyasar menang palsu)."""
    """Skor kontinuitas tepi bila kepingan (mask+bbox di img0 tray) ditaruh di
    pusat (cx,cy): utk tiap piksel TEPI kepingan (konten foto asli di tray),
    bandingkan dgn piksel tetangga LUAR tujuan (tepat di seberang jahitan).
    Lubang benar = foto nyambung mulus -> beda kecil. Semakin kecil semakin cocok.
    Fallback None bila tak terhitung."""
    try:
        a = np.asarray(img0).astype(float)
        m = (mask > 0)
        ph, pw = m.shape
        y0, x0 = bbox[1], bbox[0]
        ox0 = int(round(cx - pw / 2)); oy0 = int(round(cy - ph / 2))
        H, W = a.shape[:2]
        ys, xs = np.where(m)
        if len(ys) < 30:
            return None
        diffs = []
        for dy, dx in ((-lompat, 0), (lompat, 0), (0, -lompat), (0, lompat)):
            # piksel tepi mask dgn TETANGGA non-mask di sisi (dy,dx) -> titik jahitan
            ny, nx = ys + dy, xs + dx
            inb = (ny >= 0) & (ny < ph) & (nx >= 0) & (nx < pw)
            sel = ~inb.copy()
            ins = np.where(inb)[0]
            sel[ins] = ~m[ny[ins], nx[ins]]
            if sel.sum() < 8:
                continue
            vy, vx = ys[sel], xs[sel]
            src = a[y0 + vy, x0 + vx]               # konten tepi kepingan (di tray)
            ty, tx = oy0 + vy + dy, ox0 + vx + dx   # tetangga luar di tujuan
            ok = (ty >= 0) & (ty < H) & (tx >= 0) & (tx < W)
            if ok.sum() < 8:
                continue
            d = np.sqrt(((a[ty[ok], tx[ok]] - src[ok]) ** 2).sum(axis=1))
            diffs.append(d)
        if not diffs:
            return None
        return float(np.concatenate(diffs).mean())
    except Exception:
        return None


def _match(m2d, tpl):
    """out[y,x] = sum(m2d[y:y+h, x:x+w] * tpl) — korelasi silang valid via FFT."""
    H, W = m2d.shape
    h, w = tpl.shape
    F = np.fft.rfft2(m2d)
    T = np.fft.rfft2(tpl[::-1, ::-1], s=(H, W))
    return np.fft.irfft2(F * T, s=(H, W))[h - 1:H, w - 1:W]


def _peta_datar(g, r=2, ambang=7.0):
    """Peta ke-datar-an: 1 di mana jendela (2r+1)^2 hampir rata. Interior
    lubang DATAR apa pun warnanya (terukur: gelap 59+-3, putih 243+-1,5,
    bahkan 255). Pencari gelap saja BUTA gaya putih."""
    a = g.astype(float)
    n = (2 * r + 1) ** 2
    acc = np.zeros_like(a)
    acc2 = np.zeros_like(a)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            sh = np.roll(np.roll(a, dy, 0), dx, 1)
            acc += sh
            acc2 += sh * sh
    var = np.maximum(acc2 / n - (acc / n) ** 2, 0)
    return (np.sqrt(var) < ambang).astype(float)


def _bentuk_jendela(tpl, win):
    """Dice dua arah utk mutu kecocokan siluet (tahan menyatu/berlebih):
    a = cakupan tpl oleh win; b = kemurnian win di bawah tpl."""
    ov = float((tpl * win).sum())
    ta = float(tpl.sum())
    wa = float(win.sum())
    if ov <= 0 or ta <= 0 or wa <= 0:
        return 0.0
    a, b = ov / ta, ov / wa
    return 2 * a * b / (a + b)


def hole_candidates(g, pt, piece_mask, top_n=8, k=1.0):
    """Kandidat lubang (METODE v4 — tiga pencari: gelap, rim terang, datar).
    Interior lubang terukur bervariasi gaya: gelap (59+-3), putih (243+-1,5),
    murni putih (255) — pencari gelap BUTA gaya terang (biang 4/12 gagal,
    k1_shape<=0.56). Peta DATAR menangkap interior polos apa pun warnanya;
    cincin-siluet vs terang menangkap gaya apa pun. Bentuk = Dice jendela
    (netral gaya, tahan blob menyatu). Seat dipresisi align_masks.
    Peringkat akhir di solve_puzzle: TIER shape>=0.95 (lubang asli selalu
    siluet PERSIS kepingan ini; decoy paling banter 0,93)."""
    pm_full = (piece_mask > 0)
    # === SKALA: eksekusi deteksi pada k=1 (perilaku terbukti bench/live:
    # shape lubang asli 0,97-1,00 di k=1.0; di k=1.5-2.0 ambang datar & template
    # ternyata molor -> hasil 0,4x = buta lubang). Downscale citra+mask, koordinat
    # dikembalikan x k. (uji GT kanvas solved 264,169: k=1 -> 0,86@267,172 vs
    # k=2 native -> 0,42 sampah.)
    if k > 1.2:
        inv = 1.0 / k
        g2 = np.asarray(Image.fromarray(g.astype(np.uint8)).resize(
            (max(8, int(g.shape[1] * inv)), max(8, int(g.shape[0] * inv))),
            Image.LANCZOS)).astype(float)
        pm2 = (np.asarray(Image.fromarray((pm_full * 255).astype(np.uint8)).resize(
            (max(4, int(round(pm_full.shape[1] * inv))),
             max(4, int(round(pm_full.shape[0] * inv)))), Image.LANCZOS)) > 127)
        pt2 = max(0, int(round(pt * inv)))
        out = hole_candidates(g2, pt2, pm2, top_n=top_n, k=1.0)
        ph0, pw0 = pm_full.shape
        for c in out:
            c["x0"] = int(round(c["x0"] * k)); c["y0"] = int(round(c["y0"] * k))
            c["x1"] = c["x0"] + pw0 - 1; c["y1"] = c["y0"] + ph0 - 1
            c["cx"] = c["x0"] + pw0 // 2; c["cy"] = c["y0"] + ph0 // 2
            c["dx"] = int(round(c.get("dx", 0) * k)); c["dy"] = int(round(c.get("dy", 0) * k))
        return out
    fill = _shrink(pm_full, max(1, int(round(2 * k)))).astype(float)
    ph, pw = fill.shape
    fa = max(1.0, float(fill.sum()))
    H, W = g.shape
    f01 = (fill > 0).astype(float)
    ringm = np.zeros((ph, pw), float)
    for r in (1, 2):
        ringm[r:, :] += f01[:-r, :]
        ringm[:-r, :] += f01[r:, :]
        ringm[:, r:] += f01[:, :-r]
        ringm[:, :-r] += f01[:, r:]
    ringm = ((ringm > 0) & (f01 == 0)).astype(float)
    ra = max(1.0, float(ringm.sum()))
    # zona siluet = isi + tepi langsung (bayangan kepingan hidup di SUDUT kotak,
    # di luar zona — itu biang mampatnya skor Dice 0,84 vs 0,79 tadi)
    zona = f01.copy()
    for r in (1, 2, 3):
        zona[r:, :] = np.maximum(zona[r:, :], f01[:-r, :])
        zona[:-r, :] = np.maximum(zona[:-r, :], f01[r:, :])
        zona[:, r:] = np.maximum(zona[:, r:], f01[:, :-r])
        zona[:, :-r] = np.maximum(zona[:, :-r], f01[:, r:])
    datar = _peta_datar(g)
    datar[:pt, :] = 0
    out, seen = [], []
    for th in (40, 55, 70, 85, 100, 115, 130, 145, 160):
        dark = (g < th).astype(float)
        dark[:pt, :] = 0
        bright = (g > 175).astype(float)
        bright[:pt, :] = 0
        peta2 = [dark, bright, datar]
        tpl2 = [fill, ringm, fill]
        for mi in range(3):
            c2 = _match(peta2[mi], tpl2[mi]) / max(1.0, float(tpl2[mi].sum()))
            for _ in range(top_n // 3 + 2):
                y, x = np.unravel_index(int(np.argmax(c2)), c2.shape)
                y, x = int(y), int(x)
                if c2[y, x] < 0.40:     # longgar: gaya interior bertekstur skornya
                    break               # ~0,4-0,55 — dulu 0,58 membutakan 3-round
                c2[max(0, y - 22):y + 22, max(0, x - 22):x + 22] = -1.0
                if x < 2 or y < 2 or x + pw > W - 2 or y + ph > H - 2:
                    continue
                if any(abs(x - s[0]) < 24 and abs(y - s[1]) < 24 for s in seen):
                    continue
                seen.append((x, y))
                m0x, m0y = max(0, x - 12), max(0, y - 12)
                hm = peta2[mi][m0y:y + ph + 12, m0x:x + pw + 12]
                if hm.size == 0 or hm.shape[0] < ph or hm.shape[1] < pw:
                    continue
                # bentuk = recall align_masks vs CROP GAYA (semantik terbukti
                # bench: asli 1,00 / decoy siluet-generik 0,91 / noise <<0,6).
                # JANGAN pakai recall-zona-siluet: penyebutnya menyusut bersama
                # pembilang sehingga decoy ikut naik ke 1,00 (3 kekalahan palsu).
                pmf = pm_full.astype(float)
                hm_d = dark[m0y:y + ph + 12, m0x:x + pw + 12]
                hm_b = bright[m0y:y + ph + 12, m0x:x + pw + 12]
                hm_f = datar[m0y:y + ph + 12, m0x:x + pw + 12]
                sd, dxd, dyd = align_masks(pmf, hm_d, search=16)
                sr, rxr, ryr = align_masks(ringm, hm_b, search=16)
                sf, fxr, fyr = align_masks(pmf, hm_f, search=16)
                shape = max(sd, sr, sf)
                if sd >= max(sr, sf):
                    sx, sy = m0x + dxd, m0y + dyd
                elif sr >= sf:
                    sx, sy = m0x + rxr, m0y + ryr
                else:
                    sx, sy = m0x + fxr, m0y + fyr
                if sx < 2 or sy < 2 or sx + pw > W - 2 or sy + ph > H - 2:
                    continue
                wd = dark[sy:sy + ph, sx:sx + pw]
                wb = bright[sy:sy + ph, sx:sx + pw]
                wf = datar[sy:sy + ph, sx:sx + pw]
                cv = max(float((fill * wd).sum()) / fa,
                         float((ringm * wb).sum()) / ra,
                         float((fill * wf).sum()) / fa)
                if cv < 0.35:
                    continue
                patch = g[sy:sy + ph, sx:sx + pw]
                interior = patch[fill > 0]
                interior_std = float(interior.std()) if interior.size else 99
                spill = float(wd[fill == 0].mean()) if (fill == 0).any() else 0.0
                score = (1.4 * cv + 0.9 * shape
                         + 0.15 * max(0.0, 1.0 - interior_std / 25.0)
                         - 0.5 * spill)
                out.append({"x0": sx, "y0": sy, "x1": sx + pw - 1, "y1": sy + ph - 1,
                            "cx": sx + pw // 2, "cy": sy + ph // 2,
                            "score": score, "shape": shape,
                            "dx": 0, "dy": 0, "std": interior_std,
                            "rim": 0, "cov": round(cv, 2)})
    out.sort(key=lambda z: -z["score"])
    ded = []
    for c in out:
        if all(abs(c["cx"] - d["cx"]) > 25 or abs(c["cy"] - d["cy"]) > 25 for d in ded):
            ded.append(c)
        if len(ded) >= top_n:
            break
    return ded


def align_masks(pm, hm, search=12):
    """Geser terbaik mask kepingan pm ke mask lubang hm.
    Konvensi: pm[u,v] cocok hm[u+dy, v+dx] -> posisi duduk bbox kepingan
    (top-left) = bbox hm + (dx, dy). Skor = recall lubang (|pm n hm| / |hm|)
    pada geseran terbaik — tahan erosi tepi (rim bayangan) karena penalti
    luas pm konstan di semua geseran (argmax tak bias)."""
    ph, pw = pm.shape
    hh, hw = hm.shape
    pmf = pm.astype(float)
    hmf = hm.astype(float)
    hn = hmf.sum()
    if hn < 50:
        return 0.0, 0, 0
    best = (0.0, 0, 0)
    # ekspektasi geseran dari sepusat massa (pemecah dataran tinggi skor)
    pyc, pxc = np.array(np.where(pmf > 0)).mean(axis=1)
    hyc, hxc = np.array(np.where(hmf > 0)).mean(axis=1)
    dx_c, dy_c = hxc - pxc, hyc - pyc
    for dy in range(-search, search + 1):
        y0h, y1h = max(0, dy), min(hh, dy + ph)
        y0p, y1p = y0h - dy, y1h - dy
        if y1h <= y0h:
            continue
        for dx in range(-search, search + 1):
            x0h, x1h = max(0, dx), min(hw, dx + pw)
            x0p, x1p = x0h - dx, x1h - dx
            if x1h <= x0h:
                continue
            ov = float((pmf[y0p:y1p, x0p:x1p] * hmf[y0h:y1h, x0h:x1h]).sum())
            s = ov / hn - 0.004 * (abs(dx - dx_c) + abs(dy - dy_c))
            if s > best[0]:
                best = (s, dx, dy)
    return best


def masked_ncc(g, T, M, y0, x0):
    """Pearson NCC template T (mask M) pada posisi (y0,x0) di g. 1.0 = identik."""
    th, tw = T.shape
    R = g[y0:y0 + th, x0:x0 + tw]
    if R.shape != T.shape:
        return -1.0
    t = T[M]
    r = R[M]
    t = t - t.mean()
    r = r - r.mean()
    dn = np.sqrt((t * t).sum() * (r * r).sum())
    return float((t * r).sum() / dn) if dn > 1e-6 else -1.0


def localize_piece(g, T, M, cx, cy, span=14):
    """Cari posisi kepingan (top-left template) dlm window +-span px sekitar (cx,cy).
    Return (x, y, score) — utk closed-loop verifikasi penempatan."""
    th, tw = T.shape
    H, W = g.shape
    best = (-2.0, cx, cy)
    y_lo, y_hi = max(0, cy - span), min(H - th, cy + span)
    x_lo, x_hi = max(0, cx - span), min(W - tw, cx + span)
    for y in range(y_lo, y_hi + 1):
        for x in range(x_lo, x_hi + 1):
            s = masked_ncc(g, T, M, y, x)
            if s > best[0]:
                best = (s, x, y)
    return best[1], best[2], best[0]


# ------------------------------------------------------- aksi drag (koordinat)

def _to_viewport(box, k, x, y):
    """(x,y) piksel citra -> koordinat viewport mouse (CSS px)."""
    return box["x"] + x / k, box["y"] + y / k


def drag_to(pg, box, k, p_from, p_to, humanize=True):
    """Drag mouse dari posisi citra p_from ke p_to (skala otomatis k px-citra/CSS)."""
    x0, y0 = _to_viewport(box, k, *p_from)
    x1, y1 = _to_viewport(box, k, *p_to)
    dx, dy = x1 - x0, y1 - y0
    pg.mouse.move(x0, y0)
    time.sleep(random.uniform(0.08, 0.18))
    pg.mouse.down()
    if not humanize:
        pg.mouse.move(x1, y1)
        time.sleep(0.05)
        pg.mouse.up()
        time.sleep(0.25)
        return
    steps = 18 + random.randrange(10)
    dist = math.hypot(dx, dy)
    over = min(7.0, max(2.0, dist * 0.03)) * (1 if dx >= 0 else -1)
    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)                 # smoothstep
        x = x0 + (dx + over) * ease + random.uniform(-0.6, 0.6)
        y = y0 + dy * ease + math.sin(t * math.pi) * random.uniform(0.4, 1.8) \
            + random.uniform(-0.5, 0.5)
        pg.mouse.move(x, y)
        time.sleep(random.uniform(0.006, 0.018))
    for _ in range(2):                              # overshoot lalu settle
        pg.mouse.move(x1 + random.uniform(-0.5, 0.5), y1 + random.uniform(-0.5, 0.5))
        time.sleep(random.uniform(0.03, 0.07))
    pg.mouse.up()
    time.sleep(0.35)


def drag_verified(pg, fr, box, k, down_xy, p_to, img_ref, humanize=True):
    """Drag dgn VERIFIKASI langkah kecil: setelah mousedown + geser mikro,
    cek diff canvas (kepingan benar2 tergenggam & ikut bergerak) SEBELUM
    drag penuh dilanjutkan. Return True bila kepingan bergerak.
    img_ref = citra sebelum mousedown (biasanya img0)."""
    x0, y0 = _to_viewport(box, k, *down_xy)
    x1, y1 = _to_viewport(box, k, *p_to)
    pg.mouse.move(x0, y0)
    time.sleep(random.uniform(0.08, 0.16))
    pg.mouse.down()
    # langkah kecil dulu (±5px CSS) lalu buktikan kepingan ikut
    sx, sy = x0 + (5 if x1 >= x0 else -5), y0
    pg.mouse.move(sx, sy)
    time.sleep(0.05)
    try:
        img = grab(fr)
        d = np.abs(np.asarray(img).astype(int) - np.asarray(img_ref).astype(int)).sum(axis=2)
        moved = int((d > 40).sum()) > 150
    except Exception:
        moved = False
    if not moved:
        pg.mouse.up()
        time.sleep(0.15)
        return False
    # lanjutkan jalur ke target (smoothstep + wobble ringan + settle)
    dx, dy = x1 - sx, y1 - sy
    if humanize:
        steps = 14 + random.randrange(8)
        for i in range(1, steps + 1):
            t = i / steps
            ease = t * t * (3 - 2 * t)
            pg.mouse.move(sx + dx * ease + random.uniform(-0.4, 0.4),
                          sy + dy * ease + math.sin(t * math.pi) * random.uniform(0.3, 1.4)
                          + random.uniform(-0.4, 0.4))
            time.sleep(random.uniform(0.006, 0.016))
    pg.mouse.move(x1, y1)
    time.sleep(random.uniform(0.05, 0.1))
    pg.mouse.move(x1 + random.uniform(-0.3, 0.3), y1 + random.uniform(-0.3, 0.3))
    time.sleep(random.uniform(0.03, 0.07))
    pg.mouse.up()
    time.sleep(0.3)
    return True


# ------------------------------------------------- pemulihan widget (frame mati)

def _daftar_frame(pg):
    """URL semua frame utk diagnosa 'frame widget tak ditemukan' (absen vs nyasar)."""
    try:
        return [f.url[:90] for f in pg.frames]
    except Exception:
        return []


def pulihkan_widget(pg, note=print, tunggu=8.0):
    """Widget iframe tak hidup (spinner 'Loading' abadi = request /core/v4/captcha
    tak pernah selesai — bukti net log: api/init tak pernah muncul) -> hidupkan
    ULANG di tempat, tanpa bakar slot:
      1) muat ulang src iframe 'Captcha' — judul LITERAL dari JS Proton
         (human-verification-modal chunk: title:"Captcha"), tahan-bahasa;
         set src ulang = request BARU membatalkan koneksi yang nyangkut;
      2) toggle tab modal (Email -> CAPTCHA, selektor struktur
         button.tabs-list-link/[role=tab]) -> React mount ulang iframenya.
    Return True bila widget (frame+kanvas) hidup lagi dalam ~{tunggu} dtk."""
    try:
        ok = pg.evaluate("""() => {
            const f = document.querySelector('iframe[title="Captcha"]');
            const s = f && f.getAttribute('src');
            if (!s) return 0;
            f.removeAttribute('src');
            f.setAttribute('src', s);
            return 1;
        }""")
        if ok:
            note("[captcha] pemulihan: iframe widget dimuat ulang")
    except Exception:
        pass
    if widget_frame(pg, tries=10, pause=0.4) is not None:
        return True
    try:
        tabs = pg.locator("dialog.human-verification-modal button.tabs-list-link, "
                          "dialog.human-verification-modal [role=tab]")
        n = tabs.count()
        if n >= 2:
            tabs.nth(n - 1).click(timeout=2500)     # tab lain (mis. Email)
            time.sleep(1.0)
            tabs.nth(0).click(timeout=2500)          # balik ke tab captcha
            note("[captcha] pemulihan: tab modal di-toggle (mount ulang widget)")
    except Exception as e:
        note(f"[captcha] pemulihan: toggle tab gagal ({type(e).__name__})")
    return widget_frame(pg, tries=max(2, int(tunggu / 0.4)), pause=0.4) is not None


# ----------------------------------------------------------------- solver flow

def _content_template(img, P, bbox):
    """Isi kepingan (luma) + mask utk lokalisasi closed-loop."""
    x0, y0, x1, y1 = bbox
    g = np.asarray(img.convert("L")).astype(float)
    T = g[y0:y1 + 1, x0:x1 + 1].copy()
    M = P.copy()
    if M.shape != T.shape:
        h = min(M.shape[0], T.shape[0])
        w = min(M.shape[1], T.shape[1])
        T, M = T[:h, :w], M[:h, :w]
    return T, M


def seam_energy(img, sprite_rgb, mask, X, Y, ring_px=3):
    """Energi gradien di pita TEPI luar kepingan bila sprite ditempel di (X,Y).
    Seat benar = isi kepingan MENYAMBUNG mulus dgn foto sekitar -> energi kecil.
    (Kontinuitas gambar; terbukti membedakan seat asli vs meleset beberapa px.)"""
    comp = img.copy()
    h, w = mask.shape
    m = mask > 0
    if X < 0 or Y < 0 or X + w > comp.shape[1] or Y + h > comp.shape[0]:
        return 1e9
    comp[Y:Y + h, X:X + w][m] = sprite_rgb[m]
    ring = np.zeros_like(m)
    for r in range(1, ring_px + 1):
        ring[r:, :] |= m[:-r, :]
        ring[:-r, :] |= m[r:, :]
        ring[:, r:] |= m[:, :-r]
        ring[:, :-r] |= m[:, r:]
    ring &= ~m
    rys, rxs = np.where(ring)
    gy, gx = np.gradient(comp.mean(axis=2))
    gmag = np.abs(gy) + np.abs(gx)
    return float(gmag[rys + Y, rxs + X].mean())


def refine_seat_seam(img, sprite_rgb, mask, coarse, span=9):
    """Perbaiki target seat dari align_masks pakai kontinuitas gambar:
    cari (X,Y) dlm +-span px yg MEMINIMALKAN energi seam. Return (X, Y, energy)."""
    best = (1e9, coarse[0], coarse[1])
    for dy in range(-span, span + 1):
        for dx in range(-span, span + 1):
            X, Y = coarse[0] + dx, coarse[1] + dy
            e = seam_energy(img, sprite_rgb, mask, X, Y)
            if e < best[0]:
                best = (e, X, Y)
    return best[1], best[2], best[0]


def nilai_seat(img, P, bbox, cx, cy, std_kep, lompat=3):
    """Skor gabungan seat: seam (lompati rim terang) + 1.2*|std_sekitar -
    std_isi_kepingan| (kecocokan tekstur asal-usul potongan). Terendah =
    paling mungkin. Kebal decoy siluet-kembar: shape 1,00 pun tergusur bila
    kontinuitas/teksturnya meleset (kanvas bedah: asli nilai 39 vs kembar 123)."""
    g = np.asarray(img.convert("L")).astype(float)
    H, W = g.shape
    ph, pw = P.shape
    x, y = int(round(cx - pw / 2)), int(round(cy - ph / 2))
    if x < 2 or y + ph > H - 2 or x + pw > W - 2:
        return None
    s = seam_score(img, P, bbox, cx, cy, lompat=lompat)
    if s is None:
        return None
    y0, x0 = max(0, y - 3), max(0, x - 3)
    ling = g[y0:min(H, y + ph + 3), x0:min(W, x + pw + 3)]
    std = float(ling.std()) if ling.size else 0.0
    return s + 1.2 * abs(std - std_kep)


def seam_scan(img, P, bbox, pt):
    """Fallback pindai seat dua-tingkat saat keyakinan bentuk rendah (gaya
    interior bertekstur tak terlihat ketiga pencari). Skor = seam (kontinuitas
    foto) + PENALTI ANTI-BIAS-PUTIH: area putih-rata di sekeliling seat = artefak
    rim-vs-putih, bukan lubang (pelajaran dbg_holescan). Return (nilai, x, y)
    dgn (x,y) = top-left bbox kepingan, atau None."""
    g = np.asarray(img.convert("L")).astype(float)
    H, W = g.shape
    ph, pw = P.shape
    # tekstur isi kepingan = "sidik jari" asal-usulnya: kepingan dipotong dari
    # area bertekstur X -> seat yang benar bertekstur serupa (bukti terukur:
    # lubang asli std 74 vs titik palsu 20-24 vs acak 6-10)
    m_isi = _shrink(P > 0, 2)
    isi = g[bbox[1]:bbox[1] + ph, bbox[0]:bbox[0] + pw]
    std_kep = float(isi[m_isi].std()) if m_isi.any() and isi.shape == m_isi.shape else 30.0

    def nilai(cx, cy):
        x, y = int(round(cx - pw / 2)), int(round(cy - ph / 2))
        if x < 2 or y + ph > H - 2 or x + pw > W - 2:
            return None
        s = seam_score(img, P, bbox, cx, cy, lompat=3)   # lompati rim
        if s is None:
            return None
        y0, x0 = max(0, y - 3), max(0, x - 3)
        ling = g[y0:min(H, y + ph + 3), x0:min(W, x + pw + 3)]
        std = float(ling.std()) if ling.size else 0.0
        return s + 1.2 * abs(std - std_kep)   # penalti kecocokan tekstur

    # tingkat 1: kasar step 12 seluruh area foto
    skor = {}
    y = max(2, pt - ph // 2)
    while y < H - ph:
        x = 2
        while x < W - pw:
            v = nilai(x + pw / 2, y + ph / 2)
            if v is not None:
                skor[(x, y)] = v
            x += 12
        y += 12
    if not skor:
        return None
    minima = []
    for (x, y), v in sorted(skor.items(), key=lambda z: z[1]):
        if all(abs(x - m[1]) > 24 or abs(y - m[2]) > 24 for m in minima):
            minima.append((v, x, y))
        if len(minima) == 6:
            break
    # tingkat 2: sempitkan +-12 (step 2) lalu +-2 (step 1)
    hasil = []
    for v, x, y in minima:
        bb = (v, x, y)
        for dy in range(-12, 13, 2):
            for dx in range(-12, 13, 2):
                nv = nilai(x + dx + pw / 2, y + dy + ph / 2)
                if nv is not None and nv < bb[0]:
                    bb = (nv, x + dx, y + dy)
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                nv = nilai(bb[1] + dx + pw / 2, bb[2] + dy + ph / 2)
                if nv is not None and nv < bb[0]:
                    bb = (nv, bb[1] + dx, bb[2] + dy)
        hasil.append(bb)
    hasil.sort(key=lambda z: z[0])
    return hasil[0]


def solve_puzzle(pg, note=print, bench=None):
    """Pecahkan SATU puzzle (tanpa klik Retry). Return dict status:
    {'status': 'solved'|'fail'|'error', 'sebab': str, ...detail utk bench}"""
    t_start = time.time()
    info = {"t": 0.0, "sebab": ""}
    fr = widget_frame(pg)
    if fr is None:
        info.update(status="error", sebab="frame widget tak ditemukan")
        return info
    try:
        box = canvas_box(fr)
        img0 = grab(fr)
    except Exception as e:
        info.update(status="error", sebab=f"grab awal gagal: {e}")
        return info
    k = img0.width / box["width"]          # px-citra per CSS px (dpr backing store)
    info["k"] = round(k, 3)
    try:                                   # simpan kanvas utk bedah offline
        # [fix disk 2026-10-13] dulu SELALU simpan -> analysis/dbg-captcha 660MB
        # (500 akun x beberapa PNG/attempt = tambahan ratusan MB). Bedah captcha
        # sudah selesai; aktifkan lagi dgn DBG_CAPTCHA=1 bila perlu.
        if os.environ.get("DBG_CAPTCHA") == "1":
            d = HERE + "/analysis/dbg-captcha/live"
            os.makedirs(d, exist_ok=True)
            img0.save(f"{d}/p-{int(time.time() * 1000)}.png")
    except Exception:
        pass
    g0 = np.asarray(img0.convert("L")).astype(float)
    pt = photo_top(g0)
    info["photo_top"] = pt

    # --- kepingan: tebakan tray utk titik genggam, lalu kalibrasi diff utk mask persis
    guess = None
    for _t in range(4):                 # puzzle baru kadang belum ter-render
        guess = piece_guess(img0, k)
        if guess is not None:
            break
        time.sleep(1.2)
        try:
            img0 = grab(fr)
        except Exception:
            break
        k = img0.width / box["width"]
        g0 = np.asarray(img0.convert("L")).astype(float)
        pt = photo_top(g0)
    if guess is None:
        info.update(status="error", sebab="kepingan tak terdeteksi di tray")
        return info
    travel_css = 36                              # >= lebar/2 kepingan (max ~59 CSS) utk union-diff penuh
    grab_pt = densest_window(guess["mask"])      # relatif bbox kepingan tebakan
    down_xy = (guess["bbox"][0] + grab_pt[0], guess["bbox"][1] + grab_pt[1])

    img1 = None
    mw, mh = guess["mask"].shape[1], guess["mask"].shape[0]
    alt_pts = [down_xy,
               (guess["bbox"][0] + mw // 2, guess["bbox"][1] + mh // 2),
               (guess["bbox"][0] + mw // 3, guess["bbox"][1] + mh // 2),
               (guess["bbox"][0] + 2 * mw // 3, guess["bbox"][1] + mh // 3),
               (guess["bbox"][0] + mw // 2, guess["bbox"][1] + 2 * mh // 3)]
    for alt in range(len(alt_pts)):
        down_xy = alt_pts[alt]
        ok_drag = drag_verified(pg, fr, box, k, down_xy,
                                (down_xy[0] + travel_css * k, down_xy[1]), img0)
        try:
            fr = widget_frame(pg, tries=3) or fr
            img1 = grab(fr)
        except Exception as e:
            info.update(status="error", sebab=f"grab pasca-kalibrasi gagal: {e}")
            return info
        if ok_drag:
            P, pbb = piece_exact(img0, img1, int(round(travel_css * k)))
            if P is not None:
                break
        # mousedown nyasar / kepingan tak bergerak -> balikkan & coba titik lain
        drag_to(pg, box, k,
                (down_xy[0] + travel_css * k, down_xy[1]), down_xy, humanize=False)
    if P is None:
        info.update(status="error", sebab="kepingan tak bergerak saat kalibrasi")
        return info
    px0, py0, px1, py1 = pbb                  # bbox kepingan di img0 (posisi tray)
    P = _shrink(P)                            # buang bulu bayangan tepi
    T, M = _content_template(img0, P, pbb)
    info["piece"] = f"{P.shape[1]}x{P.shape[0]}@({px0},{py0})"

    # posisi kepingan SEKARANG (setelah drag kalibrasi) dlm piksel citra
    cur = [px0 + int(round(travel_css * k)), py0]

    # --- kandidat lubang dari img0 (keadaan puzzle asli) ---
    cands = hole_candidates(g0, pt, P, k=k)
    info["n_cand"] = len(cands)
    if not cands:
        info.update(status="error", sebab="tak ada kandidat lubang")
        return info
    # --- PERINGKAT KUNCI: kontinuitas tepi (seam). Kepingan = potongan foto asli;
    # di lubang BENAR tepinya menyambung mulus dgn sekeliling foto (skor kecil).
    # Ini bukti pemilihan lubang SEBELUM Next (puzzle hanya bisa 1x tebakan).
    for c in cands:
        cx = c["x0"] + c.get("dx", 0) + P.shape[1] / 2.0
        cy = c["y0"] + c.get("dy", 0) + P.shape[0] / 2.0
        c["seam"] = seam_score(img0, P, pbb, cx, cy)
    # ---- KEBIJAKAN TERVERIFIKASI LIVE (bench 8/16 + batch 13/19): TIER bentuk
    # (shape>=0,95 = siluet persis) lalu diurut seam. JANGAN pakai "nilai>55"
    # utk menimpa kandidat: kalibrasi 1 kanvas itu salah — di foto bertekstur
    # nilai lubang asli memang 100-160 dan pindai malah menggusur pemenang
    # (bukti: bench 20/20 kalah saat nilai-ranking menimpa tier).
    cands.sort(key=lambda z: (0 if z["shape"] >= 0.95 else 1,
                              -z["shape"],
                              z["seam"] if z["seam"] is not None else 9e9))
    info["seam_top"] = round(cands[0]["seam"], 1) if cands[0].get("seam") is not None else None
    info["k1_shape"] = round(cands[0]["shape"], 2)
    # Penyelamat HANYA bila SEMUA kandidat nyaris kosong (shape<0.55).
    # Benchmark GT (kanvas solved 264,169 + upscale 2x): lubang asli SELALU
    # shape 0,86-0,97 dan decoy <=0,42 — jadi shape adalah pemisahnya, bukan seam.
    # Ambang lama 0,85 terlalu agresif: kandidat asli 0,70 malah dibajak fallback.
    if not cands or max(cc["shape"] for cc in cands) < 0.55:
        note("[captcha] keyakinan bentuk rendah — fallback pindai seam")
        best = seam_scan(img0, P, pbb, pt)
        if best is not None:
            v, sx, sy = best
            # shape JUJUR 0.50 (bukan 0.96 karangan yang selalu menang tier-sort
            # dan membakar puzzle). [fix audit 2026-10-09] APPEND bukan insert(0):
            # dulu kandidat lemah MEMBAJAK puncak peringkat -> dipasang duluan ->
            # puzzle hangus "did not pass" (kelas 'solver meleset').
            cands.append({"x0": sx, "y0": sy, "x1": sx + P.shape[1] - 1,
                             "y1": sy + P.shape[0] - 1,
                             "cx": sx + P.shape[1] // 2, "cy": sy + P.shape[0] // 2,
                             "score": 0.0, "shape": 0.50, "dx": 0, "dy": 0,
                             "std": 0, "rim": 0, "cov": 1.0, "seam": v})
            info["scan_fallback"] = {"seat": (sx, sy), "nilai": round(v, 1)}
            note(f"[captcha] seat dari pindai seam: ({sx},{sy}) nilai={v:.1f}")
    note(f"[captcha] peringkat seam: "
         f"{[(c['x0'], c['y0'], (round(c['seam'], 1) if c.get('seam') is not None else None)) for c in cands[:4]]}")

    base = None
    trials_log = []
    for ci, c in enumerate(cands[:3]):
        # target: bbox kepingan harus duduk di bbox lubang digeser (dx,dy) hasil alignment
        tgt = [c["x0"] + c["dx"], c["y0"] + c["dy"]]
        note(f"[captcha] kandidat {ci+1}: lubang=({c['x0']},{c['y0']}) "
             f"score={c['score']:.2f} shape={c['shape']:.2f} -> duduk di ({tgt[0]},{tgt[1]})")
        pre = grab(fr)
        ok_drag = drag_verified(pg, fr, box, k, _canvas_to_down(P, cur, down_xy, pbb),
                                _canvas_to_down(P, tgt, down_xy, pbb), pre, humanize=True)
        if not ok_drag:                 # genggaman lepas -> ulangi di pusat kepingan
            note("[captcha] drag kandidat tak tergenggam — ulangi titik genggam")
            drag_verified(pg, fr, box, k,
                          (cur[0] + P.shape[1] // 2, cur[1] + P.shape[0] // 2),
                          (tgt[0] + P.shape[1] // 2, tgt[1] + P.shape[0] // 2),
                          pre, humanize=True)
        cur = list(tgt)
        # --- closed-loop: lokalisasi isi kepingan -> nudge sampai presisi ---
        placed = _close_loop(pg, fr, box, k, T, M, P, cur, tgt, note)
        trials_log.append({"tgt": tuple(tgt), "placed": tuple(cur), "nudges": placed})
        # --- verifikasi: tak ada sisa blob gap serupa di tempat lain ---
        gf = np.asarray(grab(fr).convert("L")).astype(float)
        # [DINONAKTIFKAN] heuristik "sisa lubang" -> pindah: BERMASALAH — area gelap
        # alami foto ikut terbaca sisa-lubang sehingga kepingan dipindah DARI posisi
        # benar ke decoy (terbukti di log bench: peletakan presisi justru disabot).
        # if resid: ... pindahkan kepingan ...
        _ = _residual_gap(gf, pt, P, cur)  # biarkan tercatat utk diagnostik bila perlu
        # --- BARU Next: sabar (poll actionable), tahan-bahasa ---
        base = widget_snapshot(fr)     # baseline SIAP-pakai, SETELAH drag (Reset jd enabled)
        ok = _click_next(fr, note)
        if not ok:
            info["sebab"] = "Next tak bisa diklik (disabled/absen)"
            note(f"[captcha] {info['sebab']}")
            break
        st = _wait_outcome(fr, base, note)
        if st == "solved":
            info.update(status="solved",
                        sebab=f"solved kandidat {ci+1}",
                        t=round(time.time() - t_start, 1), trials=trials_log)
            note("[captcha] SOLVED — verifikasi selesai")
            return info
        if st == "fail":
            info.update(status="fail", sebab=f"kandidat {ci+1} ditolak (did not pass)",
                        t=round(time.time() - t_start, 1), trials=trials_log)
            note("[captcha] jawaban ditolak widget — puzzle habis (butuh Retry)")
            return info
        # busy terlalu lama: anggap gagal, jangan klik Next buta lagi
        info.update(status="fail", sebab=f"kandidat {ci+1}: outcome tak jelas",
                    t=round(time.time() - t_start, 1), trials=trials_log)
        return info
    info.update(status="fail", sebab=info.get("sebab") or "semua kandidat habis",
                t=round(time.time() - t_start, 1), trials=trials_log)
    return info


def _shrink(P, n=1):
    """Erosi n px: buang tepi lunak (bayangan) dari mask union-diff."""
    Q = P.copy()
    for _ in range(n):
        E = Q.copy()
        E[1:, :] &= Q[:-1, :]
        E[:-1, :] &= Q[1:, :]
        E[:, 1:] &= Q[:, :-1]
        E[:, :-1] &= Q[:, 1:]
        Q = E
    return Q


def _canvas_to_down(P, canvas_xy, down_xy, pbb):
    """Titik mousedown relatif kepingan tetap; geser mengikuti bbox kepingan."""
    return (down_xy[0] + (canvas_xy[0] - pbb[0]),
            down_xy[1] + (canvas_xy[1] - pbb[1]))


def _close_loop(pg, fr, box, k, T, M, P, cur, tgt, note, tries=4):
    """Verifikasi+perbaiki penempatan: lokalisasi isi kepingan (masked-NCC),
    nudge halus dua sumbu (+-2..8px) sampai |err|<=1 px. Return jumlah nudge."""
    nudges = 0
    gr = densest_window(M)                      # titik genggam aman (pusat massa)
    for it in range(tries):
        try:
            gnow = np.asarray(grab(fr).convert("L")).astype(float)
        except Exception:
            time.sleep(0.4)
            continue
        x, y, s = localize_piece(gnow, T, M, tgt[0], tgt[1], span=24)
        err = (x - tgt[0], y - tgt[1])
        if s < 0.55:
            note(f"[captcha] lokalisasi lemah (score={s:.2f}) — pakai posisi drag")
            cur[0], cur[1] = tgt
            return nudges
        if abs(err[0]) <= 1 and abs(err[1]) <= 1:
            cur[0], cur[1] = x, y
            note(f"[captcha] penempatan presisi (err={err}, score={s:.2f})")
            return nudges
        # koreksi dua sumbu penuh (tutup err, cap 20px) ke arah kebalikan error
        nx = -min(20, max(2, abs(err[0]))) * (1 if err[0] > 0 else -1) if err[0] else 0
        ny = -min(20, max(2, abs(err[1]))) * (1 if err[1] > 0 else -1) if err[1] else 0
        note(f"[captcha] nudge {it+1}: err={err} -> ({nx},{ny})")
        gx, gy = x + gr[0], y + gr[1]           # genggam di pusat massa kepingan
        drag_to(pg, box, k, (gx, gy), (gx + nx, gy + ny), humanize=False)
        cur[0], cur[1] = x + nx, y + ny
        nudges += 1
    return nudges


def _residual_gap(g, pt, P, cur, min_score=0.55):
    """Cek masih adakah blob gap tersisa (kepingan meleset kandidat).
    Abaikan blob yg tertutup posisi kepingan sekarang (cur)."""
    cands = hole_candidates(g, pt, P, top_n=3)
    for c in cands:
        if c["shape"] < min_score:
            continue
        # posisi kepingan sekarang (bbox top-left kira2 cur) — kalau blob ini
        # di tempat kepingan, berarti itu isi kepingan gelap, bukan lubang
        if abs(c["x0"] - cur[0]) < 18 and abs(c["y0"] - cur[1]) < 18:
            continue
        return (c["x0"] + c["dx"], c["y0"] + c["dy"])
    return None


def _click_next(fr, note, timeout=4.0):
    """Klik tombol aksi utama tahan-bahasa, sabar: poll visible+enabled."""
    end = time.time() + timeout
    while time.time() < end:
        b = _btn(fr, SEL_NEXT)
        if b is None:
            b = _btn(fr, "button[type=submit]")
        if b is not None:
            try:
                if b.is_visible() and b.is_enabled():
                    b.click(timeout=1500)
                    return True
            except Exception:
                pass
        time.sleep(0.15)
    return False


def _wait_outcome(fr, base, note, timeout=15.0):
    """Tunggu hasil submit: solved (widget menutup) / fail (teks/tombol baru)."""
    end = time.time() + timeout
    while time.time() < end:
        st = widget_state(fr, base)
        if st != "busy":
            return st
        time.sleep(0.4)
    return "busy"


def _kelas_gagal(info):
    """Kelas kegagalan utk kebijakan bail cepat: widget_mati (frame tak pernah
    hidup — round buta TIDAK menolong) / tak_jelas (outcome submit menggantung)
    / ditolak (jawaban salah — masih ada obat: Retry puzzle baru) / transien
    (frame lepas, kepingan tak tergenggam — biasanya sembuh round berikutnya)."""
    s = (info.get("sebab") or "").lower()
    if "frame widget" in s or "widget mati" in s:
        return "widget_mati"
    if "tak jelas" in s:
        return "tak_jelas"
    if "ditolak" in s:
        return "ditolak"
    return "transien"


def solve(pg, note=print, max_rounds=3, max_try=3):
    """...max_rounds=5: puzzle fresh per round = 5 kesempatan/modal. Bukti
    bench: first-try ~50% (keluarga titik-buta masih ada) -> 1-(1-p)^5 ~= 97%
    per modal. Keluarga buta tersaring oleh Retry, bukan oleh pindai."""
    """API produksi: pecahkan modal slider sampai solved. True bila berhasil.
    Hasil terakhir dibaca pemanggil lewat solve.terakhir (dict status/sebab).
    [fix 2026-10-14] bail CEPAT utk kegagalan sistemik — jangan 5 round buta:
    bukti baseline run-capt-base (10 slot, 6/10 sukses): 3 slot mati kelas
    widget-stuck/outcome-tak-jelas ±77 dtk percuma per slot. Widget mati =
    pulihkan di tempat (reload iframe + toggle tab) sekali, tetap mati ->
    gagal CEPAT biar slot me-retry exit/profil baru; kelas sama 2-3x berturut
    = sistemik -> bail (round 4-5 hampir tak pernah menolong)."""
    import captcha_service  # noqa: F401  (hook layanan pihak ketiga; opsional)
    solve.terakhir = {"status": "error", "sebab": "belum jalan"}
    kelas_lalu, streak, widget_dipulihkan = None, 0, False
    for rnd in range(max_rounds):
        info = solve_puzzle(pg, note=note)
        solve.terakhir = dict(info)
        note(f"[captcha] round={rnd} -> {info['status']} ({info['sebab']})")
        if info["status"] == "solved":
            return True
        kelas = _kelas_gagal(info)
        streak = streak + 1 if kelas == kelas_lalu else 1
        kelas_lalu = kelas
        if kelas == "widget_mati":
            if not widget_dipulihkan:
                widget_dipulihkan = True
                note(f"[captcha] widget tak hidup — frame: {_daftar_frame(pg)}")
                if pulihkan_widget(pg, note):
                    continue
            solve.terakhir.update(status="error",
                                  sebab="widget mati (tak hidup setelah reload+toggle)")
            note("[captcha] widget tetap mati — bail cepat, minta retry slot (exit/profil baru)")
            return False
        if streak >= (2 if kelas == "tak_jelas" else 3):
            solve.terakhir.update(status="fail", sebab=f"{info['sebab']} (x{streak} berturut)")
            note(f"[captcha] kelas '{kelas}' {streak}x berturut — bail cepat, minta retry slot")
            return False
        if info["status"] == "error":
            # widget reload / frame lepas: beri kesempatan analisis ulang
            time.sleep(1.0)
            continue
        # fail = puzzle habis -> Retry (puzzle baru) lalu round berikutnya
        if not click_retry(pg, note):
            time.sleep(1.0)
    return False


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import os
        sys.path.insert(0, HERE)
        import fp_profile
        fp_profile.ensure_display()
        prof = fp_profile.build_profile()
        os.environ["FONTCONFIG_FILE"] = fp_profile.write_fonts_conf(prof)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path="/usr/bin/google-chrome",
                                  headless=False, args=fp_profile.chrome_args(prof))
            ctx = b.new_context(locale="en-US", timezone_id="Asia/Jakarta",
                                viewport=prof["viewport"])
            pg = ctx.new_page()
            pg.add_init_script(fp_profile.init_script(prof))
            pg.add_init_script(
                "window.__msg=[]; window.addEventListener('message', e => { try { "
                "window.__msg.push({t:(e.data&&e.data.type)||'?'}); } catch(x){} }, true);")
            pg.goto("https://account.proton.me/mail/signup?plan=free&noPromo=1&ref=mail_plus_intro-2",
                    wait_until="domcontentloaded", timeout=90000)
            for i in range(30):
                time.sleep(1)
                try:
                    st = pg.evaluate("window.__msg ? window.__msg.map(m => m.t) : []")
                except Exception:
                    st = []
                if st.count("onload") >= 2 and st.count("child.message.data") >= 1:
                    break
            for _t in range(15):
                ok = False
                for fr in pg.frames:
                    try:
                        if fr.locator("#username").count():
                            fr.locator("#username").click()
                            fr.locator("#username").press_sequentially(
                                "solverest" + str(int(time.time()))[-5:], delay=35)
                            ok = True
                            break
                    except Exception:
                        continue
                if ok:
                    break
                time.sleep(2)
            pw = pg.locator("#password").first
            pw.click()
            pw.press_sequentially("T3st.Probe99a1", delay=25)
            pc = pg.locator("#password-confirm").first
            pc.click()
            pc.press_sequentially("T3st.Probe99a1", delay=25)
            pg.locator("button[type=submit]").first.click(timeout=5000)
            for i in range(30):
                time.sleep(1)
                if widget_frame(pg, tries=1):
                    break
            time.sleep(2)
            hasil = solve(pg)
            print("HASIL SOLVE:", hasil)
            pg.screenshot(path=HERE + "/analysis/captcha-probe/selftest-final.png")
            time.sleep(2)
            b.close()
    else:
        print("pakai: captcha_slider.py --selftest")
