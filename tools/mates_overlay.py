#!/usr/bin/env python3
"""Draw verified mates on top of an assembly GLB so they can be inspected in any glTF viewer (e.g. sandbox.babylonjs.com).
Green = mate holds in the placed assembly with the face its id names; BLUE = holds only after choosing among several faces that share the id
(chosen by the mate itself, not independent); red = does not hold; amber = could not be checked.
Concentric mates are drawn as an axis bar, plane mates as thin plates on each selected plane. Node names carry the numbers.
usage: mates_overlay.py assembly.SLDASM base.glb out.glb [--search ROOT] [--size-mm 60]   (base.glb from sldasm2glb.py)"""
import argparse, json, os, struct, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from mates_resolve import resolve
from verify_mates import judge, SCHEMA
COL = dict(pass_=(0.10, 0.85, 0.25), fail=(0.95, 0.10, 0.10), unchecked=(1.0, 0.7, 0.1), chosen=(0.20, 0.65, 1.0))
def box(center, axis, length, thick):
    """oriented box centred at `center`, long along `axis`; returns flat-shaded (positions, normals, indices)"""
    a = axis / np.linalg.norm(axis); t = np.cross(a, [1, 0, 0]); t = np.cross(a, [0, 1, 0]) if np.linalg.norm(t) < 1e-6 else t
    t /= np.linalg.norm(t); b = np.cross(a, t); h = thick / 2
    corners = np.array([center + sa * a * length / 2 + st * t * h + sb * b * h for sa in (-1, 1) for st in (-1, 1) for sb in (-1, 1)])
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    P, N, I = [], [], []
    for f in faces:
        q = corners[list(f)]; n = np.cross(q[1] - q[0], q[2] - q[0]); n /= max(np.linalg.norm(n), 1e-12)
        if n @ (q.mean(0) - corners.mean(0)) < 0: n = -n; q = q[::-1]
        k = len(P); P += list(q); N += [n] * 4; I += [(k, k + 1, k + 2), (k, k + 2, k + 3)]
    return np.array(P), np.array(N), np.array(I)
def plate(center, normal, side, thick):
    n = normal / np.linalg.norm(normal); u = np.cross(n, [1, 0, 0]); u = np.cross(n, [0, 1, 0]) if np.linalg.norm(u) < 1e-6 else u
    u /= np.linalg.norm(u); v = np.cross(n, u); P, N, I = [], [], []
    for sgn in (1, -1):
        c = center + sgn * n * thick / 2; q = np.array([c + (-u - v) * side / 2, c + (u - v) * side / 2, c + (u + v) * side / 2, c + (-u + v) * side / 2])
        k = len(P); P += list(q); N += [sgn * n] * 4; I += [(k, k + 1, k + 2), (k, k + 2, k + 3)] if sgn == 1 else [(k, k + 2, k + 1), (k, k + 3, k + 2)]
    return np.array(P), np.array(N), np.array(I)
def build(asm, search, size_m):
    os.environ['SLD_SEARCH_ROOT'] = search
    mates, inst, models, root = resolve(asm, os.path.dirname(asm), SCHEMA); items = []; tally = {'pass': 0, 'fail': 0, 'unchecked': 0, 'undecoded': 0}
    for m in mates:
        st, det = judge(m); tally[st] += 1
        if st == 'undecoded': continue
        col = COL['chosen'] if (st == 'pass' and m.get('disambiguated')) else COL['pass_' if st == 'pass' else st] if st in ('pass', 'fail', 'unchecked') else COL['unchecked']
        if st == 'pass' and m.get('disambiguated'): det += ' [faces chosen by the mate itself]'
        f = [e['face'] for e in m['entities']]
        if m['type'] == 'concentric':
            for k, x in enumerate(f):
                if 'point' in x: items.append((f"{st.upper()} {m['name']} {det}", box(x['point'], x['dir'], size_m * 6, size_m * (0.5 if k == 0 else 0.3)), col))
        else:
            for k, x in enumerate(f):
                if 'point' in x and x.get('kind') == 'PLANE': items.append((f"{st.upper()} {m['name']} {det}", plate(x['point'], x['dir'], size_m * (2.0 if k == 0 else 1.4), size_m * 0.12), col))
    return items, tally
def merge(base_path, items, out_path):
    d = open(base_path, 'rb').read(); jl = struct.unpack_from('<I', d, 12)[0]; js = json.loads(d[20:20 + jl]); bl = struct.unpack_from('<I', d, 20 + jl)[0]; blob = bytearray(d[28 + jl:28 + jl + bl])
    def add(arr, comp, typ, target):
        b = np.ascontiguousarray(arr).tobytes(); off = len(blob); blob.extend(b); blob.extend(b'\0' * ((4 - len(b) % 4) % 4))
        js['bufferViews'].append({'buffer': 0, 'byteOffset': off, 'byteLength': len(b), 'target': target}); bv = len(js['bufferViews']) - 1
        acc = {'bufferView': bv, 'componentType': comp, 'count': len(arr), 'type': typ}
        if typ == 'VEC3' and comp == 5126: acc['min'] = arr.min(0).tolist(); acc['max'] = arr.max(0).tolist()
        js['accessors'].append(acc); return len(js['accessors']) - 1
    mats = {}; kids = []
    for name, (P, N, I), col in items:
        if col not in mats:
            js['materials'].append({'name': 'mate-%d' % len(mats), 'pbrMetallicRoughness': {'baseColorFactor': [*col, 1.0], 'metallicFactor': 0.0, 'roughnessFactor': 0.9}, 'emissiveFactor': list(col), 'doubleSided': True}); mats[col] = len(js['materials']) - 1
        a0 = add(P.astype('<f4'), 5126, 'VEC3', 34962); a1 = add(N.astype('<f4'), 5126, 'VEC3', 34962); a2 = add(I.astype('<u4').ravel(), 5125, 'SCALAR', 34963)
        js['meshes'].append({'name': name, 'primitives': [{'attributes': {'POSITION': a0, 'NORMAL': a1}, 'indices': a2, 'material': mats[col], 'mode': 4}]})
        js['nodes'].append({'name': name, 'mesh': len(js['meshes']) - 1}); kids.append(len(js['nodes']) - 1)
    js['nodes'].append({'name': 'MATES OVERLAY', 'children': kids}); js['scenes'][0]['nodes'].append(len(js['nodes']) - 1); js['buffers'][0]['byteLength'] = len(blob)
    j = json.dumps(js, separators=(',', ':')).encode(); j += b' ' * ((4 - len(j) % 4) % 4)
    with open(out_path, 'wb') as f: f.write(struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(j) + 8 + len(blob)) + struct.pack('<I4s', len(j), b'JSON') + j + struct.pack('<I4s', len(blob), b'BIN\0') + bytes(blob))
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('sldasm'); ap.add_argument('base_glb'); ap.add_argument('out_glb'); ap.add_argument('--search', default=''); ap.add_argument('--size-mm', type=float, default=60)
    a = ap.parse_args(); items, tally = build(os.path.abspath(a.sldasm), a.search, a.size_mm / 1000)
    merge(a.base_glb, items, a.out_glb); print('mates drawn:', len(items), 'meshes; tally', tally, '->', a.out_glb)
if __name__ == '__main__': main()
