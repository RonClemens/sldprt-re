#!/usr/bin/env python3
"""Which of a part's features does its detail drawing actually state?  The feature-level version of determinacy.py.

  analyse(features, sheet, tol_in=1/64) -> dict        python3 src/feature_determinacy.py PART.SLDPRT DRAWING.pdf PAGE [--md OUT.md] [--json OUT.json]

`features` is part_features.part_features() (the exact planes, cylinders and cones of the CAD part, mm); `sheet` is a page read by pdf_drawing.read_pdf. For every quantity a drawing has to give to define the part:
  extent         the size of the part along X, Y, Z
  position       the coordinate of each plane and the axis position of each hole / boss across its two perpendicular directions
  hole end       where each hole stops along its axis (the coordinate of its far end)
  diameter       of each hole and boss
it asks whether a printed dimension (or a diameter callout) carries that number: a position or end counts as stated when some dimension equals its distance to a reference (a part face or another feature's coordinate on the same axis)
within `tol_in` (default 1/64 in, the rounding of a fractional inch dimension); a diameter when a callout or diameter dimension equals it. The match is by value, not by what the dimension's arrows touch, so a value that happens to equal
a distance is credited (reported as such: `by`). `unstated` means no printed value on the sheet equals it: a model built from the sheet alone would have to take it from the drawn linework (measured to scale, which is `unconfirmed`)
or assume it. Callouts read: `0.875 THRU ALL`, `Ø 3/4`, thread callouts (`3/4-16 UNF`, nominal size and pitch are not compared with the CAD tap-drill diameter, only listed).
Sheet values are taken in the sheet's unit (inch here); the feature list is millimetres.
"""
import itertools, json, os, re, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
IN = 25.4


def callout_values(notes):
    """(inch values) of numbers in notes that look like hole/size callouts: a decimal or fraction with THRU / HOLE / DIA / Ø / UNF / UNC / DEEP, or the symbol cid for Ø"""
    out = []
    for t in notes:
        u = t.upper()
        if not re.search(r'THRU|HOLE|DIA|Ø|\(CID:145\)|UNF|UNC|DEEP|DEPTH|CBORE|C.BORE|DRILL', u): continue
        for m in re.finditer(r'(?<![\w.])(\d+(?:\.\d+)?)\s+(\d+)\s*/\s*(\d+)|(?<![\w.])(\d+)\s*/\s*(\d+)(?!\d*-)|(?<![\w.])(\d*\.\d+)', u):
            if m.group(1): out.append(float(m.group(1)) + int(m.group(2)) / int(m.group(3)))
            elif m.group(4): out.append(int(m.group(4)) / int(m.group(5)))
            elif m.group(6): out.append(float(m.group(6)))
    return out


