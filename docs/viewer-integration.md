# Viewer integration spec: progressive SolidWorks import for ipb_viz

Status: **helper and viewer patch implemented** (see `viewer/README.md`; the ipb_viz change is a patch file, not yet applied). This repo owns the converter side (sections 2-4); the viewer side
(section 5) is written down as a contract so it can be built separately. `ipb_viz` was read, never modified, to write this.

## 1. Goals

From the viewer's existing **Import CAD file** dialog, a user drops either
- a single `.sldprt`,
- a single `.sldasm` (parts alongside it are optional), or
- a SolidWorks **Pack and Go** `.zip` (flat folder of parts + assemblies; the converter already matches parts by file name,
  so a flat or nested layout both work),

and sees: (1) progress, (2) the assembly's *shape* almost immediately, (3) real part geometry appearing in its correct place
as each part finishes, (4) a usable partial model if anything stalls or fails.

## 2. What the viewer does today (read from `ipb_viz` @ 76ab6b1)

- `handleCadFile(file)` takes **one** file; `.sldprt/.sldasm` are rejected with a "convert to STEP first" message.
- `.glb` is parsed whole with `GLTFLoader.parse` -> `showImportedCad(group)`, which clears any other asset, calls
  `collectCadParts()` (numbers every `isMesh` by traversal order; used by the sidebar list, click-highlight, B/W callouts and
  the assembly manual), applies the orientation guess (`+Y` up / `+Z` front for `.glb`), and fits the camera.
- Every mesh is treated as one part, so the viewer expects **one Mesh per instance** (shared geometry is fine).
- Pack-and-Go bundles are already a concept in the repo (Windows-only `convert-solidworks*.ps1`: "if a bundle contains any
  `.sldasm`, only assemblies are converted"). The new path follows the same rule and needs no SolidWorks install.

## 3. Where conversion runs

GitHub Pages is static, so conversion cannot run in the page for large assemblies.

**Chosen: local helper** `python3 src/serve.py` (to be written), listening on `127.0.0.1:<port>`. The page talks to it with
`fetch` + Server-Sent Events. Nothing leaves the user's machine and there is no size limit. A hosted service and an in-browser
(Pyodide) path were considered and deferred (a 1.4 GB ZIP does not fit comfortably in browser memory).

Items to verify before building the viewer side (cannot be tested from this environment):
- An `https://` Pages origin calling `http://127.0.0.1` (allowed in Chromium/Firefox as a secure-context exception; Safari may
  block it). Chromium's Private Network Access preflight needs `Access-Control-Allow-Private-Network: true` from the helper.
- The helper sends `Access-Control-Allow-Origin` for the Pages origin only, and binds to loopback only.

## 4. Helper API (converter side)

All bodies JSON unless noted. Coordinates: **metres, SolidWorks' own frame (Y-up)**, i.e. the frame the current GLB export
already uses (no root rotation). Matrices are 16 numbers in glTF column-major order (the same list written into GLB nodes),
so `new THREE.Matrix4().fromArray(m)` applies directly.

| Endpoint | Purpose |
|---|---|
| `POST /jobs` | Body = the raw file (`.sldprt`, `.sldasm` or `.zip`), header `X-Filename`. Streams to disk. Returns `{job}`. |
| `GET /jobs/{job}/events` | SSE stream of the events below (replays history on reconnect via `Last-Event-ID`). |
| `GET /jobs/{job}/parts/{key}.glb` | One part, in part coordinates, shared by all of its instances. |
| `GET /jobs/{job}/assembly.glb` | The assembly with whatever has finished so far (the "export partial" button). |
| `POST /jobs/{job}/cancel` | Stops workers; finished parts stay available. |
| `POST /jobs/{job}/select` | Body `{"all": true}` or `{"instance_ids": [...]}`. Answers the pause described under `plan` below. Returns `{ok}`; `ok: false` means the job already passed the gate (done, or already answered) and this had no effect. |
| `GET /health` | `{version, ok, auth, replay}` so the page can tell the helper is not running and show how to start it. |

### Events (in order of first occurrence)

- `plan` - sent within ~seconds, **before any geometry**. Built from the assembly's component tree (plain XML with a bounding
  box per component):
  `{name, instances:[{id, path, part, name, nha, level, matrix, bbox:[min3,max3], hidden, suppressed, assembly?, key?, file?}], parts:[{key, file, bytes}], missing:[file], sldasm_files_found, top_level_assemblies:[file], assembly_refs, part_refs}`.
  `id` is the stable per-instance index (0..n-1); it never changes for the life of the job. `level` is the indenture level
  (1 = direct child of the top assembly, incrementing per nesting level); `nha` is the "next higher assembly" -- the
  immediate parent's own key, `null` for a direct child of the top assembly. A row with `part: null` and `assembly: true`
  is a sub-assembly, not a part: it carries `key` (its own part-number-equivalent, from its `.SLDASM` filename) and `file`
  instead of `part`, exists purely to give the indentured tree structure and an `nha` to look up, and is never converted.
  A lone `.sldprt` is a plan with one identity-matrix instance (no assembly rows, `nha: null`, `level: 1`).
  The last four fields are a diagnostic for "why is everything the same level": `sldasm_files_found` is how many `.sldasm`
  files the upload contained at all; `top_level_assemblies` is which of those `plan()` actually walked as a top-level
  assembly (normally exactly one -- more than one either means the ZIP genuinely bundles unrelated assemblies, or that
  `top_level()` in `batch.py` failed to recognize one of them as referenced by another and is worth a second look);
  `assembly_refs`/`part_refs` count, across the whole walk, how many references resolved to another `.sldasm` vs. a leaf
  `.sldprt`. `assembly_refs: 0` against a real, present `.sldasm` means the component tree itself has no sub-assembly
  nesting in it -- every part really is a direct child of the top assembly in SolidWorks' own data, not a parsing bug.
  **The job then pauses** (see "Server behaviour" below) until the page answers with `POST /jobs/{job}/select`.
