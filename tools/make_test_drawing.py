#!/usr/bin/env python3
"""Synthetic dimensioned drawing (vector PDF) with known ground truth, for testing the drawing reader without any real drawing.

  python3 tools/make_test_drawing.py OUT.pdf [OUT.truth.json]

The part is an invented 100 x 60 x 10 mm plate with two 8 mm holes and 5 mm corner radii, drawn at 1:2 on an A3 landscape sheet:
top and front views, linear / diameter / radius dimensions with bilateral, stacked, limit, basic and reference forms, a datum symbol,
a GD&T frame, notes and a title block. One dimension is deliberately wrong (its text says 25, its geometry spans 20) so the
printed-versus-drawn check has something to find. Written with raw PDF operators, no libraries; nothing here is a real drawing.
"""
import json, math, sys

PT = 72.0 / 25.4            # paper mm -> PDF points
SCALE = 0.5                 # drawn / real
ARROW_L, ARROW_W = 3.0, 1.0  # arrowhead length and width, paper mm
K = 4 / 3 * math.tan(math.pi / 8)


def esc(s):
    return s.encode('cp1252').replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')


class Page:
    def __init__(s):
        s.ops = []
        s.truth = dict(dimensions=[], datums=[], gdt=[], views=[], notes=[], scale=SCALE, scale_text='1:2', unit='mm')

    # ---- primitives (paper mm in, points out)
    def line(s, x0, y0, x1, y1, w=0.25):
        s.ops.append(f'{w * PT:.3f} w {x0 * PT:.3f} {y0 * PT:.3f} m {x1 * PT:.3f} {y1 * PT:.3f} l S')

    def rect(s, x0, y0, x1, y1, w=0.25):
        s.ops.append(f'{w * PT:.3f} w {x0 * PT:.3f} {y0 * PT:.3f} {(x1 - x0) * PT:.3f} {(y1 - y0) * PT:.3f} re S')

    def circle(s, cx, cy, r, w=0.35):
        pts = [(cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r), (cx + r, cy)]
        o = [f'{w * PT:.3f} w {pts[0][0] * PT:.3f} {pts[0][1] * PT:.3f} m']
        for i in range(4):
            a0 = i * math.pi / 2
            p0 = (cx + r * math.cos(a0), cy + r * math.sin(a0)); p3 = (cx + r * math.cos(a0 + math.pi / 2), cy + r * math.sin(a0 + math.pi / 2))
            c1 = (p0[0] - K * r * math.sin(a0), p0[1] + K * r * math.cos(a0)); c2 = (p3[0] + K * r * math.sin(a0 + math.pi / 2), p3[1] - K * r * math.cos(a0 + math.pi / 2))
            o.append(f'{c1[0] * PT:.3f} {c1[1] * PT:.3f} {c2[0] * PT:.3f} {c2[1] * PT:.3f} {p3[0] * PT:.3f} {p3[1] * PT:.3f} c')
        s.ops.append(' '.join(o) + ' S')

    def arc90(s, cx, cy, r, a0, w=0.35):          # quarter arc from angle a0 to a0+90deg (radians)
        p0 = (cx + r * math.cos(a0), cy + r * math.sin(a0)); p3 = (cx + r * math.cos(a0 + math.pi / 2), cy + r * math.sin(a0 + math.pi / 2))
        c1 = (p0[0] - K * r * math.sin(a0), p0[1] + K * r * math.cos(a0)); c2 = (p3[0] + K * r * math.sin(a0 + math.pi / 2), p3[1] - K * r * math.cos(a0 + math.pi / 2))
        s.ops.append(f'{w * PT:.3f} w {p0[0] * PT:.3f} {p0[1] * PT:.3f} m {c1[0] * PT:.3f} {c1[1] * PT:.3f} {c2[0] * PT:.3f} {c2[1] * PT:.3f} {p3[0] * PT:.3f} {p3[1] * PT:.3f} c S')

    def arrow(s, tx, ty, ux, uy):                 # filled triangle with its tip at (tx, ty); (ux, uy) points from the tip back along the dimension line
        bx, by = tx + ux * ARROW_L, ty + uy * ARROW_L; nx, ny = -uy * ARROW_W / 2, ux * ARROW_W / 2
        s.ops.append(f'{tx * PT:.3f} {ty * PT:.3f} m {(bx + nx) * PT:.3f} {(by + ny) * PT:.3f} l {(bx - nx) * PT:.3f} {(by - ny) * PT:.3f} l h f')

    def text(s, x, y, t, size=3.5, rot=0):        # size in paper mm
        m = '1 0 0 1' if rot == 0 else '0 1 -1 0'
        s.ops.append(f'BT /F1 {size * PT:.3f} Tf {m} {x * PT:.3f} {y * PT:.3f} Tm ({esc(t).decode("latin-1")}) Tj ET')

    # ---- dimensions
    def dim_h(s, id, x0, x1, ydim, yobj0, yobj1, texts, ext_gap=1.0, **truth):
        """horizontal linear dimension: tips at (x0, ydim) and (x1, ydim); extension lines from the objects up/down to just past the dimension line"""
        sg = 1 if ydim > yobj0 else -1
        for x, yo in ((x0, yobj0), (x1, yobj1)):
            s.line(x, yo + sg * ext_gap, x, ydim + sg * 2)
        s.line(x0, ydim, x1, ydim); s.arrow(x0, ydim, 1, 0); s.arrow(x1, ydim, -1, 0)
        for (dx, dy, t, sz) in texts: s.text((x0 + x1) / 2 + dx, ydim + dy, t, sz)
        s.truth['dimensions'].append(dict(id=id, tips=[[x0, ydim], [x1, ydim]], ext=[[x0, yobj0 + sg * ext_gap, x0, ydim + sg * 2], [x1, yobj1 + sg * ext_gap, x1, ydim + sg * 2]], style='paired', **truth))

    def dim_v(s, id, y0, y1, xdim, xobj0, xobj1, texts, ext_gap=1.0, rot=90, **truth):
        sg = 1 if xdim > xobj0 else -1
        for y, xo in ((y0, xobj0), (y1, xobj1)):
            s.line(xo + sg * ext_gap, y, xdim + sg * 2, y)
        s.line(xdim, y0, xdim, y1); s.arrow(xdim, y0, 0, 1); s.arrow(xdim, y1, 0, -1)
        for (dx, dy, t, sz) in texts: s.text(xdim + dx, (y0 + y1) / 2 + dy, t, sz, rot)
        s.truth['dimensions'].append(dict(id=id, tips=[[xdim, y0], [xdim, y1]], ext=[[xobj0 + sg * ext_gap, y0, xdim + sg * 2, y0], [xobj1 + sg * ext_gap, y1, xdim + sg * 2, y1]], style='paired', **truth))

    def leader(s, id, cx, cy, r, ang, elbow, shoulder, texts, **truth):
        tx, ty = cx + r * math.cos(ang), cy + r * math.sin(ang)
        ex, ey = elbow; sx, sy = shoulder
        d = math.hypot(ex - tx, ey - ty); s.line(tx + (ex - tx) / d * ARROW_L, ty + (ey - ty) / d * ARROW_L, ex, ey)
        s.arrow(tx, ty, (ex - tx) / d, (ey - ty) / d); s.line(ex, ey, sx, sy)
        for (dx, dy, t, sz) in texts: s.text(sx + dx, sy + dy, t, sz)
        s.truth['dimensions'].append(dict(id=id, tips=[[tx, ty]], arc=[cx, cy, r], style='leader', **truth))


