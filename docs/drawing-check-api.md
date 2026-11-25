# Drawing check API (helper `serve.py`)

Checks one sheet of a **vector PDF drawing** against the CAD model of an import job: the sheet's dimensions are linked to the model and measured on it (`drawing_link.py`), written into an annotated glTF (`drawing_gltf.py`), and
analysed for what the sheet determines of the model (`determinacy.py`). The work runs in a subprocess (`drawing_check.py`), so a hung read can be killed by the timeout or by cancelling the job. Needs `pdfminer.six`,
`opencv-python-headless` and `scipy` for the interpreter that runs `serve.py` (the check reports `missing-dependency` if they are absent). Same authentication and CORS rules as the rest of the helper. Not for scanned pages (see Limits).

## Start a check

`POST /jobs/<job>/checks` -> `202 {"check": "<id>", "url": "/jobs/<job>/checks/<id>"}`. Two body forms:

- JSON `{"doc": 3, "page": 12, "part": "27244630", "bom_csv": "item,name,description,length,comments\n...", "tol_mm": 0.25}`: `doc` is the index of a PDF the import found (the same index as `GET /jobs/<job>/docs/<n>.pdf`).
- The PDF itself, `Content-Type: application/pdf`, with `?page=12[&part=KEY][&tol_mm=0.25]` (up to 300 MB). BOM text is not accepted in this form.

`page` is 1-based (1..10000). `part` is a converted part key (as in the `part_done` events); without it the whole assembly is used, which needs a finished job with an assembly (otherwise `bad-request`). `bom_csv` (optional, up to 200 kB) is the sheet's
title-block table as CSV with any of the columns item, name, description, length, comments, material; it enables the BOM checks of the determinacy report. `tol_mm` (0.05..5, default 0.25) is how close a dimension end must lie to a
component face to count as touching it. One check runs at a time per job (`409` otherwise). `400` for a bad parameter, `404` for an unknown job, `413`/`415` for a body that is too large or of another type.

## Poll

`GET /jobs/<job>/checks/<id>` -> `{id, state, step, page, part, error, summary, seconds, files}`. `state` is `running` (`step`: read-sheet, link, annotate, determinacy), `done`, `error` or `cancelled`. `error` is `{code, message}` with
`code` one of `missing-dependency`, `bad-page`, `unreadable-pdf`, `no-dimensions` (also a scanned page), `link-failed`, `no-views`, `bad-request`, `timeout` (`--check-timeout`, default 900 s), `crashed`, `failed`.
`summary` (when done) holds **counts only, no drawing text**, so a host may log it: `dimensions`, `status` (counts of pass / fail / unlinked / reference / no-tolerance), `views`, `ambiguous_views`, `general_tolerance`,
`determinacy` (`dimensioned` / `located` / `undefined` component extents), `bom_issues`, `bom_stock_misfits`, `tol_mm`, `files`. `GET /jobs/<job>/checks` lists the checks of a job.

## Download (when `done`)

`GET /jobs/<job>/checks/<id>/<file>` with `<file>` one of `annotated.glb` (the model plus a node per dimension, loads in ipb_viz's Drawing check layer), `determinacy.md`, `determinacy.json`, `results.json`. `results.json` and the report hold
the drawing's dimension text and part numbers: show them to the user, but do not log or post them (the data boundary of ipb_viz: reports about real use are masked). Files live in the job directory and go with it when the job is pruned.

## Limits

Vector PDF pages only; orthographic views only; linear dimensions only (angular, radial, ordinate are skipped); a view whose part is symmetric under a rotation is reported in `ambiguous_views`; the determinacy report works on bounding extents
(see `determinacy.py`). Tested on a synthetic drawing and model (`tests/test_serve_check.py`) and once end to end on the Hydro 27244607 assembly (Standard configuration, page 12 of the TDP package; 13 s, 4 of 6 dimensions linked and passing).
