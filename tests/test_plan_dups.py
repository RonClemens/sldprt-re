#!/usr/bin/env python3
"""The #85 diagnostic: classes and masking (tools/plan_dups.py). usage: python3 tests/test_plan_dups.py"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'tools'))
import numpy as np
from plan_dups import analyse, classify, pose_key
def inst(path, part, tx=0.0, cfg='Default', hidden=False, suppressed=False):
    M = np.eye(4); M[3][:3] = [tx, 0, 0]
    return dict(path=path, part=part, M=M, cfg=cfg, hidden=hidden, suppressed=suppressed)
# (b) the same part stacked at one pose, three times
dup = [inst('ASM_SECRET<1>/BOLT_SECRET<%d>' % k, 'BOLT_SECRET') for k in (1, 2, 3)]
r = analyse(dup)[0]
assert (r['instances'], r['distinct_poses'], r['same_pose_extras']) == (3, 1, 2) and classify(r).startswith('(b) duplicates'), r
# (a) placed at three poses under three parents
many = [inst('A%d<1>/NUT_SECRET<1>' % k, 'NUT_SECRET', tx=0.1 * k) for k in (1, 2, 3)]
r = analyse(many)[0]
assert (r['distinct_poses'], r['distinct_parents'], r['same_pose_extras']) == (3, 3, 0) and classify(r).startswith('(a) placed'), r
# (a) also when all of them sit under one parent
solo = [inst('A<1>/RIVET_SECRET<%d>' % k, 'RIVET_SECRET', tx=0.1 * k) for k in (1, 2, 3, 4)]
assert classify(analyse(solo)[0]).startswith('(a) placed'), analyse(solo)
# configurations
cfg = [inst('A<1>/P<1>', 'P', cfg='Short'), inst('A<1>/P<2>', 'P', cfg='Long')]
assert classify(analyse(cfg)[0]).startswith('(b) configurations')
# 1 mm tolerance: 0.4 mm apart is one pose, 2 mm apart is two
assert pose_key(inst('x', 'p', tx=0.1000)[ 'M']) == pose_key(inst('x', 'p', tx=0.1004)['M']) and pose_key(inst('x', 'p', tx=0.1)['M']) != pose_key(inst('x', 'p', tx=0.102)['M'])
# masked: no name from the input appears anywhere in the output, labels are P/A
out = repr(analyse(dup + many))
assert 'SECRET' not in out and "'P1'" in out and "'A1'" in out, out
# assemblies are skipped, most-repeated first
mix = many + [dict(path='X<1>', part=None, assembly=True, M=np.eye(4), cfg='d', hidden=False, suppressed=False)] + dup
assert [x['instances'] for x in analyse(mix)] == [3, 3] and analyse(mix, top=1)[0]['label'] == 'P1'
print('plan_dups: all checks passed')
