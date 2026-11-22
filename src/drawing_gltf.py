"""Write a glTF that holds the CAD mesh plus the drawing's dimensions drawn in the model as line geometry.

  annotate_glb(src_glb, link_results, out_glb, source='') -> None     (also: python3 src/drawing_link.py DRAWING.pdf PAGE MODEL.glb --glb OUT.glb)

Each linear dimension becomes one node 'DIM <id>: <text> -> <status>' with two primitives, on the plane just in front of the model in the view the drawing
shows. LINES (mode 1): extension lines from the model features it spans (they start at real model vertices), the dimension line, an arrowhead at each end. TRIANGLES (mode 4): the label drawn as strokes (glTF has no text, and a
viewer that shows only geometry, such as the Babylon.js sandbox, must still show what the line means: "4 21/32" = 4.6602 IN"), as two single-sided copies so it reads correctly from either side, letters upright. Colour = status: pass green, fail red, unlinked orange, reference /
no-tolerance grey. The numbers also travel in each node's `extras` (text, printed, measured, deviation, tolerance used, status, why unlinked), and a summary in the scene's `extras.drawing_link`
(`vector_labels: true` tells a viewer that already draws its own labels that the file carries them). Line nodes are not triangle meshes, so a viewer that numbers parts by mesh does not count them.
The original nodes, meshes and materials are copied unchanged (KHR_materials_unlit is used for the line materials).
"""
import json, struct
import numpy as np

COLORS = {'pass': (0.15, 0.65, 0.2), 'fail': (0.9, 0.12, 0.12), 'unlinked': (1.0, 0.55, 0.0), 'reference': (0.7, 0.7, 0.7), 'no-tolerance': (0.7, 0.7, 0.7)}

# A stroke font on a 2 x 4 grid (x right, y up); every glyph is a list of polylines. Digits, the marks a dimension label needs, and the letters of the units and status marks.
_G = {
    '0': [[(0, 0), (2, 0), (2, 4), (0, 4), (0, 0)]], '1': [[(1, 0), (1, 4), (0, 3)]], '2': [[(0, 4), (2, 4), (2, 2), (0, 2), (0, 0), (2, 0)]], '3': [[(0, 4), (2, 4), (2, 0), (0, 0)], [(0, 2), (2, 2)]],
    '4': [[(0, 4), (0, 2), (2, 2)], [(2, 4), (2, 0)]], '5': [[(2, 4), (0, 4), (0, 2), (2, 2), (2, 0), (0, 0)]], '6': [[(2, 4), (0, 4), (0, 0), (2, 0), (2, 2), (0, 2)]], '7': [[(0, 4), (2, 4), (2, 0)]],
    '8': [[(0, 0), (2, 0), (2, 4), (0, 4), (0, 0)], [(0, 2), (2, 2)]], '9': [[(2, 2), (0, 2), (0, 4), (2, 4), (2, 0), (0, 0)]],
    '.': [[(0.8, 0), (1.2, 0), (1.2, 0.5), (0.8, 0.5), (0.8, 0)]], '/': [[(0, 0), (2, 4)]], '"': [[(0.5, 4), (0.5, 2.8)], [(1.5, 4), (1.5, 2.8)]], '-': [[(0, 2), (2, 2)]],
    '+': [[(0, 2), (2, 2)], [(1, 1), (1, 3)]], '=': [[(0, 1.2), (2, 1.2)], [(0, 2.8), (2, 2.8)]], '(': [[(1.6, 4), (0.5, 2), (1.6, 0)]], ')': [[(0.4, 4), (1.5, 2), (0.4, 0)]],
    '?': [[(0, 3), (0, 4), (2, 4), (2, 3), (1, 2), (1, 1)], [(0.8, 0), (1.2, 0)]], 'X': [[(0, 0), (2, 4)], [(0, 4), (2, 0)]], 'I': [[(0.4, 0), (1.6, 0)], [(1, 0), (1, 4)], [(0.4, 4), (1.6, 4)]],
    'N': [[(0, 0), (0, 4), (2, 0), (2, 4)]], 'M': [[(0, 0), (0, 4), (1, 2), (2, 4), (2, 0)]], 'O': [[(0, 0), (2, 0), (2, 4), (0, 4), (0, 0)]], 'K': [[(0, 0), (0, 4)], [(2, 4), (0, 1.6)], [(0.6, 2.2), (2, 0)]],
    ' ': [],
}


