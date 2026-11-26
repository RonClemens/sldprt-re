#!/usr/bin/env python3
"""The exact faces of a SolidWorks part: planes, cylinders (holes, bosses, fillets), cones and the rest, with their parameters, read from the Parasolid body instead of from the tessellated mesh.

  python3 src/part_features.py PART.SLDPRT [--json OUT.json]          features(brep) -> dict          part_features(path) -> dict

The mesh (sldprt2glb) loses what a drawing is about: a cylinder becomes a few hundred triangles, a face that fails to tessellate disappears (the block of the Hydro package lost the cone at the bottom of a tapped hole). Here
every FACE node of the body is kept with its surface's own numbers, in millimetres and part coordinates:

  planes     outward normal, offset d (normal . point), the face ids; coplanar faces are merged. `axis` is X/Y/Z (+ or -) for an axis-aligned plane, else null (a chamfer or a slanted face).
  cylinders  axis direction, the point where the axis crosses the plane through the part's origin perpendicular to it (so two holes on one axis share it), radius, `hole` (the material is outside the cylinder) or
             `boss` (inside: an outside diameter, a fillet), and the axial range from the face's vertices.
  cones      apex-side data: axis, a point and radius on the axis, half angle, hole-or-boss, axial range (drill points, chamfers, countersinks).
  other      torus, sphere, B-surfaces, swept/spun: listed by kind and face id only.

Whether a face is a hole or a boss follows from the face's and the surface's senses (the outward normal of the solid against the radial direction). The axial range comes from the vertices of the face's loops (a circle edge with a single
vertex contributes the circle's own extent), so a face whose tessellation fails still has it. Limits: partial cylinders (fillets, slots) are not told apart from full ones except by their radius and range; edges that are not circles
or lines are ignored for extents; units are taken to be metres in the file (as the converter assumes).
"""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
SCHEMA = os.path.join(HERE, 'sch_13006_s_t.txt')
MM = 1000.0


def read_brep(path, schema=SCHEMA):
    from sldcont import read_streams
    import sldprt2glb as S
    from brep import Brep
    st = read_streams(path); best = None
    for srcs in (S.geometry_transmits, S.fallback_transmits):
        if srcs is S.fallback_transmits and best is not None: break
        for name, k, raw in srcs(st):
            if b'TRANSMIT FILE (deltas)' in raw[:80]: continue
            try: br = Brep(raw, schema)
            except Exception: continue
            nf = sum(1 for n in br.N.values() if n['name'] == 'FACE')
            if nf and (best is None or nf > best[0]): best = (nf, br)
    if best is None: raise ValueError('no decodable Parasolid body with faces')
    return best[1]


def _sgn(s): return 1.0 if s == '+' else -1.0


def face_points(N, fid):
    """vertices of a face's loops (metres); a full-circle edge (one vertex) adds points around the circle"""
    pts = []; f = N[fid]; lid = f.get('loop'); seen_loops = set()
    while lid in N and lid not in seen_loops and N[lid]['name'] == 'LOOP':
        seen_loops.add(lid); h0 = N[lid]['halfedge']; h = h0; guard = 0
        while h in N and guard < 10000:
            he = N[h]; v = N.get(he.get('vertex')); e = N.get(he.get('edge'))
            if v and v.get('point') in N: pts.append(np.array(N[v['point']]['pvec'], float))
            if e and e.get('curve') in N and N[e['curve']]['name'] == 'CIRCLE':
                c = N[e['curve']]; other = N.get(he.get('other')); v2 = N.get(other['vertex']) if other and other.get('vertex') in N else None
                if v is None or (v2 is not None and v['id'] == v2['id']):                  # a closed circle (a ring edge has no vertex, or one): its extent is the circle's
                    cc = np.array(c['centre'], float); nz = np.array(c['normal'], float); nz /= np.linalg.norm(nz); x = np.array(c['x_axis'], float); x /= np.linalg.norm(x); y = np.cross(nz, x)
                    pts += [cc + c['radius'] * (np.cos(t) * x + np.sin(t) * y) for t in np.linspace(0, 2 * np.pi, 16, endpoint=False)]
            h = he.get('forward'); guard += 1
            if h == h0: break
        lid = N[lid].get('next')
        if lid is not None and lid in N and N[lid].get('face') != fid: break
    return np.array(pts) if pts else np.zeros((0, 3))


def _axis_label(n, tol=1e-6):
    for i, a in enumerate('XYZ'):
        if abs(abs(n[i]) - 1) < tol: return ('+' if n[i] > 0 else '-') + a
    return None


