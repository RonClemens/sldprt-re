"""Scanned / raster engineering drawing -> drawing IR (drawing_ir.py), by the same logic as the vector reader (pdf_drawing._read_sheet).

  read_scan(path, page=1) -> Drawing         python3 src/scan_drawing.py FILE.pdf [PAGE] [--json]
  read_scan_gray(gray, size_pt) -> Sheet     (an already rendered grayscale image; for tests and image files)

Needs `pip install opencv-python-headless pypdfium2 rapidocr-onnxruntime` (all permissive, no system binaries).

The scan is turned into the pieces the vector reader extracts from a PDF: text runs (OCR), line segments (long horizontal and vertical lines by morphology, other lines by a
probabilistic Hough transform), arrowheads (small filled triangles that survive removing thin strokes) and circles. `_read_sheet` then pairs arrows, reads tolerances, builds views and
measures drawn lengths exactly as it does for vector input, with a wider geometric tolerance.

OCR text is repaired with drawing-specific rules before parsing (a slash read as a digit, a space read as a dot, a diameter sign read as @ or 0). Every dimension from a scan is
marked `conf='ocr'` unless the repaired text and the drawn geometry agree (then `ok`). Not handled: skewed scans (deskew), handwriting, very low resolution (< ~100 dpi), arcs that are not circles.
Verified on a degraded copy of the synthetic drawing and looked at on one real vendor scan; see docs/drawing-compare.md for the numbers.
"""
import math, os, re, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drawing_ir import MM_PER_PT, Sheet, Drawing
import pdf_drawing as P

_OCR = None


def _ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


# ----------------------------------------------------------------------------- OCR text repair
def repair_text(t):
    """OCR output -> the text the dimension parser expects. Rules are narrow on purpose; a token that matches none is left alone."""
    t = t.strip().replace('’', "'").replace('“', '"').replace('”', '"')
    t = re.sub(r'^[@ØøO](?=\d)', 'Ø', t)                       # @18 / O18 -> diameter sign
    t = re.sub(r'^0(?=\d{1,3}[ .]\d/\d)', 'Ø', t)                         # 016.3/8 -> diameter 16 3/8
    t = re.sub(r'(\d)\.(\d{1,2}/\d{1,2})\b', r'\1 \2', t)                      # 15.5/8 -> 15 5/8
    t = re.sub(r'(\d)\.\s+(\d/\d)', r'\1 \2', t)                               # 8. 1/2 -> 8 1/2
    t = re.sub(r'(\d+)[. ]([1357])[7/](2|4|8|16)\b(?!/)', lambda m: m.group(0) if int(m.group(2)) >= int(m.group(3)) else f'{m.group(1)} {m.group(2)}/{m.group(3)}', t)    # 18.174 -> 18 1/4 (a slash read as 7)
    t = re.sub(r'\b(R|Ø)?\s*(\d{1,3})(\d)/(\d{1,2})\b', lambda m: _split_fraction(m), t)   # 81/2 -> 8 1/2, 213/8 -> 21 3/8
    return t.strip()


def _split_fraction(m):
    pre, whole, num, den = m.group(1) or '', m.group(2), m.group(3), m.group(4)
    if int(den) in (2, 4, 8, 16, 32, 64) and int(num) < int(den): return f'{pre}{whole} {num}/{den}'.replace('R ', 'R').replace('Ø ', 'Ø')
    return m.group(0)


# ----------------------------------------------------------------------------- image -> primitives
def _mk_run(text, x0, y0, x1, y1, scale, H, rot=0):
    r = P.Run.__new__(P.Run); r.text = text; r.rot = rot
    h_pt = (y1 - y0) / scale if rot == 0 else (x1 - x0) / scale
    r.size = h_pt / 0.72
    r.bbox = [x0 / scale, H - y1 / scale, x1 / scale, H - y0 / scale]
    r.ox, r.oy = (x0 / scale, H - y1 / scale) if rot == 0 else (x1 / scale, H - y1 / scale)
    r.used = False
    return r


