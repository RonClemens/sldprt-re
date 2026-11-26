#!/usr/bin/env python3
"""Sheet zones on the synthetic drawing (tools/make_test_drawing.py) with a zone border added here: numbers 4..1 along the top and bottom (right to left), letters C..A down the sides. Needs pdfminer.six, numpy, scipy.
usage: python3 tests/test_sheet_zones.py"""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
try:
    import numpy as np, pdfminer, scipy  # noqa
except ImportError as e:
    print('SKIP (missing', e.name + ')'); sys.exit(0)
import make_test_drawing as G
import sheet_zones as Z

pg = G.build()                                                                      # border 10..410 x 10..287 mm, title block bottom right, a top view and a front view
for x, c in zip((60, 160, 260, 360), '4321'):
    pg.text(x, 291, c, 3.5); pg.text(x, 4, c, 3.5)
for y, c in zip((248, 148, 48), 'CBA'):
    pg.text(4, y, c, 3.5); pg.text(413, y, c, 3.5)
path = os.path.join(tempfile.mkdtemp(), 'z.pdf'); G.write_pdf(pg, path)
z = Z.read_zones(path, 1)
g = z['grid']; assert g and g['columns'] == ['4', '3', '2', '1'] and g['rows'] == ['C', 'B', 'A'], g
assert set(z['zones']) == {r + c for r in 'CBA' for c in '4321'}, sorted(z['zones'])
box = z['zones']['C4']['box']; assert box[0] < 40 and box[3] > 800, box                                # top left zone: at the left of the sheet, at the top (PDF y up)
assert z['zones']['A1']['box'][2] > 1100 and z['zones']['A1']['box'][1] < 40, z['zones']['A1']['box']  # bottom right
tb = [t for t in z['tables'] if t['kind'] == 'title block']; assert tb and 'A1' in tb[0]['zones'], z['tables']
assert z['zones']['A1']['class'] == 'title block', z['zones']['A1']['class']
assert len(z['views']) >= 1 and all(v['range'] for v in z['views']), z['views']
top = next(v for v in z['views'] if v['circles'] >= 2); assert top['type'] == 'orthographic' and all(zn[0] in 'CB' for zn in top['zones']), top      # the top view (two holes) is in the upper rows
assert z['dimensions'] >= 6 and sum(len(v['dims']) for v in z['views']) >= 5, (z['dimensions'], [len(v['dims']) for v in z['views']])
assert any(zz['class'] == 'view' for zz in z['zones'].values()) and any(zz['class'] == 'empty' for zz in z['zones'].values())
# a sheet without labels is one zone
plain = G.build(); p2 = os.path.join(os.path.dirname(path), 'plain.pdf'); G.write_pdf(plain, p2); zp = Z.read_zones(p2, 1, virtual_grid=False); assert zp['grid'] is None and list(zp['zones']) == ['*'], zp['zones'].keys()
zv = Z.read_zones(p2, 1); assert zv['grid']['virtual'] and zv['grid']['columns'] == ['6', '5', '4', '3', '2', '1'][-len(zv['grid']['columns']):] and zv['grid']['rows'][0] > zv['grid']['rows'][-1] and len(zv['zones']) == len(zv['grid']['columns']) * len(zv['grid']['rows']), zv['grid']   # numbered right to left, lettered top down
# crops
out = os.path.join(os.path.dirname(path), 'c.png'); Z.render_crop(path, 1, top['box'], 60, out); assert os.path.getsize(out) > 500
assert Z._range(['F8', 'F7', 'E8', 'E7']) == 'F8-E7' and Z._range(['B2', 'C1']) == 'B2+C1' and Z._range(['A1']) == 'A1' and Z._range(['A2', 'A3']) == 'A3-A2' or Z._range(['A2', 'A3']) in ('A2-A3', 'A3-A2')
print('OK: zone grid (4 x 3) read from the margin labels, zone names, title block in A1, views mapped to zone ranges, an unlabelled sheet is one zone, crops rendered')
