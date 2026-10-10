"""month_end - the month's deliverable, assembled once, from the reports the program already writes.

A client's month has, until now, been five or six commands and a walk through reports/: the drift report of
each well, the alarm month, the well-test validations, the vibration report where a machine record exists,
the certificate conformity, the SRA packet of each site that has one, the site report, the seismic station
and track reports, the accuracy statement and the SLA month. This module runs the refresh for the period,
then collects every one of those into one dated folder and one archive - with an index that says, item by
item, INCLUDED (with the file's hash) or NOT AVAILABLE (with the reason: no machine record, no SRA defined,
no certificate register, never refreshed) - and a manifest of every file. Nothing is recomputed here beyond
the refresh the program would run anyway; the packet is what the reports said, collected, so it can be
handed over and later verified against the files it came from.

What it will not do: make up an item that has no source (it is listed as NOT AVAILABLE with the reason);
include a report older than the refresh that produced the packet unless told to skip the refresh (and then
the index says every report's own generated time); name a month that has not ended as closed (it is marked
OPEN).
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

PROTOCOL = 'gea.month_end/1'


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def period_bounds(period: str) -> Dict[str, str]:
    """'2026-09' -> the first and last instants of that month (UTC) and whether the month has ended."""
    try:
        y, m = int(period[:4]), int(period[5:7])
        if period[4] != '-' or not (1 <= m <= 12) or len(period) != 7:
            raise ValueError
    except (ValueError, IndexError):
        raise ValueError(f'period must be YYYY-MM, got {period!r}')
    last = calendar.monthrange(y, m)[1]
    start = f'{y:04d}-{m:02d}-01T00:00:00Z'
    end = f'{y:04d}-{m:02d}-{last:02d}T23:59:59Z'
    now = datetime.now(timezone.utc)
    closed = (y, m) < (now.year, now.month)
    return {'period': period, 'start': start, 'end': end, 'closed': closed, 'days': last}


def previous_period(now: Optional[datetime] = None) -> str:
    """The last closed month as YYYY-MM - what a monthly schedule entry asks for."""
    now = now or datetime.now(timezone.utc)
    y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return f'{y:04d}-{m:02d}'


def _copy_family(src_dir: str, names: List[str], dst_dir: str) -> List[dict]:
    """Copy the files of one report family (any extension of each basename) and return their entries."""
    out = []
    if not os.path.isdir(src_dir):
        return out
    for fn in sorted(os.listdir(src_dir)):
        stem = fn.split('.')[0]
        if any(fn == n or fn.startswith(n + '.') or fn.startswith(n + '_') for n in names) or stem in names:
            s = os.path.join(src_dir, fn)
            if os.path.isfile(s):
                os.makedirs(dst_dir, exist_ok=True)
                d = os.path.join(dst_dir, fn)
                shutil.copyfile(s, d)
                out.append({'file': fn, 'sha256': _sha256(d), 'bytes': os.path.getsize(d),
                            'generated_utc': datetime.fromtimestamp(os.path.getmtime(s), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})
    return out


def assemble(ws, period: str, actor: str = 'system', refresh: bool = True, catalog_csv: Optional[str] = None) -> dict:
    """Run the refresh for the period (unless refresh=False), collect every report into reports/month_end/<period>/,
    write INDEX (markdown, html, json) and MANIFEST.json, zip it, audit it. Returns the packet record."""
    from . import __version__
    from .client_reports import Document, Section, Table, write as _write, PROGRAM_NAME
    pb = period_bounds(period)
    items: List[dict] = []
    errors: List[str] = []
    if refresh:
        try:
            ws.refresh_dashboard(actor=actor, month=period)
        except Exception as e:                                   # the refresh's own failure is an item, never a silent gap
            errors.append(f'dashboard refresh: {e}')
        for st in ws.seismic_stations():
            try:
                ws.refresh_seismic(st['id'], actor=actor)
            except Exception as e:
                errors.append(f"seismic {st['id']}: {e}")
        for tk in ws.tracks():
            try:
                ws.refresh_track(tk['id'], actor=actor)
            except Exception as e:
                errors.append(f"track {tk['id']}: {e}")
    out = ws.dir('reports', 'month_end', period)
    for old in os.listdir(out):                                  # a packet for a period is rebuilt whole, never merged
        p = os.path.join(out, old)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    rep = ws.reports_dir

    def item(kind: str, ident: str, name: str, files: List[dict], reason: str = '') -> None:
        items.append({'kind': kind, 'id': ident, 'name': name, 'status': 'INCLUDED' if files else 'NOT AVAILABLE',
                      'reason': '' if files else (reason or 'no report written'), 'files': files})

    # -- the site as a whole ---------------------------------------------------------------------------------------
    item('site', '-', 'Dashboard index', _copy_family(rep, ['index', 'dashboard'], os.path.join(out, 'site')), 'never refreshed')
    item('site', '-', 'Accuracy statement', _copy_family(rep, ['accuracy_statement'], os.path.join(out, 'site')), 'no accuracy statement written')
    item('site', '-', f'SLA month {period}', _copy_family(rep, [f'sla_report_{period}'], os.path.join(out, 'site')),
         'the SLA month is written by the refresh with --month; none for this period')
    item('site', '-', 'FAT/SAT protocol', _copy_family(rep, ['fat_protocol', 'sat_protocol'], os.path.join(out, 'site')), 'no protocol written (gea fat-sat, or the refresh with --sat)')
    item('site', '-', 'Model cards and SBOM', _copy_family(rep, ['model_cards_index', 'sbom'], os.path.join(out, 'site')), 'never refreshed')
    # -- every well -------------------------------------------------------------------------------------------------
    for w in ws.wells():
        wd = os.path.join(rep, 'wells', w['id'])
        dst = os.path.join(out, 'wells', w['id'])
        item('well', w['id'], f"{w['display']}: gauge drift report", _copy_family(wd, ['gauge_drift_report'], dst), 'never refreshed')
        item('well', w['id'], f"{w['display']}: alarm event report", _copy_family(wd, ['alarm_event_report'], dst), 'no alarm report (no alarm config, or never refreshed)')
        item('well', w['id'], f"{w['display']}: well-test validation", _copy_family(wd, ['well_test_validation'], dst), 'no well test detected in the record, or never refreshed')
        item('well', w['id'], f"{w['display']}: shut-in / build-up", _copy_family(wd, ['pressure_transient_report'], dst), 'no shut-in found in the record, or never refreshed')
        item('well', w['id'], f"{w['display']}: data resilience", _copy_family(wd, ['data_resilience_report'], dst), 'no store-and-forward outage was given to the refresh')
        item('well', w['id'], f"{w['display']}: machine vibration", _copy_family(wd, ['vibration_report'], dst),
             'no machine record for this well (gea workspace --action vibration-report)')
        # certificate conformity: the register's standing, written here from the register as it stands
        reg = os.path.join(ws.path, 'wells', w['id'], 'records', 'certificates.jsonl')
        if os.path.isfile(reg):
            try:
                from .certificates import CertificateRegister
                conf = CertificateRegister(reg).conformance()
                os.makedirs(dst, exist_ok=True)
                p = os.path.join(dst, 'certificate_conformance.json')
                with open(p, 'w', encoding='utf-8') as f:
                    json.dump(conf, f, indent=1, default=str)
                item('well', w['id'], f"{w['display']}: certificate conformity (ISO/IEC 17025 7.8)",
                     [{'file': 'certificate_conformance.json', 'sha256': _sha256(p), 'bytes': os.path.getsize(p), 'generated_utc': _utc()}])
            except Exception as e:
                item('well', w['id'], f"{w['display']}: certificate conformity (ISO/IEC 17025 7.8)", [], f'register could not be read: {e}')
        else:
            item('well', w['id'], f"{w['display']}: certificate conformity (ISO/IEC 17025 7.8)", [], 'no certificate register for this well')
    # -- every site: the SRA packet and the site report ---------------------------------------------------------------
    for st in ws.sites():
        sd = os.path.join(rep, 'sites', st['id'])
        dst = os.path.join(out, 'sites', st['id'])
        if st.get('sra'):
            try:
                ws.write_sra_packet(st['id'], catalog_csv=catalog_csv, start=pb['start'], end=pb['end'], actor=actor)
            except Exception as e:
                errors.append(f"sra {st['id']}: {e}")
            item('site-sra', st['id'], f"{st['display']}: SRA packet for {period}", _copy_family(sd, ['sra_packet'], dst), 'the SRA packet failed to write (see errors)')
        else:
            item('site-sra', st['id'], f"{st['display']}: SRA packet for {period}", [], 'no Seismicity Response Area defined for this site (gea workspace --action sra-define)')
        try:
            ws.write_site_report(st['id'], actor=actor)
        except Exception as e:
            errors.append(f"site report {st['id']}: {e}")
        item('site-report', st['id'], f"{st['display']}: site report", _copy_family(sd, ['site_report', 'association', 'quakeml'], dst), 'the site report failed to write')
    # -- seismic stations and tracks --------------------------------------------------------------------------------
    for s in ws.seismic_stations():
        item('seismic', s['id'], f"{s['display']}: seismic station report",
             _copy_family(os.path.join(rep, 'seismic', s['id']), ['seismic_station_report', 'summary', 'detect', 'lines'], os.path.join(out, 'seismic', s['id'])), 'never refreshed')
    for t in ws.tracks():
        item('track', t['id'], f"{t['display']}: seismic track report",
             _copy_family(os.path.join(rep, 'tracks', t['id']), ['seismic_track_report', 'summary'], os.path.join(out, 'tracks', t['id'])), 'never refreshed')
    # -- the index and the manifest ----------------------------------------------------------------------------------
    inc = [i for i in items if i['status'] == 'INCLUDED']
    na = [i for i in items if i['status'] != 'INCLUDED']
    now = _utc()
    report_id = f"MONTH-{period}-{now.replace('-', '').replace(':', '')}"
    rows = [[i['kind'], i['id'], i['name'], i['status'], i['reason'] or ', '.join(f['file'] for f in i['files'])] for i in items]
    secs = [Section('1', 'The month', [
        f"{ws.manifest['name']}: the deliverable for {period} ({pb['start'][:10]} to {pb['end'][:10]}, {pb['days']} days), "
        f"{'a closed month' if pb['closed'] else 'an OPEN month - the period has not ended, and this packet will be superseded'}. "
        f"{len(inc)} item(s) included, {len(na)} not available. "
        + ('The refresh ran before collection, so every report is from the sources as they stand now. ' if refresh else
           'The refresh was skipped: each report carries its own generated time below, which may predate this packet. ')
        + (f"{len(errors)} step(s) failed and are listed in section 3." if errors else 'No step failed.')]),
        Section('2', 'What is in the packet', ['Every report the program writes for a well, a site, a station or a track, collected as written - '
                                               'INCLUDED with the file names, or NOT AVAILABLE with the reason. Hashes are in MANIFEST.json.'],
                [Table(['Kind', 'Id', 'Item', 'Status', 'Files / reason'], rows)]),
        Section('3', 'What failed', errors or ['Nothing.']),
        Section('4', 'What this packet does not call a measurement',
                ['An item that is NOT AVAILABLE - the reason names what the site lacks, and the packet does not stand in for it.',
                 'A number in any report - each report carries its own method and its own declarations; this index collects, it does not recompute.',
                 'A month that has not ended - the packet says OPEN and is superseded by the one made after the month closes.'])]
    front = [['Report ID', report_id], ['Site', ws.manifest['name']], ['Period', period], ['Closed', 'yes' if pb['closed'] else 'NO - open month'],
             ['Generated (UTC)', now], ['Program', f'{PROGRAM_NAME} build {__version__}'], ['Result', f'{len(inc)} INCLUDED, {len(na)} NOT AVAILABLE, {len(errors)} FAILED']]
    doc = Document(title=f'{PROGRAM_NAME} - Month-End Packet', report_id=report_id, front=front, sections=secs,
                   footer='Collected from the reports as written; nothing is recomputed in this index.',
                   data={'protocol': PROTOCOL, 'report_id': report_id, 'site': ws.manifest['name'], 'period': pb, 'generated_utc': now,
                         'program_version': __version__, 'refresh': refresh, 'items': items, 'errors': errors, '_records': [], '_catalogue_obj': None})
    paths = _write(doc, out, 'INDEX')
    # the manifest: every file in the packet folder
    files = {}
    for root, _, fns in os.walk(out):
        for fn in sorted(fns):
            if fn == 'MANIFEST.json':
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, out).replace(os.sep, '/')
            files[rel] = {'sha256': _sha256(p), 'bytes': os.path.getsize(p)}
    manifest = {'protocol': PROTOCOL, 'site': ws.manifest['name'], 'period': period, 'closed': pb['closed'], 'generated_utc': now, 'program_version': __version__,
                'report_id': report_id, 'n_files': len(files), 'files': files, 'included': len(inc), 'not_available': len(na), 'errors': errors}
    with open(os.path.join(out, 'MANIFEST.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=1)
    slug = ''.join(c if c.isalnum() or c in '-_' else '-' for c in ws.manifest['name']).strip('-') or 'site'
    zpath = os.path.join(ws.dir('reports', 'month_end'), f'month_end_{slug}_{period}.zip')
    with zipfile.ZipFile(zpath + '.part', 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for root, _, fns in os.walk(out):
            for fn in sorted(fns):
                p = os.path.join(root, fn)
                z.write(p, os.path.relpath(p, out).replace(os.sep, '/'))
    os.replace(zpath + '.part', zpath)
    rec = {'period': period, 'closed': pb['closed'], 'folder': out, 'archive': zpath, 'archive_sha256': _sha256(zpath), 'index': paths['html'], 'report_id': report_id,
           'included': len(inc), 'not_available': len(na), 'errors': errors, 'n_files': len(files), 'generated_utc': now, 'refresh': refresh}
    with open(os.path.join(ws.dir('records'), 'month_end.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec) + '\n')
    ws.audit(actor, 'month_end.assemble', {k: rec[k] for k in ('period', 'closed', 'archive', 'archive_sha256', 'included', 'not_available', 'n_files')}
             | ({'errors': errors} if errors else {}))
    return rec


def packets(ws) -> List[dict]:
    """Every packet assembled from this workspace, newest last, with whether its archive is still there."""
    p = os.path.join(ws.path, 'records', 'month_end.jsonl')
    out = []
    if os.path.isfile(p):
        with open(p, encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    r['present'] = os.path.isfile(r.get('archive', ''))
                    out.append(r)
    return out


def verify(folder_or_zip: str) -> dict:
    """A packet folder or its archive against its MANIFEST.json."""
    out = {'path': folder_or_zip, 'status': 'OK', 'n_files': 0, 'mismatched': [], 'missing': []}
    if zipfile.is_zipfile(folder_or_zip):
        with zipfile.ZipFile(folder_or_zip) as z:
            if 'MANIFEST.json' not in z.namelist():
                return {**out, 'status': 'FAIL', 'error': 'no MANIFEST.json'}
            man = json.loads(z.read('MANIFEST.json'))
            names = set(z.namelist())
            for rel, meta in man['files'].items():
                if rel not in names:
                    out['missing'].append(rel); continue
                if hashlib.sha256(z.read(rel)).hexdigest() != meta['sha256']:
                    out['mismatched'].append(rel)
                out['n_files'] += 1
    else:
        mp = os.path.join(folder_or_zip, 'MANIFEST.json')
        if not os.path.isfile(mp):
            return {**out, 'status': 'FAIL', 'error': 'no MANIFEST.json'}
        man = json.load(open(mp, encoding='utf-8'))
        for rel, meta in man['files'].items():
            p = os.path.join(folder_or_zip, *rel.split('/'))
            if not os.path.isfile(p):
                out['missing'].append(rel); continue
            if _sha256(p) != meta['sha256']:
                out['mismatched'].append(rel)
            out['n_files'] += 1
    out['period'] = man.get('period'); out['site'] = man.get('site'); out['generated_utc'] = man.get('generated_utc')
    if out['mismatched'] or out['missing']:
        out['status'] = 'FAIL'
    return out


def report_text(rec: dict) -> str:
    lines = [f"month-end {rec['period']} ({'closed' if rec['closed'] else 'OPEN month'}): {rec['included']} included, {rec['not_available']} not available, "
             f"{len(rec['errors'])} failed; {rec['n_files']} files",
             f"  folder:  {rec['folder']}", f"  archive: {rec['archive']}  sha256 {rec['archive_sha256'][:16]}...", f"  index:   {rec['index']}"]
    for e in rec['errors']:
        lines.append(f"  FAILED: {e}")
    return '\n'.join(lines)