def ocr_runs(gray, scale, H):
    """text runs from the image; vertical text is found by a second pass on the rotated image and merged"""
    import cv2
    out = []
    def run_pass(img, rot):
        res, _ = _ocr()(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)); found = []
        for box, txt, conf in (res or []):
            pts = np.array(box, float); x0, y0 = pts.min(0); x1, y1 = pts.max(0); txt = repair_text(txt)
            if txt: found.append((float(conf), txt, x0, y0, x1, y1))
        return found
    for conf, txt, x0, y0, x1, y1 in run_pass(gray, 0):
        out.append((conf, _mk_run(txt, x0, y0, x1, y1, scale, H)))
    h, w = gray.shape
    rot = cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)             # vertical text (reads bottom to top) becomes horizontal
    for conf, txt, x0, y0, x1, y1 in run_pass(rot, 90):
        # map the box back: rotated image coords (u, v) -> original x = w - v, y = u ... for ROTATE_90_COUNTERCLOCKWISE: x' = y, y' = w - 1 - x
        ox0, ox1 = w - y1, w - y0; oy0, oy1 = x0, x1
        if (x1 - x0) > 1.2 * (y1 - y0): out.append((conf, _mk_run(txt, ox0, oy0, ox1, oy1, scale, H, rot=90)))     # a wide box in the rotated image = text running up the page
    runs = [r for _, r in out]
    # drop vertical hits that duplicate a horizontal hit of the same place
    keep = []
    for r in runs:
        if r.rot == 90 and any(q.rot == 0 and _overlap(q.bbox, r.bbox) for q in runs): continue
        keep.append(r)
    return keep


def _overlap(a, b):
    ix = min(a[2], b[2]) - max(a[0], b[0]); iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0: return False
    return ix * iy > 0.4 * min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))


def extract_geometry(gray, scale, H, ink_level=150):
    """-> pdf_drawing.Geo with segments, filled triangles (arrowheads) and circles in page points (y up)"""
    import cv2
    geo = P.Geo()
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    ink = (blur < ink_level).astype(np.uint8)
    pt = lambda x, y: (x / scale, H - y / scale)
    # arrowheads: opening with a structuring element wider than any line stroke leaves the arrowhead blobs (without their tips); the tip is recovered by growing the blob back
    # inside the ink, then each blob is fitted with its minimum enclosing triangle
    k = max(5, int(1.7 * scale) | 1)
    el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    opened = cv2.morphologyEx(ink, cv2.MORPH_OPEN, el)
    grown = cv2.dilate(opened, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k + 4, k + 4))) & ink
    n, lab, st, _ = cv2.connectedComponentsWithStats(grown, connectivity=8)               # components, not contours: the sheet border would swallow everything inside it
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA]
        if not (3.0 * scale * scale < a < 60 * scale * scale): continue                  # 3..60 pt^2: arrowheads are about 1.5 x 8 pt, larger blobs are text or symbols
        x, y, w, h = st[i, 0], st[i, 1], st[i, 2], st[i, 3]
        cs, _ = cv2.findContours((lab[y:y + h, x:x + w] == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cs: continue
        c = max(cs, key=cv2.contourArea) + np.array([[[x, y]]])
        area, tri = cv2.minEnclosingTriangle(c)
        if area <= 0 or a / area < 0.55: continue
        tri = np.asarray(tri).reshape(3, 2)
        sides = sorted(np.linalg.norm(tri[i] - tri[(i + 1) % 3]) for i in range(3))
        if sides[2] / max(sides[0], 1e-6) < 1.4 or sides[1] < 0.45 * sides[2]: continue  # needs a short base and two long sides
        geo.tris.append([pt(*p) for p in tri])
    # long horizontal and vertical lines: opening with long structuring elements, then one segment per component
    L = max(12, int(10 * scale / 4 * 1.0))
    for horiz in (True, False):
        el = cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1) if horiz else (1, L))
        m = cv2.morphologyEx(ink, cv2.MORPH_OPEN, el)
        n, lab, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if horiz and w >= L and h <= 0.25 * w + 6: geo.segs.append([*pt(x, y + h / 2), *pt(x + w, y + h / 2), h / scale])
            if not horiz and h >= L and w <= 0.25 * h + 6: geo.segs.append([*pt(x + w / 2, y), *pt(x + w / 2, y + h), w / scale])
    # other lines (leaders, isometric edges): Hough on what is left after removing the axis-aligned lines
    rest = ink.copy()
    for horiz in (True, False):
        el = cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1) if horiz else (1, L)); rest[cv2.morphologyEx(ink, cv2.MORPH_OPEN, el) > 0] = 0
    lines = cv2.HoughLinesP(rest * 255, 1, np.pi / 180, threshold=int(0.9 * L), minLineLength=int(1.4 * L), maxLineGap=int(0.35 * L))
    for l in (lines if lines is not None else [])[:4000]:
        x0, y0, x1, y1 = np.asarray(l).reshape(-1)[:4]      # (N,1,4) in OpenCV 4, (N,4) in 5
        if abs(x1 - x0) > 2 and abs(y1 - y0) > 2: geo.segs.append([*pt(x0, y0), *pt(x1, y1), 1.0 / scale])
    # circles
    circ = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1.2, minDist=int(3 * scale / 4 * 4), param1=120, param2=60, minRadius=int(2.0 * scale), maxRadius=int(40 * scale))
    for c in (np.asarray(circ).reshape(-1, 3) if circ is not None else [])[:60]:
        cx, cy, r = c; x, y = pt(cx, cy); rp = r / scale
        geo.arcs.append([x, y, rp, [x - rp, y - rp, x + rp, y + rp]])
    return geo


