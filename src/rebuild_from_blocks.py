#!/usr/bin/env python3
"""Rebuild a part's 3D model from the building blocks of its drawing (view roles, scales, silhouettes, holes, slots) and say how much of it is known.

  python3 src/rebuild_from_blocks.py DRAWING.pdf PAGE OUT_DIR --fcpython /path/to/freecadcmd [--cad PART.SLDPRT] [--projection third|first] [--unit inch|mm]
  rebuild(pdf, page, out_dir, fcpython, ...) -> dict        (writes part_from_drawing.step, spec.json, provenance.json, report.md; with --cad also comparison.json)

How: the orthographic views of the part (front, top or bottom, a side) come from sheet_zones / view_features, each with its scale. The silhouette of each view is taken from its object linework (the outline is rasterised, closed and
traced), so rounded corners, chamfers and an L or channel profile are in it. The solid is the visual hull: the intersection of the three silhouettes, each extended along its viewing direction (when only two views exist, two).
Holes, slots and steps are cut into it from the circles and stadium outlines in the views, to the depths the neighbouring view shows (through when no depth is found, and said so). Part frame: X across the front view, Y up it, Z the depth from
the front face; the face seen in a view is the near face of its holes. Layout is read as third-angle projection unless told otherwise.

Every value carries a status in provenance.json and report.md:
  stated        a dimension or callout printed on the sheet equals it (within the measuring resolution of the view: one drawn point or 1/64 in)
  unconfirmed   measured from the drawn linework at the view's scale, or inferred; to be confirmed with the design engineer. Each says which: `measured`, `assumed through`, `assumed centred`, `silhouette`, `visual hull`.
The visual hull over-fills what no view shows: a pocket, a thin rib or gusset standing inside the profile, a cavity. A feature seen in one view only is cut through the whole part. The report lists these cases under "not shown by the views".

With --cad the result is compared with the part's exact features (part_features): extents, each hole (axis, position, diameter), and a volume overlap (intersection over union of points sampled in the CAD's bounding box, the CAD side
tested with a ray-parity test on its mesh, so only as good as the mesh). The axis permutation and flips that best map the rebuilt frame onto the CAD's are searched; they are not known from the drawing.
"""
import itertools, json, os, subprocess, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
IN = 25.4; RES = 4                                                                                # raster pixels per drawing point for the silhouettes


def silhouette(S, geo, box, objw, scale):
    """outline polygon of the view's object linework -> dict(points [(u, v) in inches from the box's lower left], area_in2, box_fill)"""
    import cv2
    x0, y0, x1, y1 = box; W = int((x1 - x0) * RES) + 12; H = int((y1 - y0) * RES) + 12; m = np.zeros((H, W), np.uint8)
    P = lambda x, y: (int(round((x - x0) * RES)) + 6, H - 6 - int(round((y - y0) * RES)))
    for sx0, sy0, sx1, sy1, w in S:
        if w < objw or not (x0 - 1 <= min(sx0, sx1) and max(sx0, sx1) <= x1 + 1 and y0 - 1 <= min(sy0, sy1) and max(sy0, sy1) <= y1 + 1): continue
        cv2.line(m, P(sx0, sy0), P(sx1, sy1), 255, 2)
    for k, a in enumerate(geo.arcs):                                                              # arcs the reader fitted: the part of the circle inside the arc's own box
        if (geo.arc_w[k] if k < len(geo.arc_w) else objw) < objw or not (x0 - 1 <= a[0] <= x1 + 1 and y0 - 1 <= a[1] <= y1 + 1): continue
        t = np.linspace(0, 2 * np.pi, 180); px = a[0] + a[2] * np.cos(t); py = a[1] + a[2] * np.sin(t); keep = (px >= a[3][0] - 0.4) & (px <= a[3][2] + 0.4) & (py >= a[3][1] - 0.4) & (py <= a[3][3] + 0.4)
        pts = np.array([P(x, y) for x, y in zip(px[keep], py[keep])], np.int32)
        if len(pts) > 1: cv2.polylines(m, [pts.reshape(-1, 1, 2)], False, 255, 2)
    m = cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=2); out = m.copy(); ff = np.zeros((H + 2, W + 2), np.uint8); cv2.floodFill(out, ff, (0, 0), 255)
    solid = (out != 255).astype(np.uint8) * 255; solid = cv2.bitwise_or(solid, (m > 0).astype(np.uint8) * 255)
    cs, _ = cv2.findContours(solid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs: return None
    c = max(cs, key=cv2.contourArea); c = cv2.approxPolyDP(c, 1.6, True).reshape(-1, 2)
    pts = [(round(float((px - 6) / RES / scale), 4), round(float((H - 6 - py) / RES / scale), 4)) for px, py in c]
    area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2; bb = ((x1 - x0) / scale) * ((y1 - y0) / scale)
    return dict(points=pts, area_in2=round(area, 4), box_fill=round(area / bb, 4) if bb else 0)


def _status(val, pool, tol):
    """('stated', id) when a printed value in the pool equals val within tol, else ('unconfirmed', None)"""
    best = min(((abs(v - val), i) for i, v in pool if abs(v - val) <= tol), default=None)
    return ('stated', best[1]) if best else ('unconfirmed', None)



def _pip(pt, poly):
    x, y = pt; ins = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0 + 1e-12) + x0: ins = not ins
    return ins