def build():
    pg = Page()
    # sheet border and title block
    pg.rect(10, 10, 410, 287, 0.7)
    pg.rect(285, 10, 410, 52, 0.5); pg.line(285, 24, 410, 24, 0.35); pg.line(285, 38, 410, 38, 0.35)
    pg.text(288, 43, 'PART NO. TEST-001', 3.5); pg.text(288, 29, 'MATERIAL: AL 6061-T6', 3.5); pg.text(288, 14, 'SCALE 1:2', 3.5)
    pg.text(288, 46.5, 'UNLESS OTHERWISE SPECIFIED: .X +-0.5  .XX +-0.1', 2.5)
    pg.truth['notes'].append(dict(text='UNLESS OTHERWISE SPECIFIED: .X +-0.5  .XX +-0.1', kind='title_block'))
    pg.text(20, 270, 'NOTES:', 3.5); pg.text(20, 264, '1. BREAK ALL SHARP EDGES 0.2 MAX', 3.5)
    pg.truth['notes'].append(dict(text='NOTES:', kind='note')); pg.truth['notes'].append(dict(text='1. BREAK ALL SHARP EDGES 0.2 MAX', kind='note'))

    # --- top view: plate X 0..100 (paper 60..110), Y 0..60 (paper 150..180), corner R5 (paper 2.5)
    x0, x1, y0, y1, r = 60.0, 110.0, 150.0, 180.0, 2.5
    pg.line(x0 + r, y0, x1 - r, y0, 0.5); pg.line(x1, y0 + r, x1, y1 - r, 0.5); pg.line(x1 - r, y1, x0 + r, y1, 0.5); pg.line(x0, y1 - r, x0, y0 + r, 0.5)
    pg.arc90(x1 - r, y0 + r, r, -math.pi / 2, 0.5); pg.arc90(x1 - r, y1 - r, r, 0, 0.5); pg.arc90(x0 + r, y1 - r, r, math.pi / 2, 0.5); pg.arc90(x0 + r, y0 + r, r, math.pi, 0.5)
    for hx in (70.0, 100.0):
        pg.circle(hx, 165.0, 2.0, 0.5); pg.line(hx - 3.2, 165, hx + 3.2, 165, 0.18); pg.line(hx, 161.8, hx, 168.2, 0.18)
    pg.truth['views'].append(dict(id='top', bbox=[x0, y0, x1, y1], label='TOP'))
    pg.text(75, 144, 'TOP', 5)
    # --- front view: X 0..100, Z 0..10 (paper 60..110 x 110..115)
    pg.rect(60, 110, 110, 115, 0.5)
    pg.truth['views'].append(dict(id='front', bbox=[60, 110, 110, 115], label='FRONT'))
    pg.text(75, 101, 'FRONT', 5)

    # dimensions (truth in REAL units; geometry drawn at scale 0.5)
    pg.dim_h('D1', 60, 110, 194, 180, 180, [(-8, 1.0, '100 \xb10.1', 3.5)], kind='linear', value=100.0, tol=dict(kind='bilateral', upper=0.1, lower=-0.1), drawn=100.0)
    pg.dim_v('D2', 150, 180, 53, 60, 60, [(-1.0, -9, '60', 3.5), (-3.2, -3.5, '+0.05', 2.5), (-1.0, -3.5, '-0.1', 2.5)], kind='linear', value=60.0, tol=dict(kind='bilateral', upper=0.05, lower=-0.1), drawn=60.0)
    pg.dim_h('D3', 70, 100, 186, 165, 165, [(-5.5, 1.0, '60', 3.5)], kind='linear', value=60.0, tol=dict(kind='basic'), basic=True, drawn=60.0)
    pg.rect(78.5, 186.2, 84.5, 190.4, 0.25)        # box round the basic dimension's text
    pg.dim_h('D4', 60, 70, 128, 150, 165, [(-3.5, 1.0, '25', 3.5)], kind='linear', value=25.0, tol=dict(kind='none'), drawn=20.0)
    pg.leader('D5', 70, 165, 2.0, math.pi / 4, (78, 176), (82, 176), [(1.0, -1.2, '2X \xd88', 3.5), (11.0, 0.0, '+0.05', 2.5), (11.0, -2.8, '0', 2.5)],
              kind='diameter', value=8.0, count=2, tol=dict(kind='bilateral', upper=0.05, lower=0.0), drawn=8.0)
    pg.leader('D6', 107.5, 177.5, 2.5, math.pi / 4, (118, 186), (122, 186), [(1.0, -1.2, 'R5', 3.5)], kind='radius', value=5.0, tol=dict(kind='none'), drawn=5.0)
    pg.dim_v('D7', 110, 115, 117, 110, 110, [(2.0, 1.0, '10.1', 3.0), (2.0, -2.2, '9.9', 3.0)], rot=0, kind='linear', value=10.0, tol=dict(kind='limits', upper=10.1, lower=9.9), drawn=10.0)
    pg.dim_h('D8', 60, 110, 122, 115, 115, [(-4.0, 1.0, '(100)', 3.5)], kind='linear', value=100.0, tol=dict(kind='reference'), reference=True, drawn=100.0)

    # datum feature symbol A under the top view: triangle on the edge, leader, box with the letter
    pg.ops.append(f'{90 * PT:.3f} {150 * PT:.3f} m {89 * PT:.3f} {147.5 * PT:.3f} l {91 * PT:.3f} {147.5 * PT:.3f} l h f')
    pg.line(90, 147.5, 90, 146, 0.25); pg.rect(87.5, 141, 92.5, 146, 0.25); pg.text(88.7, 142.3, 'A', 3.5)
    pg.truth['datums'].append(dict(letter='A'))
    # GD&T frame: position, diameter 0.1 at maximum material, datum A (three cells), under the 8 mm hole dimension's text
    fx, fy, fh = 82.0, 167.2, 4.5
    pg.rect(fx, fy, fx + 26, fy + fh, 0.3); pg.line(fx + 6, fy, fx + 6, fy + fh, 0.3); pg.line(fx + 20, fy, fx + 20, fy + fh, 0.3)
    pg.text(fx + 2, fy + 1.2, 'p', 3.5); pg.text(fx + 7, fy + 1.2, '\xd80.1 (M)', 3.0); pg.text(fx + 22.5, fy + 1.2, 'A', 3.5)
    pg.truth['gdt'].append(dict(cells=['p', '\xd80.1 (M)', 'A'], value=0.1, diameter_zone=True, modifiers=['M'], datums=['A'], attached_to='D5'))
    return pg


def write_pdf(pg, path):
    content = '\n'.join(pg.ops).encode('latin-1')
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            f'<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 {420 * PT:.2f} {297 * PT:.2f}] /Contents 5 0 R >>'.encode(),
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
            b'<< /Length %d >>\nstream\n' % len(content) + content + b'\nendstream']
    out = bytearray(b'%PDF-1.4\n'); offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b'%d 0 obj\n' % i + o + b'\nendobj\n'
    xref = len(out); out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1)
    for o in offs: out += b'%010d 00000 n \n' % o
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, xref)
    open(path, 'wb').write(bytes(out))


def main():
    if len(sys.argv) < 2: raise SystemExit(__doc__)
    pg = build(); write_pdf(pg, sys.argv[1])
    if len(sys.argv) > 2: json.dump(pg.truth, open(sys.argv[2], 'w'), indent=1, ensure_ascii=False)
    print('wrote', sys.argv[1])


if __name__ == '__main__':
    main()
