#!/usr/bin/env node
// Reimplementasi persis getRandomSrpVerifier / getSrp dari @protontech/crypto
// (packages/crypto/src/srp: passwords.ts + srp.ts) — memakai bcryptjs yang sama
// (API async hash(), satu-satunya yang menerima prefix $2y$ di v3).
// Pakai: node make_auth.js verifier '{"modulusArmored":"...","password":"...","saltHex":null}'
//        node make_auth.js login '{"modulusArmored":"...","password":"...","saltB64":"...","serverEphemeralB64":"..."}'
const bcrypt = require("bcryptjs");
const crypto = require("crypto");

const SRP_LEN = 256; // byte, dari constants.ts (2048/8)

function expandHash(input) {
    const parts = [];
    for (let i = 0; i < 4; i++) {
        parts.push(crypto.createHash("sha512").update(Buffer.concat([input, Buffer.from([i])])).digest());
    }
    return Buffer.concat(parts);
}

// passwords.ts: hashPassword3(password, salt, modulus)
async function hashPassword3(password, saltBinaryStr, modulus) {
    const saltBinary = Buffer.from(saltBinaryStr + "proton", "utf8"); // utf8StringToUint8Array(`${salt}proton`)
    const saltB64 = bcrypt.encodeBase64(saltBinary, 16);
    const unexpanded = await bcrypt.hash(password, "$2y$10$" + saltB64); // BCRYPT_PREFIX
    return expandHash(Buffer.concat([Buffer.from(unexpanded, "latin1"), modulus]));
}

const le2bi = (buf) => BigInt("0x" + (buf.length ? Buffer.from(buf).reverse().toString("hex") : "0"));
function bi2le(bi, len) {
    let h = bi.toString(16);
    if (h.length % 2) h = "0" + h;
    const b = Buffer.from(h, "hex").reverse();
    const out = Buffer.alloc(len);
    b.copy(out);
    return out;
}
function modExp(base, exp, m) {
    let r = 1n;
    base = ((base % m) + m) % m;
    while (exp > 0n) {
        if (exp & 1n) r = (r * base) % m;
        base = (base * base) % m;
        exp >>= 1n;
    }
    return r;
}
const mod = (a, m) => ((a % m) + m) % m;

// verifyAndGetModulus tanpa verifikasi signature: ambil body base64 dari cleartext signed message
function modulusFromArmored(armored) {
    const head = armored.split("-----BEGIN PGP SIGNATURE-----")[0];
    const idx = head.indexOf("\n\n");
    return Buffer.from(head.slice(idx + 2).replace(/\s+/g, ""), "base64");
}

// srp.ts: getRandomSrpVerifier
async function makeVerifier({ modulusArmored, password, saltHex }) {
    const modulus = modulusFromArmored(modulusArmored);
    const saltBytes = saltHex ? Buffer.from(saltHex, "hex") : crypto.randomBytes(10);
    const hashed = await hashPassword3(password, saltBytes.toString("latin1"), modulus);
    const verifier = bi2le(modExp(2n, le2bi(hashed), le2bi(modulus)), SRP_LEN);
    return { Version: 4, Salt: saltBytes.toString("base64"), Verifier: verifier.toString("base64") };
}

// srp.ts: generateProofs (dipakai getSrp untuk login)
async function makeSrp({ modulusArmored, password, saltB64, serverEphemeralB64 }) {
    const modulusArray = modulusFromArmored(modulusArmored);
    const N = le2bi(modulusArray);
    const saltBytes = Buffer.from(saltB64, "base64");
    const hashed = await hashPassword3(password, saltBytes.toString("latin1"), modulusArray);
    const serverEphemeralArray = Buffer.from(serverEphemeralB64, "base64");
    const g = 2n;
    const gBytes = bi2le(g, SRP_LEN);
    const k = le2bi(expandHash(Buffer.concat([gBytes, modulusArray])));
    const clientSecret = le2bi(crypto.randomBytes(SRP_LEN));
    const clientEphemeral = modExp(g, clientSecret, N);
    const clientEphemeralArray = bi2le(clientEphemeral, SRP_LEN);
    const u = le2bi(expandHash(Buffer.concat([clientEphemeralArray, serverEphemeralArray])));
    const x = le2bi(hashed);
    const kgx = mod(modExp(g, x, N) * mod(k, N), N);
    const exp = mod(u * x + clientSecret, N - 1n);
    const S = modExp(mod(le2bi(serverEphemeralArray) - kgx, N), exp, N);
    const sharedSessionArray = bi2le(S, SRP_LEN);
    const clientProof = expandHash(Buffer.concat([clientEphemeralArray, serverEphemeralArray, sharedSessionArray]));
    const expectedServerProof = expandHash(Buffer.concat([clientEphemeralArray, clientProof, sharedSessionArray]));
    return {
        ClientEphemeral: clientEphemeralArray.toString("base64"),
        ClientProof: clientProof.toString("base64"),
        ExpectedServerProof: expectedServerProof.toString("base64"),
    };
}

(async () => {
    const [mode, arg] = process.argv.slice(2);
    const args = JSON.parse(arg || "{}");
    const out = mode === "verifier" ? await makeVerifier(args)
        : mode === "login" ? await makeSrp(args)
        : (() => { throw new Error("mode: verifier | login"); })();
    process.stdout.write(JSON.stringify(out));
})().catch((e) => {
    console.error("make_auth ERROR:", e.message);
    process.exit(1);
});
