"""The signed dossier: a well's (or the pad's) record in one file that anyone can check without this program.

A dossier is a zip:

- `manifest.json` - the well or pad, the time, the program and its version, and every file the dossier carries
  with its size and SHA-256: the reports as they stand (with the history version each one is), the declarations
  behind them (the well's identity and position, its survey, rates and AFE, runs, penetrator, operations,
  catalogues, the fleet, the contract's terms with the invoices and daily reports attached, the rig-time targets
  and casing), the invoice cross-check and the rig time as they read at signing (`results/`), the audit log, and - by hash, or embedded when asked - the source records the reports
  were made from; the audit log's hash chain (every line chained to the one before, across its rotated segments)
  and its length; the previous dossier's id and manifest hash, so the dossiers of a workspace form one chain;
- `signature.json` - an Ed25519 signature (RFC 8032) over the manifest's exact bytes, with the public key;
- `verify_dossier.py` - a standalone checker (Python 3.8+, nothing else): the signature, every file's hash,
  and, given the folder, the source records;
- `README.txt`.

The key is the site's own: an Ed25519 seed made on this machine the first time a dossier is signed, kept in
`keys/dossier_ed25519.json` in the workspace (outside every report, export and evidence pack), and never sent
anywhere. Its fingerprint is printed on the Reports tab and in every check, for the site to give its clients by
another channel. Whoever holds the file can sign as the site, so it is protected like any key.

What a valid dossier shows: the files are byte-for-byte those the key's holder signed at the time stated. What it
does not show: that they are correct. Inside the workspace a check also says whether the source records still
match, whether the audit log still holds the lines the dossier chained (an earlier line rewritten breaks it), and
whether the dossier is in this workspace's ledger and signed with its key.
"""
from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import ed25519 as ED
from . import dossier_verify as DV

KEY_FILE = os.path.join('keys', 'dossier_ed25519.json')
KEY_HISTORY = os.path.join('keys', 'dossier_key_history.json')      # public keys and rotation statements only
STATEMENT = ('Signed with the site key named below: the files listed here are byte-for-byte those the program held when it signed, at the '
             'time stated. A valid signature shows they have not changed since; it does not show that they are correct.')
_LOCK = threading.Lock()


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _sha_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# -- the key ---------------------------------------------------------------------------------------------
def key_path(ws) -> str:
    return os.path.join(ws.path, KEY_FILE)


def key_info(ws) -> Optional[dict]:
    p = key_path(ws)
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as f:
        k = json.load(f)
    return {'public_key': k['public_key'], 'fingerprint': k['fingerprint'], 'created_utc': k['created_utc'], 'algorithm': 'Ed25519'}


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def key_history(ws) -> dict:
    """The site's former keys (public halves only) and the rotation statements that retired them, oldest first."""
    p = os.path.join(ws.path, KEY_HISTORY)
    if not os.path.isfile(p):
        return {'schema': 1, 'keys': [], 'rotations': []}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def _save_history(ws, h: dict) -> None:
    p = os.path.join(ws.path, KEY_HISTORY)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(h, f, indent=1)
    os.replace(p + '.tmp', p)


def _new_key_file(ws) -> dict:
    seed = os.urandom(32)
    pub = ED.public_key(seed)
    rec = {'algorithm': 'Ed25519', 'seed': seed.hex(), 'public_key': pub.hex(), 'fingerprint': ED.fingerprint(pub), 'created_utc': _utc(),
           'note': 'The site signing key. Whoever holds this file can sign dossiers as this site: keep it with the workspace, never send it.'}
    p = key_path(ws)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1)
    try:
        os.chmod(p + '.tmp', 0o600)
    except OSError:
        pass
    os.replace(p + '.tmp', p)
    return rec


