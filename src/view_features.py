#!/usr/bin/env python3
"""What each view of a drawing sheet shows, in the terms a 3D model is built from: which view it is (front, top, side ...), which part axes run across it, the scale, which dimension measures which axis, and the features:
holes (through, blind with depth, counterbored, countersunk, tapped, slotted) with their diameters, fillet radii, countersink diameters, chamfer callouts.

  analyse_views(sheet, segments, geo, circles, views, projection='third') -> adds to each view dict: role, axes, scale, dimension_axes, features, fillets, slots, chamfers      (called by sheet_zones.read_zones)

View roles from layout: the orthographic views are placed on a grid; the view with the most aligned neighbours is the front view (ties: the largest); a view above it is the top view and one below it the bottom view (third-angle
projection, the ASME default; with projection='first' they swap, and so do the side views); a view to its right is the right side, to its left the left side. A view aligned with no other, or with a scale label of its own, is a detail or
section; a view with slanted linework is pictorial. Part axes: front X across, Y up; top and bottom X across, Z up the page; sides Z across, Y up. These are conventions of the layout, not read from the sheet: the sheet's own projection
symbol is a drawing, not text, so the result says which convention it assumed.

Scale: the `SCALE a : b` label nearest the view when the printed dimensions agree with it, else inferred (drawing_part.infer_scale). Dimension axes: a printed value is assigned to the horizontal or vertical axis of its view when it
equals a spacing of lines or circle coordinates in that direction at the view's scale (both: `either`; neither: `unmatched`).

Features: circles (concentric ones stacked: outer diameter first) with their position from the view's edges; the type comes from the view aligned with this one (the profile of the hole: lines at the circle's radii spanning the whole
height are a through hole, shorter ones a blind hole with that depth, two radii with different lengths a counterbore, slanted lines between the radii a countersink) and from callout text near the hole (THRU, UNC/UNF/M threads, CSK, C'BORE,
DEEP). A hole whose profile is in no aligned view stays `unknown depth`. Fillets are the arcs that are not full circles (quarter arcs at corners) with their radius, plus radius dimensions. Slots are stadium outlines. Limits: the arcs that
make up a rounded corner drawn as a chain of short segments joined to the outline are not recognised as fillets (the setback method of drawing_part.measure finds them on simple parts); hole threads are only callouts; a diameter whose callout
text was lost (stacked fractions after the diameter symbol in fonts that are not embedded) is measured, not read.
"""
import re
import numpy as np

AXES_OF = {'front': ('X', 'Y'), 'top': ('X', 'Z'), 'bottom': ('X', 'Z'), 'right side': ('Z', 'Y'), 'left side': ('Z', 'Y')}
THREAD = re.compile(r'(\d+/\d+|#\d+|\d+)\s*-\s*\d+\s*(UNC|UNF|UN|NPT|NPTF)\b|\bM\d+(?:\.\d+)?\s*[xX]\s*\d|\bNPT\b', re.I)
THRU = re.compile(r'THRU(?:\s+ALL)?', re.I)
CSK = re.compile(r'CSK|C.SINK|COUNTERSINK|⌵', re.I)
CBORE = re.compile(r'C.?BORE|COUNTERBORE|⌴', re.I)
DEEP = re.compile(r'DEEP|DEPTH|⌶', re.I)
CHAMFER = re.compile(r'CHAMFER|X\s*45|×\s*45', re.I)


def _overlap(a0, a1, b0, b1): return max(0.0, min(a1, b1) - max(a0, b0))


