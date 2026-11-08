# Data-boundary contract between the public repositories and the CUI side (`ipb-boundary/1`)

Applies to: **ipb_viz**, **sldprt-re**, **PcbPrj-re** (the *public side*, each of which says it never contains controlled content) and every private repository, environment or workflow that holds real
program data (the *CUI side*). The same text is kept in all three public repositories; `tools/boundary-check.mjs` is the same file in each. Version line above; change both together.

This document turns the sentence "this repository never contains CUI" into obligations and checks. Read section 9 (residual risk) before relying on it: the controls make a spill **detectable before
publication and unlikely to go unnoticed**; none of them makes a careless or determined person impossible to stop, and nothing here replaces the organisation's own CUI policy, which wins where they differ.

## 1. Words used

- **Controlled data**: CUI; anything subject to export control; proprietary, program, facility or customer data; personal data. When in doubt it is controlled.
- **Derived data**: anything computed from, copied out of, or recognisable as coming from controlled data: geometry, BOMs and part numbers, drawing and file names, error messages, test fixtures made "from a real file", screenshots, logs, counts that identify a program. Derived data is controlled until a person with authority says otherwise.
- **Publish**: reach any of: a public repository (**any** branch, tag, fork or pull-request ref: a branch is not a boundary), GitHub Pages, a release asset, an issue or pull-request or comment, a CI log or artifact, a package registry, or an AI-session transcript that can be shared.
- **Masked**: carrying only pass or fail, counts, hashes, field names and generic error text (the form `docs/cui-host-verification.md` already uses).

## 2. Direction of flow

| From -> to | Rule |
|---|---|
| Public -> CUI | Allowed and expected: code, schemas, docs, tests, synthetic examples. Pulled by a pinned tag or commit and vendored **verbatim** (the CUI side never edits what it vendored; the host's sync checks the SHA). |
| CUI -> public | **Forbidden** for data and for anything derived from it. The only upward path is a *release* (section 6): a code or documentation change that is expressible without data, written without the data open, reviewed by a person. |
| CUI -> public, reports | Masked only. Failures come back as text a person has read, never as attachments, screenshots or console dumps. |
| Public -> public | The three repositories may read each other's **published** data (PcbPrj-re board data is fetched by ipb_viz from GitHub Pages) and vendor each other's code verbatim (`vendor/pcbprj-re/paths.js`, drift-checked by a test). |

## 3. What may be committed to a public repository

Code, documentation, tests and **data of exactly four classes**, each recorded per file in `data-boundary/manifest.json`:

| Class | Meaning | Evidence recorded |
|---|---|---|
| `synthetic` | Invented for the repository. | The tracked script that makes it from tracked inputs (`generated`, checked by CI), or a person's statement. |
| `open-licence` | From a design or data set published under an open licence. | Upstream URL, pinned commit, licence, and the `NOTICE.md` that carries the attribution. |
| `third-party` | Another party's, with a right to redistribute. | Where the right comes from. |
| `owner-attested-public` | Real program material that a named person with authority has cleared for **public** release. | Who, when, and where the clearance record is kept (the record itself is not committed). |

Anything else is not committed, however useful it is for testing. A file's status is `reviewed` (a person decided), `generated` (a tracked script reproduces it byte for byte; no human claim) or `unreviewed`
(grandfathered from before this contract: allowed to stay, shown as a warning on every run, and removed or attested by the owner). `file.changed` fails CI whenever a recorded file changes, so a reviewed file cannot be swapped silently.

## 4. Where controlled data lives instead

In private CUI-side repositories and environments only. **Never** on a branch, tag, fork, gist, release or LFS object of a public repository, and never in the public repository's Pages build, even "temporarily". (Repository
visibility, forks, pull-request refs, a `workflow_dispatch` and a plain clone all reach every branch. The earlier plan of a `dev/cui-templates` branch in a public repository is withdrawn; `TODO.md` is corrected.)

