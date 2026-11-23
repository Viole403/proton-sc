/* fp_forge.js v2 — mutator pre-encrypt (kait JSON.stringify di SEMUA frame) +
   lapor lintas-frame via postMessage + sinkron VisitorId pesan.
   Mode: P.forge.mode = 'forge' (visitorId 32-hex acak per attempt) | 'keep' */
(() => {
  const DBG = (typeof window !== 'undefined' && window.__FP_DEBUG === 1);
  const kirim = (o) => { if (DBG) { try { window.parent.postMessage({type: 'forge.dbg', ...o}, '*'); } catch (e) {} } };
  kirim({extra: 'boot2'});
  try {
  const P = __PROFILE_JSON__;
  const F = P.forge || {};
  const asli = JSON.stringify;
  const acak = (min, max) => min + Math.floor(Math.random() * (max - min + 1));
  const hex32 = () => { let s = ''; const h = '0123456789abcdef';
    for (let i = 0; i < 32; i++) s += h[acak(0, 15)]; return s; };
  // ===== REHASH (satu-satunya tambahan v10): murmur3-x64-128 + kalibrasi mandiri =====
  const _hx = (function () {
    'use strict';
function _x64Add(m, n) {
        //
        // Given two 64bit ints (as an array of two 32bit ints) returns the two
        // added together as a 64bit int (as an array of two 32bit ints).
        //

        m = [m[0] >>> 16, m[0] & 0xffff, m[1] >>> 16, m[1] & 0xffff];
        n = [n[0] >>> 16, n[0] & 0xffff, n[1] >>> 16, n[1] & 0xffff];
        var o = [0, 0, 0, 0];

        o[3] += m[3] + n[3];
        o[2] += o[3] >>> 16;
        o[3] &= 0xffff;

        o[2] += m[2] + n[2];
        o[1] += o[2] >>> 16;
        o[2] &= 0xffff;

        o[1] += m[1] + n[1];
        o[0] += o[1] >>> 16;
        o[1] &= 0xffff;

        o[0] += m[0] + n[0];
        o[0] &= 0xffff;

        return [(o[0] << 16) | o[1], (o[2] << 16) | o[3]];
    }

    function _x64Multiply(m, n) {
        //
        // Given two 64bit ints (as an array of two 32bit ints) returns the two
        // multiplied together as a 64bit int (as an array of two 32bit ints).
        //

        m = [m[0] >>> 16, m[0] & 0xffff, m[1] >>> 16, m[1] & 0xffff];
        n = [n[0] >>> 16, n[0] & 0xffff, n[1] >>> 16, n[1] & 0xffff];
        var o = [0, 0, 0, 0];

        o[3] += m[3] * n[3];
        o[2] += o[3] >>> 16;
        o[3] &= 0xffff;

        o[2] += m[2] * n[3];
        o[1] += o[2] >>> 16;
        o[2] &= 0xffff;

        o[2] += m[3] * n[2];
        o[1] += o[2] >>> 16;
        o[2] &= 0xffff;

        o[1] += m[1] * n[3];
        o[0] += o[1] >>> 16;
        o[1] &= 0xffff;

        o[1] += m[2] * n[2];
        o[0] += o[1] >>> 16;
        o[1] &= 0xffff;

        o[1] += m[3] * n[1];
        o[0] += o[1] >>> 16;
        o[1] &= 0xffff;

        o[0] += (m[0] * n[3]) + (m[1] * n[2]) + (m[2] * n[1]) + (m[3] * n[0]);
        o[0] &= 0xffff;

        return [(o[0] << 16) | o[1], (o[2] << 16) | o[3]];
    }

    function _x64Rotl(m, n) {
        //
        // Given a 64bit int (as an array of two 32bit ints) and an int
        // representing a number of bit positions, returns the 64bit int (as an
        // array of two 32bit ints) rotated left by that number of positions.
        //

        n %= 64;

        if (n === 32) {
            return [m[1], m[0]];
        } else if (n < 32) {
            return [(m[0] << n) | (m[1] >>> (32 - n)), (m[1] << n) | (m[0] >>> (32 - n))];
        } else {
            n -= 32;
            return [(m[1] << n) | (m[0] >>> (32 - n)), (m[0] << n) | (m[1] >>> (32 - n))];
        }
    }

    function _x64LeftShift(m, n) {
        //
        // Given a 64bit int (as an array of two 32bit ints) and an int
        // representing a number of bit positions, returns the 64bit int (as an
        // array of two 32bit ints) shifted left by that number of positions.
        //

        n %= 64;

        if (n === 0) {
            return m;
        } else if (n < 32) {
            return [(m[0] << n) | (m[1] >>> (32 - n)), m[1] << n];
        } else {
            return [m[1] << (n - 32), 0];
        }
    }

    function _x64Xor(m, n) {
        //
        // Given two 64bit ints (as an array of two 32bit ints) returns the two
        // xored together as a 64bit int (as an array of two 32bit ints).
        //

        return [m[0] ^ n[0], m[1] ^ n[1]];
    }

    function _x64Fmix(h) {
        //
        // Given a block, returns murmurHash3's final x64 mix of that block.
        // (`[0, h[0] >>> 1]` is a 33 bit unsigned right shift. This is the
        // only place where we need to right shift 64bit ints.)
        //

        h = _x64Xor(h, [0, h[0] >>> 1]);
        h = _x64Multiply(h, [0xff51afd7, 0xed558ccd]);
        h = _x64Xor(h, [0, h[0] >>> 1]);
        h = _x64Multiply(h, [0xc4ceb9fe, 0x1a85ec53]);
        h = _x64Xor(h, [0, h[0] >>> 1]);

        return h;
    }

    // PUBLIC FUNCTIONS
    // ----------------

    

    function x64hash128(key, seed) {
        //
        // Given a string and an optional seed as an int, returns a 128 bit
        // hash using the x64 flavor of MurmurHash3, as an unsigned hex.
        //

        key = key || '';
        seed = seed || 0;

        var remainder = key.length % 16;
        var bytes = key.length - remainder;

        var h1 = [0, seed];
        var h2 = [0, seed];

        var k1 = [0, 0];
        var k2 = [0, 0];

        var c1 = [0x87c37b91, 0x114253d5];
        var c2 = [0x4cf5ad43, 0x2745937f];

        for (var i = 0; i < bytes; i = i + 16) {
            k1 = [((key.charCodeAt(i + 4) & 0xff)) | ((key.charCodeAt(i + 5) & 0xff) << 8) | ((key.charCodeAt(i + 6) & 0xff) << 16) | ((key.charCodeAt(i + 7) & 0xff) << 24), ((key.charCodeAt(i) & 0xff)) | ((key.charCodeAt(i + 1) &
                0xff) << 8) | ((key.charCodeAt(i + 2) & 0xff) << 16) | ((key.charCodeAt(i + 3) & 0xff) << 24)];
            k2 = [((key.charCodeAt(i + 12) & 0xff)) | ((key.charCodeAt(i + 13) & 0xff) << 8) | ((key.charCodeAt(i + 14) & 0xff) << 16) | ((key.charCodeAt(i + 15) & 0xff) << 24), ((key.charCodeAt(i + 8) & 0xff)) | ((key.charCodeAt(i +
                9) & 0xff) << 8) | ((key.charCodeAt(i + 10) & 0xff) << 16) | ((key.charCodeAt(i + 11) & 0xff) << 24)];

            k1 = _x64Multiply(k1, c1);
            k1 = _x64Rotl(k1, 31);
            k1 = _x64Multiply(k1, c2);
            h1 = _x64Xor(h1, k1);

            h1 = _x64Rotl(h1, 27);
            h1 = _x64Add(h1, h2);
            h1 = _x64Add(_x64Multiply(h1, [0, 5]), [0, 0x52dce729]);

            k2 = _x64Multiply(k2, c2);
            k2 = _x64Rotl(k2, 33);
            k2 = _x64Multiply(k2, c1);
            h2 = _x64Xor(h2, k2);

            h2 = _x64Rotl(h2, 31);
            h2 = _x64Add(h2, h1);
            h2 = _x64Add(_x64Multiply(h2, [0, 5]), [0, 0x38495ab5]);
        }

        k1 = [0, 0];
        k2 = [0, 0];

        switch (remainder) {
            case 15:
                k2 = _x64Xor(k2, _x64LeftShift([0, key.charCodeAt(i + 14)], 48));

            case 14:
                k2 = _x64Xor(k2, _x64LeftShift([0, key.charCodeAt(i + 13)], 40));

            case 13:
                k2 = _x64Xor(k2, _x64LeftShift([0, key.charCodeAt(i + 12)], 32));

            case 12:
                k2 = _x64Xor(k2, _x64LeftShift([0, key.charCodeAt(i + 11)], 24));

            case 11:
                k2 = _x64Xor(k2, _x64LeftShift([0, key.charCodeAt(i + 10)], 16));

            case 10:
                k2 = _x64Xor(k2, _x64LeftShift([0, key.charCodeAt(i + 9)], 8));

            case 9:
                k2 = _x64Xor(k2, [0, key.charCodeAt(i + 8)]);
                k2 = _x64Multiply(k2, c2);
                k2 = _x64Rotl(k2, 33);
                k2 = _x64Multiply(k2, c1);
                h2 = _x64Xor(h2, k2);

            case 8:
                k1 = _x64Xor(k1, _x64LeftShift([0, key.charCodeAt(i + 7)], 56));

            case 7:
                k1 = _x64Xor(k1, _x64LeftShift([0, key.charCodeAt(i + 6)], 48));

            case 6:
                k1 = _x64Xor(k1, _x64LeftShift([0, key.charCodeAt(i + 5)], 40));

            case 5:
                k1 = _x64Xor(k1, _x64LeftShift([0, key.charCodeAt(i + 4)], 32));

            case 4:
                k1 = _x64Xor(k1, _x64LeftShift([0, key.charCodeAt(i + 3)], 24));

            case 3:
                k1 = _x64Xor(k1, _x64LeftShift([0, key.charCodeAt(i + 2)], 16));

            case 2:
                k1 = _x64Xor(k1, _x64LeftShift([0, key.charCodeAt(i + 1)], 8));

            case 1:
                k1 = _x64Xor(k1, [0, key.charCodeAt(i)]);
                k1 = _x64Multiply(k1, c1);
                k1 = _x64Rotl(k1, 31);
                k1 = _x64Multiply(k1, c2);
                h1 = _x64Xor(h1, k1);
        }

        h1 = _x64Xor(h1, [0, key.length]);
        h2 = _x64Xor(h2, [0, key.length]);

        h1 = _x64Add(h1, h2);
        h2 = _x64Add(h2, h1);

        h1 = _x64Fmix(h1);
        h2 = _x64Fmix(h2);

        h1 = _x64Add(h1, h2);
        h2 = _x64Add(h2, h1);

        return ("00000000" + (h1[0] >>> 0).toString(16)).slice(-8) + ("00000000" + (h1[1] >>> 0).toString(16)).slice(-8) + ("00000000" + (h2[0] >>> 0).toString(16)).slice(-8) + ("00000000" + (h2[1] >>> 0).toString(16)).slice(-8);
    };
return x64hash128;

    return x64hash128;
  })();
  const Q32 = ['fonts','domBlockers','fontPreferences','screenFrame','osCpu','languages','colorDepth','deviceMemory','screenResolution','hardwareConcurrency','timezone','sessionStorage','localStorage','indexedDB','openDatabase','cpuClass','platform','plugins','touchSupport','vendor','vendorFlavors','cookiesEnabled','colorGamut','invertedColors','forcedColors','monochrome','contrast','reducedMotion','hdr','videoCard','pdfViewerEnabled','architecture'];
  const TZ = (typeof Intl !== 'undefined') ? (Intl.DateTimeFormat().resolvedOptions().timeZone || '') : '';
  const LANG0 = P.locale || (P.languages && P.languages[0]) || 'en-US';
  function canon32(t) {
    let e = '';
    for (const k of Object.keys(t).sort()) {
      const i = t[k];
      const a = (i && i.error) ? 'error' : JSON.stringify(i.value);
      e += (e ? '|' : '') + k.replace(/([:|\\])/g, '\\$1') + ':' + a;
    }
    return e;
  }
  function susun(vis, ho) {
    const t = {};
    const hid = {domBlockers: undefined, osCpu: undefined, cpuClass: undefined,
                 invertedColors: ho._iv, languages: ho._langs, screenFrame: ho._sf,
                 cookiesEnabled: ho._ck, colorGamut: ho._gam, forcedColors: ho._fc,
                 contrast: ho._ct, reducedMotion: ho._rm, hdr: ho._hdr};
    for (const k of Q32) t[k] = {value: (k in hid) ? hid[k] : vis[k]};
    return canon32(t);
  }
  function visDari(s0) {
    const v = {};
    for (const k of Q32) if (k in s0) v[k] = s0[k];
    return v;
  }
  const _ckal = new Map();
  function kalibrasi(visAsli, vidAsli, langH) {
    if (_ckal.has(vidAsli)) return _ckal.get(vidAsli);
    const L0 = langH || LANG0;
    // ==== KANDIDAT DERIVED-EXACT (rumus collector asli + jawaban stub) ====
    // contrast: J('no-preference')?0:J('high')||J('more')?1:J('low')||J('less')?-1:J('forced')?10:void 0
    // reducedMotion/hdr: !!X||!Y&&void 0 -> boolean (stub menjawab dua-duanya)
    // invertedColors/forcedColors: stub -> SELALU false; colorGamut = F.gamut
    // screenFrame: X()=[availTop, w-availW-availL, h-availH-availT, availL] dibulat B(,10)
    //   dgn stub = [0, 0, taskbar, 0]; languages: jalur Chrome = [[lang]]
    const r10 = (x) => Math.round(x / 10) * 10;
    const ho0 = {
      _ck: true,
      _gam: (P.media && P.media.gamut) || 'srgb',
      _fc: false,
      _ct: (P.media && P.media.contrast === 'high') ? 1 : 0,
      _rm: !!(P.media && P.media.reducedMotion),
      _hdr: !!(P.media && P.media.hdr),
      _sf: [0, 0, r10(P.taskbar | 0), 0],
      _langs: [[L0]],
      _iv: false,
    };
    for (const ho of [ho0, Object.assign({}, ho0, {_langs: [[L0, L0.split('-')[0]]]})]) {
      if (_hx(susun(visAsli, ho), 0) === vidAsli) { _ckal.set(vidAsli, ho); return ho; }
    }
    // ==== fallback brute-force: domain PERSIS dari source collector ====
    const XS = [...new Set([0, P.taskbar|0, P.sb_w|0, P.chrome_h|0, 20, 30, 40, 50, 60, 68, 76, 86, 94, 103, 110])];
    const sf = [[0, 0, r10(P.taskbar | 0), 0], [0,0,0,0]];
    for (const x of XS) if (x) for (let pos = 0; pos < 4; pos++) {
      const v = [0,0,0,0]; v[pos] = r10(x); sf.push(v);
    }
    for (const x of XS) for (const y of XS) if (x && y) {
      sf.push([r10(x), r10(y), 0, 0]); sf.push([0, 0, r10(x), r10(y)]); sf.push([r10(x), 0, r10(y), 0]);
    }
    const lg = [[[L0]], [[L0, L0.split('-')[0]]], [[L0], [L0, L0.split('-')[0]]]];
    const iv = [false, undefined, true];
    for (const _ck of [true,false]) for (const _gam of ['srgb','p3','rec2020'])
    for (const _fc of [false,true]) for (const _ct of [0, 1, -1, 10, undefined])
    for (const _rm of [false,true]) for (const _hdr of [false,true])
    for (const _sf of sf) for (const _langs of lg) for (const _iv of iv) {
      const ho = {_ck,_gam,_fc,_ct,_rm,_hdr,_sf,_langs,_iv};
      if (_hx(susun(visAsli, ho), 0) === vidAsli) { _ckal.set(vidAsli, ho); return ho; }
    }
    _ckal.set(vidAsli, null);
    return null;
  }
  function rehash(s0, H) {
    // hidden dicari dari hash(nilai ASLI H) == vid asli, lalu hash(nilai ideal s0)
    const ho = kalibrasi(visDari(H), H.visitorId, H.language);
    if (!ho) return null;
    const vis = visDari(s0);
    return _hx(susun(vis, ho), 0);
  }

  // ===== PRNG seeded (mulberry32 + FNV-1a): output MURNI fungsi (P, H) —
  // deterministik lintas frame & lintas serialisasi ulang. Math.random dulu bikin
  // fontsIdeal/fontPrefsIdeal/perilaku beda tiap frame & tiap POST (blob vs resubmit
  // beda isi, visitorId blob vs body beda nilai) = sinyal "blob sedang dimutasi". =====
  const _h32 = (str) => {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  };
  const _sd = _h32(String((F.vid || '') + '|' + (P.fontSeed || 0) + '|' + (P.seed || 0) + '|' + (P.platform || '')));
  let _rs = _sd;
  const rnd = () => {
    _rs = (_rs + 0x6D2B79F5) >>> 0;
    let t = _rs;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const rint = (min, max) => min + Math.floor(rnd() * (max - min + 1));
  const seedUlang = (tag) => { _rs = (_sd ^ _h32(String(tag))) >>> 0; };

  const pluginsIdeal = (P.plugins || []).map((pl) => ({
    name: pl[0], description: pl[1],
    mimeTypes: pl[2].map((mm) => ({type: mm[0], suffixes: mm[1]}))}));
  const fontsIdeal = (P.fonts_fake || []).concat(P.fonts_real || []).slice(0, rint(3, 6));
  // fontPreferences NYATA = hasil ukur rendering, selalu kelipatan 1/64 px
  // (bukti H asli: 144.015625, 131.625, 10.125) dan sering IDENTIK antar family
  // (default=apple=serif=162). Kuantisasi 1/64 + pola nyata (X1: isolasi toxin).
  const q64 = (x) => Math.round(x * 64) / 64;
  const fontPrefsIdeal = (() => {
    const base = rint(132, 178);
    return {default: base, apple: base, serif: base,
            sans: q64(base - rint(8, 24)),
            mono: q64(base - rint(16, 34)),
            min: q64(rint(10, 12) + rint(0, 3) / 8),
            system: q64(base - rint(2, 14))};
  })();
  const vidBaru = (F.mode === 'keep' || F.mode === 'off') ? null : (F.vid || hex32());
  let _vidfinal = null, _vidstash = null, _siapB64 = null;   // [fix 2026-10-13] state di CLOSURE — window harus bersih dari global buatan (pemindai Object.keys window = linkage lintas-attempt)
  const lapor = (extra) => {
    kirim({hit: 1, vid: vidBaru, extra: extra || ''});
    // MERGE, jangan timpa: dump kalibrasi dari ideal() harus selamat sampai capture
    if (DBG) try {
      window.__forge_dbg = Object.assign({}, window.__forge_dbg || {},
        {hit: 1, vid: vidBaru, extra: extra || ''});
    } catch (e) {}
  };
  lapor('boot:' + location.host);

  function perilaku(nilai) {
    try {
      seedUlang('p|' + nilai);   // deterministik lintas frame & serialisasi ulang
      // SHAPE SINTETIS (kit v2) = penentu tier CAPTCHA (matriks A/B: shape real -> tier sms)
      const huruf = 'abcdefghijklmnopqrstuvwxyz0123456789';
      const kd = [];
      for (let i = 0; i < rint(8, 14); i++)
        kd.push({type: 'keydown', id: 'username', key: huruf[rint(0, huruf.length - 1)]});
      const klik = {}; const kx = String(rint(140, 340));
      klik[kx] = [[rint(140, 340), rint(24, 44)], [rint(140, 340), rint(24, 44)]];
      return {click: klik, keydown: kd,
              focus: {'#username': [rint(1000, 3000)]},
              blur: {'#username': [rint(30000, 60000)]},
              mousemove: rint(120, 420)};
    } catch (e) { return {}; }
  }

  // stream perilaku SEKALI HITUNG (konstan tiap frame & tiap serialisasi).
  // Kit v2: keydown = huruf acak (bukan echo username) — jadi aman tanpa input.
  const streamIdeal = (() => {
    try {
      seedUlang('p|stream');
      const huruf = 'abcdefghijklmnopqrstuvwxyz0123456789';
      const kd = [];
      for (let i = 0; i < rint(8, 14); i++)
        kd.push({type: 'keydown', id: 'username', key: huruf[rint(0, huruf.length - 1)]});
      const klik = {}; const kx = String(rint(140, 340));
      klik[kx] = [[rint(140, 340), rint(24, 44)], [rint(140, 340), rint(24, 44)]];
      return {click: klik, keydown: kd,
              focus: {'#username': [rint(1000, 3000)]},
              blur: {'#username': [rint(30000, 60000)]},
              mousemove: rint(120, 420),
              duration: [rint(2600, 6900)]};
    } catch (e) { return {}; }
  })();

  function ideal(H) {
    const s = {};
    for (const k of Object.keys(H)) s[k] = H[k];
    // HANYA-TIMPA: jangan pernah menambah field yang tak ada di H asli
    // (invertedColors dulu disuntik padahal wire H tak punya -> tamper kasat mata).
    const timpa = (k, v) => { if (k in s) s[k] = v; };
    timpa('fonts', fontsIdeal);
    timpa('fontPreferences', fontPrefsIdeal);
    timpa('plugins', pluginsIdeal);
    timpa('videoCard', {vendor: P.gl_vendor, renderer: P.gl_renderer});
    timpa('webglVendorAndRenderer', P.gl_vendor + '~' + P.gl_renderer);
    timpa('platform', P.platform);
    timpa('hardwareConcurrency', P.cores);
    timpa('deviceMemory', P.mem);
    timpa('colorDepth', P.colorDepth);
    // screenResolution collector asli: [e(width), e(height)].sort().reverse()
    // = sort DEFAULT (lexicographic!) lalu reverse. BUKAN sort numerik!
    // Bukti run 2028: 1366x768 -> asli [768,1366]; sort numerik menghasilkan
    // [1366,768] = mustahil dari browser -> 2028. 1920x1080 kebetulan sama.
    timpa('screenResolution', [P.screen_w, P.screen_h].sort().reverse());
    timpa('touchSupport', {maxTouchPoints: P.touch, touchEvent: !!P.touch, touchStart: !!P.touch});
    timpa('languages', P.languages.slice());   // FLAT = wire asli (T2 nested = sms, bukan umpan)
    // SPLIT-KIRIM FINAL (seluruh sel matriks terjawab):
    // bait menu = kit-tanpa-keydown@42 + keydown-obj@47 (S2c: captcha terbukti)
    // create    = blob BASE (stream nyata + device identik) — TE1 18:01 lolos.
    const _is47 = ('keydown' in H) || ('blur' in H);
    if (!_is47) {
      const p = perilaku('split');
      delete p.keydown;
      for (const [k, v] of Object.entries(p)) s[k] = v;
    } else {
      try { if (window.__FORGE_SPLIT) window.__baseJson = asli(s); } catch (e) {}   // [fix 2026-10-13] split off: tak tulis global
      seedUlang('p|splitkd');
      const huruf = 'abcdefghijklmnopqrstuvwxyz0123456789';
      const kd = [];
      for (let i = 0; i < rint(8, 14); i++)
        kd.push({type: 'keydown', id: 'username', key: huruf[rint(0, 35)]});
      s.keydown = kd;
    }
    timpa('language', P.languages[0]);
    timpa('pixelRatio', P.dpr);
    timpa('monochrome', 0);
    timpa('adBlock', false);
    // T1 (bisect umpan): stream SHAPE sintetis kit v2 ditulis tanpa syarat
    // (komentar Era B: "shape sintetis = penentu tier CAPTCHA"). duration TIDAK
    // ditulis (kit v2 juga tidak). invertedColors & bahasa nested TIDAK — dua
    // kandidat toxin grammar. Stream collector asli ditimpa utk 47-key H.
    // T2: umpan non-stream — bahasa NESTED (keanehan struktural di field STABIL,
    // konsisten lintas 42+47). Matriks stream TERTUTUP kontradiksi (bait=toxin=
    // keanehan struktural). X7 (nilai mustahil bentuk sah) = sms 2/2 — bukan umpan.
    const _vh = rehash(s, H);
    // KANONIK: _vh (murmur VALID utk komponen termutasi) — dipakai blob & body.
    // Bukti run probe: body POST-1 vs resubmit byte-identik, 2028 tetap; dan jejak
    // msg 'child.message.data' datang SEBELUM stringify -> body keburu ditulis
    // vidBaru sementara blob _vh = split-brain = "blob sedang dimutasi".
    // X1: vid kembali _vh (murmur valid) — isolasi stream sebagai satu-satunya
    // umpan/toxin kandidat (base q64+_vh = create-LOLOS via email 16:43).
    s.visitorId = (F.mode === 'keep') ? H.visitorId : (_vh || vidBaru || H.visitorId);
    try {
      _vidfinal = s.visitorId;   // satu id utk SEMUA kanal [closure]
      // sunting objek pesan yang datang sebelum _vh lahir (anti split-brain)
      if (typeof _antreVid !== 'undefined' && _antreVid.length) {
        for (const o of _antreVid) { try { o.visitorId = s.visitorId; } catch (x) {} }
        _antreVid.length = 0;
      }
    } catch (e) {}
    try {
      const _dump = {hitung: _vh, asli: H.visitorId, cocok: !!_vh, vis: visDari(H)};
      _vidstash = _dump;
      if (DBG) window.__forge_dbg = Object.assign({}, window.__forge_dbg, _dump);   // [fix 2026-10-13] hanya debug
      try { (window.top || window.parent).postMessage({type: 'forge.vid', ..._dump, bjson: window.__baseJson || ''}, '*'); } catch (x) {}
    } catch (e) {}
    return s;
  }

  JSON.stringify = function (v) {
    try {
      if (v && typeof v === 'object' && 'visitorId' in v && 'fonts' in v && 'platform' in v) {
        if (F.mode === 'off') { lapor('mode-off'); return asli.apply(JSON, arguments); }  // kontrol: blob asli utuh
        lapor('cond-match');
        const out = asli.call(JSON, ideal(v), ...[].slice.call(arguments, 1));
        lapor('stringify');
        return out;
      }
    } catch (e) { try { lapor('err:' + e.message); } catch (x) {} }
    return asli.apply(JSON, arguments);
  };

  // ===== SPLIT-KIRIM: resubmit (create) membawa blob BASE, POST-1 blob umpan.
  // Kunci AES = nama key map Payload (latin1 16) — sumber blob_tool decrypt.
  try {
    _siapB64 = null;
    const _u8e = new TextEncoder();
    const _b64e = (buf) => { let s = ''; const b = new Uint8Array(buf);
      for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]); return btoa(s); };
    async function _siapkan(kid) {
      if (!window.__FORGE_SPLIT) return;   // [fix 2026-10-13] split-kirim off: __siapB64 tak pernah ditulis (global bocor ke pemindai window)
      try {
        const kb = _u8e.encode(kid);
        _nota('siap-kid:' + String(kid).length + ':' + kb.length + ':' + (window.__baseJson ? 'ada' : 'NULL'));
        if (!window.__baseJson) return;
        const ck = await crypto.subtle.importKey('raw', kb, 'AES-CBC', false, ['encrypt']);
        const ct = await crypto.subtle.encrypt({name: 'AES-CBC', iv: new Uint8Array(16)},
                                               ck, _u8e.encode(window.__baseJson));
        _siapB64 = _b64e(ct);
        lapor('siap-swap:' + _siapB64.length);
      } catch (e) { lapor('siap-gagal:' + (e && e.message)); }
    }
    function _ganti(body) {
      // [fix 2026-10-13] SPLIT-KIRIM DIMATIKAN (window.__FORGE_SPLIT tidak pernah
      // diset = off). Bukti capture mitm manual user 2026-10-13 (signup penuh s/d
      // recovery email, 1 shot): resubmit create berbody SAMA PERSIS dgn POST-1
      // (4120=4120). Pertukaran blob (__baseJson dr iframe challenge) terkait
      // 77/78 run LIMBO: auth/refresh 422 -> token hilang scope 9106 -> UI
      // 'Loading' selamanya -> akun dibunuh 10003. Alur manual TANPA auth/refresh.
      // Blob apa adanya = perilaku browser asli. Eksperimen bisa dibuka lagi via
      // window.__FORGE_SPLIT = 1.
      if (!window.__FORGE_SPLIT) return body;
      if (typeof body !== 'string') { _nota('ganti:bukan-string'); return body; }
      if (!_siapB64) { _nota('ganti:siap-null'); return body; }
      try {
        const sm = body.match(/"Payload":\{"[^"]+":"([^"]+)"/);
        if (!sm) { _nota('ganti:sm-ko' + 'song:' + body.slice(0, 60)); return body; }
        _nota('ganti:' + (sm[1] === _siapB64 ? 'SAMA' : 'BEDA') + ':' + sm[1].length + '/' + _siapB64.length);
        return body.split(sm[1]).join(_siapB64);
      } catch (e) { _nota('ganti-err:' + (e && e.message)); return body; }
    }
    function _kenalHv(hs) {
      try {
        if (!hs) return false;
        if (typeof hs.get === 'function') return !!hs.get('x-pm-human-verification-token');
        for (const k of Object.keys(hs))
          if (String(k).toLowerCase().includes('human-verification')) return true;
      } catch (e) {}
      return false;
    }
    const _stat = {xhr: 0, fetch: 0, hv: 0, swap: 0, siap: 0, lihat: ''};   // [fix 2026-10-13] stat di closure; window.__forge_dbg hanya saat FP_DEBUG
    const _hs = () => { try {
        if (DBG) { window.__forge_dbg = window.__forge_dbg || {}; window.__forge_dbg.hookstat = _stat; }
        return _stat; } catch (e) { return _stat; } };
    const _nota = (x) => { try { const h = _hs(); h.lihat = ((h.lihat || '') + '|' + x).slice(-300); } catch (e) {} };
    const _xo = XMLHttpRequest.prototype.open;
    const _xs = XMLHttpRequest.prototype.send;
    const _xh = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.open = function (m, u) {
      this.__m = String(m || '').toUpperCase(); this.__u = String(u || '');
      return _xo.apply(this, arguments);
    };
    XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
      if (String(k).toLowerCase().includes('human-verification')) this.__hv = 1;
      return _xh.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function (body) {
      try {
        const h = _hs(); h.xhr = (h.xhr || 0) + 1;
        const bt = typeof body;
        if (this.__m === 'POST' && this.__u.includes('/api/core/v4/users')) {
          _nota('xhr:' + bt + (this.__hv ? ':hv' : ''));
          const str = (bt === 'string') ? body
            : (bt === 'object' && body && typeof body.toString === 'function' && bt !== 'FormData') ? String(body) : null;
          if (str && str.includes('"Payload"')) {
            if (this.__hv) {
              h.swap = (h.swap || 0) + 1; _nota('swap-resubmit');
              body = _ganti(str);
            } else {
              const mm = str.match(/"Payload":\{"([^"]+)":"/);
              if (mm) { h.siap = (h.siap || 0) + 1; _siapkan(mm[1]); }
            }
          }
        }
      } catch (e) { _nota('xhr-err:' + (e && e.message)); }
      return _xs.call(this, body);
    };
    const _f = window.fetch;
    if (_f) {
      window.fetch = function (input, init) {
        try {
          const h = _hs(); h.fetch = (h.fetch || 0) + 1;
          const u = (typeof input === 'string') ? input
            : ((input && (input.url || input.href)) || String(input && input));
          const i = init || {};
          const m = String(i.method || (input && input.method) || 'GET').toUpperCase();
          _nota(m + '~' + String(u).slice(0, 60) + '#' + typeof i.body);
          if (m === 'POST' && u.includes('/api/core/v4/users')) {
            const bt = typeof i.body;
            _nota('fetch:' + bt);
            if (typeof i.body === 'string' && i.body.includes('"Payload"')) {
              if (_kenalHv(i.headers) || _kenalHv(input && input.headers)) {
                h.swap = (h.swap || 0) + 1; _nota('swap-resubmit-f');
                init = Object.assign({}, i, {body: _ganti(i.body)});
              } else {
                const mm = i.body.match(/"Payload":\{"([^"]+)":"/);
                if (mm) { h.siap = (h.siap || 0) + 1; _siapkan(mm[1]); }
              }
            }
          }
        } catch (e) { _nota('fetch-err:' + (e && e.message)); }
        return _f.apply(this, [input, init]);
      };
    }
  } catch (e) { lapor('hook-gagal:' + (e && e.message)); }

  // sinkron VisitorId di pesan postMessage (level body request punya field ini)
  // URUTAN KRITIS: pesan ini datang SEBELUM blob di-stringify — nilai final (_vh)
  // belum ada. Karena itu: antre objek, sunting saat _vh lahir (ideal()).
  const _antreVid = [];
  try {
    window.addEventListener('message', (e) => {
      try {
        const d = e.data;
        if (d && d.type === 'forge.vid') {
          _vidstash = d;
          if (window.__FORGE_SPLIT && d.bjson) { try { window.__baseJson = d.bjson; } catch (x) {} }  // lintas frame: stash blob-base [fix 2026-10-13] split off: tak tulis global
          if (DBG) window.__forge_dbg = Object.assign({}, window.__forge_dbg, d);   // [fix 2026-10-13] hanya debug
          // PENTING: pesan ini datang SEBELUM child.message.data — set id final
          // SEKARANG agar kanal body kebagian nilai yang sama dengan blob.
          if (d.hitung) {
            try { _vidfinal = d.hitung; } catch (x) {}
            for (const o of _antreVid) { try { o.visitorId = d.hitung; } catch (x) {} }
            _antreVid.length = 0;
          }
        } else if (d && d.type === 'child.message.data' && d.data && vidBaru) {
          const fin = _vidfinal || (_vidstash && _vidstash.hitung);
          if (d.data && typeof d.data === 'object' && 'visitorId' in d.data && 'fonts' in d.data && F.mode !== 'keep') {
            try { Object.assign(d.data, ideal(d.data)); } catch (x) {}
          } else if (F.mode !== 'keep') {
            if (fin) d.data.visitorId = fin;
            else _antreVid.push(d.data);   // disunting saat _vh lahir
          }
          lapor('msg-sync');
        }
      } catch (x) {}
    }, true);
  } catch (e) {}
  // ===== [fix 2026-10-13] NOISE RENDER per-attempt: canvas2D (toDataURL/toBlob/
  // getImageData) + WebGL/WebGL2 readPixels. AKAR linkage: hash render IDENTIK
  // lintas attempt (Chrome jatuh ke software rendering) -> server menganggap
  // SEMUA attempt = SATU mesin. Noise +-1-2 level kanal pada ~12% piksel
  // (alpha>0) diturunkan dari HASH KONTEN piksel (FNV-1a) XOR seed persona
  // (P.seed|F.vid|fontSeed) via PRNG mulberry32 = PRNG ideal() -> piksel sama =
  // noise sama (stabil dlm halaman, lintas frame & serialisasi ulang; toDataURL/
  // toBlob/getImageData/readPixels pada konten sama TIDAK kontradiktif), seed
  // beda = noise beda tiap attempt. State di closure (window bersih); hook tampil
  // native via rantai _FSRC (blok masking di bawah, panjang = _x.length asli).
  // FORGE_MODE=off = kontrol tanpa noise. Gagal install -> perilaku asli utuh.
  let _tdHook, _tbHook, _gidHook, _rpHook, _rp2Hook, _natLen, _rp2Len;
  // [gate 2026-10-13] NOISE_RENDER=1 baru aktif (default mati — curiga memicu
  // 2028 'abusive': semua attempt setelah c788310 kena 2028, pagi ini 10/16
  // sukses tanpa noise). FORGE_MODE=off tetap kontrol penuh tanpa noise.
  if (F.mode !== 'off' && P.noise_render) try {
    const _sdN = _h32('r|' + String(F.vid || '') + '|' + String(P.seed || 0)
                      + '|' + String(P.fontSeed || 0));
    const _hfnv = (a) => {
      let h = 2166136261 >>> 0;
      for (let i = 0; i < a.length; i++) { h ^= a[i]; h = Math.imul(h, 16777619); }
      return h >>> 0;
    };
    const _prngN = (sd) => {
      let s = sd >>> 0;
      return () => {
        s = (s + 0x6D2B79F5) >>> 0; let t = s;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
      };
    };
    // in-place, deterministik atas konten; klamping manual (Uint8Array wrap-around)
    const _acakPiksel = (data) => {
      try {
        const n = data.length >> 2;
        if (!n) return data;
        const r = _prngN((_hfnv(data) ^ _sdN) >>> 0);
        for (let i = 0; i < n; i++) {
          if (r() >= 0.12) continue;
          const o = i << 2;
          if (data[o + 3] === 0) continue;  // px transparan: RGB tak survive PNG -> jangan disentuh (toDataURL vs getImageData tetap klop)
          for (let c = 0; c < 3; c++) {
            const d = (r() < 0.5 ? -1 : 1) * (1 + (r() < 0.18 ? 1 : 0));
            const v = data[o + c] + d;
            data[o + c] = v < 0 ? 0 : (v > 255 ? 255 : v);
          }
        }
      } catch (e) {}
      return data;
    };
    const _g2 = CanvasRenderingContext2D.prototype.getImageData;
    _gidHook = function () {
      const img = _g2.apply(this, arguments);
      try { _acakPiksel(img.data); } catch (e) {}
      return img;
    };
    CanvasRenderingContext2D.prototype.getImageData = _gidHook;
    // salin kanvas -> noise -> encode; berlaku utk kanvas 2D maupun WebGL
    // (drawImage menyalin apa adanya; tainted/gagal -> jatuh ke perilaku asli)
    const _salinNoise = (srcK) => {
      const w = srcK.width, h = srcK.height;
      if (!w || !h || w * h > 4194304) return null;
      const tmp = document.createElement('canvas');
      tmp.width = w; tmp.height = h;
      const cx = tmp.getContext('2d');
      if (!cx) return null;
      cx.drawImage(srcK, 0, 0);
      const img = _g2.call(cx, 0, 0, w, h);
      _acakPiksel(img.data);
      cx.putImageData(img, 0, 0);
      return tmp;
    };
    const _td = HTMLCanvasElement.prototype.toDataURL;
    _tdHook = function (t, q) {
      try {
        const tmp = _salinNoise(this);
        if (tmp) return _td.call(tmp, t, q);
      } catch (e) {}
      return _td.call(this, t, q);
    };
    HTMLCanvasElement.prototype.toDataURL = _tdHook;
    const _tb = HTMLCanvasElement.prototype.toBlob;
    _tbHook = function (cb, t, q) {
      try {
        const tmp = _salinNoise(this);
        if (tmp) return _tb.call(tmp, cb, t, q);
      } catch (e) {}
      return _tb.call(this, cb, t, q);
    };
    HTMLCanvasElement.prototype.toBlob = _tbHook;
    const _acakRp = (args) => {
      try {
        const pix = args[6];
        if (pix && typeof pix !== 'number' && pix.length
            && (pix instanceof Uint8Array || pix instanceof Uint8ClampedArray
                || pix instanceof Int8Array)) _acakPiksel(pix);
      } catch (e) {}
    };
    const _rp = WebGLRenderingContext.prototype.readPixels;
    _rpHook = function () { _rp.apply(this, arguments); _acakRp(arguments); };
    WebGLRenderingContext.prototype.readPixels = _rpHook;
    if (window.WebGL2RenderingContext) {
      const _rp2 = WebGL2RenderingContext.prototype.readPixels;
      _rp2Len = _rp2.length;  // panjang ASLI sebelum ditimpa
      _rp2Hook = function () { _rp2.apply(this, arguments); _acakRp(arguments); };
      WebGL2RenderingContext.prototype.readPixels = _rp2Hook;
    }
    _natLen = {td: _td.length, tb: _tb.length, gid: _g2.length,
               rp: _rp.length, rp2: _rp2Len == null ? 7 : _rp2Len};
  } catch (e) { kirim({extra: 'noise-gagal:' + (e && e.message)}); }
  // ===== [fix 2026-10-13] MASKING hook forge: fungsi terpatch WAJIB tampil native.
  // P0 (fp_profile) hanya menutup fungsi yg terdaftar di SRC-nya; hook forge ini
  // tidak terdaftar -> .toString() membocorkan source -> probe sepele
  // `JSON.stringify.toString().includes('[native code]')` menghubungkan SEMUA
  // attempt (persona apa pun, exit apa pun) sbg SATU browser yang sama —
  // konsisten dgn menu HV anjlok [sms,email,coupon] & 2028 lintas-persona.
  // Rantai: cek _FSRC dulu -> jatuh ke toString P0 (SRC base) -> native asli.
  try {
    const _FSRC = new WeakMap();
    const _fts = Function.prototype.toString;
    Function.prototype.toString = function () {
      if (_FSRC.has(this)) return _FSRC.get(this);
      return _fts.call(this);
    };
    const _m = (fn, nama, len) => {
      _FSRC.set(fn, 'function ' + nama + '() { [native code] }');
      try { Object.defineProperty(fn, 'name', {value: nama, configurable: true}); } catch (e) {}
      try { Object.defineProperty(fn, 'length', {value: len, configurable: true}); } catch (e) {}
      return fn;
    };
    _m(Function.prototype.toString, 'toString', 0);
    _m(JSON.stringify, 'stringify', 3);
    _m(XMLHttpRequest.prototype.open, 'open', 2);
    _m(XMLHttpRequest.prototype.setRequestHeader, 'setRequestHeader', 2);
    _m(XMLHttpRequest.prototype.send, 'send', 1);
    if (window.fetch) _m(window.fetch, 'fetch', 1);
    // [fix 2026-10-13] hook noise render ikut tampil native (panjang = asli)
    if (_tdHook && _natLen) _m(_tdHook, 'toDataURL', _natLen.td);
    if (_tbHook && _natLen) _m(_tbHook, 'toBlob', _natLen.tb);
    if (_gidHook && _natLen) _m(_gidHook, 'getImageData', _natLen.gid);
    if (_rpHook && _natLen) _m(_rpHook, 'readPixels', _natLen.rp);
    if (_rp2Hook && _natLen) _m(_rp2Hook, 'readPixels', _natLen.rp2);
  } catch (e) { kirim({extra: 'mask-gagal:' + (e && e.message)}); }
  } catch (e) {
    kirim({extra: 'FATAL:' + (e && e.message)});
  }
})();