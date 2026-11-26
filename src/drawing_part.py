#!/usr/bin/env python3
"""Rebuild a prismatic detail part from its drawing: overall size, corner radii, round holes with their positions and depths, measured to scale from the drawn linework, each value marked `stated` or `unconfirmed`.

  python3 src/drawing_part.py DRAWING.pdf PAGE [--json OUT.json] [--md OUT.md] [--unit inch|mm] [--cad PART.SLDPRT] [--freecad OUT.step --fcpython /path/to/freecadcmd]      measure(pdf, page) -> dict

The drawing is read twice: its dimensions and callouts as text (pdf_drawing.read_pdf) and its linework as geometry. A drawing that says DO NOT SCALE is not a source of dimensions, so every number taken from the linework is
`unconfirmed`: it is the drawn length divided by the scale. A measured value becomes `stated` only when a printed dimension (or callout) equals it within 1/64 in, and then the printed value is used. The elevation may lie above the plan (as in the Hydro sheet) or below it. The scale is not read from the sheet but
inferred: for each candidate scale (1/4 .. 4 times the 72 pt/in of full size) the number of printed dimensions that equal the spacing of two object lines or circle centres in the plan view is counted, and the best candidate (with at least three
matches) wins; ties go to full size. If no candidate has three matches nothing is measured and the reason is given.

What it finds, for a part drawn as a plan view (the view holding the most full circles) with an elevation directly above or below it (the same horizontal extent):
  overall   width and depth from the outline rectangle of the plan view (object lines only: line width >= 0.6 pt, the thin lines are dimension and centre lines), height from the elevation's outline
  corners   a corner whose outline lines stop short of it by the same setback on both sides is a rounded corner of that radius
  holes     each group of concentric full circles: diameters (outermost first), centre from the left and bottom edges; depth of a step from the horizontal lines (visible or hidden) in the elevation that span the
            hole's width, counted down from the top; a hole with no such line is `through (assumed)`
Not found: anything visible only in a side or section view (the tapped hole in the side of the Hydro block), slots, threads, chamfers, non-circular pockets; the elevation's levels are read from lines that span a hole exactly, so a
step hidden behind another line is missed. The result lists what it did not measure. Circles are recognised as closed chains of short segments whose best-fit circle leaves under 5% of the radius (an outlined circle is
a polyline); a circle drawn as a Bezier path arrives the same way.
"""
import json, os, re, subprocess, sys, collections
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
IN = 25.4; OBJ_W = 0.6


def read_segments(pdf, page):
    from pdfminer.high_level import extract_pages
    import pdf_drawing as P
    for pg in extract_pages(pdf, page_numbers=[page - 1]):
        chars = []; geo = P.Geo(); P._walk(pg, chars, geo)
    return np.array(geo.segs, float).reshape(-1, 5), geo


def _components(S, tol=0.1):
    key = lambda x, y: (round(x / tol), round(y / tol)); adj = collections.defaultdict(list)
    for i, s in enumerate(S): adj[key(s[0], s[1])].append(i); adj[key(s[2], s[3])].append(i)
    seen = set(); out = []
    for i in range(len(S)):
        if i in seen: continue
        st = [i]; seen.add(i); comp = []
        while st:
            j = st.pop(); comp.append(j)
            for k in (key(S[j][0], S[j][1]), key(S[j][2], S[j][3])):
                for m in adj[k]:
                    if m not in seen: seen.add(m); st.append(m)
        out.append(comp)
    return out


