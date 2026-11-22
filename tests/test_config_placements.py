#!/usr/bin/env python3
"""Per-configuration component placements (src/config_placements.py) on synthetic Config streams: references named `<part>-<n>`, each followed by its transform (3x3 rotation, translation) as 12 doubles. usage: python3 tests/test_config_placements.py"""
import os, struct, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import config_placements as C


def mfc(s): return b'\xff\xfe\xff' + bytes([len(s)]) + s.encode('utf-16-le')
def block(R, t): return np.concatenate([np.asarray(R, float).reshape(-1), np.asarray(t, float)]).astype('<f8').tobytes()
RX = [[0, 0, -1], [0, -1, 0], [-1, 0, 0]]; I3 = np.eye(3)


def stream(entries):
    out = b'\xff\xff\x01\x00\x0cmoAssembly_c\x00\x00' + b'\x00' * 20
    for name, R, t in entries:
        out += mfc(name) + b'\x00' * 30 + (block(R, t) if R is not None else b'') + b'\x00' * 16 + mfc('Standard') + b'\x00' * 10
    return out + b'\xff\xff\x01\x00\x0emoUnitsTable_c\x00\x00' + block(I3, [0.5, 0, 0])                 # a block after the units table is not a component placement


def main():
    c0 = stream([('Rod-1', None, None), ('Pin-1', I3, [0.0, 0.0, 0.0]), ('Screw-1', I3, [0.01, 0.0, 0.0]), ('Screw-2', RX, [0.0, 0.0, 0.0])])
    c1 = stream([('Rod-1', None, None), ('Pin-1', I3, [0.0, 0.0254, 0.0]), ('Screw-1', I3, [0.01, 0.0, 0.0]), ('Screw-2', I3, [0.0, 0.0, 0.0])])
    got = C.component_transforms(c0)
    assert sorted(got) == ['Pin-1', 'Screw-1', 'Screw-2'], sorted(got)                 # Rod-1 has no block; the units-table block is ignored
    assert np.allclose(got['Screw-1'][1], [0.01, 0, 0]) and np.allclose(got['Screw-2'][0], RX)
    dd, rr = C.deltas_between(c0, c1)
    assert list(dd) == ['Pin-1'] and np.allclose(dd['Pin-1'], [0, 0.0254, 0]), dd         # only the moved component
    assert rr == ['Screw-2'], rr                                                           # a changed rotation is reported, not applied
    assert C.deltas_between(c0, c0) == ({}, [])
    assert C.component_transforms(b'\x00' * 200) == {}
    print('OK: moved component found (+25.4 mm), unmoved ones and the units-table block ignored, rotation change reported')


main()