def find_rib_triangle(points, X, Y, area):
    """a plate standing in the inside corner of an angle, seen edge-on in the front view: the outline has a diagonal edge (the plate's slanted side); from one end of it the outline runs back along a long vertical edge (one leg), from the other end
    on to a long horizontal edge (the other leg). The plate's region is bounded by the diagonal, the pieces of outline between it and the two legs, and the legs' inside corner C. -> dict(polygon, C, P, Q, area_in2) in inches, or None"""
    n = len(points); tol = 0.03; pts = [np.array(p, float) for p in points]
    def vert(j, k): return abs(pts[j][0] - pts[k][0]) < tol and abs(pts[j][1] - pts[k][1]) >= 0.08 * Y
    def horiz(j, k): return abs(pts[j][1] - pts[k][1]) < tol and abs(pts[j][0] - pts[k][0]) >= 0.08 * X
    for i in range(n):
        j = (i + 1) % n; d = pts[j] - pts[i]
        if abs(d[0]) < 0.15 or abs(d[1]) < 0.15 or np.hypot(*d) < 0.15 * max(X, Y): continue
        for a_idx, b_idx, step in ((i, j, -1), (j, i, 1)):                           # walk away from a (backwards or forwards) to a vertical leg edge, away from b the other way to a horizontal one
            for first, second in ((vert, horiz), (horiz, vert)):
                ka = next((k for k in range(1, 7) if first((a_idx + step * k) % n, (a_idx + step * (k - 1)) % n)), None)
                kb = next((k for k in range(1, 7) if second((b_idx - step * k) % n, (b_idx - step * (k - 1)) % n)), None)
                if ka is None or kb is None: continue
                la = pts[(a_idx + step * ka) % n]; lb = pts[(b_idx - step * kb) % n]
                C = np.array([la[0], lb[1]]) if first is vert else np.array([lb[0], la[1]])
                chain = [pts[(a_idx + step * k) % n] for k in range(ka - 1, -1, -1)] + [pts[(b_idx - step * k) % n] for k in range(0, kb)]       # up to the end of each leg edge nearest the diagonal
                poly = [C] + chain; ar = abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(poly, poly[1:] + poly[:1]))) / 2
                if ar >= 0.05 * area and ar <= 0.75 * area and _pip(tuple(np.mean(poly, axis=0)), [tuple(p) for p in points]):
                    return dict(polygon=[[round(float(p[0]), 4), round(float(p[1]), 4)] for p in poly], C=[round(float(C[0]), 4), round(float(C[1]), 4)], P=[float(pts[a_idx][0]), float(pts[a_idx][1])], Q=[float(pts[b_idx][0]), float(pts[b_idx][1])], area_in2=round(float(ar), 4))
    return None


def find_rib_span(S, views, by_role, tri, D, X, Y, objw):
    """the rib's extent along the depth: a pair of long object lines inside a side view (vertical, running along the height) or inside a top / bottom view (horizontal, running along the width), a rib's two faces. -> (z1, z2, view id, evidence) or None"""
    cands = []
    for role in ('left side', 'right side'):
        v = by_role.get(role)
        if not v: continue
        b = v['box']; s = v['scale_pt_per_in']; H = (b[3] - b[1]) / s; W = (b[2] - b[0]) / s
        xs = sorted({round(float((x0 - b[0]) / s), 3) for x0, y0, x1, y1, w in S if w >= objw and abs(x0 - x1) < 0.1 and b[0] + 1.5 < x0 < b[2] - 1.5 and b[1] - 1 <= min(y0, y1) and max(y0, y1) <= b[3] + 1 and abs(y1 - y0) / s >= 0.4 * H})
        for a, c in zip(xs, xs[1:]):
            if 0.05 <= c - a <= 0.35 * W: cands.append((c - a, (a, c), v, role))
    for role in ('top', 'bottom'):
        v = by_role.get(role)
        if not v: continue
        b = v['box']; s = v['scale_pt_per_in']; W = (b[2] - b[0]) / s; Hh = (b[3] - b[1]) / s
        ys = sorted({round(float((y0 - b[1]) / s), 3) for x0, y0, x1, y1, w in S if w >= objw and abs(y0 - y1) < 0.1 and b[1] + 1.5 < y0 < b[3] - 1.5 and b[0] - 1 <= min(x0, x1) and max(x0, x1) <= b[2] + 1 and abs(x1 - x0) / s >= 0.5 * abs(tri['Q'][0] - tri['P'][0])})
        for a, c in zip(ys, ys[1:]):
            if 0.05 <= c - a <= 0.35 * Hh: cands.append((c - a, (a, c), v, role))
    if not cands: return None
    t, (a, c), v, role = min(cands, key=lambda k: k[0])
    z = {'right side': (a, c), 'left side': (D - c, D - a), 'top': (a, c), 'bottom': (D - c, D - a)}[role]
    return dict(z1=round(z[0], 4), z2=round(z[1], 4), thickness=round(t, 4), view=v['id'], role=role, scale=v['scale_pt_per_in'])


