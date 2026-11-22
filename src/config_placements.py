"""Per-configuration component placements of a SolidWorks assembly.

  config_deltas(path) -> {config_id: {instance name: (dx, dy, dz)}}     python3 src/config_placements.py ASSEMBLY.SLDASM

The assembly's component tree (swXmlContents/COMPINSTANCETREE) lists every configuration but stores the same placements for all of them. The placements that differ between configurations
(a piston at a different stroke, a flexible sub-assembly) are in the binary streams `Contents/Config-<id>`: an MFC archive whose `moAssembly_c` holds one `moReference_c` per component, each followed by
its transform as 12 doubles (a 3x3 rotation, then the translation, metres). A component that has no such block in a configuration is at its default placement.

This returns, for each configuration other than 0, the translation by which each component differs from configuration 0, in the assembly's own frame (the parent frame of the component), so that
a placement from the tree can be moved with `T' = T @ Translate(delta)` (row-vector convention). Rotations that differ between configurations are reported in `rotation_changes` and not applied.

Verified on one assembly (the Festo cylinder sub-assembly of the Hydro package: configurations Standard and Extended, one component moved by its 25.4 mm stroke). Unverified on any other file; configurations
driven by anything other than a moved component transform (suppression, dimension changes that rebuild features) are not covered.
"""
import os, re, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_INSTANCE = re.compile(r'.*[A-Za-z0-9\)]-\d+$')


def transform_blocks(d):
    """[(offset, R (3,3), t (3,))]: places where 12 consecutive doubles form a proper rotation followed by a translation under 1 m"""
    out = []
    for p in range(0, len(d) - 96):
        v = np.frombuffer(d[p:p + 96], '<f8')
        if not np.all(np.isfinite(v)): continue
        R = v[:9].reshape(3, 3)
        if np.all(np.abs(R) <= 1.0000001) and abs(R).sum() > 2.5 and np.allclose(R @ R.T, np.eye(3), atol=1e-6) and abs(np.linalg.det(R) - 1) < 1e-6 and np.all(np.abs(v[9:12]) < 1):
            out.append((p, R.copy(), v[9:12].copy()))
    return out


def component_transforms(d):
    """{instance name: (R, t)} for one Config stream: each block belongs to the component (an MFC string ending in -<n>) named most recently before it, within the references (before the units table)"""
    strs = [(m.start(), d[m.end():m.end() + 2 * m.group(1)[0]].decode('utf-16-le', 'ignore')) for m in re.finditer(rb'\xff\xfe\xff(.)', d, re.S)]
    names = [(o, s) for o, s in strs if _INSTANCE.match(s) and '\\' not in s]
    limit = len(d)
    m = re.search(rb'\xff\xff..[\x03-\x40]\x00moUnitsTable_c', d, re.S)
    if m: limit = m.start()
    out = {}
    for p, R, t in transform_blocks(d[:limit]):
        prev = [s for o, s in names if o < p]
        if prev and prev[-1] not in out: out[prev[-1]] = (R, t)
    return out


def deltas_between(base_stream, other_stream):
    """({instance: (dx, dy, dz)}, [instance names whose rotation differs]) between two Config streams"""
    base = component_transforms(base_stream); cur = component_transforms(other_stream); dd = {}; rr = []
    for name, (R, t) in cur.items():
        R0, t0 = base.get(name, (None, np.zeros(3)))
        if R0 is not None and not np.allclose(R, R0, atol=1e-6): rr.append(name)
        if np.linalg.norm(t - t0) > 1e-9: dd[name] = tuple(float(x) for x in (t - t0))
    return dd, rr


def config_deltas(path):
    from sldcont import read_streams
    st = read_streams(path)
    ids = sorted(int(m.group(1)) for k in st for m in [re.fullmatch(r'Contents/Config-(\d+)', k)] if m)
    if 0 not in ids: return dict(deltas={}, rotation_changes={})
    deltas = {}; rot = {}
    for cid in ids:
        if cid == 0: continue
        deltas[cid], rot[cid] = deltas_between(st['Contents/Config-0'], st[f'Contents/Config-{cid}'])
    return dict(deltas=deltas, rotation_changes=rot)


def main():
    if len(sys.argv) < 2: raise SystemExit(__doc__)
    r = config_deltas(sys.argv[1])
    for cid, dd in r['deltas'].items():
        print(f'configuration {cid}:', {k: tuple(round(x * 1000, 3) for x in v) for k, v in dd.items()} or 'no moved components', '(mm)', '| rotation changes:', r['rotation_changes'][cid] or 'none')


if __name__ == '__main__':
    main()
