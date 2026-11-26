#!/usr/bin/env python3
"""Draw a three-view vector PDF (front, top above it, right side to its right: third-angle) of one part of a GLB, to try the drawing tools on geometry that has no drawings of its own.

  python3 tools/make_drawing_from_glb.py MODEL.glb NODE_NAME OUT.pdf [--scale N] [--json OUT.json]       write_part_drawing(glb, node, out_pdf) -> dict

The linework is made here, not drawn by a person: the mesh's feature edges (adjacent faces more than 35 degrees apart, and boundary edges) plus the silhouette edges of each view (one adjacent face towards the viewer, one away) are projected
orthographically and drawn as lines of object width, with no hidden-line removal; three overall dimensions (width and height under and beside the front view, depth beside the top view) are drawn with arrows and text in millimetres, and a SCALE 1 : N label. The
part is viewed in its own coordinates, y up, from +z (front), +y (top) and +x (right). The result tests how the tools cope with other geometry, in a controlled way: it is a drawing of this mesh made by a script, so it is cleaner than a drawing made
by a person and carries no information the mesh did not (the rebuilt solid can be compared with the mesh).
"""
import json, math, os, struct, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import make_test_drawing as G


def load_node(glb, name):
    """-> (V (n,3) mm, F (m,3)) of the first mesh primitive of the node named `name` (own coordinates)"""
    d = open(glb, 'rb').read(); jl = struct.unpack_from('<I', d, 12)[0]; js = json.loads(d[20:20 + jl]); b = d[20 + jl + 8:]
    ct = {5126: '<f4', 5123: '<u2', 5125: '<u4'}
    def acc(i, n):
        a = js['accessors'][i]; bv = js['bufferViews'][a['bufferView']]; return np.frombuffer(b, ct[a['componentType']], a['count'] * n, bv.get('byteOffset', 0) + a.get('byteOffset', 0)).reshape(-1, n) if n > 1 else np.frombuffer(b, ct[a['componentType']], a['count'], bv.get('byteOffset', 0) + a.get('byteOffset', 0))
    node = next(n for n in js['nodes'] if n.get('name') == name and 'mesh' in n); pr = js['meshes'][node['mesh']]['primitives'][0]
    V = acc(pr['attributes']['POSITION'], 3).astype(float) * 1000.0; F = acc(pr['indices'], 1).astype(np.int64).reshape(-1, 3) if 'indices' in pr else np.arange(len(V)).reshape(-1, 3)
    return V, F


def edges_of(V, F, view_dir, feature_deg=35.0):
    """edges of the mesh to draw for a view looking along view_dir: feature + boundary + silhouette -> list of (p, q) 3-D points"""
    key = {}; Vq = np.round(V * 1000).astype(np.int64); ids = {}
    vid = np.array([ids.setdefault(tuple(r), len(ids)) for r in Vq])
    tri = vid[F]; nrm = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]); ln = np.linalg.norm(nrm, axis=1); keep = ln > 1e-12; nrm[keep] /= ln[keep, None]
    emap = {}
    for fi, t in enumerate(tri):
        if not keep[fi] or len({*t}) < 3: continue
        for a, c, ia, ic in ((t[0], t[1], F[fi, 0], F[fi, 1]), (t[1], t[2], F[fi, 1], F[fi, 2]), (t[2], t[0], F[fi, 2], F[fi, 0])): emap.setdefault((min(a, c), max(a, c)), []).append((fi, ia, ic))
    out = []; cosf = math.cos(math.radians(feature_deg)); d = np.array(view_dir, float)
    for (a, c), fl in emap.items():
        fi, ia, ic = fl[0]; p, q = V[ia], V[ic]
        if len(fl) == 1: out.append((p, q)); continue
        n1, n2 = nrm[fl[0][0]], nrm[fl[1][0]]
        if float(n1 @ n2) < cosf: out.append((p, q))
        elif (float(n1 @ d) < 0) != (float(n2 @ d) < 0): out.append((p, q))                  # silhouette: one face towards the viewer, one away
    return out


def write_part_drawing(glb, node, out_pdf, scale_n=None):
    V, F = load_node(glb, node); lo = V.min(0); hi = V.max(0); ext = hi - lo; W, H, Dp = [float(x) for x in ext]
    gap = 45.0
    if scale_n is None:
        for n in (1, 2, 5, 10, 20, 50, 100):
            if (W + Dp) / n + gap + 60 <= 330 and (H + Dp) / n + gap + 40 <= 210: scale_n = n; break
        else: scale_n = 200
    s = 1.0 / scale_n; x0, y0 = 40.0, 45.0
    G.Page.line.__defaults__ = (0.18,)                                                              # dimension and extension lines are thinner than object lines, as on a drawing made by a person
    pg = G.Page(); pg.rect(10, 10, 410, 287, 0.35)
    views = dict(front=(np.array([0, 0, -1.0]), lambda p: (p[0] - lo[0], p[1] - lo[1]), (x0, y0)),
                 top=(np.array([0, -1.0, 0]), lambda p: (p[0] - lo[0], -(p[2] - hi[2])), (x0, y0 + H * s + gap)),                       # page-up = back = -z
                 right=(np.array([-1.0, 0, 0]), lambda p: (-(p[2] - hi[2]), p[1] - lo[1]), (x0 + W * s + gap, y0)))                      # page-right = back = -z
    for nm, (vd, proj, (ox, oy)) in views.items():
        for p, q in edges_of(V, F, vd):
            a = proj(p); b = proj(q)
            if math.hypot(a[0] - b[0], a[1] - b[1]) * s < 0.05: continue
            pg.line(ox + a[0] * s, oy + a[1] * s, ox + b[0] * s, oy + b[1] * s, 0.35)
    fmt = lambda v: ('%.1f' % v).rstrip('0').rstrip('.')
    pg.dim_h('D1', x0, x0 + W * s, y0 - 18, y0, y0, [(-6, 1.2, fmt(W), 3.5)], kind='linear', value=W)
    pg.dim_v('D2', y0, y0 + H * s, x0 - 18, x0, x0, [(-1.5, -3.0, fmt(H), 3.5)], rot=90, kind='linear', value=H)
    pg.dim_v('D3', y0 + H * s + gap, y0 + H * s + gap + Dp * s, x0 - 18, x0, x0, [(-1.5, -3.0, fmt(Dp), 3.5)], rot=90, kind='linear', value=Dp)
    pg.text(x0, y0 - 30, 'SCALE 1 : %d' % scale_n, 3.5); pg.text(250, 20, 'PART ' + node, 3.5)
    G.write_pdf(pg, out_pdf)
    return dict(node=node, scale_n=scale_n, size_mm=[round(W, 3), round(H, 3), round(Dp, 3)], triangles=int(len(F)))


def main():
    a = sys.argv[1:]
    if len(a) < 3: raise SystemExit(__doc__)
    r = write_part_drawing(a[0], a[1], a[2], int(a[a.index('--scale') + 1]) if '--scale' in a else None)
    if '--json' in a: json.dump(r, open(a[a.index('--json') + 1], 'w'))
    print(json.dumps(r))


if __name__ == '__main__':
    main()
