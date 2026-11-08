"""usage: python3 tests/test_slddrw.py   -- builds a tiny SolidWorks-style container and reads it back with src/slddrw.py"""
import os, struct, sys, tempfile, zlib
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import slddrw
M = bytes.fromhex('140006000800')
def chunk(name, data):
    nm = name.encode()                                   # key 0 (d[7] & 7 == 0): names are stored as-is
    co = zlib.compressobj(wbits=-15); comp = co.compress(data) + co.flush()
    return M + b'\x00' * 4 + struct.pack('<IIII', 0, len(comp), len(data), len(nm)) + nm + comp
def png(w, h):
    def c(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + c(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + c(b'IDAT', zlib.compress(b'\x00' + b'\xff' * (3 * w))) + c(b'IEND', b'')
definition = ('\x00PART-1.SLDPRT\x00ASM 2.SLDASM\x00PART-1.SLDPRT\x00Sheet1\x00Other.SLDPRT').encode('utf-16-le')
names = ('﻿Sheet1﻿Sheet2').encode('utf-16-le')
blob = b'\x00' * 7 + b'\x00' + b'\x00' * 8 + chunk('Images/Sheet_0', png(640, 480)) + chunk('Images/Sheet_1', png(640, 480)) + chunk('SheetPreviews/SheetNames', names) + chunk('Contents/Definition', definition) + chunk('Contents/Other', b'ignored')
with tempfile.NamedTemporaryFile(suffix='.SLDDRW', delete=False) as f: f.write(blob); path = f.name
try:
    d = slddrw.read_drawing(path)
    assert [n for n, _ in d['sheets']] == ['Sheet1', 'Sheet2'], d['sheets']
    assert all(slddrw.png_size(p) == (640, 480) for _, p in d['sheets'])
    assert d['refs'] == ['PART-1.SLDPRT', 'ASM 2.SLDASM'], d['refs']      # the header list only, de-duplicated, before the first sheet marker
    assert slddrw.sheet_names(b'', 3) == ['Sheet1', 'Sheet2', 'Sheet3']
    assert d['sizes'] == [None, None], d['sizes']                          # no sheet-format text in this drawing: no size is invented
    print('ok')
finally: os.remove(path)

# sheet sizes: best-effort text scan of the definition stream (see slddrw.py: unverified against real drawings)
def build(definition_text, n=2):
    nm = ''.join('\ufeffSheet%d' % (i + 1) for i in range(n)).encode('utf-16-le')
    b = b'\x00' * 7 + b'\x00' + b'\x00' * 8 + b''.join(chunk('Images/Sheet_%d' % i, png(640, 480)) for i in range(n)) + chunk('SheetPreviews/SheetNames', nm) + chunk('Contents/Definition', definition_text.encode('utf-16-le'))
    with tempfile.NamedTemporaryFile(suffix='.SLDDRW', delete=False) as f: f.write(b); return f.name
def sizes_of(text, n=2):
    p = build(text, n)
    try: return slddrw.read_drawing(p)['sizes']
    finally: os.remove(p)
one = sizes_of('\x00PART-1.SLDPRT\x00a3 - landscape.slddrt\x00Sheet1\x00Sheet2')
assert [x['style'] for x in one] == ['A3', 'A3'] and one[0]['scope'] == 'document' and one[0]['standard'] == 'ISO' and one[0]['orientation'] == 'landscape' and one[0]['via'] == 'text-scan' and one[0]['confidence'] == 'low', one
per = sizes_of('x\x00A3 - Landscape\x00Sheet1\x00B (ANSI) Landscape\x00Sheet2')
assert [(x['style'], x['standard'], x['scope']) for x in per] == [('A3', 'ISO', 'sheet'), ('B', 'ANSI', 'sheet')], per
assert sizes_of('A0 (ISO)\x00Sheet1')[0]['style'] == 'A0' and sizes_of('A (ANSI) Portrait')[0]['orientation'] == 'portrait'
assert sizes_of('A3 - Landscape\x00B - Portrait\x00A4 - Portrait\x00x') == [None, None], 'three different mentions for two sheets are ambiguous: no guess'
for neg in ('A3BRACKET.SLDPRT\x00E-STOP.SLDPRT\x00Part A rev\x00SheetA3', 'B3 landscape-ish', 'DRAWING A3'):
    assert sizes_of(neg) == [None, None], (neg, sizes_of(neg))
assert slddrw.size_hits(b'') == [] and slddrw.sheet_sizes(b'', 0) == []
print('sheet sizes ok')
