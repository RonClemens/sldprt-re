#!/usr/bin/env python3
"""Rebuild from building blocks on a synthetic part sheet drawn here (a 3.5 x 3.5 x 2.125 in block with a 0.875 in bore, two counterbored holes and 0.5 in corner radii, plan below the elevation, SCALE 1 : 1). The specification is checked
without FreeCAD; with FreeCAD (env FREECADCMD = the freecadcmd binary) the solid is built and its volume checked against the analytic one. Needs pdfminer.six, numpy, scipy, opencv. usage: python3 tests/test_rebuild_from_blocks.py"""
import math, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
try:
    import numpy as np, pdfminer, scipy, cv2  # noqa
except ImportError as e:
    print('SKIP (missing', e.name + ')'); sys.exit(0)
import make_test_drawing as G
import rebuild_from_blocks as R

PT = G.PT
pg = G.Page(); X0, Y0, W, T = 100.0, 40.0, 88.9, 88.9; TH = 53.975; EY = 170.0                         # plan lower left, plan size, thickness, elevation bottom (paper mm)
r = 12.7
for (x0, y0, x1, y1) in ((X0 + r, Y0, X0 + W - r, Y0), (X0 + r, Y0 + T, X0 + W - r, Y0 + T), (X0, Y0 + r, X0, Y0 + T - r), (X0 + W, Y0 + r, X0 + W, Y0 + T - r)): pg.line(x0, y0, x1, y1, 0.35)   # plan outline with 0.5 in corners (arcs below)
for a0, cx, cy in ((math.pi, X0 + r, Y0 + r), (1.5 * math.pi, X0 + W - r, Y0 + r), (0, X0 + W - r, Y0 + T - r), (0.5 * math.pi, X0 + r, Y0 + T - r)): pg.arc90(cx, cy, r, a0, 0.35)
bore = (X0 + 44.45, Y0 + 44.45 + 0.0, 11.1125); holes = [(X0 + 22.0, Y0 + 20.0), (X0 + 67.0, Y0 + 20.0)]
pg.circle(bore[0], bore[1], bore[2], 0.35)
for hx, hy in holes: pg.circle(hx, hy, 4.7625, 0.35); pg.circle(hx, hy, 2.794, 0.35)
pg.rect(X0, EY, X0 + W, EY + TH, 0.35)                                                           # elevation outline
for x in (bore[0] - bore[2], bore[0] + bore[2]): pg.line(x, EY, x, EY + TH, 0.35)               # the bore runs the whole way
for hx, hy in holes:
    for dx in (-4.7625, 4.7625): pg.line(hx + dx, EY, hx + dx, EY + 34.925, 0.35)               # 0.375 in for 1.375 in from the edge next to the plan view
    for dx in (-2.794, 2.794): pg.line(hx + dx, EY + 34.925, hx + dx, EY + TH, 0.35)            # then 0.22 in to the far face
    pg.line(hx - 4.7625, EY + 34.925, hx + 4.7625, EY + 34.925, 0.35)
pg.dim_h('D1', X0, X0 + W, Y0 + T + 14, Y0 + T, Y0 + T, [(-6, 1.0, '3.500', 3.5)], kind='linear', value=3.5, tol=dict(kind='none'))
pg.dim_h('D2', X0, X0 + 44.45, Y0 - 14, Y0, Y0, [(-6, -4.5, '1.750', 3.5)], kind='linear', value=1.75, tol=dict(kind='none'))
pg.dim_v('D3', Y0, Y0 + 44.45, X0 + W + 14, X0 + W, X0 + W, [(1.0, -6, '1.750', 3.5)], kind='linear', value=1.75, tol=dict(kind='none'))
pg.dim_h('D4', X0, X0 + 22.0, Y0 - 24, Y0, Y0, [(-6, -4.5, '0.866', 3.5)], kind='linear', value=0.866, tol=dict(kind='none'))
pg.dim_v('D5', EY, EY + TH, X0 + W + 14, X0 + W, X0 + W, [(1.0, -6, '2.125', 3.5)], kind='linear', value=2.125, tol=dict(kind='none'))
pg.dim_v('D6', EY, EY + 34.925, X0 - 14, X0, X0, [(-8.0, -6, '1.375', 3.5)], rot=90, kind='linear', value=1.375, tol=dict(kind='none'))
pg.text(X0 - 50, Y0 + 40, '0.875 THRU ALL', 3.5)
pg.rect(10, 10, 410, 287, 0.35)                                                                                    # the sheet border: must not become the outline
pg.text(X0 + 10, Y0 - 34, 'SCALE 1 : 1', 3.5)
pg.text(X0 + 60, Y0 + 12, '0.875 THRU ALL', 3.5)
pg.text(X0 + 60, Y0 + 12, '0.875 THRU ALL', 3.5)
path = os.path.join(tempfile.mkdtemp(), 'part.pdf'); G.write_pdf(pg, path)