def build_spec(pdf, page, projection='third', unit=None):
    import drawing_part as DP, sheet_zones as Z
    from pdf_drawing import read_pdf
    z = Z.read_zones(pdf, page, projection, unit, virtual_grid=False); sheet = read_pdf(pdf, pages=[page]).sheets[0]
    if unit is None and z.get('unit_guess'): unit = z['unit_guess']['chosen']                 # the sheet had no inch marks: the zone reader chose the unit the linework supports
    if unit in ('inch', 'mm'):
        for d in sheet.dimensions: d.unit = unit
    S, geo = DP.read_segments(pdf, page); C = DP.circles(S); objw = (float(np.median([c['w'] for c in C])) - 0.05) if C else 0.6
    views = [v for v in z['views'] if v['role'] in ('front', 'top', 'bottom', 'right side', 'left side') and v.get('scale_pt_per_in')]
    if not any(v['role'] == 'front' for v in views): return dict(error='no front view with a scale was found among the views')
    by_role = {v['role']: v for v in views}; unc = []; prov = []
    dims = {d.id: (d.value if d.unit == 'inch' else d.value / IN) for d in sheet.dimensions if d.value}
    def pool(v): return [(i, dims[i]) for i in v['dims'] if i in dims]
    def extent(v, axis):
        b = v['box']; s = v['scale_pt_per_in']; return ((b[2] - b[0]) if axis == 'u' else (b[3] - b[1])) / s
    fr = by_role['front']; X = extent(fr, 'u'); Y = extent(fr, 'v'); D = None; D_src = None
    for r in ('top', 'bottom'):
        if r in by_role: D = extent(by_role[r], 'v'); D_src = r; break
    if D is None:
        for r in ('right side', 'left side'):
            if r in by_role: D = extent(by_role[r], 'u'); D_src = r; break
    sizes = {}
    for nm, val, v in (('X (width, across the front view)', X, fr), ('Y (height)', Y, fr)) + ((('Z (depth)', D, by_role[D_src]),) if D else ()):
        sharing = {'X': ('front', 'top', 'bottom'), 'Y': ('front', 'right side', 'left side'), 'Z': ('top', 'bottom', 'right side', 'left side')}[nm[0]]       # a size is printed in any view that shows that axis
        st, i = _status(val, [x for r in sharing if r in by_role for x in pool(by_role[r])], max(1 / 64, 1.2 / v['scale_pt_per_in'])); sizes[nm] = (val, st, i)
        prov.append(dict(name='overall ' + nm, value=round(val, 4), status=st, evidence=f'printed {i}' if i else f'measured from the {v["role"]} view outline at {v["scale_pt_per_in"]} pt per inch'))
    if D is None: unc.append('no top, bottom or side view: the depth is not known and the solid is not built'); return dict(error=unc[-1])
    # the views must agree on the sizes they share: widths of front and top/bottom, heights of front and side, depths of top/bottom and side
    warnings = []
    def ext_of(role, axis): return extent(by_role[role], axis) if role in by_role else None
    for nm, pairs in (('width X', [('front', 'u'), ('top', 'u'), ('bottom', 'u')]), ('height Y', [('front', 'v'), ('right side', 'v'), ('left side', 'v')]), ('depth Z', [('top', 'v'), ('bottom', 'v'), ('right side', 'u'), ('left side', 'u')])):
        vals = [(r, ext_of(r, a)) for r, a in pairs if ext_of(r, a) is not None]
        if len(vals) >= 2 and (max(v for _, v in vals) - min(v for _, v in vals)) > 0.03 * max(v for _, v in vals): warnings.append(f"the views disagree on the {nm}: " + ', '.join(f'{r} {v:.3f} in' for r, v in vals) + ' (a broken or foreshortened view, a detail at another scale, a different part, or a wrong scale); the solid is not trustworthy')
    unc.extend(warnings)
    # silhouettes and their cross-check
    sil = {}
    for v in views:
        sg = silhouette(S, geo, v['box'], objw, v['scale_pt_per_in'])
        if sg: sil[v['role']] = dict(view=v['id'], **sg)
    # frame mapping of each view: (u, v) -> part coordinates
    def to_part(role, u, vv):
        return {'front': (u, vv, 0.0), 'top': (u, 0.0, vv), 'bottom': (u, 0.0, D - vv), 'right side': (0.0, vv, u), 'left side': (0.0, vv, D - u)}[role]
    holes = []; slots = []; notes = []
    for v in views:
        s = v['scale_pt_per_in']; b = v['box']; pl = pool(v)
        for f in v['features']:
            u = (f['centre_pt'][0] - b[0]) / s; vv = (f['centre_pt'][1] - b[1]) / s; px, py, pz = to_part(v['role'], u, vv); hw = extent(v, 'u'); hh = extent(v, 'v')
            pos = []
            for nm, val in (('from the left of its view', u), ('from the right of its view', hw - u), ('from the bottom of its view', vv), ('from the top of its view', hh - vv)):
                st, i = _status(val, pl, max(1 / 64, 1.2 / s)); pos.append(dict(what=nm, value=round(val, 4), status=st, evidence=f'printed {i}' if i else 'measured'));
            steps = []
            for k, d in enumerate(f['diameters_in']):
                length = None; side = None
                if len(f['diameters_in']) == 1: length = f.get('depth_in') if 'depth_in' in f else None; side = f.get('depth_from')
                elif k == 0: length = f.get('outer_step_length_in'); side = f.get('outer_from')
                elif k == len(f['diameters_in']) - 1: length = f.get('inner_step_length_in'); side = f.get('inner_from')
                st, i = _status(d, pl + [('callout', c) for c in []], max(1 / 64, 1.2 / s)); st_len = _status(length, pl, max(1 / 64, 1.2 / s)) if length else ('unconfirmed', None)
                steps.append(dict(diameter_in=d, diameter_status=st, diameter_evidence=f'printed {i}' if i else 'measured' if not f['callout'] else 'measured (callout: ' + f['callout'] + ')', length_in=length, length_from=side, length_status=st_len[0] if length else 'unconfirmed: through assumed (no depth in a neighbouring view)', length_evidence=f'printed {st_len[1]}' if st_len[1] else ('measured in the ' + (f['profile_from'] or '?') + ' view' if length else '')))
            holes.append(dict(view=v['id'], role=v['role'], centre_in_part=[round(px, 4), round(py, 4), round(pz, 4)], type=f['type'], steps=steps, position=pos, callout=f['callout']))
        for sl in v['slots']:
            u = (sl['centre_pt'][0] - b[0]) / s; vv = (sl['centre_pt'][1] - b[1]) / s; px, py, pz = to_part(v['role'], u, vv)
            slots.append(dict(view=v['id'], role=v['role'], centre_in_part=[round(px, 4), round(py, 4), round(pz, 4)], width_in=sl['width_in'], length_between_centres_in=sl['length_between_centres_in'], orientation=sl['orientation'], through='assumed'))
    rib = None
    if 'front' in sil:
        tri = find_rib_triangle(sil['front']['points'], X, Y, sil['front']['area_in2'])
        if tri:
            span = find_rib_span(S, views, by_role, dict(P=tri['P'], Q=tri['Q']), D, X, Y, objw)
            if span:
                rib = dict(triangle=tri, **span); pl = pool(by_role[span['role']]); st, i = _status(span['thickness'], pl, max(1 / 64, 1.2 / span['scale']))
                prov.append(dict(name='rib or gusset (a plate standing in the inside corner of the front profile)', value=f"profile {tri['polygon']}, thickness {span['thickness']}, across the depth from {span['z1']} to {span['z2']}", status='unconfirmed (inferred)', evidence=f"a diagonal silhouette edge between the two legs of an angle in the front view, and two lines {span['thickness']} in apart in the {span['role']} view {span['view']}"))
                prov.append(dict(name='rib thickness', value=span['thickness'], status=st, evidence=f'printed {i}' if i else 'measured from the two lines'))
            else: notes.append('the front profile has a diagonal edge across an inside corner (a gusset?) but no pair of lines in another view gives its thickness: it is kept as full-depth material')
    for role, sg in sil.items():
        v = by_role[role]; prov.append(dict(name=f'silhouette of the {role} view', value=f"{len(sg['points'])} vertices, fills {sg['box_fill']} of its box", status='unconfirmed (silhouette)', evidence='traced from the object linework; corners and chamfers are polygon approximations'))
    notes.extend(warnings)
    if len(sil) < 3: notes.append(f"only {len(sil)} silhouette(s): the visual hull is bounded by those views only, so what the missing view would trim is not removed")
    spec = dict(unit_guess=z.get('unit_guess'), X=X, Y=Y, D=D, rib=rib, silhouettes=sil, holes=holes, slots=slots, projection=projection, provenance=prov, not_shown=notes, views={v['role']: dict(id=v['id'], scale=v['scale_pt_per_in'], scale_source=v['scale_source'], zones=v['range']) for v in views})
    return spec


