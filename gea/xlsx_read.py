"""An Excel workbook (.xlsx) read as a table, with the standard library alone.

Contractors send invoices and daily reports as spreadsheets; a site keeps its declarations sheet in one. Every
reader in this program takes CSV text by its column headings, so this module turns one worksheet of an Office Open
XML workbook (ECMA-376: a zip of XML parts) into that text:

- the sheet: the first one in the workbook, or the one named;
- the cells: shared strings, inline strings, numbers, booleans, and a formula's cached value (the value Excel last
  computed and saved - the formula itself is not evaluated);
- dates: a number in a cell whose number format is a date or time (the built-in formats 14-22 and 45-47, or a custom
  format with day, month, year or hour codes) is written as an ISO date, date and time, or time of day, counting
  from the workbook's own epoch (1899-12-30, or 1904-01-01 for a workbook that says so);
- a whole number is written without its ".0"; empty cells stay empty; a row's cells keep their columns.

What it will not do: evaluate formulas, read the older binary .xls, follow merged cells (a merged block's value is
in its first cell), or read charts and pictures.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Optional

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'pr': 'http://schemas.openxmlformats.org/package/2006/relationships'}
DATE_BUILTIN = set(range(14, 23)) | {45, 46, 47}


def is_xlsx(content: bytes) -> bool:
    if not content[:2] == b'PK':
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            return 'xl/workbook.xml' in z.namelist()
    except zipfile.BadZipFile:
        return False


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r'[A-Z]+', ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _is_date_format(code: str) -> bool:
    c = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', '', code.lower())       # quoted text, colours and escapes are not codes
    return bool(re.search(r'[dmyhs]', c)) and not re.fullmatch(r'[#0.,%e+\- ]*', c)


def _num_text(v: float) -> str:
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)


def sheets(content: bytes) -> List[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        wb = ET.fromstring(z.read('xl/workbook.xml'))
    return [s.get('name') for s in wb.find('m:sheets', NS)]


def rows(content: bytes, sheet: Optional[str] = None) -> List[List[str]]:
    """The worksheet as rows of text."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = set(z.namelist())
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        pr = wb.find('m:workbookPr', NS)
        epoch = datetime(1904, 1, 1) if pr is not None and pr.get('date1904') in ('1', 'true') else datetime(1899, 12, 30)
        sh = list(wb.find('m:sheets', NS))
        if not sh:
            raise ValueError('the workbook has no worksheet')
        pick = sh[0] if sheet is None else next((s for s in sh if s.get('name') == sheet), None)
        if pick is None:
            raise ValueError(f"no worksheet {sheet!r}: the workbook has {', '.join(s.get('name') for s in sh)}")
        rid = pick.get(f"{{{NS['r']}}}id")
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target = next(r.get('Target') for r in rels if r.get('Id') == rid)
        path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        shared: List[str] = []
        if 'xl/sharedStrings.xml' in names:
            for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', NS):
                shared.append(''.join(t.text or '' for t in si.iter(f"{{{NS['m']}}}t")))
        date_styles = set()
        if 'xl/styles.xml' in names:
            st = ET.fromstring(z.read('xl/styles.xml'))
            nf = st.find('m:numFmts', NS)
            custom = {int(f.get('numFmtId')): f.get('formatCode', '') for f in (list(nf) if nf is not None else [])}
            xfs = st.find('m:cellXfs', NS)
            for i, xf in enumerate(list(xfs) if xfs is not None else []):
                fid = int(xf.get('numFmtId', '0'))
                if fid in DATE_BUILTIN or (fid in custom and _is_date_format(custom[fid])):
                    date_styles.add(i)
        root = ET.fromstring(z.read(path))
    out: List[List[str]] = []
    for row in root.iter(f"{{{NS['m']}}}row"):
        r = int(row.get('r', len(out) + 1))
        while len(out) < r - 1:
            out.append([])
        cells: List[str] = []
        for c in row.findall('m:c', NS):
            ref = c.get('r')
            j = _col(ref) if ref else len(cells)
            while len(cells) < j:
                cells.append('')
            t = c.get('t', 'n')
            v = c.find('m:v', NS)
            text = ''
            if t == 's' and v is not None:
                text = shared[int(v.text)]
            elif t == 'inlineStr':
                text = ''.join(x.text or '' for x in c.iter(f"{{{NS['m']}}}t"))
            elif t == 'b' and v is not None:
                text = 'TRUE' if v.text == '1' else 'FALSE'
            elif t in ('str', 'e') and v is not None:
                text = v.text or ''
            elif v is not None and v.text not in (None, ''):
                x = float(v.text)
                if int(c.get('s', '0')) in date_styles:
                    d = epoch + timedelta(days=x)
                    d = d.replace(microsecond=0) + timedelta(seconds=round(d.microsecond / 1e6))
                    if x < 1:
                        text = d.strftime('%H:%M') if d.second == 0 else d.strftime('%H:%M:%S')
                    elif abs(x - round(x)) < 1e-9:
                        text = d.strftime('%Y-%m-%d')
                    else:
                        text = d.strftime('%Y-%m-%dT%H:%M') if d.second == 0 else d.strftime('%Y-%m-%dT%H:%M:%S')
                else:
                    text = _num_text(x)
            cells.append(text)
        out.append(cells)
    while out and not any(x.strip() for x in out[-1]):
        out.pop()
    return out


