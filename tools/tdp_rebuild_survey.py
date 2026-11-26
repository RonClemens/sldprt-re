#!/usr/bin/env python3
"""Run drawing_part.measure over the detail-part pages of a drawing package and summarise what it can and cannot rebuild.

  python3 tools/tdp_rebuild_survey.py PACKAGE.pdf TDP_INDEX.json OUT.json [--md OUT.md] [--cad-dir DIR] [--workers 4] [--timeout 180]

TDP_INDEX.json is the output of src/tdp_index.py; the pages classed `detail-part` are measured. With --cad-dir, a page whose drawing number matches a part file there (<number>.SLDPRT, any case) is also compared with that part's exact
features (drawing_part.compare_cad). Per page it records: ok or the reason it failed, the inferred scale and whether it is ambiguous, stated / unconfirmed counts, holes found, thickness found, and the comparison's largest difference.
The output holds drawing numbers and measured values: keep it where the drawings may be kept. The printed summary is counts only.
"""
import json, os, sys, glob
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'src'))


def one(args):
    pdf, page, drawing, cad_dir = args
    import drawing_part as D
    out = dict(page=page, drawing=drawing)
    try:
        m = D.measure(pdf, page)
    except Exception as e:
        return dict(out, status='crashed', why=f'{type(e).__name__}: {str(e)[:120]}')
    if 'error' in m: return dict(out, status='not measured', why=m['error'])
    q = m['quantities']; out.update(status='measured', scale=m['scale_pt_per_in'], scale_ambiguous=m.get('scale_ambiguous'), counts=m['counts'], holes=len(m['holes']), has_thickness=any(x['what'].startswith('elevation height') for x in q),
                                     outline_in=[round((m['plan_outline_pt'][2] - m['plan_outline_pt'][0]) / m['scale_pt_per_in'], 2), round((m['plan_outline_pt'][3] - m['plan_outline_pt'][1]) / m['scale_pt_per_in'], 2)], outline_pt=[round(m['plan_outline_pt'][2] - m['plan_outline_pt'][0]), round(m['plan_outline_pt'][3] - m['plan_outline_pt'][1])], rounded_corners=len(m.get('rounded_corners', [])), steps_found=sum(1 for h in m['holes'] for s in h['steps'] if s.get('value_in')), not_measured=m['not_measured'][:1])
    if cad_dir and drawing:
        cand = [f for f in glob.glob(os.path.join(cad_dir, '*')) if os.path.basename(f).upper() == drawing.upper() + '.SLDPRT']
        if cand:
            try:
                from part_features import part_features
                c = D.compare_cad(m, part_features(cand[0]))
                if 'error' in c: out['compare'] = dict(error=c['error'])
                else:
                    big = [r for r in c['rows'] if r['delta'] is not None and abs(r['delta']) > 0.5]
                    out['compare'] = dict(rows=len(c['rows']), differ_over_0_5mm=len(big), max_abs_delta_mm=max([abs(r['delta']) for r in c['rows'] if r['delta'] is not None] + [0]), hole_position_error_mm=c['hole_position_error_mm'], worst=[(r['what'], r['delta'], r['status']) for r in sorted(big, key=lambda r: -abs(r['delta']))[:4]])
            except Exception as e: out['compare'] = dict(error=f'{type(e).__name__}: {str(e)[:100]}')
    return out


def main():
    a = sys.argv[1:]
    if len(a) < 3 or a[0].startswith('-'): raise SystemExit(__doc__)
    opt = lambda n, d=None: a[a.index(n) + 1] if n in a else d
    idx = json.load(open(a[1]))['pages']; pages = [(r['page'], r.get('drawing')) for r in idx if r.get('cls') == 'detail-part']
    jobs = [(a[0], p, d, opt('--cad-dir')) for p, d in pages]; res = []
    with Pool(int(opt('--workers', 4))) as pool:
        asyncs = [(j, pool.apply_async(one, (j,))) for j in jobs]
        for j, r in asyncs:
            try: res.append(r.get(timeout=float(opt('--timeout', 180))))
            except Exception as e: res.append(dict(page=j[1], drawing=j[2], status='timeout', why=type(e).__name__))
    summ = {}
    for r in res: summ[r['status']] = summ.get(r['status'], 0) + 1
    ok = [r for r in res if r['status'] == 'measured']
    S = dict(pages=len(res), status=summ, ambiguous_scale=sum(1 for r in ok if r.get('scale_ambiguous')), with_holes=sum(1 for r in ok if r['holes']), with_thickness=sum(1 for r in ok if r['has_thickness']),
             compared=sum(1 for r in ok if r.get('compare') and 'error' not in r['compare']), stated=sum(r['counts'].get('stated', 0) for r in ok), unconfirmed=sum(r['counts'].get('unconfirmed', 0) for r in ok))
    json.dump(dict(summary=S, pages=res), open(a[2], 'w'), indent=1, default=float)
    if opt('--md'):
        L = ['# Rebuild survey of the detail-part pages', '', json.dumps(S), '', '| page | drawing | status | scale | amb. | stated | unconfirmed | holes | thickness | vs CAD (max mm, >0.5 mm) | why |', '|---|---|---|---|---|---|---|---|---|---|---|']
        for r in res:
            c = r.get('compare') or {}
            L.append(f"| {r['page']} | {r.get('drawing') or ''} | {r['status']} | {r.get('scale', '')} | {'yes' if r.get('scale_ambiguous') else ''} | {r.get('counts', {}).get('stated', '')} | {r.get('counts', {}).get('unconfirmed', '')} | {r.get('holes', '')} | {'yes' if r.get('has_thickness') else ''} | "
                     + (f"{round(c['max_abs_delta_mm'], 3)}, {c['differ_over_0_5mm']}" if c and 'error' not in c else (c.get('error', '') if c else '')) + f" | {r.get('why', '')[:90]} |")
        open(opt('--md'), 'w').write('\n'.join(L) + '\n')
    print(json.dumps(S))


if __name__ == '__main__':
    main()
