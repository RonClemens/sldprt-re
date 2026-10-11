# Viewer-side code for the progressive SolidWorks import

Nothing here is applied to `ipb_viz` automatically. This repo never commits to that repo.

- `sld-helper-client.js` - framework-neutral client for `src/serve.py` (health, upload with progress, SSE events, part fetch, cancel).
- `sld-progressive-import.js` - Three.js adapter: boxes from the `plan` event, real geometry swapped in per `part_done`, red boxes for
  failures, a "show unresolved parts" toggle, Cancel / Keep partial. Stable mesh order, so part numbers never shuffle.

Landed in `ipb_viz` directly (the two files above + hooks in `app.js`, `index.html`, `style.css`) — see
[ipb_viz#1](https://github.com/RonClemens/ipb_viz/pull/1), merged. The `ipb_viz.patch` file that carried this change
over is gone now that it has; these two files stay here as the source of truth for the viewer side.

## Run it on a desktop

    pip install numpy scipy
    SLD_TOL=5e-4 python3 src/serve.py --port 8765 --work ~/sldwork        # add --token SECRET to require a token

Open the ipb_viz page in Chrome/Edge (Firefox should work; Safari may block https->loopback), click **Import CAD file**, pick
`Hydro_CAD.zip`. Optional settings: `?sldhelper=http://127.0.0.1:8765&sldtoken=SECRET` (or `localStorage.sldHelperUrl` / `sldHelperToken`).

## Develop / test without converting

    python3 src/serve.py --port 8766 --replay <recorded-job-dir> --speed 15
    python3 tests/e2e_viewer.py <ipb_viz checkout> <recorded-job-dir> <screenshot-prefix> 15

A job directory is recorded automatically (`events.jsonl` + `parts/`) every time the helper converts something.
`tests/e2e_viewer.py` drives headless Chromium through the whole flow (file picker -> boxes -> geometry in place -> done).

## Phone / tunnel

See `docs/phone-access.md`. `tests/test_phone.py` covers the helper endpoints and the JS client's resume logic (needs Node 18+).

## Work directory, timeouts, cache

By default the helper keeps its cache and the last jobs in `~/.sldprt-re/work` (override with `--work DIR`), so a restart does not
start from a cold cache. Only the newest two jobs are kept (each holds the extracted ZIP). A part that exceeds `--timeout` (default 600 s)
is not reported as failed straight away: it emits `part_retry` and is retried once with 2x the time (`--retry-factor`) and half the parallelism. Each
worker is limited to one numpy thread. Every job writes `events.jsonl` in its folder; `grep part_failed events.jsonl` lists what failed and why.