def to_csv(content: bytes, sheet: Optional[str] = None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    for r in rows(content, sheet):
        w.writerow(r)
    return buf.getvalue()


def text_of(content: bytes) -> str:
    """A file's content as the CSV text the readers take: a workbook's first sheet, or the text as it is."""
    if is_xlsx(content):
        return to_csv(content)
    return content.decode('utf-8-sig', errors='replace')


def write_simple(path: str, rows_: List[List[object]], date_cols=()) -> None:
    """A minimal workbook for tests and templates: one sheet, strings shared, numbers as numbers, the columns named in
    date_cols holding datetime values written as dates (built-in format 14 or 22)."""
    strings: List[str] = []
    idx = {}

    def si(s):
        if s not in idx:
            idx[s] = len(strings); strings.append(s)
        return idx[s]
    xml_rows = []
    for i, r in enumerate(rows_, start=1):
        cs = []
        for j, v in enumerate(r):
            ref = (chr(65 + j) if j < 26 else chr(64 + j // 26) + chr(65 + j % 26)) + str(i)
            if v is None or v == '':
                continue
            if isinstance(v, datetime):
                serial = (v - datetime(1899, 12, 30)).total_seconds() / 86400.0
                style = 1 if v.hour == 0 and v.minute == 0 else 2
                cs.append(f'<c r="{ref}" s="{style}"><v>{serial!r}</v></c>')
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                cs.append(f'<c r="{ref}"><v>{v!r}</v></c>')
            else:
                cs.append(f'<c r="{ref}" t="s"><v>{si(str(v))}</v></c>')
        xml_rows.append(f'<row r="{i}">{"".join(cs)}</row>')
    from xml.sax.saxutils import escape
    m = NS['m']
    parts = {
        '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                               '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                               '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
                               '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels': '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{m}" xmlns:r="{NS["r"]}"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                                      '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
                                      '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml': f'<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{m}"><cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/>'
                         '<xf numFmtId="22" applyNumberFormat="1"/></cellXfs></styleSheet>',
        'xl/sharedStrings.xml': f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{m}" count="{len(strings)}" uniqueCount="{len(strings)}">'
                                + ''.join(f'<si><t xml:space="preserve">{escape(x)}</t></si>' for x in strings) + '</sst>',
        'xl/worksheets/sheet1.xml': f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{m}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>',
    }
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for k, v in parts.items():
            z.writestr(k, v)