def _rotation(ws, actor: str, old: Optional[dict], old_seed: Optional[bytes], new: dict, new_seed: bytes, reason: str) -> dict:
    """The statement that hands the site's signature from one key to the next, signed by the old key (when it is
    still held) and by the new one; recorded in the key history and carried in every later dossier."""
    st = {'kind': 'GEA signing key rotation', 'utc': _utc(), 'by': actor, 'workspace': ws.manifest.get('name') or '', 'reason': reason,
          'old': {k: old[k] for k in ('public_key', 'fingerprint', 'created_utc')} if old else None,
          'new': {k: new[k] for k in ('public_key', 'fingerprint', 'created_utc')},
          'meaning': ('Every dossier signed with the new key from this time on is signed by the site that held the old key. Dossiers signed '
                      'with the old key before this time stay as they were signed; one signed with it after this time is not the site\'s.')}
    b = _canon(st)
    return {'statement': st, 'statement_sha256': hashlib.sha256(b).hexdigest(),
            'old_signature': ED.sign(old_seed, b).hex() if (old and old_seed) else None,
            'new_signature': ED.sign(new_seed, b).hex()}


def ensure_key(ws, actor: str) -> dict:
    lost = None
    with _LOCK:
        info = key_info(ws)
        if info:
            return info
        h = key_history(ws)
        signed_with = [e.get('fingerprint') for e in ledger(ws) if e.get('fingerprint')]
        known = [k for k in h['keys'] if not k.get('retired_utc')]
        if known or signed_with:                       # the site had a key and its file is gone: say so in the history
            lost = known[-1] if known else {'public_key': None, 'fingerprint': signed_with[-1], 'created_utc': None}
        rec = _new_key_file(ws)
        if lost is not None:
            rot = _rotation(ws, actor, lost if lost.get('public_key') else None, None, rec, bytes.fromhex(rec['seed']),
                            'the key file was not found when the next dossier was signed; the former key is retired unsigned')
            rot['statement']['old_fingerprint'] = lost['fingerprint']
            b = _canon(rot['statement'])
            rot['statement_sha256'] = hashlib.sha256(b).hexdigest()
            rot['new_signature'] = ED.sign(bytes.fromhex(rec['seed']), b).hex()
            for k in h['keys']:
                if k['fingerprint'] == lost['fingerprint'] and not k.get('retired_utc'):
                    k.update({'retired_utc': rot['statement']['utc'], 'reason': 'key file not found', 'successor': rec['fingerprint']})
            if not any(k['fingerprint'] == lost['fingerprint'] for k in h['keys']):
                h['keys'].append({'public_key': lost.get('public_key'), 'fingerprint': lost['fingerprint'], 'created_utc': lost.get('created_utc'),
                                  'retired_utc': rot['statement']['utc'], 'reason': 'key file not found', 'successor': rec['fingerprint']})
            h['rotations'].append(rot)
        h['keys'].append({'public_key': rec['public_key'], 'fingerprint': rec['fingerprint'], 'created_utc': rec['created_utc'], 'retired_utc': None})
        _save_history(ws, h)
    ws.audit(actor, 'dossier.key.create', {'public_key': rec['public_key'], 'fingerprint': rec['fingerprint'],
                                           **({'replaces_lost': lost['fingerprint']} if lost else {})})
    return key_info(ws)


