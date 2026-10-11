# Viewer-side code for the progressive SolidWorks import

Nothing here is applied to `ipb_viz` automatically. This repo never commits to that repo.

- `sld-helper-client.js` - framework-neutral client for `src/serve.py` (health, upload with progress, SSE events, part fetch, cancel).
- `sld-progressive-import.js` - Three.js adapter: boxes from the `plan` event, real geometry swapped in per `part_done`, red boxes for
  failures, a "show unresolved parts" toggle, Cancel / Keep partial. Stable mesh order, so part numbers never shuffle.
- `ipb_viz.patch` - the complete ipb_viz change (the two files above + hooks in `app.js`, `index.html`, `style.css`).
  Apply in an ipb_viz checkout: `git apply /path/to/sldprt-re/viewer/ipb_viz.patch`. Not applied yet; a PR will only be opened when authorized.

## Run it on a desktop

    pip install numpy scipy
    SLD_TOL=5e-4 python3 src/serve.py --port 8765 --work ~/sldwork        # add --token SECRET to require a token

Open the ipb_viz page in Chrome/Edge (Firefox should work; Safari may block https->loopback), click **Import CAD file**, pick
`Hydro_CAD.zip`. Optional settings: `?sldhelper=http://127.0.0.1:8765&sldtoken=SECRET` (or `localStorage.sldHelperUrl` / `sldHelperToken`).

## Develop / test without converting

    python3 src/serve.py --port 8766 --replay <recorded-job-dir> --speed 15
    python3 tests/e2e_viewer.py <ipb_viz checkout with the patch> <recorded-job-dir> <screenshot-prefix> 15

A job directory is recorded automatically (`events.jsonl` + `parts/`) every time the helper converts something.
`tests/e2e_viewer.py` drives headless Chromium through the whole flow (file picker -> boxes -> geometry in place -> done).