FC = r'''
import json, FreeCAD, Part
from FreeCAD import Vector as V
sp = json.load(open(%r)); IN = 25.4; X, Y, D = sp['X'] * IN, sp['Y'] * IN, sp['D'] * IN
def prism(role, pts):
    P = [(u * IN, v * IN) for u, v in pts]
    if role == 'front': poly = [V(u, v, 0) for u, v in P]; ext = V(0, 0, D)
    elif role in ('top', 'bottom'):
        poly = [V(u, 0, (v if role == 'top' else D - v)) for u, v in P]; ext = V(0, Y, 0)
    else: poly = [V(0, v, (u if role == 'right side' else D - u)) for u, v in P]; ext = V(X, 0, 0)
    face = Part.Face(Part.makePolygon(poly + [poly[0]])); return face.extrude(ext)
solid = None; rib = sp.get('rib')
for role, sg in sp['silhouettes'].items():
    try: pr = prism(role, sg['points'])
    except Exception: continue
    if role == 'front' and rib:                                       # the inside corner of the angle is not filled: only the rib is, over its own span
        t = rib['triangle']; tp = [V(u * IN, v * IN, -1) for u, v in t['polygon']]
        pr = pr.cut(Part.Face(Part.makePolygon(tp + [tp[0]])).extrude(V(0, 0, D + 2)))
    solid = pr if solid is None else solid.common(pr)
if rib and solid is not None:
    t = rib['triangle']; tp = [V(u * IN, v * IN, rib['z1'] * IN) for u, v in t['polygon']]
    solid = solid.fuse(Part.Face(Part.makePolygon(tp + [tp[0]])).extrude(V(0, 0, (rib['z2'] - rib['z1']) * IN)))
if solid is None: raise SystemExit('no silhouette could be extruded')
def cyl(role, c, d, length, side):
    """cylinder of diameter d (inches) along the viewing axis of `role` at c (part coordinates, inches); from the near face (side near) or the far face, `length` deep (None: through)"""
    r = d * IN / 2; cx, cy, cz = [t * IN for t in c]
    ax = {'front': (0, 0, 1), 'top': (0, -1, 0), 'bottom': (0, 1, 0), 'right side': (-1, 0, 0), 'left side': (1, 0, 0)}[role]       # from the near face inward
    nearpt = {'front': V(cx, cy, 0), 'top': V(cx, Y, cz), 'bottom': V(cx, 0, cz), 'right side': V(X, cy, cz), 'left side': V(0, cy, cz)}[role]
    total = {'front': D, 'top': Y, 'bottom': Y, 'right side': X, 'left side': X}[role]
    direction = V(*ax)
    if length is None: start = nearpt - direction * 1.0; ln = total + 2.0
    elif side == 'far': start = nearpt + direction * (total - length * IN); ln = length * IN + 1.0
    else: start = nearpt - direction * 1.0; ln = length * IN + 1.0
    return Part.makeCylinder(r, ln, start, direction)
for h in sp['holes']:
    for st in h['steps']:
        try: solid = solid.cut(cyl(h['role'], h['centre_in_part'], st['diameter_in'], st['length_in'], st['length_from']))
        except Exception: pass
FR = {'front': ((1, 0, 0), (0, 1, 0), (0, 0, 1)), 'top': ((1, 0, 0), (0, 0, 1), (0, -1, 0)), 'bottom': ((1, 0, 0), (0, 0, -1), (0, 1, 0)), 'right side': ((0, 0, 1), (0, 1, 0), (-1, 0, 0)), 'left side': ((0, 0, -1), (0, 1, 0), (1, 0, 0))}
for s in sp['slots']:
    role = s['role']; c = [t * IN for t in s['centre_in_part']]; w = s['width_in'] * IN; L = s['length_between_centres_in'] * IN; eu, ev, ea = [V(*t) for t in FR[role]]; horiz = s['orientation'] == 'x'
    d, x = (eu, ev) if horiz else (ev, eu); cen = V(*c)
    total = {'front': D, 'top': Y, 'bottom': Y, 'right side': X, 'left side': X}[role]
    nearpt = {'front': V(c[0], c[1], 0), 'top': V(c[0], Y, c[2]), 'bottom': V(c[0], 0, c[2]), 'right side': V(X, c[1], c[2]), 'left side': V(0, c[1], c[2])}[role]
    try:
        for sg in (1, -1): solid = solid.cut(Part.makeCylinder(w / 2, total + 2.0, nearpt + d * (sg * L / 2) - ea * 1.0, ea))
        corners = [nearpt - ea * 1.0 + d * (sa * L / 2) + x * (sb * w / 2) for sa, sb in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        solid = solid.cut(Part.Face(Part.makePolygon(corners + [corners[0]])).extrude(ea * (total + 2.0)))
    except Exception: pass
solid = solid.removeSplitter()
doc = FreeCAD.newDocument('r'); o = doc.addObject('Part::Feature', 'part'); o.Shape = solid; doc.recompute(); Part.export([o], %r)
bb = solid.BoundBox
print(json.dumps(dict(volume_in3=round(solid.Volume / IN ** 3, 4), solids=len(solid.Solids), faces=len(solid.Faces), valid=solid.isValid(), bbox_in=[round(bb.XLength / IN, 4), round(bb.YLength / IN, 4), round(bb.ZLength / IN, 4)])))
'''


