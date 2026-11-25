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
            a0 = prev['bbox'][2] if rot == 0 else prev['bbox'][3]; a1 = c['bbox'][0] if rot == 0 else c['bbox'][1]    # glyph extents (pdfminer's `adv` is in em units, not points)
            if a1 - a0 > 3.5 * size: parts.append(cur); cur = [c]
            else:
                if a1 - a0 > 0.3 * size and prev['c'] != ' ' and c['c'] != ' ': cur.append(dict(c=' ', x=prev['x'], y=prev['y'], adv=0, size=size, rot=rot, bbox=prev['bbox']))
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
        s.glyphs = []   # bounding boxes of small filled curve shapes (outlined text, parentheses)
        s.claimed = set()


def _add_path(geo, ops, fill, stroke, width):
    cur = None; start = None; poly = []; curve_pts = []; polys = []
    if stroke and not fill and len(ops) >= 9 and ops[0][0] == 'm' and all(o[0] == 'l' for o in ops[1:]):       # a shallow many-segment bow: a parenthesis drawn as a polyline
        xs = [o[1][0] for o in ops]; ys = [o[1][1] for o in ops]; w_, h_ = max(xs) - min(xs), max(ys) - min(ys)
        if 8 < max(w_, h_) < 60 and 1.0 < min(w_, h_) < 0.3 * max(w_, h_): geo.glyphs.append([min(xs), min(ys), max(xs), max(ys)])
    if fill and not stroke and any(o[0] == 'c' for o in ops):
        xs = [pt[0] for o in ops for pt in o[1:] if isinstance(pt, tuple)]; ys = [pt[1] for o in ops for pt in o[1:] if isinstance(pt, tuple)]
        if xs and max(xs) - min(xs) < 60 and max(ys) - min(ys) < 60: geo.glyphs.append([min(xs), min(ys), max(xs), max(ys)]); return
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


_CID = re.compile(r'^\(cid:(\d+)\)$')


def _decode_cid(t):
    """A font that is not embedded and has no ToUnicode map gives pdfminer `(cid:N)`; for the usual TrueType fonts (Arial, Times New Roman, Century Gothic ...) the code is the glyph index, which for printable ASCII is the
    character code minus 29 (glyph 3 is the space). Other glyphs (symbols such as the diameter sign) are left as `(cid:N)` so the caller can see they were not decoded."""
    m = _CID.match(t)
    if m and 3 <= int(m.group(1)) <= 97: return chr(int(m.group(1)) + 29)
    return t


def _walk(obj, chars, geo):
    from pdfminer.layout import LTChar, LTLine, LTRect, LTCurve
    if isinstance(obj, LTChar):
        a, b, _, _, e, f = obj.matrix; rot = int(round(math.degrees(math.atan2(b, a)))) % 360
        size = obj.height if rot in (0, 180) else obj.width
        txt = obj.get_text(); chars.append(dict(c=_decode_cid(txt), dec=txt != _decode_cid(txt), x=e, y=f, adv=obj.adv, size=size, rot=rot, bbox=list(obj.bbox)))
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


SUFFIX_WORDS = {'THRU', 'TYP', 'TYPICAL', 'PLCS', 'PLACES', 'PL', 'EQ', 'EQL', 'SP', 'SPACES', 'REF', 'MAX', 'MIN', 'DEEP', 'DP', 'DIA', 'X', 'EA', 'BSC', 'CSK', 'CBORE', 'C\'BORE', 'DRILL', 'TAP', 'UNC', 'UNF', 'NPT', 'GA', 'AF', 'ALL', 'AROUND', 'OD', 'ID', 'SQ', 'EACH', 'PLACE', 'SIDE', 'SIDES', 'BOTH', 'OPP'}