def text_strokes(text, origin, t, n, height):
    """segments (pairs of 3-D points) that draw `text` starting at `origin`, reading along unit vector t, with unit vector n up, letters `height` tall"""
    s = height / 4.0; pts = []
    for i, ch in enumerate(text.upper()):
        for line in _G.get(ch, _G['?']):
            for (x0, y0), (x1, y1) in zip(line, line[1:]):
                a = origin + t * (x0 * s + i * 3 * s) + n * (y0 * s); b = origin + t * (x1 * s + i * 3 * s) + n * (y1 * s); pts += [a, b]
    return pts


def stroke_quads(segs, w, width, back=False):
    """thin quads (triangles) along each stroke, in the plane with normal w. Wound counter-clockwise seen from +w (or from -w when back), so a single-sided material shows each copy from one side only"""
    tris = []; hw = width / 2
    for i in range(0, len(segs), 2):
        a, b = segs[i], segs[i + 1]; d = b - a; ln = np.linalg.norm(d)
        if ln < 1e-12: continue
        d = d / ln; perp = np.cross(w, d) * hw; A, B, C, D = a + perp, a - perp, b - perp, b + perp
        tris += [A, C, B, A, D, C] if back else [A, B, C, A, C, D]
    return tris


def label_text(r):
    """4 21/32" = 4.6602 IN  (a reference dimension in parentheses; a dimension that did not link ends in ?)"""
    t = ' '.join(str(r.get('text') or r.get('id')).replace('"', '').split())
    if r.get('unit') == 'inch': t += '"'
    if r.get('status') == 'reference': t = f'({t})'
    if r.get('measured') is None: return f'{t} ?'
    return f"{t} = {r['measured']:.4g} {({'inch': 'IN', 'mm': 'MM'}.get(r.get('unit'), ''))}".strip()


def dimension_segments(r):
    """(segments (N,3) as endpoint pairs, text triangles (M,3)) for one linked or unlinked dimension, in model coordinates (metres); (None, None) when the record has no geometry"""
    g = r.get('geom')
    if not g: return None, None
    R = np.array(g['basis'], float); u, v, w = R[0], R[1], R[2]; axis = g['axis']; E = g['extent']; oc = g['offset']; wf = g['w']
    along, across = (u, v) if axis == 0 else (v, u)
    pt = lambda c, o, depth=wf: (c * along + o * across + depth * w)
    a, b = pt(g['c0'], oc), pt(g['c1'], oc)
    segs = [a, b]
    # arrowheads: a small triangle at each end, pointing outwards along the line
    L = 0.025 * E; d = (b - a) / max(np.linalg.norm(b - a), 1e-12)
    for tip, direction in ((a, d), (b, -d)):
        back = tip + direction * L; perp = across * (L / 3.2)
        segs += [tip, back + perp, tip, back - perp, back + perp, back - perp]
    # extension lines: from the model vertex the dimension spans to just past the dimension line
    for k, c in enumerate((g['c0'], g['c1'])):
        vtx = g['ext'][k]
        if vtx is None: continue
        vtx = np.array(vtx, float); other = float(vtx @ across); sign = 1.0 if oc >= other else -1.0
        segs += [vtx, pt(c, oc + sign * 0.012 * E)]
    # the label, centred above the dimension line (for a line running up the view, rotated to read upwards); drawn as thin single-sided quads, one copy readable from the viewer's side, one from the other
    height = 0.035 * E; text = label_text(r); width = (3 * len(text) - 1) * height / 4.0
    up = np.array([0.0, 1.0, 0.0])                            # letters stand up in the world; a line running up the view is read upwards
    if abs(float(along @ up)) > 0.7: t = along * (1.0 if along @ up > 0 else -1.0); n = across
    else:
        t = along; n = across * (-1.0 if across @ up < -0.1 else 1.0)
    centre = (a + b) / 2 + n * (0.9 * height); sw = 0.1 * height
    tw = np.cross(t, n); tw = tw / np.linalg.norm(tw)         # the side the text reads correctly from (the basis can be left-handed, so this need not be +w)
    front = stroke_quads(np.array(text_strokes(text, centre - t * (width / 2), t, n, height)), tw, sw)
    back = stroke_quads(np.array(text_strokes(text, centre + t * (width / 2), -t, n, height)), tw, sw, back=True)
    return np.array(segs, np.float32), np.array(front + back, np.float32).reshape(-1, 3)


