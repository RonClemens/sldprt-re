#!/usr/bin/env python3
"""Vector-PDF drawing reader against the synthetic drawing's ground truth (tools/make_test_drawing.py). Needs pdfminer.six. usage: python3 tests/test_pdf_drawing.py"""
import json, math, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
import make_test_drawing as G
from pdf_drawing import read_pdf, parse_dim_text
from drawing_ir import Drawing

PT = G.PT


def close(a, b, tol=1e-3): return a is not None and b is not None and abs(a - b) <= tol


def main():
    tmp = tempfile.mkdtemp(); pdf = os.path.join(tmp, 'd.pdf')
    pg = G.build(); G.write_pdf(pg, pdf); truth = pg.truth
    d = read_pdf(pdf); assert len(d.sheets) == 1; s = d.sheets[0]
    assert close(s.scale, truth['scale']) and s.scale_text == '1:2', (s.scale, s.scale_text)
    assert not s.warnings, s.warnings
    # views
    assert len(s.views) == len(truth['views']), [v.bbox for v in s.views]
    for tv in truth['views']:
        box = [x * PT for x in tv['bbox']]
        v = [v for v in s.views if all(close(a, b, 1.0) for a, b in zip(v.bbox, box))]
        assert v and v[0].label == tv['label'], (tv, [(x.bbox, x.label) for x in s.views])
    # dimensions: match by arrow tips
    assert len(s.dimensions) == len(truth['dimensions']), [(x.id, x.text) for x in s.dimensions]
    by_truth = {}
    for t in truth['dimensions']:
        tips = [[x * PT for x in p] for p in t['tips']]
        m = [r for r in s.dimensions if len(r.tips) == len(tips) and all(any(math.hypot(a[0] - b[0], a[1] - b[1]) < 0.6 for b in r.tips) for a in tips)]
        assert len(m) == 1, (t['id'], [r.text for r in m]); r = m[0]; by_truth[t['id']] = r.id
        assert r.kind == t['kind'] and r.style == t['style'], (t['id'], r.kind, r.style)
        assert close(r.value, t['value']), (t['id'], r.value)
        assert r.tol.kind == t['tol']['kind'], (t['id'], r.tol)
        if t['tol']['kind'] in ('bilateral', 'limits'): assert close(r.tol.upper, t['tol']['upper']) and close(r.tol.lower, t['tol']['lower']), (t['id'], r.tol)
        assert r.count == t.get('count'), (t['id'], r.count)
        assert close(r.drawn, t['drawn'], 0.01), (t['id'], r.drawn, t['drawn'])
        if t['style'] == 'paired': assert len(r.ext) == 2, (t['id'], r.ext)
        if t['kind'] in ('diameter', 'radius'): assert r.arc is not None and close(r.arc[2], t['arc'][2] * PT, 0.3), (t['id'], r.arc)
        assert r.view in [v.id for v in s.views], (t['id'], r.view)
        assert r.conf == 'ok'
    # the deliberately wrong dimension is the only one whose drawing disagrees with its text
    off = [r for r in s.dimensions if r.drawn_dev is not None and abs(r.drawn_dev) > 0.05]
    assert [r.id for r in off] == [by_truth['D4']] and close(off[0].drawn_dev, -5.0, 0.01), [(r.id, r.drawn_dev) for r in off]
    # datum, GD&T frame, notes
    assert [x.letter for x in s.datums] == [t['letter'] for t in truth['datums']]
    assert len(s.gdt) == 1; g, tg = s.gdt[0], truth['gdt'][0]
    assert g.cells == tg['cells'] and close(g.value, tg['value']) and g.diameter_zone == tg['diameter_zone'] and g.modifiers == tg['modifiers'] and g.datums == tg['datums'], g
    assert g.attached_to == by_truth[tg['attached_to']], (g.attached_to, by_truth)
    texts = {n.text: n.kind for n in s.notes}
    for tn in truth['notes']: assert texts.get(tn['text']) == tn['kind'], (tn, texts)
    # text parsing
    for txt, want in (('2X Ø8 THRU', dict(kind='diameter', value=8.0, count=2, suffix=['THRU'])), ('R5', dict(kind='radius', value=5.0)), ('45°', dict(kind='angle', value=45.0)),
                      ('100 ±0.1', dict(kind='linear', value=100.0, tol=(0.1, -0.1))), ('(12.5)', dict(value=12.5, reference=True)), ('[60]', dict(value=60.0, basic=True)), ('4 PLCS', None), ('hello', None)):
        p = parse_dim_text(txt)
        if want is None: assert p is None or txt == '4 PLCS', (txt, p); continue
        for k, v in want.items(): assert p[k] == v, (txt, k, p)
    # IR round trip
    d2 = Drawing.from_dict(json.loads(json.dumps(d.to_dict()))); assert json.dumps(d2.to_dict(), sort_keys=True) == json.dumps(d.to_dict(), sort_keys=True)
    # a page with no text must warn, not crash
    blank = os.path.join(tmp, 'blank.pdf'); b = G.Page(); b.line(10, 10, 100, 100); G.write_pdf(b, blank)
    ws = read_pdf(blank).sheets[0]; assert ws.warnings and not ws.dimensions, ws.warnings
    print('OK:', len(s.dimensions), 'dimensions,', len(s.views), 'views,', len(s.datums), 'datum,', len(s.gdt), 'GD&T frame')


main()
