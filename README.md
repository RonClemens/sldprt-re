# sldprt-re

Converts SolidWorks part and assembly files (`.SLDPRT` / `.SLDASM`, SolidWorks 2015+ format) to glTF binary (`.glb`)
without SolidWorks or a CAD kernel. Pure Python + numpy.

Pipeline: file container → Parasolid XT B-rep → per-face tessellation → GLB (+ OBJ for parts), with the assembly tree
as glTF nodes and decoded mates/joints in the root node's `extras`.

Target consumer: [ipb_viz](https://ronclemens.github.io/ipb_viz/) (loads `.glb` through its Import CAD button).

## Usage

```bash
pip install numpy scipy
cd src
python3 sldprt2glb.py  part.SLDPRT --schema sch_13006_s_t.txt -o out
python3 sldasm2glb.py  asm.SLDASM  --schema sch_13006_s_t.txt -o out [--include-hidden]
python3 sldasm_mates.py asm.SLDASM --schema sch_13006_s_t.txt -o mates.json
```

Assembly conversion expects the referenced part files in the same folder as the `.SLDASM` (matched by file name).
GLB is in metres, Y-up; OBJ is in millimetres.

## Tests

```bash
python3 tests/regress.py          # converts the public ROS example assemblies, compares to tests/expected.json
python3 tests/regress.py --update # accept new results as the baseline
```

`tools/glbstat.py` prints per-mesh triangle count, open/non-manifold edges, volume and bounding box.
`tools/babylon_check.py` loads models into sandbox.babylonjs.com with Playwright and saves screenshots.

## Status

See [NOTES.md](NOTES.md) for the decoded file structures, the empirical rules and how each was validated.

Supported: SolidWorks 2015+ container; Parasolid schema 13006 base (tested with PS 30.0, 31.0, 35.1 files);
plane, cylinder, cone, sphere, torus, B-spline faces; assembly transforms, suppressed/hidden instances,
configuration-specific children; mates in both the string and the binary MatesList layouts
(Concentric, Coincident, Distance, Angle, LimitAngle).

Not supported yet: pre-2015 files (OLE2 container); reference-plane mate entities; blend (rolling-ball) faces and
thread B-spline faces are meshed from their boundary only; tessellation density is not configurable yet (fine, 0.01 mm).

## Data handling

This repo holds code and public test references only. CAD files and converted models are git-ignored on purpose;
program or facility data must never be committed here.
