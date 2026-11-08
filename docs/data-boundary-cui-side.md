# The CUI side's half of the data-boundary contract

Companion to `docs/data-boundary.md` (contract `ipb-boundary/1`). The public repositories (ipb_viz, sldprt-re, PcbPrj-re) can check what they publish; they cannot see the private repositories, so nothing here is enforced
until **each CUI-side repository adopts it**. Copy `tools/cui-side-kit/` into the private repository (it contains no data and nothing specific to any program) and fill in the two local files named below.

## What a CUI-side repository must do

1. **Never push to a public remote.** Install `tools/cui-side-kit/pre-push-remote-allowlist.sh` as the repository's pre-push hook and list the approved remote URLs, one per line, in `.cui-allowed-remotes` (committed, reviewed). The hook refuses any push to a remote not on the list, and refuses a remote whose URL matches a public-hosting pattern in `.cui-public-patterns` unless it is explicitly allow-listed. (A hook can be skipped with `--no-verify`; the repository's server-side settings are the backstop: only approved remotes configured, push restricted to the team.)
2. **Consume the public repositories read-only, pinned, verbatim.** `tools/cui-side-kit/verify-vendored.sh <dir> <repo-url> <tag> <expected-sha>` checks that a vendored copy matches the tag's commit and has no local edits; run it in the host's sync and in CI. Changes to public code go back through section 6 of the contract, never by editing the vendored copy.
3. **Report in masked form only** (`tools/cui-side-kit/masked-report-template.md`): pass or fail, counts, hashes, field names, generic error text. No part numbers, program or host names, user ids, controlled URLs, screenshots or console output that contain real data.
4. **Keep sessions separate.** A person or AI session on the CUI side has the public repositories read-only or not at all; a session on the public side never gets CUI input. The session's attached-repository list is the boundary.
5. **Supply real values at run time** to the viewer (the embed messages, the `[[CUI-STUB:<id>]]` inputs) in the page's memory for that person's session; never bake them into a file the public side builds or vendors back.
6. **Run the same check on anything that could travel**: `node tools/boundary-check.mjs --range` (with the private deny-list) on any patch proposed for a public repository, before a person reviews it (contract section 6).
7. **Keep the deny-list private and current**: one term per line at `~/.config/boundary/denylist.txt` on each person's machine and as the `BOUNDARY_DENYLIST` secret on the public repositories. It is never committed anywhere public.

## Mirroring and testing with the public samples

Everything on a public repository's `main` is public, so the CUI side may mirror it, pull it and test with it. What is there, and what is not (as of 2026-11-08):

| Repository | Data on `main` | Status in its manifest |
|---|---|---|
| ipb_viz | 47 files, about 84 MB: `tools/examples/` (bike, brick house, enclosure, demo, nested STEP rack, block diagrams, the real SolidWorks robot-arm Pack-and-Go sample with placeholder drawings, the federated enclosure + Altium board + harness set), `sources/Hydro_Docs/` (the drawing package and O&M manual), `tools/examples/hydro/` (the converted Hydro assembly, BOM, drawings, index), `docs/IADSProgrammingGuide.pdf` | 15 Hydro files `owner-attested-public`, 9 federated files `generated`, 23 `unreviewed` (still public; awaiting a person's decision) |
| PcbPrj-re | 58 files, about 166 MB: `samples/` (five open-hardware boards as SVG schematics, glTF boards, link tables; upstream repository, pinned commit and licence in `samples/sources.json`), `docs/` screenshots | `unreviewed` (grandfathered; open-licence origin documented) |
| sldprt-re | none by design: native CAD, archives, glTF and converted models are git-ignored | none |

**Not on `main`, so a mirror of `main` alone cannot test it:**
- **The native Hydro assembly** (`Hydro_CAD.zip`, 440 SolidWorks files): only what was derived from it is committed. Every sldprt-re command that takes it (`survey.py`, `batch.py`, `structure.py`, the progressive import) needs the file itself, and sldprt-re's `.gitignore` refuses native CAD and archives. It would have to be published some other way (a release asset with its SHA-256 recorded in the manifest) or supplied by the CUI side from its own copy.
- **sldprt-re's regression inputs:** `tests/regress.py` clones `github.com/ros/solidworks_urdf_exporter` at test time (unpinned, depth 1) and compares with `tests/expected.json`. A CUI-side mirror needs network access to that repository, or its own mirror of it.
- **PcbPrj-re's upstream designs:** `tools/build_samples.py --fetch` clones each upstream repository at its pinned commit; the committed `samples/` is the derived output. Rebuilding needs those upstream hosts.
- **Generated outputs that need extra tools** (the enclosure and brick-house drawing PDFs need `pymupdf` to regenerate); the committed copies are what the tests use.

**Proving the mirror is the public set.** In the mirror of a public repository:

```bash
node tools/boundary-check.mjs --verify-samples <mirror-dir>     # every file its manifest lists is present with the recorded SHA-256; data files it does not list are noted as yours
```

Run it after each sync, next to `verify-vendored.sh`. It needs only Node and the mirror's own `data-boundary/manifest.json`; no git, no network. Files of your own (private samples) in the same folder are reported as `mirror.extra`: keep them in a separate directory of the CUI repository, never inside the mirror, so a sync never overwrites them and a push never carries them.

## Release checklist (CUI -> public, code or documentation only)

- [ ] The change can be explained without the data.
- [ ] Made in a clean checkout of the public repository, no controlled file open, no CUI-side repository attached to the session.
- [ ] Fixtures are synthetic and generated by a tracked script.
- [ ] `boundary-check` and `boundary-check --history` pass with the private deny-list.
- [ ] The pull-request title and text are generic.
- [ ] A person with authority has read the diff and approved it.

## If something went the wrong way

Stop, tell the security officer, and follow the organisation's procedure first; then section 7 of the contract. Do not "fix" it by deleting the file and pushing again.

## Open items on this side

Name the CUI-side repositories and their approved remotes; name who may approve a release; decide whether CUI-side CI should run `verify-vendored.sh` and the hook check on every push.
