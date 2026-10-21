# BOM structure of a package (`src/structure.py`)

Makes one reconciled, indented BOM from a Pack-and-Go folder or zip (SLDASM / SLDPRT / SLDDRW), optionally with a drawing-package index
(`tools/index-tdp.py` in ipb_viz). The CAD component tree is the skeleton; what is known about each node from other places is laid on it, and
every disagreement is reported instead of being hidden.

```
python3 src/structure.py Hydro_CAD.zip -o hydro --tdp-index tdp-index.json [--root 27244600.SLDASM] [--part-number-from properties|file-name]
```
Writes `hydro.csv` (the viewer opens it as its 1D tree) and `hydro.json` (summary, rows, discrepancies). Needs only the container readers; it
reads each file's streams, never its geometry (about 20 s for the 440 files of Hydro_CAD).

## Rules
| Column | Taken from, strongest first |
|---|---|
| Part Number | a part number the file's own properties set (the configuration's first), else the file name. With `--part-number-from file-name` always the file name (for a static converted model that carries no properties); the property number is in its own column either way. This column is what the viewer matches a BOM row to a CAD part by, so nothing else goes into it. |
| Description | `Description` (a `-` placeholder counts as empty), `Name`, `Desc1 + Desc2` (McMaster style), the drawing's title in the TDP index, a name made from the file |
| Drawing | a `.SLDDRW` named like the model, or like its part number; the `SEE DWG n` comment property; a TDP drawing; a TDP drawing number the file name starts with (marked *inferred*) |
| Qty | identical siblings (same file and configuration) are one row; a repeated sub-assembly is listed once and its quantity counted at its parent |
| Make/Buy | Buy when the file name starts with a vendor part number (`91251A621_...`) or a `Vendor` property is set, else Make |
| Manufacturer, Material, Vendor P/N, Property P/N, Configuration, File | as named |

`Level`, `Part Number`, `Item No.` (outline number), `Description`, `Qty`, `Make/Buy` and `Envelope W x H x D (mm)` come first, in the order the
viewer's earlier example BOM used.

## Discrepancies (`.json`)
- `assembly_without_drawing`: a CAD assembly nothing in the package draws.
- `sub_assemblies_differ`: the assembly's drawing prints a different set of "SEE DWG" sub-assemblies than the CAD tree holds (lists what is only on the drawing and what is only in CAD).
- `drawing_without_model`: a `.SLDDRW` that no model matches by name.
- `tdp_drawing_not_matched`: a drawing in the TDP index (with a parts list) that no CAD file matches.

## Hydro_CAD result
534 rows, 53 assemblies; reproduces all 488 parent/child relationships of the earlier hand-made BOM and adds the imported STEP components and
virtual saddle parts it left out; 98 rows have a drawing; 213 rows take their part number from a file property (`PartNo`, per configuration
where set). Loaded in the viewer against the Hydro model: 387 parts matched and no unmatched CAD parts (before: 362 and 39).
