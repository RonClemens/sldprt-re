#!/usr/bin/env python3
"""Why does a part render more often than its BOM quantity says? Reads an assembly's component tree (the instance list `plan` is built from,
BEFORE any same-pose collapse) and reports, per part, how its instances differ. Masked by construction: parts are P1.. (most instances
first), parent assemblies A1.., no file or part names are printed.
usage: plan_dups.py <assembly>.SLDASM [--top 5] [--json]
Per part: instances, distinct poses (translation to 1 mm + rotation), distinct parents, distinct configurations, same-pose extras
(instances minus distinct pose+configuration), hidden and suppressed counts, then the first 12 instances.
Reading: same_pose_extras == instances-1 -> duplicates a collapse rule can hide; distinct_poses == instances (under one parent or several) ->
placed several times (the BOM quantity is per assembly); distinct_configs > 1 -> configurations; anything else -> look at the instances."""
import argparse, collections, hashlib, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'src'))

def pose_key(M):
    """(translation in mm rounded to 1 mm, rotation label): 'identity' or a short hash of the rotation rounded to 1e-4."""
    t = tuple(int(round(float(x) * 1000.0)) for x in M[3][:3])
    R = [round(float(M[i][j]), 4) + 0.0 for i in range(3) for j in range(3)]
    ident = all(abs(R[i * 3 + j] - (1.0 if i == j else 0.0)) < 1e-4 for i in range(3) for j in range(3))
    return t, 'identity' if ident else hashlib.sha1(repr(R).encode()).hexdigest()[:6]

def analyse(instances, top=5):
    """instances: dicts with path ('A<1>/B<2>/part<3>'), part, M (4x4, row-vector, metres), cfg, suppressed, hidden; assemblies are skipped.
    Returns [{label, instances, distinct_poses, distinct_parents, distinct_configs, same_pose_extras, hidden, suppressed, first}] most-repeated first."""
    by = collections.defaultdict(list)
    for it in instances:
        if it.get('assembly') or it.get('part') is None: continue
        by[it['part']].append(it)
    parents = {}                                           # parent path -> A<k>, in order of first appearance
    def parent_label(path):
        p = '/'.join(path.split('/')[:-1])
        return parents.setdefault(p, 'A%d' % (len(parents) + 1) if p else 'A0')
    ranked = sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    out = []
    for rank, (_, its) in enumerate(ranked[:top], 1):
        poses = [pose_key(i['M']) for i in its]
        pose_cfg = {(p, i.get('cfg')) for p, i in zip(poses, its)}
        first = []
        for i, p in list(zip(its, poses))[:12]:
            tail = i['path'].split('/')[-1]; num = tail[tail.rfind('<') + 1:-1] if tail.endswith('>') and '<' in tail else ''
            first.append(dict(parent=parent_label(i['path']), translation_mm=list(p[0]), rotation=p[1], n=num, config=i.get('cfg') or '', hidden=bool(i.get('hidden')), suppressed=bool(i.get('suppressed'))))
        out.append(dict(label='P%d' % rank, instances=len(its), distinct_poses=len(set(poses)), distinct_parents=len({parent_label(i['path']) for i in its}),
                        distinct_configs=len({i.get('cfg') for i in its}), same_pose_extras=len(its) - len(pose_cfg),
                        hidden=sum(bool(i.get('hidden')) for i in its), suppressed=sum(bool(i.get('suppressed')) for i in its), first=first))
    return out

def classify(r):
    if r['instances'] > 1 and r['same_pose_extras'] == r['instances'] - 1: return '(b) duplicates a collapse rule can hide'
    if r['distinct_configs'] > 1: return '(b) configurations'
    if r['distinct_poses'] == r['instances']: return '(a) placed several times, each at its own pose (BOM qty is per assembly)'
    return '(c) mixed: read the instances'

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0]); ap.add_argument('assembly'); ap.add_argument('--top', type=int, default=5); ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    from sldcont import read_streams
    from sldasm2glb import parse_tree, flatten_instances, find_root
    st = read_streams(a.assembly)
    if 'swXmlContents/COMPINSTANCETREE' not in st: sys.exit('no COMPINSTANCETREE stream (older OLE2 SolidWorks file?)')
    _, models, _ = parse_tree(st['swXmlContents/COMPINSTANCETREE'])
    root = find_root(models, os.path.basename(a.assembly))
    rows = analyse(flatten_instances(models, root['id']), a.top)
    for r in rows: r['reading'] = classify(r)
    if a.json: print(json.dumps(rows, indent=1)); return
    for r in rows:
        print('%s: instances=%d distinct_poses=%d distinct_parents=%d distinct_configs=%d same_pose_extras=%d hidden=%d suppressed=%d  => %s' % (r['label'], r['instances'], r['distinct_poses'], r['distinct_parents'], r['distinct_configs'], r['same_pose_extras'], r['hidden'], r['suppressed'], r['reading']))
        for f in r['first']: print('    %s  t=%s mm  rot=%s  <%s>  cfg=%s%s%s' % (f['parent'], f['translation_mm'], f['rotation'], f['n'], f['config'] or '-', '  hidden' if f['hidden'] else '', '  suppressed' if f['suppressed'] else ''))
if __name__ == '__main__': main()
