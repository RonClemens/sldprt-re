"""Link a drawing's dimensions to a 3D model and compare (proof of concept).

  link_sheet(sheet, glb_path, unit_per_m=None) -> dict      python3 src/drawing_link.py DRAWING.pdf PAGE MODEL.glb [--png OUT.png] [--json]

For each orthographic view of the sheet (needs the reader's `keep_lines=True`), the model is projected in each of the 24 axis-aligned orientations and aligned to the drawn linework by
minimising how far the projected sharp/silhouette edges lie from drawn lines (scale and shift free, the dimension text is not used). With the view registered, each linear dimension's two
anchors (where its arrows touch) are mapped into model coordinates, snapped to the nearest strong feature coordinate (a plane or edge in the model), and the distance between the snapped
features is the model's value for that dimension. It is compared with the printed value and tolerance: pass / fail / unlinked / no-tolerance.

Limits of this proof of concept: linear dimensions along a view axis only (no angular, radial, aligned or ordinate dimensions); orthographic views only (the isometric view is skipped); a part symmetric
under a view rotation gives an ambiguous orientation (reported); the model is the assembly mesh from sldasm2glb (parts that failed to tessellate are missing from it).
"""
import json, math, os, re, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MM_PER_M = 1000.0


# ----------------------------------------------------------------------------- model
def load_glb_world(path):
    """-> (V (n,3), F (m,3)) in world coordinates (metres): all mesh nodes with their transforms applied"""
    d = open(path, 'rb').read(); jl = struct.unpack_from('<I', d, 12)[0]; js = json.loads(d[20:20 + jl]); b = d[20 + jl + 8:]
    ctype = {5126: np.float32, 5123: np.uint16, 5125: np.uint32, 5121: np.uint8}; ncomp = {'SCALAR': 1, 'VEC3': 3, 'VEC4': 4, 'VEC2': 2}
    def acc(i):
        a = js['accessors'][i]; bv = js['bufferViews'][a['bufferView']]; n = ncomp[a['type']]; dt = np.dtype(ctype[a['componentType']])
        off = bv.get('byteOffset', 0) + a.get('byteOffset', 0); arr = np.frombuffer(b, dt, a['count'] * n, off).reshape(a['count'], n)
        return arr if n > 1 else arr[:, 0]
    nodes = js['nodes']; Vs = []; Fs = []; base = 0
    def mat(n):
        if 'matrix' in n: return np.array(n['matrix'], float).reshape(4, 4).T
        M = np.eye(4)
        if 'translation' in n: M[:3, 3] = n['translation']
        return M
    def walk(i, M):
        nonlocal base
        n = nodes[i]; M = M @ mat(n)
        if 'mesh' in n:
            for pr in js['meshes'][n['mesh']]['primitives']:
                P = acc(pr['attributes']['POSITION']).astype(float); P = P @ M[:3, :3].T + M[:3, 3]
                F = acc(pr['indices']).astype(np.int64).reshape(-1, 3) if 'indices' in pr else np.arange(len(P)).reshape(-1, 3)
                Vs.append(P); Fs.append(F + base); base += len(P)
        for c in n.get('children', []): walk(c, M)
    for r in js['scenes'][js.get('scene', 0)]['nodes']: walk(r, np.eye(4))
    return np.vstack(Vs), np.vstack(Fs)


