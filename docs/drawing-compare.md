# Drawings versus the 3D model: plan and status

Goal: read a 2D drawing (dimensions, tolerances, datums, GD&T, notes), link each dimension to the reconstructed 3D model, and report how fully
and how accurately the drawing describes the end item.

## Pipeline

1. **Reader** -> `Drawing` (`src/drawing_ir.py`, one structure for every source).
   - `src/pdf_drawing.py`: vector PDFs (done, see below).
   - native `.SLDDRW` (later): same IR, plus the stored entity references that make linking exact. `src/slddrw.py` today reads only previews, sheet names and referenced model names.
2. **Link** (not built): register each view to the model (project the model in the six standard views, match silhouette and edges), then find the model edge/face pair each dimension spans. From a PDF this is geometric and can be ambiguous (many identical holes): ambiguous links are reported, not guessed.
3. **Compare** (not built): measure the model between the linked entities, compare to the stated nominal and tolerance (pass / fail / unlinked / not measurable), and compute coverage: faces and features with no dimension, reference-only dimensions, duplicates.
4. **Output** (not built): report plus glTF annotation layer (leaders and text as line geometry, values and pass/fail in `extras`).

## PDF reader (`python3 src/pdf_drawing.py FILE.pdf [--json]`, needs `pip install pdfminer.six`)

Per sheet it reads: scale (`SCALE 1:2`, `SCALE ~ 1:38`), views (object linework clusters, label beneath), dimensions, datum feature symbols, GD&T frames, notes, title-block text.

Dimensions: paired-arrow linear dimensions (horizontal, vertical, aligned) and single-arrow leader dimensions (diameter, radius). Text forms: `100`, `100 ±0.1`, stacked
`+0.05 / -0.1`, limits `10.1 / 9.9`, basic (boxed), reference `(100)`, `2X Ø8`, `4 PLCS`, `45°`, `THRU`. Each carries the extension lines, the arc a diameter/radius points at,
and `drawn`: the value measured from the drawn geometry (drawn length / sheet scale). `drawn_dev` (drawn minus printed) is the self-consistency check: a dimension whose text
disagrees with its own geometry shows up here before any 3D model is involved.

Not handled yet: angular dimensions' geometry (value is read, `drawn` is empty), chain/baseline/ordinate styles, arrowheads drawn as open "V" strokes or ticks, text converted to outlines or
scans (needs OCR; the page gets a warning), per-view scales, inch drawings (the unit is assumed mm and flagged `unit_assumed`), GD&T characteristic glyphs (font-specific: the raw cell text is kept in
`cells`, `symbol` stays empty unless the glyph is one of the standard Unicode GD&T characters), surface-finish and weld symbols, multi-line notes (each text run is its own note).

## First check on real drawings (public Hydro package, `Full_TDP.pdf`, 151 pages)

The reader was run on the 40 pages that carry clean text (real SolidWorks exports, mostly inch drawings with fractions). What the real files taught it, all now handled:
a fractional dimension (`4 21/32"`) is a whole number plus two rows of same-size digits (numerator above denominator), not small type; `adv` in pdfminer is in em units, so glyph
spacing must come from bounding boxes; pure fractions (`3/4"`) look like a limits pair and are told apart by order (limits run high over low); a reference dimension's parentheses are
shallow stroked polylines, not characters; a title block and tables are many separate line segments (rectangles are rebuilt from them); a sheet may carry no scale text (the scale is
then inferred from the dimensions when most of them agree, and flagged `inferred`). Page 12 (a dimensioned part drawing) reads completely: six dimensions, units, the text overrides
"TOTAL HEIGHT / TOTAL WIDTH", the reference dimension, and each drawn length agrees with its printed value to 0.01 in.

Still wrong or missing on real pages: pages that are not dimensioned drawings (wiring schematics, tables) still produce spurious dimensions (no page classifier yet); some pages use fonts without a
Unicode mapping (the text comes out shifted, `REVISIONS` as `TGXKUKQPU`) and a few have text drawn as outlines: both need OCR; datum symbols and GD&T frames were not found on any of the 40 pages
(either none are present or the detection is too strict: unverified); angular and chamfer notation (`1 X 45°`) is read as text only.

## Verification

`tests/test_pdf_drawing.py` generates a synthetic drawing (`tools/make_test_drawing.py`, an invented 100 x 60 x 10 plate with a deliberately wrong dimension) and checks every
dimension, view, datum, GD&T frame and note against the generator's ground truth. **The reader has not yet been run on a real SolidWorks PDF export.** On the public Hydro
drawing PDFs (ipb_viz-generated envelope sheets with no dimensions) it reports 0 dimensions on 210 pages (no false positives) and reads the sheet scales.
No drawing data is committed: test PDFs are generated at test time.
