#!/usr/bin/env python3
"""The zones of a drawing sheet (A1, C3, F8 ...), what each holds, and the views that span several of them, with zoomed crops of each.

  python3 src/sheet_zones.py DRAWING.pdf PAGE [--json OUT.json] [--md OUT.md] [--crops DIR] [--dpi 200] [--projection third|first] [--unit inch|mm] [--no-virtual-grid]        read_zones(pdf, page, projection='third', unit=None) -> dict        render_crop(pdf, page, bbox_pt, dpi, out_png)

A sheet's border carries a letter for each row and a number for each column (here letters down the sides, numbers along the top and bottom; the numbers run right to left). The labels are read from the page text (single
characters in the margins), the zone boundaries are the midpoints between neighbouring labels and the border frame, and a zone is named letter + number as on the sheet (F8 is top left when the numbers run 8..1).

What it reports:
  grid      the label positions and zone edges in PDF points
  zones     for each zone: its box, a class (title block, BOM table, revision table, notes, view, dimensions only, empty) and what is in it: views that touch it, number of dimensions, circles, text items
  views     the clusters of object linework (the views): box, the zones it spans written as a range (F8-D7), orthographic or pictorial (isometric), circles, the dimensions that belong to it (nearest view),
            the title and SCALE label found beside it; two views that share a zone are listed, a view that spans several zones is the combined region (A1-A2)
  tables    revision table, BOM table, notes block, title block: where they are and in which zones
With --crops DIR every view region and table is rendered to a PNG at --dpi so the details (a slot callout, a dimension chain) can be looked at one zone group at a time.

Heuristics: the views are clusters of linework of object width (the width of the circles, else 0.6 pt) whose boxes lie within 8 pt of each other, after the border and title block are removed; a title is the text within 80 pt below
(or above) a view's box; a view is pictorial when fewer than 60 percent of its long segments are horizontal or vertical. A sheet without readable margin labels gets a virtual grid (`grid['virtual']`): zones about 198 x 204 pt, the size of the labelled sheets of the Hydro package, numbered right to left and lettered top down as on a labelled sheet (`virtual_grid=False`: one zone `*`).
"""
import collections, json, os, re, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)


def _labels(sheet):
    W, H = sheet.size_pt; cols = collections.defaultdict(list); rows = collections.defaultdict(list)
    for n in sheet.notes:
        t = n.text.strip()
        if len(t) != 1 or not n.pos: continue
        x, y = n.pos[0], n.pos[1]
        if t.isdigit() and (y > H - 40 or y < 40): cols[t].append(x)
        elif t.isalpha() and t.isupper() and (x < 45 or x > W - 45): rows[t].append(y)
    cols = sorted(((float(np.mean(v)), k) for k, v in cols.items())); rows = sorted(((float(np.mean(v)), k) for k, v in rows.items()), reverse=True)
    return cols, rows


def _frame(S, W, H):
    xs = [min(s[0], s[2]) for s in S if abs(s[1] - s[3]) < 0.05 and abs(s[2] - s[0]) >= 0.85 * W] + [max(s[0], s[2]) for s in S if abs(s[1] - s[3]) < 0.05 and abs(s[2] - s[0]) >= 0.85 * W]
    ys = [min(s[1], s[3]) for s in S if abs(s[0] - s[2]) < 0.05 and abs(s[3] - s[1]) >= 0.85 * H] + [max(s[1], s[3]) for s in S if abs(s[0] - s[2]) < 0.05 and abs(s[3] - s[1]) >= 0.85 * H]
    return (min(xs), min(ys), max(xs), max(ys)) if xs and ys else (0.0, 0.0, W, H)