def feature_points(V, F, sharp_deg=30.0, spacing=0.001):
    """sample points along sharp edges and boundary edges (view independent) and along silhouette edges for the three axis directions.
    -> dict(base=(n,3), sil={0: (n,3), 1: ..., 2: ...}, vertices=(k,3) welded feature vertices)"""
    key = np.round(V / 1e-6).astype(np.int64); _, inv = np.unique(key, axis=0, return_inverse=True); inv = inv.reshape(-1)
    W = np.zeros((inv.max() + 1, 3)); W[inv] = V
    G = inv[F]; ok = (G[:, 0] != G[:, 1]) & (G[:, 1] != G[:, 2]) & (G[:, 0] != G[:, 2]); G = G[ok]
    N = np.cross(W[G[:, 1]] - W[G[:, 0]], W[G[:, 2]] - W[G[:, 0]]); ln = np.linalg.norm(N, axis=1); good = ln > 1e-18; G = G[good]; N = N[good] / ln[good, None]
    E = np.vstack([G[:, [0, 1]], G[:, [1, 2]], G[:, [2, 0]]]); fid = np.tile(np.arange(len(G)), 3); Es = np.sort(E, axis=1)
    order = np.lexsort((Es[:, 1], Es[:, 0])); Es = Es[order]; fid = fid[order]
    same = np.all(Es[1:] == Es[:-1], axis=1); first = np.r_[True, ~same]; grp = np.cumsum(first) - 1; cnt = np.bincount(grp)
    starts = np.where(first)[0]; edges = Es[starts]; f1 = fid[starts]; f2 = np.where(cnt > 1, fid[np.minimum(starts + 1, len(fid) - 1)], -1)
    boundary = cnt == 1; two = cnt == 2
    cosang = np.ones(len(edges)); cosang[two] = (N[f1[two]] * N[f2[two]]).sum(1)
    sharp = boundary | (two & (cosang < math.cos(math.radians(sharp_deg))))
    def sample(sel):
        if not np.any(sel): return np.zeros((0, 3))
        a = W[edges[sel, 0]]; b = W[edges[sel, 1]]; L = np.linalg.norm(b - a, axis=1); n = np.maximum(2, np.ceil(L / spacing).astype(int) + 1)
        t = np.concatenate([np.linspace(0, 1, k) for k in n]); idx = np.repeat(np.arange(len(a)), n)
        return a[idx] + (b[idx] - a[idx]) * t[:, None]
    base = sample(sharp); sil = {}; sil_v = {}
    for ax in range(3):
        d = np.zeros(3); d[ax] = 1.0; s1 = N[f1] @ d; s2 = np.where(f2 >= 0, N[np.maximum(f2, 0)] @ d, 0.0); sel = two & (s1 * s2 < 0) & ~sharp
        sil[ax] = sample(sel)
        sil_v[ax] = np.unique(np.round(W[np.unique(edges[sel])] / 1e-5).astype(np.int64), axis=0) * 1e-5 if np.any(sel) else np.zeros((0, 3))
    vtx = np.unique(np.round(W[np.unique(edges[sharp])] / 1e-5).astype(np.int64), axis=0) * 1e-5
    return dict(base=base, sil=sil, vertices=vtx, sil_vertices=sil_v)


# ----------------------------------------------------------------------------- registration
def _rotations():
    out = []
    for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        for sg in ((1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1), (-1, 1, 1), (-1, 1, -1), (-1, -1, 1), (-1, -1, -1)):
            R = np.zeros((3, 3));
            for r in range(3): R[r, perm[r]] = sg[r]
            if np.linalg.det(R) > 0: out.append(R)
    return out