def _roles(views, projection):
    ortho = [v for v in views if v['type'] == 'orthographic']
    if not ortho: return
    area = lambda v: (v['box'][2] - v['box'][0]) * (v['box'][3] - v['box'][1]); big = max(area(v) for v in ortho)
    main = [v for v in ortho if area(v) >= 0.06 * big and v['segments'] >= 6]
    nb = {v['id']: [] for v in main}
    for a in main:
        for b in main:
            if a is b: continue
            wa, wb = a['box'][2] - a['box'][0], b['box'][2] - b['box'][0]; ha, hb = a['box'][3] - a['box'][1], b['box'][3] - b['box'][1]
            if _overlap(a['box'][0], a['box'][2], b['box'][0], b['box'][2]) >= 0.7 * min(wa, wb) and (b['box'][1] >= a['box'][3] - 1 or b['box'][3] <= a['box'][1] + 1): nb[a['id']].append(('v', b))
            elif _overlap(a['box'][1], a['box'][3], b['box'][1], b['box'][3]) >= 0.7 * min(ha, hb) and (b['box'][0] >= a['box'][2] - 1 or b['box'][2] <= a['box'][0] + 1): nb[a['id']].append(('h', b))
    for v in views: v['role'] = 'pictorial (isometric)' if v['type'] == 'pictorial' else 'detail or section (aligned with no view)'; v['aligned_with'] = []
    if not main: return
    hub = max(main, key=lambda v: (len(nb[v['id']]), -v['circles'], area(v))); hub['role'] = 'front'      # most aligned neighbours; then the view with fewest circles (hole axes square to the view belong to top, bottom and side views)
    third = projection != 'first'
    for kind, b in nb[hub['id']]:
        above = b['box'][1] >= hub['box'][3] - 1; right = b['box'][0] >= hub['box'][2] - 1
        if kind == 'v': b['role'] = ('top' if above else 'bottom') if third else ('bottom' if above else 'top')
        else: b['role'] = ('right side' if right else 'left side') if third else ('left side' if right else 'right side')
        b['aligned_with'] = [hub['id']]; hub['aligned_with'].append(b['id'])
    for v in main:                                                                    # views aligned only with a non-front view keep a note of it
        if v['role'].startswith('detail'):
            al = [b['id'] for _, b in nb[v['id']]]
            if al: v['role'] = 'auxiliary (aligned with ' + ', '.join(al) + ')'; v['aligned_with'] = al
    for v in views:
        v['axes'] = dict(zip(('horizontal', 'vertical'), AXES_OF.get(v['role'], ('?', '?'))))


def _coords(Sv, box, circles):
    x0, y0, x1, y1 = box; xs = []; ys = []
    for sx0, sy0, sx1, sy1, w in Sv:
        if not (x0 - 1 <= min(sx0, sx1) and max(sx0, sx1) <= x1 + 1 and y0 - 1 <= min(sy0, sy1) and max(sy0, sy1) <= y1 + 1): continue
        if abs(sx0 - sx1) < 0.05: xs.append(round(float(sx0), 1))
        elif abs(sy0 - sy1) < 0.05: ys.append(round(float(sy0), 1))
    for c in circles:
        if x0 - 1 <= c['cx'] <= x1 + 1 and y0 - 1 <= c['cy'] <= y1 + 1: xs += [c['cx'] - c['r'], c['cx'], c['cx'] + c['r']]; ys += [c['cy'] - c['r'], c['cy'], c['cy'] + c['r']]
    return sorted(set(xs)), sorted(set(ys))


def _scale(sheet, v, Sv, C):
    import drawing_part as DP
    dims = [d for d in sheet.dimensions if d.id in v['dims'] and d.value]; vals = [d.value if d.unit == 'inch' else d.value / 25.4 for d in dims]
    xs, ys = _coords(Sv, v['box'], C); inview = [c for c in C if v['box'][0] <= c['cx'] <= v['box'][2] and v['box'][1] <= c['cy'] <= v['box'][3]]
    b = v['box']; ext = [b[2] - b[0], b[3] - b[1]]
    free = DP.infer_scale(vals, xs, ys, ext, [2 * c['r'] for c in inview]) if len(vals) >= 3 else (None, 0, 0, True)
    if v.get('scale'):
        a, _, bb = v['scale'].partition(':'); f = float(a) / float(bb) if float(bb) else None
        if f:
            lab = DP.infer_scale(vals, xs, ys, ext, [2 * c['r'] for c in inview], factors=(f,)) if len(vals) >= 3 else (72.0 * f, 0, 0, False)
            if lab[0] is not None and (free[0] is None or lab[1] >= 0.7 * free[1]): return lab[0], 'label ' + v['scale'], lab[1]
            if free[0] is None: return 72.0 * f, 'label ' + v['scale'] + ' (not cross-checked: too few printed values)', 0
    return (free[0], 'inferred', free[1]) if free[0] else (None, 'not determined', free[1])