def annotate_glb(src, results, out, source=''):
    d = open(src, 'rb').read(); jl = struct.unpack_from('<I', d, 12)[0]; js = json.loads(d[20:20 + jl]); b = bytearray(d[20 + jl + 8:])
    js.setdefault('materials', []); js.setdefault('accessors', []); js.setdefault('bufferViews', []); js.setdefault('meshes', []); js.setdefault('nodes', [])
    ext = js.setdefault('extensionsUsed', [])
    if 'KHR_materials_unlit' not in ext: ext.append('KHR_materials_unlit')
    mat = {}
    for status, rgb in COLORS.items():
        js['materials'].append(dict(name='dim-' + status, pbrMetallicRoughness=dict(baseColorFactor=[*rgb, 1.0], metallicFactor=0.0, roughnessFactor=1.0), extensions=dict(KHR_materials_unlit={}), doubleSided=True)); mat[status] = len(js['materials']) - 1
    mat_text = {}                                                         # single-sided: each copy of a label shows from one side only
    for status, rgb in COLORS.items():
        js['materials'].append(dict(name='dim-text-' + status, pbrMetallicRoughness=dict(baseColorFactor=[*rgb, 1.0], metallicFactor=0.0, roughnessFactor=1.0), extensions=dict(KHR_materials_unlit={}), doubleSided=False)); mat_text[status] = len(js['materials']) - 1
    scene = js['scenes'][js.get('scene', 0)]; added = []
    for r in results['dimensions']:
        P, T = dimension_segments(r)
        if P is None and r.get('line_m'): P, T = np.array(r['line_m'], np.float32), None
        if P is None: continue
        prims = []
        for arr, mode, m in ((P, 1, mat.get(r['status'], mat['unlinked'])), (T, 4, mat_text.get(r['status'], mat_text['unlinked']))):
            if arr is None or not len(arr): continue
            off = len(b); b += arr.tobytes(); b += b'\0' * (-len(b) % 4)
            js['bufferViews'].append(dict(buffer=0, byteOffset=off, byteLength=arr.nbytes)); bv = len(js['bufferViews']) - 1
            js['accessors'].append(dict(bufferView=bv, componentType=5126, count=int(len(arr)), type='VEC3', min=[float(x) for x in arr.min(0)], max=[float(x) for x in arr.max(0)])); ac = len(js['accessors']) - 1
            prims.append(dict(attributes=dict(POSITION=ac), mode=mode, material=m))
        js['meshes'].append(dict(name='DIM ' + r['id'], primitives=prims)); mi = len(js['meshes']) - 1
        extras = {k: r[k] for k in ('id', 'view', 'text', 'printed', 'unit', 'tol', 'measured', 'deviation', 'deviation_mm', 'status', 'why', 'tolerance_used', 'anchor_error_mm') if k in r}
        js['nodes'].append(dict(name=f"DIM {r['id']}: {r['text']} -> {r['status']}", mesh=mi, extras=extras)); added.append(len(js['nodes']) - 1)
    scene.setdefault('nodes', []).extend(added)
    counts = {}
    for r in results['dimensions']: counts[r['status']] = counts.get(r['status'], 0) + 1
    scene['extras'] = dict(scene.get('extras', {}), drawing_link=dict(source=source, general_tolerance=results.get('general_tolerance'), counts=counts, vector_labels=True, views=[{k: v for k, v in x.items() if k != 'dims'} for x in results['views']]))
    if js['buffers']: js['buffers'][0]['byteLength'] = len(b)
    else: js['buffers'] = [dict(byteLength=len(b))]
    j = json.dumps(js, default=float).encode(); j += b' ' * (-len(j) % 4); b += b'\0' * (-len(b) % 4)
    open(out, 'wb').write(b'glTF' + struct.pack('<II', 2, 12 + 8 + len(j) + 8 + len(b)) + struct.pack('<I', len(j)) + b'JSON' + j + struct.pack('<I', len(b)) + b'BIN\0' + bytes(b))