def rotate_key(ws, actor: str, reason: str) -> dict:
    """Retire the site's key and start a new one. The old key signs a statement naming the new one (so a client who
    trusts the old fingerprint can trust the new through it), the new key signs it too, and the old seed is
    destroyed: dossiers it signed before now still check out, and nothing can be signed with it again."""
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('say why the key is being retired (for example: a scheduled change, the laptop was lost, the file was copied)')
    with _LOCK:
        old = key_info(ws)
        if not old:
            raise ValueError('this workspace has no signing key yet: the first dossier makes one')
        old_seed = _seed(ws)
        h = key_history(ws)
        if not any(k['fingerprint'] == old['fingerprint'] for k in h['keys']):     # a key made before the history was kept
            h['keys'].append({'public_key': old['public_key'], 'fingerprint': old['fingerprint'], 'created_utc': old['created_utc'], 'retired_utc': None})
        rec = _new_key_file(ws)
        rot = _rotation(ws, actor, old, old_seed, rec, bytes.fromhex(rec['seed']), reason)
        for k in h['keys']:
            if k['fingerprint'] == old['fingerprint']:
                k.update({'retired_utc': rot['statement']['utc'], 'reason': reason, 'successor': rec['fingerprint']})
        h['keys'].append({'public_key': rec['public_key'], 'fingerprint': rec['fingerprint'], 'created_utc': rec['created_utc'], 'retired_utc': None})
        h['rotations'].append(rot)
        _save_history(ws, h)
        del old_seed
    ws.audit(actor, 'dossier.key.rotate', {'old': old['fingerprint'], 'new': rec['fingerprint'], 'reason': reason,
                                           'statement_sha256': rot['statement_sha256']})
    return {'old': old, 'new': key_info(ws), 'rotation': rot}


def _seed(ws) -> bytes:
    with open(key_path(ws), encoding='utf-8') as f:
        return bytes.fromhex(json.load(f)['seed'])


# -- the audit chain -------------------------------------------------------------------------------------
def audit_segments(ws) -> List[str]:
    cur = ws.audit_path
    segs = sorted(p for p in glob.glob(cur + '.*') if not p.endswith('.tmp'))
    return segs + ([cur] if os.path.isfile(cur) else [])


def chain_lines(parts: List[tuple], upto: Optional[int] = None) -> dict:
    """[(segment name, bytes)] chained line by line: h = SHA-256(h_prev + line), from 32 zero bytes."""
    h = b'\x00' * 32
    n = 0
    segs = []
    for name, b in parts:
        k = 0
        for line in b.splitlines(keepends=True):
            if upto is not None and n >= upto:
                break
            h = hashlib.sha256(h + line).digest()
            n += 1
            k += 1
        segs.append({'name': name, 'lines': k})
        if upto is not None and n >= upto:
            break
    return {'lines': n, 'head': h.hex(), 'segments': segs}


def audit_chain(ws, upto: Optional[int] = None) -> dict:
    """The audit log's lines chained in order across its rotated segments: h = SHA-256(h_prev + line). With `upto`, the
    chain over the first `upto` lines only (to re-check a dossier's)."""
    parts = []
    for p in audit_segments(ws):
        with open(p, 'rb') as f:
            parts.append((os.path.basename(p), f.read()))
    return chain_lines(parts, upto)


# -- the ledger ------------------------------------------------------------------------------------------
def _dir(ws, *p) -> str:
    return os.path.join(ws.reports_dir, 'dossiers', *p)


def ledger(ws) -> List[dict]:
    p = _dir(ws, 'ledger.jsonl')
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