def _dim_axes(sheet, v, Sv, C, s):
    out = []
    if not s: return [dict(id=i, direction='?', note='no scale') for i in v['dims']]
    xs, ys = _coords(Sv, v['box'], C); px = [abs(b - a) for i, a in enumerate(xs) for b in xs[i + 1:]]; py = [abs(b - a) for i, a in enumerate(ys) for b in ys[i + 1:]]
    for d in sheet.dimensions:
        if d.id not in v['dims'] or not d.value: continue
        val = d.value if d.unit == 'inch' else d.value / 25.4; t = val * s; tol = max(0.7, 0.012 * t)
        hx = any(abs(q - t) <= tol for q in px); hy = any(abs(q - t) <= tol for q in py)
        direction = 'horizontal' if hx and not hy else 'vertical' if hy and not hx else 'either' if hx and hy else 'unmatched'
        if len(d.tips) >= 2:                                                               # a dimension line with two arrow tips says which way it runs
            dx = abs(d.tips[0][0] - d.tips[1][0]); dy = abs(d.tips[0][1] - d.tips[1][1])
            if max(dx, dy) > 2: direction = 'horizontal' if dx > dy else 'vertical'
        out.append(dict(id=d.id, value_in=round(val, 4), count=d.count, kind=d.kind, direction=direction))
    return out


def _profile(Sv, neighbour, cx, cy, r, vertical_neighbour, s_n, r_in=None):
    """lines of a hole's wall in the neighbouring view: -> (length_in, spans_all, diagonal_count)"""
    nb = neighbour['box']; lines = []; diag = 0
    for sx0, sy0, sx1, sy1, w in Sv:
        if not (nb[0] - 1 <= min(sx0, sx1) and max(sx0, sx1) <= nb[2] + 1 and nb[1] - 1 <= min(sy0, sy1) and max(sy0, sy1) <= nb[3] + 1): continue
        if vertical_neighbour:
            if abs(sx0 - sx1) < 0.1 and (abs(sx0 - (cx - r)) < 0.8 or abs(sx0 - (cx + r)) < 0.8): lines.append((min(sy0, sy1), max(sy0, sy1)))
            elif r_in and abs(sx0 - sx1) > 0.5 and abs(sy0 - sy1) > 0.5 and np.hypot(sx1 - sx0, sy1 - sy0) >= max(3.0, 0.4 * (r - r_in)) and all(r_in - 1.0 <= abs(x - cx) <= r + 1.0 for x in (sx0, sx1)): diag += 1        # a slanted line from the inner radius out to the outer one: a countersink wall
        else:
            if abs(sy0 - sy1) < 0.1 and (abs(sy0 - (cy - r)) < 0.8 or abs(sy0 - (cy + r)) < 0.8): lines.append((min(sx0, sx1), max(sx0, sx1)))
            elif r_in and abs(sx0 - sx1) > 0.5 and abs(sy0 - sy1) > 0.5 and np.hypot(sx1 - sx0, sy1 - sy0) >= max(3.0, 0.4 * (r - r_in)) and all(r_in - 1.0 <= abs(y - cy) <= r + 1.0 for y in (sy0, sy1)): diag += 1
    if not lines: return None, False, diag
    lines.sort(); merged = [list(lines[0])]
    for a, b in lines[1:]:
        if a <= merged[-1][1] + 4: merged[-1][1] = max(merged[-1][1], b)
        else: merged.append([a, b])
    best = max(merged, key=lambda t: t[1] - t[0]); length = best[1] - best[0]
    def wall(pos):                                                                   # a long object line across the hole's direction at this coordinate: an outer face of the part
        for sx0, sy0, sx1, sy1, w in Sv:
            if not (nb[0] - 1 <= min(sx0, sx1) and max(sx0, sx1) <= nb[2] + 1 and nb[1] - 1 <= min(sy0, sy1) and max(sy0, sy1) <= nb[3] + 1): continue
            if vertical_neighbour and abs(sy0 - sy1) < 0.1 and abs(sy0 - pos) < 1.0 and abs(sx1 - sx0) >= max(40.0, 4 * r): return True
            if not vertical_neighbour and abs(sx0 - sx1) < 0.1 and abs(sx0 - pos) < 1.0 and abs(sy1 - sy0) >= max(40.0, 4 * r): return True
        return False
    return length / s_n, bool(wall(best[0]) and wall(best[1])), diag


