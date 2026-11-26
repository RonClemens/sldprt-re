#!/usr/bin/env python3
"""Determinacy report on a synthetic assembly written here (no data file): a plate 100 x 40 x 60 mm and a pin 20 x 50 x 20 mm standing on it, with a made-up list of dimensions and BOM.
usage: python3 tests/test_determinacy.py"""
import csv, json, os, struct, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import numpy as np
import determinacy as D


def box(size):
    x, y, z = size; V = np.array([[a * x, b * y, c * z] for a in (0, 1) for b in (0, 1) for c in (0, 1)], np.float32); I = []
    for a, b, c, d in [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]: I += [a, b, c, a, c, d]
    return V, np.array(I, np.uint16)


def write_glb(path, parts):
    """parts: [(name, size_m, translation_m)] as children of one root node"""
    buf = b''; bvs = []; acc = []; meshes = []; nodes = [dict(name='asm', children=list(range(1, len(parts) + 1)))]
    for name, size, t in parts:
        V, I = box(size); o = len(buf); buf += V.tobytes(); bvs.append(dict(buffer=0, byteOffset=o, byteLength=V.nbytes)); acc.append(dict(bufferView=len(bvs) - 1, componentType=5126, count=8, type='VEC3', min=V.min(0).tolist(), max=V.max(0).tolist()))
        o = len(buf); buf += I.tobytes(); buf += b'\0' * (-len(buf) % 4); bvs.append(dict(buffer=0, byteOffset=o, byteLength=I.nbytes)); acc.append(dict(bufferView=len(bvs) - 1, componentType=5123, count=len(I), type='SCALAR'))
        meshes.append(dict(primitives=[dict(attributes=dict(POSITION=len(acc) - 2), indices=len(acc) - 1)])); nodes.append(dict(name=name, mesh=len(meshes) - 1, translation=list(t)))
    js = dict(asset=dict(version='2.0'), scene=0, scenes=[dict(nodes=[0])], nodes=nodes, meshes=meshes, accessors=acc, bufferViews=bvs, buffers=[dict(byteLength=len(buf))])
    j = json.dumps(js).encode(); j += b' ' * (-len(j) % 4)
    open(path, 'wb').write(b'glTF' + struct.pack('<II', 2, 12 + 8 + len(j) + 8 + len(buf)) + struct.pack('<I', len(j)) + b'JSON' + j + struct.pack('<I', len(buf)) + b'BIN\0' + buf)


def dim(i, axis, lo, hi, status='pass', unit='mm', sign=1):
    a = [0, 0, 0]; a[axis] = sign; return dict(id=i, text=str(hi - lo), printed=hi - lo, unit=unit, status=status, model_axis=a, model_range_mm=sorted([sign * lo, sign * hi]), measured=hi - lo)


def main():
    tmp = tempfile.mkdtemp(); glb = os.path.join(tmp, 'asm.glb')
    write_glb(glb, [('10001_PLATE<1>', (0.1, 0.04, 0.06), (0, 0, 0)), ('10002_PIN<1>', (0.02, 0.05, 0.02), (0.04, 0.04, 0.02))])
    comps = D.load_components(glb); assert [c['name'] for c in comps] == ['10001_PLATE<1>', '10002_PIN<1>'], comps
    assert np.allclose(comps[1]['max'] - comps[1]['min'], [20, 50, 20]) and np.allclose(comps[1]['min'], [40, 40, 20]), comps[1]          # millimetres, transform applied
    results = dict(views=[dict(id='V1', ambiguous=False, coverage=dict(u=dict(major_coordinates=6, dimensioned=2), v=dict(major_coordinates=5, dimensioned=1)))], dimensions=[
        dim('D1', 0, 0, 100),                    # the plate's width: its extent
        dim('D2', 0, 0, 50),                     # from the plate's left face to the pin's centre (x 50): locates both
        dim('D3', 1, 40, 90, sign=-1),                    # the pin's extent in y is 50 but it spans plate top to pin top: pin min face + pin max face
        dim('D4', 2, 20, 60, status='reference'),
        dict(id='D5', text='7', status='unlinked', unit='mm')])
    rep = D.analyse(results, comps)
    ax = {r['component']: {a['axis']: a for a in r['axes']} for r in rep['components']}
    assert ax['10001_PLATE<1>']['X']['status'] == 'dimensioned' and ax['10001_PLATE<1>']['X']['by'] == ['D1'], ax['10001_PLATE<1>']['X']
    P, N = ax['10001_PLATE<1>'], ax['10002_PIN<1>']
    assert P['Y']['status'] == 'located' and P['Y']['referenced'] == ['max face'], P['Y']                                       # D3 starts on the plate's top face
    assert P['Z']['status'] == 'undefined', P['Z']                                                                           # only the reference dimension touches z: it fixes nothing
    assert N['X']['status'] == 'located' and N['X']['referenced'] == ['centre'], N['X']                                      # D2 ends on the pin's axis
    assert N['Y']['status'] == 'dimensioned' and N['Y']['by'] == ['D3'] and N['Z']['status'] == 'undefined', N
    assert rep['unlinked'] == ['D5'] and rep['reference'][0]['id'] == 'D4' and rep['unattributed_dimensions'] == [], rep
    assert rep['component_axis_counts'] == dict(dimensioned=2, located=2, undefined=2), rep['component_axis_counts']
    # BOM: the plate's stock is thinner than the CAD plate (40 mm = 1.575 in; stock 1.25 in), and two rows cite the plate's number
    bom = os.path.join(tmp, 'bom.csv')
    with open(bom, 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['item', 'name', 'description', 'length', 'comments']); w.writerow(['1', 'PLATE', 'PLATE, 1 1/4" x 3 15/16"', '2 31/32"', 'SEE DWG 10001']); w.writerow(['2', 'PIN', 'ROUND BAR, 25/32"', '1 31/32"', 'SEE DWG 10001'])
    rep = D.analyse(results, comps, D.read_bom(bom)); fnd = {x['component']: x for x in rep['bom']['findings']}
    assert not fnd['10001_PLATE']['stock']['fits'] and abs(max(fnd['10001_PLATE']['stock']['excess_in']) - (40 / 25.4 - 1.25)) < 0.01, fnd['10001_PLATE']['stock']
    assert any('cite 10001' in i for i in rep['bom']['issues']) and '10002_PIN' in fnd and fnd['10002_PIN']['stock']['fits'], (rep['bom'], )
    assert D.inch_values('1 3/4" x 3 1/2"') == [1.75, 3.5] and D.inch_values('ROUND BAR, 3/4"') == [0.75] and D.inch_values('1-3/8', True) == [1.375] and D.inch_values('FESTO 557067') == []
    md = D.to_markdown(rep, 'synthetic'); assert '**dimensioned**' in md and 'Identification' in md and 'Stock check' in md
    print('OK: components read in mm with transforms, dimensioned / located / undefined classified, BOM stock check and ambiguous citation reported')


main()
