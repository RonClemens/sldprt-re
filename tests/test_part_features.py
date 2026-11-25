#!/usr/bin/env python3
"""Exact part features (part_features) on the public 3-DOF arm sample parts, and the feature-level determinacy (feature_determinacy) on a made-up feature list and sheet. Needs numpy, scipy and the sample in
.cache/solidworks_urdf_exporter (skipped without it). usage: python3 tests/test_part_features.py"""
import os, sys
from types import SimpleNamespace as NS
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
try:
    import numpy as np, scipy  # noqa
except ImportError as e:
    print('SKIP (missing', e.name + ')'); sys.exit(0)
import feature_determinacy as FD

EX = os.path.join(ROOT, '.cache', 'solidworks_urdf_exporter', 'examples', '3_DOF_ARM')
if os.path.isdir(EX):
    from part_features import part_features
    from sldprt2glb import load_part
    for name in ('3_DOF_ARM_BASE', '3_DOF_ARM_SEGMENT', '3_DOF_ARM_END_EFFECTOR'):
        path = os.path.join(EX, name + '.SLDPRT'); r = part_features(path); P = load_part(path, os.path.join(ROOT, 'src', 'sch_13006_s_t.txt'))[0] * 1000.0
        assert np.allclose(r['bbox_mm'][0], P.min(0), atol=0.05) and np.allclose(r['bbox_mm'][1], P.max(0), atol=0.05), (name, r['bbox_mm'], P.min(0), P.max(0))        # the faces' own extent is the mesh's
        assert r['faces'] == sum(len(p['faces']) for p in r['planes']) + sum(len(c['faces']) for c in r['cylinders']) + len(r['cones']) + len(r['other']), name
        assert all(c['radius_mm'] > 0 and c['kind'] in ('hole', 'boss') and abs(np.linalg.norm(c['axis']) - 1) < 1e-4 for c in r['cylinders']), name
    seg = part_features(os.path.join(EX, '3_DOF_ARM_SEGMENT.SLDPRT')); assert abs(seg['bbox_mm'][1][1] - seg['bbox_mm'][0][1] - 200.0) < 0.05 and any(p['axis'] in ('+Y', '-Y') for p in seg['planes'])
    ee = part_features(os.path.join(EX, '3_DOF_ARM_END_EFFECTOR.SLDPRT')); assert sum(1 for c in ee['cylinders'] if c['kind'] == 'hole') >= 1 and sum(1 for c in ee['cylinders'] if c['kind'] == 'boss') >= 1, 'the end effector has both holes and bosses'
    print('features read from', 3, 'sample parts: faces and extents agree with the mesh')
else: print('(sample parts not present: part_features not run)')

# feature determinacy: a 100 x 50 x 20 mm block (X, Y up, Z) with one vertical through hole dia 10 at (30, 25 from the corner) and a blind dia 6 hole, 8 deep, at x = 70; sheet states some of it
def plane(axis, d, lo, hi): return dict(axis=axis, d_mm=d, normal=[0, 0, 0], faces=[1], lo_mm=lo, hi_mm=hi)
feats = dict(bbox_mm=[[0, 0, 0], [100, 20, 50]], cones=[], other=[], planes=[plane('+X', 100, 0, 1), plane('-X', 0, 0, 1), plane('+Y', 20, 0, 1), plane('-Y', 0, 0, 1), plane('+Z', 50, 0, 1), plane('-Z', 0, 0, 1)], cylinders=[
    dict(kind='hole', axis_label='+Y', axis=[0, 1, 0], radius_mm=5.0, diameter_mm=10.0, foot_mm=[30.0, 0, 25.0], faces=[7], axial_range_mm=[0.0, 20.0]),
    dict(kind='hole', axis_label='+Y', axis=[0, 1, 0], radius_mm=3.0, diameter_mm=6.0, foot_mm=[70.0, 0, 25.0], faces=[8], axial_range_mm=[12.0, 20.0])])
IN = 25.4
dim = lambda i, mm, t: NS(id=i, value=mm / IN, unit='inch', text=t)
sheet = NS(dimensions=[dim('D1', 100, '3.937'), dim('D2', 50, '1.969'), dim('D3', 20, '0.787'), dim('D4', 30, '1.181'), dim('D5', 25, '0.984'), dim('D6', 8, '0.315')], notes=[NS(text='0.394 THRU ALL')])        # 10 mm = 0.3937 in
rep = FD.analyse(feats, sheet)
rows = {r['what']: r for r in rep['rows']}
assert rep['stated_of']['extent'] == [3, 3], rep['stated_of']                                                                           # 100 x 20 x 50 all printed
pos = [r for r in rep['rows'] if r['kind'] == 'position']; ends = [r for r in rep['rows'] if r['kind'] == 'hole end']; dia = {r['inch']: r for r in rep['rows'] if r['kind'] == 'diameter'}
assert all(r['stated'] for r in pos if 'X = ' in r['what'] and r['mm'] in (30.0, 70.0)), pos                                        # x = 30 is printed (D4); x = 70 is 30 from the far face
assert any(r['stated'] and abs(r['mm'] - 25) < 1e-6 for r in pos), pos                                                                   # z = 25 is half of 50: printed 25 from a face
assert len(ends) == 1 and ends[0]['stated'] and ends[0]['by'] == 'D6', ends                                                              # the blind hole ends 8 mm from the top face
assert dia[round(10 / IN, 4)]['stated'] and dia[round(10 / IN, 4)]['by'] == 'callout' and not dia[round(6 / IN, 4)]['stated'], dia      # 10 mm through-all callout; the 6 mm hole's diameter is printed nowhere
md = FD.to_markdown(rep, 'made up'); assert '**no**' in md and 'quantities the drawing must give' in md
print('OK: feature determinacy: extents, positions, a hole end and diameters classified as stated or not')
