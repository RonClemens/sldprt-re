"""Masked instance-plan diagnostic: why does a part show more instances than its BOM quantity?
usage: python tools/plan_dups.py <assembly.SLDASM> [--top N]
Prints only masked structure (P1.. / A1.. labels, counts, rounded poses); no names, no file contents."""
import sys, os, re, hashlib, argparse, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import numpy as np
from sldcont import read_streams
from sldasm2glb import parse_tree, flatten_instances, find_root

def pose_key(M):
    M = np.asarray(M, float).reshape(4, 4)
    t = tuple(int(round(x * 1000)) for x in M[3, :3])                       # translation, 1 mm (model units are metres)
    R = M[:3, :3]; rot = 'identity' if np.allclose(R, np.eye(3), atol=1e-6) else hashlib.sha1(np.round(R, 5).tobytes()).hexdigest()[:6]
    return t, rot


def analyse(inst, top=3):
    """inst: flatten_instances rows. Returns masked report lines."""
    parts = [i for i in inst if i.get('part')]
    byp = collections.defaultdict(list)
    for i in parts: byp[i['part']].append(i)
    ranked = sorted(byp.items(), key=lambda kv: -len(kv[1]))[:top]
    out = []; amap = {}
    for pn, (_, L) in enumerate(ranked, 1):
        poses = collections.Counter(pose_key(i['M']) for i in L)
        parents = collections.Counter('/'.join(i['path'].split('/')[:-1]) for i in L)
        nums = collections.Counter(re.search(r'<(\d+)>$', i['path'].rsplit('/', 1)[-1]).group(1) for i in L)
        cfgs = collections.Counter(str(i.get('cfg')) for i in L)
        for p in parents: amap.setdefault(p, 'A%d' % (len(amap) + 1))
        same = sum(c - 1 for c in poses.values())
        out.append('P%d: instances=%d distinct_poses=%d distinct_parents=%d distinct_configs=%d same_pose_extras=%d hidden=%d suppressed=%d'
                   % (pn, len(L), len(poses), len(parents), len(cfgs), same, sum(1 for i in L if i['hidden']), sum(1 for i in L if i['suppressed'])))
        out.append('   name pattern: P%d<n> (n = %d distinct values, %s)' % (pn, len(nums), 'contiguous' if sorted(map(int, nums)) == list(range(1, len(nums) + 1)) else 'gaps'))    # structure only, never the real name
        for k, i in enumerate(L[:12]):
            t, rot = pose_key(i['M'])
            out.append('   #%d parent=%s t_mm=%s rot=%s num=%s' % (k, amap.get('/'.join(i['path'].split('/')[:-1]), 'A0(root)') if '/' in i['path'] else 'A0(root)', t, rot, re.search(r'<(\d+)>$', i['path']).group(1)))
    return out

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('sldasm'); ap.add_argument('--top', type=int, default=3); a = ap.parse_args()
    st = read_streams(a.sldasm); files, models, _ = parse_tree(st['swXmlContents/COMPINSTANCETREE'])
    root = find_root(models, os.path.basename(a.sldasm))
    inst = flatten_instances(models, root['id'])
    print('\n'.join(analyse(inst, a.top)))