def register_view(lines, bbox, feat, scale_pt_per_m=None, max_pts=3000):
    """lines: drawn segments (K,4) in points; bbox: the view's linework box. -> best dict(R, s, t, score, ambiguity) or None.
    With a known sheet scale the shift is found by cross-correlating the distance map of the drawn lines with each orientation's projected edge points; without one, the
    scale starts from a bounding-box fit (fragile: stray lines inflate the box)."""
    import cv2
    from scipy.optimize import minimize
    L = np.asarray(lines, float); x0, y0, x1, y1 = bbox; res = 2.0; rng = np.random.default_rng(0); results = []
    rots = _rotations(); pts_of = {}
    for R in rots:
        ax = int(np.argmax(np.abs(R[2])))
        if ax not in pts_of:
            pts = np.vstack([feat['base'], feat['sil'][ax]])
            pts_of[ax] = pts[rng.choice(len(pts), max_pts, replace=False)] if len(pts) > max_pts else pts
    allp = np.vstack(list(pts_of.values())); ext_m = float(np.linalg.norm(allp.max(0) - allp.min(0)))
    s_nom = scale_pt_per_m; margin = (ext_m * (s_nom or 3000.0)) + 20
    X0, Y0, X1, Y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin; w = int((X1 - X0) * res) + 1; h = int((Y1 - Y0) * res) + 1
    img = np.zeros((h, w), np.uint8)
    for a, b, c, d in L: cv2.line(img, (int((a - X0) * res), int((b - Y0) * res)), (int((c - X0) * res), int((d - Y0) * res)), 255, 1)
    dt = np.minimum(cv2.distanceTransform(255 - img, cv2.DIST_L2, 3) / res, 20.0).astype(np.float32)
    def cost(P):
        ix = np.clip(((P[:, 0] - X0) * res).astype(int), 0, w - 1); iy = np.clip(((P[:, 1] - Y0) * res).astype(int), 0, h - 1)
        off = (P[:, 0] < X0) | (P[:, 0] > X1) | (P[:, 1] < Y0) | (P[:, 1] > Y1); return float(np.mean(np.where(off, 20.0, dt[iy, ix])))
    for R in rots:
        ax = int(np.argmax(np.abs(R[2]))); pts = pts_of[ax]; P2 = pts @ R[:2].T; mn, mx = P2.min(0), P2.max(0)
        if mx[0] - mn[0] < 1e-6 or mx[1] - mn[1] < 1e-6: continue
        if s_nom:
            s0 = s_nom; Q = P2 * s0; qmn = Q.min(0); tw = int((Q[:, 0].max() - qmn[0]) * res) + 2; th = int((Q[:, 1].max() - qmn[1]) * res) + 2
            if tw >= w or th >= h: continue
            tmpl = np.zeros((th, tw), np.float32); tmpl[((Q[:, 1] - qmn[1]) * res).astype(int), ((Q[:, 0] - qmn[0]) * res).astype(int)] = 1.0
            cc = cv2.matchTemplate(dt, tmpl, cv2.TM_CCORR) / max(tmpl.sum(), 1.0); j, i = np.unravel_index(np.argmin(cc), cc.shape)[::-1][0], np.unravel_index(np.argmin(cc), cc.shape)[0]
            t0 = np.array([X0 + j / res - qmn[0], Y0 + i / res - qmn[1]])
        else:
            Wd, Hd = x1 - x0, y1 - y0; sx, sy = Wd / (mx[0] - mn[0]), Hd / (mx[1] - mn[1])
            if abs(sx / sy - 1) > 0.12: continue
            s0 = (sx + sy) / 2; t0 = np.array([(x0 + x1) / 2, (y0 + y1) / 2]) - s0 * (mn + mx) / 2
        f = lambda q: cost(P2 * q[0] + q[1:3]) if 0.93 * s0 <= q[0] <= 1.07 * s0 else 1e3
        r = minimize(f, [s0, t0[0], t0[1]], method='Nelder-Mead', options=dict(xatol=1e-3, fatol=1e-4, maxiter=250, initial_simplex=np.array([[s0, t0[0], t0[1]], [s0 * 1.01, t0[0], t0[1]], [s0, t0[0] + 2, t0[1]], [s0, t0[0], t0[1] + 2]])))
        results.append(dict(R=R, s=float(r.x[0]), t=r.x[1:3].copy(), score=float(r.fun), s0=float(s0)))
    if not results: return None
    results.sort(key=lambda d: d['score']); best = results[0]
    rivals = [d for d in results[1:] if not np.allclose(d['R'], best['R'])]
    best['rival_score'] = rivals[0]['score'] if rivals else None
    best['ambiguous'] = bool(rivals and rivals[0]['score'] < 1.15 * best['score'] + 0.05)
    return best


# ----------------------------------------------------------------------------- linking
def _clusters(vals, tol=5e-5, min_count=3):
    """strong coordinates: values (metres) where at least min_count feature vertices coincide within tol"""
    v = np.sort(np.asarray(vals)); out = []; i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[j + 1] - v[j] <= tol: j += 1
        if j - i + 1 >= min_count: out.append(float(np.mean(v[i:j + 1])))
        i = j + 1
    return np.array(out)


def general_tolerance(sheet):
    """the title block's general tolerance for fractional dimensions ('FRACTION +- 1/16'), in the sheet's unit, or None"""
    text = ' '.join(n.text for n in sheet.notes if n.kind == 'title_block')
    m = re.search(r'FRACTION\s*[±+\-]*\s*(\d+)\s*/\s*(\d+)', text)       # the +- is often a drawn glyph, not text
    return int(m.group(1)) / int(m.group(2)) if m else None