def build_freecad(spec_path, step_out, fcpython):
    p = os.path.join(os.path.dirname(os.path.abspath(step_out)), '_rebuild.py'); open(p, 'w').write(FC % (spec_path, step_out))
    r = subprocess.run([fcpython, '-c', f"exec(open({p!r}).read())"], capture_output=True, text=True, timeout=600)
    line = next((l for l in (r.stdout + '\n' + r.stderr).splitlines() if l.startswith('{') and 'volume_in3' in l), None)
    if not line: return dict(error='FreeCAD build failed: ' + (r.stderr or r.stdout)[-400:])
    return json.loads(line)


def _inside_mesh(P, V, F, chunk=2000):
    """ray parity along +x for points P (n,3) against the triangles V[F]"""
    tri = V[F]; a = tri[:, 0]; e1 = tri[:, 1] - a; e2 = tri[:, 2] - a; d = np.array([1.0, 1e-4, 2e-4]); d /= np.linalg.norm(d)
    h = np.cross(d, e2); det = (e1 * h).sum(1); ok = np.abs(det) > 1e-12; out = np.zeros(len(P), bool)
    for i in range(0, len(P), chunk):
        Q = P[i:i + chunk]; cnt = np.zeros(len(Q), int)
        s = Q[:, None, :] - a[None]; u = (s * h[None]).sum(2) / np.where(ok, det, 1)[None]; q = np.cross(s, e1[None]); v = (d[None, None] * q).sum(2) / np.where(ok, det, 1)[None]; t = (e2[None] * q).sum(2) / np.where(ok, det, 1)[None]
        hit = ok[None] & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9); out[i:i + chunk] = hit.sum(1) % 2 == 1
    return out


