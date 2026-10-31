# Assessment: 3D content inside the interactive PDF (ipb_viz #92)

Assessment only; nothing here is implemented. Masked: no part numbers, program names or file contents. Figures marked *(est.)* are estimates
from general knowledge of the formats, not measurements of a prototype; the one measured number is the mesh size below.

## Question 1: is GLB -> U3D / PRC feasible inside sldprt-re?

**Yes, feasible; not trivial; offline-capable.**

| Item | U3D (ECMA-363) | PRC (ISO 14739-1) |
|---|---|---|
| Approach | Write the U3D chunk stream directly from our per-part meshes (node tree, mesh resources, materials), or emit IDTF text and run a converter | Write a PRC product tree (assembly occurrences, part definitions, tessellated meshes, colours) |
| Existing code to build on | Intel's U3D sample library (IDTF -> U3D; reported Apache-2.0, **confirm before vendoring**); MeshLab uses it | Asymptote's PRC writer (LGPL, C++); no maintained pure-Python writer known |
| Hierarchy and names | Node tree with names; limited | Native product structure with names and per-node colour; better fit for an assembly |
| Size | Optional mesh compression | Compact (deflate of tessellation) |
| Reader support | Acrobat (and some other viewers) | Acrobat essentially only |

- **Dependencies and licences:** a hand-written U3D writer in Python + numpy needs no new dependency (the repo already uses numpy/scipy). Using the Intel library or the Asymptote PRC writer brings a C++ build step; check the licence of each before vendoring (Apache-2.0 would be fine, LGPL needs the dynamic-link or separate-tool route).
- **Offline / CUI:** yes. Conversion is a pure local file transform; no network call is needed. The converter must never fetch anything and the PDF must be written locally. Add a test that the step runs with sockets disabled.
- **Output size, ~100-part assembly:** our default tessellation (10 um chord tolerance) is built for fidelity, not viewing. Measured on a 99-part sample assembly (81 parts with geometry): about 1.6 million triangles in total. Raw that is roughly 30-40 MB of U3D *(est.)*, about 10-15 MB with U3D mesh compression *(est.)*. With the existing coarse mode (`SLD_TOL=2e-4`) the triangle count drops several-fold, so a 3-6 MB 3D stream per PDF is realistic *(est.)*. A PDF carrying one 3D stream per section multiplies that, so share one stream across sections and switch views instead.
- **Effort *(est.)*:** U3D writer for static meshes with names, colours and hierarchy: 4-6 days including tests. Add named views, mesh compression and the pdf-lib 3D annotation wiring (annotation + stream + default view + JavaScript-free setup): +3-4 days. PRC via a vendored C++ writer: 5-8 days, mostly build and licence work.

## Question 2: what must the 3D data preserve?

| Need | Plan |
|---|---|
| Part names / ids so balloons and selection map to part numbers | One U3D node per instance, named with the plan's stable instance id plus the part key (for example `i<id>:<key>`); the plan event already carries both |
| Colours | Per-part material from the colour the converter already reads; one U3D material per distinct colour |
| Hierarchy | Mirror the plan's `nha`/`level` tree as nested nodes, transforms taken from each instance matrix (row-vector convention in the plan, column-vector in U3D: transpose) |
| Named views | Camera views per section (position, target, roll, field of view) written as U3D view nodes; the PDF annotation references the default one |
| Units | The plan is in metres; set the U3D unit scale explicitly so Acrobat's measurement tools do not misreport |
| Hidden / suppressed instances | Omit, as the GLB path does |

## Question 3: which readers would show it, and how it fails

- **Adobe Acrobat / Acrobat Reader (desktop):** the only reader I can state as reliably supporting embedded U3D/PRC. 3D must be enabled in preferences; Protected View and some managed environments block it. Vendor support for 3D in PDF has been shrinking, so confirm the current Acrobat release behaviour before committing.
- **Others *(unverified)*:** Foxit has historically rendered U3D in its desktop reader; mobile viewers, browser built-in viewers (Chrome, Edge, Firefox), macOS Preview and most print or archive tools show only the poster image or nothing. Treat as unsupported unless a target reader is named and tested.
- **Failure modes:** blank 3D box (3D disabled, Protected View, wrong reader); the fallback poster image shown instead; wrong scale or orientation (transform convention, units); very slow open or rotate (too many triangles); no selection-to-part mapping if node names are lost; PDF/A or print-to-PDF flows dropping the 3D stream; a corrupt stream that makes some readers refuse the whole file.

## Question 4: recommendation

**(b) Link each section to the existing interactive HTML export, for now; (c) park the embedded 3D until the owner names a target reader.**

Reason: the HTML export already carries part ids, colours, hierarchy and selection, runs in any browser, needs no new converter, and avoids the one-reader (Acrobat) dependency and the CUI-review surface of a new binary writer. Embedded 3D only pays off if a named audience must open the PDF offline in Acrobat. If the owner names such a reader, option (a) with U3D, one shared stream and coarse tessellation is the route to cost out first (7-10 days *(est.)*). **Do not start (a) until the owner agrees.**
