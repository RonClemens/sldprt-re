"""Determinacy report: what does a 2D drawing sheet actually define of the 3D model, and what does it leave to the CAD?

  analyse(results, components, bom=None, tol_mm=0.25) -> dict      python3 src/determinacy.py RESULTS.json MODEL.glb [--bom BOM.csv] [--md OUT.md] [--json OUT.json] [--tol-mm 0.25]

RESULTS.json is the output of `python3 src/drawing_link.py DRAWING.pdf PAGE MODEL.glb --json`: the linked dimensions (each with the model axis and the model-coordinate range it spans) and the per-view
coverage of the sheet. MODEL.glb is the CAD mesh the dimensions were linked to. The optional BOM is a CSV of the sheet's title-block table (columns item, name, description, length, comments, material; any
subset, any case): rows are matched to model components by a part number found in the row's text, and the sizes printed in it (`1 3/4"`, `3/4"`, `1-3/8`) are compared with the component's extents.

For every component of the model (the children of the assembly root) and for the assembly as a whole, along each model axis, the report says whether the sheet determines its extent:
  dimensioned  a dimension spans exactly the component's extent, or dimensions reference both of its end faces
  located      a dimension references one face (min / max) or the centre, so its position is fixed along that axis but not its size
  undefined    no dimension on the sheet touches it: a model built only from the sheet would have to assume it
It also lists the dimensions that touch no component face (they refer to features inside a component: holes, edges), the views' coverage of the major coordinates the model has, and BOM-versus-CAD findings
(a component that cannot be cut from the stock the BOM lists, a BOM size that matches no extent).

What it is not: this works on bounding extents and located centres. It does not know a component's holes, fillets, threads or interior; the per-view coverage (dimensioned / major coordinates) is the only
measure of that, and it is a count of planes, not of features. A dimension counts as touching a face when an end of it lies within `tol_mm` of that face; with a small component next to a large one a dimension
can be credited to a face by coincidence. Unlinked dimensions are listed but cannot be placed on the model, so they determine nothing here.
"""
import csv, json, math, os, re, struct, sys
import numpy as np

AXES = 'XYZ'


# ---------------------------------------------------------------- model components
def _local(n):
    M = np.eye(4)
    if 'matrix' in n: return np.array(n['matrix'], float).reshape(4, 4).T
    if 'scale' in n: M[:3, :3] = np.diag(n['scale'])
    if 'rotation' in n:
        x, y, z, w = n['rotation']; R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M[:3, :3] = R @ M[:3, :3]
    if 'translation' in n: M[:3, 3] = n['translation']
    return M