- `convert_started {instances_selected, instances_total}` - sent once the pause above is resolved, before the first
  `part_started`. `instances_total` counts every leaf (non-assembly) instance from `plan`; `instances_selected` is how many
  will actually be converted.
- `part_started {key}` / `part_done {key, url, tris, failed_faces, sec}` / `part_failed {key, reason}`
  (reasons are short codes: `timeout`, `unsupported_surface:<type>`, `ole2`, `no_body`, `missing_file`, `error:<text>`).
- `progress {parts_done, parts_total, instances_ready, instances_total, elapsed_s}`. `instances_total` here is
  `instances_selected` from `convert_started`, not every instance in `plan` -- a deselected part is never "pending", it
  simply never existed for the rest of the job.
- `mates {joints, mates}` - the decoded mates/joints (same content as the GLB root `extras` today); sent when available.
- `job_done {ok, parts_ok, parts_failed, seconds}`. A stalled job emits no further events; the helper also sends a `: keep-alive`
  comment every 10 s so the page can tell "slow" from "dead".

### Server behaviour

- Parts are converted in parallel in **separate processes with a hard kill on timeout** (per-part default 600 s, configurable with `--timeout`; a part that times out is retried once with `--retry-factor` times that).
  Hangs inside native numpy code cannot be interrupted by an in-process alarm; this was observed on the facility data.
- Results are cached on disk by file hash (+ bounding box), so re-importing is instant and a cancelled job loses nothing.
- Order: smallest parts first (fast visible progress); heavy outliers last.
- A part referenced by an assembly but absent from the upload is reported as `missing_file` and stays a box.
- One job at a time per helper by default; a second import cancels the first.
- **Selection gate:** after emitting `plan`, the job always pauses (up to `--select-timeout`, default 1800s/30min) for a
  `POST /jobs/{job}/select`. This lets the page show a "pick which assemblies/parts to load" modal for a large import
  *before* spending any CPU converting parts the user doesn't want, without the helper needing to know or care how big
  "large" is -- that threshold lives entirely in the page, which can just answer `{"all": true}` immediately for a small
  import so the pause is imperceptible. A client that never answers (closed tab, a viewer built before this existed) does
  not hang the job forever: past the timeout the helper proceeds as if `{"all": true}` had been posted, i.e. exactly the
  old unconditional behavior. `POST .../cancel` while paused wakes the job immediately instead of waiting out the timeout.

## 5. Viewer contract (to be implemented in ipb_viz, not here)

1. **Entry points.** Drop/browse accepts `.sldprt .sldasm .zip` in addition to today's formats; `.zip`/SolidWorks files go to the
   helper, everything else keeps its current path. If `/health` fails, show "start the helper: `python3 src/serve.py`" instead of
   the current convert-to-STEP message.