def analyse_views(sheet, Sv, geo, C, views, SL, projection='third'):
    import drawing_part as DP
    _roles(views, projection); notes = [n for n in sheet.notes if n.kind == 'note' and n.pos and len(n.text.strip()) > 2]
    by_id = {v['id']: v for v in views}
    for v in views:
        v.setdefault('role', 'pictorial (isometric)' if v['type'] == 'pictorial' else 'detail or section'); v.setdefault('axes', dict(horizontal='?', vertical='?'))
        s, src, n = _scale(sheet, v, Sv, C) if v['type'] == 'orthographic' else (None, 'n/a', 0); v['scale_pt_per_in'] = None if s is None else round(float(s), 3); v['scale_source'] = src
    for v in views:                                                                   # orthographic views aligned with each other are drawn at one scale unless one carries its own label
        if v['scale_pt_per_in'] is None and v['type'] == 'orthographic' and not v.get('scale'):
            for al in v.get('aligned_with', []):
                if by_id[al]['scale_pt_per_in']: v['scale_pt_per_in'] = by_id[al]['scale_pt_per_in']; v['scale_source'] = f'shared with {al}'; break
    for v in views:
        if v['scale_pt_per_in'] is None and v['type'] == 'orthographic' and v['segments'] >= 6: v['scale_pt_per_in'] = 72.0; v['scale_source'] = 'assumed full size (nothing to infer it from): inches below are drawn inches'
    for v in views:
        s = v['scale_pt_per_in']
        v['dimension_axes'] = _dim_axes(sheet, v, Sv, C, s) if v['type'] == 'orthographic' else []
        v['features'] = []; v['fillets'] = []; v['slots'] = []; v['chamfers'] = []
        if not s: continue
        b = v['box']; inview = [c for c in C if b[0] <= c['cx'] <= b[2] and b[1] <= c['cy'] <= b[3]]
        stacks = []
        for c in sorted(inview, key=lambda c: -c['r']):
            for st in stacks:
                if np.hypot(c['cx'] - st['cx'], c['cy'] - st['cy']) < 1.0: st['rs'].append(c['r']); break
            else: stacks.append(dict(cx=c['cx'], cy=c['cy'], rs=[c['r']]))
        for st in stacks:
            rs = sorted(st['rs'], reverse=True); ro = rs[0]; near = [(float(np.hypot(n.pos[0] - st['cx'], n.pos[1] - st['cy'])), n.text) for n in notes if np.hypot(n.pos[0] - st['cx'], n.pos[1] - st['cy']) < 170 and (THRU.search(n.text) or THREAD.search(n.text) or CSK.search(n.text) or CBORE.search(n.text) or DEEP.search(n.text) or 'Ø' in n.text or 'DIA' in n.text.upper())]
            callout = min(near)[1].strip() if near else ''
            feat = dict(kind='hole', centre_pt=[round(st['cx'], 1), round(st['cy'], 1)], diameters_in=[round(2 * r / s, 4) for r in rs], from_left_in=round((st['cx'] - b[0]) / s, 4), from_right_in=round((b[2] - st['cx']) / s, 4), from_bottom_in=round((st['cy'] - b[1]) / s, 4), from_top_in=round((b[3] - st['cy']) / s, 4), callout=callout, type='hole')
            # profile from an aligned view
            prof = None
            for al in v.get('aligned_with', []):
                nbv = by_id[al]; sn = nbv.get('scale_pt_per_in')
                if not sn: continue
                vertical = nbv['box'][1] >= b[3] - 1 or nbv['box'][3] <= b[1] + 1
                outer = _profile(Sv, nbv, st['cx'], st['cy'], ro, vertical, sn, rs[-1] if len(rs) > 1 else None); inner = _profile(Sv, nbv, st['cx'], st['cy'], rs[-1], vertical, sn) if len(rs) > 1 else None
                if outer[0] is not None or (inner and inner[0] is not None): prof = (al, outer, inner); break
            if len(rs) > 1:
                if CSK.search(callout) or (prof and prof[1][2] > 0): feat['type'] = 'countersunk hole'; feat['countersink_diameter_in'] = feat['diameters_in'][0]; feat['hole_diameter_in'] = feat['diameters_in'][-1]
                elif CBORE.search(callout) or (prof and prof[1][0] is not None and prof[2] and prof[2][0] is not None): feat['type'] = 'counterbored hole'; feat['counterbore_diameter_in'] = feat['diameters_in'][0]; feat['hole_diameter_in'] = feat['diameters_in'][-1]
                else: feat['type'] = 'stepped hole (counterbore or countersink: not told apart)'
                if prof and prof[1][0] is not None: feat['outer_step_length_in'] = round(prof[1][0], 4)
                if prof and prof[2] and prof[2][0] is not None: feat['inner_step_length_in'] = round(prof[2][0], 4); feat['inner_through'] = prof[2][1]
            else:
                if THREAD.search(callout): feat['type'] = 'tapped hole'; feat['thread'] = THREAD.search(callout).group(0)
                if prof and prof[1][0] is not None:
                    feat['depth_in'] = round(prof[1][0], 4); feat['through'] = bool(prof[1][1]); feat['type'] = ('through ' if prof[1][1] else 'blind ') + feat['type'] if feat['type'] == 'hole' else feat['type']
                elif THRU.search(callout): feat['through'] = True; feat['type'] = 'through ' + feat['type'] if feat['type'] == 'hole' else feat['type']
                else: feat['type'] = 'hole (unknown depth)' if feat['type'] == 'hole' else feat['type']
            feat['profile_from'] = prof[0] if prof else None; v['features'].append(feat)
        for sl in SL:
            if b[0] <= sl['cx'] <= b[2] and b[1] <= sl['cy'] <= b[3]: v['slots'].append(dict(width_in=round(sl['width'] / s, 4), length_between_centres_in=round(sl['length_between_centres'] / s, 4), orientation=sl['orientation'], centre_pt=[round(sl['cx'], 1), round(sl['cy'], 1)]))
        arcs = [a for k, a in enumerate(geo.arcs) if b[0] - 1 <= a[0] <= b[2] + 1 and b[1] - 1 <= a[1] <= b[3] + 1 and (a[3][2] - a[3][0]) < 1.5 * a[2] and a[2] / s >= 0.06 and (geo.arc_w[k] if k < len(geo.arc_w) else 1.0) >= 0.6]       # object-width arcs of at least 1/16 in: thinner or smaller ones are glyph shapes, centre marks, arrowheads
        rr = {}
        for a in arcs: rr.setdefault(round(a[2] / s, 3), []).append([round(a[0], 1), round(a[1], 1)])
        v['fillets'] = [dict(radius_in=r, count=len(p), at_pt=p[:4]) for r, p in sorted(rr.items())]
        # rounded corners of the outline drawn as short segments: the outline lines stop short of the box corner by the same setback on both sides
        Hh, Vv = DP.lines_by_axis([r for r in Sv if b[0] - 1 <= min(r[0], r[2]) and max(r[0], r[2]) <= b[2] + 1 and b[1] - 1 <= min(r[1], r[3]) and max(r[1], r[3]) <= b[3] + 1], width=0.6)
        for nm, (kx, ky) in dict(bl=(b[0], b[1]), br=(b[2], b[1]), tl=(b[0], b[3]), tr=(b[2], b[3])).items():
            vv = next((iv for k, iv in Vv.items() if abs(k - kx) < 0.4), []); hh = next((iv for k, iv in Hh.items() if abs(k - ky) < 0.4), [])
            sv = min([min(abs(a - ky), abs(c - ky)) for a, c in vv]) if vv else None; sh = min([min(abs(a - kx), abs(c - kx)) for a, c in hh]) if hh else None
            if sv is not None and sh is not None and sv > 3 and sv / s >= 0.06 and abs(sv - sh) < 1.0 and sv < 0.5 * min(b[2] - b[0], b[3] - b[1]):
                r_in = round(float((sv + sh) / 2 / s), 3); dx = 1 if kx == b[0] else -1; dy = 1 if ky == b[1] else -1
                p1 = (kx + dx * sh, ky); p2 = (kx, ky + dy * sv)
                chamfer = any((np.hypot(a0 - p1[0], a1 - p1[1]) < 1.5 and np.hypot(a2 - p2[0], a3 - p2[1]) < 1.5) or (np.hypot(a0 - p2[0], a1 - p2[1]) < 1.5 and np.hypot(a2 - p1[0], a3 - p1[1]) < 1.5) for a0, a1, a2, a3, _ in Sv if abs(a0 - a2) > 0.5 and abs(a1 - a3) > 0.5)     # one straight segment between the two stops
                if chamfer: v['chamfers'].append(f'{r_in} x 45 deg corner (straight cut)')
                else:
                    ex = next((f for f in v['fillets'] if abs(f['radius_in'] - r_in) < 0.004), None)
                    if ex and ex.get('source') != 'outline corner': continue                  # the same corner already found as an arc
                    if ex: ex['count'] += 1; ex['at_pt'].append([round(kx, 1), round(ky, 1)])
                    else: v['fillets'].append(dict(radius_in=r_in, count=1, at_pt=[[round(kx, 1), round(ky, 1)]], source='outline corner'))
        rd = [d for d in sheet.dimensions if d.id in v['dims'] and d.kind == 'radius' and d.value]
        v['radius_dimensions'] = [dict(id=d.id, value_in=round(d.value if d.unit == 'inch' else d.value / 25.4, 4), count=d.count) for d in rd]
        v['chamfers'] += [n.text.strip() for n in notes if CHAMFER.search(n.text) and b[0] - 120 <= n.pos[0] <= b[2] + 120 and b[1] - 120 <= n.pos[1] <= b[3] + 120]