def circles(S, min_segs=8):
    """full circles from closed chains of segments -> [dict(cx, cy, r)] in sheet points"""
    out = []
    for comp in _components(S):
        if len(comp) < min_segs: continue
        pts = np.vstack([S[comp][:, :2], S[comp][:, 2:4]]); A = np.c_[2 * pts[:, 0], 2 * pts[:, 1], np.ones(len(pts))]; b = (pts ** 2).sum(1)
        try: sol = np.linalg.lstsq(A, b, rcond=None)[0]
        except np.linalg.LinAlgError: continue
        cx, cy = sol[:2]; r2 = sol[2] + cx * cx + cy * cy
        if r2 <= 0: continue
        r = float(np.sqrt(r2)); res = float(np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r).max()); ang = np.degrees(np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx))
        cover = len(set((ang // 15).astype(int))) * 15
        if 5 < r < 400 and res < 0.05 * r and cover >= 345: out.append(dict(cx=float(cx), cy=float(cy), r=r, w=float(np.median(S[comp][:, 4]))))
    return out


def slots(S, min_segs=10):
    """closed stadium outlines (a slot: two straight sides and two semicircular ends) from chains of segments -> [dict(cx, cy, width, length_between_centres, orientation 'x'|'y')] in points; a closed chain whose perimeter equals
    that of its bounding box read as a stadium (within 4 percent) and whose box is at least 1.2 times longer than wide"""
    out = []
    for comp in _components(S):
        if len(comp) < min_segs: continue
        seg = S[comp]; pts = np.vstack([seg[:, :2], seg[:, 2:4]]); x0, y0 = pts.min(0); x1, y1 = pts.max(0); w, h = x1 - x0, y1 - y0; lo, hi = min(w, h), max(w, h)
        if lo < 4 or hi < 1.2 * lo: continue
        ln = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
        if float(np.median(seg[:, 4])) < 0.6 or len(comp) < 14 or int((ln >= 0.6 * (hi - lo)).sum()) < 2 or int((ln < 0.4 * lo).sum()) < 8: continue          # two long straight sides and many short segments for the round ends
        per = float(np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]).sum()); stad = 2 * (hi - lo) + np.pi * lo
        if abs(per - stad) <= 0.04 * stad: out.append(dict(cx=float((x0 + x1) / 2), cy=float((y0 + y1) / 2), width=float(lo), length_between_centres=float(hi - lo), orientation='x' if w >= h else 'y', w=float(np.median(seg[:, 4]))))
    return out


def _union(intervals, gap=6.0):
    iv = sorted((min(a, b), max(a, b)) for a, b in intervals); out = []
    for a, b in iv:
        if out and a <= out[-1][1] + gap: out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out


def lines_by_axis(S, min_len=8.0, width=None):
    """horizontal -> {y: [x intervals]}, vertical -> {x: [y intervals]}, rounded to 0.05 pt; width filter: 'object' keeps widths >= OBJ_W"""
    H = collections.defaultdict(list); V = collections.defaultdict(list)
    for x0, y0, x1, y1, w in S:
        if width is not None and w < width: continue
        if abs(y0 - y1) < 0.05 and abs(x1 - x0) >= min_len: H[round((y0 + y1) / 2 * 20) / 20].append((x0, x1))
        elif abs(x0 - x1) < 0.05 and abs(y1 - y0) >= min_len: V[round((x0 + x1) / 2 * 20) / 20].append((y0, y1))
    return {k: _union(v) for k, v in H.items()}, {k: _union(v) for k, v in V.items()}


def infer_scale(values_in, xs, ys, extents_pt=(), diameters_pt=(), pt_per_in=72.0, factors=(0.25, 0.5, 1.0, 2.0, 4.0, 1.5, 3.0, 0.75), tol=0.7):
    """-> (pt per inch, matches, runner-up score, ambiguous) or (None, best matches, 0, True). Each candidate scale is scored by how many printed values equal the spacing of two line coordinates (xs, ys: points) within tol
    points beyond what chance would give (a target near many spacings matches by luck: the expected number is subtracted), plus two for each of the plan view's overall sizes that equals a printed value. Ties go to full size.
    `ambiguous` when the best score is under 1.2 times the runner-up's."""
    def pairs(c):
        c = sorted(set(round(v, 2) for v in c)); return np.array([abs(b - a) for i, a in enumerate(c) for b in c[i + 1:]])
    dx, dy = pairs(xs), pairs(ys); alld = np.concatenate([dx, dy]) if len(dx) + len(dy) else np.zeros(0); res = []
    for f in factors:
        sc = pt_per_in * f; n = 0; chance = 0.0
        for v in values_in:
            t = v * sc
            if len(alld) and np.min(np.abs(alld - t)) <= tol: n += 1
            chance += min(0.95, float(np.sum(np.abs(alld - t) <= 8.0)) / 16.0 * 2 * tol) if len(alld) else 0.0              # how likely a random target of this size lands on a spacing
        bonus = 2 * sum(1 for e in extents_pt if any(abs(e / sc - v) * sc <= tol for v in values_in)) + 1.5 * min(4, sum(1 for d_ in set(round(x, 1) for x in diameters_pt) if any(abs(d_ / sc - v) * sc <= tol for v in values_in)))      # circle diameters equal to a printed value or callout
        res.append((n - chance + bonus, f == 1.0, n, sc))
    res.sort(reverse=True); ok = [r for r in res if r[2] >= 3]
    if not ok: return None, max(r[2] for r in res), 0, True
    best = ok[0]; run = max([r[0] for r in res if r is not best] + [0])
    return best[3], best[2], round(run, 1), best[0] < 1.2 * run


def _near(v, vals, tol): return next((x for x in vals if abs(x - v) <= tol), None)


def measure(pdf, page, stated_tol_in=1 / 64, unit=None):
    """unit: 'inch' or 'mm' to override the sheet's unit when the reader could not tell (`unit_assumed`); default: as read"""
    from pdf_drawing import read_pdf
    sheet = read_pdf(pdf, pages=[page]).sheets[0]; S, geo0 = read_segments(pdf, page); W_, H_ = sheet.size_pt
    keep = np.ones(len(S), bool)                                                    # the sheet border (very long lines) and the title block are not part of any view
    for i, (x0, y0, x1, y1, w) in enumerate(S):
        if abs(y0 - y1) < 0.05 and abs(x1 - x0) >= 0.85 * W_ or abs(x0 - x1) < 0.05 and abs(y1 - y0) >= 0.85 * H_: keep[i] = False
    tb = [n.pos for n in sheet.notes if n.kind == 'title_block' and n.pos]
    tbx0, tby1 = (min(p_[0] for p_ in tb) - 60, max(p_[1] for p_ in tb) + 30) if tb else (None, None)
    if tb:
        for i, (x0, y0, x1, y1, w) in enumerate(S):
            if min(x0, x1) >= tbx0 and max(y0, y1) <= tby1: keep[i] = False
    S = S[keep]
    if unit and unit not in ('inch', 'mm'): raise ValueError('unit must be inch or mm')
    as_in = lambda d: d.value if (unit == 'inch' or (not unit and d.unit == 'inch')) else d.value / IN
    vals = [(d.id, as_in(d)) for d in sheet.dimensions if d.value]; call = []
    from feature_determinacy import callout_values
    call = callout_values([n.text for n in sheet.notes]); notfound = []
    C = circles(S); geo = geo0
    for k, a in enumerate(geo.arcs):                                                    # circles the PDF reader already fitted (Bezier circles)
        if tb and a[0] >= tbx0 and a[1] <= tby1: continue
        if a[2] > 5 and not any(np.hypot(a[0] - c['cx'], a[1] - c['cy']) < 0.5 and abs(a[2] - c['r']) < 0.5 for c in C) and (a[3][2] - a[3][0]) > 1.8 * a[2]: C.append(dict(cx=float(a[0]), cy=float(a[1]), r=float(a[2]), w=float(geo.arc_w[k]) if k < len(geo.arc_w) else OBJ_W))
    if len(C) < 2: return dict(error='no plan view: fewer than 2 full circles were found in the linework', circles=len(C))
    # plan view: the biggest cluster of circles (centres within 110 pt of a neighbour; callout bubbles and other views are farther away)
    groups = []
    for c in C:
        for g in groups:
            if any(np.hypot(c['cx'] - o['cx'], c['cy'] - o['cy']) < 110 for o in g): g.append(c); break
        else: groups.append([c])
    g = max(groups, key=len); gx0 = min(c['cx'] - c['r'] for c in g); gx1 = max(c['cx'] + c['r'] for c in g); gy0 = min(c['cy'] - c['r'] for c in g); gy1 = max(c['cy'] + c['r'] for c in g)
    objw = float(np.median([c['w'] for c in g])) - 0.05                               # the plan's circles are object lines: lines at least that thick are the part's outline, thinner ones dimension and centre lines
    H, V = lines_by_axis(S, width=objw)
    # views = clusters of object-width linework whose boxes lie within 8 pt of each other; the plan view is the cluster that holds the circles
    boxes = [(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)) for x0, y0, x1, y1, w in S if w >= objw] + [tuple(a[3]) for k, a in enumerate(geo.arcs) if (geo.arc_w[k] if k < len(geo.arc_w) else objw) >= objw]
    parent = list(range(len(boxes)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    cell = collections.defaultdict(list)
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        for gx in range(int((x0 - 8) // 24), int((x1 + 8) // 24) + 1):
            for gy in range(int((y0 - 8) // 24), int((y1 + 8) // 24) + 1):
                for j in cell[(gx, gy)]:
                    bx = boxes[j]
                    if x0 - 8 <= bx[2] and bx[0] <= x1 + 8 and y0 - 8 <= bx[3] and bx[1] <= y1 + 8: parent[find(i)] = find(j)
                cell[(gx, gy)].append(i)
    clusters = collections.defaultdict(list)
    for i, bx in enumerate(boxes): clusters[find(i)].append(bx)
    cbox = {k: (min(b[0] for b in v), min(b[1] for b in v), max(b[2] for b in v), max(b[3] for b in v)) for k, v in clusters.items()}
    c0 = g[0]; plan = next((k for k, bx in cbox.items() if bx[0] <= c0['cx'] - c0['r'] + 1 and bx[2] >= c0['cx'] + c0['r'] - 1 and bx[1] <= c0['cy'] - c0['r'] + 1 and bx[3] >= c0['cy'] + c0['r'] - 1 and any(abs(b[0] - bx[0]) < 0.1 or True for b in clusters[k])), None)
    if plan is None: return dict(error='no outline of object lines around the circles', circles=len(C))
    L, B, R, T = cbox[plan]
    if (R - L) < 1.15 * (gx1 - gx0) or (T - B) < 1.15 * (gy1 - gy0): return dict(error='no outline of object lines around the circles (the cluster is the circles alone)', circles=len(C))
    if R - L > 0.6 * W_ or T - B > 0.6 * H_: return dict(error='the outline around the circles spans most of the sheet: a whole assembly or the sheet border, not one part', circles=len(C))
    labels = []
    for n in sheet.notes:
        mm_ = re.search(r'SCALE\s*(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)', n.text.upper())
        if mm_ and n.pos and float(mm_.group(2)) > 0 and float(mm_.group(1)) > 0:
            dx = max(L - n.pos[0], 0, n.pos[0] - R); dy = max(B - n.pos[1], 0, n.pos[1] - T); labels.append((float(np.hypot(dx, dy)), float(mm_.group(1)) / float(mm_.group(2)), n.text))
    label = min(labels) if labels and min(labels)[0] <= 150 else None                     # the SCALE a : b label nearest to the plan view
    # scale from the dimensions: coordinates of the lines of the plan view and of the elevation (any width: extension lines carry feature coordinates too) and of the circles
    ev = next(((bx[1], bx[3]) for k, bx in cbox.items() if k != plan and abs(bx[0] - L) < 1.5 and abs(bx[2] - R) < 1.5 and (bx[1] > T or bx[3] < B) and bx[3] - bx[1] > 20), None)
    y_lo, y_hi = min(B, ev[0] if ev else B) - 2, max(T, ev[1] if ev else T) + 2
    nearx = [x for x in V if L - 2 <= x <= R + 2] + [c['cx'] + k * c['r'] for c in g for k in (-1, 0, 1)]; neary = [y for y in H if y_lo <= y <= y_hi] + [c['cy'] + k * c['r'] for c in g for k in (-1, 0, 1)]
    src = 'inferred'; res_ = None; allv = [v for _, v in vals] + call; ds = [2 * c['r'] for c in g]
    free = infer_scale(allv, nearx, neary, [R - L, T - B], ds)
    if label:                                                                                    # the sheet says the scale: accept it when the printed values agree with it about as well as with the best scale
        r_ = infer_scale(allv, nearx, neary, [R - L, T - B], ds, factors=(label[1],))
        if r_[0] is not None and (free[0] is None or r_[1] >= 0.7 * free[1]): res_ = r_; src = f'label {label[2].strip()}'
        else: notfound.append(f"the label '{label[2].strip()}' near the plan view disagrees with the printed values; the scale was inferred instead")
    s, nm, runner, amb = res_ if res_ else free
    if s is None: return dict(error=f'scale not determined: at most {nm} printed values match a spacing of lines in the plan view (3 needed)', circles=len(C))
    if src != 'inferred': amb = False
    if s is None: return dict(error=f'scale not determined: at most {nm} printed values match a spacing of lines in the plan view (3 needed)', circles=len(C))
    tol = stated_tol_in
    dpos = {d.id: d.text_pos for d in sheet.dimensions}; cpos = []
    for n in sheet.notes:
        for v in callout_values([n.text]): cpos.append(('callout', v, n.pos))
    allv = [(i, v, dpos.get(i)) for i, v in vals] + cpos
    # the elevation first: its rectangle decides where the plan region ends and the elevation region begins
    elev = None
    for k, bx in sorted(cbox.items(), key=lambda t: min(abs(t[1][1] - T), abs(t[1][3] - B))):
        if k != plan and abs(bx[0] - L) < 1.5 and abs(bx[2] - R) < 1.5 and (bx[1] > T or bx[3] < B) and bx[3] - bx[1] > 20: elev = (bx[1], bx[3]); break
    above = bool(elev and elev[0] > T)
    def region(box):
        x0, y0, x1, y1 = box; return [(i, v) for i, v, p in allv if p and x0 <= p[0] <= x1 and y0 <= p[1] <= y1]
    if not elev: plan_vals = region((L - 170, B - 170, R + 170, T + 170)); elev_vals = []
    elif above: plan_vals = region((L - 170, B - 170, R + 170, elev[0] - 25)); elev_vals = region((L - 170, elev[0] - 25, R + 170, elev[1] + 170))
    else: plan_vals = region((L - 170, elev[1] + 25, R + 170, T + 170)); elev_vals = region((L - 170, elev[0] - 170, R + 170, elev[1] + 25))
    def val(x_pt, what, pool, alts=(), total_in=None):
        """x_pt: the drawn length; alts: other lengths the same feature could be printed as (from the other edge): the first that equals a printed value in the pool is used"""
        for k, cand_pt in enumerate((x_pt,) + tuple(a for a, _ in alts)):
            raw = cand_pt / s; m = next(((i, v) for i, v in pool if abs(v - raw) <= tol), None)
            if m:
                std = m[1] if k == 0 or total_in is None else total_in - m[1]                  # a value printed from the other edge: the position from the standard edge is the stated span minus it
                return dict(what=what, measured_in=round(x_pt / s, 4), status='stated', value_in=round(std, 4), by=m[0], printed_in=round(m[1], 4), residual_in=round(raw - m[1], 4), reference=('' if k == 0 else alts[k - 1][1]))
        raw = x_pt / s; return dict(what=what, measured_in=round(raw, 4), status='unconfirmed', value_in=round(raw, 4), by=None, residual_in=None, reference='')
    out = dict(scale_pt_per_in=round(s, 3), scale_source=src, scale_matches=nm, scale_runner_up=runner, scale_ambiguous=bool(amb), plan_outline_pt=[round(L, 2), round(B, 2), round(R, 2), round(T, 2)], quantities=[])
    q = out['quantities']; q.append(val(R - L, 'plan width', plan_vals)); q.append(val(T - B, 'plan depth', plan_vals))
    corners = []
    for name, (cx_, cy_) in dict(bl=(L, B), br=(R, B), tl=(L, T), tr=(R, T)).items():
        vv = next((iv for k, iv in V.items() if abs(k - cx_) < 0.4), []); hh = next((iv for k, iv in H.items() if abs(k - cy_) < 0.4), [])
        sv = min([min(abs(a - cy_), abs(b - cy_)) for a, b in vv]) if vv else None; sh = min([min(abs(a - cx_), abs(b - cx_)) for a, b in hh]) if hh else None
        if sv is not None and sh is not None and sv > 3 and abs(sv - sh) < 1.0 and sv < 0.5 * min(R - L, T - B): corners.append((name, (sv + sh) / 2))
    if corners:
        rr = float(np.median([r for _, r in corners])); q.append(val(rr, f"corner radius at {', '.join(n for n, _ in corners)}", plan_vals)); out['rounded_corners'] = [n for n, _ in corners]; out['corner_radius_pt'] = round(rr, 2)
    verts = {}
    if elev:
        q.append(val(elev[1] - elev[0], 'elevation height (thickness)', elev_vals)); _, Vall = lines_by_axis(S, min_len=1.0)
        verts = Vall
    else: notfound.append('no elevation with the same horizontal extent as the plan view was found: heights and hole depths are not measured')
    holes = []
    for c in sorted(g, key=lambda c: -c['r']):
        for h in holes:
            if np.hypot(c['cx'] - h['cx'], c['cy'] - h['cy']) < 1.0: h['rs'].append(c['r']); break
        else: holes.append(dict(cx=c['cx'], cy=c['cy'], rs=[c['r']]))
    out['holes'] = []
    for i, h in enumerate(sorted(holes, key=lambda h: (-max(h['rs']), h['cx'], h['cy'])), 1):
        rs = sorted(h['rs'], reverse=True)
        rec = dict(id=f'H{i}', diameters=[val(2 * r, f'hole {i} diameter {k + 1}', plan_vals + elev_vals) for k, r in enumerate(rs)],
                   x_from_left=val(h['cx'] - L, f'hole {i} centre x', plan_vals, [(R - h['cx'], 'from the right edge')], total_in=q[0]['value_in']), y_from_bottom=val(h['cy'] - B, f'hole {i} centre y', plan_vals, [(T - h['cy'], 'from the top edge')], total_in=q[1]['value_in']), steps=[])
        if elev:
            for k, r in enumerate(rs):
                iv = []
                for xs_ in (h['cx'] - r, h['cx'] + r):
                    for x, yy in verts.items():
                        if abs(x - xs_) < 0.5: iv += [(a, b) for a, b in yy if b > elev[0] - 1 and a < elev[1] + 1]
                u = sorted(_union(iv, gap=4.0), key=lambda t: -(t[1] - t[0]))
                if not u: rec['steps'].append(dict(what=f'hole {i} diameter {k + 1} length', status='unconfirmed', value_in=None, note='no hidden or visible lines at this diameter in the elevation: through (assumed)')); continue
                a, b = max(u[0][0], elev[0]), min(u[0][1], elev[1]); near, far = (abs(a - elev[0]) < 1.5, abs(b - elev[1]) < 1.5) if above else (abs(b - elev[1]) < 1.5, abs(a - elev[0]) < 1.5); face = 'plan face' if near else ('far face' if far else 'interior')
                st = val(b - a, f'hole {i} diameter {k + 1} length from the {face}', elev_vals); st['from'] = face; st['through'] = bool(near and far); rec['steps'].append(st)
        out['holes'].append(rec)
        for x in rec['diameters'] + [rec['x_from_left'], rec['y_from_bottom']] + rec['steps']: q.append(x)
    out['not_measured'] = notfound + ['features visible only in side or section views (tapped side holes, slots, threads, chamfers) are not measured']
    out['counts'] = collections.Counter(x['status'] for x in q); out['counts'] = dict(out['counts']); out['elevation_pt'] = [round(elev[0], 2), round(elev[1], 2)] if elev else None
    return out


FC_SCRIPT = '''
import json, FreeCAD, Part
from FreeCAD import Vector as V
m = json.load(open(%r)); IN = 25.4
q = {x['what']: x['value_in'] for x in m['quantities'] if x.get('value_in') is not None}
W, D = q['plan width'] * IN, q['plan depth'] * IN; T = q.get('elevation height (thickness)', 0) * IN
if not T: raise SystemExit('no thickness measured')
solid = Part.makeBox(W, D, T)                                   # X = plan x from the left edge, Y = plan y from the bottom edge, Z = up from the far face; the face seen in the plan view is z = T
if m.get('corner_radius_pt'):
    r = m['corner_radius_pt'] / m['scale_pt_per_in'] * IN
    pick = {'bl': (0, 0), 'br': (W, 0), 'tl': (0, D), 'tr': (W, D)}
    edges = [e for e in solid.Edges if abs(e.Vertexes[0].Point.z - e.Vertexes[1].Point.z) > 1e-6 and any(abs(e.Vertexes[0].Point.x - pick[k][0]) < 1e-6 and abs(e.Vertexes[0].Point.y - pick[k][1]) < 1e-6 for k in m['rounded_corners'])]
    if edges: solid = solid.makeFillet(r, edges)
for h in m['holes']:
    cx = h['x_from_left']['value_in'] * IN; cy = h['y_from_bottom']['value_in'] * IN
    for k, d in enumerate(h['diameters']):
        st = h['steps'][k] if k < len(h['steps']) else {}
        if st.get('value_in') and st.get('from') in ('plan face', 'far face'): L = st['value_in'] * IN; z0 = T - L if st['from'] == 'plan face' else 0.0
        else: L = T; z0 = 0.0                                     # no length found: through (assumed)
        solid = solid.cut(Part.makeCylinder(d['value_in'] * IN / 2, L + 0.02, V(cx, cy, z0 - 0.01), V(0, 0, 1)))
doc = FreeCAD.newDocument('p'); o = doc.addObject('Part::Feature', 'part'); o.Shape = solid; doc.recompute()
Part.export([o], %r)
print(json.dumps(dict(volume_mm3=round(solid.Volume, 1), faces=len(solid.Faces), bbox_mm=[round(solid.BoundBox.XLength, 3), round(solid.BoundBox.YLength, 3), round(solid.BoundBox.ZLength, 3)])))
'''


def build_freecad(result_json, step_out, fcpython):
    """builds the measured part in FreeCAD (a separate interpreter: freecadcmd -c or the conda python) and writes a STEP"""
    sc = FC_SCRIPT % (result_json, step_out)
    p = os.path.join(os.path.dirname(os.path.abspath(step_out)), '_build.py'); open(p, 'w').write(sc)
    r = subprocess.run([fcpython, '-c', f"exec(open({p!r}).read())"], capture_output=True, text=True, timeout=300)           # fcpython: the freecadcmd binary (a plain python cannot import FreeCAD)
    lines = [l for l in (r.stdout + '\n' + r.stderr).splitlines() if l.startswith('{') and 'volume_mm3' in l]
    return lines[-1] if lines else 'FreeCAD build failed: ' + (r.stderr or r.stdout)[-300:]



def compare_cad(m, feats):
    """drawing-measured part against the CAD part's feature list (part_features): the vertical axis is the one most holes share; the 8 axis-aligned placements of the plan view are tried and the best is kept.
    -> dict(rows=[...]) each row: what, drawing, cad, delta, unit, status (the drawing value's status)"""
    IN_ = 25.4; q = {x['what']: x for x in m['quantities']}; lo = np.array(feats['bbox_mm'][0]); hi = np.array(feats['bbox_mm'][1]); ext = hi - lo
    holes = [c for c in feats['cylinders'] if c['kind'] == 'hole' and c['axis_label']]; cnt = collections.Counter(c['axis_label'][1] for c in holes)
    if not cnt: return dict(error='the CAD part has no holes to compare')
    ax = 'XYZ'.index(cnt.most_common(1)[0][0]); other = [i for i in range(3) if i != ax]
    stacks = collections.defaultdict(list)
    for c in holes:
        if c['axis_label'][1] == 'XYZ'[ax]: stacks[(round(c['foot_mm'][other[0]], 2), round(c['foot_mm'][other[1]], 2))].append(c)
    cad = [dict(pos=np.array(k) - lo[other], dia=sorted([c['diameter_mm'] for c in v], reverse=True), rng={c['diameter_mm']: c['axial_range_mm'] for c in v}) for k, v in stacks.items()]
    W, D = q['plan width']['value_in'] * IN_, q['plan depth']['value_in'] * IN_; dr = [(h['x_from_left']['value_in'] * IN_, h['y_from_bottom']['value_in'] * IN_, h) for h in m['holes']]
    best = None
    for sw in (False, True):
        for fx in (False, True):
            for fy in (False, True):
                Wd, Dd = (D, W) if sw else (W, D)
                if abs(Wd - ext[other[0]]) > 2.0 or abs(Dd - ext[other[1]]) > 2.0: continue
                tot = 0.0; pairs = []
                for x, y, h in dr:
                    u, v = (y, x) if sw else (x, y); u = Wd - u if fx else u; v = Dd - v if fy else v
                    near = min(cad, key=lambda c: np.hypot(c['pos'][0] - u, c['pos'][1] - v)); tot += float(np.hypot(near['pos'][0] - u, near['pos'][1] - v)); pairs.append(((u, v), near, h))
                if best is None or tot < best[0]: best = (tot, pairs, (sw, fx, fy))
    rows = []
    def add(what, dv, cv, unit, status): rows.append(dict(what=what, drawing=None if dv is None else round(float(dv), 3), cad=None if cv is None else round(float(cv), 3), delta=None if dv is None or cv is None else round(float(dv - cv), 3), unit=unit, status=status))
    add('plan width', W, max(ext[other]) if abs(W - D) < 1 else ext[other[0]], 'mm', q['plan width']['status']); add('plan depth', D, ext[other[1]], 'mm', q['plan depth']['status'])
    if 'elevation height (thickness)' in q: add('thickness', q['elevation height (thickness)']['value_in'] * IN_, ext[ax], 'mm', q['elevation height (thickness)']['status'])
    bosses = [c for c in feats['cylinders'] if c['kind'] == 'boss' and c['axis_label'] and c['axis_label'][1] == 'XYZ'[ax]]
    if m.get('corner_radius_pt') and bosses: add('corner radius', m['corner_radius_pt'] / m['scale_pt_per_in'] * IN_, bosses[0]['radius_mm'], 'mm', q[next(k for k in q if k.startswith('corner radius'))]['status'])
    if best:
        for (u, v), near, h in best[1]:
            add(f"{h['id']} centre u", u, near['pos'][0], 'mm', h['x_from_left']['status']); add(f"{h['id']} centre v", v, near['pos'][1], 'mm', h['y_from_bottom']['status'])
            for k, d in enumerate(h['diameters']):
                cd = near['dia'][k] if k < len(near['dia']) else None; add(f"{h['id']} diameter {k + 1}", d['value_in'] * IN_, cd, 'mm', d['status'])
                st = h['steps'][k] if k < len(h['steps']) else {}
                if st.get('value_in') and cd is not None:
                    r = near['rng'].get(cd); add(f"{h['id']} diameter {k + 1} length", st['value_in'] * IN_, None if not r else r[1] - r[0], 'mm', st['status'])
    return dict(rows=rows, placement=dict(swap=best[2][0], flip_x=best[2][1], flip_y=best[2][2]) if best else None, hole_position_error_mm=round(best[0], 3) if best else None, drawing_holes=len(dr), cad_holes=len(cad))


def compare_markdown(c):
    if 'error' in c: return 'CAD comparison: ' + c['error'] + '\n'
    L = ['## Drawing-measured part against the CAD part', '', f"{c['drawing_holes']} hole stacks measured, {c['cad_holes']} in the CAD along the same axis; plan view placed as {c['placement']}; summed centre error {c['hole_position_error_mm']} mm.", '', '| quantity | drawing (mm) | CAD (mm) | drawing - CAD | drawing value is |', '|---|---|---|---|---|']
    for r in c['rows']: L.append(f"| {r['what']} | {r['drawing']} | {r['cad']} | {r['delta']} | {r['status']} |")
    big = [r for r in c['rows'] if r['delta'] is not None and abs(r['delta']) > 0.2]
    L += ['', f"{len(c['rows'])} quantities compared; {len(big)} differ by more than 0.2 mm" + (': ' + ', '.join(f"{r['what']} ({r['delta']})" for r in big) if big else '') + '. A printed fractional-inch dimension differs from a metric CAD value by up to about 0.2 mm just by rounding.']
    return '\n'.join(L) + '\n'


def to_markdown(m, name=''):
    if 'error' in m: return f"# Part rebuilt from the drawing {name}\n\nNot measured: {m['error']}\n"
    L = [f'# Part rebuilt from the drawing {name}'.strip(), '', f"Scale ({m.get('scale_source', 'inferred')}): {m['scale_pt_per_in']} pt per inch ({m['scale_matches']} printed values match line spacings; runner-up score {m['scale_runner_up']}{'; the scale is NOT clearly better than the next candidate' if m.get('scale_ambiguous') else ''}). Values: {m['counts']}. `unconfirmed` = measured from the linework at that scale, not printed on the sheet.", '',
         '| quantity | value (in) | status | printed by | measured - printed (in) |', '|---|---|---|---|---|']
    for x in m['quantities']: L.append(f"| {x['what']} | {x['value_in'] if x.get('value_in') is not None else ''} | {x['status']} | {x.get('by') or ''} | {x.get('residual_in') if x.get('residual_in') is not None else ''} |" if 'note' not in x else f"| {x['what']} | | {x['status']} | | {x['note']} |")
    L += ['', 'Not measured: ' + '; '.join(m['not_measured'])]
    return '\n'.join(L) + '\n'


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0].startswith('-'): raise SystemExit(__doc__)
    m = measure(a[0], int(a[1]), unit=a[a.index('--unit') + 1] if '--unit' in a else None)
    if '--json' in a: json.dump(m, open(a[a.index('--json') + 1], 'w'), indent=1, default=float)
    if '--freecad' in a and 'error' not in m:
        out = a[a.index('--freecad') + 1]; jp = a[a.index('--json') + 1] if '--json' in a else out + '.json'
        if '--json' not in a: json.dump(m, open(jp, 'w'), default=float)
        print(build_freecad(jp, out, a[a.index('--fcpython') + 1] if '--fcpython' in a else 'python3'))
    md = to_markdown(m, os.path.basename(a[0]))
    if '--cad' in a and 'error' not in m:
        from part_features import part_features
        c = compare_cad(m, part_features(a[a.index('--cad') + 1])); md += '\n' + compare_markdown(c)
        if '--json' in a: json.dump(c, open(a[a.index('--json') + 1] + '.compare.json', 'w'), indent=1, default=float)
    if '--md' in a: open(a[a.index('--md') + 1], 'w').write(md)
    print(md)


if __name__ == '__main__':
    main()
