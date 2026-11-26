"""Scale of an assembly sheet from the parts it shows, taken from their detail drawings.

  estimate(pdf, page, refs)            refs: {drawing number: {sizes_in: {X, Y, Z}, confirmed: [axes]}} from tools/part_reference_sizes.py
  -> dict(parts, scale_pt_per_in, support, ...)

A part whose detail drawing gives confirmed overall sizes is found on the assembly sheet through the parts list (an item row that says `SEE DWG <number>`) and the item balloon on the view (a circle with the item number, a leader line from its edge to the part).
The drawn length of that part is looked for in the linework around the leader's end: the box of the connected object linework there, and the long horizontal and vertical lines. Every drawn length divided by every confirmed size is a possible scale (pt per inch); the scale that
the most distinct parts agree on (within `tol`) wins, so one part gives a weak estimate and each further part that lands on the same scale makes it stronger. The result lists which part and which length voted, and compares with the printed `SCALE a : b` when the sheet has one.
Only sizes the detail drawing states count as reference; sizes it merely shows are not used, or the scale would be circular. The estimate is `unconfirmed (inferred)` until a person confirms it.
"""
import math, re
import numpy as np


def bom_rows(sheet):
    """parts list rows: {item: dict(qty, name, dwg)} from notes on one baseline: `3.1 1` (item, qty), a name, and `SEE DWG nnnnnnnn`"""
    byy = {}
    for n in sheet.notes:
        if n.pos: byy.setdefault(round(n.pos[1] / 3), []).append(n)
    rows = {}
    for k, ns in byy.items():
        ns = sorted(ns, key=lambda n: n.pos[0])
        for n in ns:
            m = re.fullmatch(r'(\d+(?:\.\d+)*)\s+(\d+)', n.text.strip())
            if not m or n.size > 11: continue
            line = [x for x in byy.get(k, []) + byy.get(k - 1, []) + byy.get(k + 1, []) if abs(x.pos[1] - n.pos[1]) < 3]
            txt = ' '.join(x.text.strip() for x in sorted(line, key=lambda x: x.pos[0]))
            d = re.search(r'SEE\s+DWG\.?\s*(\d{6,})', txt.upper()); name = next((x.text.strip() for x in sorted(line, key=lambda x: x.pos[0]) if x.pos[0] > n.pos[0] + 40 and x.text.strip() not in ('-',)), '')
            rows[m.group(1)] = dict(qty=int(m.group(2)), name=name, dwg=d.group(1) if d else None, y=n.pos[1])
    return rows


def balloons(sheet, C, S, items, r_text=17.0):
    """item balloons: a circle (r 8 to 25 pt) around an item number, with a leader line from its edge -> [dict(item, centre, r, target)] (target: the far end of the leader)"""
    out = []; seen = set()
    for n in sheet.notes:
        t = n.text.strip()
        if not n.pos or t not in items or n.size < 11: continue
        cs = [c for c in C if 8 <= c['r'] <= 25 and np.hypot(c['cx'] - n.pos[0], c['cy'] - n.pos[1]) < r_text]
        if not cs: continue
        c = min(cs, key=lambda c: np.hypot(c['cx'] - n.pos[0], c['cy'] - n.pos[1])); key = (round(c['cx']), round(c['cy']))
        if key in seen: continue
        seen.add(key); best = None
        for s in S:
            for (x, y, u, v) in ((s[0], s[1], s[2], s[3]), (s[2], s[3], s[0], s[1])):
                if abs(math.hypot(x - c['cx'], y - c['cy']) - c['r']) < 1.3 and math.hypot(u - c['cx'], v - c['cy']) > c['r'] + 4:
                    L = math.hypot(u - x, v - y)
                    if best is None or L > best[0]: best = (L, (float(u), float(v)))
        if best: out.append(dict(item=t, centre=(float(c['cx']), float(c['cy'])), r=float(c['r']), target=best[1], leader_pt=round(best[0], 1)))
    return out


def _objw(S):
    ws, n = np.unique(np.round(S[:, 4], 2), return_counts=True)
    if not len(ws): return 0.0
    keep = ws[n >= 0.2 * n.max()]                                                     # the heaviest width that carries a real share of the linework (title-block frames and a few heavy lines do not)
    return float(keep.max())