def compare_cad(spec, step, cad_path, out_dir, fcpython, grid=36):
    from part_features import part_features
    from sldprt2glb import load_part
    f = part_features(cad_path); lo = np.array(f['bbox_mm'][0]) / IN; hi = np.array(f['bbox_mm'][1]) / IN; ext = hi - lo; mine = np.array([spec['X'], spec['Y'], spec['D']]); res = dict(extents=dict(rebuild=[round(float(x), 3) for x in mine], cad=[round(float(x), 3) for x in ext]))
    cad_holes = [c for c in f['cylinders'] if c['kind'] == 'hole' and c['axis_label']]; role_axis = {'front': 2, 'top': 1, 'bottom': 1, 'right side': 0, 'left side': 0}
    rb = []
    for h in spec['holes']:
        for st in h['steps']: rb.append((h['role'], np.array(h['centre_in_part']), st['diameter_in']))
    FRM = {'front': ((1, 0, 0), (0, 1, 0)), 'top': ((1, 0, 0), (0, 0, 1)), 'bottom': ((1, 0, 0), (0, 0, -1)), 'right side': ((0, 0, 1), (0, 1, 0)), 'left side': ((0, 0, -1), (0, 1, 0))}
    for s in spec['slots']:                                                          # a slot is two end cylinders in the CAD
        eu, ev = FRM[s['role']]; dv = np.array(eu if s['orientation'] == 'x' else ev, float)
        for sign in (1, -1): rb.append((s['role'], np.array(s['centre_in_part']) + sign * dv * s['length_between_centres_in'] / 2, s['width_in']))
    best = None
    for perm in itertools.permutations(range(3)):                                    # rebuilt axis i -> CAD axis perm[i]
        if np.any(np.abs(ext[list(perm)] - mine) > 0.15): continue
        for flips in itertools.product((0, 1), repeat=3):
            def tp(p):
                q = np.zeros(3)
                for i in range(3): q[perm[i]] = lo[perm[i]] + ((mine[i] - p[i]) if flips[i] else p[i])
                return q
            matched = 0; err = 0.0; used = set(); pairs = []
            for role, c, d in rb:
                ax = perm[role_axis[role]]; q = tp(c); best_j = None
                for j, ch in enumerate(cad_holes):
                    if j in used or ch['axis_label'][1] != 'XYZ'[ax] or abs(ch['diameter_mm'] / IN - d) > 0.03: continue
                    foot = np.array(ch['foot_mm']) / IN; perp = [k for k in range(3) if k != ax]; dist = float(np.hypot(*[q[k] - foot[k] for k in perp]))
                    if dist < 0.1 and (best_j is None or dist < best_j[0]): best_j = (dist, j)
                if best_j: used.add(best_j[1]); matched += 1; err += best_j[0]; pairs.append(best_j[0])
            key = (matched, -err)
            if best is None or key > best[0]: best = (key, perm, flips, pairs, used)
    if best is None: res['mapping'] = None; res['note'] = 'the extents do not match under any axis permutation'; return res
    (m, _), perm, flips, pairs, used = best
    res['mapping'] = dict(permutation=list(perm), flips=list(flips)); res['holes'] = dict(rebuild_cuts=len(rb), cad_holes=len(cad_holes), matched=m, mean_position_error_in=round(float(np.mean(pairs)), 4) if pairs else None, max_position_error_in=round(float(np.max(pairs)), 4) if pairs else None, cad_holes_not_in_rebuild=len(cad_holes) - len(used), rebuild_cuts_not_in_cad=len(rb) - m)
    P3 = np.array(load_part(cad_path, os.path.join(HERE, 'sch_13006_s_t.txt'))[:3][0]) * 1000 / IN; Fi = np.asarray(load_part(cad_path, os.path.join(HERE, 'sch_13006_s_t.txt'))[2], int)
    res['volume'] = volume_overlap(step, P3, Fi, lo, ext, mine, perm, flips, out_dir, fcpython, grid)
    json.dump(res, open(os.path.join(out_dir, 'comparison.json'), 'w'), indent=1); return res


