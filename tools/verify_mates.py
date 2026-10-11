#!/usr/bin/env python3
"""Numerically check decoded mates against the placed assembly (SolidWorks places components so their mates hold, so a decoded
mate that does not hold means the wrong entity was picked). usage: verify_mates.py <folder or zip-extract root> [-o mates_check.json]
Checks: concentric = axes coincide; coincident/parallel planes = parallel (+ coplanar for coincident); distance = plane gap equals D1."""
import argparse, collections, json, math, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'src'))
import numpy as np
from mates_resolve import resolve
SCHEMA = os.path.join(ROOT, 'src', 'sch_13006_s_t.txt')
from mates_check import judge, unit
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('-o', '--out', default='mates_check.json'); a = ap.parse_args()
    from virtualcomp import materialize    # virtual / imported components are embedded in their assemblies: write them out so mates on them can be resolved
    materialize(a.root, log=print)
    os.environ['SLD_SEARCH_ROOT'] = os.pathsep.join(d for d in (a.root, os.path.join(a.root, '__virtual__')) if os.path.isdir(d))
    asms = sorted(os.path.join(d, f) for d, _, fs in os.walk(a.root) for f in fs if f.lower().endswith('.sldasm') and '__virtual__' not in d)
    out = {}; dis = collections.Counter(); tot = collections.Counter(); bytype = collections.defaultdict(collections.Counter)
    for asm in asms:
        try: mates, inst, models, root = resolve(asm, os.path.dirname(asm), SCHEMA)
        except Exception as e: print(f'{os.path.basename(asm):34} cannot resolve: {repr(e)[:60]}'); continue
        rows = []
        for m in mates:
            st, det = judge(m); rows.append(dict(name=m['name'], type=m['type'], status=st, detail=det, disambiguated=bool(m.get('disambiguated')), ambiguous=m.get('ambiguous_faces'))); tot[st] += 1; bytype[m['type']][st] += 1
            if m.get('disambiguated'): dis['faces chosen by the mate itself (not an independent check)'] += 1
            if m.get('ambiguous_faces') is not None: dis['id matches several faces, none/several satisfy it'] += 1
        c = collections.Counter(r['status'] for r in rows); out[os.path.basename(asm)] = rows
        print(f'{os.path.basename(asm):34} mates {len(rows):4}  pass {c["pass"]:4}  fail {c["fail"]:3}  unchecked {c["unchecked"]:3}  undecoded {c["undecoded"]:3}  dangling {c["dangling"]:3}', flush=True)
    json.dump(out, open(a.out, 'w'), indent=1)
    print('\nTOTAL', dict(tot), dict(dis)); [print(f'  {t:12}', dict(c)) for t, c in sorted(bytype.items())]
if __name__ == '__main__': main()
