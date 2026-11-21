#!/usr/bin/env python3
"""Linker proof of concept on the synthetic drawing (tools/make_test_drawing.py) and a plain 100 x 60 x 10 mm box model written here. Needs pdfminer.six, opencv-python-headless, scipy.
The drawing has holes and corner radii the box lacks, so dimensions that refer to those must come back unlinked, not wrong. usage: python3 tests/test_drawing_link.py"""
import json, os, struct, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
try:
    import numpy as np, cv2, scipy, pdfminer  # noqa
except ImportError as e:
    print('SKIP (missing', e.name + ')'); sys.exit(0)
import make_test_drawing as G
from pdf_drawing import read_pdf
import drawing_link as L


def box_glb(path, size=(0.1, 0.06, 0.01)):
    x, y, z = size
    V = np.array([[a * x, b * y, c * z] for a in (0, 1) for b in (0, 1) for c in (0, 1)], np.float32)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]; I = []
    for a, b, c, d in quads: I += [a, b, c, a, c, d]
    I = np.array(I, np.uint16); vb = V.tobytes(); ib = I.tobytes(); ib += b'\0' * (-len(ib) % 4)
    js = dict(asset=dict(version='2.0'), scene=0, scenes=[dict(nodes=[0])], nodes=[dict(mesh=0)], meshes=[dict(primitives=[dict(attributes=dict(POSITION=0), indices=1)])],
              accessors=[dict(bufferView=0, componentType=5126, count=len(V), type='VEC3', min=V.min(0).tolist(), max=V.max(0).tolist()), dict(bufferView=1, componentType=5123, count=len(I), type='SCALAR')],
              bufferViews=[dict(buffer=0, byteOffset=0, byteLength=len(vb)), dict(buffer=0, byteOffset=len(vb), byteLength=len(I) * 2)], buffers=[dict(byteLength=len(vb) + len(ib))])
    j = json.dumps(js).encode(); j += b' ' * (-len(j) % 4); b = vb + ib
    open(path, 'wb').write(b'glTF' + struct.pack('<II', 2, 12 + 8 + len(j) + 8 + len(b)) + struct.pack('<I', len(j)) + b'JSON' + j + struct.pack('<I', len(b)) + b'BIN\0' + b)


def main():
    tmp = tempfile.mkdtemp(); pdf = os.path.join(tmp, 'd.pdf'); glb = os.path.join(tmp, 'box.glb'); pg = G.build(); G.write_pdf(pg, pdf); box_glb(glb)
    V, F = L.load_glb_world(glb); assert V.shape == (8, 3) and F.shape == (12, 3), (V.shape, F.shape)
    sheet = read_pdf(pdf, keep_lines=True).sheets[0]; res = L.link_sheet(sheet, glb)
    assert len(res['views']) == 2 and all('orientation' in v for v in res['views']), res['views']
    by = {}
    for r in res['dimensions']: by[r['text']] = r
    def find(sub): return next(r for t, r in by.items() if sub in t)
    d1 = find('100 ±0.1'); assert d1['status'] == 'pass' and abs(d1['measured'] - 100.0) < 0.05, d1
    d2 = find('+0.05'); assert d2['status'] == 'pass' and abs(d2['measured'] - 60.0) < 0.05, d2
    d7 = find('10.1'); assert d7['status'] == 'pass' and abs(d7['measured'] - 10.0) < 0.05, d7
    d8 = find('(100)'); assert d8['status'] == 'reference' and abs(d8['measured'] - 100.0) < 0.05, d8
    for r in res['dimensions']: assert r['status'] != 'fail', r                     # nothing in the plate drawing contradicts the box
    unl = [r for r in res['dimensions'] if r['status'] == 'unlinked']; assert len(unl) >= 2, res['dimensions']     # the hole-related dimensions have no feature in a plain box
    # annotated glTF: the CAD geometry is untouched, every linked dimension becomes a line node with its numbers, and the file loads back
    from drawing_gltf import annotate_glb
    out = os.path.join(tmp, 'annotated.glb'); annotate_glb(glb, res, out, source='test'); V2, F2 = L.load_glb_world(out); assert V2.shape == V.shape and F2.shape == F.shape
    js = json.loads(open(out, 'rb').read()[20:20 + struct.unpack_from('<I', open(out, 'rb').read(), 12)[0]])
    dim_nodes = [n for n in js['nodes'] if n.get('name', '').startswith('DIM ')]; assert len(dim_nodes) == sum(1 for r in res['dimensions'] if r.get('line_m')) >= 4
    assert all(js['meshes'][n['mesh']]['primitives'][0]['mode'] == 1 for n in dim_nodes) and all('status' in n['extras'] for n in dim_nodes)
    assert js['scenes'][0]['extras']['drawing_link']['counts'].get('pass', 0) >= 3 and 'KHR_materials_unlit' in js['extensionsUsed']
    print('OK: %d dimensions linked, %d unlinked (no model feature), none failed' % (sum(1 for r in res['dimensions'] if 'measured' in r), len(unl)))


main()
