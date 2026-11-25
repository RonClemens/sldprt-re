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

## Scanned and raster drawings (`src/scan_drawing.py`)

`python3 src/scan_drawing.py FILE.pdf PAGE`. Needs `pip install opencv-python-headless pypdfium2 rapidocr-onnxruntime` (permissive licences, no system binary such as Tesseract).
The page is rendered, then turned into the pieces the vector reader extracts: text runs (RapidOCR, a second pass on the rotated image for text that runs up the page), long horizontal and
vertical lines (morphology), other lines (Hough), arrowheads (blobs that survive an opening wider than any line stroke, fitted with a minimum enclosing triangle) and circles. The same
`_read_sheet` logic then pairs arrows, reads tolerances and measures drawn lengths, with a 2.5x geometric tolerance. OCR text is repaired first with narrow rules for mistakes seen on a real
scan (`81/2` for `8 1/2`, `R213/8`, `@18` / `016.3/8` for a diameter, `15.5/8`, `18.174` for `18 1/4`).

Results so far: a degraded copy of the synthetic drawing (300 dpi, blur, noise) gives the scale, both views, 6 of the 8 dimensions, and finds the deliberately wrong dimension (drawn 20.3 against 25).
One real scanned vendor drawing in the Hydro package (about 100 dpi, with a watermark) gives 8 dimensions of which 7 are real dimensions with correct values, out of roughly 25 on the sheet: precision is
decent, recall is low. Known losses: small stacked tolerances, leader-style diameter/radius dimensions, digits the OCR misses or merges, circles/arcs on low-resolution scans.
Every dimension from a raster carries `conf='ocr'` unless its drawn length agrees with its printed value. Not done: deskewing, handwriting, a repair step that uses the drawn length to choose
between OCR readings (e.g. `81/2` as 8 1/2 or 40.5), page-level OCR when a vector page has an unusable font mapping.

## Linking to the 3D model: proof of concept (`src/drawing_link.py`)

`python3 src/drawing_link.py DRAWING.pdf PAGE MODEL.glb [--png OUT.png] [--json]`. For each orthographic view the model is projected in the 24 axis-aligned orientations and aligned to the drawn
linework: the projected sharp and silhouette edges are matched to the drawn lines (distance map, shift found by cross-correlation, then refined) at the sheet scale, **without using the dimension text**.
With the view registered, each linear dimension's two anchors (where its arrows touch) are mapped into model coordinates and snapped to the nearest *strong feature coordinate* (where several true model vertices
coincide: a plane, an edge). The distance between the snapped features is the model's value; it is compared with the printed value against the dimension's own tolerance, or the title block's general
tolerance (`FRACTION +-1/16`) when it has none: pass / fail / reference / no-tolerance / unlinked (with how far the nearest feature is). Per view it also counts major feature coordinates and how many carry a dimension.