# ----------------------------------------------------------------------------- entry points
def read_scan_gray(gray, size_pt, index=1, scale=None):
    """gray: 2-D uint8 image of the whole sheet; size_pt: (width, height) of the sheet in points"""
    W, H = size_pt; scale = scale or gray.shape[1] / W
    sh = Sheet(index=index, size_pt=[round(W, 2), round(H, 2)])
    runs = ocr_runs(gray, scale, H)
    geo = extract_geometry(gray, scale, H)
    if not runs: sh.warnings.append('OCR found no text')
    P._read_sheet(sh, runs, geo, tol=2.5)
    sh.warnings.append('read from a raster image (OCR + line/arrow detection): check the text before relying on it')
    for d in sh.dimensions:
        d.conf = 'ocr' if d.conf == 'ok' and (d.drawn_dev is None or abs(d.drawn_dev) > 0.04 * max(abs(d.value or 1), 1)) else d.conf
    return sh


def read_scan(path, page=1, dpi=300):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path); p = pdf[page - 1]; W, H = p.get_size()
    gray = np.array(p.render(scale=dpi / 72).to_pil().convert('L'))
    d = Drawing('pdf-scan'); d.sheets.append(read_scan_gray(gray, (W, H), index=page, scale=dpi / 72)); return d


def main():
    import json
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args: raise SystemExit(__doc__)
    d = read_scan(args[0], int(args[1]) if len(args) > 1 else 1)
    if '--json' in sys.argv: print(json.dumps(d.to_dict(), indent=1, ensure_ascii=False)); return
    for s in d.sheets:
        print(f'sheet {s.index}: {s.size_pt} scale {s.scale_text} unit {s.unit} views {len(s.views)} dims {len(s.dimensions)} notes {len(s.notes)}', s.warnings)
        for dm in s.dimensions: print(f'  {dm.id} {dm.kind:8s} {dm.text!r:24s} value {dm.value} tol {dm.tol.kind} drawn {dm.drawn} dev {dm.drawn_dev} {dm.conf}')


if __name__ == '__main__':
    main()