def _clusters(S, geo, objw, gap=8.0):
    boxes = [(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)) for x0, y0, x1, y1, w in S if w >= objw] + [tuple(a[3]) for k, a in enumerate(geo.arcs) if (geo.arc_w[k] if k < len(geo.arc_w) else objw) >= objw]
    parent = list(range(len(boxes)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    cell = collections.defaultdict(list)
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        for gx in range(int((x0 - gap) // 24), int((x1 + gap) // 24) + 1):
            for gy in range(int((y0 - gap) // 24), int((y1 + gap) // 24) + 1):
                for j in cell[(gx, gy)]:
                    b = boxes[j]
                    if x0 - gap <= b[2] and b[0] <= x1 + gap and y0 - gap <= b[3] and b[1] <= y1 + gap: parent[find(i)] = find(j)
                cell[(gx, gy)].append(i)
    cl = collections.defaultdict(list)
    for i, b in enumerate(boxes): cl[find(i)].append(b)
    out = [[min(b[0] for b in v), min(b[1] for b in v), max(b[2] for b in v), max(b[3] for b in v), len(v)] for v in cl.values()]
    out.sort(key=lambda c: -(c[2] - c[0]) * (c[3] - c[1])); merged = []
    for c in out:                                                           # a cluster inside another one's box (the slots inside an outline) belongs to it
        for m in merged:
            if c[0] >= m[0] - 1 and c[1] >= m[1] - 1 and c[2] <= m[2] + 1 and c[3] <= m[3] + 1: m[4] += c[4]; break
        else: merged.append(c)
    return [tuple(m) for m in merged]


def _inter(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0]); h = min(a[3], b[3]) - max(a[1], b[1]); return max(w, 0) * max(h, 0)


def _range(names):
    """['F8','F7','E8','E7'] -> 'F8-E7' when they fill a rectangle, else the list joined with +"""
    rows = sorted({n[0] for n in names}, reverse=True); cols = sorted({n[1:] for n in names}, key=lambda c: -int(c) if c.isdigit() else 0)
    if len(names) == len(rows) * len(cols) and (len(rows) > 1 or len(cols) > 1): return f'{rows[0]}{cols[0]}-{rows[-1]}{cols[-1]}'
    return '+'.join(sorted(names)) if len(names) > 1 else (names[0] if names else '')


def read_zones(pdf, page, projection='third', unit=None, virtual_grid=True):
    from pdf_drawing import read_pdf
    import drawing_part as DP
    sheet = read_pdf(pdf, pages=[page]).sheets[0]; S, geo = DP.read_segments(pdf, page); W, H = sheet.size_pt
    if unit in ('inch', 'mm'):                                                        # override when the reader could not tell the sheet's unit
        for d in sheet.dimensions: d.unit = unit
    elif getattr(sheet, 'unit_assumed', False) and sheet.dimensions and projection != '_no_unit_guess':
        # nothing on the sheet says the unit (no inch marks): read it both ways and keep the one the drawn linework supports better (more printed values equal a spacing of lines at the view's scale)
        a = read_zones(pdf, page, '_no_unit_guess', 'mm', virtual_grid); b = read_zones(pdf, page, '_no_unit_guess', 'inch', virtual_grid)
        ma = sum(v.get('scale_matches', 0) for v in a['views']); mb = sum(v.get('scale_matches', 0) for v in b['views'])
        pick = b if mb > 1.2 * ma else a; pick['unit_guess'] = dict(chosen='inch' if pick is b else 'mm', matches_if_mm=ma, matches_if_inch=mb, note='the sheet carries no inch marks, so the unit was inferred; pass unit= to override')
        return pick if projection == 'third' else read_zones(pdf, page, projection, pick['unit_guess']['chosen'], virtual_grid)
    fx0, fy0, fx1, fy1 = _frame(S, W, H); cols, rows = _labels(sheet)
    if len(cols) >= 2 and len(rows) >= 2:
        xe = [fx0] + [(a[0] + b[0]) / 2 for a, b in zip(cols, cols[1:])] + [fx1]; ye = [fy1] + [(a[0] + b[0]) / 2 for a, b in zip(rows, rows[1:])] + [fy0]
        zones = {f'{rn}{cn}': dict(box=[xe[i], ye[j + 1], xe[i + 1], ye[j]]) for i, (_, cn) in enumerate(cols) for j, (_, rn) in enumerate(rows)}; grid = dict(columns=[c for _, c in cols], rows=[r for _, r in rows], x_edges=[round(x, 1) for x in xe], y_edges=[round(y, 1) for y in ye])
    elif virtual_grid:                                                                 # no labels on the sheet: a grid of the same zone size as the labelled sheets of the package (about 198 x 204 pt), numbered the usual way
        nc = max(2, round((fx1 - fx0) / 198.0)); nr = max(2, round((fy1 - fy0) / 204.0)); xe = [fx0 + (fx1 - fx0) * i / nc for i in range(nc + 1)]; ye = [fy1 - (fy1 - fy0) * j / nr for j in range(nr + 1)]
        cn = [str(nc - i) for i in range(nc)]; rn = [chr(ord('A') + nr - 1 - j) for j in range(nr)]
        zones = {f'{rn[j]}{cn[i]}': dict(box=[xe[i], ye[j + 1], xe[i + 1], ye[j]]) for i in range(nc) for j in range(nr)}; grid = dict(columns=cn, rows=rn, x_edges=[round(x, 1) for x in xe], y_edges=[round(y, 1) for y in ye], virtual=True)
    else: zones = {'*': dict(box=[fx0, fy0, fx1, fy1])}; grid = None
    for z in zones.values(): z.update(tags=[], dims=0, circles=0, text=0, views=[])
    # border and title block are not views
    keep = np.ones(len(S), bool)
    for i, (x0, y0, x1, y1, w) in enumerate(S):
        if abs(y0 - y1) < 0.05 and abs(x1 - x0) >= 0.85 * W or abs(x0 - x1) < 0.05 and abs(y1 - y0) >= 0.85 * H: keep[i] = False
    tbn = [n for n in sheet.notes if n.kind == 'title_block' and n.pos]
    tbbox = [min(n.pos[0] for n in tbn) - 60, min(n.pos[1] for n in tbn) - 20, max(n.pos[0] for n in tbn) + 60, max(n.pos[1] for n in tbn) + 30] if tbn else None
    if tbbox:
        for i, (x0, y0, x1, y1, w) in enumerate(S):
            if min(x0, x1) >= tbbox[0] and max(y0, y1) <= tbbox[3] + 10: keep[i] = False
    Sv = S[keep]; C = DP.circles(Sv)
    for k, a in enumerate(geo.arcs):
        if a[2] > 5 and (a[3][2] - a[3][0]) > 1.8 * a[2] and not any(np.hypot(a[0] - c['cx'], a[1] - c['cy']) < 0.5 and abs(a[2] - c['r']) < 0.5 for c in C) and not (tbbox and a[0] >= tbbox[0] and a[1] <= tbbox[3]): C.append(dict(cx=float(a[0]), cy=float(a[1]), r=float(a[2]), w=float(geo.arc_w[k]) if k < len(geo.arc_w) else 0.7))
    objw = float(np.median([c['w'] for c in C])) - 0.05 if C else 0.6
    views = []
    cl = [list(c) for c in _clusters(Sv, geo, objw)]; balloons = []; symbols = []  # balloons here: object-width lone circles only; thin item balloons are listed in the result as callout_balloons
    for c in list(cl):                                                              # a lone circle of 30 to 80 pt (an item balloon) and anything under 20 pt (arrowheads, datum marks) are callout symbols, not views
        w_, h_ = c[2] - c[0], c[3] - c[1]
        inside = [k for k in C if c[0] - 1 <= k['cx'] <= c[2] + 1 and c[1] - 1 <= k['cy'] <= c[3] + 1]
        if 30 <= w_ <= 80 and 0.85 <= w_ / max(h_, 1e-6) <= 1.18 and len(inside) == 1 and abs(w_ - 2 * inside[0]['r']) < 3 and abs(h_ - 2 * inside[0]['r']) < 3: balloons.append(c); cl.remove(c)
        elif max(w_, h_) < 20: symbols.append(c); cl.remove(c)
    big = sorted(cl, key=lambda c: -(c[2] - c[0]) * (c[3] - c[1])); fragments = 0
    for c in list(big):                                                             # a small cluster beside a much bigger one (a hidden-line or section fragment) is part of it
        host = next((h for h in big if h is not c and (h[2] - h[0]) * (h[3] - h[1]) >= 8 * (c[2] - c[0]) * (c[3] - c[1]) and c[0] - 30 <= h[2] and h[0] <= c[2] + 30 and c[1] - 30 <= h[3] and h[1] <= c[3] + 30), None)
        if host: host[0] = min(host[0], c[0]); host[1] = min(host[1], c[1]); host[2] = max(host[2], c[2]); host[3] = max(host[3], c[3]); host[4] += c[4]; big.remove(c); fragments += 1
    cl = big
    for (x0, y0, x1, y1, n) in cl:
        if n < 6 or max(x1 - x0, y1 - y0) < 20: continue
        long_ = [s for s in Sv if s[4] >= objw and min(x0, x1) - 1 <= min(s[0], s[2]) and max(s[0], s[2]) <= x1 + 1 and y0 - 1 <= min(s[1], s[3]) and max(s[1], s[3]) <= y1 + 1 and np.hypot(s[2] - s[0], s[3] - s[1]) >= 12]
        ortho = sum(1 for s in long_ if abs(s[0] - s[2]) < 0.5 or abs(s[1] - s[3]) < 0.5) / max(len(long_), 1)
        views.append(dict(box=[round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)], segments=n, circles=sum(1 for c in C if x0 - 1 <= c['cx'] <= x1 + 1 and y0 - 1 <= c['cy'] <= y1 + 1), type='orthographic' if ortho >= 0.45 else 'pictorial', dims=[], title='', scale=None))
    views.sort(key=lambda v: (-v['box'][3], v['box'][0]))
    for i, v in enumerate(views, 1): v['id'] = f'V{i}'
    import view_features as VF
    # dimensions go to the nearest view; titles and scale labels to the nearest view above or below
    def dist(p, b): return float(np.hypot(max(b[0] - p[0], 0, p[0] - b[2]), max(b[1] - p[1], 0, p[1] - b[3])))
    for d in sheet.dimensions:
        if d.text_pos and views:
            v = min(views, key=lambda v: dist(d.text_pos, v['box']))
            if dist(d.text_pos, v['box']) <= 160: v['dims'].append(d.id)
    for n in sheet.notes:
        if n.kind != 'note' or not n.pos or not views or len(n.text.strip()) < 3: continue
        t = n.text.strip(); up = t.upper()
        for v in views:
            bx = v['box']
            if bx[0] - 120 <= n.pos[0] <= bx[2] + 60 and (bx[1] - 130 <= n.pos[1] <= bx[1] or bx[3] <= n.pos[1] <= bx[3] + 40):
                m = re.search(r'SCALE\s*(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)', up)
                if m: v['scale'] = f'{m.group(1)}:{m.group(2)}'
                elif n.size >= 12 and up.replace(' ', '').replace('-', '').isalpha() and 'NOT SCALE' not in up and not v['title']: v['title'] = t
                break
    VF.analyse_views(sheet, Sv, geo, C, views, DP.slots(Sv), projection)
    # tables and blocks from text
    tables = []
    def add_table(kind, notes):
        if notes:
            xs = [n.pos[0] for n in notes]; ys = [n.pos[1] for n in notes]; tables.append(dict(kind=kind, box=[round(min(xs) - 10, 1), round(min(ys) - 10, 1), round(max(xs) + 60, 1), round(max(ys) + 14, 1)]))
    if not tbn: tbn = [n for n in sheet.notes if n.pos and n.pos[0] > 0.55 * W and n.pos[1] < 0.2 * H and re.search(r'DRWN BY|PROJECT #|MASTER ASSEMBLY|SHEET \d+ OF|PROPRIETARY|UNLESS OTHERWISE', n.text.upper())]
    big = [n for n in sheet.notes if n.pos and n.size >= 20 and n.kind == 'note' and n.text.strip().upper().replace(' ', '').isalpha()]
    for n in big: tables.append(dict(kind='sheet title', box=[round(n.pos[0] - 5, 1), round(n.pos[1] - 5, 1), round(n.pos[0] + len(n.text) * n.size * 0.62, 1), round(n.pos[1] + n.size + 5, 1)], text=n.text.strip()))
    add_table('title block', tbn)
    for t in tables:
        if t['kind'] == 'title block' and t['box'][0] > 0.4 * W: t['box'][2] = max(t['box'][2], fx1 - 1); t['box'][1] = min(t['box'][1], fy0 + 1)           # the title block runs to the frame at the right and at the bottom
    add_table('revision table', [n for n in sheet.notes if n.pos and re.fullmatch(r'REVISIONS|REV\.?|DESCRIPTION|DATE|RELEASE', n.text.strip().upper()) and n.pos[1] > 0.8 * H and n.pos[0] > 0.6 * W])
    bom = [n for n in sheet.notes if n.pos and re.fullmatch(r'ITEM|QTY\.?|NAME|DESCRIPTION|LENGTH|COMMENTS|MATERIAL|APP\. CODE', n.text.strip().upper()) and n.pos[1] < 0.3 * H and n.pos[1] > 0.05 * H]
    if len(bom) >= 3: add_table('BOM table', bom)
    nb = [n for n in sheet.notes if n.pos and re.match(r'NOTES?\b', n.text.strip().upper())]
    if nb:
        top = nb[0].pos; items = [n for n in sheet.notes if n.pos and n.pos[1] <= top[1] + 2 and abs(n.pos[0] - top[0]) < 500 and n.pos[1] > top[1] - 160 and n.kind == 'note']; add_table('notes block', items)
    # fill the zones
    for name, z in zones.items():
        zb = z['box']; za = max((zb[2] - zb[0]) * (zb[3] - zb[1]), 1.0)
        for v in views:
            if _inter(zb, v['box']) >= 0.12 * min(za, max((v['box'][2] - v['box'][0]) * (v['box'][3] - v['box'][1]), 1.0)): z['views'].append(v['id'])
        for t in tables:
            if _inter(zb, t['box']) >= 0.15 * min(za, max((t['box'][2] - t['box'][0]) * (t['box'][3] - t['box'][1]), 1.0)): z['tags'].append(t['kind'])
        z['dims'] = sum(1 for d in sheet.dimensions if d.text_pos and zb[0] <= d.text_pos[0] < zb[2] and zb[1] <= d.text_pos[1] < zb[3]); z['circles'] = sum(1 for c in C if zb[0] <= c['cx'] < zb[2] and zb[1] <= c['cy'] < zb[3])
        z['text'] = sum(1 for n in sheet.notes if n.pos and zb[0] <= n.pos[0] < zb[2] and zb[1] <= n.pos[1] < zb[3] and len(n.text.strip()) > 1)
        z['class'] = next((k for k in ('title block', 'BOM table', 'revision table', 'notes block') if k in z['tags']), 'view' if z['views'] else ('dimensions only' if z['dims'] else ('text only' if z['text'] > 2 else 'empty')))
    for v in views:
        names = [n for n, z in zones.items() if v['id'] in z['views']]; v['zones'] = sorted(names); v['range'] = _range(names)
    VF.zone_blocks(zones, views, sheet.dimensions)
    for t in tables: t['zones'] = sorted(n for n, z in zones.items() if _inter(z['box'], t['box']) >= 0.15 * min(max((z['box'][2] - z['box'][0]) * (z['box'][3] - z['box'][1]), 1.0), max((t['box'][2] - t['box'][0]) * (t['box'][3] - t['box'][1]), 1.0))); t['range'] = _range(t['zones'])
    bal = [dict(centre_pt=[round(k['cx'], 1), round(k['cy'], 1)], radius_pt=round(k['r'], 1), text=next((n.text.strip() for n in sheet.notes if n.pos and np.hypot(n.pos[0] - k['cx'], n.pos[1] - k['cy']) < k['r']), ''), zone=next((nm for nm, zz in zones.items() if zz['box'][0] <= k['cx'] < zz['box'][2] and zz['box'][1] <= k['cy'] < zz['box'][3]), '')) for k in C if 12 <= k['r'] <= 40 and not any(v['box'][0] <= k['cx'] <= v['box'][2] and v['box'][1] <= k['cy'] <= v['box'][3] for v in views)]       # item balloons: thin circles outside every view
    return dict(callout_balloons=bal, merged_fragments=fragments, sheet_pt=[W, H], frame=[round(x, 1) for x in (fx0, fy0, fx1, fy1)], grid=grid, zones=zones, views=views, tables=tables, circles=len(C), dimensions=len(sheet.dimensions))


def render_crop(pdf, page, bbox, dpi, out):
    import pypdfium2 as pdfium
    pg = pdfium.PdfDocument(pdf)[page - 1]; W, H = pg.get_size(); x0, y0, x1, y1 = bbox
    l, b, r, t = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
    pg.render(scale=dpi / 72, crop=(l, b, W - r, H - t)).to_pil().save(out)                      # crop = margins to cut from left, bottom, right, top


def blocks_markdown(z, name=''):
    """the building blocks per view and per zone as markdown: roles, axes, scale, dimension directions, holes, fillets, slots, chamfers"""
    L = [f'# Building blocks {name}'.strip(), '', 'Layout read as third-angle projection unless the run said otherwise: a view above the front view is the top view, below it the bottom view, right of it the right side. Inches are drawn inches at the scale shown.', '']
    for v in z['views']:
        L.append(f"## {v['id']} in zones {v['range']}: {v['role']}" + (f" ({v['title']})" if v['title'] else '')); L.append('')
        L.append(f"- axes across / up the page: {v['axes']['horizontal']} / {v['axes']['vertical']}; scale {v['scale_pt_per_in']} pt per inch ({v['scale_source']})")
        if v['dimension_axes']:
            by = collections.defaultdict(list)
            for d in v['dimension_axes']:
                a = v['axes']['horizontal'] if d.get('direction') == 'horizontal' else v['axes']['vertical'] if d.get('direction') == 'vertical' else d.get('direction', '?')
                by[a].append(f"{d['id']}={d.get('value_in', '?')}" + (f" x{d['count']}" if d.get('count') else ''))
            L.append('- dimensions by axis: ' + '; '.join(f"{a}: {', '.join(x)}" for a, x in sorted(by.items())))
        for f in v['features']:
            bits = [f"{f['type']}", 'diameters ' + ' / '.join(str(d) for d in f['diameters_in'])]
            for k, lab in (('countersink_diameter_in', 'countersink diameter'), ('counterbore_diameter_in', 'counterbore diameter'), ('hole_diameter_in', 'hole diameter'), ('depth_in', 'depth'), ('outer_step_length_in', 'outer step length'), ('inner_step_length_in', 'inner step length'), ('thread', 'thread')):
                if k in f: bits.append(f'{lab} {f[k]}')
            bits.append(f"from the left/right edge {f['from_left_in']}/{f['from_right_in']}, from the bottom/top {f['from_bottom_in']}/{f['from_top_in']}")
            if f['callout']: bits.append(f"callout '{f['callout']}'")
            L.append('- ' + '; '.join(bits))
        for sl in v['slots']: L.append(f"- slot: width {sl['width_in']}, centres {sl['length_between_centres_in']} apart, along {sl['orientation']}")
        if v['fillets']: L.append('- fillet radii: ' + ', '.join(f"{f['radius_in']} in x{f['count']}" for f in v['fillets']))
        if v['chamfers']: L.append('- chamfers: ' + '; '.join(v['chamfers']))
        L.append('')
    L += ['## Zones', '', '| zone | class | views | dimensions by axis | holes | fillet radii | slots |', '|---|---|---|---|---|---|---|']
    for n, zz in z['zones'].items():
        b = zz.get('blocks') or {}
        if not b.get('views') and zz['class'] in ('empty',): continue
        L.append(f"| {n} | {zz['class']} | {'; '.join(b.get('views', []))} | {b.get('dimension_axes', '')} | {b.get('holes', '')} | {b.get('fillet_radii_in', '')} | {b.get('slots', '') or ''} |")
    return '\n'.join(L) + '\n'


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0].startswith('-'): raise SystemExit(__doc__)
    opt = lambda n, d=None: a[a.index(n) + 1] if n in a else d
    z = read_zones(a[0], int(a[1]), opt('--projection', 'third'), opt('--unit'), '--no-virtual-grid' not in a)
    if opt('--json'): json.dump(z, open(opt('--json'), 'w'), indent=1, default=float)
    if opt('--md'): open(opt('--md'), 'w').write(blocks_markdown(z, os.path.basename(a[0]) + ' page ' + a[1]))
    if opt('--crops'):
        os.makedirs(opt('--crops'), exist_ok=True); dpi = int(opt('--dpi', 200))
        for v in z['views']: render_crop(a[0], int(a[1]), [v['box'][0] - 150, v['box'][1] - 110, v['box'][2] + 150, v['box'][3] + 110], dpi, os.path.join(opt('--crops'), f"{v['id']}_{v['range'].replace('+', '_')}.png"))
        for t in z['tables']: render_crop(a[0], int(a[1]), t['box'], dpi, os.path.join(opt('--crops'), f"{t['kind'].replace(' ', '_')}.png"))
    g = z['grid']; print(f"sheet {z['sheet_pt']}, grid {('%d columns x %d rows%s' % (len(g['columns']), len(g['rows']), ' (virtual: the sheet has no zone labels)' if g.get('virtual') else '')) if g else 'none'}, {len(z['views'])} views, {len(z['tables'])} tables, {z['circles']} circles, {z['dimensions']} dimensions")
    for v in z['views']:
        print(f"  {v['id']} {v['range']:10s} {v['role'][:26]:26s} axes {v['axes']['horizontal']}/{v['axes']['vertical']} scale {str(v['scale_pt_per_in']):6s} ({v['scale_source'][:14]}) dims {len(v['dims']):2d} title {v['title']!r}")
        da = collections.Counter(d['direction'] for d in v['dimension_axes']); print(f"      dimensions: {dict(da)}")
        for f in v['features']: print('      ' + ' '.join(f"{k}={f[k]}" for k in ('type', 'diameters_in', 'depth_in', 'through', 'thread', 'countersink_diameter_in', 'counterbore_diameter_in', 'outer_step_length_in', 'inner_step_length_in', 'callout') if k in f and f[k] not in ('', None)))
        for sl in v['slots']: print(f"      slot width {sl['width_in']} in, centres {sl['length_between_centres_in']} in apart, along {sl['orientation']}")
        if v['fillets']: print('      fillet/arc radii (in): ' + ', '.join(f"{f['radius_in']} x{f['count']}" for f in v['fillets']))
        if v['chamfers']: print('      chamfer callouts: ' + '; '.join(v['chamfers']))
    for t in z['tables']: print(f"  table {t['kind']:15s} {t['range']}")
    cls = collections.Counter(zz['class'] for zz in z['zones'].values()); print('  zone classes', dict(cls))


if __name__ == '__main__':
    main()
