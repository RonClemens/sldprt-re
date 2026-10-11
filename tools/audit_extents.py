#!/usr/bin/env python3
"""Self-audit: compare every part's mesh extent with the bounding box SolidWorks recorded for it in the assembly tree.
Parts whose mesh is much larger (stray geometry from a wrongly traced face) or much smaller (failed/truncated faces) are listed, weighted by instance count.
usage: audit_extents.py <extracted root>   (env SLD_TOL / SLDPRT_CACHE as for the converter)"""
import collections, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'src'))
import numpy as np
from sldcont import read_streams
from sldasm2glb import parse_tree, flatten_instances, find_root
from sldprt2glb import load_part
from batch import top_level
SCHEMA = os.path.join(ROOT, 'src', 'sch_13006_s_t.txt')
def main(root_dir):
    idx = {}
    for d, _, fs in os.walk(root_dir):
        for f in fs: idx.setdefault(f.lower(), os.path.join(d, f))
    asms = sorted(os.path.join(d, f) for d, _, fs in os.walk(root_dir) for f in fs if f.lower().endswith('.sldasm') and '__virtual__' not in d)
    mesh = {}; rows = []
    def mbox(part):
        k = part.lower() + '.sldprt'
        if k not in idx: return None
        if k not in mesh:
            try: P, N, I, c, fails = load_part(idx[k], SCHEMA); mesh[k] = (P.min(0), P.max(0), len(fails))
            except Exception: mesh[k] = None
        return mesh[k]
    for a in top_level(asms):
        st = read_streams(a, want=lambda n: n == 'swXmlContents/COMPINSTANCETREE'); files, models, _ = parse_tree(st['swXmlContents/COMPINSTANCETREE']); root = find_root(models, os.path.basename(a))
        byfile = {m['file'].rsplit('.', 1)[0]: m['bbox'] for m in models.values() if m['file'] and m['bbox']}
        for i in flatten_instances(models, root['id']):
            if i.get('assembly') or i['suppressed'] or i['hidden']: continue
            b = byfile.get(i['part']); mb = mbox(i['part'])
            if b is not None and mb is not None: rows.append((i['part'], np.array(b[3:]) - np.array(b[:3]), mb[1] - mb[0], mb[2]))
    byp = collections.defaultdict(list)
    for r in rows: byp[r[0]].append(r)
    bad = []
    for p, rs in byp.items():
        _, decl, got, fl = rs[0]; ratio = np.where(decl > 1e-6, got / np.maximum(decl, 1e-9), 1.0)
        sh = float(np.max(np.maximum(1 - ratio, 0))); ex = float(np.max(np.maximum(ratio - 1, 0)))
        if sh > 0.05 or ex > 0.05: bad.append((len(rs) * max(sh, ex), p, len(rs), sh, ex, decl, got, fl))
    bad.sort(reverse=True); tot = sum(len(v) for v in byp.values()); n = sum(b[2] for b in bad)
    print(f'{len(bad)} of {len(byp)} parts differ from the recorded bounding box by >5%, covering {n} of {tot} instances ({100*n/max(tot,1):.1f}%)')
    for _, p, k, sh, ex, decl, got, fl in bad[:25]: print(f'  x{k:3} short {sh*100:4.0f}% over {ex*100:5.0f}% failed_faces {fl:3}  recorded {np.round(decl*1e3).astype(int)} mm  mesh {np.round(got*1e3).astype(int)} mm  {p[:50]}')
if __name__ == '__main__': main(sys.argv[1])