How real values still reach the viewer: **inject, never commit.** Public code defines placeholders (`[[CUI-STUB:<id>]]`, the embed messages `s1000d-project`, `load-bom`, `instance-refdes`, `load-datasheets`, ...); the CUI-side host supplies values at run time, in the page's
memory, for the person's own session. Nothing the host injects is written into an artifact the public side builds.

## 5. Controls

| # | Control | What it does | Enforced by | Does not cover |
|---|---|---|---|---|
| C1 | Manifest of data files | Every data-like file (PDF, CSV, glTF/GLB, STEP, archives, images, office files, ...) has a record: class, status, evidence, SHA-256. An unlisted data file, or a recorded file that changed, fails. | `tools/boundary-check.mjs` in CI, pre-commit, pre-push | Whether the *evidence* is true: `reviewed` is a person's claim. |
| C2 | Marking scan | Banners, portion marks, designation indicators, distribution statements other than public release, export-control and handling caveats, proprietary legends; in text, PDF text (poppler's `pdftotext`, so embedded fonts decode), glTF strings, archive members and their names. Hits are reported by rule, file and line or page; **the matched text is never printed**, so a CI log cannot become the spill. | same | Bitmaps (reported as `scan.bitmaps`), unreadable fonts (`scan.unreadable`), unmarked controlled data. |
| C3 | Private deny-list | Terms only the owner knows (program and host names, part-number prefixes, people). Supplied at run time (`~/.config/boundary/denylist.txt`, `$BOUNDARY_DENYLIST`, or the repository secret `BOUNDARY_DENYLIST`); **never committed**. | same | Runs that do not have the list (a fork's pull request, a clone without it): every run says so. |
| C4 | Hashed terms | Salted SHA-256 of terms (`data-boundary/hashed-terms.json`) lets public CI catch them without revealing them. | same | Low-entropy terms can be brute-forced from the hashes: use it for part-number-like tokens, the private list for the rest. |
| C5 | File-type rules | Native CAD / ECAD / exchange formats, key material and mailboxes are errors unless listed; inside archives too. | same | A native file renamed to another extension (C2 and C1 still see a data file). |
| C6 | History scan | Added lines in all refs, files ever added, data files deleted but still downloadable. Weekly and on every push; fails on a shallow clone. | CI (`fetch-depth: 0`), `--history` | What GitHub caches outside git. |
| C7 | Deploy gate | The Pages deploy does not run unless the boundary workflow passes; only `main` ever deploys. | `.github/workflows/deploy-pages.yml` `needs: boundary` | A change to the workflow itself: protect `.github/**` (C13). |
| C8 | Local hooks | pre-commit checks the staged files, pre-push the range and every line its commits added. | `tools/install-boundary-hooks.sh` | `--no-verify`: CI is the gate that cannot be skipped. |
| C9 | Runtime egress policy | A Content-Security-Policy on the viewer: `connect-src` is this origin, `blob:`, the owner's Pages origin and loopback; scripts only from this origin (the import map pinned by hash); forms off. A file opened in the viewer cannot be sent to another host by the page or by an injected script, and a misconfigured helper URL cannot send it elsewhere. | `index.html`; `tests/csp.test.mjs`, `tests/no-egress.mjs` | Navigation (a link the person clicks, such as a part-number web search, which the host must allow explicitly), browser extensions, the clipboard, screenshots. |
| C10 | Local-only processing | The viewer is static and has no server. The sldprt-re helper binds to 127.0.0.1 and answers one named origin. CAD data is converted on the person's own machine. | design; documented in each repository | A person who points the helper at a non-loopback address is stopped only by C9. |
| C11 | Embed contract | Same-origin `postMessage` only; datasheet web search is off unless the host says so (a part number can itself be sensitive). | `embed.js`, `tests/embed-contract.mjs` | The host's own behaviour. |
| C12 | Masked reporting | Reports, issues, pull requests and comments from the CUI side carry only what section 1 calls masked. | people; the templates in `tools/cui-side-kit/` | Free text. |
| C13 | Repository settings (owner) | Branch protection or a ruleset on `main` requiring the `boundary` check; pull requests for everyone but the owner; CODEOWNERS on `data-boundary/**`, `.github/**`, `tools/boundary-check.mjs`, `docs/data-boundary.md`; secret scanning and push protection on; Pages published from the workflow only; fork and visibility settings reviewed. | GitHub settings: **not visible from the code, status unknown** | Owners who disable them. |
| C14 | Session separation | A person or AI session works on public repositories **or** CUI repositories, never both attached at once; a public session is never given CUI input; a CUI session reaches the public side read-only. | people and the session's repository list | Transcripts and attachments live outside git. |

## 6. Releasing something from the CUI side

Only code or documentation, and only by this route:
1. The change can be described without the data. If the explanation needs the data, it is not a release.
2. It is made in a clean checkout of the **public** repository, with no controlled file open, by a session that has only public repositories attached.
3. Fixtures are synthetic (a generator under `tools/`), not "a real file with the names changed".
4. `node tools/boundary-check.mjs` and `--history` pass **with the private deny-list**.
5. The pull-request text is generic (what the code does, not what it was for).
6. A person with authority reads the diff and approves; the approval is the pull request's.

## 7. If a spill is suspected

Generic steps; the organisation's incident procedure takes precedence and starts first.
1. Stop pushing. Do not delete and hope: a deleted file stays in history, in forks, in pull-request refs and in Pages caches.
2. The owner makes the repository private and disables Pages at once.
3. Scope it: `node tools/boundary-check.mjs --history` with the deny-list, plus the Pages site, releases, issues and CI logs.
4. Rewrite history (`git filter-repo`), force-push every ref, ask GitHub support to purge cached views and pull-request refs, and tell forks' owners.
5. Report to the security officer; record what, when, who saw it. Search caches (web archives, search engines) outside GitHub.
6. Add a rule or deny-list entry so it cannot recur, and say so in the review.

## 8. This contract in each public repository

| Repository | Publishes | Data | Notes |
|---|---|---|---|
| ipb_viz | the **whole tree** to GitHub Pages (`path: .`): anything committed is public within a minute | examples under `tools/examples`, `sources/` | viewer CSP (C9); the Hydro material needs an owner decision (section 10) |
| sldprt-re | code and public test references; CAD files and converted models are git-ignored | none committed | the local helper (C10) |
| PcbPrj-re | the viewer and derived board data to Pages, **built by GitHub straight from the `main` branch** (`/`), not from a workflow | `samples/` derived from open-licence upstream designs, pinned in `samples/sources.json` | upstream licences carried in each dataset's `NOTICE.md`. With no workflow in the path there is **no deploy gate (C7)**: the `boundary` check fails the commit status but cannot stop the build. Switching Pages to a workflow deploy, as ipb_viz does, closes it. |

## 9. Residual risk (read this)

- The scanner reads text, not meaning and not pictures. A controlled drawing with no marking, or with the marking as a bitmap, passes. Controlled **data** looks like any other data; only a person's decision (the manifest, C1) protects against it.
- The deny-list is only as good as its owner. A term nobody listed is not found. Forks and clones without the list do not apply it; the run says "NOT SUPPLIED".
- `reviewed` and `owner-attested-public` are claims by a person. The tool records them and notices a change; it cannot check them.
- Hooks can be skipped. The CI gate cannot, but only if C13 (required checks, protected workflows) is set; those settings are not visible from the code.
- GitHub, search engines and web archives keep copies outside the repository. History rewriting does not reach them.
- AI-session transcripts, uploads and attachments are outside git. C14 is a rule for people.
- The viewer's policy (C9) stops the page's own network use, not what a person does with the screen, the clipboard or a screenshot.
- Counts, sizes and names can themselves identify a program. "Masked" is a judgement, not a filter.

## 10. Decisions the owner still has to take

1. **The Hydro material** (`sources/Hydro_Docs/*`, `tools/examples/hydro/*`, 15 files, about 75 MB): **decided 2026-11-08.** The owner stated that it is not CUI and is to be treated as publicly accessible by all repositories; it is recorded in the manifest as `owner-attested-public` with that statement as the evidence. Still open: name where a clearance record is kept (the statement is the only record cited so far). The scanner found no controlled-information banner, distribution statement or export caveat in the text it could read, found a proprietary-company legend in the drawing package (a third-party legend, not a controlled marking: it stays a visible warning) and could not examine 724 images in the package. The native SolidWorks assembly these files were made from (`Hydro_CAD.zip`) is a release asset of `RonClemens/sldprt-re` (v0.0.1), recorded in the manifest's `external` section with its SHA-256; see `docs/data-boundary-cui-side.md`, section "Mirroring and testing".
2. **IADS Programming User Guide (`docs/IADSProgrammingGuide.pdf`): decided 2026-11-08, hosted.** The owner found it on a company-specific public website (<https://defense-solutions.curtisswright.com/system/files/2023-10/IADSProgrammingGuide_1.pdf>) and considers it publicly releasable regardless of its markings; recorded as `owner-attested-public`, and the docs link to the vendor's copy as well. Still open: name where the clearance record is kept, and confirm the vendor's terms allow redistribution (public availability is not by itself a right to redistribute). Page snapshots for browser comparison are made locally with `tools/pdf-snapshots.mjs` and are git-ignored.
3. The other grandfathered examples (bike, bricks, enclosure, demo, sample files, `sample-arm-with-drawings.zip`): attest or leave them as warnings; `--accept` records it.
4. Set C13 on all three repositories and add the `BOUNDARY_DENYLIST` secret (a private list of terms, one per line).
5. Name the CUI-side repositories and where they live, so the kit in `tools/cui-side-kit/` can be pinned to their remotes (section 11).
6. Who is "a person with authority" for `owner-attested-public` and for a release (section 6).
7. PcbPrj-re: switch Pages to a workflow deploy that needs the `boundary` check (C7), or accept that it cannot be gated.
8. **Tags and old branches are refs too.** ipb_viz holds six `archive/*` tags (about 20 to 140 commits each, October 2026), including `archive/dev/templates`, the former template-picker branch whose commit message calls the template folder CUI, and `archive/e2e-results` / `archive/test/e2e-hydro-drawings` (test output against the Hydro material). The scanner reads them as part of the history and found no marking and no data file in them apart from one code literal that tests for a banner prefix; but they are public, fetchable and unneeded: delete them (or move them to the private side) unless a person decides they are public material.

## 11. The CUI side's half

`docs/data-boundary-cui-side.md` and `tools/cui-side-kit/` give the CUI-side repositories what they need to keep their half, and to mirror and test with the public sample set (`--verify-samples`): a pre-push hook that refuses any remote not on their allow-list, a pinned verbatim vendoring check, a masked report template, and the checklist for a release. This repository cannot see or enforce
anything on that side; the contract is only complete when those repositories adopt it.

## Appendix: file formats

`data-boundary/manifest.json`: `{ contract, codeDirs: [prefixes not scanned as data, e.g. "vendor/"], files: { "<path>": { class, status: reviewed|generated|unreviewed, sha256, size, evidence, generator?, reviewed|recorded: date, suggestedClass?, note? } } }`.
`data-boundary/hashed-terms.json`: `{ salt, hashes: [sha256(salt + lowercase term)] }`; terms of one to three words. Deny-list file: one term per line, `#` comments, case-insensitive, `re:<regular expression>` for patterns.
Commands: `node tools/boundary-check.mjs [--tracked | --staged | --range A..B | --history] [--strict] [--json]`, `--init-manifest`, `--accept PATH... --class C --evidence "..." [--generated SCRIPT]`, `--rehash`, `--list-unreviewed`.
