"""Vector-PDF engineering drawing -> drawing IR (drawing_ir.py).

  read_pdf(path) -> Drawing            python3 src/pdf_drawing.py FILE.pdf [--json]

Needs `pip install pdfminer.six` (MIT). Works on vector PDFs (text is real text, lines are real paths). Scanned/raster pages and drawings whose text was
converted to outlines give an empty sheet with a warning: they need OCR, which this reader does not do.

What it recovers, per page: the sheet scale ("SCALE 1:2"), views (clusters of object linework, with the label below them), dimensions
(paired-arrow linear dimensions, leader-style diameter/radius dimensions; value, tolerance forms, count, extension lines, the arc a diameter/radius points at, and the
value measured from the drawn geometry), datum feature symbols, GD&T frames, notes and title-block text.

Conventions: PDF points, origin bottom-left, y up. `drawn` is drawn length / sheet scale converted to mm. Everything is heuristic and reports what it could not read
(Sheet.warnings, Dimension.conf) instead of guessing. Verified on the synthetic drawing of tools/make_test_drawing.py; NOT yet checked against a real SolidWorks export.
"""
import math, os, re, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drawing_ir import MM_PER_PT, Tol, Dimension, View, Note, Datum, GdtFrame, Sheet, Drawing

NUM = r'(?:\d+(?:\.\d+)?|\.\d+)'
GDT_SYMBOLS = {'⌖': 'position', '⏥': 'flatness', '⟂': 'perpendicularity', '∥': 'parallelism', '∠': 'angularity', '◎': 'concentricity',
               '⌭': 'cylindricity', '⌯': 'symmetry', '⌓': 'profile of a surface', '⌒': 'profile of a line', '↗': 'circular runout',
               '⌰': 'total runout', '○': 'circularity', '⏤': 'straightness'}
VIEW_LABEL = re.compile(r'^(FRONT|TOP|RIGHT|LEFT|BACK|BOTTOM|REAR|ISOMETRIC|ISO|SECTION [A-Z]+-[A-Z]+|DETAIL [A-Z]+|VIEW [A-Z]+)\b', re.I)


# ----------------------------------------------------------------------------- extraction
class Run:
    """a string of characters on one baseline, one font size, one rotation. Fields: text, rot (0 or 90), size, ox/oy (origin of the first character), bbox, used."""
    __slots__ = ('text', 'rot', 'size', 'ox', 'oy', 'bbox', 'used')


def _runs_from_chars(chars):
    """chars: list of dicts(c, x, y, adv, size, rot, bbox). Groups characters into runs (baseline, size, rotation) with spaces inserted for visible gaps."""
    groups = {}
    for ch in chars:
        key = (ch['rot'], round((ch['y'] if ch['rot'] in (0, 180) else ch['x']) * 2) / 2, round(ch['size'], 1))
        groups.setdefault(key, []).append(ch)
    runs = []
    for (rot, base, size), cs in groups.items():
        if rot not in (0, 90):
            continue
        cs.sort(key=lambda c: c['x'] if rot == 0 else c['y'])
        cur = [cs[0]]; parts = []
        for prev, c in zip(cs, cs[1:]):
            a0 = (prev['x'] + prev['adv']) if rot == 0 else (prev['y'] + prev['adv']); a1 = c['x'] if rot == 0 else c['y']
            if a1 - a0 > 2.0 * size: parts.append(cur); cur = [c]
            else:
                if a1 - a0 > 0.15 * size and prev['c'] != ' ' and c['c'] != ' ': cur.append(dict(c=' ', x=prev['x'], y=prev['y'], adv=0, size=size, rot=rot, bbox=prev['bbox']))
                cur.append(c)
        parts.append(cur)
        for p in parts:
            p = [c for c in p]
            while p and p[-1]['c'] == ' ': p.pop()
            while p and p[0]['c'] == ' ': p.pop(0)
            if not p: continue
            r = Run.__new__(Run); r.rot, r.size = rot, size; r.text = ''.join(c['c'] for c in p)
            r.ox, r.oy = p[0]['x'], p[0]['y']
            r.bbox = [min(c['bbox'][0] for c in p), min(c['bbox'][1] for c in p), max(c['bbox'][2] for c in p), max(c['bbox'][3] for c in p)]
            r.used = False; runs.append(r)
    return runs


