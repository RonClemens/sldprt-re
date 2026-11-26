#!/usr/bin/env python3
"""Confirmed overall sizes of the detail parts of a drawing package, for scaling its assembly sheets.

  python3 tools/part_reference_sizes.py FULL.pdf INDEX.json OUT.json

For each detail-part page of the package index, rebuild_from_blocks.build_spec reads the views; an overall size counts as *confirmed* only when its provenance says `stated` (a dimension on the sheet gives it), never when it was measured from the linework or assumed.
-> {drawing number: {page, unit, sizes_in: {X, Y, Z: value or null}, confirmed: [axes]}}
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))


def reference_sizes(pdf, index):
    import rebuild_from_blocks as R
    out = {}
    for p in index['pages']:
        if p['cls'] != 'detail-part' or not p.get('drawing'): continue
        try: sp = R.build_spec(pdf, p['page'])
        except Exception as e: out.setdefault(p['drawing'], dict(page=p['page'], error=str(e)[:120])); continue
        if 'provenance' not in sp: out.setdefault(p['drawing'], dict(page=p['page'], error=str(sp.get('error') or sp.get('reason') or list(sp))[:160])); continue
        st = {n['name'].split()[1]: n['status'] for n in sp['provenance'] if n['name'].startswith('overall ')}
        conf = [a for a in 'XYZ' if st.get(a) == 'stated']
        sz = dict(X=sp['X'], Y=sp['Y'], Z=sp['D'])
        out[p['drawing']] = dict(page=p['page'], unit=sp.get('unit_guess'), sizes_in={a: round(float(sz[a]), 4) for a in 'XYZ'}, confirmed=conf, scale_source={v: w['scale_source'] for v, w in sp['views'].items()})
    return out


if __name__ == '__main__':
    pdf, idx, out = sys.argv[1:4]
    r = reference_sizes(pdf, json.load(open(idx))); json.dump(r, open(out, 'w'), indent=1)
    for k, v in r.items(): print(k, v.get('page'), v.get('confirmed'), v.get('sizes_in'), v.get('error', ''))