spec = R.build_spec(path, 1, unit='inch'); assert 'error' not in spec, spec
assert abs(spec['X'] - 3.5) < 0.01 and abs(spec['Y'] - 2.125) < 0.01 and abs(spec['D'] - 3.5) < 0.01, (spec['X'], spec['Y'], spec['D'])
prov = {p['name']: p for p in spec['provenance']}
assert prov['overall X (width, across the front view)']['status'] == 'stated' and prov['overall Y (height)']['status'] == 'stated', prov     # printed 3.500 and 2.125
assert set(spec['silhouettes']) == {'front', 'bottom'} and spec['silhouettes']['bottom']['box_fill'] < 1.0 and spec['silhouettes']['front']['box_fill'] > 0.97, {k: v['box_fill'] for k, v in spec['silhouettes'].items()}     # the plan has rounded corners
assert spec['rib'] is None and len(spec['holes']) == 3 and not spec['slots'], (spec['rib'], len(spec['holes']))
bore = next(h for h in spec['holes'] if len(h['steps']) == 1); assert bore['steps'][0]['diameter_status'] in ('stated', 'unconfirmed') and bore['steps'][0]['length_in'] and abs(bore['steps'][0]['length_in'] - 2.125) < 0.02
cb = [h for h in spec['holes'] if len(h['steps']) == 2]; assert len(cb) == 2 and all(abs(h['steps'][0]['length_in'] - 1.375) < 0.02 and h['steps'][0]['length_from'] == 'near' and h['steps'][1]['length_from'] == 'far' and h['steps'][0]['diameter_status'] == 'unconfirmed' for h in cb), cb   # diameters are measured, not printed
assert any(p['status'].startswith('unconfirmed') for p in spec['provenance']) and all('status' in p and 'evidence' in p for p in spec['provenance'])
# the two plain helpers
poly = [(0, 0), (6, 0), (6, 8), (5.5, 8), (5.0, 6.5), (2.5, 0.77), (2.5, 0.5), (0, 0.5)]; tri = R.find_rib_triangle([(0, 0), (6, 0), (6, 8), (5.5, 8), (5.5, 6.5), (5.0, 6.5), (2.5, 0.77), (2.5, 0.5), (0, 0.5)], 6.0, 8.0, 16.0)
assert tri and abs(tri['C'][0] - 5.5) < 0.01 and abs(tri['C'][1] - 0.5) < 0.01, tri                                                          # the inside corner of the angle behind a gusset
fc = os.environ.get('FREECADCMD')
if fc and os.path.exists(fc):
    out = tempfile.mkdtemp(); r = R.rebuild(path, 1, out, fc, unit='inch'); b = r['build']; assert 'error' not in b and b['valid'] and b['solids'] == 1, b
    exp = 3.5 * 3.5 * 2.125 - 4 * (1 - math.pi / 4) * 0.25 * 2.125 - math.pi * 0.4375 ** 2 * 2.125 - 2 * (math.pi * 0.1875 ** 2 * 1.375 + math.pi * 0.11 ** 2 * 0.75)
    assert abs(b['volume_in3'] - exp) / exp < 0.02, (b['volume_in3'], exp); assert os.path.exists(os.path.join(out, 'report.md')) and 'unconfirmed' in open(os.path.join(out, 'report.md')).read()
    print('OK: spec with statuses, silhouettes, holes with steps and their faces, gusset corner found; solid built, volume %.3f in3 against %.3f analytic' % (b['volume_in3'], exp))
else: print('OK: spec with statuses, silhouettes, holes with steps and their faces, gusset corner found (FreeCAD not available: solid not built)')
