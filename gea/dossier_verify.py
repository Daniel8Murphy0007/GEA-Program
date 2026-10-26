"""Check a GEA dossier without GEA: its signature, and every file it carries against the hash it was signed with.

Usage:  python verify_dossier.py DOSSIER.zip [--sources FOLDER] [--anchors ROOTS.pem]

A dossier is a zip holding `manifest.json` (every file's path, size and SHA-256, the well, the time, the signer's
public key), `signature.json` (an Ed25519 signature over the manifest's exact bytes), the files themselves, this
script and a README. The check:

1. the signature over `manifest.json` verifies against the public key it names (Ed25519, RFC 8032);
2. every file the manifest lists is in the zip, with the size and SHA-256 the manifest gives;
3. nothing else in the zip claims to be covered (this script and the README are not, and say so);
4. with --sources, each source file the manifest lists by hash (the drilling records, which a dossier may hold by
   hash alone) is found in that folder and hashed;
5. when the dossier carries `timestamp.tsr`, the RFC 3161 token a time-stamping authority signed over the SHA-256 of
   the dossier's signature: the token, the authority's signature and certificate, and its chain to a root this
   computer trusts (or one given with --anchors) - the time it gives, against the signing machine's own;
6. when the site has changed its signing key, the dossier carries `keys/rotations.json`: each statement that
   handed the signature from one key to the next, signed by the retired key and by its successor. Each signature
   is checked, the chain must end at the key that signed this dossier, and the fingerprints along it are printed,
   so a client who was given an earlier fingerprint can follow the chain to this one.

A valid signature shows the files are byte-for-byte those the key's holder signed. Who holds the key is a separate
question: compare the fingerprint printed here with the one the site gave you, by a channel other than this file.
The script needs Python 3.8 or later and nothing else.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile

try:                                                         # inside the program
    from .ed25519 import verify as _ed_verify, fingerprint as _fingerprint
    from .timestamp import ts_verify as _ts_verify, ts_pem_certs as _ts_pem, ts_system_anchors as _ts_anchors
except ImportError:                                          # inside a dossier: the RFC 8032 and RFC 3161 functions sit above this text
    _ed_verify, _fingerprint = verify, fingerprint            # noqa: F821
    _ts_verify, _ts_pem, _ts_anchors = ts_verify, ts_pem_certs, ts_system_anchors   # noqa: F821

UNCOVERED = ('verify_dossier.py', 'README.txt', 'timestamp.tsr')     # the checker, the README, and the authority's own signed token


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def check(zip_path: str, sources_dir: str = '', anchors=None) -> dict:
    out = {'dossier': os.path.abspath(zip_path), 'checks': [], 'status': 'INVALID'}

    def add(name, ok, detail):
        out['checks'].append({'check': name, 'ok': bool(ok), 'detail': detail})
        return ok
    try:
        z = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        add('the file is a dossier', False, f'not a zip file: {e}')
        return out
    with z:
        names = set(z.namelist())
        if not add('the file is a dossier', 'manifest.json' in names and 'signature.json' in names,
                   'manifest.json and signature.json present' if 'manifest.json' in names and 'signature.json' in names else 'no manifest.json or signature.json'):
            return out
        seen, dupes = set(), []
        for n in z.namelist():                               # a second entry under one name: another zip reader may show the other copy
            if n in seen and n not in dupes:
                dupes.append(n)
            seen.add(n)
        if dupes:
            add('every name in the zip is used once', False, 'two entries share a name: ' + ', '.join(dupes[:5]))
        mb = z.read('manifest.json')
        try:
            sig = json.loads(z.read('signature.json').decode('utf-8'))
            man = json.loads(mb.decode('utf-8'))
        except ValueError as e:
            add('the manifest and signature read', False, str(e))
            return out
        pub = bytes.fromhex(sig.get('public_key', ''))
        out.update({'fingerprint': _fingerprint(pub) if len(pub) == 32 else None, 'dossier_id': man.get('dossier_id'),
                    'signed_utc': man.get('created_utc'), 'scope': man.get('scope'), 'generator': man.get('generator'),
                    'n_files': len(man.get('files', []))})
        add('the signature is Ed25519', sig.get('algorithm') == 'Ed25519', sig.get('algorithm'))
        add('the signature names the manifest\'s signer', (man.get('signer') or {}).get('public_key') == sig.get('public_key'),
            'the public key in signature.json is the one manifest.json names' if (man.get('signer') or {}).get('public_key') == sig.get('public_key')
            else 'signature.json and manifest.json name different keys')
        add('the manifest hash', sig.get('manifest_sha256') == _sha(mb), f"sha256 {_sha(mb)}")
        try:
            good = _ed_verify(pub, mb, bytes.fromhex(sig.get('signature', '')))
        except ValueError:
            good = False
        add('the signature verifies', good, ('the manifest is byte-for-byte what was signed' if good else 'the manifest was changed after signing, or was signed by another key'))
        bad, missing = [], []
        for f in man.get('files', []):
            if f['path'] not in names:
                missing.append(f['path']); continue
            b = z.read(f['path'])
            if len(b) != f['bytes'] or _sha(b) != f['sha256']:
                bad.append(f['path'])
        add('every file is present', not missing, 'all present' if not missing else 'missing: ' + ', '.join(missing[:10]))
        add('every file matches its hash', not bad, f"{len(man.get('files', [])) - len(bad) - len(missing)} file(s) match" if not bad else 'changed: ' + ', '.join(bad[:10]))
        listed = {f['path'] for f in man.get('files', [])} | {'manifest.json', 'signature.json'}
        extra = sorted(n for n in names - listed if not n.endswith('/') and n not in UNCOVERED)
        add('nothing unsigned rides along', not extra, 'only this script, the README and a time-stamp token are outside the signature' if not extra else 'not covered by the signature: ' + ', '.join(extra[:10]))
        if 'timestamp.tsr' in names:
            try:
                tsr = _ts_verify(z.read('timestamp.tsr'), hashlib.sha256(bytes.fromhex(sig.get('signature', ''))).digest(), 'sha256', anchors=anchors)
            except ValueError as e:
                tsr = {'status': 'INVALID', 'checks': [{'check': 'the token reads', 'ok': False, 'detail': str(e)}], 'gen_time': None}
            out['timestamp'] = {k: tsr.get(k) for k in ('status', 'gen_time', 'tsa', 'root', 'chain_detail', 'accuracy_s', 'serial', 'policy')}
            out['timestamp']['checks'] = tsr.get('checks')
            bad = next((c for c in tsr.get('checks') or [] if not c['ok']), None)
            skew = None
            if tsr.get('gen_time') and man.get('created_utc'):
                from datetime import datetime as _dt
                try:
                    skew = (_dt.strptime(tsr['gen_time'][:19], '%Y-%m-%dT%H:%M:%S') - _dt.strptime(str(man['created_utc'])[:19], '%Y-%m-%dT%H:%M:%S')).total_seconds()
                except ValueError:
                    skew = None
            out['timestamp']['after_signing_s'] = skew
            add('the trusted timestamp verifies', tsr['status'] in ('TRUSTED', 'VERIFIED'),
                (f"{tsr['tsa']} vouches that this signature existed at {tsr['gen_time']}"
                 + (f" ({abs(skew):,.0f} s {'after' if skew >= 0 else 'BEFORE'} the signing machine's clock)" if skew is not None else '')
                 + (f"; its chain reaches {tsr['root']}, a root this computer trusts" if tsr['status'] == 'TRUSTED' else
                    f"; its chain does not reach a root this computer trusts ({tsr.get('chain_detail')}): the time is that authority's word only for whoever trusts it"))
                if tsr['status'] in ('TRUSTED', 'VERIFIED') else f"the token does not verify ({bad['check']}: {bad['detail']})" if bad else 'the token does not verify')
        if 'keys/rotations.json' in names:
            chain = []
            try:
                rots = json.loads(z.read('keys/rotations.json').decode('utf-8')).get('rotations', [])
            except ValueError:
                rots = None
            if rots is None:
                add('the key rotations verify', False, 'keys/rotations.json does not read')
            else:
                good_all = True
                for r_ in rots:
                    st = r_.get('statement') or {}
                    b = json.dumps(st, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
                    old = st.get('old') or {}
                    new = st.get('new') or {}
                    try:
                        ok_new = _ed_verify(bytes.fromhex(new.get('public_key', '')), b, bytes.fromhex(r_.get('new_signature') or ''))
                    except ValueError:
                        ok_new = False
                    if r_.get('old_signature') and old.get('public_key'):
                        try:
                            ok_old = _ed_verify(bytes.fromhex(old['public_key']), b, bytes.fromhex(r_['old_signature']))
                        except ValueError:
                            ok_old = False
                    else:
                        ok_old = None                            # the old key was lost: the step rests on the site's word
                    good_all = good_all and ok_new and ok_old is not False
                    chain.append({'utc': st.get('utc'), 'old': old.get('fingerprint') or st.get('old_fingerprint'), 'new': new.get('fingerprint'),
                                  'reason': st.get('reason', ''), 'signed_by_old': ok_old, 'signed_by_new': ok_new})
                ends = bool(chain) and (rots[-1].get('statement') or {}).get('new', {}).get('public_key') == sig.get('public_key')
                links = all(chain[i]['new'] == chain[i + 1]['old'] for i in range(len(chain) - 1))
                out['key_chain'] = chain
                add('the key rotations verify', good_all and ends and links,
                    (f"{len(chain)} rotation(s), each signed by the new key" + ('' if all(c['signed_by_old'] for c in chain) else
                     '; one or more not signed by the retired key (it was lost), so that step rests on the site\'s word')
                     + ', ending at the key that signed this dossier') if good_all and ends and links else
                    ('a rotation statement does not verify' if not good_all else
                     'the chain does not end at the key that signed this dossier' if not ends else 'the rotations do not follow one another'))
        srcs = man.get('sources', [])
        if sources_dir:
            rows = []
            for s_ in srcs:
                cand = [os.path.join(sources_dir, s_['path']), os.path.join(sources_dir, os.path.basename(s_['path']))]
                p = next((c for c in cand if os.path.isfile(c)), None)
                if p is None:
                    rows.append((s_['path'], 'MISSING'))
                    continue
                h = hashlib.sha256()
                with open(p, 'rb') as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b''):
                        h.update(chunk)
                rows.append((s_['path'], 'MATCH' if h.hexdigest() == s_['sha256'] else 'DIFFERS'))
            out['sources'] = [{'path': a, 'status': b} for a, b in rows]
            add('the source files match', all(b == 'MATCH' for _, b in rows), f"{sum(b == 'MATCH' for _, b in rows)} of {len(rows)} match")
    out['status'] = 'VALID' if all(c['ok'] for c in out['checks']) else 'INVALID'
    return out


def text(r: dict) -> str:
    L = [f"{r['status']}: {r['dossier']}"]
    if r.get('dossier_id'):
        sc = r.get('scope') or {}
        L.append(f"  dossier {r['dossier_id']} - {sc.get('display') or sc.get('kind', '')}, signed {r.get('signed_utc')}, {r.get('n_files')} file(s)")
    for c in r['checks']:
        L.append(f"  {'ok ' if c['ok'] else 'NO '} {c['check']}: {c['detail']}")
    for s_ in r.get('sources') or []:
        L.append(f"      {s_['status']:<8} {s_['path']}")
    if not (r.get('timestamp') or {}).get('gen_time') and r.get('dossier_id'):
        L.append("  no trusted timestamp: the time stated is the signing machine's own clock")
    for c in r.get('key_chain') or []:
        L.append(f"  key rotation {c['utc']}: {c['old']} -> {c['new']} ({c['reason']}); "
                 + ('signed by both keys' if c['signed_by_old'] and c['signed_by_new'] else
                    'signed by the new key only: the old one was lost' if c['signed_by_old'] is None and c['signed_by_new'] else 'NOT VALID'))
    if r.get('fingerprint'):
        L.append(f"  signer's key fingerprint: {r['fingerprint']}")
        L.append("  compare it with the fingerprint the site gave you separately; a valid signature by an unknown key proves nothing about who signed"
                 + ("; or, if the site gave you an earlier fingerprint, find it in the rotations above" if r.get('key_chain') else ''))
    return '\n'.join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description='Check a GEA dossier: its signature and every file it carries.')
    ap.add_argument('dossier')
    ap.add_argument('--sources', default='', help='a folder holding the source files the dossier lists by hash')
    ap.add_argument('--anchors', default='', help='a PEM file of root certificates to trust for the timestamp, beside this computer\'s own')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    anchors = None
    if a.anchors:
        with open(a.anchors, 'rb') as fh:
            anchors = _ts_anchors() + _ts_pem(fh.read())
    r = check(a.dossier, a.sources, anchors)
    print(json.dumps(r, indent=1) if a.json else text(r))
    return 0 if r['status'] == 'VALID' else 1


if __name__ == '__main__':
    sys.exit(main())
