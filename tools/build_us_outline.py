"""Build gea/reference/us_outline.json - the site map's offline outline of the US states and counties - from the
us-atlas 3.0.1 npm package's counties-10m.json (TopoJSON derived from the US Census Bureau's 2017 cartographic
boundary files; Census data are public domain, the package is ISC-licensed and its notice is kept in the sidecar).

    npm pack us-atlas@3.0.1 && tar xzf us-atlas-3.0.1.tgz
    python tools/build_us_outline.py package/counties-10m.json

Each TopoJSON arc - a stretch of boundary shared by at most two counties - is decoded once, in degrees, rounded to
1e-4 degree (about ten metres, below the source's own simplification), and tagged as a state line when the counties
on its two sides are in different states (or one side is outside). Each county carries its name, state and a label
point (the mean of its ring's vertices). Run at build time only; the program reads the JSON it writes."""
import hashlib, json, os, sys
from datetime import datetime, timezone

src = sys.argv[1]
topo = json.load(open(src, encoding='utf-8'))
sc, tr = topo['transform']['scale'], topo['transform']['translate']
arcs = []
for a in topo['arcs']:
    x = y = 0; pts = []
    for dx, dy in a:
        x += dx; y += dy
        pts.append([round(x * sc[0] + tr[0], 4), round(y * sc[1] + tr[1], 4)])
    arcs.append(pts)
owners = {}
counties = []
for g in topo['objects']['counties']['geometries']:
    fips = str(g['id']); st = fips[:2]
    rings = g['arcs'] if g['type'] == 'Polygon' else [r for poly in g['arcs'] for r in poly]
    xs = ys = n = 0
    for ring in rings:
        for ai in ring:
            k = ai if ai >= 0 else ~ai
            owners.setdefault(k, set()).add(st)
            for px, py in arcs[k]:
                xs += px; ys += py; n += 1
    counties.append({'fips': fips, 'name': g['properties']['name'], 'state': st, 'label': [round(xs / n, 4), round(ys / n, 4)] if n else None})
states = {str(g['id']): g['properties']['name'] for g in topo['objects']['states']['geometries']}
# an arc is a state line when it is used by one county only (the edge of the country) or by counties of two states
use = {}
for g in topo['objects']['counties']['geometries']:
    rings = g['arcs'] if g['type'] == 'Polygon' else [r for poly in g['arcs'] for r in poly]
    for ring in rings:
        for ai in ring:
            k = ai if ai >= 0 else ~ai
            use[k] = use.get(k, 0) + 1
out_arcs = []
for k, pts in enumerate(arcs):
    if k not in use:
        continue
    state_line = use[k] == 1 or len(owners.get(k, ())) > 1
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    out_arcs.append({'s': 1 if state_line else 0, 'b': [min(xs), min(ys), max(xs), max(ys)], 'p': pts})
here = os.path.dirname(os.path.abspath(__file__))
dst = os.path.join(here, '..', 'gea', 'reference', 'us_outline.json')
doc = {'schema': 1, 'datum': 'NAD83 (as the Census Bureau publishes it)', 'precision_deg': 1e-4, 'states': states, 'counties': counties, 'arcs': out_arcs}
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(doc, f, separators=(',', ':'))
src_sha = hashlib.sha256(open(src, 'rb').read()).hexdigest()
lic = open(os.path.join(os.path.dirname(os.path.abspath(src)), 'LICENSE'), encoding='utf-8').read()
prov = {'file': 'us_outline.json', 'sha256': hashlib.sha256(open(dst, 'rb').read()).hexdigest(),
        'derived_from': {'package': 'us-atlas@3.0.1 (npm)', 'file': 'counties-10m.json', 'sha256': src_sha,
                         'repository': 'https://github.com/topojson/us-atlas'},
        'original_source': "US Census Bureau, cartographic boundary files, 2017 edition (counties), https://www2.census.gov/geo/tiger/GENZ2017/shp/ - a work of the US Government, public domain",
        'package_license': 'ISC', 'package_license_text': lic,
        'how': 'TopoJSON arcs decoded once to degrees, rounded to 1e-4 degree; a state line is an arc on one county only or between counties of two states',
        'built_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'counts': {'arcs': len(out_arcs), 'state_line_arcs': sum(a['s'] for a in out_arcs), 'points': sum(len(a['p']) for a in out_arcs),
                   'counties': len(counties), 'states': len(states)}}
with open(dst.replace('.json', '.provenance.json'), 'w', encoding='utf-8') as f:
    json.dump(prov, f, indent=1)
print(json.dumps(prov['counts']), os.path.getsize(dst))