First result, real data (public Hydro package): drawing 27244607 (page 12 of `Full_TDP.pdf`, a SolidWorks assembly drawing in inches) against the assembly mesh converted from `27244607.SLDASM`:
both views register (mean distance of projected edges to drawn lines 0.2-0.35 pt) and the five dimensions with model features pass, all within 0.0002 in except the overall height (printed `4 21/32"` = 4.6563, model 4.6602, within
the 1/16 in general tolerance; the drawing's own geometry measures 4.6597, so the print is the 1/32-rounded form). The reference dimension `(5 21/32")` cannot be linked: one anchor lies exactly
1.001 in (25.4 mm) beyond the nearest model feature. In the overlay the drawn rod below the block has no model edges. Not established: whether the drawing shows the cylinder's rod extended by its 1 in stroke (the part is `ADN-1"-1"`)
while the mesh holds the default configuration, or the mesh is missing geometry (two small parts converted with one failed face each). The native `27244607.SLDDRW` is in the package, so the drawing's own configuration
could be read once the native format is decoded.

Limits: linear dimensions along a view axis only (no angular, radial, diameter, aligned or ordinate); orthographic views only; an orientation that is symmetric gives `ambiguous: true` (the measured
values are still reported for the best fit); the shift/scale search depends on the sheet scale, which is read from the sheet or inferred from the dimensions (both independent of the model); the model is the mesh
from `sldasm2glb`, so a part that failed to tessellate looks like a missing feature. `tests/test_drawing_link.py` checks the method on the synthetic drawing against a plain box model: dimensions that the box can satisfy pass, those that refer to holes it lacks come back unlinked, none fail.

### Annotated glTF (`src/drawing_gltf.py`)

`python3 src/drawing_link.py DRAWING.pdf PAGE MODEL.glb --glb OUT.glb` writes the CAD mesh unchanged plus one `DIM <id>` node per linear dimension, drawn in the plane of its view just in front of the model: a LINES primitive
(extension lines that start at the real model vertices the dimension spans, the dimension line, an arrowhead at each end) and a TRIANGLES primitive holding the label as stroke-font geometry (`4 21/32" = 4.66 IN`, reference
dimensions in parentheses, `?` when unlinked), written as two single-sided copies so it reads correctly from either side. Colours: green pass, red fail, orange unlinked, grey reference / no tolerance (KHR_materials_unlit).
Each node's `extras` carries the text, printed and measured values, deviation, tolerance used, status and the reason when unlinked; the scene's `extras.drawing_link` carries the source, the general tolerance, the status counts, the
per-view registration and `vector_labels: true`.

An earlier version wrote one two-point line per dimension, far outside the model and with no label; in a geometry-only viewer (the Babylon.js sandbox) that looked like a few stray lines. Checked now by loading the Hydro
27244607 result in Babylon.js and Three.js in headless Chromium, looking straight at each label plane from both sides: lines meet the model, arrowheads and labels are present, labels read left to right (or upwards along a vertical line)
and upright. A viewer that does not know the layer shows it as ordinary lines and meshes; the ipb_viz layer (PR in that repo) adds a toggle and a panel.

### Determinacy report (`src/determinacy.py`)

`python3 src/drawing_link.py DRAWING.pdf PAGE MODEL.glb --json > results.json` then `python3 src/determinacy.py results.json MODEL.glb [--bom BOM.csv] [--md OUT.md] [--json OUT.json]` answers a different question from the link:
not "does the drawing agree with the CAD" but "what does the sheet actually define of the model". For each component (the children of the assembly root) and each model axis it classifies the extent as **dimensioned**
(a dimension spans it, or dimensions reference both end faces), **located** (one face or the centre is referenced, the size is not) or **undefined** (nothing on the sheet touches it: a model built from the sheet alone must assume it). Reference
dimensions are listed but fix nothing. It also lists dimensions that touch no component face (they refer to features inside a component), the per-view coverage of major coordinates, and, with a BOM table as CSV, whether each component fits
the stock the BOM lists (`PLATE`, `BAR`, ... rows with three sizes), whether BOM sizes match a CAD extent, and whether the BOM identifies the component unambiguously (two rows citing one part number, or a component no row cites).

Tried on Hydro 27244607 (Standard configuration): of 21 component extents the sheet dimensions 1, locates 8 and leaves 12 undefined (the screws are not touched at all); the front and top views dimension 5 of 140 and 3 of 202 major coordinates. Block thickness 2.125 in does not fit the BOM stock
`PLATE, 1 3/4" x 3 1/2"` (0.375 in over), and the PIN row cites the block's drawing number 27244630 while the pin component is 27244631. Both are findings to review against the component drawings, not conclusions: the BOM text was
entered by hand from the sheet for this test, and the CAD thickness may include features the stock line does not describe. Works on bounding extents only (see the module docstring); `tests/test_determinacy.py` checks it on a synthetic assembly. A FreeCAD
(conda-forge, headless) build of a solid from only the printed dimensions and BOM text gave the same picture: the six dimensions fix the width, the axis position and the overall height; every other extent had to be assumed.
