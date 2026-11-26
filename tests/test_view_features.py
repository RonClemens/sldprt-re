#!/usr/bin/env python3
"""View roles and building blocks (view_features via sheet_zones) on a synthetic part sheet drawn here: a plan view below an elevation of a 3.5 x 3.5 x 2.125 in block with a 0.875 in through bore, two counterbored holes
(0.375 in for 1.375 in, then 0.22 in), 0.5 in corner radii, a SCALE label and dimensions. Needs pdfminer.six, numpy, scipy. usage: python3 tests/test_view_features.py"""
import math, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
try:
    import numpy as np, pdfminer, scipy  # noqa
except ImportError as e:
    print('SKIP (missing', e.name + ')'); sys.exit(0)
import make_test_drawing as G
import sheet_zones as Z

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
path = os.path.join(tempfile.mkdtemp(), 'part.pdf'); G.write_pdf(pg, path)


z = Z.read_zones(path, 1, unit='inch'); views = {v['id']: v for v in z['views']}
roles = {v['role']: v for v in z['views']}
assert 'front' in roles and 'bottom' in roles, [v['role'] for v in z['views']]                                     # plan below the elevation: third-angle reading calls it the bottom view
front, bottom = roles['front'], roles['bottom']; assert front['box'][1] > bottom['box'][3] and front['axes'] == dict(horizontal='X', vertical='Y') and bottom['axes'] == dict(horizontal='X', vertical='Z'), (front['axes'], bottom['axes'])
zf = Z.read_zones(path, 1, 'first', 'inch'); assert {v['role'] for v in zf['views']} >= {'front', 'top'}                    # first-angle: the view below the front is the top view
assert abs(bottom['scale_pt_per_in'] - 72.0) < 0.01 and bottom['scale_source'].startswith('label') and front['scale_source'].startswith('shared'), (bottom['scale_source'], front['scale_source'])
hs = bottom['features']; assert len(hs) == 3, hs
bore = next(f for f in hs if abs(f['diameters_in'][0] - 0.875) < 0.01); assert bore['type'] == 'through hole' and bore['through'] and abs(bore['depth_in'] - 2.125) < 0.02, bore
cb = [f for f in hs if len(f['diameters_in']) == 2]; assert len(cb) == 2 and all(f['type'] == 'counterbored hole' and abs(f['counterbore_diameter_in'] - 0.375) < 0.01 and abs(f['hole_diameter_in'] - 0.22) < 0.01 and abs(f['outer_step_length_in'] - 1.375) < 0.02 and abs(f['inner_step_length_in'] - 0.75) < 0.02 for f in cb), cb
assert any(abs(f['radius_in'] - 0.5) < 0.01 and f['count'] == 4 for f in bottom['fillets']), bottom['fillets']
dirs = {d['id']: d['direction'] for d in bottom['dimension_axes'] + front['dimension_axes']}; assert 'horizontal' in dirs.values() and 'vertical' in dirs.values(), dirs
blocks = [zz['blocks'] for zz in z['zones'].values() if zz['blocks']['views']]; assert blocks and any(b['holes'].get('counterbored hole') == 2 and b['holes'].get('through hole') == 1 for b in blocks), blocks
# a countersunk hole: a circle pair in one view whose neighbour shows slanted lines between the two radii
pg2 = G.Page(); X0, Y0 = 100.0, 40.0; pg2.rect(X0, Y0, X0 + 60, Y0 + 60, 0.35); pg2.circle(X0 + 30, Y0 + 30, 8, 0.35); pg2.circle(X0 + 30, Y0 + 30, 4, 0.35)
pg2.rect(X0, 120.0, X0 + 60, 140.0, 0.35); pg2.line(X0 + 22, 120.0, X0 + 26, 130.0, 0.35); pg2.line(X0 + 38, 120.0, X0 + 34, 130.0, 0.35); pg2.line(X0 + 26, 130.0, X0 + 26, 140.0, 0.35); pg2.line(X0 + 34, 130.0, X0 + 34, 140.0, 0.35)
p2 = os.path.join(os.path.dirname(path), 'csk.pdf'); G.write_pdf(pg2, p2); z2 = Z.read_zones(p2, 1); hole = [f for v in z2['views'] for f in v['features'] if len(f['diameters_in']) == 2]
assert hole and hole[0]['type'] == 'countersunk hole' and abs(hole[0]['countersink_diameter_in'] / hole[0]['hole_diameter_in'] - 2.0) < 0.05, [v['features'] for v in z2['views']]
print('OK: front / bottom (third angle) or top (first angle), axes, scale from the label and shared, through / counterbored / countersunk holes with diameters and step lengths, fillet radii, dimension directions, zone blocks')
