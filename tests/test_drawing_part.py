#!/usr/bin/env python3
"""Rebuilding a part from its drawing, on a synthetic sheet drawn here (nothing real): a 88.9 x 88.9 x 53.975 mm (3.5 x 3.5 x 2.125 in) block drawn 1:1 with a plan view below an elevation, a 0.875 in bore, two stepped holes
(0.375 in for 1.375 in, then 0.22 in), corner radius 0.5 in, and text dimensions for some of it. Needs pdfminer.six, numpy, scipy. usage: python3 tests/test_drawing_part.py"""
import json, math, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
try:
    import numpy as np, pdfminer  # noqa
except ImportError as e:
    print('SKIP (missing', e.name + ')'); sys.exit(0)
import make_test_drawing as G
import drawing_part as D

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
path = os.path.join(tempfile.mkdtemp(), 'part.pdf'); G.write_pdf(pg, path)

m = D.measure(path, 1, unit='inch')
assert 'error' not in m, m
assert abs(m['scale_pt_per_in'] - 72.0) < 0.01 and m['scale_matches'] >= 3 and m['scale_source'].startswith('label') and not m['scale_ambiguous'], m          # the SCALE 1 : 1 label below the plan view is used
q = {x['what']: x for x in m['quantities']}
assert q['plan width']['status'] == 'stated' and abs(q['plan width']['value_in'] - 3.5) < 1e-3 and abs(q['plan width']['measured_in'] - 3.5) < 0.01, q['plan width']
assert abs(q['plan depth']['measured_in'] - 3.5) < 0.01 and abs(q['elevation height (thickness)']['value_in'] - 2.125) < 0.01, q
assert m['rounded_corners'] and abs(m['corner_radius_pt'] / 72 - 0.5) < 0.02 and len(m['rounded_corners']) == 4, m.get('rounded_corners')
hs = {h['id']: h for h in m['holes']}; assert len(hs) == 3, list(hs)
bore_h = hs['H1']; assert abs(bore_h['diameters'][0]['value_in'] - 0.875) < 0.01 and bore_h['diameters'][0]['status'] == 'stated' and bore_h['steps'][0]['through'], bore_h
for h in (hs['H2'], hs['H3']):
    assert len(h['diameters']) == 2 and abs(h['diameters'][0]['measured_in'] - 0.375) < 0.01 and abs(h['diameters'][1]['measured_in'] - 0.22) < 0.01 and h['diameters'][0]['status'] == 'unconfirmed', h['diameters']        # no printed value: measured
    s0, s1 = h['steps']; assert s0['from'] == 'plan face' and abs(s0['value_in'] - 1.375) < 0.01 and s0['status'] == 'stated' and s1['from'] == 'far face' and abs(s1['value_in'] - 0.75) < 0.01 and s1['status'] == 'unconfirmed', h['steps']
assert m['counts']['stated'] >= 5 and m['counts']['unconfirmed'] >= 5, m['counts']
# comparison with a CAD feature list (made up here in the same layout the converter's part_features gives)
cad = dict(bbox_mm=[[-44.45, -26.9875, -44.45], [44.45, 26.9875, 44.45]], planes=[], cones=[], other=[], cylinders=[
    dict(kind='boss', axis_label='+Y', radius_mm=12.7, diameter_mm=25.4, foot_mm=[31.75, 0, 31.75], axial_range_mm=[-26.9875, 26.9875]),
    dict(kind='hole', axis_label='+Y', radius_mm=11.1125, diameter_mm=22.225, foot_mm=[0.0, 0.0, 0.0], axial_range_mm=[-26.9875, 26.9875]),
    dict(kind='hole', axis_label='+Y', radius_mm=4.7625, diameter_mm=9.525, foot_mm=[-22.45, 0, -24.45], axial_range_mm=[-26.9875 + 19.05, 26.9875]),
    dict(kind='hole', axis_label='+Y', radius_mm=2.794, diameter_mm=5.588, foot_mm=[-22.45, 0, -24.45], axial_range_mm=[-26.9875, -26.9875 + 19.05]),
    dict(kind='hole', axis_label='+Y', radius_mm=4.7625, diameter_mm=9.525, foot_mm=[22.55, 0, -24.45], axial_range_mm=[-26.9875 + 19.05, 26.9875]),
    dict(kind='hole', axis_label='+Y', radius_mm=2.794, diameter_mm=5.588, foot_mm=[22.55, 0, -24.45], axial_range_mm=[-26.9875, -26.9875 + 19.05])])
c = D.compare_cad(m, cad); assert 'error' not in c and c['hole_position_error_mm'] < 1.0 and c['drawing_holes'] == 3, c
bad = [r for r in c['rows'] if r['delta'] is not None and abs(r['delta']) > 0.3]; assert not bad, bad
md = D.to_markdown(m) + D.compare_markdown(c); assert 'unconfirmed' in md and 'Drawing-measured part against the CAD part' in md
# circles from a polyline chain, and a page with no plan view
S = np.array([[50 + 20 * math.cos(a), 50 + 20 * math.sin(a), 50 + 20 * math.cos(b), 50 + 20 * math.sin(b), 0.7] for a, b in zip(np.linspace(0, 2 * math.pi, 41)[:-1], np.linspace(0, 2 * math.pi, 41)[1:])])
cc = D.circles(S); assert len(cc) == 1 and abs(cc[0]['r'] - 20) < 0.1 and abs(cc[0]['cx'] - 50) < 0.1, cc
empty = G.Page(); empty.rect(10, 10, 50, 50, 0.35); p2 = os.path.join(os.path.dirname(path), 'empty.pdf'); G.write_pdf(empty, p2); assert 'error' in D.measure(p2, 1)
# a label that disagrees with the printed values is not trusted: the scale is inferred and the disagreement reported
pg2 = G.Page()
for line in pg.ops: pg2.ops.append(line)
pg2.text(X0 + 10, Y0 - 44, 'SCALE 1 : 4', 3.5)       # a nearer wrong label would win only if it were nearer: this one is farther, so the 1 : 1 label still decides
p3 = os.path.join(os.path.dirname(path), 'two.pdf'); G.write_pdf(pg2, p3); m2 = D.measure(p3, 1, unit='inch'); assert m2['scale_source'].startswith('label SCALE 1 : 1'), m2['scale_source']
wrong = G.Page()
for line in pg.ops: wrong.ops.append(line.replace('(SCALE 1 : 1)', '(SCALE 1 : 3)'))
p4 = os.path.join(os.path.dirname(path), 'wrong.pdf'); G.write_pdf(wrong, p4); m3 = D.measure(p4, 1, unit='inch'); assert m3['scale_source'] == 'inferred' and abs(m3['scale_pt_per_in'] - 72) < 0.01 and any('disagrees' in x for x in m3['not_measured']), (m3['scale_source'], m3['not_measured'])
# circles alone (a hole detail with no outline) are refused
solo = G.Page(); solo.circle(100, 100, 5, 0.35); solo.circle(130, 100, 5, 0.35); solo.circle(160, 100, 5, 0.35); p5 = os.path.join(os.path.dirname(path), 'solo.pdf'); G.write_pdf(solo, p5); assert 'error' in D.measure(p5, 1)
print('OK: scale 72 pt/in inferred, outline / corners / thickness / bore / stepped holes measured, stated vs unconfirmed marked, compared with a CAD feature list, polyline circles found, a page without holes refused')