# -- what goes in ----------------------------------------------------------------------------------------
def _gather(ws, well_id: Optional[str]) -> Dict[str, List[tuple]]:
    """{role: [(absolute path, path in the dossier)]} for a well, or the pad when no well is named."""
    rep = ws.reports_dir
    out: Dict[str, List[tuple]] = {'report': [], 'declaration': [], 'audit': [], 'source': []}
    wells = [ws.well(well_id)] if well_id else ws.wells()

    def files_in(d, arc):
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                p = os.path.join(d, n)
                if os.path.isfile(p) and not n.endswith('.tmp'):
                    out_list.append((p, arc + '/' + n))
    out_list = out['report']
    if not well_id:
        files_in(rep, 'reports')
    for w in wells:
        files_in(os.path.join(rep, 'wells', w['id']), f"reports/wells/{w['id']}")
    out_list = out['declaration']
    for w in wells:
        wd = os.path.join(ws.path, 'wells', w['id'])
        if os.path.isfile(os.path.join(wd, 'well.json')):
            out_list.append((os.path.join(wd, 'well.json'), f"declarations/wells/{w['id']}/well.json"))
        files_in(os.path.join(wd, 'trajectory'), f"declarations/wells/{w['id']}/trajectory")
        files_in(os.path.join(wd, 'plan'), f"declarations/wells/{w['id']}/plan")
    for rel in (('economics', 'costs.json'), ('drillstring', 'runs.json'), ('completion', 'esp.json'), ('seismicity', 'operations.json'),
                ('seismicity', 'catalogs.json'), ('seismicity', 'settings.json'), ('config', 'fleet.json'), ('workspace.json',),
                ('economics', 'invoices', 'terms.json'), ('economics', 'invoices', 'files.json'), ('drillstring', 'rig_time.json'),
                ('drillstring', 'casing.json'), ('wellpath', 'collision.json'), ('wellpath', 'survey_program.json')):
        p = os.path.join(ws.path, *rel)
        if os.path.isfile(p):
            out_list.append((p, 'declarations/' + '/'.join(rel)))
    files_in(os.path.join(ws.path, 'seismicity', 'catalogs'), 'declarations/seismicity/catalogs')
    for w in wells:                                        # the contractor's invoices and daily reports, as attached
        files_in(os.path.join(ws.path, 'economics', 'invoices', w['id']), f"declarations/economics/invoices/{w['id']}")
    for p in audit_segments(ws):
        out['audit'].append((p, 'audit/' + os.path.basename(p)))
    for w in wells:
        sd = os.path.join(ws.path, 'wells', w['id'], 'source')
        if os.path.isdir(sd):
            for n in sorted(os.listdir(sd)):
                p = os.path.join(sd, n)
                if os.path.isfile(p):
                    out['source'].append((p, f"sources/wells/{w['id']}/{n}"))
    return out


def _results(ws, well_id: Optional[str]) -> List[tuple]:
    """The invoice cross-check of each well with invoices or reports attached, the rig time of each well with a drilling
    record, the collision check and each surveyed well against its plan, as JSON computed when the dossier is signed (a
    check that cannot run is left out, and says why)."""
    out = []
    wells = [well_id] if well_id else [w['id'] for w in ws.wells()]
    try:
        from . import invoice_check as IC
        if IC.terms(ws):
            for wid in wells:
                if IC.files(ws, wid):
                    r = IC.check(ws, wid)
                    out.append((f'results/invoice_check/{wid}.json', json.dumps(r, indent=1, sort_keys=True, default=str).encode('utf-8')))
    except Exception as e:
        out.append(('results/invoice_check/not_run.txt', f'the invoice check did not run: {type(e).__name__}: {e}\n'.encode('utf-8')))
    try:
        from . import rig_state as RS
        p = RS.pad(ws)
        ws_ = [w for w in p['wells'] if w['well_id'] in wells]
        if ws_:
            p = {**p, 'wells': ws_}
            out.append(('results/rig_time.json', json.dumps(p, indent=1, sort_keys=True, default=str).encode('utf-8')))
    except Exception as e:
        out.append(('results/rig_time_not_run.txt', f'rig time did not run: {type(e).__name__}: {e}\n'.encode('utf-8')))
    try:
        from . import collision as CO
        from . import wellplan as WPL
        c = CO.page(ws)
        if c['pairs']:
            c = {**c, 'pairs': [{k: v for k, v in q.items() if k != 'profile'} for q in c['pairs']
                                if not well_id or well_id in (q['reference'], q['offset'])]}
            out.append(('results/collision.json', json.dumps(c, indent=1, sort_keys=True, default=str).encode('utf-8')))
        for wid in wells:
            w = ws.well(wid)
            if w.get('plan') and w.get('trajectory'):
                r = WPL.page(ws, wid)
                if r.get('comparison'):
                    out.append((f'results/plan_vs_actual/{wid}.json', json.dumps({k: v for k, v in r['comparison'].items() if k != 'views'},
                                                                                 indent=1, sort_keys=True, default=str).encode('utf-8')))
    except Exception as e:
        out.append(('results/collision_not_run.txt', f'the collision check did not run: {type(e).__name__}: {e}\n'.encode('utf-8')))
    return out


