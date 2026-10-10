# SLDPRT / SLDASM reverse-engineering notes (SolidWorks 2015+ files)

## Container (both SLDPRT and SLDASM)
- Header byte 7 & 7 = rotate-left key for stream names. Chunks are found by scanning for `14 00 06 00 08 00`.
  At marker+6: type byte; +10: u32 checksum (NOT a size/inline flag); +14 csize; +18 usize; +22 name length; +26 name (rotated), then raw-deflate payload.
- Later copies of the chunks (central directory) have no payload; ignore those.
- Older files (pre-2015) are OLE2 (`D0 CF 11 E0`): not handled.

## Geometry (SLDPRT)
- `Contents/Config-N-Partition` = sequence of `[u32 len][16-byte magic][u32 usize][u32 csize][zlib]` sections.
  Section 0 = Parasolid XT transmit (big endian, `PS` header). Section 1 = deltas (ignored).
- XT node stream needs the Parasolid base schema (sch_13006.s_t). Only *transmitted* base fields are in the stream; delta
  instructions C/D count only those. Pointers are i16; node id 1 is NULL.
- Empirical edge rules (validated by watertightness, 0 orientation conflicts, volumes, and matching URDF meshes):
  * start/end vertex = vertices of the halfedge with sense '+'.
  * circle/ellipse arc between two vertices: clockwise about the curve normal if curve sense '+', counter-clockwise if '-'.
  * closed curves (full circle, closed intersection chart): reverse when curve sense is '-'.
  * directed loops: walk `forward`, reverse an edge's polyline when halfedge sense is '-'; arcs are directed B->A.
    On planar faces this gives outer loop CCW / holes CW about the outward normal (19 of 20 test faces).
- Surfaces handled: plane, cylinder, cone, sphere, torus, B-spline; blended (rolling-ball) faces are meshed from their boundary only.

## Assembly (SLDASM)
- `swXmlContents/COMPINSTANCETREE` is plain XML: swFile (paths), swModel (name, configuration, bounding box) and swReference
  (instance: model, configuration, swSuppressed, swHidden, swTransform).
- swTransform = 16 numbers, a row-major 4x4 for ROW vectors: p_world = p * R + t, with t in the last row.
  Nested: M_world = M_child * M_parent. glTF's column-major matrix list equals this list unchanged.
- Sub-assembly contents are inlined in the same XML, so sub-assembly files need not be opened.
- Not decoded yet: `Contents/Config-0-MatesList` (mates), features, configuration tables.

## Validation on ORIGINAL_3_DOF_ARM (against the exporter's URDF meshes)
- link1/2/3 bounding boxes agree to 0.001-0.023 mm; volumes within +0.9..+1.3% (the URDF STLs are coarse inscribed meshes).
- base_link: volume +0.46%; its STL lives in the exporter's Z-up frame (x, -Z, Y).

## Mates (SLDASM `Contents/Config-0-MatesList`)  -- decoded with `mates.py`, `mates_resolve.py`, `joints.py`
- MFC-style serialized archive: header u32 + u16 mate count (11 here); class tags `FF FF <schema u16> <len u16> <name>`
  (later objects of a known class use back-reference tags); strings are UTF-16 CStrings `FF FE FF <len u8>` (may end in a newline).
- Each mate record starts at its name CString (`Concentric1`, `Coincident4`, `Angle1`, `LimitAngle1`, ...). Entities are
  component-path strings (`Arm_link-1@ASSY/Arm_brace-1@Arm_link`). Face entities carry an id-rep string
  (`moFromSktEnt3IntSurfIdRep_c,58,5,-1,0`, `moFromSktEntSurfIdRep_c,31,1`, `moEndFaceSurfIdRep_c,28,1,0`) that equals the part's
  `FACE_ID_2001` face attribute `[code, F, timestamp, 0, I, ...]` -> F,I select the B-rep face. Reference-plane entities have no id-rep.
- Parameters: `moAngleParameter_c` name `D1` then the value (radians). `LimitAngle` also stores two doubles (upper, lower bound)
  right after the value; the two limit mates here both span exactly 250 deg.