def analyse(features, sheet, tol_in=1 / 64):
    dims = [(d.id, d.value if d.unit == 'inch' else d.value / IN, d.text) for d in sheet.dimensions if d.value]
    calls = callout_values([n.text for n in sheet.notes])
    tol = tol_in * IN; rows = []
    def stated_distance(mm, refs, what):
        """the dimension whose value equals |mm - r| for some reference r"""
        best = None
        for r in refs:
            dist = abs(mm - r)
            if dist < 1e-6: continue
            for i, v, t in dims:
                e = abs(v * IN - dist)
                if e <= tol and (best is None or e < best[0]): best = (e, i, t, r)
        return best
    lo, hi = np.array(features['bbox_mm'][0]), np.array(features['bbox_mm'][1]); ext = hi - lo
    for a, ax in enumerate('XYZ'):
        planes = sorted({(p['d_mm'] if p['axis'] and p['axis'][0] == '+' else -p['d_mm']) for p in features['planes'] if p['axis'] and p['axis'][1] == ax})
        centres = sorted({c['foot_mm'][a] for c in features['cylinders'] if c['axis_label'] and c['axis_label'][1] != ax})
        refs = sorted(set(planes) | set(centres) | {lo[a], hi[a]})
        m = stated_distance(hi[a], [lo[a]], 'extent')
        rows.append(dict(kind='extent', what=f'overall {ax}', mm=round(float(ext[a]), 3), inch=round(float(ext[a] / IN), 4), stated=bool(m), by=(m[1] if m else None), residual_mm=(round(m[0], 3) if m else None)))
        for p in planes:
            if abs(p - lo[a]) < 1e-3 or abs(p - hi[a]) < 1e-3: continue
            m = stated_distance(p, [r for r in refs if abs(r - p) > 1e-3], 'plane')
            rows.append(dict(kind='position', what=f'plane {ax} = {p / IN:+.4f} in', mm=round(p, 3), inch=round(p / IN, 4), stated=bool(m), by=(m[1] if m else None), residual_mm=(round(m[0], 3) if m else None)))
    for c in features['cylinders']:
        al = c['axis_label']; a = 'XYZ'.index(al[1]) if al else None
        d_in = c['diameter_mm'] / IN; hit = next(((i, t) for i, v, t in dims if abs(v - d_in) * IN <= tol), None); cal = any(abs(v - d_in) * IN <= tol for v in calls)
        rows.append(dict(kind='diameter', what=f"{c['kind']} diameter at {[round(x / IN, 3) for x in c['foot_mm']]} in", mm=c['diameter_mm'], inch=round(d_in, 4), stated=bool(hit or cal), by=(hit[0] if hit else ('callout' if cal else None)), residual_mm=None))
        if a is None: continue
        for b in range(3):
            if b == a: continue
            refs = sorted({lo[b], hi[b]} | {p for p in {(q['d_mm'] if q['axis'][0] == '+' else -q['d_mm']) for q in features['planes'] if q['axis'] and q['axis'][1] == 'XYZ'[b]}} | {o['foot_mm'][b] for o in features['cylinders'] if o is not c and o['axis_label'] and o['axis_label'][1] != 'XYZ'[b]})
            mm = c['foot_mm'][b]; m = stated_distance(mm, [r for r in refs if abs(r - mm) > 1e-3], 'centre')
            rows.append(dict(kind='position', what=f"axis of the {c['kind']} dia {c['diameter_mm'] / IN:.4f} in, {'XYZ'[b]} = {mm / IN:+.4f} in", mm=round(mm, 3), inch=round(mm / IN, 4), stated=bool(m), by=(m[1] if m else None), residual_mm=(round(m[0], 3) if m else None)))
        if c['axial_range_mm']:
            rng = c['axial_range_mm']; planes = sorted({(q['d_mm'] if q['axis'][0] == '+' else -q['d_mm']) for q in features['planes'] if q['axis'] and q['axis'][1] == al[1]} | {lo[a], hi[a]})
            for end in rng:
                if any(abs(end - q) < 1e-3 for q in (lo[a], hi[a])): continue                           # ends on a face of the part: the part's own extent
                m = stated_distance(end, [q for q in planes if abs(q - end) > 1e-3] + [lo[a], hi[a]], 'end')
                rows.append(dict(kind='hole end', what=f"end of the {c['kind']} dia {c['diameter_mm'] / IN:.4f} in at {al[1]} = {end / IN:+.4f} in", mm=round(end, 3), inch=round(end / IN, 4), stated=bool(m), by=(m[1] if m else None), residual_mm=(round(m[0], 3) if m else None)))
    s = {k: [sum(1 for r in rows if r['kind'] == k and r['stated']), sum(1 for r in rows if r['kind'] == k)] for k in ('extent', 'position', 'hole end', 'diameter')}
    return dict(tol_in=tol_in, dimensions=len(dims), callout_values=len(calls), rows=rows, stated_of=s, cones=features['cones'])


def to_markdown(rep, name=''):
    s = rep['stated_of']; tot = sum(v[1] for v in s.values()); got = sum(v[0] for v in s.values())
    L = [f'# Feature determinacy {name}'.strip(), '', f"{got} of {tot} quantities the drawing must give are carried by a printed value within 1/{round(1 / rep['tol_in'])} in ({rep['dimensions']} dimensions, {rep['callout_values']} callout numbers on the sheet): "
         + ', '.join(f"{k} {v[0]}/{v[1]}" for k, v in s.items()) + '.', '', '| quantity | value (in) | stated | by | residual (mm) |', '|---|---|---|---|---|']
    for r in rep['rows']: L.append(f"| {r['kind']}: {r['what']} | {r['inch']} | {'yes' if r['stated'] else '**no**'} | {r['by'] or ''} | {r['residual_mm'] if r['residual_mm'] is not None else ''} |")
    if rep['cones']: L += ['', 'Cones (drill points, chamfers; not compared): ' + '; '.join(f"{c['kind']} half-angle {c['half_angle_deg']} deg" for c in rep['cones'])]
    L += ['', 'A value is credited when it equals a distance to a part face or to another feature on the same axis; the arrows of the dimension are not checked, so an equal value elsewhere on the sheet can be credited by coincidence.']
    return '\n'.join(L) + '\n'


def main():
    a = sys.argv[1:]
    if len(a) < 3 or a[0].startswith('-'): raise SystemExit(__doc__)
    from part_features import part_features
    from pdf_drawing import read_pdf
    rep = analyse(part_features(a[0]), read_pdf(a[1], pages=[int(a[2])]).sheets[0]); md = to_markdown(rep, os.path.basename(a[0]))
    if '--md' in a: open(a[a.index('--md') + 1], 'w').write(md)
    if '--json' in a: json.dump(rep, open(a[a.index('--json') + 1], 'w'), indent=1, default=float)
    if '--md' not in a and '--json' not in a: print(md)


if __name__ == '__main__':
    main()
