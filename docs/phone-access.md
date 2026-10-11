# Using the converter from an Android phone

The conversion always runs on the desktop (a phone cannot convert a 1.4 GB assembly). The phone is a client of the desktop helper.
It reaches it over an **HTTPS tunnel**, because an https page (GitHub Pages) cannot call a plain-http LAN address.

## 1. Start the helper with a token and a tunnel (on the desktop)

    SLD_TOL=5e-4 python3 src/serve.py --token auto --public-url https://<your-tunnel-host>

Pick one tunnel:

| Tunnel | Command | Notes |
|---|---|---|
| Cloudflare quick tunnel | `cloudflared tunnel --url http://127.0.0.1:8765` | No account. Prints `https://<random>.trycloudflare.com`. Requests are capped (~100 MB), which is why uploads are chunked (32 MB). |
| Tailscale Funnel | `tailscale funnel 8765` | Stable URL; install Tailscale on the desktop only. |

The helper prints a ready-to-open link: `https://ronclemens.github.io/ipb_viz/?sldhelper=<tunnel>&sldtoken=<token>`. Open it on the phone
(the viewer remembers the helper URL and token in that browser's localStorage). The token is the only protection while the tunnel is up:
use `--token auto`, do not share the link, stop the tunnel when done.

## 2. Three ways to get the ZIP converted

1. **Pick the ZIP on the phone.** Uploaded in 32 MB chunks (`POST /uploads`, `PUT /uploads/<id>` with `X-Offset`, `POST /uploads/<id>/complete`);
   a dropped connection resumes at the last good byte. Slow on mobile data, fine on Wi-Fi.
2. **Import from URL** (no phone upload at all). The helper downloads, e.g., the GitHub release asset itself: `POST /jobs` with
   `{"url": "https://github.com/<owner>/<repo>/releases/download/v0.0.1/Hydro_CAD.zip"}`. Needs `--token`; https only; hosts must be on
   `--allow-host` (default `github.com`, `githubusercontent.com`; every redirect hop is re-checked; private addresses are refused).
3. **View only.** Start the import on the desktop, then open the same helper link on the phone and attach to the running or finished job
   (`GET /jobs` lists them; `GET /jobs/<id>/events` replays the whole history, so the phone sees the model fill in from the start).

## 3. Status

Helper side: implemented and tested (`tests/test_phone.py`: auth, resume after a wrong offset and after an injected network failure,
URL guards). Client side: `uploadResumable`, `importFromUrl`, `listJobs` in `viewer/sld-helper-client.js`, and
`startProgressiveImport({ url })` / `({ job })` in `viewer/sld-progressive-import.js`.
Not yet done: the ipb_viz UI for "import from URL" and "attach to a running job" (and a `download` phase label in its progress card),
and a check on a real phone through a real tunnel. The ipb_viz copies of the two client files need syncing from `viewer/` once the desktop
tuning there settles.