- Validation: every Concentric mate is coaxial in the placed assembly (0.0000 mm, 0.0000 deg); both face Coincident mates are coplanar;
  stored angles are reproduced from component origin planes (LimitAngle D1 = pi - angle between the two links' Top-plane normals).
- Joints from Concentric mates: joint1/joint2 axes+origins match the URDF exporter to < 0.002 deg / < 0.004 mm. Joint 2 -> 3 axis
  spacing from the mates is 181.767 mm = the URDF's 0.18177, axes mutually perpendicular. The URDF's link2->link3 frame does not
  reproduce joint 3 under any rpy convention I tried (its STL meshes are in global coordinates), so the mates are taken as authoritative.
- Not decoded: which origin plane a reference-plane entity is (Coincident4/6/12), plane-ref mates' offsets/alignment flags, other mate types.

## Session 2026-10-10 (generalization pass)
Test data: github.com/ros/solidworks_urdf_exporter examples (cloned). Reference = the exporter's STL meshes + URDF joints.

Regression: ORIGINAL_3_DOF_ARM GLB binary chunk is byte-identical to the previous session's output; mates JSON identical
except the Coincident check wording (already changed in the bundled code).

Babylon sandbox (Playwright, file input `#files`, script tools/babylon_check.py): nut, eyebolt, ORIGINAL_3_DOF_ARM GLBs and
nut/eyebolt OBJs all load with no loader errors; 4_WHEELER and 3_DOF_ARM GLBs too. Eyebolt/nut/arm orientation correct (Y-up).

File versions seen: Parasolid schema banners SCH_3000298/3000310_30000_13006 (PS 30.0, 4_WHEELER/3_DOF_ARM, swVersion 11000)
and SCH_3100264_31001_13006 (PS 31.0, ORIGINAL arm). All decode with the 13006 base schema.
4 of 18 example files are OLE2 (pre-2015): Arm.SLDASM, Basic.SLDASM, Stupid_basic.SLDASM, Arm_link1_tube.SLDPRT, BlockA.SLDPRT -> still unsupported.

### Geometry: tube wall with cross holes (new tess path 'strip-holes')
Cylinder face with 2 loops winding once around the axis + N non-winding hole loops (3_DOF_ARM_SEGMENT faces 37/115 failed
with 'hole bridge: no visible edge'). Fix in tess.py `_strip_with_holes`: cut the annulus with a seam placed in the widest
u-gap between holes -> one simple polygon in unwrapped (u,v); interior grid (refine_uv, new `aniso=True` band = 1.3 cells per
axis) + ear-clip; then `refine_periodic_edges` = conforming red-green split of interior edges whose u-span exceeds the chord
angle (face boundary edges never split, so no T-junctions). Result: SEGMENT 0 open / 0 non-manifold edges; cylinder face
areas within 0.01% of the analytic value; part volume 132564.3 mm3 vs analytic 132571 (cross holes approximated) and vs
the exporter STL 132248 (coarse). Only triggers on this face pattern -> ORIGINAL arm output unchanged.
Cost: dense (~35k tris per tube face at TOL=1e-5 m). Tessellation density is a global knob to revisit for facility scale.

### Mates: binary MatesList layout (mates_bin.py)
In the swVersion 11000 assemblies, mate records carry no component-path / id-rep CStrings. Decoded:
- face id-rep = class tag (e.g. moEndFace3IntSurfIdRep_c / moFromSktEntSurfIdRep_c, or a 2-byte back-ref) then
  u32 F, u32 timestamp, i32 I [, i32 a, i32 b for the 3Int classes] = FACE_ID_2001 [code, F, ts, 0, I, a, b].
  Found by searching the record for each part face's (F, ts) pair.
- each entity's id-reps are followed by the component placement: [rotation 9 doubles only if not identity] translation
  3 doubles, 1.0 -> equals swTransform -> identifies the instance (twice per entity).
- face entity: first id-rep = the face, the rest are its neighbours (a box face lists its 4 neighbours).
  Edge entity (moCircleRef_w + moCompEdge_c): id-reps are the edge's two adjacent faces. Selection rule: first id-rep whose
  face kind suits the mate (cylinder/cone for Concentric, plane otherwise).
- Distance D1 stored in metres (same CString + 2.0 + value layout as angles).
Validation: 4_WHEELER 8/8 mates hold in the placed assembly (4 Concentric coaxial 0.0000 mm/0.0000 deg; 4 Coincident
coplanar, 0.00000 mm); its 4 wheel joints match the URDF (axis lines y/z offsets -9.974/+-50 mm, x along axle).
3_DOF_ARM 5/6 mates hold (3 Concentric coaxial 0.0000 mm, Coincident1 coplanar, Distance1 gap 10.00000 mm = stored D1);
Coincident2 is a reference-plane entity (no id-rep), not decoded. Joint axes: prox at x=2.491 mm, dist at y=189.21 mm,
matching URDF prox_joint xyz 0.00249 and dist_joint offset 0.18921; effector axis perpendicular to and intersecting the
dist axis, consistent with the URDF.
