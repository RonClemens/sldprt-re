#!/usr/bin/env python3
"""Native drawing dimensions (src/slddrw_dims.py) on a synthetic MFC-archive fragment: the layout seen in a real SLDDRW (name `RD<n>`, the double 2.0, the measured value in metres, component names,
the text markup), written here, no real file. usage: python3 tests/test_slddrw_dims.py"""
import os, struct, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import slddrw_dims as S


def mfc(s): return b'\xff\xfe\xff' + bytes([len(s)]) + s.encode('utf-16-le')
def dbl(v): return struct.pack('<d', v)


def dim(name, value, markup, comps=(), faces=()):
    return (b'\x00' * 11 + mfc(name) + b'\x01\x00\x00\x00' + dbl(2.0) + mfc('') + dbl(value) + dbl(1.0) + dbl(1.0) + b'\x00' * 20
            + b''.join(mfc(c) + b'\x00' * 6 for c in comps) + b'\xff\xff\x01\x00\x14\x00moEndFaceSurfIdRep_c' + b'\x00\x00' + b''.join(struct.pack('<III', F, 0x5a932ca2, I) + b'\x00' * 4 for F, I in faces) + b'\x00' * 30 + mfc(markup) + b'\x00' * 40)


def main():
    blob = b'\x00' * 50 + mfc('<objID=1>') + dbl(0.0254) + mfc('D1') + dbl(2.0) + dbl(0.00508)            # a sketch dimension: not a reference dimension, must be skipped
    blob += dim('RD1', 0.143774245, '<Border type=14> 5<STACK size=1 divhratio=0.1 divwgap=1.0>21<OVER>32</STACK>" </Border>', ['Cylinder-1', 'Rod-1'], [(28, 45), (28, 1), (28, 45)])
    blob += dim('RD2', 0.118375, ' 4<STACK size=1 divhratio=0.1 divwgap=1.0>21<OVER>32</STACK>" \r\nTOTAL HEIGHT')
    blob += dim('RD3', 0.0889, '3.50')
    r = S.read_dimensions(blob)
    assert [x['name'] for x in r] == ['RD1', 'RD2', 'RD3'], [x['name'] for x in r]
    assert [round(x['value_m'], 6) for x in r] == [0.143774, 0.118375, 0.0889], [x['value_m'] for x in r]
    assert r[0]['reference'] and abs(r[0]['printed'] - 5 - 21 / 32) < 1e-9 and r[0]['unit'] == 'inch' and r[0]['components'] == ['Cylinder-1', 'Rod-1'], r[0]
    assert [(x['component'], x['feature'], x['face']) for x in r[0]['entities']] == [('Rod-1', 28, 45)] and r[0]['entities'][0]['rep'] == 'moEndFaceSurfIdRep_c' and r[0]['entities'][0]['neighbours'] == [(28, 1), (28, 45)], r[0]['entities']
    assert r[1]['entities'] == [] and r[2]['entities'] == []
    assert not r[1]['reference'] and abs(r[1]['printed'] - 4 - 21 / 32) < 1e-9 and r[1]['label'] == 'TOTAL HEIGHT', r[1]
    assert r[2]['printed'] == 3.5 and r[2]['unit'] is None and r[2]['label'] is None, r[2]
    # markup parsing on its own
    for markup, want in ((' 2<STACK size=1 divhratio=0.1 divwgap=1.0>1<OVER>2</STACK>" ', dict(printed=2.5, unit='inch')), (' 1<STACK a=1>3<OVER>4</STACK>" ', dict(printed=1.75)), ('<STACK a=1>3<OVER>4</STACK>"', dict(printed=0.75)),
                         ('12.5', dict(printed=12.5, unit=None)), ('<Border type=14>7</Border>', dict(printed=7.0, reference=True)), ('8"\r\nTOTAL', dict(printed=8.0, label='TOTAL'))):
        got = S.parse_markup(markup)
        for k, v in want.items(): assert abs(got[k] - v) < 1e-9 if isinstance(v, float) else got[k] == v, (markup, k, got)
    assert S.read_dimensions(b'\x00' * 100) == []
    print('OK: 3 reference dimensions read from the synthetic archive, markup parsing, sketch dimension skipped')


main()