def features(br):
    N = br.N; planes = {}; cyls = {}; cones = []; other = []; allpts = []
    for fid, f in sorted(N.items()):
        if f['name'] != 'FACE': continue
        sn = N.get(f.get('surface'))
        if sn is None: other.append(dict(face=fid, kind='missing surface')); continue
        kind = sn['name']; flip = _sgn(sn.get('sense', '+')) * _sgn(f.get('sense', '+')); P = face_points(N, fid); allpts += list(P)
        if kind == 'PLANE':
            n = np.array(sn['normal'], float); n /= np.linalg.norm(n); n = n * flip; d = float(n @ np.array(sn['pvec'], float)) * MM
            key = (tuple(np.round(n, 5)), round(d, 3)); r = planes.setdefault(key, dict(normal=[round(float(x), 6) for x in n], axis=_axis_label(n), d_mm=round(d, 4), faces=[], lo=None, hi=None))
            r['faces'].append(fid)
            if len(P): r['lo'] = P.min(0) if r['lo'] is None else np.minimum(r['lo'], P.min(0)); r['hi'] = P.max(0) if r['hi'] is None else np.maximum(r['hi'], P.max(0))
        elif kind in ('CYLINDER', 'CONE'):
            a = np.array(sn['axis'], float); a /= np.linalg.norm(a); p = np.array(sn['pvec'], float); r0 = float(sn['radius']) * MM
            hole = flip < 0            # the solid's outward normal points towards the axis: the material is outside the cylinder
            lo, hi = (None, None)
            if len(P): t = (P - p) @ a * MM; lo, hi = float(t.min()), float(t.max())
            if kind == 'CYLINDER':
                if a[np.argmax(np.abs(a))] < 0: a = -a; lo, hi = (None, None) if lo is None else (-hi, -lo)
                foot = p - (p @ a) * a                                                           # where the axis crosses the plane through the origin perpendicular to it
                key = (tuple(np.round(a, 5)), tuple(np.round(foot * MM, 3)), round(r0, 3), hole)
                c = cyls.setdefault(key, dict(axis=[round(float(x), 6) for x in a], axis_label=_axis_label(a), foot_mm=[round(float(x), 4) for x in foot * MM], radius_mm=round(r0, 4), diameter_mm=round(2 * r0, 4), kind='hole' if hole else 'boss', faces=[], lo=None, hi=None))
                c['faces'].append(fid)
                if len(P):
                    t = (P @ a) * MM; c['lo'] = float(t.min()) if c['lo'] is None else min(c['lo'], float(t.min())); c['hi'] = float(t.max()) if c['hi'] is None else max(c['hi'], float(t.max()))
            else:
                cones.append(dict(face=fid, axis=[round(float(x), 6) for x in a], point_mm=[round(float(x), 4) for x in p * MM], radius_at_point_mm=round(r0, 4), half_angle_deg=round(float(np.degrees(np.arctan2(sn['sin_half_angle'], sn['cos_half_angle']))), 3),
                                  kind='hole' if hole else 'boss', axial_range_from_point_mm=None if lo is None else [round(lo, 4), round(hi, 4)]))
        else: other.append(dict(face=fid, kind=kind.lower()))
    for r in planes.values():
        r['lo_mm'] = None if r['lo'] is None else [round(float(x) * MM, 3) for x in r.pop('lo')]; r['hi_mm'] = None if r['hi'] is None else [round(float(x) * MM, 3) for x in r.pop('hi')]
    for c in cyls.values():
        c['axial_range_mm'] = None if c['lo'] is None else [round(c.pop('lo'), 4), round(c.pop('hi'), 4)]; c.pop('lo', None); c.pop('hi', None)
    A = np.array(allpts) * MM if allpts else np.zeros((0, 3))
    return dict(units='mm', faces=sum(1 for n in N.values() if n['name'] == 'FACE'), bbox_mm=None if not len(A) else [[round(float(x), 3) for x in A.min(0)], [round(float(x), 3) for x in A.max(0)]],
                planes=sorted(planes.values(), key=lambda r: (str(r['axis']), r['d_mm'])), cylinders=sorted(cyls.values(), key=lambda c: (-c['radius_mm'], c['axis_label'] or '')), cones=cones, other=other)


def part_features(path, schema=SCHEMA): return features(read_brep(path, schema))


def main():
    a = sys.argv[1:]
    if not a or a[0].startswith('-'): raise SystemExit(__doc__)
    r = part_features(a[0])
    if '--json' in a: json.dump(r, open(a[a.index('--json') + 1], 'w'), indent=1)
    else:
        print(f"{r['faces']} faces; bbox {r['bbox_mm']}"); print(f"{len(r['planes'])} planes:"); [print('  ', p['axis'] or 'oblique', p['normal'], 'd', p['d_mm'], 'faces', p['faces']) for p in r['planes']]
        print(f"{len(r['cylinders'])} cylinders:"); [print('  ', c['kind'], 'dia', c['diameter_mm'], 'axis', c['axis_label'] or c['axis'], 'foot', c['foot_mm'], 'range', c['axial_range_mm'], 'faces', c['faces']) for c in r['cylinders']]
        print(f"{len(r['cones'])} cones:"); [print('  ', c) for c in r['cones']]; print('other', r['other'])


if __name__ == '__main__':
    main()