def load_components(path):
    """[{name, min (3,), max (3,), triangles}] in millimetres: the children of the single root node (each with its sub-tree), or the root nodes; world transforms applied; non-triangle primitives (dimension lines) skipped"""
    d = open(path, 'rb').read(); jl = struct.unpack_from('<I', d, 12)[0]; js = json.loads(d[20:20 + jl]); b = d[20 + jl + 8:]
    ctype = {5126: '<f4', 5123: '<u2', 5125: '<u4'}
    def pos(i):
        a = js['accessors'][i]; bv = js['bufferViews'][a['bufferView']]; return np.frombuffer(b, ctype[a['componentType']], a['count'] * 3, bv.get('byteOffset', 0) + a.get('byteOffset', 0)).reshape(-1, 3).astype(float)
    nodes = js['nodes']; roots = js['scenes'][js.get('scene', 0)]['nodes']
    def points(i, M):
        n = nodes[i]
        if str(n.get('name', '')).startswith('DIM '): return []
        M = M @ _local(n); out = []
        if 'mesh' in n:
            for p in js['meshes'][n['mesh']]['primitives']:
                if p.get('mode', 4) == 4: out.append((pos(p['attributes']['POSITION']) @ M[:3, :3].T + M[:3, 3], (js['accessors'][p['indices']]['count'] // 3) if 'indices' in p else js['accessors'][p['attributes']['POSITION']]['count'] // 3))
        for c in n.get('children', []): out += points(c, M)
        return out
    tops = list(roots)
    if len(tops) == 1 and nodes[tops[0]].get('children') and 'mesh' not in nodes[tops[0]]: base = _local(nodes[tops[0]]); tops = [(c, base) for c in nodes[tops[0]]['children']]
    else: tops = [(r, np.eye(4)) for r in tops]
    comps = []
    for i, M in tops:
        pts = points(i, M)
        if not pts: continue
        P = np.vstack([p for p, _ in pts]) * 1000.0 if _unit_is_metres(js) else np.vstack([p for p, _ in pts])
        comps.append(dict(name=str(nodes[i].get('name', f'node{i}')), min=P.min(0), max=P.max(0), triangles=int(sum(t for _, t in pts))))
    return comps


def _unit_is_metres(js): return True                                  # glTF is metres by definition


# ---------------------------------------------------------------- BOM
_FRAC = r'(\d+)\s*/\s*(\d+)'


def inch_values(text, quote_optional=False):
    """inch sizes in a BOM text: `1 3/4"`, `3 1/2"`, `3/4"`, `1-3/8"`, `0.75"`; with quote_optional (a length column) the inch mark may be missing"""
    q = r'\s*"?' if quote_optional else r'\s*"'
    pat = re.compile(r'(?<![\w/.\-])(?:(\d+(?:\.\d+)?)[ \-]+' + _FRAC + r'|' + _FRAC + r'|(\d+\.\d+|\d+))' + q)
    out = []
    for m in pat.finditer(str(text or '')):
        if m.group(1) is not None and m.group(2): out.append(float(m.group(1)) + int(m.group(2)) / int(m.group(3)))
        elif m.group(4): out.append(int(m.group(4)) / int(m.group(5)))
        elif m.group(6): out.append(float(m.group(6)))
    return out


def read_bom(path):
    rows = []
    with open(path, newline='') as f:
        for r in csv.DictReader(f): rows.append({(k or '').strip().lower(): (v or '').strip() for k, v in r.items()})
    return rows


def _token(name):
    m = re.match(r'([A-Za-z0-9\-]*\d[A-Za-z0-9\-]*)', name.replace('_', ' ').strip()); t = m.group(1) if m else ''
    return t if len(t) >= 5 else ''


def _base(name): return re.sub(r'<\d+>\s*$', '', name)


def _row_vs_component(row, c, tol_in):
    desc = row.get('description', ''); sizes = inch_values(desc) + inch_values(row.get('length', ''), quote_optional=True)
    ext_in = sorted(((c['max'] - c['min']) / 25.4).tolist(), reverse=True)
    f = dict(component=_base(c['name']), bom_item=row.get('item', ''), bom_text=' | '.join(x for x in (row.get('name', ''), desc, row.get('length', '')) if x), extents_in=[round(x, 4) for x in ext_in], sizes_in=[round(x, 4) for x in sizes], matches=[], unmatched=[], explained=[])
    for sz in sizes:
        near = min(ext_in, key=lambda e: abs(e - sz)); item = dict(size=round(sz, 4), nearest_extent=round(near, 4))
        if abs(near - sz) <= tol_in: f['matches'].append(item)
        elif re.search(r'SCREW|BOLT', desc, re.I) and 0 < max(ext_in) - sz <= 0.5: f['explained'].append(dict(item, why='a fastener length is under the head: the CAD extent includes it'))
        else: f['unmatched'].append(item)
    stock = list(sizes)
    if re.search(r'ROUND|ROD', desc, re.I) and stock: stock.append(stock[0])               # a round bar's diameter counts in both cross-section directions
    if re.search(r'PLATE|BAR|SHEET|STOCK|TUBE|ROD', desc, re.I) and len(stock) >= 3:
        st = sorted(stock, reverse=True)[:3]; over = [round(e - x, 4) + 0.0 for e, x in zip(ext_in, st)]
        f['stock'] = dict(sizes_in=[round(x, 4) for x in st], fits=all(o <= tol_in for o in over), excess_in=[max(o, 0.0) for o in over])
    f['score'] = len(f['matches']) - len(f['unmatched']) - (0 if f.get('stock', {}).get('fits', True) else 2)
    return f


def bom_findings(comps, bom, tol_in):
    """one finding per BOM row and component kind. A row is matched to the component whose part number its text cites; when several rows cite the same number (or none cites a component) the row whose sizes agree best with the
    component is taken, and the citation is reported as an issue: the BOM does not identify the component unambiguously"""
    groups = {}
    for c in comps: groups.setdefault(_base(c['name']), []).append(c)
    reps = {k: v[0] for k, v in groups.items()}; claimed = {}; issues = []; out = []
    cites = {k: [r for r in bom if _token(k) and _token(k).lower() in ' '.join(r.values()).lower()] for k in reps}
    for k, rows in cites.items():
        if not rows: continue
        scored = sorted(((_row_vs_component(r, reps[k], tol_in), r) for r in rows), key=lambda t: -t[0]['score']); best = scored[0][0]; claimed[id(scored[0][1])] = k; best['cited'] = True; out.append(best)
        if len(rows) > 1: issues.append(f"{len(rows)} BOM rows (items {', '.join(r.get('item', '?') for r in rows)}) cite {_token(k)}; item {best['bom_item'] or '?'} agrees best with {k}.")
    for k, rows in cites.items():
        if rows: continue
        left = [r for r in bom if id(r) not in claimed]
        if not left: issues.append(f"No BOM row identifies {k}."); continue
        scored = sorted(((_row_vs_component(r, reps[k], tol_in), r) for r in left), key=lambda t: -t[0]['score'])
        if scored[0][0]['score'] > 0:
            best = scored[0][0]; best['cited'] = False; claimed[id(scored[0][1])] = k; out.append(best); issues.append(f"No BOM row cites {_token(k)}; item {best['bom_item'] or '?'} was matched to {k} by its sizes only.")
        else: issues.append(f"No BOM row identifies {k}.")
    for f in out: f.pop('score', None)
    return dict(findings=out, issues=issues)


# ---------------------------------------------------------------- analysis
def _range(r):
    """[lo, hi] of the dimension along the model's +axis (the link stores it along the dimension's own axis, which may point to -axis)"""
    sign = 1.0 if r['model_axis'][_axis_of(r)] > 0 else -1.0; return sorted(sign * v for v in r['model_range_mm'])


def _axis_of(r):
    a = r.get('model_axis')
    return int(np.argmax(np.abs(a))) if a is not None and any(a) else None


def analyse(results, comps, bom=None, tol_mm=0.25):
    dims = [r for r in results['dimensions'] if r.get('model_range_mm') and _axis_of(r) is not None and r.get('status') != 'reference']          # a reference dimension is derived, it fixes nothing
    units = [r.get('unit') for r in results['dimensions'] if r.get('unit')]; unit = max(set(units), key=units.count) if units else 'mm'; per = 25.4 if unit == 'inch' else 1.0
    allmin = np.min([c['min'] for c in comps], 0); allmax = np.max([c['max'] for c in comps], 0)
    items = [dict(name='(assembly)', min=allmin, max=allmax, triangles=sum(c['triangles'] for c in comps))] + comps
    touched = set(); rows = []
    for c in items:
        row = dict(component=c['name'], triangles=c['triangles'], axes=[])
        for a in range(3):
            lo_c, hi_c = float(c['min'][a]), float(c['max'][a]); mid = (lo_c + hi_c) / 2; refs = {}; extent_by = []
            for r in dims:
                if _axis_of(r) != a: continue
                lo, hi = _range(r)
                if abs(lo - lo_c) <= tol_mm and abs(hi - hi_c) <= tol_mm: extent_by.append(r['id'])
                for tag, v in (('min face', lo_c), ('max face', hi_c), ('centre', mid)):
                    if abs(lo - v) <= tol_mm or abs(hi - v) <= tol_mm: refs.setdefault(tag, []).append(r['id'])
            if c['name'] != '(assembly)':
                touched.update(extent_by)
                for ids in refs.values(): touched.update(ids)
            if extent_by: status, by = 'dimensioned', extent_by
            elif 'min face' in refs and 'max face' in refs: status, by = 'dimensioned', sorted(set(refs['min face'] + refs['max face']))
            elif refs: status, by = 'located', sorted({i for v in refs.values() for i in v})
            else: status, by = 'undefined', []
            row['axes'].append(dict(axis=AXES[a], extent=round((hi_c - lo_c) / per, 4), status=status, by=by, referenced=sorted(refs)))
        rows.append(row)
    unattributed = [r['id'] for r in dims if r['id'] not in touched]
    comp_rows = [r for r in rows if r['component'] != '(assembly)']
    cnt = {s: sum(1 for r in comp_rows for a in r['axes'] if a['status'] == s) for s in ('dimensioned', 'located', 'undefined')}
    rep = dict(unit=unit, tol_mm=tol_mm, dimensions=len(results['dimensions']), linked=len(dims) + sum(1 for r in results['dimensions'] if r.get('status') == 'reference' and r.get('model_range_mm')), unlinked=[r['id'] for r in results['dimensions'] if r.get('status') != 'reference' and not (r.get('model_range_mm') and _axis_of(r) is not None)], reference=[dict(id=r['id'], text=r.get('text'), measured=r.get('measured'), printed=r.get('printed')) for r in results['dimensions'] if r.get('status') == 'reference'],
               components=rows, component_axis_counts=cnt, unattributed_dimensions=unattributed, views=[dict(id=v['id'], coverage=v.get('coverage'), ambiguous=v.get('ambiguous')) for v in results.get('views', [])])
    rep['bom'] = bom_findings(comps, bom, tol_mm / 25.4) if bom else dict(findings=[], issues=[])
    return rep


# ---------------------------------------------------------------- text
def to_markdown(rep, source=''):
    u = rep['unit']; c = rep['component_axis_counts']; n = sum(c.values()); L = []
    L.append('# Determinacy report: what the drawing sheet defines of the model' + (f' ({source})' if source else '')); L.append('')
    L.append(f"{rep['linked']} of {rep['dimensions']} dimensions placed on the model (reference dimensions listed below but not counted as determining anything). Of the {n} component extents (component x axis) the sheet fixes {c['dimensioned']} (**dimensioned**), fixes the position but not the size of {c['located']} (**located**), "
             f"and says nothing about {c['undefined']} (**undefined**: a model built from this sheet alone has to assume them). Face matching tolerance {rep['tol_mm']} mm. Sizes in {u}.")
    amb = [v['id'] for v in rep['views'] if v.get('ambiguous')]
    if amb: L.append(''); L.append(f"Views with an ambiguous orientation (the part is symmetric under a view rotation, so the link may use the mirrored face): {', '.join(amb)}.")
    L.append(''); L.append('| component | ' + ' | '.join(f'{a} extent' for a in AXES) + ' |'); L.append('|---|---|---|---|')
    for r in rep['components']:
        L.append(f"| {r['component']} | " + ' | '.join(f"{a['extent']} {a['status']}" + (f" ({', '.join(a['by'])})" if a['by'] else '') + (f" [{', '.join(a['referenced'])}]" if a['status'] == 'located' else '') for a in r['axes']) + ' |')
    L.append('')
    if rep['unattributed_dimensions']: L.append(f"Dimensions that touch no component face (they refer to features inside a component, such as holes or edges): {', '.join(rep['unattributed_dimensions'])}."); L.append('')
    if rep['unlinked']: L.append(f"Not placed on the model (unlinked, so they determine nothing here): {', '.join(rep['unlinked'])}."); L.append('')
    if rep['reference']: L.append('Reference dimensions (not driving): ' + ', '.join(f"{r['id']} {r['text']} measured {r['measured']}" for r in rep['reference']) + '.'); L.append('')
    L.append('## Coverage of the views'); L.append(''); L.append('| view | axis | major coordinates in the model | dimensioned | ambiguous orientation |'); L.append('|---|---|---|---|---|')
    for v in rep['views']:
        for ax, cv in (v['coverage'] or {}).items(): L.append(f"| {v['id']} | {ax} | {cv['major_coordinates']} | {cv['dimensioned']} | {'yes' if v['ambiguous'] else 'no'} |")
    L.append('')
    if rep['bom']['findings'] or rep['bom']['issues']:
        L.append('## BOM against the CAD'); L.append('')
        for i in rep['bom']['issues']: L.append(f'- **Identification:** {i}')
        for f in rep['bom']['findings']:
            L.append(f"- **{f['component']}** (BOM item {f['bom_item'] or '?'}: {f['bom_text']}) CAD extents {f['extents_in']} in; BOM sizes {f['sizes_in']} in. " + (f"Matched: {[m['size'] for m in f['matches']]}. " if f['matches'] else '')
                     + (f"Explained: {[m['size'] for m in f['explained']]} ({f['explained'][0]['why']}). " if f['explained'] else '') + (f"No matching extent for {[m['size'] for m in f['unmatched']]} (nearest {[m['nearest_extent'] for m in f['unmatched']]}). " if f['unmatched'] else '')
                     + ((f"**Stock check: the CAD component {'fits' if f['stock']['fits'] else 'does NOT fit'} the stock {f['stock']['sizes_in']} in" + ('' if f['stock']['fits'] else f" (excess {f['stock']['excess_in']} in, largest extent first)") + '.**') if 'stock' in f else ''))
        L.append('')
    L.append('## How to read this'); L.append('')
    L.append('This works on bounding extents and located centres, not on a component\'s holes, fillets, threads or interior; the view coverage above counts planes, not features. A dimension is credited to a face when one of its ends lies within the tolerance of it, '
             'so a coincidence between neighbouring components can credit a dimension to the wrong one. The measurements come from the CAD mesh, so this report says what the sheet determines, not whether the product conforms.')
    return '\n'.join(L) + '\n'


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[0].startswith('-'): raise SystemExit(__doc__)
    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default
    results = json.load(open(args[0])); comps = load_components(args[1]); bom = read_bom(opt('--bom')) if opt('--bom') else None
    rep = analyse(results, comps, bom, float(opt('--tol-mm', 0.25))); md = to_markdown(rep, os.path.basename(args[1]))
    if opt('--md'): open(opt('--md'), 'w').write(md)
    if opt('--json'): json.dump(rep, open(opt('--json'), 'w'), indent=1, default=float)
    if not opt('--md') and not opt('--json'): print(md)


if __name__ == '__main__':
    main()
