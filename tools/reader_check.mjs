// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// reader_check.mjs - GEA Reader's checks, run in Node against the Python checker's verdicts.
//
// tools/reader_fixtures.py writes a set of dossiers (signed, re-signed after a key hand-over, tampered in each way
// verify_dossier.py names, stored without compression, not a zip at all) and expect.json with what
// verify_dossier.py says of each. This script loads the reader's own script out of gea/web/reader.html - the same
// text a client's browser runs - and checks every fixture with it: the verdict, each check by name and outcome,
// the signer's fingerprint and the key hand-overs must equal Python's. Node 20 or later (Web Crypto, DecompressionStream).
//
// Usage:  python tools/reader_fixtures.py OUT   then   node tools/reader_check.mjs OUT
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const dir = process.argv[2];
if (!dir) { console.error('usage: node tools/reader_check.mjs FIXTURE_DIR'); process.exit(2); }
const html = fs.readFileSync(path.join(root, 'gea', 'web', 'reader.html'), 'utf8');
const m = html.match(/<script id="gea-core">([\s\S]*?)<\/script>/);
if (!m) { console.error('no gea-core script in reader.html'); process.exit(2); }
const ctx = {crypto: globalThis.crypto, TextEncoder, TextDecoder, Blob, Response, DecompressionStream, Uint8Array, Map, Set, BigInt, JSON, Math, Number, String, Array, Object, Error, console};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(m[1] + '\n;globalThis.GEAReader = GEAReader;', ctx);
const R = ctx.GEAReader;
const expect = JSON.parse(fs.readFileSync(path.join(dir, 'expect.json'), 'utf8'));
let fails = 0;
const say = (ok, msg) => { console.log(`${ok ? 'ok ' : 'NO '} ${msg}`); if (!ok) fails++; };

const st = await R.selfTest();
say(st.ok, `self-test: ${st.detail}`);
say(R.canon({b: 'é', a: [1, null, true], c: {z: '"\n', y: '\u007f'}}) === '{"a":[1,null,true],"b":"\\u00e9","c":{"y":"\\u007f","z":"\\"\\n"}}', 'canonical JSON as the program writes it');

for (const [name, py] of Object.entries(expect.fixtures)) {
  const buf = fs.readFileSync(path.join(dir, name));
  const r = await R.check(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), name);
  const js = r.checks.filter(c => c.ok !== null).map(c => [c.check, c.ok]);
  const pyc = py.checks.filter(c => c.check !== 'the trusted timestamp verifies').map(c => [c.check, c.ok]);
  const stricter = (expect.reader_stricter || {})[name];
  const extra = r.checks.filter(c => c.ok === false && !py.checks.some(p => p.check === c.check) && c.check !== stricter).map(c => c.check);
  const same = JSON.stringify(js.filter(([c]) => py.checks.some(p => p.check === c))) === JSON.stringify(pyc);
  const status = stricter ? r.status === 'INVALID' && r.checks.some(c => c.check === stricter && c.ok === false) : r.status === py.status;
  say(status && same && !extra.length && (r.fingerprint || null) === (py.fingerprint || null)
      && JSON.stringify((r.key_chain || []).map(c => [c.old, c.new, c.signed_by_old, c.signed_by_new])) === JSON.stringify((py.key_chain || []).map(c => [c.old, c.new, c.signed_by_old, c.signed_by_new])),
      `${name}: reader ${r.status}, verify_dossier.py ${py.status}` + (stricter ? ` (the reader also requires: ${stricter})` : '') + (same ? '' : ` - checks differ: ${JSON.stringify(js)} vs ${JSON.stringify(pyc)}`) + (extra.length ? ` - the reader alone failed: ${extra}` : ''));
}
const good = await R.check(fs.readFileSync(path.join(dir, 'good.zip')).buffer, 'good.zip');
say(R.whose(good, {fingerprints: [expect.fp1]}, '').state === 'match', 'a dossier signed with the key the reader was given is the site\'s');
say(R.whose(good, {fingerprints: [expect.fp2]}, '').state === 'other', 'a dossier signed with another key is not');
say(R.whose(good, null, expect.fp1.toUpperCase().replace(/ /g, '')).state === 'match', 'a fingerprint typed by the client, any case and spacing');
const rot = await R.check(fs.readFileSync(path.join(dir, 'rotated.zip')).buffer, 'rotated.zip');
say(R.whose(rot, {fingerprints: [expect.fp1]}, '').state === 'via-rotation', 'a dossier signed after a key hand-over is the site\'s through the hand-over its old key signed');
console.log(fails ? `READER CHECK: ${fails} failure(s)` : 'READER CHECK: every fixture reads as verify_dossier.py reads it');
process.exit(fails ? 1 : 0);
