#!/usr/bin/env python3
"""Zone index of a whole drawing package: for every page the zone grid, the views with the zones they span, the tables, and the class of every zone (sheet_zones.read_zones).

  python3 tools/tdp_zone_survey.py PACKAGE.pdf OUT.json [--md OUT.md] [--workers 4] [--timeout 240] [--pages 1-40]

The JSON holds, per page, the grid labels, the views (box, zone range such as F8-D7, type, dimension ids, title, scale) and the zone classes; it carries drawing text (view titles), keep it where the drawings may be kept. The printed summary is counts only.
"""
import json, os, sys, collections
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'src'))


def one(a):
    pdf, page = a
    import sheet_zones as Z
    try:
        z = Z.read_zones(pdf, page)
    except Exception as e: return dict(page=page, error=f'{type(e).__name__}: {str(e)[:100]}')
    roles = collections.Counter(v['role'].split(' (')[0] for v in z['views']); holes = collections.Counter(f['type'] for v in z['views'] for f in v['features']); fil = collections.Counter(f['radius_in'] for v in z['views'] for f in v['fillets'] for _ in range(f['count']))
    return dict(page=page, roles=dict(roles), hole_types=dict(holes), fillet_radii_in={str(k): n for k, n in fil.items()}, slots=sum(len(v['slots']) for v in z['views']), chamfers=sum(len(v['chamfers']) for v in z['views']), dimension_directions=dict(collections.Counter(d.get('direction') for v in z['views'] for d in v['dimension_axes'])), grid=z['grid'] and dict(columns=z['grid']['columns'], rows=z['grid']['rows']), views=[dict(id=v['id'], range=v['range'], type=v['type'], dims=len(v['dims']), circles=v['circles'], title=v['title'], scale=v['scale']) for v in z['views']],
                tables=[dict(kind=t['kind'], range=t['range']) for t in z['tables']], zone_classes=dict(collections.Counter(zz['class'] for zz in z['zones'].values())), zones=len(z['zones']))


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0].startswith('-'): raise SystemExit(__doc__)
    opt = lambda n, d=None: a[a.index(n) + 1] if n in a else d
    from pdfminer.pdfpage import PDFPage
    with open(a[0], 'rb') as f: n = sum(1 for _ in PDFPage.get_pages(f))
    pages = list(range(1, n + 1))
    if opt('--pages'): lo, _, hi = opt('--pages').partition('-'); pages = list(range(int(lo), int(hi or lo) + 1))
    res = []
    with Pool(int(opt('--workers', 4))) as pool:
        asy = [(p, pool.apply_async(one, ((a[0], p),))) for p in pages]
        for p, r in asy:
            try: res.append(r.get(timeout=float(opt('--timeout', 240))))
            except Exception as e: res.append(dict(page=p, error=type(e).__name__))
    ok = [r for r in res if 'error' not in r]; zc = collections.Counter()
    for r in ok: zc.update(r['zone_classes'])
    roles = collections.Counter(); ht = collections.Counter(); fr = collections.Counter(); dd = collections.Counter()
    for r in ok: roles.update(r['roles']); ht.update(r['hole_types']); fr.update(r['fillet_radii_in']); dd.update(r['dimension_directions'])
    S = dict(view_roles=dict(roles), hole_types=dict(ht), fillet_radii_in=dict(fr), slots=sum(r['slots'] for r in ok), chamfer_items=sum(r['chamfers'] for r in ok), dimension_directions=dict(dd), pages=len(res), errors=len(res) - len(ok), with_grid=sum(1 for r in ok if r['grid']), grids=dict(collections.Counter(f"{len(r['grid']['columns'])}x{len(r['grid']['rows'])}" for r in ok if r['grid'])), views=sum(len(r['views']) for r in ok), zone_classes=dict(zc),
             pages_with_bom=sum(1 for r in ok if any(t['kind'] == 'BOM table' for t in r['tables'])), pages_with_title_block=sum(1 for r in ok if any(t['kind'] == 'title block' for t in r['tables'])))
    json.dump(dict(summary=S, pages=res), open(a[1], 'w'), indent=1)
    if opt('--md'):
        L = ['# Zone index', '', json.dumps(S), '', '| page | grid | views (zones) | tables | zone classes |', '|---|---|---|---|---|']
        for r in res:
            if 'error' in r: L.append(f"| {r['page']} | | error {r['error']} | | |"); continue
            L.append(f"| {r['page']} | {('%sx%s' % (len(r['grid']['columns']), len(r['grid']['rows']))) if r['grid'] else '-'} | " + '; '.join(f"{v['id']} {v['range']} {v['type'][:5]}{' ' + v['title'] if v['title'] else ''}" for v in r['views'])[:260] + ' | ' + '; '.join(f"{t['kind']} {t['range']}" for t in r['tables']) + ' | ' + ', '.join(f'{k} {n}' for k, n in sorted(r['zone_classes'].items())) + ' |')
        open(opt('--md'), 'w').write('\n'.join(L) + '\n')
    print(json.dumps(S))


if __name__ == '__main__':
    main()