def volume_overlap(step, P3, Fi, lo, ext, mine, perm, flips, out_dir, fcpython, grid=36):
    """intersection over union of the rebuilt STEP and a mesh (P3 in inches, Fi triangles) on a grid of points in the mesh's bounding box (lo, ext in inches); perm / flips map the rebuilt frame onto the mesh's"""
    hi = lo + ext
    xs = [np.linspace(lo[i] + ext[i] / (2 * grid), hi[i] - ext[i] / (2 * grid), grid) for i in range(3)]; G_ = np.array(np.meshgrid(*xs, indexing='ij')).reshape(3, -1).T; cad_in = _inside_mesh(G_, P3, Fi)
    R = np.zeros_like(G_)
    for i in range(3):
        c = G_[:, perm[i]] - lo[perm[i]]; R[:, i] = (mine[i] - c) if flips[i] else c
    pts = os.path.join(out_dir, '_samples.json'); json.dump((R * IN).tolist(), open(pts, 'w'))
    script = f"import json, Part\nsh = Part.read({step!r}); P = json.load(open({pts!r})); out = [1 if sh.isInside(__import__('FreeCAD').Vector(*p), 1e-6, True) else 0 for p in P]; json.dump(out, open({pts + '.out'!r}, 'w'))\n"
    sp = os.path.join(out_dir, '_inside.py'); open(sp, 'w').write(script); subprocess.run([fcpython, '-c', f"exec(open({sp!r}).read())"], capture_output=True, text=True, timeout=1800)
    mine_in = np.array(json.load(open(pts + '.out')), bool); inter = int((mine_in & cad_in).sum()); union = int((mine_in | cad_in).sum())
    return dict(samples=len(G_), cad_inside=int(cad_in.sum()), rebuild_inside=int(mine_in.sum()), intersection=inter, union=union, iou=round(inter / union, 4) if union else None, only_in_rebuild=int((mine_in & ~cad_in).sum()), only_in_cad=int((cad_in & ~mine_in).sum()),
                note='the other side is a ray-parity test on a mesh; a mesh that is not closed gives wrong parity')


def compare_mesh(spec, step, V_mm, F, out_dir, fcpython, perm=(0, 1, 2), flips=(0, 0, 1), grid=28):
    """overlap with a mesh (mm) whose frame is related to the rebuilt one by perm / flips (default: the frame the GLB drawings are made in: front view from +z, so the depth runs from +z)"""
    V = V_mm / IN; lo = V.min(0); ext = V.max(0) - lo; mine = np.array([spec['X'], spec['Y'], spec['D']])
    res = dict(extents=dict(rebuild=[round(float(x), 3) for x in mine], mesh=[round(float(x), 3) for x in ext]), mapping=dict(permutation=list(perm), flips=list(flips)))
    res['volume'] = volume_overlap(step, V, F, lo, ext, mine, perm, flips, out_dir, fcpython, grid); return res


