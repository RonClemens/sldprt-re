"""Write a glTF that holds the CAD mesh plus the drawing's dimensions as line geometry in model coordinates.

  annotate_glb(src_glb, link_results, out_glb, source='') -> None     (also: python3 src/drawing_link.py DRAWING.pdf PAGE MODEL.glb --glb OUT.glb)

Each linear dimension that has a 3D line (drawing_link.link_sheet's `line_m`) becomes one node 'DIM <id>' with a LINES primitive (mode 1) on the plane in front of the model in that dimension's
view. Colour = status: pass green, fail red, unlinked orange, reference / no-tolerance grey. The numbers travel in each node's `extras` (text, printed, measured, deviation, tolerance used, status,
why it is unlinked), and a summary in the scene's `extras.drawing_link`. Line nodes are not meshes of triangles, so a viewer that numbers parts by triangle mesh does not count them.
The original nodes, meshes and materials are copied unchanged (KHR_materials_unlit is used for the line materials).
"""
import json, struct
import numpy as np

COLORS = {'pass': (0.15, 0.65, 0.2), 'fail': (0.9, 0.12, 0.12), 'unlinked': (1.0, 0.55, 0.0), 'reference': (0.55, 0.55, 0.55), 'no-tolerance': (0.55, 0.55, 0.55)}


def annotate_glb(src, results, out, source=''):
    d = open(src, 'rb').read(); jl = struct.unpack_from('<I', d, 12)[0]; js = json.loads(d[20:20 + jl]); b = bytearray(d[20 + jl + 8:])
    js.setdefault('materials', []); js.setdefault('accessors', []); js.setdefault('bufferViews', []); js.setdefault('meshes', []); js.setdefault('nodes', [])
    ext = js.setdefault('extensionsUsed', [])
    if 'KHR_materials_unlit' not in ext: ext.append('KHR_materials_unlit')
    mat = {}
    for status, rgb in COLORS.items():
        js['materials'].append(dict(name='dim-' + status, pbrMetallicRoughness=dict(baseColorFactor=[*rgb, 1.0], metallicFactor=0.0, roughnessFactor=1.0), extensions=dict(KHR_materials_unlit={}), doubleSided=True)); mat[status] = len(js['materials']) - 1
    scene = js['scenes'][js.get('scene', 0)]; added = []
    for r in results['dimensions']:
        line = r.get('line_m')
        if not line: continue
        P = np.array(line, np.float32); off = len(b); b += P.tobytes(); b += b'\0' * (-len(b) % 4)
        js['bufferViews'].append(dict(buffer=0, byteOffset=off, byteLength=P.nbytes)); bv = len(js['bufferViews']) - 1
        js['accessors'].append(dict(bufferView=bv, componentType=5126, count=2, type='VEC3', min=[float(x) for x in P.min(0)], max=[float(x) for x in P.max(0)])); ac = len(js['accessors']) - 1
        js['meshes'].append(dict(name='DIM ' + r['id'], primitives=[dict(attributes=dict(POSITION=ac), mode=1, material=mat.get(r['status'], mat['unlinked']))])); mi = len(js['meshes']) - 1
        extras = {k: r[k] for k in ('id', 'view', 'text', 'printed', 'unit', 'tol', 'measured', 'deviation', 'deviation_mm', 'status', 'why', 'tolerance_used', 'anchor_error_mm') if k in r}
        js['nodes'].append(dict(name=f"DIM {r['id']}: {r['text']} -> {r['status']}", mesh=mi, extras=extras)); added.append(len(js['nodes']) - 1)
    scene.setdefault('nodes', []).extend(added)
    counts = {}
    for r in results['dimensions']: counts[r['status']] = counts.get(r['status'], 0) + 1
    scene['extras'] = dict(scene.get('extras', {}), drawing_link=dict(source=source, general_tolerance=results.get('general_tolerance'), counts=counts, views=[{k: v for k, v in x.items() if k != 'dims'} for x in results['views']]))
    if js['buffers']: js['buffers'][0]['byteLength'] = len(b)
    else: js['buffers'] = [dict(byteLength=len(b))]
    j = json.dumps(js, default=float).encode(); j += b' ' * (-len(j) % 4); b += b'\0' * (-len(b) % 4)
    open(out, 'wb').write(b'glTF' + struct.pack('<II', 2, 12 + 8 + len(j) + 8 + len(b)) + struct.pack('<I', len(j)) + b'JSON' + j + struct.pack('<I', len(b)) + b'BIN\0' + bytes(b))