def link_sheet(sheet, glb_path, snap_mm=1.5):
    V, F = load_glb_world(glb_path); feat = feature_points(V, F)
    gtol = general_tolerance(sheet); results = dict(views=[], dimensions=[], general_tolerance=gtol, model=dict(bbox_mm=[round(float(x) * MM_PER_M, 3) for x in np.r_[V.min(0), V.max(0)]], triangles=int(len(F))))
    uconv = 25.4 if sheet.unit == 'inch' else 1.0            # millimetres per drawing unit
    from drawing_ir import MM_PER_PT
    scale_pt_per_m = (1000.0 * sheet.scale / MM_PER_PT) if sheet.scale else None
    for v in sheet.views:
        dims = [d for d in sheet.dimensions if d.view == v.id and d.style == 'paired' and d.value is not None]
        if not dims or not v.lines or len(v.lines) < 4: continue
        reg = register_view(v.lines, v.bbox, feat, scale_pt_per_m)
        if reg is None: results['views'].append(dict(id=v.id, status='no orientation fits the view proportions')); continue
        R, s, t = reg['R'], reg['s'], reg['t']
        vinfo = dict(id=v.id, orientation=[[int(x) for x in row] for row in R], score_pt=round(reg['score'], 3), scale_pt_per_mm=round(s / MM_PER_M, 5), ambiguous=reg['ambiguous'], dims=[d.id for d in dims])
        results['views'].append(vinfo)
        allv = np.vstack([feat['vertices'], feat['sil_vertices'][int(np.argmax(np.abs(R[2])))]])      # true vertices only: sampled edge points would make every coordinate look like a feature
        U = allv @ R[0]; Vv = allv @ R[1]; strong = {0: _clusters(U), 1: _clusters(Vv)}; major = {0: _clusters(U, min_count=12), 1: _clusters(Vv, min_count=12)}; used = {0: set(), 1: set()}
        for d in dims:
            a, b = np.array(d.tips[0]), np.array(d.tips[1]); dv = b - a; ln = np.linalg.norm(dv); rec = dict(id=d.id, view=v.id, text=d.text, printed=d.value, unit=sheet.unit, tol=d.tol.kind)
            axis = 0 if abs(dv[0]) > 0.99 * ln else 1 if abs(dv[1]) > 0.99 * ln else None
            if axis is None: rec.update(status='unlinked', why='not along a view axis'); results['dimensions'].append(rec); continue
            ca = (a[axis] - t[axis]) / s; cb = (b[axis] - t[axis]) / s; S_ = strong[axis]; tol_m = snap_mm / MM_PER_M
            if len(S_) == 0: rec.update(status='unlinked', why='no model features'); results['dimensions'].append(rec); continue
            sa = S_[np.argmin(np.abs(S_ - ca))]; sb = S_[np.argmin(np.abs(S_ - cb))]
            rec['anchor_error_mm'] = [round(abs(sa - ca) * MM_PER_M, 3), round(abs(sb - cb) * MM_PER_M, 3)]
            if abs(sa - ca) > tol_m or abs(sb - cb) > tol_m or sa == sb:
                far = max(abs(sa - ca), abs(sb - cb)) * MM_PER_M
                rec.update(status='unlinked', why='an anchor has no model feature within %.1f mm (nearest is %.2f mm = %.4g %s away)' % (snap_mm, far, far / uconv, sheet.unit)); results['dimensions'].append(rec); continue
            meas_mm = abs(sb - sa) * MM_PER_M; meas = meas_mm / uconv; dev = meas - d.value
            used[axis].update([float(sa), float(sb)])
            rec.update(measured=round(meas, 5), deviation=round(dev, 5), deviation_mm=round(dev * uconv, 3), model_axis=[int(x) for x in R[axis]], model_range_mm=[round(float(min(sa, sb)) * MM_PER_M, 3), round(float(max(sa, sb)) * MM_PER_M, 3)])
            if d.tol.kind == 'reference': rec['status'] = 'reference'
            elif d.tol.kind == 'bilateral' and d.tol.lower is not None: rec['status'] = 'pass' if d.tol.lower - 1e-9 <= dev <= d.tol.upper + 1e-9 else 'fail'
            elif d.tol.kind == 'limits': rec['status'] = 'pass' if d.tol.lower - 1e-9 <= meas <= d.tol.upper + 1e-9 else 'fail'
            elif d.tol.kind == 'none' and gtol is not None and sheet.unit == 'inch' and '/' in d.text: rec['status'] = 'pass' if abs(dev) <= gtol + 1e-9 else 'fail'; rec['tolerance_used'] = f'general +-{gtol:.4g}'
            else: rec['status'] = 'no-tolerance'
            results['dimensions'].append(rec)
        vinfo['coverage'] = {ax: dict(major_coordinates=int(len(major[i])), dimensioned=int(sum(1 for c in major[i] if any(abs(c - u) < 2e-4 for u in used[i])))) for i, ax in ((0, 'u'), (1, 'v'))}
    return results