def local_lengths(S, target, objw, R=200.0, tol=0.6):
    """drawn lengths near a leader end -> list of (length_pt, kind). kinds: `box width` / `box height` of the connected object linework at the target (whole when it is isolated), `line` for long horizontal and vertical runs in the window"""
    sel = S[S[:, 4] >= objw - 0.05]
    if not len(sel): return []
    tx, ty = target
    # the object segment nearest the leader end, then the connected component around it (joined where ends meet within tol)
    d0 = np.minimum(np.hypot(sel[:, 0] - tx, sel[:, 1] - ty), np.hypot(sel[:, 2] - tx, sel[:, 3] - ty)); dm = np.array([_ptseg(tx, ty, *s[:4]) for s in sel]) if len(sel) < 20000 else d0
    i0 = int(np.argmin(dm))
    if dm[i0] > 12: return []
    key = lambda x, y: (round(x / tol), round(y / tol)); adj = {}
    for i, s in enumerate(sel):
        for k in (key(s[0], s[1]), key(s[2], s[3])): adj.setdefault(k, []).append(i)
    seen = {i0}; stack = [i0]
    while stack:
        i = stack.pop(); s = sel[i]
        for k in (key(s[0], s[1]), key(s[2], s[3])):
            for j in adj.get(k, []):
                if j not in seen: seen.add(j); stack.append(j)
    comp = sel[sorted(seen)]; x0 = min(comp[:, 0].min(), comp[:, 2].min()); x1 = max(comp[:, 0].max(), comp[:, 2].max()); y0 = min(comp[:, 1].min(), comp[:, 3].min()); y1 = max(comp[:, 1].max(), comp[:, 3].max())
    out = [(x1 - x0, 'box width'), (y1 - y0, 'box height')]
    # long straight runs in the window
    H = {}; V = {}
    for a, b, c, d, w in sel:
        if max(abs(a - tx), abs(c - tx)) > R and min(abs(a - tx), abs(c - tx)) > R: continue
        if abs(b - d) < 0.1 and abs(b - ty) <= R: H.setdefault(round(b), []).append((min(a, c), max(a, c)))
        elif abs(a - c) < 0.1 and abs(a - tx) <= R: V.setdefault(round(a), []).append((min(b, d), max(b, d)))
    for dct, nm in ((H, 'line (horizontal)'), (V, 'line (vertical)')):
        for iv in dct.values():
            iv.sort(); cur = list(iv[0])
            for p, q in iv[1:] + [(1e18, 1e18)]:
                if p <= cur[1] + 1.0: cur[1] = max(cur[1], q)
                else:
                    if cur[1] - cur[0] >= 10: out.append((cur[1] - cur[0], nm))
                    cur = [p, q]
    return [(float(L), k) for L, k in out if L >= 10]


def _ptseg(px, py, a, b, c, d):
    dx, dy = c - a, d - b; L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - a) * dx + (py - b) * dy) / L2))
    return math.hypot(px - (a + t * dx), py - (b + t * dy))


def vote(parts, tol=0.02):
    """parts: [dict(item, drawing, sizes (axis -> inches), lengths [(pt, kind)])] -> clusters of candidate scale: [dict(scale, parts, votes)] best first"""
    cands = []
    for p in parts:
        for ax, d in p['sizes'].items():
            if d <= 0.05: continue
            for L, kind in p['lengths']:
                cands.append((math.log(L / d), p['item'], ax, kind, L, d))
    cands.sort()
    best = []
    for i, (lg, *_r) in enumerate(cands):
        grp = [c for c in cands[i:] if c[0] - lg <= math.log1p(tol) * 2]
        if not grp: continue
        items = {c[1] for c in grp}
        if len(items) < 1: continue
        m = float(np.median([c[0] for c in grp])); grp = [c for c in cands if abs(c[0] - m) <= math.log1p(tol)]; items = {c[1] for c in grp}
        w = sum(2 if c[3].startswith('box') else 1 for c in grp)
        best.append(dict(scale=math.exp(m), parts=sorted(items), votes=w, matches=[dict(item=c[1], axis=c[2], drawn_pt=round(c[4], 1), kind=c[3], size_in=c[5]) for c in grp][:12]))
    best.sort(key=lambda b: (-len(b['parts']), -b['votes']))
    out = []
    for b in best:
        if not any(abs(math.log(b['scale'] / o['scale'])) < math.log1p(tol) for o in out): out.append(b)
    return out[:5]


def estimate(pdf, page, refs, tol=0.02, expected=None):
    import pdf_drawing as P, drawing_part as D
    sheet = P.read_pdf(pdf, [page]).sheets[0]; S, geo = D.read_segments(pdf, page); C = D.circles(S); objw = _objw(S)
    rows = bom_rows(sheet); bl = balloons(sheet, C, S, set(rows))
    parts = []; skipped = []
    for b in bl:
        r = rows.get(b['item']); dwg = r and r['dwg']
        if not dwg: skipped.append(dict(item=b['item'], why='purchased or no detail drawing in the parts list')); continue
        ref = refs.get(dwg)
        if not ref or not ref.get('confirmed'): skipped.append(dict(item=b['item'], dwg=dwg, why='detail drawing gives no confirmed overall size' if ref else 'detail drawing not in the package')); continue
        sizes = {a: ref['sizes_in'][a] for a in ref['confirmed']}
        parts.append(dict(item=b['item'], drawing=dwg, name=r['name'], sizes=sizes, target=b['target'], lengths=local_lengths(S, b['target'], objw)))
    res = dict(page=page, parts=[dict(item=p['item'], drawing=p['drawing'], name=p['name'], confirmed_sizes_in=p['sizes'], leader_end=[round(x, 1) for x in p['target']], lengths_found=len(p['lengths'])) for p in parts], balloons=len(bl), skipped=skipped, rows=len(rows))
    cl = vote(parts, tol); res['candidates'] = cl
    if expected:                                                                      # the printed scale as a check: which confirmed sizes have a drawn length of that size near their balloon
        hits = []
        for p in parts:
            for ax, d in p['sizes'].items():
                for L, kind in p['lengths']:
                    if abs(L / d / expected - 1) <= 0.03: hits.append(dict(item=p['item'], drawing=p['drawing'], axis=ax, size_in=d, drawn_pt=round(L, 1), kind=kind)); break
        res['at_printed_scale'] = dict(scale_pt_per_in=expected, confirmed_by=hits, parts_with_a_match=sorted({h['item'] for h in hits}), parts_tried=[p['item'] for p in parts])
    if cl:
        top = cl[0]; res['scale_pt_per_in'] = round(top['scale'], 3); res['supporting_parts'] = top['parts']; res['status'] = 'unconfirmed (inferred from %d part%s: %s)' % (len(top['parts']), '' if len(top['parts']) == 1 else 's', ', '.join(top['parts']))
        res['ratio'] = '1 : %.2f' % (72.0 / top['scale']) if top['scale'] else None
    return res
