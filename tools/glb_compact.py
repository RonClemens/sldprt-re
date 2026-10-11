#!/usr/bin/env python3
"""Shrink a GLB without changing the geometry: NORMAL float32x3 -> normalized int8 (padded to 4 bytes), indices uint32 -> uint16 where the
mesh has < 65536 vertices. Both are core glTF 2.0 (no extensions); Babylon.js and Three.js read them natively.  usage: glb_compact.py in.glb out.glb"""
import json, struct, sys
import numpy as np
def main(src, dst):
    d = open(src, 'rb').read(); jl = struct.unpack_from('<I', d, 12)[0]; js = json.loads(d[20:20 + jl]); bl = struct.unpack_from('<I', d, 20 + jl)[0]; blob = d[28 + jl:28 + jl + bl]
    normal_acc = set(); index_acc = {}
    for m in js['meshes']:
        for p in m['primitives']:
            normal_acc.add(p['attributes']['NORMAL'])
            if p['attributes']['POSITION'] is not None and js['accessors'][p['attributes']['POSITION']]['count'] < 65536: index_acc[p['indices']] = True
    out = bytearray(); views = []; accs = []
    def add(arr, target, stride=None):
        b = np.ascontiguousarray(arr).tobytes(); off = len(out); out.extend(b); out.extend(b'\0' * ((4 - len(b) % 4) % 4))
        v = {'buffer': 0, 'byteOffset': off, 'byteLength': len(b), 'target': target}
        if stride: v['byteStride'] = stride
        views.append(v); return len(views) - 1
    for i, a in enumerate(js['accessors']):
        bv = js['bufferViews'][a['bufferView']]; raw = blob[bv['byteOffset']:bv['byteOffset'] + bv['byteLength']]; na = dict(a)
        if i in normal_acc and a['componentType'] == 5126:
            n = np.frombuffer(raw, '<f4').reshape(-1, 3); q = np.zeros((len(n), 4), np.int8); q[:, :3] = np.clip(np.rint(n * 127), -127, 127)
            na.update(bufferView=add(q, 34962, 4), componentType=5120, normalized=True); na.pop('min', None); na.pop('max', None)
        elif i in index_acc and a['componentType'] == 5125:
            na.update(bufferView=add(np.frombuffer(raw, '<u4').astype('<u2'), 34963), componentType=5123)
        else:
            na['bufferView'] = add(np.frombuffer(raw, np.uint8), bv.get('target', 34962))
        accs.append(na)
    js['bufferViews'] = views; js['accessors'] = accs; js['buffers'] = [{'byteLength': len(out)}]
    j = json.dumps(js, separators=(',', ':')).encode(); j += b' ' * ((4 - len(j) % 4) % 4)
    with open(dst, 'wb') as f: f.write(struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(j) + 8 + len(out)) + struct.pack('<I4s', len(j), b'JSON') + j + struct.pack('<I4s', len(out), b'BIN\0') + bytes(out))
    print(f'{src}: {len(d)/2**20:.1f} MiB -> {dst}: {(20+len(j)+8+len(out))/2**20:.1f} MiB')
if __name__ == '__main__': main(*sys.argv[1:3])
