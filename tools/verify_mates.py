#!/usr/bin/env python3
"""Numerically check decoded mates against the placed assembly (SolidWorks places components so their mates hold, so a decoded
mate that does not hold means the wrong entity was picked). usage: verify_mates.py <folder or zip-extract root> [-o mates_check.json]
Checks: concentric = axes coincide; coincident/parallel planes = parallel (+ coplanar for coincident); distance = plane gap equals D1."""
import argparse, collections, json, math, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'src'))
import numpy as np
from mates_resolve import resolve
SCHEMA = os.path.join(ROOT, 'src', 'sch_13006_s_t.txt')
def unit(v): return v / np.linalg.norm(v)
def judge(m, tol_mm=0.05, tol_deg=0.05):
    """-> (status, detail); status in pass / fail / unchecked / undecoded"""
    ents = m.get('entities') or []; f = [e.get('face') for e in ents]
    if len(f) != 2 or not all(f): return 'undecoded', f'{len(f)} entities, faces {sum(1 for x in f if x)}'
    if not all('point' in x and 'dir' in x for x in f): return 'unchecked', 'non-analytic face'
    t = m['type']; a, b = f; d1, d2 = unit(a['dir']), unit(b['dir']); ang = math.degrees(math.acos(min(1, abs(float(d1 @ d2)))))
    if t == 'concentric':
        c = np.cross(d1, d2); off = np.linalg.norm(np.cross(b['point'] - a['point'], d1)) if np.linalg.norm(c) < 1e-9 else abs((b['point'] - a['point']) @ unit(c))
        ok = off * 1e3 < tol_mm and ang < tol_deg; return ('pass' if ok else 'fail'), f'axis offset {off*1e3:.3f} mm, misalignment {ang:.3f} deg'
    if t in ('coincident', 'parallel', 'distance') and a.get('kind') == b.get('kind') == 'PLANE':
        gap = abs(float((b['point'] - a['point']) @ d1))
        if t == 'parallel': return ('pass' if ang < tol_deg else 'fail'), f'angle {ang:.3f} deg'
        if t == 'coincident': return ('pass' if ang < tol_deg and gap * 1e3 < tol_mm else 'fail'), f'angle {ang:.3f} deg, gap {gap*1e3:.3f} mm'
        D = (m.get('params') or {}).get('D1')
        if D is None: return 'unchecked', 'no distance value'
        return ('pass' if ang < tol_deg and abs(gap - abs(D)) * 1e3 < tol_mm else 'fail'), f'gap {gap*1e3:.3f} mm vs stored {abs(D)*1e3:.3f} mm'
    return 'unchecked', t
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('-o', '--out', default='mates_check.json'); a = ap.parse_args()
    os.environ['SLD_SEARCH_ROOT'] = os.pathsep.join(d for d in (a.root, os.path.join(a.root, '__virtual__')) if os.path.isdir(d))
    asms = sorted(os.path.join(d, f) for d, _, fs in os.walk(a.root) for f in fs if f.lower().endswith('.sldasm') and '__virtual__' not in d)
    out = {}; tot = collections.Counter(); bytype = collections.defaultdict(collections.Counter)
    for asm in asms:
        try: mates, inst, models, root = resolve(asm, os.path.dirname(asm), SCHEMA)
        except Exception as e: print(f'{os.path.basename(asm):34} cannot resolve: {repr(e)[:60]}'); continue
        rows = []
        for m in mates:
            st, det = judge(m); rows.append(dict(name=m['name'], type=m['type'], status=st, detail=det)); tot[st] += 1; bytype[m['type']][st] += 1
        c = collections.Counter(r['status'] for r in rows); out[os.path.basename(asm)] = rows
        print(f'{os.path.basename(asm):34} mates {len(rows):4}  pass {c["pass"]:4}  fail {c["fail"]:3}  unchecked {c["unchecked"]:3}  undecoded {c["undecoded"]:3}', flush=True)
    json.dump(out, open(a.out, 'w'), indent=1)
    print('\nTOTAL', dict(tot)); [print(f'  {t:12}', dict(c)) for t, c in sorted(bytype.items())]
if __name__ == '__main__': main()