def report(spec, build, comp=None, name=''):
    L = [f'# Part rebuilt from the building blocks of its drawing {name}'.strip(), '', 'Layout read as ' + spec['projection'] + '-angle projection. Frame: X across the front view, Y up it, Z the depth from the front face. `stated` = a printed value equals it; `unconfirmed` = measured, assumed or inferred: to be confirmed with the design engineer.', '']
    if spec.get('unit_guess'): L.append(f"**Unit not printed on the sheet:** read as {spec['unit_guess']['chosen']} because the drawn linework supports it better ({spec['unit_guess']['matches_if_inch']} printed values match line spacings read as inches, {spec['unit_guess']['matches_if_mm']} as millimetres). Confirm with the design engineer."); L.append('')
    L.append('Views used: ' + '; '.join(f"{r} = {v['id']} in {v['zones']} at {v['scale']} pt/in ({v['scale_source']})" for r, v in spec['views'].items()) + '.'); L.append('')
    if build and 'error' not in build: L.append(f"Solid: {build['volume_in3']} in3, {build['faces']} faces, bounding box {build['bbox_in']} in, valid {build['valid']}."); L.append('')
    L += ['| quantity | value | status | evidence |', '|---|---|---|---|']
    for p in spec['provenance']: L.append(f"| {p['name']} | {p['value']} | {p['status']} | {p['evidence']} |")
    for h in spec['holes']:
        for k, st in enumerate(h['steps'], 1):
            L.append(f"| {h['view']} {h['type']} at {h['centre_in_part']}: diameter {k} | {st['diameter_in']} | {st['diameter_status']} | {st['diameter_evidence']} |")
            L.append(f"| {h['view']} {h['type']} at {h['centre_in_part']}: length {k} | {st['length_in'] if st['length_in'] else 'through'} ({st['length_from'] or '-'}) | {st['length_status']} | {st['length_evidence']} |")
        for p in h['position']: L.append(f"| {h['view']} hole at {h['centre_in_part']}: {p['what']} | {p['value']} | {p['status']} | {p['evidence']} |")
    for s in spec['slots']: L.append(f"| {s['view']} slot at {s['centre_in_part']}: width {s['width_in']}, centres {s['length_between_centres_in']} apart along {s['orientation']} | | unconfirmed (measured; through {s['through']}) | |")
    L += ['', '## Not shown by the views / inferred', ''] + [f'- {n}' for n in spec['not_shown']] + ['- the solid is the visual hull of the silhouettes: a pocket, rib, gusset or cavity inside the profile is not recovered; holes seen in one view are cut through unless a neighbouring view shows their depth', '- threads, chamfers on hole edges, edge breaks, weld preparations, material and finish are not in the geometry']
    if comp:
        L += ['', '## Against the CAD part', '']
        if comp.get('mapping') is None: L.append(comp.get('note', 'not compared'))
        else:
            h = comp['holes']; v = comp['volume']; L.append(f"Extents rebuild {comp['extents']['rebuild']} in, CAD {comp['extents']['cad']} in. Frame mapping found by search: {comp['mapping']}. Holes: {h['matched']} of {h['rebuild_cuts']} rebuilt cuts match a CAD hole (mean position error {h['mean_position_error_in']} in, max {h['max_position_error_in']} in); {h['cad_holes_not_in_rebuild']} CAD holes are not in the rebuild.")
            L.append(f"Volume overlap on {v['samples']} samples: IoU {v['iou']}; only in the rebuild {v['only_in_rebuild']}, only in the CAD {v['only_in_cad']}. {v['note']}.")
    return '\n'.join(L) + '\n'


def rebuild(pdf, page, out_dir, fcpython, cad=None, projection='third', unit=None):
    os.makedirs(out_dir, exist_ok=True); spec = build_spec(pdf, page, projection, unit)
    if 'error' in spec: return spec
    sp = os.path.join(out_dir, 'spec.json'); json.dump(spec, open(sp, 'w'), indent=1, default=float); json.dump(spec['provenance'], open(os.path.join(out_dir, 'provenance.json'), 'w'), indent=1, default=float)
    step = os.path.join(out_dir, 'part_from_drawing.step'); build = build_freecad(sp, step, fcpython); comp = None
    if 'error' not in build and build['volume_in3'] < 1e-6: build = dict(error='the views gave an empty solid (their silhouettes do not intersect): not built')
    if cad and 'error' not in build: comp = compare_cad(spec, step, cad, out_dir, fcpython)
    open(os.path.join(out_dir, 'report.md'), 'w').write(report(spec, build, comp, os.path.basename(pdf) + f' page {page}'))
    return dict(spec=spec, build=build, comparison=comp)


def main():
    a = sys.argv[1:]
    if len(a) < 3 or '--fcpython' not in a: raise SystemExit(__doc__)
    opt = lambda n, d=None: a[a.index(n) + 1] if n in a else d
    r = rebuild(a[0], int(a[1]), a[2], opt('--fcpython'), opt('--cad'), opt('--projection', 'third'), opt('--unit'))
    if 'error' in r: print('not rebuilt:', r['error']); return
    print(json.dumps(r['build'])); print(open(os.path.join(a[2], 'report.md')).read()[:200000] if '--print' in a else '')
    if r['comparison']: print(json.dumps({k: v for k, v in r['comparison'].items()}, default=float))


if __name__ == '__main__':
    main()
