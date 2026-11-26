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
`PLATE, 1 3/4" x 3 1/2"` (0.375 in over), and the PIN row cites the block's drawing number 27244630 while the pin component is 27244631 (this turned out to be correct: the pin is drawn on the block's sheet, see the part-drawing section below). Both are findings to review against the component drawings, not conclusions: the BOM text was
entered by hand from the sheet for this test, and the CAD thickness may include features the stock line does not describe. Works on bounding extents only (see the module docstring); `tests/test_determinacy.py` checks it on a synthetic assembly. A FreeCAD
(conda-forge, headless) build of a solid from only the printed dimensions and BOM text gave the same picture: the six dimensions fix the width, the axis position and the overall height; every other extent had to be assumed.

### Package index and glyph-index text (`src/tdp_index.py`, `pdf_drawing._decode_cid`)

`python3 src/tdp_index.py PACKAGE.pdf OUT.json [--md OUT.md] [--pages 1-40] [--workers 4]` reads every page of a drawing package once and classifies it (assembly, detail-part, layout-no-dims, schematic-or-wiring, piping, list-or-cover, other, unreadable) with the
evidence for the class, the counts the dimension reader finds, the SEE DWG references, a drawing-number guess from the title block, and `link_ready`. The classes are heuristics for sorting a package. On the 151-page Hydro package (2.3 minutes with 4 workers): 35 assembly,
48 detail-part, 36 layout-no-dims, 12 schematic-or-wiring, 4 piping, 2 list-or-cover, 12 other, 2 unreadable; 1246 dimensions read; 61 pages link-ready. The drawing-number guess agrees with the independent index in ipb_viz (`tools/index-tdp.py`) on 111 of 151 pages: use that index
for the number and keep this one for what the reader can do with each page.

