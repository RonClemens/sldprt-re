"""Numeric check of one decoded mate against the placed assembly (shared by the resolver and tools/verify_mates.py)."""
import math
import numpy as np
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