def parse_dim_text(text):
    """-> dict(kind, value, count, suffix, tol(upper, lower or None), basic, reference) or None when the text is not a dimension value."""
    t = text.strip(); basic = reference = False
    for ch, frac in (('\u00bd', ' 1/2'), ('\u00bc', ' 1/4'), ('\u00be', ' 3/4')): t = t.replace(ch, frac)
    pre = re.match(r'^\((\d+)\)\s+(\S.*)$', t)                 # "(2) 1 1/8" : the instance count in parentheses
    count = None
    if pre: count = int(pre.group(1)); t = pre.group(2)
    elif t.startswith('(') or t.endswith(')'): t = t.strip('() ').strip(); reference = True
    t = re.sub(r'\s*["\u201d\u2033]\s*$', '', t) if re.search(r'\d\s*["\u201d\u2033]\s*$', t) else t
    m = re.fullmatch(r'\[(.+)\]', t)
    if m: t = m.group(1).strip(); basic = True
    m = re.match(rf'^(?:(\d+)\s*[xX\u00d7]\s*)?([\u00d8\u00f8\u2205\u2300]|R)?\s*({NUM})\s*(\u00b0)?\s*(?:(\u00b1|\+/-|\+-)\s*({NUM}))?\s*(.*)$', t)
    if not m: return None
    cnt, sym, val, deg, pm, tolv, rest = m.groups()
    frac = re.match(r'^(\d+)\s*/\s*(\d+)\s*(.*)$', rest)
    if frac and not deg: val = str(float(val) + int(frac.group(1)) / int(frac.group(2))); rest = frac.group(3)
    kind = 'diameter' if sym and sym != 'R' else 'radius' if sym == 'R' else 'angle' if deg else 'linear'
    suffix = [w for w in re.split(r'\s+', rest.strip()) if w]
    if cnt: count = int(cnt)
    for i, w in enumerate(suffix):
        if w.upper() in ('PLCS', 'PLACES') and i > 0 and suffix[i - 1].isdigit(): count = int(suffix[i - 1]); suffix = suffix[:i - 1] + suffix[i + 1:]; break
    if any(w.upper().strip('.,') not in SUFFIX_WORDS and not re.fullmatch(r'\d+[\u00b0"]|[\u00b0"]|\d+/\d+', w) for w in suffix): return None    # other words or numbers: a note, a part number, an item number
    tol = None
    if pm: tol = (float(tolv), -float(tolv))
    return dict(kind=kind, value=float(val), count=count, suffix=suffix, tol=tol, basic=basic, reference=reference)


def _num(s):
    m = re.fullmatch(rf'\s*([+\-−]?)\s*({NUM})\s*', s)
    if not m: return None
    return (-1 if m.group(1) in ('-', '−') else 1) * float(m.group(2)), bool(m.group(1))


# ----------------------------------------------------------------------------- the reader
def read_pdf(path, pages=None, keep_lines=False):
    """pages: optional list of 1-based page numbers (a 150-page package is slow to read whole)."""
    from pdfminer.high_level import extract_pages
    drawing = Drawing('pdf-vector')
    nums = [n - 1 for n in pages] if pages else None
    for pi, page in zip(pages or iter(int, 1) if False else (pages or range(1, 10 ** 6)), extract_pages(path, page_numbers=nums)):
        chars = []; geo = Geo()
        _walk(page, chars, geo)
        sheet = Sheet(index=pi, size_pt=[round(page.width, 2), round(page.height, 2)])
        runs = _runs_from_chars(chars); ndec = sum(1 for c in chars if c.get('dec')); nbad = sum(1 for c in chars if c['c'].startswith('(cid:'))
        if ndec: sheet.warnings.append(f'text decoded from glyph indices ({ndec} characters; the font is not embedded)')
        if nbad: sheet.warnings.append(f'{nbad} characters could not be decoded (symbols of a font that is not embedded)')
        if not runs: sheet.warnings.append('no text on this page (raster or outlined text: needs OCR)')
        _read_sheet(sheet, runs, geo, keep_lines=keep_lines)
        drawing.sheets.append(sheet)
    return drawing


def _pt_seg_dist(P, a, b):
    """distances from many points P (n, 2) to the segment a-b"""
    a = np.asarray(a, float); b = np.asarray(b, float); ab = b - a; L = float(ab @ ab)
    t = np.clip(((P - a) @ ab) / max(L, 1e-12), 0, 1)
    return np.hypot(*(P - (a + t[:, None] * ab)).T)


def _rects_from_segs(S):
    """axis-aligned rectangles drawn as four separate line segments (the usual form in CAD PDF exports): two horizontals with the same extent and verticals joining them"""
    dx = np.abs(S[:, 2] - S[:, 0]); dy = np.abs(S[:, 3] - S[:, 1])
    Hs = S[(dy < 0.3) & (dx > 3)]; Vs = S[(dx < 0.3) & (dy > 3)]
    vidx = {}
    for x, y0, y1 in zip((Vs[:, 0] + Vs[:, 2]) / 2, np.minimum(Vs[:, 1], Vs[:, 3]), np.maximum(Vs[:, 1], Vs[:, 3])): vidx.setdefault(int(round(x)), []).append((x, y0, y1))
    hidx = {}
    for y, x0, x1 in zip((Hs[:, 1] + Hs[:, 3]) / 2, np.minimum(Hs[:, 0], Hs[:, 2]), np.maximum(Hs[:, 0], Hs[:, 2])): hidx.setdefault((int(round(x0)), int(round(x1))), []).append((y, x0, x1))
    def has_v(x, lo, hi):
        return any(abs(xx - x) < 0.8 and a <= lo + 0.8 and b >= hi - 0.8 for k in (int(round(x)) - 1, int(round(x)), int(round(x)) + 1) for xx, a, b in vidx.get(k, ()))
    out = []
    for (kx0, kx1), lst in hidx.items():
        ys = sorted({round(y, 1) for y, _, _ in lst})
        if len(ys) < 2 or len(ys) > 40: continue
        x0 = min(l[1] for l in lst); x1 = max(l[2] for l in lst)
        for i in range(len(ys)):
            for j in range(i + 1, len(ys)):
                if has_v(x0, ys[i], ys[j]) and has_v(x1, ys[i], ys[j]): out.append([x0, ys[i], x1, ys[j]])
    return out