def _history_versions(ws) -> Dict[str, dict]:
    """The latest history entry of each report file, by its path under reports/, with that version's folder."""
    from . import report_history as RH
    best: Dict[str, dict] = {}
    for e in RH.entries(ws.reports_dir):
        fam_dir = '/'.join(e['family'].split('/')[:-1])
        for f in e['files']:
            key = (fam_dir + '/' if fam_dir else '') + f['name']
            if key not in best:
                best[key] = {'dir': e['dir'], 'utc': e['utc'], 'sha256': f['sha256'], 'report_id': e.get('report_id', '')}
    return best


def verifier_source() -> str:
    """The standalone checker that rides in every dossier: the RFC 8032 functions, then the checker."""
    here = os.path.dirname(__file__)
    with open(os.path.join(here, 'ed25519.py'), encoding='utf-8') as f:
        ed = f.read()
    with open(os.path.join(here, 'dossier_verify.py'), encoding='utf-8') as f:
        dv = f.read()
    dv = '\n'.join(l for l in dv.splitlines() if l.strip() != 'from __future__ import annotations')
    return ('#!/usr/bin/env python3\n# verify_dossier.py - written by GEA-Program into every dossier. Python 3.8+, standard library only.\n'
            + ed + '\n\n# ---- the checker ----\n' + dv + '\n')


README = """GEA dossier
===========

This zip is a signed record. To check it you need Python 3.8 or later and nothing else:

    python verify_dossier.py {name}

(add --sources FOLDER to check the source records it lists by hash against your copies.)

It prints VALID when the signature over manifest.json verifies and every file listed there is present with the
size and SHA-256 the manifest gives, and INVALID otherwise, with the reason.

It also prints the signer's key fingerprint. A valid signature shows the files are byte-for-byte those the key's
holder signed; compare the fingerprint with the one the site gave you by another channel to know who that is.
This README and verify_dossier.py are not covered by the signature; manifest.json and every file it lists are.
"""