def zone_blocks(zones, views, dims):
    """aggregate per zone: the roles of the views in it, dimensions by part axis, hole types, fillet radii"""
    for name, z in zones.items():
        zb = z['box']; roles = []; axis = {'X': 0, 'Y': 0, 'Z': 0, '?': 0}; holes = {}; fil = []; slots = 0
        for v in views:
            if v['id'] not in z['views']: continue
            roles.append(f"{v['id']} {v['role']}")
            ax = v.get('axes', {})
            pos = {d['id']: d for d in v.get('dimension_axes', [])}
            for d in dims:
                if d.id in pos and d.text_pos and zb[0] <= d.text_pos[0] < zb[2] and zb[1] <= d.text_pos[1] < zb[3]:
                    dr = pos[d.id].get('direction'); a = ax.get('horizontal') if dr == 'horizontal' else ax.get('vertical') if dr == 'vertical' else '?'; axis[a if a in axis else '?'] += (pos[d.id].get('count') or 1) if False else 1
            for f in v['features']:
                if zb[0] <= f['centre_pt'][0] < zb[2] and zb[1] <= f['centre_pt'][1] < zb[3]: holes[f['type']] = holes.get(f['type'], 0) + 1
            for sl in v['slots']:
                if zb[0] <= sl['centre_pt'][0] < zb[2] and zb[1] <= sl['centre_pt'][1] < zb[3]: slots += 1
            for fl in v['fillets']:
                if any(zb[0] <= p[0] < zb[2] and zb[1] <= p[1] < zb[3] for p in fl['at_pt']): fil.append(fl['radius_in'])
        z['blocks'] = dict(views=roles, dimension_axes={k: n for k, n in axis.items() if n}, holes=holes, slots=slots, fillet_radii_in=sorted(set(fil)))