def _bez(p0, p1, p2, p3, n=10):
    t = np.linspace(0, 1, n)[:, None]
    return ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * t * t * p2 + (t ** 3) * p3


def _fit_circle(P):
    """algebraic circle fit; returns (cx, cy, r, max residual) or None"""
    P = np.asarray(P, float)
    if len(P) < 3: return None
    A = np.c_[2 * P[:, 0], 2 * P[:, 1], np.ones(len(P))]; b = (P ** 2).sum(1)
    try: sol = np.linalg.lstsq(A, b, rcond=None)[0]
    except np.linalg.LinAlgError: return None
    cx, cy = sol[0], sol[1]; r2 = sol[2] + cx * cx + cy * cy
    if r2 <= 0: return None
    r = math.sqrt(r2); res = np.abs(np.hypot(P[:, 0] - cx, P[:, 1] - cy) - r).max()
    return cx, cy, r, res


class Geo:
    def __init__(s):
        s.segs = []     # [x0, y0, x1, y1, width]
        s.arcs = []     # [cx, cy, r, bbox]
        s.tris = []     # filled triangles: 3 points
        s.boxes = []    # axis-aligned rectangles [x0, y0, x1, y1]
        s.claimed = set()


def _add_path(geo, ops, fill, stroke, width):
    cur = None; start = None; poly = []; curve_pts = []; polys = []
    def flush_curves():
        nonlocal curve_pts
        if curve_pts:
            pts = np.vstack(curve_pts)
            fit = _fit_circle(pts[:: max(1, len(pts) // 24)])
            if fit and fit[2] > 0.5 and fit[3] < 0.02 * fit[2] + 0.05:
                geo.arcs.append([fit[0], fit[1], fit[2], [float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())]])
            else:
                for a, b in zip(pts, pts[1:]): geo.segs.append([a[0], a[1], b[0], b[1], width])
        curve_pts = []
    for op in ops:
        k = op[0]
        if k == 'm': flush_curves(); cur = np.array(op[1], float); start = cur; poly = [cur]
        elif k == 'l':
            flush_curves(); nxt = np.array(op[1], float); poly.append(nxt)
            if stroke: geo.segs.append([cur[0], cur[1], nxt[0], nxt[1], width])
            cur = nxt
        elif k == 'c':
            seg = _bez(cur, np.array(op[1], float), np.array(op[2], float), np.array(op[3], float))
            curve_pts.append(seg if not curve_pts else seg[1:]); cur = np.array(op[3], float)
        elif k == 'h':
            flush_curves()
            if cur is not None and start is not None and np.linalg.norm(cur - start) > 1e-6:
                if stroke: geo.segs.append([cur[0], cur[1], start[0], start[1], width])
                poly.append(start)
            cur = start
    flush_curves()
    pts = []
    for p in poly:
        if not pts or np.linalg.norm(p - pts[-1]) > 1e-6: pts.append(p)
    if len(pts) > 1 and np.linalg.norm(pts[0] - pts[-1]) < 1e-6: pts.pop()
    if fill and len(pts) == 3:
        geo.tris.append([tuple(p) for p in pts])
    if stroke and len(pts) == 4:
        xs = sorted({round(p[0], 2) for p in pts}); ys = sorted({round(p[1], 2) for p in pts})
        if len(xs) == 2 and len(ys) == 2: geo.boxes.append([xs[0], ys[0], xs[1], ys[1]])


def _walk(obj, chars, geo):
    from pdfminer.layout import LTChar, LTLine, LTRect, LTCurve
    if isinstance(obj, LTChar):
        a, b, _, _, e, f = obj.matrix; rot = int(round(math.degrees(math.atan2(b, a)))) % 360
        size = obj.height if rot in (0, 180) else obj.width
        chars.append(dict(c=obj.get_text(), x=e, y=f, adv=obj.adv, size=size, rot=rot, bbox=list(obj.bbox)))
        return
    if isinstance(obj, (LTLine, LTRect, LTCurve)):
        ops = getattr(obj, 'original_path', None)
        if ops: _add_path(geo, ops, bool(obj.fill), bool(obj.stroke), float(obj.linewidth or 0))
        return
    try: kids = list(obj)
    except TypeError: return
    for k in kids: _walk(k, chars, geo)


# ----------------------------------------------------------------------------- helpers
def _dist_pt_seg(p, a, b):
    a = np.asarray(a, float); b = np.asarray(b, float); p = np.asarray(p, float); ab = b - a; L = float(ab @ ab)
    t = 0.0 if L < 1e-12 else max(0.0, min(1.0, float((p - a) @ ab) / L))
    return float(np.linalg.norm(p - (a + t * ab)))


def _bbox_gap(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0.0); dy = max(a[1] - b[3], b[1] - a[1], 0.0); return math.hypot(dx, dy)


def _inside(inner, outer, m=1.0): return inner[0] >= outer[0] - m and inner[1] >= outer[1] - m and inner[2] <= outer[2] + m and inner[3] <= outer[3] + m


def parse_dim_text(text):
    """-> dict(kind, value, count, suffix, tol(upper, lower or None), basic, reference) or None when the text is not a dimension value."""
    t = text.strip(); basic = reference = False
    m = re.fullmatch(r'\((.+)\)', t)
    if m: t = m.group(1).strip(); reference = True
    m = re.fullmatch(r'\[(.+)\]', t)
    if m: t = m.group(1).strip(); basic = True
    m = re.match(rf'^(?:(\d+)\s*[xX×]\s*)?([Øø∅⌀]|R)?\s*({NUM})\s*(°)?\s*(?:(±|\+/-|\+-)\s*({NUM}))?\s*(.*)$', t)
    if not m: return None
    cnt, sym, val, deg, pm, tolv, rest = m.groups()
    kind = 'diameter' if sym and sym != 'R' else 'radius' if sym == 'R' else 'angle' if deg else 'linear'
    suffix = [w for w in re.split(r'\s+', rest.strip()) if w]
    count = int(cnt) if cnt else None
    for i, w in enumerate(suffix):
        if w.upper() in ('PLCS', 'PLACES') and i > 0 and suffix[i - 1].isdigit(): count = int(suffix[i - 1]); suffix = suffix[:i - 1] + suffix[i + 1:]; break
    tol = None
    if pm: tol = (float(tolv), -float(tolv))
    return dict(kind=kind, value=float(val), count=count, suffix=suffix, tol=tol, basic=basic, reference=reference)


def _num(s):
    m = re.fullmatch(rf'\s*([+\-−]?)\s*({NUM})\s*', s)
    if not m: return None
    return (-1 if m.group(1) in ('-', '−') else 1) * float(m.group(2)), bool(m.group(1))


# ----------------------------------------------------------------------------- the reader
def read_pdf(path):
    from pdfminer.high_level import extract_pages
    drawing = Drawing('pdf-vector')
    for pi, page in enumerate(extract_pages(path), 1):
        chars = []; geo = Geo()
        _walk(page, chars, geo)
        sheet = Sheet(index=pi, size_pt=[round(page.width, 2), round(page.height, 2)])
        runs = _runs_from_chars(chars)
        if not runs: sheet.warnings.append('no text on this page (raster or outlined text: needs OCR)')
        _read_sheet(sheet, runs, geo)
        drawing.sheets.append(sheet)
    return drawing


def _read_sheet(sh, runs, geo):
    W, H = sh.size_pt
    # --- scale
    for r in runs:
        m = re.search(rf'SCALE\s*[:~=]?\s*({NUM})\s*:\s*({NUM})', r.text, re.I)
        if m: sh.scale = float(m.group(1)) / float(m.group(2)); sh.scale_text = f'{m.group(1)}:{m.group(2)}'; break
    if sh.scale is None:
        for r in runs:
            if re.fullmatch(r'SCALE', r.text.strip(), re.I):
                near = sorted((q for q in runs if re.fullmatch(rf'{NUM}\s*:\s*{NUM}', q.text.strip())), key=lambda q: math.hypot(q.ox - r.ox, q.oy - r.oy))
                if near and math.hypot(near[0].ox - r.ox, near[0].oy - r.oy) < 120:
                    a, b = re.split(r'\s*:\s*', near[0].text.strip()); sh.scale = float(a) / float(b); sh.scale_text = f'{a}:{b}'; break
    if sh.scale is None: sh.warnings.append('no sheet scale found: drawn values are not computed')
    mmpp = MM_PER_PT
    # --- title block region: the smallest rectangle that encloses a title-block keyword, else none
    tb = None
    kw = [r for r in runs if re.search(r'\b(SCALE|MATERIAL|PART NO|DRAWN|SHEET|UNLESS OTHERWISE)\b', r.text, re.I)]
    cand = [b for b in geo.boxes if (b[2] - b[0]) * (b[3] - b[1]) < 0.35 * W * H and any(_inside(r.bbox, b, 1) for r in kw)]
    if cand: tb = min(cand, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    border = [b for b in geo.boxes if (b[2] - b[0]) * (b[3] - b[1]) >= 0.35 * W * H]
    def in_tb(b): return tb is not None and _inside(b, tb, 1.0)
    segs = [sg for sg in geo.segs if not in_tb([min(sg[0], sg[2]), min(sg[1], sg[3]), max(sg[0], sg[2]), max(sg[1], sg[3])])]
    claimed = [False] * len(segs)
    def claim_seg(pred):
        for i, sg in enumerate(segs):
            if not claimed[i] and pred(sg): claimed[i] = True
    def on_line(sg, a, b, tol=0.6):
        return _dist_pt_seg((sg[0], sg[1]), a, b) < tol and _dist_pt_seg((sg[2], sg[3]), a, b) < tol
    def box_edges(b): return (((b[0], b[1]), (b[2], b[1])), ((b[0], b[3]), (b[2], b[3])), ((b[0], b[1]), (b[0], b[3])), ((b[2], b[1]), (b[2], b[3])))
    def claim_box(b):
        for i, sg in enumerate(segs):
            if any(on_line(sg, p, q) for p, q in box_edges(b)): claimed[i] = True
    for b in border: claim_box(b)                  # the sheet border is not object linework
    # --- datum feature symbols: a small square holding one capital letter, with a triangle joined to it by a short line
    boxes_used = set(); consumed_tris = set(); datums = []
    letters = [r for r in runs if re.fullmatch(r'[A-Z]', r.text.strip())]
    for bi, b in enumerate(geo.boxes):
        w, h = b[2] - b[0], b[3] - b[1]
        if not (8 < w < 40 and abs(w - h) < 0.25 * max(w, h)) or b in border or in_tb(b): continue
        inside = [r for r in letters if _inside(r.bbox, b, 0.5) and not r.used]
        if len(inside) != 1: continue
        r = inside[0]; r.used = True; boxes_used.add(bi)
        datums.append(Datum(id='', letter=r.text.strip(), pos=[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]))
        for ti, t in enumerate(geo.tris):         # the datum triangle: its base centre is joined to the box by a line
            cx = sum(p[0] for p in t) / 3; cy = sum(p[1] for p in t) / 3
            if _bbox_gap([cx, cy, cx, cy], [b[0] - 30, b[1] - 30, b[2] + 30, b[3] + 30]) == 0 and any(_bbox_gap([sg[0], sg[1], sg[0], sg[1]], b) < 0.8 or _bbox_gap([sg[2], sg[3], sg[2], sg[3]], b) < 0.8 for sg in segs if _dist_pt_seg((cx, cy), (sg[0], sg[1]), (sg[2], sg[3])) < 4):
                consumed_tris.add(ti)
                for i, sg in enumerate(segs):
                    if _dist_pt_seg((cx, cy), (sg[0], sg[1]), (sg[2], sg[3])) < 4 and not claimed[i]: claimed[i] = True
        claim_box(b)
    # --- GD&T frames: a wide short rectangle cut into cells by vertical lines, each cell holding text
    frames = []
    for bi, b in enumerate(geo.boxes):
        w, h = b[2] - b[0], b[3] - b[1]
        if bi in boxes_used or b in border or in_tb(b) or not (w > 2.2 * h and 8 < h < 40): continue
        divs = sorted({round(sg[0], 1) for sg in segs if abs(sg[0] - sg[2]) < 0.3 and b[0] + 1 < sg[0] < b[2] - 1 and abs(min(sg[1], sg[3]) - b[1]) < 1 and abs(max(sg[1], sg[3]) - b[3]) < 1})
        if not divs: continue
        edges = [b[0]] + divs + [b[2]]; cells = []
        inside = [r for r in runs if _inside(r.bbox, b, 0.5) and r.rot == 0 and not r.used]
        for lo, hi in zip(edges, edges[1:]):
            cs = sorted((r for r in inside if lo - 0.5 <= (r.bbox[0] + r.bbox[2]) / 2 <= hi + 0.5), key=lambda r: r.ox)
            for r in cs: r.used = True
            cells.append(' '.join(r.text for r in cs))
        if not any(cells): continue
        boxes_used.add(bi)
        fr = GdtFrame(id='', pos=[b[0], b[1], b[2], b[3]], cells=cells)
        c0 = cells[0].strip(); fr.symbol = GDT_SYMBOLS.get(c0[:1]) if c0 else None
        if len(cells) > 1:
            m = re.match(rf'^\s*([Ø∅⌀])?\s*({NUM})\s*(.*)$', cells[1])
            if m:
                fr.diameter_zone = bool(m.group(1)); fr.value = float(m.group(2)); fr.modifiers = re.findall(r'\(?\b([MLSPF])\b\)?', m.group(3))
        for c in cells[2:]:
            fr.datums += re.findall(r'\b([A-Z])\b', re.sub(r'\([MLSPF]\)', '', c))
        frames.append(fr)
        claim_box(b)
        for i, sg in enumerate(segs):
            if any(abs(sg[0] - d) < 0.5 and abs(sg[2] - d) < 0.5 and sg[1] >= b[1] - 1 and sg[3] <= b[3] + 1 for d in divs): claimed[i] = True
    # --- arrows
    arrows = []
    for ti, t in enumerate(geo.tris):
        if ti in consumed_tris: continue
        P = [np.array(p) for p in t]; sides = [(np.linalg.norm(P[(i + 1) % 3] - P[(i + 2) % 3]), i) for i in range(3)]; sides.sort()
        base_len, ia = sides[0]; tip = P[ia]; bm = (P[(ia + 1) % 3] + P[(ia + 2) % 3]) / 2; ln = float(np.linalg.norm(bm - tip))
        if not (3.5 < ln < 30 and ln / max(base_len, 1e-6) > 1.3): continue
        arrows.append(dict(tip=tip, u=(bm - tip) / ln, base=bm, length=ln, used=False))
    # --- paired arrows on a dimension line
    dims = []
    cand = []
    for i, a in enumerate(arrows):
        for j in range(i + 1, len(arrows)):
            b = arrows[j]; v = b['tip'] - a['tip']; L = float(np.linalg.norm(v))
            if L < 2.0: continue
            d = v / L; ca = float(a['u'] @ d); cb = float(b['u'] @ d)
            if abs(abs(ca) - 1) > 0.02 or abs(abs(cb) - 1) > 0.02 or ca * cb > 0: continue       # both along the line, one each way
            if not any(on_line(sg, a['tip'], b['tip'], 0.6) or (_dist_pt_seg(a['base'], (sg[0], sg[1]), (sg[2], sg[3])) < 1.2 and abs((np.array([sg[2] - sg[0], sg[3] - sg[1]]) / (math.hypot(sg[2] - sg[0], sg[3] - sg[1]) + 1e-9)) @ d) > 0.99) for sg in segs):
                continue
            cand.append((L, i, j))
    cand.sort()
    pairs = []
    for L, i, j in cand:
        if arrows[i]['used'] or arrows[j]['used']: continue
        arrows[i]['used'] = arrows[j]['used'] = True; pairs.append((i, j))
    for i, j in pairs:
        a, b = arrows[i], arrows[j]; d = (b['tip'] - a['tip']); L = float(np.linalg.norm(d)); d = d / L
        dm = Dimension(id='', kind='linear', text='', style='paired', tips=[[float(a['tip'][0]), float(a['tip'][1])], [float(b['tip'][0]), float(b['tip'][1])]])
        n = np.array([-d[1], d[0]])
        for k, sg in enumerate(segs):
            if claimed[k]: continue
            v = np.array([sg[2] - sg[0], sg[3] - sg[1]]); lv = float(np.linalg.norm(v))
            if lv < 1e-6: continue
            if on_line(sg, a['tip'], b['tip'], 0.6) or (abs(float((v / lv) @ d)) > 0.999 and _dist_pt_seg(a['base'], (sg[0], sg[1]), (sg[2], sg[3])) < 1.5) or (abs(float((v / lv) @ d)) > 0.999 and _dist_pt_seg(b['base'], (sg[0], sg[1]), (sg[2], sg[3])) < 1.5):
                claimed[k] = True; continue
            if abs(float((v / lv) @ d)) < 0.02:      # perpendicular to the dimension line: an extension line if it passes through a tip
                for tip in (a['tip'], b['tip']):
                    if _dist_pt_seg(tip, (sg[0], sg[1]), (sg[2], sg[3])) < 0.8:
                        claimed[k] = True; far = (sg[0], sg[1]) if np.linalg.norm(np.array(sg[:2]) - tip) > np.linalg.norm(np.array(sg[2:4]) - tip) else (sg[2], sg[3]); near = (sg[2], sg[3]) if far == (sg[0], sg[1]) else (sg[0], sg[1])
                        # the end beyond the tip is the "overshoot", the other end sits next to the feature
                        dm.ext.append([float(far[0]), float(far[1]), float(near[0]), float(near[1])]) if np.linalg.norm(np.array(far) - tip) > np.linalg.norm(np.array(near) - tip) else None
                        break
        # extension lines were recorded as [feature_end, overshoot_end]: feature end is the one FARTHER from the tip along the line, overshoot is past the dimension line
        dm._line = (a['tip'], b['tip']); dm._d = d; dm._len = L
        dims.append(dm)
    # --- leaders (single arrows)
    for a in arrows:
        if a['used']: continue
        a['used'] = True
        dm = Dimension(id='', kind='linear', text='', style='leader', tips=[[float(a['tip'][0]), float(a['tip'][1])]])
        end = None
        for k, sg in enumerate(segs):
            if claimed[k]: continue
            for p, q in (((sg[0], sg[1]), (sg[2], sg[3])), ((sg[2], sg[3]), (sg[0], sg[1]))):
                if np.linalg.norm(np.array(p) - a['base']) < 1.5:
                    v = np.array(q) - np.array(p); lv = np.linalg.norm(v)
                    if lv > 1e-6 and float((v / lv) @ a['u']) > 0.98: claimed[k] = True; end = np.array(q); break
            if end is not None: break
        shoulder = end
        if end is not None:                       # a horizontal shoulder after the elbow
            for k, sg in enumerate(segs):
                if claimed[k]: continue
                for p, q in (((sg[0], sg[1]), (sg[2], sg[3])), ((sg[2], sg[3]), (sg[0], sg[1]))):
                    if np.linalg.norm(np.array(p) - end) < 1.5 and abs(q[1] - p[1]) < 0.5: claimed[k] = True; shoulder = np.array(q); break
        dm._anchor = shoulder if shoulder is not None else a['tip']; dm._line = None
        dims.append(dm)
    # --- text association and parsing
    free = [r for r in runs if not r.used]
    def near_runs(dm):
        if dm.style == 'paired':
            a, b = dm._line; ref = lambda r: _dist_pt_seg(((r.bbox[0] + r.bbox[2]) / 2, (r.bbox[1] + r.bbox[3]) / 2), a, b)
        else:
            ref = lambda r: _bbox_gap([dm._anchor[0], dm._anchor[1], dm._anchor[0], dm._anchor[1]], r.bbox)
        return sorted((r for r in free if not r.used), key=ref), ref
    for dm in dims:
        rs, ref = near_runs(dm)
        main = None; stack = []
        # limit dimensions: two plain numbers of the same size, one above the other, no nominal value
        nums = [r for r in rs if ref(r) <= 3.2 * r.size and _num(r.text) and not _num(r.text)[1] and r.rot in (0, 90)]
        for r in nums:
            for q in nums:
                if r is not q and abs(r.size - q.size) < 0.2 and (abs(r.bbox[0] - q.bbox[0]) < 2 * r.size if r.rot == 0 else abs(r.bbox[1] - q.bbox[1]) < 2 * r.size) and (0.3 * r.size < abs(r.oy - q.oy) < 2.6 * r.size if r.rot == 0 else 0.3 * r.size < abs(r.ox - q.ox) < 2.6 * r.size):
                    stack = [r, q]; break
            if stack: break
        if not stack:
            for r in rs:
                if ref(r) > (3.2 * r.size if dm.style == 'paired' else 2.0 * r.size + 6): break
                if parse_dim_text(r.text): main = r; break
        if main is None:
            if stack:
                hi, lo = (stack if (stack[0].oy > stack[1].oy if stack[0].rot == 0 else stack[0].ox < stack[1].ox) else stack[::-1])
                dm.tol = Tol('limits', _num(hi.text)[0], _num(lo.text)[0]); dm.value = round((dm.tol.upper + dm.tol.lower) / 2, 6); dm.text = f'{hi.text} / {lo.text}'
                dm.text_pos = [hi.ox, hi.oy]; dm.text_rot = hi.rot; hi.used = lo.used = True
                continue
            dm.conf = 'partial'; dm.style = dm.style or 'text-only'; continue
        p = parse_dim_text(main.text); main.used = True
        dm.kind, dm.value, dm.count, dm.suffix = p['kind'], p['value'], p['count'], p['suffix']; dm.text = main.text; dm.text_pos = [main.ox, main.oy]; dm.text_rot = main.rot
        dm.tol = Tol('reference') if p['reference'] else Tol('basic') if p['basic'] else Tol('none')
        if p['tol']: dm.tol = Tol('bilateral', p['tol'][0], p['tol'][1])
        # basic dimension: the text sits in a box
        for k, b in enumerate(geo.boxes):
            if k not in boxes_used and b not in border and _inside(main.bbox, b, 2.5) and not _inside(b, main.bbox, 0) and (b[2] - b[0]) < 6 * main.size:
                dm.tol = Tol('basic'); claim_box(b)
        # stacked tolerance values next to the main text: smaller type, beside it, one above the other
        def beside(r):
            if r.used or r.size > 0.95 * main.size or not _num(r.text): return False
            if main.rot == 0: return -0.2 * main.size < r.ox - main.bbox[2] < 2.5 * main.size and abs(r.oy - main.oy) < 2.2 * main.size
            return -0.2 * main.size < r.oy - main.bbox[3] < 2.5 * main.size and abs(r.ox - main.ox) < 2.6 * main.size
        tolruns = [r for r in free if beside(r)]
        if tolruns and not p['tol']:
            key = (lambda r: -r.oy) if main.rot == 0 else (lambda r: r.ox)        # "above" first
            tolruns.sort(key=key); vals = []
            for r in tolruns[:2]:
                n = _num(r.text); r.used = True; vals.append(n[0]); dm.text += ' ' + r.text
            if len(vals) == 2: dm.tol = Tol('bilateral', vals[0], vals[1])
            elif len(vals) == 1: dm.tol = Tol('bilateral', vals[0], None)
    # --- measure from the drawn geometry
    for dm in dims:
        dm.unit = sh.unit
        if sh.scale is None or dm.value is None: continue
        if dm.style == 'paired' and dm.kind in ('linear', 'diameter'):
            L = float(np.linalg.norm(np.array(dm.tips[0]) - np.array(dm.tips[1]))); dm.drawn = round(L * mmpp / sh.scale, 4)
        elif dm.tips and dm.kind in ('diameter', 'radius'):
            best = None
            for cx, cy, r, bb in geo.arcs:
                e = abs(math.hypot(dm.tips[0][0] - cx, dm.tips[0][1] - cy) - r)
                if e < 0.6 and (best is None or e < best[0]): best = (e, cx, cy, r)
            if best:
                dm.arc = [best[1], best[2], best[3]]; dm.drawn = round((2 * best[3] if dm.kind == 'diameter' else best[3]) * mmpp / sh.scale, 4)
        if dm.drawn is not None: dm.drawn_dev = round(dm.drawn - dm.value, 4)
    # --- views: object linework not used by dimensions, datums, frames or the title block, clustered by proximity
    obj = [[min(sg[0], sg[2]), min(sg[1], sg[3]), max(sg[0], sg[2]), max(sg[1], sg[3])] for k, sg in enumerate(segs) if not claimed[k]]
    obj += [list(a[3]) for a in geo.arcs if not in_tb(a[3])]
    obj = [b for b in obj if not any(b == c for c in border)]
    gap = 0.015 * W
    parent = list(range(len(obj)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i in range(len(obj)):
        for j in range(i + 1, len(obj)):
            if _bbox_gap(obj[i], obj[j]) <= gap: parent[find(i)] = find(j)
    cl = {}
    for i, b in enumerate(obj): cl.setdefault(find(i), []).append(b)
    views = []
    for members in cl.values():
        if len(members) < 3: continue
        bb = [min(b[0] for b in members), min(b[1] for b in members), max(b[2] for b in members), max(b[3] for b in members)]
        if (bb[2] - bb[0]) < 0.02 * W and (bb[3] - bb[1]) < 0.02 * W: continue
        # a table (BOM, revision block): ruled lines only, with many text runs inside
        if len(members) >= 4 and sum(1 for r in runs if _inside(r.bbox, bb, 1.0)) >= 6 and all((b[2] - b[0]) < 0.5 or (b[3] - b[1]) < 0.5 for b in members): continue
        views.append(View(id='', bbox=[round(x, 2) for x in bb], n_segments=len(members), scale=sh.scale))
    views.sort(key=lambda v: (-v.bbox[3], v.bbox[0]))
    for i, v in enumerate(views, 1): v.id = f'V{i}'
    # --- notes, labels
    rest = [r for r in runs if not r.used]
    for v in views:
        best = None
        for r in rest:
            if VIEW_LABEL.match(r.text.strip()) and v.bbox[0] - 30 <= (r.bbox[0] + r.bbox[2]) / 2 <= v.bbox[2] + 30 and -60 < v.bbox[1] - r.bbox[3] < 30:
                d = abs(v.bbox[1] - r.bbox[3])
                if best is None or d < best[0]: best = (d, r)
        if best: v.label = best[1].text.strip()
    ni = 0
    for r in sorted(rest, key=lambda r: (-r.oy, r.ox)):
        ni += 1
        kind = 'title_block' if tb is not None and _inside(r.bbox, tb, 1) else 'view_label' if any(v.label == r.text.strip() for v in views) else 'note'
        sh.notes.append(Note(id=f'N{ni}', text=r.text, pos=[round(r.ox, 2), round(r.oy, 2)], size=round(r.size, 2), kind=kind)); r.used = True
    # --- assign dimensions to views, order, ids
    for dm in dims:
        pts = [tuple(t) for t in dm.tips] + [(e[0], e[1]) for e in dm.ext]
        best = None
        for v in views:
            g = min(_bbox_gap([p[0], p[1], p[0], p[1]], v.bbox) for p in pts) if pts else 1e9
            if best is None or g < best[0]: best = (g, v)
        if best and best[0] < 0.2 * W: dm.view = best[1].id
    dims.sort(key=lambda d: (d.view or 'ZZ', -(d.text_pos[1] if d.text_pos else 0), d.text_pos[0] if d.text_pos else 0))
    for i, dm in enumerate(dims, 1): dm.id = f'D{i}'
    for k in ('_line', '_d', '_len', '_anchor'):
        for dm in dims:
            if hasattr(dm, k): delattr(dm, k)
    sh.dimensions = dims; sh.views = views
    for i, d in enumerate(sorted(datums, key=lambda d: (-d.pos[1], d.pos[0])), 1): d.id = f'T{i}'; sh.datums.append(d)
    for i, f in enumerate(sorted(frames, key=lambda f: (-f.pos[3], f.pos[0])), 1):
        f.id = f'G{i}'
        best = None
        for dm in dims:
            anchors = ([dm.text_pos] if dm.text_pos else []) + [list(t) for t in dm.tips]
            for p in anchors:
                g = _bbox_gap([p[0], p[1], p[0], p[1]], f.pos)
                if best is None or g < best[0]: best = (g, dm.id)
        if best and best[0] < 0.1 * W: f.attached_to = best[1]
        sh.gdt.append(f)
    if not dims: sh.warnings.append('no dimensions found')


def main():
    import json
    if len(sys.argv) < 2: raise SystemExit(__doc__)
    d = read_pdf(sys.argv[1])
    if '--json' in sys.argv: print(json.dumps(d.to_dict(), indent=1, ensure_ascii=False)); return
    for s in d.sheets:
        print(f'sheet {s.index}: {s.size_pt} scale {s.scale_text} views {len(s.views)} dims {len(s.dimensions)} datums {len(s.datums)} gdt {len(s.gdt)} notes {len(s.notes)}', s.warnings)
        for dm in s.dimensions:
            print(f'  {dm.id} {dm.kind:8s} {dm.text!r:28s} value {dm.value} tol {dm.tol.kind} {dm.tol.upper}/{dm.tol.lower} count {dm.count} drawn {dm.drawn} view {dm.view} {dm.conf}')


if __name__ == '__main__':
    main()