def build(ws, actor: str, version: str, well_id: Optional[str] = None, include_sources: bool = False) -> dict:
    """Sign a dossier for a well, or for the pad (every well and the site reports) when no well is named."""
    if well_id and not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')
    key = ensure_key(ws, actor)
    seed = _seed(ws)
    g = _gather(ws, well_id)
    if not g['report']:
        raise ValueError('nothing to sign yet: write the reports first (the Reports tab)')
    hist = _history_versions(ws)
    files, payload = [], []
    for role in ('report', 'declaration', 'audit'):
        for p, arc in g[role]:
            with open(p, 'rb') as f:
                b = f.read()
            row = {'path': arc, 'role': role, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest(),
                   'workspace_path': os.path.relpath(p, ws.path).replace(os.sep, '/')}
            if role == 'report':
                hv = hist.get(arc[len('reports/'):])
                if hv and hv['sha256'] == row['sha256']:
                    row['history'] = {'dir': hv['dir'], 'written_utc': hv['utc'], 'report_id': hv['report_id']}
            files.append(row); payload.append((arc, b))
    for arc, b in _results(ws, well_id):                   # the checks as they read at signing
        files.append({'path': arc, 'role': 'result', 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest(), 'workspace_path': None})
        payload.append((arc, b))
    kh = key_history(ws)
    if kh['rotations']:                                  # the key's lineage, so a client who knows an earlier fingerprint can follow it
        b = json.dumps({'schema': 1, 'rotations': kh['rotations'],
                        'how': 'each statement is canonical JSON (sorted keys, no spaces, ASCII); old_signature is the retired key\'s Ed25519 '
                               'signature over those bytes, new_signature the new key\'s'}, indent=1, sort_keys=True).encode('utf-8')
        files.append({'path': 'keys/rotations.json', 'role': 'key', 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest(),
                      'workspace_path': KEY_HISTORY.replace(os.sep, '/')})
        payload.append(('keys/rotations.json', b))
    sources = []
    for p, arc in g['source']:
        row = {'path': arc, 'bytes': os.path.getsize(p), 'sha256': _sha_file(p), 'embedded': bool(include_sources),
               'workspace_path': os.path.relpath(p, ws.path).replace(os.sep, '/')}
        sources.append(row)
        if include_sources:
            with open(p, 'rb') as f:
                b = f.read()
            files.append({'path': arc, 'role': 'source', 'bytes': len(b), 'sha256': row['sha256'], 'workspace_path': row['workspace_path']})
            payload.append((arc, b))
    with _LOCK:
        led = ledger(ws)
        prev = led[-1] if led else None
        if well_id:
            w = ws.well(well_id)
            scope = {'kind': 'well', 'well_id': well_id, 'display': w.get('display') or well_id, 'identity': w.get('identity') or {}}
        else:
            scope = {'kind': 'pad', 'display': ws.manifest.get('name') or 'the pad', 'wells': [w['id'] for w in ws.wells()]}
        did = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
        man = {'schema': 1, 'kind': 'GEA dossier', 'dossier_id': did, 'created_utc': _utc(), 'created_by': actor,
               'generator': {'program': 'GEA-Program', 'version': version}, 'workspace': {'name': ws.manifest.get('name') or ''},
               'scope': scope, 'statement': STATEMENT, 'files': files, 'sources': sources,
               'audit_chain': chain_lines([(arc.split('/')[-1], b) for arc, b in payload if arc.startswith('audit/')]),
               'previous': ({'dossier_id': prev['dossier_id'], 'manifest_sha256': prev['manifest_sha256']} if prev else None),
               'signer': {'algorithm': 'Ed25519', 'public_key': key['public_key'], 'fingerprint': key['fingerprint']}}
        mb = json.dumps(man, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        sig = ED.sign(seed, mb)
        sigj = {'algorithm': 'Ed25519', 'public_key': key['public_key'], 'fingerprint': key['fingerprint'], 'signature': sig.hex(),
                'signed': 'manifest.json', 'manifest_sha256': hashlib.sha256(mb).hexdigest()}
        label = (well_id or 'pad')
        out_dir = _dir(ws, label)
        os.makedirs(out_dir, exist_ok=True)
        name = f'{did}_{label}_dossier.zip'
        zp = os.path.join(out_dir, name)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json', mb)
            z.writestr('signature.json', json.dumps(sigj, indent=1))
            for arc, b in payload:
                z.writestr(arc, b)
            z.writestr('verify_dossier.py', verifier_source())
            z.writestr('README.txt', README.format(name=name))
        with open(zp + '.tmp', 'wb') as f:
            f.write(buf.getvalue())
        os.replace(zp + '.tmp', zp)
        entry = {'dossier_id': did, 'utc': man['created_utc'], 'scope': scope['kind'], 'well_id': well_id, 'display': scope['display'],
                 'path': os.path.relpath(zp, ws.reports_dir).replace(os.sep, '/'), 'manifest_sha256': sigj['manifest_sha256'],
                 'zip_sha256': _sha_file(zp), 'bytes': os.path.getsize(zp), 'n_files': len(files), 'n_sources': len(sources),
                 'sources_embedded': bool(include_sources), 'fingerprint': key['fingerprint'],
                 'previous': man['previous']['dossier_id'] if man['previous'] else None, 'by': actor}
        with open(_dir(ws, 'ledger.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, sort_keys=True) + '\n')
    ws.audit(actor, 'dossier.sign', {k: entry[k] for k in ('dossier_id', 'scope', 'well_id', 'path', 'manifest_sha256', 'n_files', 'n_sources', 'fingerprint')})
    return entry


def verify(zip_path: str, ws=None, sources_dir: str = '') -> dict:
    """The standalone check, and inside a workspace the four more: this site's key, its ledger, its source records
    as they are now, and its audit log still holding the lines the dossier chained."""
    r = DV.check(zip_path, sources_dir)
    if ws is None or not r.get('dossier_id'):
        return r
    with zipfile.ZipFile(zip_path) as z:
        man = json.loads(z.read('manifest.json').decode('utf-8'))
    site = key_info(ws)
    led = {e['dossier_id']: e for e in ledger(ws)}
    ws_checks = []

    def add(name, ok, detail):
        ws_checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    pub = man['signer']['public_key']
    former = next((k for k in key_history(ws)['keys'] if k.get('public_key') == pub and k.get('retired_utc')), None)
    if site and site['public_key'] == pub:
        add("signed with this site's key", True, f"this site's key is {site['fingerprint']}")
    elif former:
        before = str(man.get('created_utc') or '') <= former['retired_utc']
        add("signed with this site's key", before,
            (f"a former key of this site ({former['fingerprint']}), retired {former['retired_utc']}; this dossier was signed before that" if before else
             f"signed {man.get('created_utc')} with a key this site retired {former['retired_utc']} ({former.get('reason', '')}): not the site's"))
    else:
        add("signed with this site's key", False, f"this site's key is {site['fingerprint']}" if site else 'this workspace has no signing key yet')
    e = led.get(man['dossier_id'])
    add("in this site's ledger", bool(e) and e['manifest_sha256'] == hashlib.sha256(json.dumps(man, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest(),
        f"signed {e['utc']} by {e['by']}" if e else 'not in this workspace\'s ledger')
    rows = []
    for s_ in man.get('sources', []):
        p = os.path.join(ws.path, *s_['workspace_path'].split('/'))
        rows.append({'path': s_['path'], 'status': ('MISSING' if not os.path.isfile(p) else ('MATCH' if _sha_file(p) == s_['sha256'] else 'DIFFERS'))})
    add('the source records still match', all(x['status'] == 'MATCH' for x in rows), f"{sum(x['status'] == 'MATCH' for x in rows)} of {len(rows)} match")
    ac = man.get('audit_chain') or {}
    now = audit_chain(ws, ac.get('lines'))
    add('the audit log still holds what was chained', now['lines'] == ac.get('lines') and now['head'] == ac.get('head'),
        (f"the first {ac.get('lines')} line(s) chain to the same head" if now['head'] == ac.get('head') else
         f"the first {ac.get('lines')} line(s) no longer chain to the signed head: an earlier line was changed or removed"))
    r['workspace_checks'] = ws_checks
    r['workspace_sources'] = rows
    if r['status'] == 'VALID' and not all(c['ok'] for c in ws_checks):
        r['status'] = 'VALID, BUT THE WORKSPACE HAS CHANGED'
    return r


def text(r: dict) -> str:
    t = DV.text(r)
    for c in r.get('workspace_checks') or []:
        t += f"\n  {'ok ' if c['ok'] else 'NO '} {c['check']}: {c['detail']}"
    for s_ in r.get('workspace_sources') or []:
        if s_['status'] != 'MATCH':
            t += f"\n      {s_['status']:<8} {s_['path']}"
    return t


def page(ws) -> dict:
    from . import report_history as RH
    kh = key_history(ws)
    return {'key': key_info(ws), 'key_history': {'keys': kh['keys'], 'rotations': [{'utc': r['statement']['utc'], 'by': r['statement']['by'],
                                                                                  'reason': r['statement']['reason'],
                                                                                  'old': (r['statement'].get('old') or {}).get('fingerprint') or r['statement'].get('old_fingerprint'),
                                                                                  'new': r['statement']['new']['fingerprint'],
                                                                                  'signed_by_old': bool(r.get('old_signature')),
                                                                                  'statement_sha256': r['statement_sha256']} for r in kh['rotations']]},
            'dossiers': list(reversed(ledger(ws))), 'history': RH.summary(ws.reports_dir),
            'wells': [{'id': w['id'], 'display': w.get('display') or w['id']} for w in ws.wells()]}