2. **On `plan`:** decide which instances to actually load, then `POST /jobs/{job}/select` to unblock the job (see "Selection
   gate" above) -- below some instance-count threshold, answer `{"all": true}` immediately so small imports see no change at
   all; above it, show a modal built from `plan.instances` (sortable/filterable by `level`, `part`/`key`, `name`, `nha`) and
   answer with `{"instance_ids": [...]}` once the user confirms. **Check the response's `ok`.** `ok: false` means the gate had
   already closed (the `--select-timeout` elapsed first) and the job is already converting everything regardless of what was
   picked -- a selection that arrives late is silently discarded server-side, never queued or retried, so a page that doesn't
   check this will show a scene built for a selection the helper never used while it keeps converting far more in the
   background. On `ok: false`, fall back to loading everything client-side too (matching what's actually happening) and tell
   the user why, rather than building a group for instance ids the helper ignored. Only then create the import group and, for each *selected,
   non-assembly* instance, one `Mesh` with a `BoxGeometry` sized from `bbox`, a flat translucent "pending" material, `matrix`
   applied, `name` = last path segment, `userData.instanceId = id`. Then call the existing `collectCadParts()` and fit the
   camera **once**. Part numbers are therefore stable for the whole import (they are assigned from the selected instances'
   fixed order), so selection, the sidebar list and callouts do not shuffle as parts arrive. A deselected instance is simply
   never added to the scene -- not shown as a box, not counted anywhere.
3. **On `part_done`:** fetch the part GLB once, take its geometry/material, and assign `mesh.geometry` (shared) to every pending
   mesh of that part. Do not recreate meshes, do not renumber, do not refit the camera. Dispose of the box geometry.
4. **On `part_failed`:** leave the box, tint it red, tooltip = reason; list it in the progress panel.
5. **Progress panel** in the import dialog / sidebar: a bar from `progress`, counts `ready / total`, failed list, elapsed, and a
   "stalled?" hint if no event arrives for >30 s. Buttons: **Cancel** and **Keep partial model** (close the dialog, leave the
   scene as it is; boxes remain for unfinished parts).
6. **Orientation.** Use the `.glb` default (`+Y` up, `+Z` front) so the existing opt-in Orientation control still works.
7. **Scale/performance.** The viewer makes one `Mesh` per instance. The facility's biggest assembly has 2,890 instances, so
   expect to need either `InstancedMesh` per part or an option to hide/skip small hardware; decide after measuring in the viewer.
8. **Unresolved parts toggle.** Boxes for parts that failed or never finished are controlled by a user toggle ("Show unresolved
   parts", on by default while importing). It applies to the 3D view, the B/W callout diagram and the assembly manual; when off,
   those parts are hidden and skipped in the parts list numbering used for output. They are never drawn as if they were real geometry.

## 6. What this repo will deliver

1. `src/serve.py` implementing section 4, built on `src/batch.py` (already does extraction, the process-per-part pool with kill on
   timeout, the disk cache and a per-file report).
2. Per-part GLB output + the `plan` builder (component-tree walk already exists in `sldasm2glb.py`).
3. A fake-data mode (`serve.py --replay report.json`) so the viewer can be developed without waiting for real conversions.
4. Converter fixes found on real data (listed in the README status section as they land).

## 7. Decisions

- **Packaging:** Python 3 on the user's machine is required for now (`pip install numpy scipy`). No bundled executable.
- **Large assemblies:** left to a performance evaluation once the viewer path exists. Default is one `Mesh` per instance; switch to
  `InstancedMesh` per part and/or hide-small-hardware only if measurements on the facility assembly demand it.
- **Unresolved parts:** the user can toggle them on/off. Each failed or pending part keeps its box; a "Show unresolved parts" toggle
  (sidebar + manual options) controls whether the boxes are shown in the 3D view, the B/W diagram and the assembly manual.

- **Desktop first.** Browser and helper on the same machine (`127.0.0.1`), so no tunnel is needed. An optional `--token` (header or `?token=`) is supported. The Android phone is a later viewer-only client (load the exported GLB, or use a tunnel to the desktop helper).
- **Delivery to ipb_viz:** landed via [ipb_viz#1](https://github.com/RonClemens/ipb_viz/pull/1), merged.
