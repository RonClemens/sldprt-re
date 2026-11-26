#!/usr/bin/env python3
"""Check one sheet of a vector PDF drawing against a CAD model: link its dimensions to the mesh, write the annotated glTF, and write the determinacy report.

  run_check(pdf, page, glb, outdir, bom_csv=None, tol_mm=0.25, progress=None) -> summary dict        python3 src/drawing_check.py DRAWING.pdf PAGE MODEL.glb OUTDIR [--bom BOM.csv] [--tol-mm 0.25] [--source NAME]

Writes into OUTDIR: annotated.glb (the model plus one node per dimension, see drawing_gltf.py), results.json (every dimension with its printed and measured value and status; this holds the drawing's text),
determinacy.json and determinacy.md (what the sheet defines of the model, see determinacy.py), summary.json (counts only). The returned summary has no drawing text, only counts and file names, so a host may log it.
The command line prints `STEP <name>` as it goes and ends with one line `RESULT <json>` or `ERROR <code> <message>`; the helper server (serve.py, POST /jobs/<id>/checks) runs it as a subprocess so that a hung read can be killed.
Vector PDFs only: a scanned page has no lines to link (scan_drawing reads its dimensions but not its geometry), and gives the error `no-dimensions`. Needs pdfminer.six, opencv-python-headless, scipy.
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)


class CheckError(Exception):
    def __init__(self, code, message): super().__init__(message); self.code = code


def run_check(pdf, page, glb, outdir, bom_csv=None, tol_mm=0.25, progress=None, source=None):
    step = progress or (lambda s: None); os.makedirs(outdir, exist_ok=True)
    try:
        import numpy, cv2, scipy, pdfminer  # noqa
    except ImportError as e: raise CheckError('missing-dependency', f'the drawing check needs {e.name} (pip install pdfminer.six opencv-python-headless scipy)')
    from pdf_drawing import read_pdf
    import drawing_link as L, determinacy as D
    from drawing_gltf import annotate_glb
    step('read-sheet')
    try: sheet = read_pdf(pdf, pages=[page], keep_lines=True).sheets[0]
    except IndexError: raise CheckError('bad-page', f'the PDF has no page {page}')
    except Exception as e: raise CheckError('unreadable-pdf', f'the PDF could not be read ({type(e).__name__})')
    if not sheet.dimensions:
        why = '; '.join(sheet.warnings)[:200]
        raise CheckError('no-dimensions', 'no dimensions were read from this page' + (f' ({why})' if why else '') + '. Only vector PDF pages can be linked; a scanned page is not supported yet.')
    step('link')
    try: res = L.link_sheet(sheet, glb)
    except Exception as e: raise CheckError('link-failed', f'the dimensions could not be linked to the model ({type(e).__name__}: {str(e)[:120]})')
    if not res['views']: raise CheckError('no-views', 'no orthographic view on this page could be registered to the model')
    step('annotate'); annotate_glb(glb, res, os.path.join(outdir, 'annotated.glb'), source=f'{source or os.path.basename(pdf)} page {page}')
    json.dump(res, open(os.path.join(outdir, 'results.json'), 'w'), default=float)
    step('determinacy'); bom = None
    if bom_csv:
        p = os.path.join(outdir, 'bom.csv'); open(p, 'w', newline='').write(bom_csv); bom = D.read_bom(p)
    rep = D.analyse(res, D.load_components(glb), bom, tol_mm)
    json.dump(rep, open(os.path.join(outdir, 'determinacy.json'), 'w'), indent=1, default=float); open(os.path.join(outdir, 'determinacy.md'), 'w').write(D.to_markdown(rep, f'page {page}'))
    counts = {}
    for r in res['dimensions']: counts[r['status']] = counts.get(r['status'], 0) + 1
    summary = dict(page=page, dimensions=len(res['dimensions']), status=counts, views=len(res['views']), ambiguous_views=[v['id'] for v in res['views'] if v.get('ambiguous')], general_tolerance=res.get('general_tolerance'),
                   determinacy=rep['component_axis_counts'], bom_issues=len(rep['bom']['issues']), bom_stock_misfits=sum(1 for f in rep['bom']['findings'] if f.get('stock') and not f['stock']['fits']),
                   files=['annotated.glb', 'results.json', 'determinacy.json', 'determinacy.md'], tol_mm=tol_mm)
    json.dump(summary, open(os.path.join(outdir, 'summary.json'), 'w')); return summary


def main():
    a = sys.argv[1:]
    if len(a) < 4 or a[0].startswith('-'): raise SystemExit(__doc__)
    opt = lambda n, d=None: a[a.index(n) + 1] if n in a else d
    bom = open(opt('--bom')).read() if opt('--bom') else None
    try: summary = run_check(a[0], int(a[1]), a[2], a[3], bom, float(opt('--tol-mm', 0.25)), lambda s: print('STEP', s, flush=True), opt('--source'))
    except CheckError as e: print('ERROR', e.code, str(e), flush=True); sys.exit(2)
    print('RESULT', json.dumps(summary, default=float), flush=True)


if __name__ == '__main__':
    main()
