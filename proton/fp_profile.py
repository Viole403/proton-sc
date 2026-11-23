#!/usr/bin/env python3
"""fp_profile.py v3 — persona "laptop benar-benar baru" per-run.

v2 menutup 20 sumbu JS; v3 menutup celah hasil bedah-laptop-baru.md:
  P0: native-code masking (toString patch tak lagi menampilkan source),
      branding host-object (PluginArray/MediaQueryList/MimeType + name/length),
      bug invertedColors (:none ikut dijawab), devicePixelRatio, geometri
      jendela (outer*/screenX/Y/screenFrame), GREASE sec-ch-ua per-milé,
      WebRTC disable_non_proxied_udp (bocor IP asli)
  P1: tabel parameter WebGL penuh + daftar ekstensi + shader precision
      (sebelumnya tanda tangan llvmpipe bocor), fontconfig PER-PERSONA
      (set font terpasang = kunci klaster "mesin sama"; ala Camoufox),
      jitter getBoundingClientRect (fontPreferences), storage.estimate.
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
import copy
import json
import os
from pathlib import Path
import random
import re
import secrets
import subprocess
import time

_BASE_ARGS = ["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled",
              "--ozone-platform=x11",
              "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
              # [fix kebocoran 2026-10-14] lalu lintas internal Chrome dimatikan
              # TOTAL (flag terbukti berbulan-bulan di browser gate): tanpa ini,
              # safebrowsing/gvt1/variations mengetuk host Google keluar DIRECT
              # dari IP VPS (dulu lolas bypass) = kebocoran anti-spoofing.
              # 0 request = 0 bocor = 0 byte. Bukti bedah: 40 attempt menunjukkan
              # halaman Proton TIDAK PERNAH meminta host-host itu (murni internal).
              "--disable-background-networking", "--disable-component-update",
              "--disable-component-extensions-with-background-pages",
              "--disable-extensions", "--disable-sync", "--disable-quic",
              "--disable-domain-reliability",
              "--disable-features=DnsOverHttps,MediaRouter",
              "--variations-server-url=", "--variations-disable-limited-entropy",
              "--password-store=basic", "--metrics-recording-only"]

# GREASE brand (WICG: bentuk BERUBAH per rilis — jangan hardcode satu bentuk)
_GREASE = ['"Not;A=Brand";v="8"', '"Not_A Brand";v="24"', '"Not:A;Brand";v="99"',
           '"Not A;Brand";v="16"', '"Not)A;Brand";v="32"']

_GL_EXT_COMMON = [
    "ANGLE_instanced_arrays", "EXT_blend_minmax", "EXT_color_buffer_half_float",
    "EXT_float_blend", "EXT_frag_depth", "EXT_shader_texture_lod",
    "EXT_texture_compression_rgtc", "EXT_texture_filter_anisotropic",
    "OES_element_index_uint", "OES_fbo_render_mipmap", "OES_standard_derivatives",
    "OES_texture_float", "OES_texture_float_linear", "OES_texture_half_float",
    "OES_texture_half_float_linear", "OES_vertex_array_object",
    "WEBGL_color_buffer_float", "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb", "WEBGL_debug_renderer_info",
    "WEBGL_depth_texture", "WEBGL_lose_context", "WEBGL_multi_draw",
]
_GL_PARAMS = {
    "nvidia": {"MAX_TEXTURE_SIZE": 16384, "MAX_RENDERBUFFER_SIZE": 16384,
               "MAX_VIEWPORT_DIMS": [32767, 32767], "MAX_VARYING_VECTORS": 30,
               "MAX_VERTEX_UNIFORM_VECTORS": 4096, "MAX_FRAGMENT_UNIFORM_VECTORS": 1024,
               "ALIASED_POINT_SIZE_RANGE": [1, 2048], "ALIASED_LINE_WIDTH_RANGE": [1, 1]},
    "amd": {"MAX_TEXTURE_SIZE": 16384, "MAX_RENDERBUFFER_SIZE": 16384,
            "MAX_VIEWPORT_DIMS": [16384, 16384], "MAX_VARYING_VECTORS": 30,
            "MAX_VERTEX_UNIFORM_VECTORS": 4096, "MAX_FRAGMENT_UNIFORM_VECTORS": 1024,
            "ALIASED_POINT_SIZE_RANGE": [1, 2047], "ALIASED_LINE_WIDTH_RANGE": [1, 1]},
    "intel": {"MAX_TEXTURE_SIZE": 16384, "MAX_RENDERBUFFER_SIZE": 16384,
              "MAX_VIEWPORT_DIMS": [32767, 32767], "MAX_VARYING_VECTORS": 15,
              "MAX_VERTEX_UNIFORM_VECTORS": 256, "MAX_FRAGMENT_UNIFORM_VECTORS": 221,
              "ALIASED_POINT_SIZE_RANGE": [1, 255], "ALIASED_LINE_WIDTH_RANGE": [1, 1]},
    "apple": {"MAX_TEXTURE_SIZE": 16384, "MAX_RENDERBUFFER_SIZE": 16384,
              "MAX_VIEWPORT_DIMS": [16384, 16384], "MAX_VARYING_VECTORS": 31,
              "MAX_VERTEX_UNIFORM_VECTORS": 4096, "MAX_FRAGMENT_UNIFORM_VECTORS": 1024,
              "ALIASED_POINT_SIZE_RANGE": [1, 511], "ALIASED_LINE_WIDTH_RANGE": [1, 1]},
}

_MIME_PDF = [["application/pdf", "pdf", "Portable Document Format"],
             ["text/pdf", "pdf", "Portable Document Format"]]

# nama font yang lazim dicek kolektor: [nama_probe, font_asli_penyedia]
_REAL_FONTS = ["Arial", "Arial Black", "Comic Sans MS", "Courier New", "Georgia",
               "Impact", "Times New Roman", "Trebuchet MS", "Verdana", "Andale Mono",
               "Carlito", "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
               "FreeSans", "FreeSerif", "FreeMono", "Ubuntu", "Webdings"]
_FAKABLE = ["Segoe UI", "Segoe UI Symbol", "Tahoma", "Palatino", "Palatino Linotype",
            "Book Antiqua", "Bookman Old Style", "Garamond", "Lucida Console",
            "Lucida Sans Unicode", "Franklin Gothic Medium", "Gill Sans", "Futura",
            "Optima", "Cambria", "Calibri", "Candara", "Consolas", "Constantia",
            "Corbel", "Helvetica", "Helvetica Neue", "Monaco", "Menlo", "Geneva",
            "Osaka", "MS Gothic", "SimSun", "SimHei", "Microsoft YaHei", "AppleGothic",
            "Copperplate Gothic Light", "Brush Script MT", "Papyrus"]

_PERSONAS = [
    {"os": "win",
     "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full} Safari/537.36",
     "platform": "Win32", "ua_platform": "Windows", "ua_platform_version": "10.0.0",
     "arch": "x86", "bitness": "64", "taskbar": [40, 50], "sb_w": [15, 17],
     "chrome_h": [86, 94, 103, 110], "dpr": [1.0, 1.25, 1.5], "touch_choices": [0, 0, 0, 0],  # [fix 2026-11-08] touch=10 dihapus: wire maxTouchPoints:10 + touchEvent:false (fp_forge mati) = combo mustahil -> hasLiedOs:true (2/2 run mati vs emas false; BEDAH-SKOR-RISKCONTROL)
     "langs": [["en-US"], ["en-GB"], ["de-DE"]],
     "gpus": [["Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER/PCIe/SSE2, OpenGL 4.5.0)", "nvidia"],
              ["Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060/PCIe/SSE2, OpenGL 4.5.0)", "nvidia"],
              ["Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Series (POLARIS10, DRM 3.41.0, LLVM 15.0.7), OpenGL 4.6)", "amd"],
              ["Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.5.0)", "intel"]],
     "viewports": [[1920, 1080], [1366, 768], [1536, 864], [2560, 1440]],
     "cores": [4, 8, 12, 16], "depths": [24, 30],
     "gamuts": ["srgb", "srgb", "p3"],
     "plugins": [[["PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chrome PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chromium PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Microsoft Edge PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["WebKit built-in PDF", "Portable Document Format", _MIME_PDF]]],
     "font_real": ["Arial", "Arial Black", "Comic Sans MS", "Courier New", "Georgia",
                   "Impact", "Times New Roman", "Trebuchet MS", "Verdana", "Carlito"],
     "font_fake": ["Segoe UI", "Tahoma", "Palatino Linotype", "Book Antiqua", "Garamond",
                   "Lucida Console", "Lucida Sans Unicode", "Franklin Gothic Medium",
                   "Cambria", "Calibri", "Candara", "Consolas", "Constantia", "Corbel",
                   "Segoe UI Symbol", "Microsoft YaHei", "Copperplate Gothic Light",
                   "Brush Script MT", "Bookman Old Style"]},
    {"os": "mac",
     "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full} Safari/537.36",
     "platform": "MacIntel", "ua_platform": "macOS", "ua_platform_version": "14.5.0",
     "arch": "x86", "bitness": "64", "taskbar": [20, 30], "sb_w": [0, 15],
     "chrome_h": [0, 68, 76], "dpr": [2.0, 2.0, 1.0], "touch_choices": [0],
     "langs": [["en-US"], ["en-GB"]],
     "gpus": [["Apple", "Apple M1", "apple"], ["Apple", "Apple M2", "apple"],
              ["Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon Pro 5500M OpenGL Engine)", "amd"]],
     "viewports": [[1440, 900], [1680, 1050], [1728, 1117], [2560, 1600]],
     "cores": [8, 10, 12], "depths": [30],
     "gamuts": ["p3"],
     "plugins": [[["PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chrome PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chromium PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["WebKit built-in PDF", "Portable Document Format", _MIME_PDF]]],
     "font_real": ["Arial", "Courier New", "Georgia", "Times New Roman", "Verdana",
                   "FreeSans", "FreeSerif", "FreeMono"],
     "font_fake": ["Helvetica", "Helvetica Neue", "Monaco", "Menlo", "Geneva", "Osaka",
                   "Futura", "Optima", "Gill Sans", "Papyrus", "AppleGothic", "Candara"]},
    {"os": "linux",
     "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full} Safari/537.36",
     "platform": "Linux x86_64", "ua_platform": "Linux", "ua_platform_version": "6.6.0",
     "arch": "x86", "bitness": "64", "taskbar": [30], "sb_w": [15],
     "chrome_h": [86, 94], "dpr": [1.0], "touch_choices": [0],
     "langs": [["en-US"], ["en-GB"], ["de-DE"]],
     "gpus": [["Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon Graphics (radeonsi renoir ACO), OpenGL ES 3.2)", "amd"],
              ["Google Inc. (Intel)", "ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)", "intel"],
              ["Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1050 Ti/PCIe/SSE2, OpenGL 4.5.0)", "nvidia"]],
     "viewports": [[1920, 1080], [1366, 768], [1600, 900]],
     "cores": [4, 8, 12], "depths": [24],
     "gamuts": ["srgb"],
     "plugins": [[["PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chrome PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chromium PDF Viewer", "Portable Document Format", _MIME_PDF]]],
     "font_real": ["DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono", "Ubuntu",
                   "FreeSans", "FreeSerif", "FreeMono", "Arial"],
     "font_fake": ["Liberation Sans", "Helvetica", "Courier", "Garamond"]},
    # [fix hv=None 2026-10-14] ruang persona DIPERLUAS: 3 persona saja =
    # tabrakan kombinasi (GPU+core+vp+major) antar attempt berdekatan ->
    # "sidik jari laptop terdeteksi SATU mesin yang sama lintas attempt"
    # (AGENTS.md #9) -> menu verifikasi hilang (hv=None) -> slot mati.
    # Tambahan MURNI ADDITIF (entri lama tak disentuh).
    {"os": "win",
     "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full} Safari/537.36",
     "platform": "Win32", "ua_platform": "Windows", "ua_platform_version": "10.0.0",
     "arch": "x86", "bitness": "64", "taskbar": [48, 57], "sb_w": [15, 17, 19],
     "chrome_h": [86, 94, 110, 118], "dpr": [1.25, 1.5, 2.0], "touch_choices": [0, 0, 0],  # [fix 2026-11-08] lihat catatan touch di atas — hasLiedOs:true harus 0
     "langs": [["en-US"], ["en-GB"], ["de-DE"], ["fr-FR"]],
     "gpus": [["Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Laptop GPU/PCIe/SSE2, OpenGL 4.5.0)", "nvidia"],
              ["Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (TGL GT2), OpenGL 4.5.0)", "intel"],
              ["Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon(TM) Graphics (renoir LLVM 15.0.7), OpenGL 4.6)", "amd"]],
     "viewports": [[1920, 1200], [2560, 1440], [1366, 768], [1600, 900]],
     "cores": [6, 8, 16], "depths": [24, 30],
     "gamuts": ["srgb", "p3"],
     "plugins": [[["PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chrome PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chromium PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Microsoft Edge PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["WebKit built-in PDF", "Portable Document Format", _MIME_PDF]]],
     "font_real": ["Arial", "Calibri", "Cambria", "Segoe UI", "Consolas",
                   "Times New Roman", "Verdana", "Tahoma"],
     "font_fake": ["Century Gothic", "Franklin Gothic Book", "Gill Sans MT",
                   "Lucida Bright", "MS Gothic", "Palatino Linotype", "Rockwell",
                   "Sylfaen", "Tw Cen MT", "Yu Gothic"]},
    {"os": "linux",
     "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full} Safari/537.36",
     "platform": "Linux x86_64", "ua_platform": "Linux", "ua_platform_version": "6.1.0",
     "arch": "x86", "bitness": "64", "taskbar": [30, 36], "sb_w": [15, 17],
     "chrome_h": [86, 103], "dpr": [1.0, 1.25], "touch_choices": [0],
     "langs": [["en-US"], ["en-GB"], ["de-DE"]],
     "gpus": [["Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070/PCIe/SSE2, OpenGL 4.5.0)", "nvidia"],
              ["Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon 680M (rembrandt LLVM 15.0.7), OpenGL 4.6)", "amd"],
              ["Google Inc. (Intel)", "ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2), OpenGL 4.6)", "intel"]],
     "viewports": [[1280, 720], [1600, 900], [1920, 1080]],
     "cores": [2, 4, 8, 16], "depths": [24],
     "gamuts": ["srgb"],
     "plugins": [[["PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chrome PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chromium PDF Viewer", "Portable Document Format", _MIME_PDF]]],
     "font_real": ["DejaVu Sans", "Ubuntu", "Liberation Sans", "FreeSans",
                   "Noto Sans", "Cantarell"],
     "font_fake": ["Noto Serif", "Ubuntu Mono", "Liberation Serif", "Padauk",
                   "Lato", "Open Sans"]},
    {"os": "mac",
     "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full} Safari/537.36",
     "platform": "MacIntel", "ua_platform": "macOS", "ua_platform_version": "14.6.0",
     "arch": "arm", "bitness": "64", "taskbar": [20, 25, 37], "sb_w": [0, 15],
     "chrome_h": [0, 76, 83], "dpr": [2.0, 2.0, 1.0], "touch_choices": [0],
     "langs": [["en-US"], ["en-GB"], ["de-DE"]],
     "gpus": [["Apple", "Apple M3", "apple"], ["Apple", "Apple M2 Pro", "apple"],
              ["Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon Pro 5300M OpenGL Engine)", "amd"]],
     "viewports": [[1512, 982], [1470, 956], [1728, 1117], [1440, 900]],
     "cores": [8, 11, 12], "depths": [30],
     "gamuts": ["p3"],
     "plugins": [[["PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chrome PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["Chromium PDF Viewer", "Portable Document Format", _MIME_PDF],
                  ["WebKit built-in PDF", "Portable Document Format", _MIME_PDF]]],
     "font_real": ["Arial", "Helvetica", "Times", "Verdana", "Courier New", "Georgia"],
     "font_fake": ["Avenir", "Baskerville", "Didot", "Futura", "Hoefler Text",
                   "Optima", "Zapfino", "Savoye LET", "American Typewriter"]},
]

_MAJORS = ["156", "157", "157", "157", "158", "158"]


def build_profile(seed=None):
    """Satu persona device kohesif acak (laptop berbeda tiap run)."""
    rng = random.Random(seed) if seed is not None else random
    p = copy.deepcopy(rng.choice(_PERSONAS))
    maj = rng.choice(_MAJORS)
    full = f"{maj}.0.{rng.choice([7912, 7945, 8103])}.{rng.randrange(5, 95)}"
    languages = list(rng.choice(p["langs"]))
    vw, vh = rng.choice(p["viewports"])
    # [fix koherensi 2026-10-23] platform di blob = Linux ASLI (spoof platform
    # mati sejak 22 Okt) -> GPU WAJIB Linux-plausible: 'Apple M1/M2' atau
    # 'Radeon Pro 5300M OpenGL Engine' (nomenklatur Mac) = MUSTAHIL di Linux
    # (bukti validasi: maronsei664 = 'Apple M2 Pro' + platform Linux).
    # Ambil hanya renderer bergaya driver Linux (Mesa/PCIe/LLVM/radeonsi/DRM);
    # bila template tak punya, pinjam dari kumpulan lintas template.
    _KATA_GPU_LINUX = ("Mesa", "PCIe", "LLVM", "radeonsi", "DRM ")
    kandidat_gpu = [g for g in p["gpus"] if any(s in g[1] for s in _KATA_GPU_LINUX)]
    if not kandidat_gpu:
        kandidat_gpu = [g for p2 in _PERSONAS for g in p2["gpus"]
                        if any(s in g[1] for s in _KATA_GPU_LINUX)]
    glv, glr, glclass = rng.choice(kandidat_gpu)
    # [fix 2026-10-23 koherensi persona — bedah menu ['sms','email'] tibus93376]
    # cores/mem dulu DITARIK BEBAS ("cores": rng.choice(p["cores]),
    # "mem": rng.choice([2,4,8])) -> kombinasi MUSTAHIL: 2 core + Radeon 680M
    # (APU Ryzen-6000 selalu >=6 core) & 12 core + RAM 2GB. Blob korban menu
    # sms = kombinasi terburuk tsb; aturan user #1: persona WAJIB koheren
    # spt laptop asli. Kini dipasangkan per kelas GPU. deviceMemory Chrome
    # DIBATASI maks 8 (8 = ">8GB") — jangan pernah nilai >8.
    if glclass == "apple":
        cores = rng.choice([8, 10, 12])          # M1=8, M2=8/10, M2 Pro=10/12, M3=8/10/12
        mem = 8                                   # Apple-silicon Mac >=8GB
    elif glclass in ("nvidia", "amd"):
        # hardwareConcurrency = THREAD (bukan core fisik): APU Ryzen mobile
        # minimal 4c/8t (renoir/rembrandt) -> 4 thread + APU = mustahil
        # (temuan augus9914). Diskrit boleh 4t (CPU lama) s/d 16t.
        apu = any(s in glr for s in ("renoir", "rembrandt", "radeonsi",
                                     "Radeon(TM) Graphics", "Radeon Graphics"))
        cores = rng.choice([8, 12, 16]) if apu else rng.choice([4, 8, 12, 16])
        mem = 8                                   # mesin game/kreator >=8GB
    else:                                         # intel integrated (UHD/Iris)
        cores = rng.choice([2, 4, 8, 12])
        # 2GB hanya utk mesin 2-core (Celeron tua) — 8 core + 2GB = mustahil
        # [RE-PROTON-2028 2026-11-08] hanya 8/16: mem 2/4 = 0/26 lulus (nilai
        # cap buatan, laptop 2GB nyaris mustahil 2026); blob emas = 16 (RAM asli).
        mem = rng.choice([8, 8, 16])
    # set font: subset nyata + subset palsu, dipotong acak utk variasi antar persona
    f_real = [f for f in p["font_real"] if rng.random() < 0.85]
    f_fake = [f for f in p["font_fake"] if rng.random() < 0.55]
    brand_grease = _GREASE[int(maj) % len(_GREASE)]
    brands = [{"brand": "Not;A=Brand", "version": "8"},
              {"brand": "Chromium", "version": maj},
              {"brand": "Google Chrome", "version": maj}]
    return {
        "seed": seed or secrets.token_hex(8),
        # [gate 2026-10-13] noise render canvas/WebGL: default MATI dulu — semua
        # attempt SETELAH noise masuk (c788310) kena 2028 'abusive', padahal pagi
        # ini 10/16 sukses tanpa noise. NOISE_RENDER=1 utk mengaktifkan kembali.
        "noise_render": os.environ.get("NOISE_RENDER", "0") == "1",
        "os": p["os"], "platform": p["platform"],
        "ua": p["ua"].format(full=f"{maj}.0.0.0"),
        "ua_full": full, "ua_platform": p["ua_platform"],
        "ua_platform_version": p["ua_platform_version"],
        "arch": p["arch"], "bitness": p["bitness"],
        "grease": brand_grease,
        "brands": brands,
        "brands_full": [{"brand": "Not;A=Brand", "version": "24.0.0.0"},
                        {"brand": "Chromium", "version": full},
                        {"brand": "Google Chrome", "version": full}],
        "languages": languages, "locale": languages[0],
        "cores": cores, "mem": mem,
        "viewport": {"width": vw, "height": vh},
        "screen_w": vw, "screen_h": vh,
        "taskbar": rng.choice(p["taskbar"]), "sb_w": rng.choice(p["sb_w"]),
        "chrome_h": rng.choice(p["chrome_h"]), "dpr": rng.choice(p["dpr"]),
        "colorDepth": rng.choice(p["depths"]), "touch": rng.choice(p["touch_choices"]),
        "plugins": copy.deepcopy(rng.choice(p["plugins"])),  # jangan lupa utk init JS!
        "gl_vendor": glv, "gl_renderer": glr,
        "gl_params": copy.deepcopy(_GL_PARAMS[glclass]),
        "gl_exts": list(_GL_EXT_COMMON),
        "gl_sl": ("WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)"
                  if glclass != "apple" else "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0)"),
        "media": {"gamut": rng.choice(p["gamuts"]),
                  "hdr": rng.random() < 0.3, "monochrome": 0,
                  "contrast": rng.choice(["no-preference", "no-preference", "high"]),
                  "reducedMotion": rng.random() < 0.2,
                  "forcedColors": "none", "invertedColors": "none"},
        "fonts_real": sorted(set(f_real)),
        "fonts_fake": sorted(set(f_fake)),
        "fontSeed": rng.randrange(1000, 99999),
        # [fix 2026-10-13] default OFF: mutasi blob fp_forge (ideal(): stream
        # sintetis + randomisasi fonts/visitorId) = PEMICU 2028 'abusive'.
        # Bukti A/B 1-session-perah konfigurasi identik: forge=2028 vs
        # off=users 200 (4/4 attempt sukses, tier ['captcha','email'] = manual).
        # Persona+hook+solver TETAP jalan di mode off. FORGE_MODE=forge hanya
        # utk eksperimen — jangan kembalikan default tanpa uji A/B baru.
        "forge": {"mode": os.environ.get("FORGE_MODE", "off"),
                  "vid": secrets.token_hex(16)},  # satu visitorId per attempt, lintas frame
    }


_INIT_JS_A = """(() => {
  const P = __PROFILE_JSON__;
  // ===== infra native-code masking (P0): toString patch tak lagi bocor source =====
  const SRC = new WeakMap();
  const _toStr = Function.prototype.toString;
  Function.prototype.toString = function () {
    if (SRC.has(this)) return SRC.get(this);
    return _toStr.call(this);
  };
  SRC.set(Function.prototype.toString, 'function toString() { [native code] }');
  const maskFn = (fn, s) => { try { SRC.set(fn, s); } catch (e) {} return fn; };
  const maskGetter = (get, prop) => maskFn(get, 'function get ' + prop + '() { [native code] }');
  const setNL = (fn, name, len) => {
    try { Object.defineProperty(fn, 'name', {value: name, configurable: true}); } catch (e) {}
    try { Object.defineProperty(fn, 'length', {value: len == null ? 0 : len, configurable: true}); } catch (e) {}
    return fn;
  };
  const so = (o, k, v) => {
    try { Object.defineProperty(o, k, {value: v, writable: true, enumerable: true, configurable: true}); }
    catch (e) { try { o[k] = v; } catch (e2) {} }
  };
  const brand = (obj, tag) => {
    try { Object.defineProperty(obj, Symbol.toStringTag, {value: tag, configurable: true}); } catch (e) {}
    return obj;
  };
  // objek dgn prototype host-object ASLI -> instanceof & [object X] klop
  const ofProto = (ctorName, props, tag) => {
    let o;
    try { o = Object.create(window[ctorName].prototype); } catch (e) { o = {}; }
    for (const k in props) {
      try {
        Object.defineProperty(o, k, {value: props[k], writable: true,
                                     enumerable: true, configurable: true});
      } catch (e) { try { o[k] = props[k]; } catch (e2) {} }
    }
    if (tag) brand(o, tag);
    return o;
  };
  const def = (obj, prop, val) => {
    const get = setNL(maskGetter(function () { return val; }, prop), 'get ' + prop, 0);
    try { Object.defineProperty(obj, prop, {get: get, configurable: true}); } catch (e) {}
  };
  // ===== identitas dasar =====
  def(Navigator.prototype, 'webdriver', false);  // Chrome asli: false
  // [fix 2026-10-22 bedah bot-leak] platform/language/languages TIDAK lagi
  // dipalsukan: dulu berpasangan dgn spoof UA (apply_ua_cdp) — setelah spoof
  // dimatikan (TLS/Accept asli = Linux 155), klaim 'MacIntel' + language palsu
  // justru kontradiksi dgn UA asli & Accept-Language. Native = konsisten;
  // variasi persona datang dari LANG env per-attempt (Accept-Language asli
  // Chrome 4-entri) + layar/dpr/font.
  // def(Navigator.prototype, 'platform', P.platform);
  // def(Navigator.prototype, 'language', P.languages[0]);
  // def(Navigator.prototype, 'languages', Object.freeze(P.languages.slice()));
  def(Navigator.prototype, 'maxTouchPoints', P.touch);
  def(Navigator.prototype, 'hardwareConcurrency', P.cores);
  def(Navigator.prototype, 'deviceMemory', P.mem);
  // ===== dPR + geometri jendela (P0) =====
  def(window, 'devicePixelRatio', P.dpr);
  const gx = P.fontSeed % 180, gy = (P.fontSeed * 7) % 90;
  def(window, 'outerWidth', P.viewport.width + P.sb_w);
  def(window, 'outerHeight', P.viewport.height + P.chrome_h);
  def(window, 'screenX', gx); def(window, 'screenY', gy);
  def(window, 'screenLeft', gx); def(window, 'screenTop', gy);
  // ===== screen + screenFrame (availLeft..Bottom) =====
  try {
    const s = Screen.prototype;
    def(s, 'colorDepth', P.colorDepth);
    def(s, 'pixelDepth', P.colorDepth);
    def(s, 'width', P.screen_w); def(s, 'height', P.screen_h);
    def(s, 'availWidth', P.screen_w);
    def(s, 'availHeight', P.screen_h - P.taskbar);
    def(s, 'availLeft', 0); def(s, 'availTop', 0);
    // [fix tamper-T7 2026-11-06 audit] availRight/availBottom DIHAPUS —
    // Chrome asli (CSSOM View) TIDAK punya dua properti itu; menambahkannya
    // ke Screen = jejak tamper (getOwnPropertyNames(Screen.prototype) bocor).
  } catch (e) {}
  // ===== plugins + mimeTypes (branding host-object + native item/namedItem) =====
  try {
    const mimeMap = {};
    const plugs = [];
    P.plugins.forEach((pl) => {
      const name = pl[0], desc = pl[1], mimeDefs = pl[2];
      const ms = mimeDefs.map((m) => ofProto('MimeType', {
        type: m[0], suffixes: m[1], description: m[2], enabledPlugin: null}, 'MimeType'));
      const pg = ofProto('Plugin', {
        name: name, description: desc,
        filename: 'internal-' + name.toLowerCase().replace(/\\W+/g, '-'),
        length: ms.length}, 'Plugin');
      ms.forEach((m, i) => { so(m, 'enabledPlugin', pg); so(pg, i, m); });
      setNL(maskFn(function (i) { return this[i] || null; }, 'item'), 'item', 1);
      pg.item = maskFn(function (i) { return this[i] || null; }, 'function item() { [native code] }');
      pg.namedItem = maskFn(function (n) { return ms.find((x) => x.type === n) || null; }, 'function namedItem() { [native code] }');
      setNL(pg.item, 'item', 1); setNL(pg.namedItem, 'namedItem', 1);
      ms.forEach((m) => { mimeMap[m.type] = m; });
      plugs.push(pg);
    });
    const pa = ofProto('PluginArray', {}, 'PluginArray');
    plugs.forEach((p2, i) => { so(pa, i, p2); });
    so(pa, 'length', plugs.length);
    pa.item = maskFn(function (i) { return this[i] || null; }, 'function item() { [native code] }');
    pa.namedItem = maskFn(function (n) { return plugs.find((p2) => p2.name === n) || null; }, 'function namedItem() { [native code] }');
    setNL(pa.item, 'item', 1); setNL(pa.namedItem, 'namedItem', 1);
    const buatIter = (obj) => maskFn(function () {
      let i = 0;
      return {next() { return i < obj.length ? {value: obj[i++], done: false}
                                            : {value: undefined, done: true}; },
              [Symbol.iterator]() { return this; }};
    }, 'function values() { [native code] }');
    for (const m of ['forEach', 'entries', 'keys', 'values', 'find']) {
      try { Object.defineProperty(pa, m, {value: Array.prototype[m], configurable: true}); }
      catch (e) {}
    }
    try { Object.defineProperty(pa, Symbol.iterator, {value: buatIter(pa), configurable: true}); } catch (e) {}
    // iterator objek-biasa: native Array/PluginArray iterator menolak objek host-palsu
    // [fix 2026-10-22 bedah bot-leak] instalasi plugins/mimeTypes DIMATIKAN:
    // (1) Chrome asli SUDAH punya 5 plugins (probe) — patch ini cuma risiko;
    // (2) properti hasil 'so()' = own data property, asli = prototype accessor
    //     (satu baris deteksi: Object.getOwnPropertyDescriptor(navigator
    //     .plugins[0],'name') harus undefined). Native dibiarkan utuh.
    // def(Navigator.prototype, 'plugins', pa);
    const ma = ofProto('MimeTypeArray', {}, 'MimeTypeArray');
    const keys = Object.keys(mimeMap);
    keys.forEach((k, i) => { so(ma, i, mimeMap[k]); });
    so(ma, 'length', keys.length);
    ma.item = maskFn(function (i) { return this[i] || null; }, 'function item() { [native code] }');
    ma.namedItem = maskFn(function (n) { return mimeMap[n] || null; }, 'function namedItem() { [native code] }');
    setNL(ma.item, 'item', 1); setNL(ma.namedItem, 'namedItem', 1);
    for (const m of ['forEach', 'entries', 'keys', 'values', 'find']) {
      try { Object.defineProperty(ma, m, {value: Array.prototype[m], configurable: true}); }
      catch (e) {}
    }
    try { Object.defineProperty(ma, Symbol.iterator, {value: buatIter(ma), configurable: true}); } catch (e) {}
    // [fix 2026-10-22] lihat catatan plugins di atas — native dibiarkan utuh.
    // def(Navigator.prototype, 'mimeTypes', ma);
  } catch (e) {}
"""

_INIT_JS_B = """  // ===== matchMedia v4 [fix tamper-T3/T4 2026-11-06 — audit fingerprint] =====
  // Objek MQL kini NATIVE (mm asli) — identitas/keys/proto/toString klop asli
  // (v3 = objek tiruan: 8 own-prop vs asli 0; matches = own data prop = tell).
  // Jawaban persona via PATCH GETTER MediaQueryList.prototype.matches yg
  // membaca this.media; kueri GABUNGAN ('a and b') dijawab konsisten = AND
  // tiap bagian (bagian tak dikenal -> sub-query native) — dulu kueri gabungan
  // jatuh utuh ke native = kontradiksi dgn jawaban kueri tunggal (T3).
  // Varian -webkit-min-device-pixel-ratio ikut dijawab (koheren dgn P.dpr).
  // inverted-colors TETAP tak dijawab (native; wire-fix 'invertedColors'
  // dihapus dari wire — jangan dikembalikan).
  try {
    const mm = window.matchMedia;
    const normQ = (q) => String(q).trim().toLowerCase()
      .replace(/\\s*\\(\\s*/g, '(').replace(/\\s*\\)\\s*/g, ')')
      .replace(/\\s*:\\s*/g, ': ').replace(/\\s*,\\s*/g, ', ');
    const F = P.media;
    // jawab SATU fitur; null = tak dikenal -> biarkan sub-query native
    const jwb1 = (s) => {
      let m;
      if ((m = s.match(/^\\(?color-gamut:(\\w+)\\)?$/))) {
        const ord = {srgb: 0, p3: 1, rec2020: 2};
        return ord[F.gamut] >= (m[1] in ord ? ord[m[1]] : 99);
      }
      if ((m = s.match(/^\\(?dynamic-range:(\\w+)\\)?$/)))
        return m[1] === 'high' ? !!F.hdr : true;
      if ((m = s.match(/^\\(?prefers-reduced-motion:(\\w+)\\)?$/)))
        return m[1] === 'reduce' ? !!F.reducedMotion
             : (m[1] === 'no-preference' ? !F.reducedMotion : false);
      if ((m = s.match(/^\\(?prefers-contrast:(\\w+)\\)?$/))) {
        const map = {high: 'high', more: 'high', low: 'low', less: 'low',
                     'no-preference': 'no-preference', custom: 'custom'};
        return map[m[1]] === F.contrast;
      }
      if (s.match(/^\\(?inverted-colors:(\\w+)\\)?$/)) return null;
      if ((m = s.match(/^\\(?forced-colors:(\\w+)\\)?$/)))
        return (m[1] === 'active') === (F.forcedColors === 'active');
      if ((m = s.match(/^\\(?max-monochrome:(\\d+)\\)?$/)))
        return F.monochrome <= +m[1];
      if ((m = s.match(/^\\(?(?:min-)?monochrome:(\\d+)\\)?$/)))
        return F.monochrome >= +m[1];
      if (s === '(monochrome)') return F.monochrome > 0;
      if ((m = s.match(/^\\(?(?:-webkit-)?(min-|max-)?resolution:([\\d.]+)dppx\\)?$/))) {
        const op = m[1] || '', v = +m[2];
        return op === 'min-' ? P.dpr >= v - 1e-9
             : op === 'max-' ? P.dpr <= v + 1e-9
             : Math.abs(P.dpr - v) < 1e-9;
      }
      if ((m = s.match(/^\\(?(?:-webkit-)?(min-|max-)?device-pixel-ratio:?([\\d.]+)\\)?$/))) {
        const op = m[1] || '', v = +m[2];
        return op === 'min-' ? P.dpr >= v - 1e-9
             : op === 'max-' ? P.dpr <= v + 1e-9
             : Math.abs(P.dpr - v) < 1e-9;
      }
      if (s === '(resolution)' || s === '(device-pixel-ratio)')
        return P.dpr > 0;
      return null;
    };
    const _proto = window.MediaQueryList && window.MediaQueryList.prototype;
    const _desc = _proto ? Object.getOwnPropertyDescriptor(_proto, 'matches') : null;
    if (_proto && _desc && _desc.get) {
      const _ng = _desc.get;
      Object.defineProperty(_proto, 'matches', {
        configurable: true, enumerable: _desc.enumerable,
        get: function () {
          try {
            const parts = String(this.media).split(/\\s+and\\s+/);
            let hasil = true;
            for (const p of parts) {
              const j = jwb1(p.replace(/\\s/g, ''));
              if (j === null) {
                try { hasil = hasil && mm(p).matches; }
                catch (e) { return _ng.call(this); }
              } else {
                hasil = hasil && j;
              }
            }
            return hasil;
          } catch (e) {}
          return _ng.call(this);
        }
      });
    }
  } catch (e) {}
  // ===== WebGL v3: tabel parameter penuh + ekstensi + shader precision =====
  try {
    const PN = {
      0x1F00: 'VENDOR', 0x1F01: 'RENDERER', 0x1F02: 'VERSION',
      0x8B8C: 'SHADING_LANGUAGE_VERSION', 0x0D33: 'MAX_TEXTURE_SIZE',
      0x0D3A: 'MAX_VIEWPORT_DIMS', 0x84E8: 'MAX_RENDERBUFFER_SIZE',
      0x8DFC: 'MAX_VARYING_VECTORS', 0x8DFB: 'MAX_VERTEX_UNIFORM_VECTORS',
      0x8DFD: 'MAX_FRAGMENT_UNIFORM_VECTORS', 0x8869: 'MAX_VERTEX_ATTRIBS',
      0x8872: 'MAX_TEXTURE_IMAGE_UNITS', 0x8B4D: 'MAX_COMBINED_TEXTURE_IMAGE_UNITS',
      0x851C: 'MAX_CUBE_MAP_TEXTURE_SIZE', 0x8B4C: 'MAX_VERTEX_TEXTURE_IMAGE_UNITS',
      0x846E: 'ALIASED_LINE_WIDTH_RANGE', 0x846D: 'ALIASED_POINT_SIZE_RANGE',
      0x9245: 'UNMASKED_VENDOR_WEBGL', 0x9246: 'UNMASKED_RENDERER_WEBGL',
    };
    const patchGL = (proto) => {
      if (!proto) return;
      const ge = proto.getExtension, gp = proto.getParameter,
            gse = proto.getSupportedExtensions, gsp = proto.getShaderPrecisionFormat;
      proto.getExtension = setNL(maskFn(function (name) {
        if (name === 'WEBGL_debug_shaders') return null;  // sembunyikan: bongkar backend
        if (name === 'WEBGL_debug_renderer_info') {
          return ofProto('WebGLExtension',
            {UNMASKED_VENDOR_WEBGL: 0x9245, UNMASKED_RENDERER_WEBGL: 0x9246}, null);
        }
        return ge.call(this, name);
      }, 'function getExtension() { [native code] }'), 'getExtension', 1);
      proto.getParameter = setNL(maskFn(function (p2) {
        const k = PN[p2];
        if (k === 'UNMASKED_VENDOR_WEBGL') return P.gl_vendor;
        if (k === 'UNMASKED_RENDERER_WEBGL') return P.gl_renderer;
        if (k === 'VENDOR' || k === 'RENDERER') return gp.call(this, p2);
        if (k === 'VERSION') return 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
        if (k === 'SHADING_LANGUAGE_VERSION') return P.gl_sl;
        const g = P.gl_params[k];
        if (g !== undefined) return Array.isArray(g) ? new Int32Array(g) : g;
        return gp.call(this, p2);
      }, 'function getParameter() { [native code] }'), 'getParameter', 1);
      proto.getSupportedExtensions = setNL(maskFn(function () {
        return P.gl_exts.slice();
      }, 'function getSupportedExtensions() { [native code] }'), 'getSupportedExtensions', 0);
      proto.getShaderPrecisionFormat = setNL(maskFn(function () {
        return ofProto('WebGLShaderPrecisionFormat', {
          rangeMin: 127, rangeMax: 127, precision: 23}, 'WebGLShaderPrecisionFormat');
      }, 'function getShaderPrecisionFormat() { [native code] }'), 'getShaderPrecisionFormat', 2);
    };
    // [fix 2026-10-22 bedah bot-leak subagent] patch WebGL BESAR DIMATIKAN:
    // paksaan VERSION 'WebGL 1.0' utk konteks WebGL2, shader precision
    // [127,127,23] utk SEMUA parameter, ekstensi statis == = nilai MUSTAHIL
    // dari browser asli (probe: asli VERSION beda per konteks, precision beda
    // per parameter) — persis jenis sinyal yang dibaca kit ke blob.
    //
    // [fix 2026-10-23 bunuh-diri-sesi] HANYA UNMASKED vendor/renderer yang
    // dipalsukan ulang (2 angka, tanpa nilai mustahil). Alasan bukti blob
    // (log.blob_dump): tanpa spoof, videoCard/webglVendorAndRenderer = 'ANGLE
    // (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)))' = SIGNATURE
    // headless/software-render yang TIDAK mungkin di laptop asli (rekaman emas
    // = GPU sungguhan) — kandidat pemicu anti-abuse (SEMUA akun kita berakhir
    // 10003-suspended / 2028-locked / sesi dicabut). VERSION/precision/
    // ekstensi dibiarkan NATIVE (konsisten).
    try {
      const PN2 = {0x9245: 'UNMASKED_VENDOR_WEBGL', 0x9246: 'UNMASKED_RENDERER_WEBGL'};
      const patchGLRingan = (proto) => {
        if (!proto) return;
        const ge = proto.getExtension, gp = proto.getParameter;
        proto.getExtension = setNL(maskFn(function (name) {
          // [fix tamper-T5 2026-11-06 audit] objek ekstensi = NATIVE apa
          // adanya (dulu ditukar objek tiruan ofProto('WebGLExtension') ->
          // [object Object] vs asli [object WebGLDebugRendererInfo] = tell).
          // Nilai UNMASKED tetap dipalsukan lewat getParameter di bawah.
          return ge.call(this, name);
        }, 'function getExtension() { [native code] }'), 'getExtension', 1);
        proto.getParameter = setNL(maskFn(function (p2) {
          const k = PN2[p2];
          if (k === 'UNMASKED_VENDOR_WEBGL') return P.gl_vendor;
          if (k === 'UNMASKED_RENDERER_WEBGL') return P.gl_renderer;
          return gp.call(this, p2);
        }, 'function getParameter() { [native code] }'), 'getParameter', 1);
      };
      patchGLRingan(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
      patchGLRingan(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
    } catch (e) {}
  } catch (e) {}
  // ===== rect jitter (fontPreferences via getBoundingClientRect) =====
  // [fix 2026-10-22 bedah bot-leak] jitter DIMATIKAN: 137px -> 137.2 = nilai
  // mustahil (elemen berdimensi eksplisit HARUS presisi), dan pecahan 1/10
  // mencemari field fontPreferences blob (asli selalu kelipatan 1/64 px).
  // try {
  //   const grb = Element.prototype.getBoundingClientRect;
  //   Element.prototype.getBoundingClientRect = setNL(maskFn(function () {
  //     const r = grb.call(this);
  //     const j = (v) => v + (((Math.floor(v * 10) + P.fontSeed) % 7) - 3) / 10;
  //     try { return new DOMRect(j(r.x), j(r.y), Math.max(0, j(r.width)), Math.max(0, j(r.height))); }
  //     catch (e) { return r; }
  //   }, 'function getBoundingClientRect() { [native code] }'), 'getBoundingClientRect', 0);
  // } catch (e) {}
  // ===== storage.estimate [fix tamper-T6 2026-11-06 audit] DIBUKA NATIVE =====
  // (dulu patch: usage 123MB di profil BARU = mustahil, quota kelipatan GiB
  // persis, usageDetails hilang, own-prop shadow — tak memberi nilai ke blob,
  // hanya jejak tamper utk skrip pemeriksa. Koheren 'laptop baru' = usage 0.)
  // ===== userAgentData shim (fallback bila CDP metadata belum terpasang) =====
  try {
    if (!navigator.userAgentData) {
      const uad = ofProto('NavigatorUAData', {
        brands: P.brands, mobile: false, platform: P.ua_platform,
        getHighEntropyValues: setNL(maskFn(async () => ({
          brands: P.brands_full, mobile: false, platform: P.ua_platform,
          platformVersion: P.ua_platform_version, architecture: P.arch,
          bitness: P.bitness, model: '', uaFullVersion: P.ua_full,
          fullVersionList: P.brands_full}),
          'function getHighEntropyValues() { [native code] }'), 'getHighEntropyValues', 1),
        toJSON: setNL(maskFn(function () {
          return {brands: P.brands, mobile: false, platform: P.ua_platform};
        }, 'function toJSON() { [native code] }'), 'toJSON', 0),
      }, 'NavigatorUAData');
      def(Navigator.prototype, 'userAgentData', uad);
    }
  } catch (e) {}
})();"""


def init_script(prof):
    base = (_INIT_JS_A + _INIT_JS_B).replace("__PROFILE_JSON__", json.dumps(prof))
    # [fix 2026-10-22 bedah bot-leak] fp_forge.js HANYA disuntik saat mode ON.
    # Saat FORGE_MODE=off hook-nya tetap terpasang (XHR diberi properti __m/__u
    # + send.length=1 palsu) = jejak kasat mata: Object.getOwnPropertyNames(xhr)
    # harus kosong & XMLHttpRequest.prototype.send.length=0 di browser asli.
    if os.environ.get("FORGE_MODE", "off") == "off":
        return base
    forge = (Path(__file__).resolve().parent / "fp_forge.js").read_text()
    return base + "\n" + forge.replace("__PROFILE_JSON__", json.dumps(prof))


# ================= fontconfig per-persona (kunci klaster "fonts") =================
# [fix klaster 2026-10-23] daftar probe font KIT (diambil persis dari JS kit
# di trace emas — array j). Hanya nama di daftar ini yang bisa tampil di field
# 'fonts' blob. Sisanya tak terlihat kit.
_J_KIT = ["sans-serif-thin", "ARNO PRO", "Agency FB", "Arabic Typesetting",
          "Arial Unicode MS", "AvantGarde Bk BT", "BankGothic Md BT", "Batang",
          "Bitstream Vera Sans Mono", "Calibri", "Century", "Century Gothic",
          "Clarendon", "EUROSTILE", "Franklin Gothic", "Futura Bk BT",
          "Futura Md BT", "GOTHAM", "Gill Sans", "HELV", "Haettenschweiler",
          "Helvetica Neue", "Humanst521 BT", "Leelawadee", "Letter Gothic",
          "Levenim MT", "Lucida Bright", "Lucida Sans", "Menlo", "MS Mincho",
          "MS Outlook", "MS Reference Specialty", "MS UI Gothic", "MT Extra",
          "MYRIAD PRO", "Marlett", "Meiryo UI", "Microsoft Uighur", "Minion Pro",
          "Monotype Corsiva", "PMingLiU", "Pristina", "SCRIPTINA",
          "Segoe UI Light", "Serifa", "SimHei", "Small Fonts", "Staccato222 BT",
          "TRAJAN PRO", "Univers CE 55 Medium", "Vrinda", "ZWAdobeF"]


def write_fonts_conf(prof, out_dir=None):
    """FONTCONFIG_FILE per persona — kini berbasis ARKETIPE FONT.

    [fix klaster 2026-10-23 — akar 2028/HV-hilang] Resep ASLI kit (trace emas,
    fungsi fonts): nama font 'terdeteksi' bila render "'nama',base" berbeda
    dari base polos (base=monospace/sans-serif/serif). Uji probe
    (probe_kitfont.py, algoritma kit persis) membuktikan:
      - pemetaan nama -> font NYATA (DejaVu Sans dll) = TERDETEKSI
      - pemetaan nama -> dirinya sendiri (tak terpasang) = TIDAK terdeteksi
    CONF LAMA TERBALIK: mengira pemetaan -> DejaVu Sans = 'absen' -> akibatnya
    5 nama Mac/Win/China [Calibri, Gill Sans, Helvetica Neue, Menlo, SimHei]
    (irisan _FAKABLE x daftar probe kit) SELALU terdeteksi di mesin Linux =
    nilai mustahil + identik antar akun = kunci klaster 'mesin sama'.
    Kini tiap persona punya arketipe font laptop-nyata (dipilih dari seed):
      polos (30%)  : []                     — seperti laptop tanpa font tambahan
      ms   (30%)   : [Calibri]              — ms-corefonts/Carlito (spt emas)
      cjk  (20%)   : [Calibri, PMingLiU]    — PERSIS laptop user (blob emas!)
      kantor (20%) : subset acak 2-4 nama   — kantor/desain ber-font aneh
    -> set 'fonts' bervariasi antar akun & selalu masuk akal."""
    out_dir = out_dir or (Path(__file__).resolve().parent / "cache" / "fc")
    out_dir.mkdir(parents=True, exist_ok=True)
    real = list(prof["fonts_real"]) or ["DejaVu Sans"]
    rng = random.Random(int(str(prof["seed"])[:8], 16))
    roll = rng.randrange(100)
    if roll < 30:
        arketipe = []
    elif roll < 60:
        arketipe = ["Calibri"]
    elif roll < 80:
        arketipe = ["Calibri", "PMingLiU"]
    else:
        # [fix koherensi font 2026-11-05 — RISET-DEEP-10003-LANJUT] kantor DULU
        # mengacak dari satu pool campur-keluarga -> hasil seperti
        # [Gill Sans(mac), Segoe UI Light(win)] = 'font campur-OS' nilai
        # MUSTAHIL (AGENTS.md #12; pola pemicu klaster 23 Okt). Bukti emas
        # B-blob-diff.md: blob Linux sehat = ['Calibri','PMingLiU'] (satu
        # keluarga), blob Win32 = ['Bookman Old Style','Brush Script MT',
        # 'Cambria'] (satu keluarga). Kini SATU keluarga per persona — variasi
        # antar akun tetap ada (anti-klaster), lintas-keluarga dihapus.
        keluarga = rng.choice([
            ["Calibri", "Century Gothic", "Lucida Bright", "Segoe UI Light",
             "Franklin Gothic", "Bookman Old Style", "Brush Script MT", "Cambria"],
            # [RE-PROTON-2028 2026-11-08] keluarga Mac (Gill Sans/Helvetica
            # Neue/Menlo) DIHAPUS utk platform Linux: 0/15 lulus, emas tak
            # pernah menampilkan font Mac.
            ["Calibri", "PMingLiU", "SimHei"],
        ])
        arketipe = sorted(rng.sample(keluarga, rng.randint(2, min(4, len(keluarga)))))
    # nama yang TIDAK di arketipe -> dirinya sendiri (tak terpasang = absen
    # menurut algoritma kit); nama di arketipe -> font nyata (rotasi real[]).
    semua = list(dict.fromkeys(_REAL_FONTS + _FAKABLE + _J_KIT))
    # target HARUS font yang benar-benar terpasang di VPS — fonts_real persona
    # kadang berisi nama tak terpasang (mis. 'Arial') -> pemetaan ke nama hantu
    # = sama dgn absen (temuan probe: ms/cjk kadang tak terdeteksi karenanya).
    target_terpasang = ["DejaVu Sans", "DejaVu Serif", "Nimbus Sans",
                        "Liberation Sans", "DejaVu Sans Mono"]
    aturan = {}
    for n in semua:
        if n in arketipe:
            aturan[n] = target_terpasang[arketipe.index(n) % len(target_terpasang)]
        else:
            aturan[n] = n          # self-map = tak terpasang = TIDAK terdeteksi
    alias_xml = "\n".join(
        f'    <match target="pattern">'
        f'<test name="family" qual="any"><string>{n}</string></test>'
        f'<edit name="family" mode="assign" binding="strong"><string>{t}</string></edit>'
        f'</match>' for n, t in sorted(aturan.items()))
    conf = f'''<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
    <dir>/usr/share/fonts</dir>
    <dir>/usr/local/share/fonts</dir>
    <cachedir prefix="xdg">fontconfig</cachedir>
    <include ignore_missing="yes">/etc/fonts/conf.d</include>
{alias_xml}
</fontconfig>
'''
    p = out_dir / f"fc-{prof['seed']}.conf"
    p.write_text(conf)
    return str(p)


def chrome_args(prof=None):
    """Arg launch koheren dgn persona (warna render sesuai gamut yang diklaim)."""
    args = list(CHROME_ARGS)
    if prof:
        args.append("--force-color-profile=" +
                    ("display-p3" if prof["media"]["gamut"] != "srgb" else "srgb"))
    return args


def _parse_grease(g):
    m = re.match(r'^"(.*)";v="(.*)"$', g)
    return (m.group(1), m.group(2)) if m else ("Not;A=Brand", "8")


def apply_ua_cdp(ctx, page, prof):
    """UA + userAgentData + sec-ch-ua konsisten via CDP metadata; GREASE per-milé.
    [fix 2026-10-22 bedah bot-leak] DIMATIKAN default: binary Chrome = 155
    (TLS/JA4 + header Accept dgn image/jxl = tell 155) sementara spoof kita
    mengklaim 148-151 = kontradiksi versi per-request (kelas deteksi JA4-map).
    Biarkan Chrome mengirim versi ASLI 155 di SEMUA permukaan (UA, sec-ch-ua,
    TLS, Accept) = konsisten. UA_SPOOF=1 utk mengembalikan perilaku lama."""
    if os.environ.get("UA_SPOOF", "0") != "1":
        return False
    try:
        cdp = ctx.new_cdp_session(page)
        gname, gver = _parse_grease(prof["grease"])
        maj = prof["brands"][1]["version"]
        brands = [{"brand": gname, "version": gver},
                  {"brand": "Chromium", "version": maj},
                  {"brand": "Google Chrome", "version": maj}]
        full = prof["ua_full"]
        brands_full = [{"brand": gname, "version": "24.0.0.0"},
                       {"brand": "Chromium", "version": full},
                       {"brand": "Google Chrome", "version": full}]
        cdp.send("Emulation.setUserAgentOverride", {
            "userAgent": prof["ua"],
            "platform": prof["ua_platform"],
            "userAgentMetadata": {
                "brands": brands, "fullVersionList": brands_full,
                "fullVersion": full, "platform": prof["ua_platform"],
                "platformVersion": prof["ua_platform_version"],
                "architecture": prof["arch"], "bitness": prof["bitness"],
                "model": "", "mobile": False,
            },
        })
        prof["brands"] = brands
        prof["brands_full"] = brands_full
        return True
    except Exception:
        return False


def sync_playwright():
    """Default playwright asli; patchright opt-in (USE_PATCHRIGHT=1).

    Catatan empiris: patchright 1.63 membuat getContext('webgl') -> null di setup
    Xvfb ini (teruji A/B) — fatal utk fingerprint (challenge membaca WebGL).
    Manfaat patchright hanya menutup jejak Runtime.enable (P1); dg google-chrome
    asli kohort TLS/JA3 sudah natural. */
    """
    if os.environ.get("USE_PATCHRIGHT") == "1":
        try:
            from patchright.sync_api import sync_playwright as _sw
            return _sw()
        except Exception:
            pass
    from playwright.sync_api import sync_playwright as _sw
    return _sw()


CHROME_ARGS = _BASE_ARGS


def ensure_display():
    """Sediakan X DISPLAY VIRTUAL (Xvfb) utk Chrome headed tanpa jendela nyata."""
    if os.environ.get("USE_DESKTOP_DISPLAY") == "1" and os.environ.get("DISPLAY"):
        return os.environ["DISPLAY"]
    num = os.environ.get("XVFB_DISPLAY", "99")
    disp = f":{num}"
    sock = f"/tmp/.X11-unix/X{num}"
    if not os.path.exists(sock):
        subprocess.Popen(
            ["Xvfb", disp, "-screen", "0", "2560x1440x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(30):
            time.sleep(0.3)
            if os.path.exists(sock):
                break
    os.environ["DISPLAY"] = disp
    os.environ.pop("WAYLAND_DISPLAY", None)
    return disp


if __name__ == "__main__":
    p = build_profile()
    fc = write_fonts_conf(p)
    slim = {k: v for k, v in p.items() if k not in ("gl_exts", "brands_full")}
    print(json.dumps(slim, indent=1, ensure_ascii=False)[:1200])
    print("fc conf:", fc)
    print("init_script bytes:", len(init_script(p)))
