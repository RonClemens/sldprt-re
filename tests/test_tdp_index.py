#!/usr/bin/env python3
"""TDP indexing on the synthetic drawing (tools/make_test_drawing.py) and the glyph-index decoder. Needs pdfminer.six, numpy. usage: python3 tests/test_tdp_index.py"""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
try:
    import numpy, pdfminer  # noqa
except ImportError as e:
    print('SKIP (missing', e.name + ')'); sys.exit(0)
import make_test_drawing as G
from pdf_drawing import _decode_cid
import tdp_index as T

assert _decode_cid('(cid:53)') == 'R' and _decode_cid('(cid:3)') == ' ' and _decode_cid('(cid:97)') == '~' and _decode_cid('(cid:200)') == '(cid:200)' and _decode_cid('(cid:145)') == '\u00d8' and _decode_cid('(cid:131)') == '\u00b0' and _decode_cid('(cid:2)') == '(cid:2)' and _decode_cid('A') == 'A', 'glyph index = code - 29'
pdf = os.path.join(tempfile.mkdtemp(), 'd.pdf'); G.write_pdf(G.build(), pdf)
assert T.page_count(pdf) == 1
r = T.index_page(pdf, 1)
assert r['cls'] == 'detail-part' and r['dimensions'] >= 6 and r['views'] >= 2 and not r['decoded_from_glyphs'] and r['evidence'], r
bad = T.index_page(pdf, 5); assert bad['cls'] == 'unreadable' and bad['link_ready'] is False, bad                      # a page that is not in the file
s = T.summary([r, bad]); assert s['pages'] == 2 and s['classes'] == {'detail-part': 1, 'unreadable': 1} and s['errors'] in (0, 1), s
md = T.to_markdown([r, bad], 'synthetic'); assert '2 pages' in md and '| 1 |' in md
print('OK: glyph-index decoding, page classified as detail-part with its evidence, a missing page reported unreadable, summary and table')
