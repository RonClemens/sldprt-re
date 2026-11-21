#!/usr/bin/env python3
"""Scanned-drawing reader: OCR text repair rules, and a degraded raster of the synthetic drawing (tools/make_test_drawing.py) read back against its ground truth.
Needs opencv-python-headless, pypdfium2, rapidocr-onnxruntime; prints SKIP and exits 0 when they are not installed. usage: python3 tests/test_scan_drawing.py"""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
try:
    import cv2, numpy as np, pypdfium2 as pdfium, rapidocr_onnxruntime  # noqa
except ImportError as e:
    print('SKIP (missing', e.name + ')'); sys.exit(0)
import make_test_drawing as G
import scan_drawing as S


def main():
    # OCR repair rules (each is a mistake seen on a real scan)
    for raw, want in (('81/2', '8 1/2'), ('R213/8', 'R21 3/8'), ('231/2', '23 1/2'), ('@18', 'Ø18'), ('15.5/8', '15 5/8'), ('8. 1/2', '8 1/2'),
                      ('016.3/8', 'Ø16 3/8'), ('@18.174', 'Ø18 1/4'), ('100', '100'), ('10.1', '10.1'), ('2.5', '2.5'), ('31', '31'), ('1/4', '1/4'), ('61/2', '6 1/2')):
        got = S.repair_text(raw); assert got == want, (raw, got, want)
    # a degraded raster of the synthetic drawing
    tmp = tempfile.mkdtemp(); pdf = os.path.join(tmp, 'd.pdf'); pg = G.build(); G.write_pdf(pg, pdf); truth = pg.truth
    p = pdfium.PdfDocument(pdf)[0]; W, H = p.get_size(); dpi = 300
    img = np.array(p.render(scale=dpi / 72).to_pil().convert('L'))
    img = cv2.GaussianBlur(img, (0, 0), 1.0); img = np.clip(img.astype(float) + np.random.default_rng(1).normal(0, 10, img.shape), 0, 255).astype(np.uint8)
    sh = S.read_scan_gray(img, (W, H), scale=dpi / 72)
    assert abs(sh.scale - 0.5) < 1e-6, sh.scale
    assert len(sh.views) >= 2, sh.views
    right = 0
    for t in truth['dimensions']:
        if any(d.kind == t['kind'] and d.value is not None and abs(d.value - t['value']) < 1e-6 for d in sh.dimensions): right += 1
    assert right >= 5, (right, [(d.kind, d.text, d.value) for d in sh.dimensions])          # of 8; the small stacked tolerances and the leader are the usual losses
    off = [d for d in sh.dimensions if d.drawn_dev is not None and abs(d.drawn_dev) > 2.0]
    assert len(off) == 1 and -6.5 < off[0].drawn_dev < -3.5 and off[0].value == 25.0, [(d.text, d.drawn_dev) for d in off]       # the deliberately wrong dimension, and only it
    assert len(sh.dimensions) <= len(truth['dimensions']) + 2
    print('OK: %d of %d dimensions recovered from the degraded raster; wrong dimension found (%.1f)' % (right, len(truth['dimensions']), off[0].drawn_dev))


main()