**Fonts that are not embedded.** 42 of the 151 pages use Arial / Times New Roman / Century Gothic without embedding them or a ToUnicode map, so pdfminer returned `(cid:N)` and the readers saw nonsense (page 40, the block's detail drawing, gave 2 dimensions). For these TrueType fonts
the code is the glyph index, and for printable ASCII that is the character code minus 29, which `_decode_cid` applies; page 40 then reads 15 dimensions in 3 views. Symbols (the diameter sign, counterbore, degree) have other glyph indices that are not decoded: 40 pages still have some,
reported in the page's warnings, and a dimension such as a diameter may then lack its symbol.

**Part drawings are not linked well yet.** Linking the block's detail drawing (page 40) to its CAD part (27244630) registered 2 views (one flagged ambiguous) and linked 2 of 15 dimensions, one of them a failing false link: the linker was built for assembly
sheets. The mesh also lacks one face of the part (face 97, KeyError(1) in the part converter). The sheet prints 3 1/2" (twice) and 2 1/8", which are the CAD extents (3.5 x 3.5 x 2.125 in; I did not check which view each belongs to); the assembly sheet's BOM stock line `PLATE, 1 3/4" x 3 1/2"` does not match them. A feature-level comparison needs the part's
planes and cylinders (hole axes), which the converter has but the mesh does not carry.

### Part features, feature determinacy, and a part rebuilt from its drawing (`src/part_features.py`, `src/feature_determinacy.py`, `src/drawing_part.py`)

**part_features.py** reads the exact faces of a part from the Parasolid body, not the mesh: planes (outward normal, offset), cylinders (axis, radius, hole or boss, axial range), cones (drill points, chamfers), with the face ids. The Hydro block (27244630) has 24 faces: 8 planes
(one 45 degree chamfer), 12 cylinders (the 7/8 in bore through everything, a tapped hole in the side, four stepped holes 3/8 in for 1 3/8 in then 0.22 in, two 1/2 in corner radii) and the cone at the bottom of the tapped hole, which the mesh had dropped.

**feature_determinacy.py** asks, for each quantity a drawing has to give (overall sizes, plane positions, hole axes, hole ends, diameters), whether a printed value on the sheet equals it. On page 40 of the Hydro package: 40 of 49 quantities are carried by a printed value
(extents 3/3, positions 25/25, hole ends 9/9, diameters 3/12). The nine that are not: the diameters of the four stepped holes (0.375 and 0.22 in) and of the tapped hole (0.6875 in, a tap drill; the sheet gives the thread), which no readable text states. Matching is by value, not by
what the dimension's arrows touch, so a coincidence is credited.

**drawing_part.py** goes the other way: the drawing alone, to a part. It finds the plan view (the cluster of full circles), its outline, the elevation next to it, infers the scale from the printed dimensions, and measures overall size, corner radii, hole diameters, hole centres and
the lengths of each hole step from the drawn linework. Each value is `stated` when a printed value equals it within 1/64 in (the printed value is then used) and `unconfirmed` otherwise: measured at the inferred scale from a sheet that says DO NOT SCALE. It can build the result in FreeCAD
(`--freecad OUT.step --fcpython .../freecadcmd`) and compare it with the CAD part's feature list (`--cad PART.SLDPRT`). On the Hydro block: scale 72 pt/in (19 printed values match, runner-up score 6); 32 quantities compared with the CAD, none differ by more than 0.2 mm
(the largest, 0.096 mm, is the fractional-inch rounding of the hole pattern, which the CAD has as 26 mm); 19 values stated, 13 unconfirmed, and the unconfirmed ones (the two hole diameters, the 0.75 in step, the 1/2 in corner radius) agree with the CAD to 0.005 mm. The FreeCAD solid
has the right outline, 5 stacks of holes and the corner radii; it lacks what the plan and elevation do not show (the tapped hole in the side, the 1/4 in chamfer), about 5 to 8 thousand mm3 of the CAD volume.

**What the sheet shows about the pin and the stock.** Page 40 is the detail drawing of two parts: the block (2.2.6.1, part 27244630) and the pin (2.2.6.2, part 27244631, 3/4 in round bar, 1 3/4 in long), so the assembly BOM's PIN row citing 27244630 is correct, and my earlier note that it might be a typo was wrong.
The stock line printed on the sheet, `PLATE, 1 3/4" x 3 1/2" x 3 1/2"`, is thinner than the part's printed 2 1/8 in thickness: the same stock-versus-part mismatch as the assembly BOM; worth a look in the released drawing (a stock note left from an earlier revision is the usual cause).

**Limits.** drawing_part handles prismatic parts drawn as a plan with round holes and an elevation next to it (above or below), with the scale one of 1/4 .. 4 times full size and at least 3 printed values matching; it does not read side or section views, threads, slots, chamfers or non-round pockets, and it gets the scale wrong
(and says so with `scale_ambiguous`) when too few printed values match. The circle recogniser needs the circles as closed polylines or Bezier circles; text symbols (diameter, depth) in fonts that are not embedded stay undecoded except the two seen on this sheet.

**Survey of the 48 detail-part pages of the Hydro package (`tools/tdp_rebuild_survey.py`).** 28 pages gave measurements, 19 had no plan view (fewer than two full circles: no round holes, or circles drawn another way), 1 timed out. Only one rebuild is verified correct: page 40, the block. Of the 11 pages whose drawing number
matches a CAD file and so could be compared, 10 differ from the CAD by a large amount; the causes seen are a plan outline that is the sheet border or a whole weldment (8 pages in all have an outline spanning the sheet), a scale that is a different power of two than the real one (17 of 28 pages first reported an ambiguous scale; 11 still do after circle diameters were added to the
evidence), and parts that are not simple prismatic blocks (the classifier's detail-part class includes weldments and frames drawn with many dimensions and no BOM). Page 39 is Rev 02 of the block and differs from the CAD by hole positions: the CAD is the Rev 03 model, whose revision notes mention opened and removed holes, so a revision difference is the likely cause, not verified.
Take the tool as validated on one prismatic part with a plan view and an elevation, and as a measurement aid, not a general drawing-to-part converter.

**Second pass over the same 48 pages** (scale from the nearest `SCALE a : b` label when the printed values agree with it about as well as with the best inferred scale; the sheet border and the title block removed before clustering; a cluster that is the circles alone, or an outline over 60 percent of the sheet, refused).
Fewer pages give a measurement and the refusals say why: 12 measured, 16 without a plan view (fewer than two full circles), 15 whose circles have no outline around them, 4 whose outline spans the sheet, 1 timeout. Page 40 is still the only verified rebuild; the 6 comparisons with a CAD file of the same drawing number that
disagree are not trustworthy checks either way, because those numbers belong to weldments and assemblies in the CAD, not to a block like the one measured. The remaining measured pages (27244644, 27244646, 27244600 sheets) have no CAD part to compare with. So the tool now refuses more and is wrong less often, but what it has proven is one part.

### Zones, and a drawing with no CAD part rebuilt zone by zone (`src/sheet_zones.py`, `tools/tdp_zone_survey.py`, `tools/rebuild_stop_bracket.py`)

`python3 src/sheet_zones.py DRAWING.pdf PAGE [--json OUT.json] [--crops DIR --dpi 200]` reads the sheet's zone border (the letters and numbers in the margins), names every zone as on the sheet (F8, C3, A1 ...), finds the views (clusters of object linework) and writes each as the zone range it spans (F8-D7;
B2+C1+C2 when the zones do not form a rectangle), assigns each dimension to its nearest view, picks up the view titles, `SCALE a : b` labels and the tables (title block, revision table, BOM, notes), classes every zone (view, title block, BOM table, revision table, notes block, dimensions only, text only, empty) and renders a crop of each view region and table at the
dpi given, so a slot callout or a dimension chain can be looked at one zone group at a time. On a sheet with no readable zone labels it gives one zone `*`.

Over the 151-page Hydro package (`tools/tdp_zone_survey.py`): 84 pages have a zone border (30 as 8 x 6, 39 as 4 x 4, 10 as 8 x 3, a few other grids), 65 have none (the electrical cabinet sheets 46 to 86 and others have no labels), 2 pages (2 and 52) exceed the 240 s limit because their linework has thousands of segments.
854 views were found, of which a good share are fragments of one real view (a view is a cluster of object-width lines within 8 pt of each other, so hidden-line or section fragments can split); 136 zones are title block, 55 revision table, 84 notes block, 123 dimensions only. Only 7 pages were recognised as having a BOM table (the detection wants ITEM, QTY and similar words as single notes).
It is a sorting aid, with the per-page rows in the survey JSON.

**Worked example: STOP BRACKET (27244633, page 41), a drawing the CAD export has no part for.** The sheet is an 8 x 6 zone grid; the zones give six views: V1 F8-D7 (the upright seen from outside), V2 F6-D5 (the side with the gusset), V3 F3-D2 (isometric), V4 and V5 (the gusset, 1:2), V6 C6-B5 (the base from above). `tools/rebuild_stop_bracket.py` measures the circles and slots in V1 and V6, takes each view's scale from the printed overall size
(54 pt per inch, 3:4), checks every position against the printed dimensions of its own view, and builds the angle, four slots, two holes and the gusset in FreeCAD. All 20 printed dimensions were read; every hole and slot position is stated (the printed chain values are used); the hole (5/16 in) and slot (17/32 in) diameters are unconfirmed measurements (the callout fractions after the diameter symbol are not in the text the reader recovers, and agree with the measurement in the crops);
the way the views relate (which view shows what, which edge the chains start from, the gusset standing against the upright's inner face) is my reading of the sheet and is written in the script. Independent check: the solid's volume (24.93 in3) at the density of ASTM A36 steel (0.2836 lb/in3) is 7.07 lb, against the 7.04 lb printed on the sheet (+0.4 percent). The same check on the Hydro block gives 6.60 lb for the CAD part
(6.64 printed) and 6.75 lb for the part measured from the drawing (which lacks the side hole and the chamfer). Not modelled: the rounded outer corners and inner radius of the angle, weld fillets, the thread. The mirror of the member (which end the "right edge" of the views is) is not determined by the sheet.

### Building blocks per view and zone (`src/view_features.py`, `sheet_zones.blocks_markdown`)

Each view is now classified in the terms a 3D model is built from, and the zones aggregate them. `python3 src/sheet_zones.py DRAWING.pdf PAGE --md OUT.md [--projection third|first] [--unit inch|mm]` writes, per view: its role (front, top, bottom, right or left side, auxiliary, detail or section, isometric), the part axes across and up the page
(front X/Y, top and bottom X/Z, sides Z/Y), the scale (the `SCALE a : b` label when the printed values agree with it, else inferred, else shared with the aligned view, else assumed full size and flagged), each dimension's direction (from its arrow tips, else by which line spacings its value equals) mapped to a part axis, the holes
(through, blind with depth, counterbored with both diameters and step lengths, countersunk with countersink diameter, tapped with the thread callout, slots), fillet radii (arcs and rounded outline corners, with the count), chamfers (straight cuts at corners, callouts), and for each zone the roles, dimension counts by axis, hole types, fillet radii and slots in it.
The roles come from the layout under the third-angle (ASME) assumption, which the run states; the sheet's own projection symbol is a drawing, not text. When two views are aligned and neither has more neighbours, the one with fewer circles is the front view (the hole axes square to a view belong to the top, bottom and side views).

Hydro block (page 40): V1 (F6-E5) front at 72 pt per inch with the tapped side hole (3/4-16 UNF, depth 0.75 to the cone, drill 0.6875); V4 (D6-D5) the bottom view with a 0.875 through bore (depth 2.125, THRU ALL callout), four counterbored holes (0.375 for 1.375, then 0.22 for 0.75) and 0.5 in fillets on two corners; the isometric and pin views flagged as such;
this is the inventory the rebuild of the block needs. STOP BRACKET (page 41): V1 the left side (Z/Y) with two 0.313 holes (one tapped 3/8-16 UNC THRU, 0.5 deep = through the upright), V2 the front, V6 the bottom view with four slots (0.5315 wide, 0.7167 between centres), V5 the gusset at 1:2 with its 1/2 x 45 chamfer and 1/4, 3, 6, 1/2 dimensions.
Limits: a hole whose profile is in no aligned view stays `unknown depth`; a callout shared by several holes ("2 x ... THRU") is attached to the nearest only; a fillet drawn as a chain of short segments joined to the outline is found only at the corners of a view's box (not at an interior corner); a one-view sheet or a sheet with no dimensions has its scale assumed.