def render_png(sheet, glb_path, results, out):
    import cv2
    V, F = load_glb_world(glb_path); feat = feature_points(V, F); panels = []
    for vi in [x for x in results['views'] if 'orientation' in x]:
        v = next(x for x in sheet.views if x.id == vi['id']); R = np.array(vi['orientation'], float); s = vi['scale_pt_per_mm'] * MM_PER_M
        reg = register_view(v.lines, v.bbox, feat, (1000.0 * sheet.scale / (25.4 / 72)) if sheet.scale else None); t = reg['t']; x0, y0, x1, y1 = v.bbox; sc = 3.0; w = int((x1 - x0 + 90) * sc); h = int((y1 - y0 + 60) * sc)
        img = np.full((h, w, 3), 255, np.uint8); P = lambda x, y: (int((x - x0 + 45) * sc), int((y1 - y + 30) * sc))
        for a, b, c, d in v.lines: cv2.line(img, P(a, b), P(c, d), (205, 205, 205), 1)
        ax = int(np.argmax(np.abs(R[2]))); pts = np.vstack([feat['base'], feat['sil'][ax]])[::3] @ R[:2].T * s + t
        for p in pts: cv2.circle(img, P(*p), 1, (200, 120, 40), -1)
        for r in results['dimensions']:
            if r['view'] != v.id: continue
            d = next(x for x in sheet.dimensions if x.id == r['id']); col = {'pass': (60, 160, 60), 'fail': (40, 40, 220), 'reference': (150, 150, 150)}.get(r['status'], (0, 140, 230))
            cv2.line(img, P(*d.tips[0]), P(*d.tips[1]), col, 2); lab = f"{r['text']} -> " + (f"{r['measured']:.4g} ({r['status']})" if 'measured' in r else r['status'])
            cv2.putText(img, lab, (P(*d.tips[0])[0] + 6, P(*d.tips[0])[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        panels.append(img)
    if panels:
        H_ = max(p.shape[0] for p in panels); panels = [np.pad(p, ((0, H_ - p.shape[0]), (0, 10), (0, 0)), constant_values=255) for p in panels]; cv2.imwrite(out, np.hstack(panels))


def main():
    from pdf_drawing import read_pdf
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) < 3: raise SystemExit(__doc__)
    sheet = read_pdf(args[0], pages=[int(args[1])], keep_lines=True).sheets[0]; res = link_sheet(sheet, args[2])
    if '--png' in sys.argv: render_png(sheet, args[2], res, sys.argv[sys.argv.index('--png') + 1])
    if '--json' in sys.argv: print(json.dumps(res, indent=1)); return
    print('model bbox (mm):', res['model']['bbox_mm'], 'triangles', res['model']['triangles'], '| general tolerance', res['general_tolerance'])
    for v in res['views']: print('view', v)
    for r in res['dimensions']:
        print(f"  {r['id']} {r['text']!r:16s} printed {r['printed']:<9.5g} measured {r.get('measured', '-')!s:<9} dev {r.get('deviation', '-')!s:<9} {r['status']:<13} {r.get('why', '')}")


if __name__ == '__main__':
    main()