def _tf(r, x, y):
    """(x, y) in the text's own frame: u along the reading direction, v up (rot 90 text reads bottom to top)"""
    return (x, y) if r.rot == 0 else (y, -x)


def _read_fraction(main, free):
    """A fractional dimension (4 21/32") is a whole number plus two rows of digits, numerator above denominator, in the same size, laid inside or just after the whole
    number's extent. Returns (fraction value, [numerator run, denominator run]) or (None, [])."""
    def uv(r): return _tf(r, r.ox, r.oy)
    mu0, mv0 = uv(main)
    mu1 = (main.bbox[2] if main.rot == 0 else main.bbox[3]) if main.rot == 0 else main.bbox[3]
    cand = []
    for r in free:
        if r.used or r is main or not re.fullmatch(r'\d{1,3}', r.text.strip()) or r.rot != main.rot or abs(r.size - main.size) > 0.35 * main.size: continue
        u, v = uv(r)
        if mu0 - 0.5 * main.size <= u <= mu1 + 0.5 * main.size and 0.3 * main.size < abs(v - mv0) < 1.4 * main.size: cand.append((u, v - mv0, r))
    for ui, vi, ri in cand:
        for uj, vj, rj in cand:
            if ri is not rj and vi > 0 > vj and abs(ui - uj) < 0.6 * main.size and int(rj.text) > 0 and int(ri.text) < 10 * int(rj.text): return int(ri.text) / int(rj.text), [ri, rj]
    return None, []


