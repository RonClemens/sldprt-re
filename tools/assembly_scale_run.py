#!/usr/bin/env python3
"""python3 tools/assembly_scale_run.py FULL.pdf INDEX.json REFS.json OUT.json [PAGE ...]   scale of each assembly page from the confirmed sizes of the parts it shows; printed `SCALE a:b` of the index is listed beside it"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import assembly_scale as A
pdf, idx, refs, out = sys.argv[1:5]; idx = json.load(open(idx)); refs = json.load(open(refs)); only = [int(x) for x in sys.argv[5:]]; res = {}
for p in idx['pages']:
    if p['cls'] != 'assembly' or (only and p['page'] not in only): continue
    import re
    m = re.fullmatch(r'(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)', (p.get('scale_text') or '').strip()); exp = 72.0 * float(m.group(1)) / float(m.group(2)) if m else None
    try: r = A.estimate(pdf, p['page'], refs, expected=exp)
    except Exception as e: r = dict(error=str(e)[:150])
    r['printed_scale'] = p.get('scale_text'); res[p['page']] = r
    top = (r.get('candidates') or [None])[0]
    print(p['page'], p['drawing'], 'printed', p.get('scale_text'), 'est', r.get('scale_pt_per_in'), r.get('ratio'), 'parts', r.get('supporting_parts'), 'of', [q['item'] + ':' + q['drawing'] for q in r.get('parts', [])], (r.get('at_printed_scale') or {}).get('parts_with_a_match'), r.get('error', ''), flush=True)
    json.dump(res, open(out, 'w'), indent=1, default=float)