def _read_sheet(sh, runs, geo, tol=1.0, keep_lines=False):
    """tol: geometric tolerance multiplier (1.0 for vector input; a scan's lines are a few pixels off)."""
    W, H = sh.size_pt; T = tol
    mmpp = MM_PER_PT
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
    # --- line segments as arrays
    S = np.array(geo.segs, float).reshape(-1, 5)
    S = S[np.hypot(S[:, 2] - S[:, 0], S[:, 3] - S[:, 1]) > 1e-6]
    rects = [list(b) for b in geo.boxes]
    for r in _rects_from_segs(S) if len(S) else []:
        if not any(all(abs(a - b) < 0.8 for a, b in zip(r, q)) for q in rects): rects.append(r)
    # --- title block: the smallest rectangle (not the sheet border) that encloses a title-block keyword
    border = [b for b in rects if (b[2] - b[0]) * (b[3] - b[1]) >= 0.35 * W * H]
    kw = [r for r in runs if re.search(r'\b(SCALE|MATERIAL|PART NO|DRAWN|DRWN|SHEET|UNLESS OTHERWISE|TITLE|DWG)\b', r.text, re.I)]
    cand = [b for b in rects if (b[2] - b[0]) * (b[3] - b[1]) < 0.35 * W * H and any(_inside(r.bbox, b, 1) for r in kw)]
    tb = max(cand, key=lambda b: (b[2] - b[0]) * (b[3] - b[1])) if cand else None      # the outermost small enclosing rectangle: the whole block, not one cell
    if tb is not None and sum(1 for r in kw if _inside(r.bbox, tb, 1)) < 2: tb = None
    def in_tb(b): return tb is not None and _inside(b, tb, 1.0)
    sb = np.c_[np.minimum(S[:, 0], S[:, 2]), np.minimum(S[:, 1], S[:, 3]), np.maximum(S[:, 0], S[:, 2]), np.maximum(S[:, 1], S[:, 3])] if len(S) else np.zeros((0, 4))
    if tb is not None and len(S):
        keep = ~((sb[:, 0] >= tb[0] - 1) & (sb[:, 1] >= tb[1] - 1) & (sb[:, 2] <= tb[2] + 1) & (sb[:, 3] <= tb[3] + 1))
        S, sb = S[keep], sb[keep]
    claimed = np.zeros(len(S), bool)
    def on_line(a, b, tol=0.6): return (_pt_seg_dist(S[:, 0:2], a, b) < tol * T) & (_pt_seg_dist(S[:, 2:4], a, b) < tol * T) if len(S) else np.zeros(0, bool)
    def claim_box(b):
        for p, q in (((b[0], b[1]), (b[2], b[1])), ((b[0], b[3]), (b[2], b[3])), ((b[0], b[1]), (b[0], b[3])), ((b[2], b[1]), (b[2], b[3]))): claimed[on_line(p, q)] = True
    for b in border: claim_box(b)                  # the sheet border is not object linework
    # --- datum feature symbols: a small square holding one capital letter, with a triangle joined to it by a short line
    boxes_used = set(); consumed_tris = set(); datums = []
    letters = [r for r in runs if re.fullmatch(r'[A-Z]', r.text.strip())]
    for bi, b in enumerate(rects):
        w, h = b[2] - b[0], b[3] - b[1]
        if not (8 < w < 40 and abs(w - h) < 0.25 * max(w, h)) or b in border or in_tb(b): continue
        if min(b[0], W - b[2], b[1], H - b[3]) < 0.04 * W: continue          # the sheet-size / revision boxes in the margin strip
        inside = [r for r in letters if _inside(r.bbox, b, 0.5) and not r.used]
        if len(inside) != 1: continue
        r = inside[0]; r.used = True; boxes_used.add(bi)
        datums.append(Datum(id='', letter=r.text.strip(), pos=[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]))
        for ti, t in enumerate(geo.tris):         # the datum triangle: a line joins it to the box
            cx = sum(p[0] for p in t) / 3; cy = sum(p[1] for p in t) / 3
            if abs(cx - (b[0] + b[2]) / 2) > 30 + w or abs(cy - (b[1] + b[3]) / 2) > 30 + h: continue
            join = np.zeros(len(S), bool)
            if len(S):
                near_tri = _pt_seg_dist(S[:, 0:2], (cx, cy), (cx, cy)) < 4; near_tri |= _pt_seg_dist(S[:, 2:4], (cx, cy), (cx, cy)) < 4
                to_box = np.array([_bbox_gap([a, c, a, c], b) < 0.8 for a, c in S[:, 0:2]]) | np.array([_bbox_gap([a, c, a, c], b) < 0.8 for a, c in S[:, 2:4]]) if near_tri.any() else np.zeros(len(S), bool)
                join = near_tri & to_box
            if join.any(): consumed_tris.add(ti); claimed[join] = True
        claim_box(b)
    # --- GD&T frames: a wide short rectangle cut into cells by vertical lines, each cell holding text
    frames = []
    for bi, b in enumerate(rects):
        w, h = b[2] - b[0], b[3] - b[1]
        if bi in boxes_used or b in border or in_tb(b) or not (w > 2.2 * h and 8 < h < 40) or not len(S): continue
        vm = (np.abs(S[:, 0] - S[:, 2]) < 0.3) & (S[:, 0] > b[0] + 1) & (S[:, 0] < b[2] - 1) & (np.abs(sb[:, 1] - b[1]) < 1) & (np.abs(sb[:, 3] - b[3]) < 1)
        divs = sorted({round(x, 1) for x in S[vm, 0]})
        if not divs: continue
        edges = [b[0]] + divs + [b[2]]; cells = []
        inside = [r for r in runs if _inside(r.bbox, b, 0.5) and r.rot == 0 and not r.used]
        for lo, hi in zip(edges, edges[1:]):
            cs = sorted((r for r in inside if lo - 0.5 <= (r.bbox[0] + r.bbox[2]) / 2 <= hi + 0.5), key=lambda r: r.ox)
            for r in cs: r.used = True
            cells.append(' '.join(r.text for r in cs))
        if not any(cells) or len(cells[0].strip()) > 2: continue
        boxes_used.add(bi)
        fr = GdtFrame(id='', pos=[b[0], b[1], b[2], b[3]], cells=cells)
        c0 = cells[0].strip(); fr.symbol = GDT_SYMBOLS.get(c0[:1]) if c0 else None
        if len(cells) > 1:
            m = re.match(rf'^\s*([Ø∅⌀])?\s*({NUM})\s*(.*)$', cells[1])
            if m: fr.diameter_zone = bool(m.group(1)); fr.value = float(m.group(2)); fr.modifiers = re.findall(r'\(?\b([MLSPF])\b\)?', m.group(3))
        for c in cells[2:]: fr.datums += re.findall(r'\b([A-Z])\b', re.sub(r'\([MLSPF]\)', '', c))
        if fr.value is None: boxes_used.discard(bi); continue        # not a feature control frame (a table row)
        frames.append(fr); claim_box(b); claimed[vm] = True
    # --- arrows
    arrows = []
    for ti, t in enumerate(geo.tris):
        if ti in consumed_tris: continue
        P = [np.array(p) for p in t]; sides = sorted((np.linalg.norm(P[(i + 1) % 3] - P[(i + 2) % 3]), i) for i in range(3))
        base_len, ia = sides[0]; tip = P[ia]; bm = (P[(ia + 1) % 3] + P[(ia + 2) % 3]) / 2; ln = float(np.linalg.norm(bm - tip))
        if not (3.5 < ln < 30 and ln / max(base_len, 1e-6) > 1.3): continue
        arrows.append(dict(tip=tip, u=(bm - tip) / ln, base=bm, length=ln, used=False))
    # --- paired arrows on a dimension line
    cand = []
    for i, a in enumerate(arrows):
        for j in range(i + 1, len(arrows)):
            b = arrows[j]; v = b['tip'] - a['tip']; L = float(np.linalg.norm(v))
            if L < 2.0: continue
            d = v / L; ca = float(a['u'] @ d); cb = float(b['u'] @ d)
            if abs(abs(ca) - 1) > 0.02 or abs(abs(cb) - 1) > 0.02 or ca * cb > 0: continue
            if len(S):
                m = on_line(a['tip'], b['tip'], 0.6)
                if not m.any():
                    ux = S[:, 2] - S[:, 0]; uy = S[:, 3] - S[:, 1]; ln = np.hypot(ux, uy) + 1e-9
                    m = (np.abs((ux * d[0] + uy * d[1]) / ln) > 0.99) & (_pt_seg_dist(S[:, 0:2], a['base'], a['base']) < 1.5 * T)
                if not m.any(): continue
            else: continue
            cand.append((L, i, j))
    cand.sort(); pairs = []
    for L, i, j in cand:
        if arrows[i]['used'] or arrows[j]['used']: continue
        arrows[i]['used'] = arrows[j]['used'] = True; pairs.append((i, j))
    dims = []
    for i, j in pairs:
        a, b = arrows[i], arrows[j]; d = b['tip'] - a['tip']; L = float(np.linalg.norm(d)); d = d / L
        dm = Dimension(id='', kind='linear', text='', style='paired', tips=[[float(a['tip'][0]), float(a['tip'][1])], [float(b['tip'][0]), float(b['tip'][1])]])
        ux = S[:, 2] - S[:, 0]; uy = S[:, 3] - S[:, 1]; ln = np.hypot(ux, uy) + 1e-9; cosd = np.abs((ux * d[0] + uy * d[1]) / ln)
        dimline = (~claimed) & (on_line(a['tip'], b['tip'], 0.6) | ((cosd > 0.999) & ((_pt_seg_dist(S[:, 0:2], a['base'], a['base']) < 1.5 * T) | (_pt_seg_dist(S[:, 2:4], a['base'], a['base']) < 1.5 * T) | (_pt_seg_dist(S[:, 0:2], b['base'], b['base']) < 1.5 * T) | (_pt_seg_dist(S[:, 2:4], b['base'], b['base']) < 1.5 * T))))
        claimed |= dimline
        for tip in (a['tip'], b['tip']):             # extension lines: perpendicular to the dimension line and through a tip
            e = np.where((~claimed) & (cosd < 0.02) & (_pt_seg_dist(np.array([tip]), (0, 0), (0, 0))[0] >= 0))[0]
            if not len(e): continue
            dd = np.array([_pt_seg_dist(np.array([tip]), S[k, 0:2], S[k, 2:4])[0] for k in e]); hit = e[dd < 0.8 * T]
            for k in hit[:1]:
                claimed[k] = True; p0 = S[k, 0:2]; p1 = S[k, 2:4]
                far, near = (p0, p1) if np.linalg.norm(p0 - tip) > np.linalg.norm(p1 - tip) else (p1, p0)
                dm.ext.append([float(far[0]), float(far[1]), float(near[0]), float(near[1])])
        dm._line = (a['tip'], b['tip']); dims.append(dm)
    # --- leaders (single arrows)
    for a in arrows:
        if a['used']: continue
        a['used'] = True
        dm = Dimension(id='', kind='linear', text='', style='leader', tips=[[float(a['tip'][0]), float(a['tip'][1])]])
        end = None
        if len(S):
            ux = S[:, 2] - S[:, 0]; uy = S[:, 3] - S[:, 1]; ln = np.hypot(ux, uy) + 1e-9
            for k in np.where(~claimed)[0]:
                for p, q in ((S[k, 0:2], S[k, 2:4]), (S[k, 2:4], S[k, 0:2])):
                    if np.linalg.norm(p - a['base']) < 1.5 * T and float(((q - p) / (np.linalg.norm(q - p) + 1e-9)) @ a['u']) > 0.98: claimed[k] = True; end = q; break
                if end is not None: break
        shoulder = end
        if end is not None and len(S):
            for k in np.where(~claimed)[0]:
                for p, q in ((S[k, 0:2], S[k, 2:4]), (S[k, 2:4], S[k, 0:2])):
                    if np.linalg.norm(p - end) < 1.5 * T and abs(q[1] - p[1]) < 0.5 * T: claimed[k] = True; shoulder = q; break
        dm._anchor = shoulder if shoulder is not None else a['tip']; dm._line = None
        dims.append(dm)
    # --- text association and parsing
    free = [r for r in runs if not r.used]
    def near_runs(dm):
        if dm.style == 'paired':
            a, b = dm._line; ref = lambda r: float(_pt_seg_dist(np.array([[(r.bbox[0] + r.bbox[2]) / 2, (r.bbox[1] + r.bbox[3]) / 2]]), a, b)[0])
        else:
            ref = lambda r: _bbox_gap([dm._anchor[0], dm._anchor[1], dm._anchor[0], dm._anchor[1]], r.bbox)
        return sorted((r for r in free if not r.used), key=ref), ref
    for dm in dims:
        rs, ref = near_runs(dm)
        main = None; stack = []
        for r in rs:                                 # a fractional dimension first: its numerator and denominator would otherwise read as a limits pair
            if ref(r) > (3.2 * r.size if dm.style == 'paired' else 2.0 * r.size + 6): break
            if parse_dim_text(r.text) and _read_fraction(r, free)[0] is not None: main = r; break
        nums = [r for r in rs if main is None and ref(r) <= 3.2 * r.size and _num(r.text) and not _num(r.text)[1] and r.rot in (0, 90)]
        for r in nums:
            for q in nums:
                if r is not q and abs(r.size - q.size) < 0.2 and (abs(r.bbox[0] - q.bbox[0]) < 2 * r.size if r.rot == 0 else abs(r.bbox[1] - q.bbox[1]) < 2 * r.size) and (0.3 * r.size < abs(r.oy - q.oy) < 2.6 * r.size if r.rot == 0 else 0.3 * r.size < abs(r.ox - q.ox) < 2.6 * r.size):
                    stack = [r, q]; break
            if stack: break
        if not stack and main is None:
            for r in rs:
                if ref(r) > (3.2 * r.size if dm.style == 'paired' else 2.0 * r.size + 6): break
                if parse_dim_text(r.text): main = r; break
        if main is None:
            if stack:
                hi, lo = (stack if (stack[0].oy > stack[1].oy if stack[0].rot == 0 else stack[0].ox < stack[1].ox) else stack[::-1])
                if hi.text.strip().isdigit() and lo.text.strip().isdigit() and int(hi.text) < int(lo.text) and int(lo.text) in (2, 4, 8, 16, 32, 64):     # 3 over 4 is three quarters, not limits (limits run high over low)
                    dm.value = int(hi.text) / int(lo.text); dm.text = f'{hi.text}/{lo.text}'; dm.text_pos = [hi.ox, hi.oy]; dm.text_rot = hi.rot; hi.used = lo.used = True; dm.unit = 'inch'
                    dm.tol = Tol('none'); mk = [r for r in free if not r.used and r.text.strip() in ('"', '\u201d', '\u2033') and abs(r.oy - hi.oy) < 4 * hi.size + 20 and abs(r.ox - hi.ox) < 6 * hi.size]
                    if mk: mk[0].used = True; dm.text += '"'
                    continue
                dm.tol = Tol('limits', _num(hi.text)[0], _num(lo.text)[0]); dm.value = round((dm.tol.upper + dm.tol.lower) / 2, 6); dm.text = f'{hi.text} / {lo.text}'
                dm.text_pos = [hi.ox, hi.oy]; dm.text_rot = hi.rot; hi.used = lo.used = True
                continue
            dm.conf = 'partial'; continue
        p = parse_dim_text(main.text); main.used = True
        dm.kind, dm.value, dm.count, dm.suffix = p['kind'], p['value'], p['count'], p['suffix']; dm.text = main.text; dm.text_pos = [main.ox, main.oy]; dm.text_rot = main.rot
        dm.tol = Tol('reference') if p['reference'] else Tol('basic') if p['basic'] else Tol('none')
        if p['tol']: dm.tol = Tol('bilateral', p['tol'][0], p['tol'][1])
        for k, b in enumerate(rects):
            if k not in boxes_used and b not in border and _inside(main.bbox, b, 2.5) and not _inside(b, main.bbox, 0) and (b[2] - b[0]) < 6 * main.size:
                dm.tol = Tol('basic'); claim_box(b)
        # whole number + stacked fraction, and the inch mark
        frac, used = _read_fraction(main, free); parts = [main] + list(used)
        if frac is not None:
            dm.value = (dm.value or 0.0) + frac; dm.text += ' ' + '/'.join(r.text for r in used)
            for r in used: r.used = True
            dm.unit = 'inch'
        for r in free:
            if not r.used and r.text.strip() in ('"', '”', '″') and abs(r.ox - main.bbox[2]) < 4 * main.size + (30 if frac else 0) and abs(r.oy - main.oy) < 2.5 * main.size:
                r.used = True; dm.unit = 'inch'; dm.text += '"'; parts.append(r); break
        # parentheses are drawn as filled shapes: two tall thin ones flanking the whole dimension text mark a reference dimension
        ext = parts
        ub = [_tf(main, bx, by) for r in ext for bx, by in ((r.bbox[0], r.bbox[1]), (r.bbox[2], r.bbox[3]))]
        u0 = min(u for u, v in ub); u1 = max(u for u, v in ub); vmid = sum(v for u, v in ub) / len(ub); left = right = False
        for g in geo.glyphs:
            gu0, gv0 = _tf(main, g[0], g[1]); gu1, gv1 = _tf(main, g[2], g[3]); gl, gh = min(gu0, gu1), max(gu0, gu1); gvl, gvh = min(gv0, gv1), max(gv0, gv1)
            if (gvh - gvl) > 1.4 * (gh - gl) and (gvh - gvl) > 0.8 * main.size and gvl - 0.8 * main.size < vmid < gvh + 0.8 * main.size:
                if u0 - 2.5 * main.size < gh <= u0 + 0.5 * main.size: left = True
                if u1 - 0.5 * main.size <= gl < u1 + 2.5 * main.size: right = True
        if left and right and dm.tol.kind == 'none': dm.tol = Tol('reference')
        # descriptive text beside the value (TOTAL HEIGHT)
        for r in rs:
            if not r.used and ref(r) < 2.8 * main.size and re.fullmatch(r'[A-Za-z][A-Za-z \-/]{2,30}', r.text.strip()) and r.rot == main.rot and not VIEW_LABEL.match(r.text.strip()):
                dm.label = r.text.strip(); r.used = True; break
        def beside(r):
            if r.used or r.size > 0.95 * main.size or not _num(r.text): return False
            if main.rot == 0: return -0.2 * main.size < r.ox - main.bbox[2] < 2.5 * main.size and abs(r.oy - main.oy) < 2.2 * main.size
            return -0.2 * main.size < r.oy - main.bbox[3] < 2.5 * main.size and abs(r.ox - main.ox) < 2.6 * main.size
        tolruns = [r for r in free if beside(r)]
        if tolruns and not p['tol'] and frac is None:
            tolruns.sort(key=(lambda r: -r.oy) if main.rot == 0 else (lambda r: r.ox)); vals = []
            for r in tolruns[:2]:
                n = _num(r.text); r.used = True; vals.append(n[0]); dm.text += ' ' + r.text
            if len(vals) == 2: dm.tol = Tol('bilateral', vals[0], vals[1])
            elif len(vals) == 1: dm.tol = Tol('bilateral', vals[0], None)
    # leaders and pairs that never found a value are not dimensions (balloon leaders, notes with arrows): keep only paired ones that have extension lines
    dims = [d for d in dims if (d.conf == 'ok' and (d.style != 'paired' or d.ext)) or (d.style == 'paired' and len(d.ext) == 2)]
    # --- unit: inch marks on the dimensions decide it for the sheet
    inch = sum(1 for d in dims if d.unit == 'inch'); metric = sum(1 for d in dims if d.unit != 'inch' and d.value is not None)
    if inch and inch >= metric: sh.unit = 'inch'; sh.unit_assumed = False
    for d in dims: d.unit = sh.unit
    unit_per_pt = (1 / 72.0) if sh.unit == 'inch' else mmpp
    # --- measure from the drawn geometry (length in drawing units; the scale converts it to real size)
    def paper_len(dm):
        if dm.style == 'paired' and dm.kind in ('linear', 'diameter'): return float(np.linalg.norm(np.array(dm.tips[0]) - np.array(dm.tips[1])))
        if dm.tips and dm.kind in ('diameter', 'radius'):
            best = None
            for cx, cy, r, bb in geo.arcs:
                e = abs(math.hypot(dm.tips[0][0] - cx, dm.tips[0][1] - cy) - r)
                if e < 0.6 * T and (best is None or e < best[0]): best = (e, cx, cy, r)
            if best:
                dm.arc = [best[1], best[2], best[3]]; return 2 * best[3] if dm.kind == 'diameter' else best[3]
        return None
    plen = {d.id or id(d): paper_len(d) for d in dims}
    if sh.scale is None:                           # no scale text: infer it from the dimensions when they agree with each other
        ratios = [plen[d.id or id(d)] * unit_per_pt / d.value for d in dims if plen[d.id or id(d)] and d.value and d.tol.kind != 'reference']
        if len(ratios) >= 2:
            med = float(np.median(ratios)); agree = sum(1 for r in ratios if abs(r / med - 1) < 0.02)
            if agree >= max(2, 0.6 * len(ratios)): sh.scale = med; sh.scale_text = 'inferred'; sh.warnings.append(f'scale inferred from {agree} of {len(ratios)} dimensions (no scale text on the sheet)')
    if sh.scale is None: sh.warnings.append('no sheet scale: drawn values are not computed')
    for dm in dims:
        L = plen[dm.id or id(dm)]
        if sh.scale and L and dm.value is not None:
            dm.drawn = round(L * unit_per_pt / sh.scale, 4); dm.drawn_dev = round(dm.drawn - dm.value, 4)
    # --- views: object linework not used by dimensions, datums, frames or the title block, clustered by proximity (grid hashing)
    obj = [list(b) for b in sb[~claimed]] if len(S) else []
    obj += [list(a[3]) for a in geo.arcs if not in_tb(a[3])]
    obj = [b for b in obj if not any(all(abs(x - y) < 0.5 for x, y in zip(b, c)) for c in border)]
    gap = 0.015 * W; cell = gap / 2
    cl = {}
    if obj:
        from scipy import ndimage
        nx, ny = int(W // cell) + 3, int(H // cell) + 3; mask = np.zeros((nx, ny), bool)
        ob = np.array(obj, float)
        ix0 = np.clip((ob[:, 0] // cell).astype(int), 0, nx - 1); ix1 = np.clip((ob[:, 2] // cell).astype(int), 0, nx - 1)
        iy0 = np.clip((ob[:, 1] // cell).astype(int), 0, ny - 1); iy1 = np.clip((ob[:, 3] // cell).astype(int), 0, ny - 1)
        for a0, a1, b0, b1 in zip(ix0, ix1, iy0, iy1): mask[a0:a1 + 1, b0:b1 + 1] = True
        lab, _ = ndimage.label(ndimage.binary_dilation(mask, iterations=1), structure=np.ones((3, 3)))
        for i, b in enumerate(obj): cl.setdefault(int(lab[ix0[i], iy0[i]]), []).append(b)
    views = []
    for members in cl.values():
        if len(members) < 3: continue
        bb = [min(b[0] for b in members), min(b[1] for b in members), max(b[2] for b in members), max(b[3] for b in members)]
        if (bb[2] - bb[0]) < 0.02 * W and (bb[3] - bb[1]) < 0.02 * W: continue
        if max(bb[2] - bb[0], bb[3] - bb[1]) < 0.04 * W and len(members) < 8: continue          # a stray fragment (leftover arrowhead outlines, a tick), not a view
        if len(members) >= 4 and sum(1 for r in runs if _inside(r.bbox, bb, 1.0)) >= 6 and all((b[2] - b[0]) < 0.5 or (b[3] - b[1]) < 0.5 for b in members): continue     # a table
        views.append(View(id='', bbox=[round(x, 2) for x in bb], n_segments=len(members), scale=sh.scale))
    views.sort(key=lambda v: (-v.bbox[3], v.bbox[0]))
    for i, v in enumerate(views, 1): v.id = f'V{i}'
    if keep_lines and len(S):
        free = S[~claimed]; cx = (free[:, 0] + free[:, 2]) / 2; cy = (free[:, 1] + free[:, 3]) / 2
        for v in views:
            m = (cx >= v.bbox[0] - 1) & (cx <= v.bbox[2] + 1) & (cy >= v.bbox[1] - 1) & (cy <= v.bbox[3] + 1); v.lines = np.round(free[m, :4], 2).tolist()
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
    for dm in dims:
        for k in ('_line', '_anchor'):
            if hasattr(dm, k): delattr(dm, k)
    sh.dimensions = dims; sh.views = views
    for i, d in enumerate(sorted(datums, key=lambda d: (-d.pos[1], d.pos[0])), 1): d.id = f'T{i}'; sh.datums.append(d)
    for i, f in enumerate(sorted(frames, key=lambda f: (-f.pos[3], f.pos[0])), 1):
        f.id = f'G{i}'; best = None
        for dm in dims:
            for p in ([dm.text_pos] if dm.text_pos else []) + [list(t) for t in dm.tips]:
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
