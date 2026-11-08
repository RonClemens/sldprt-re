#!/usr/bin/env node
// Data-boundary check (contract ipb-boundary/1, docs/data-boundary.md). Zero dependencies; the same file is used in ipb_viz, sldprt-re and PcbPrj-re.
//
// What it answers: "can what is about to be committed, pushed or published contain controlled or program data?" It checks
//   1. every data-like file (PDF, CSV, GLB, STEP, archives, images, office files, ...) is listed in data-boundary/manifest.json with its origin class and hash;
//   2. no CAD / ECAD / PLM native format or key / mailbox file is tracked unless the manifest lists it;
//   3. no controlled-information marking (banners, distribution statements, export-control and handling caveats) appears in text, in PDF text, in glTF strings, in archive members;
//   4. no term of the PRIVATE deny-list appears (program names, part-number prefixes, host names, people): the list is supplied at run time and is never committed;
//      optionally, salted hashes of terms can be committed (data-boundary/hashed-terms.json) so public CI can still catch them.
// What it never does: print the text it matched. A finding names the rule, the file and the line, so a CI log cannot become the spill.
// What it cannot do: read text in fonts without a usable encoding, text inside bitmaps, or meaning. Files it could not read fully are reported (rule scan.unreadable); a data file that
// is not `reviewed` in the manifest is only as safe as the person who reviewed it. See "Residual risk" in docs/data-boundary.md.
//
// Usage: node tools/boundary-check.mjs [--tracked | --staged | --range A..B | --history] [--strict] [--json] [--manifest FILE] [--denylist FILE]
//        node tools/boundary-check.mjs --init-manifest              add every unlisted data file as `unreviewed` (grandfathering), record hashes
//        node tools/boundary-check.mjs --accept PATH... --class synthetic|open-licence|third-party|owner-attested-public --evidence "text"   a person reviewed these files
//        node tools/boundary-check.mjs --accept PATH... --class ... --evidence "text" --generated tools/make-x.py   a tracked script reproduces them from tracked inputs (checked: the script must exist)
//        node tools/boundary-check.mjs --verify-samples DIR          on a mirror of a public repository (no git needed): every file its manifest lists is there with the recorded hash; extra data files are noted
//        node tools/boundary-check.mjs --list-unreviewed             the grandfathered files by size, with the suggested class and note (what the owner still has to decide)
//        node tools/boundary-check.mjs --rehash                     re-record hashes of listed files that changed on purpose (generated examples): review the diff first
// External assets (a release asset too large for git): manifest.external { NAME: { url, sha256, size, class, status, evidence, reviewed } }. The run checks the record is well formed; --verify-samples DIR
// also hashes DIR/NAME (or DIR/external/NAME) when the file is there, and notes the URL to download it from when it is not. The asset itself is never committed.
// Accepted findings: data-boundary/accepted-findings.json, [{ rule, file, commit?, evidence, accepted }]: a person's decision that one specific finding is a false positive (rule + file, and for history
// findings the commit prefix). Keep it small, protected by CODEOWNERS, and prefer rewording the text. Add one with --accept-finding RULE FILE [COMMIT] --evidence "text".
// Deny-list: --denylist FILE, or $BOUNDARY_DENYLIST (a path), or ~/.config/boundary/denylist.txt. One term per line, '#' comments, case-insensitive; "re:..." is a regular expression.
// Exit status: 1 when there is an error (or any warning with --strict), 0 otherwise.
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, existsSync, statSync, mkdirSync, mkdtempSync, rmSync, readdirSync } from "node:fs";
import { createHash } from "node:crypto";
import { inflateSync, inflateRawSync } from "node:zlib";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import { createInterface } from "node:readline";

export const CONTRACT = "ipb-boundary/1";
const DATA_EXT = new Set([".pdf", ".csv", ".tsv", ".glb", ".gltf", ".stp", ".step", ".vsdx", ".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".xlsx", ".xls", ".xlsm", ".docx", ".doc", ".pptx", ".ppt", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".prn", ".bin", ".dat", ".obj", ".stl", ".3mf", ".fbx", ".mp4", ".mov", ".wasm", ".sldprt", ".sldasm", ".slddrw", ".igs", ".iges", ".x_t", ".x_b", ".sat", ".dwg", ".dxf", ".pcbdoc", ".schdoc", ".prjpcb", ".pcblib", ".schlib", ".intlib", ".brd", ".sch", ".gbr", ".drl", ".ipc"]);
const CODE_DIRS_DEFAULT = ["vendor/", "node_modules/", ".github/"];
const CLASSES = ["synthetic", "open-licence", "third-party", "owner-attested-public"];
const argv = process.argv.slice(2);
const flag = (n) => argv.includes(n), opt = (n) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : null; };
if (flag("--verify-samples")) {                                          // runs on a plain directory: the CUI side's mirror of a public repository
  const dir = opt("--verify-samples"), mfile = path.join(dir || "", "data-boundary", "manifest.json");
  if (!dir || !existsSync(mfile)) { console.error(`--verify-samples needs a directory that holds data-boundary/manifest.json (${dir || "none given"})`); process.exit(2); }
  const mf = JSON.parse(readFileSync(mfile, "utf8")), missing = [], changed = [], ok = [];
  for (const [f, v] of Object.entries(mf.files || {})) {
    const fp = path.join(dir, f); if (!existsSync(fp)) { missing.push(f); continue; }
    (createHash("sha256").update(readFileSync(fp)).digest("hex") === v.sha256 ? ok : changed).push(f);
  }
  const extNames = Object.entries(mf.external || {}), extMissing = [], extChanged = [], extOk = [];
  for (const [n, v] of extNames) {
    const fp = [path.join(dir, n), path.join(dir, "external", n)].find((x) => existsSync(x));
    if (!fp) { extMissing.push([n, v.url]); continue; }
    (createHash("sha256").update(readFileSync(fp)).digest("hex") === v.sha256 ? extOk : extChanged).push(n);
  }
  const extra = [];
  (function walk(d, rel) { for (const e of readdirSync(d, { withFileTypes: true })) { if (e.name === ".git" || e.name === "node_modules") continue; const r = rel ? `${rel}/${e.name}` : e.name;
    if (e.isDirectory()) walk(path.join(d, e.name), r); else if (DATA_EXT.has(path.extname(e.name).toLowerCase()) && !(mf.codeDirs || CODE_DIRS_DEFAULT).some((c) => r.startsWith(c)) && !mf.files[r]) extra.push(r); } })(dir, "");
  const total = Object.keys(mf.files || {}).length, statuses = {}; for (const v of Object.values(mf.files || {})) statuses[v.status] = (statuses[v.status] || 0) + 1;
  console.log(`mirror ${dir} against its manifest (${CONTRACT}): ${ok.length}/${total} files match (${Object.entries(statuses).map(([k, n]) => `${n} ${k}`).join(", ") || "none listed"})`);
  for (const f of missing.slice(0, 40)) console.log(`  ERROR mirror.missing  ${f}`);
  for (const f of changed.slice(0, 40)) console.log(`  ERROR mirror.changed  ${f}  (differs from the recorded hash: not the public copy)`);
  for (const f of extra.slice(0, 10)) console.log(`  NOTE  mirror.extra    ${f}  (a data file the public manifest does not list: yours, not the public set)`);
  for (const n of extOk) console.log(`  OK    external.match   ${n}`);
  for (const [n, u] of extMissing) console.log(`  NOTE  external.absent  ${n}  (release asset, not in the mirror: download ${u} and check sha256 ${mf.external[n].sha256})`);
  for (const n of extChanged) console.log(`  ERROR external.changed ${n}  (differs from the recorded hash: not the public copy)`);
  console.log(`result: ${missing.length + changed.length + extChanged.length} error(s), ${extra.length + extMissing.length} note(s)`);
  process.exit(missing.length + changed.length + extChanged.length ? 1 : 0);
}
const list = (n) => { const i = argv.indexOf(n); if (i < 0) return []; const out = []; for (let k = i + 1; k < argv.length && !argv[k].startsWith("--"); k++) out.push(argv[k]); return out; };

// ---------------------------------------------------------------- rules. Patterns are assembled from fragments so this file contains no literal marking of its own.
const j = (...p) => p.join("");
const RULES = [
  { id: "marking.cui", sev: "error", why: "a controlled-unclassified marking (banner, portion mark or designation indicator)",
    re: [new RegExp(j("\\bC", "UI\\s*\\/\\/(?![\"'`])"), "i"),      // a bare prefix in quotes (code that tests for a banner) carries no category
          new RegExp(j("\\/\\/\\s*C", "UI\\b"), "i"), new RegExp(j("CONTROLLED\\s+UNCLASSIFIED\\s+INFORMATION"), "i"), new RegExp(j("^\\s*\\(C", "UI[)/]"), "im"),
         new RegExp(j("^\\s*C", "UI\\s+CATEGORY\\s*:"), "im"), new RegExp(j("^\\s*CONTROLLED\\s+BY\\s*:"), "im"), new RegExp(j("DISSEMINATION\\s+CONTROL\\s*:"), "i")] },
  { id: "marking.distribution", sev: "error", why: "a distribution statement other than public release",
    re: [new RegExp(j("DISTRIBUTION\\s+STATEMENT\\s+[B-F]\\b"), "i"), new RegExp(j("DISTRIBUTION\\s+(?:IS\\s+)?AUTHORI[SZ]ED\\s+TO\\s+(?:U\\.?S\\.?\\s+)?GOVERNMENT"), "i"), new RegExp(j("DISTRIBUTION\\s+LIMITED\\s+TO"), "i")] },
  { id: "marking.export", sev: "error", why: "an export-control or handling caveat",
    re: [new RegExp(j("\\bIT", "AR\\b")), new RegExp(j("EXPORT[-\\s]CONTROLLED"), "i"), new RegExp(j("EXPORT\\s+ADMINISTRATION\\s+REGULATIONS"), "i"), new RegExp(j("22\\s*CFR\\s*(?:PARTS?\\s*)?12[0-9]"), "i"),
         new RegExp(j("\\bEC", "CN\\b")), new RegExp(j("\\bNO", "FORN\\b")), new RegExp(j("\\bOR", "CON\\b")), new RegExp(j("\\bFO", "UO\\b")), new RegExp(j("FOR\\s+OFFICIAL\\s+USE\\s+ONLY"), "i"),
         new RegExp(j("LAW\\s+ENFORCEMENT\\s+SENSITIVE"), "i"), new RegExp(j("(?:TOP\\s+SECRET|SECRET|CONFIDENTIAL)\\s*\\/\\/"))] },
  { id: "marking.proprietary", sev: "warn", why: "a proprietary or company-confidential legend",
    re: [new RegExp(j("COMPANY\\s+PROPRIETARY"), "i"), new RegExp(j("PROPRIETARY\\s+(?:AND|&)\\s+CONFIDENTIAL"), "i"), new RegExp(j("PROPRIETARY\\s+INFORMATION"), "i"), new RegExp(j("\\bPRO", "PIN\\b"))] },
];
const TEXT_EXT = new Set([".md", ".txt", ".js", ".mjs", ".cjs", ".ts", ".json", ".csv", ".tsv", ".svg", ".html", ".htm", ".css", ".xml", ".yml", ".yaml", ".py", ".sh", ".ps1", ".vbs", ".prn", ".toml", ".ini", ".cfg", ".gitignore", ".lock", ".rst", ".tex", ".sql", ".bat"]);
const FORBIDDEN_EXT = new Map([[".sldprt", "native CAD"], [".sldasm", "native CAD"], [".slddrw", "native CAD drawing"], [".igs", "CAD exchange"], [".iges", "CAD exchange"], [".x_t", "Parasolid"], [".x_b", "Parasolid"], [".sat", "ACIS"],
  [".dwg", "CAD drawing"], [".dxf", "CAD drawing"], [".pcbdoc", "native ECAD"], [".schdoc", "native ECAD"], [".prjpcb", "native ECAD"], [".pcblib", "native ECAD"], [".schlib", "native ECAD"], [".intlib", "native ECAD"],
  [".pem", "key material"], [".key", "key material"], [".p12", "key material"], [".pfx", "key material"], [".eml", "mailbox"], [".msg", "mailbox"], [".pst", "mailbox"], [".ost", "mailbox"], [".env", "environment secrets"]]);
const MAX_TEXT = 40 * 1024 * 1024;
const ARCHIVE_REVIEWED = new Set(), ARCHIVE_LISTED = new Set();                                  // archives the manifest lists as reviewed: native files inside are expected there

// ---------------------------------------------------------------- git helpers
const git = (...a) => execFileSync("git", a, { encoding: "buffer", maxBuffer: 1 << 30 });
const gitText = (...a) => git(...a).toString("utf8");
const root = gitText("rev-parse", "--show-toplevel").trim();
process.chdir(root);
const split0 = (b) => b.toString("utf8").split("\0").filter(Boolean);
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");
const ext = (f) => path.extname(f).toLowerCase() || (path.basename(f).startsWith(".") ? path.basename(f).toLowerCase() : "");

// ---------------------------------------------------------------- deny-lists (private, runtime) and hashed terms (committed)
function loadDenylist() {
  const src = opt("--denylist") || process.env.BOUNDARY_DENYLIST || path.join(homedir(), ".config", "boundary", "denylist.txt");
  if (!src || !existsSync(src)) return { terms: [], source: null };
  const terms = [];
  for (const raw of readFileSync(src, "utf8").split(/\r?\n/)) {
    const l = raw.trim(); if (!l || l.startsWith("#")) continue;
    try { terms.push(l.startsWith("re:") ? new RegExp(l.slice(3), "i") : new RegExp(l.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/^(\w)/, "\\b$1").replace(/(\w)$/, "$1\\b"), "i")); } catch (_) { /* a bad line must not stop the run */ }
  }
  return { terms, source: src };
}
function loadHashed(manifestDir) {
  const f = path.join(manifestDir, "hashed-terms.json");
  if (!existsSync(f)) return null;
  const d = JSON.parse(readFileSync(f, "utf8"));
  return { salt: d.salt || "", set: new Set(d.hashes || []), n: (d.hashes || []).length };
}
const hashTerm = (salt, t) => createHash("sha256").update(salt + t.toLowerCase().normalize("NFKC")).digest("hex");

// ---------------------------------------------------------------- readers: text of a file as far as it can be read
let POPPLER = null;
/** Text of a PDF by poppler's pdftotext when it is installed (it decodes embedded fonts); null when it is not or fails. */
function popplerText(buf) {
  if (POPPLER === false) return null;
  if (POPPLER === null) POPPLER = spawnSync("pdftotext", ["-v"], { encoding: "utf8" }).error ? false : true;
  if (!POPPLER) return null;
  const d = mkdtempSync(path.join(tmpdir(), "bnd-")), f = path.join(d, "x.pdf");
  try { writeFileSync(f, buf); const r = spawnSync("pdftotext", ["-q", "-layout", f, "-"], { maxBuffer: 1 << 30, encoding: "utf8", timeout: 120000 }); return r.status === 0 ? r.stdout : null; } catch (_) { return null; } finally { rmSync(d, { recursive: true, force: true }); }
}
function pdfText(buf) {
  const s = buf.toString("latin1"), out = [], info = { streams: 0, unread: 0, hexOps: 0, images: 0, flate: 0 };
  out.push(...[...s.matchAll(/\/(Title|Author|Subject|Keywords|Creator|Producer|Company)\s*\(((?:\\.|[^\\)])*)\)/g)].map((m) => m[2]));
  info.images += (s.match(/\/Subtype\s*\/Image/g) || []).length;
  const re = /stream\r?\n/g; let m;
  while ((m = re.exec(s))) {
    const start = m.index + m[0].length, end = s.indexOf("endstream", start); if (end < 0) break;
    const dictStart = s.lastIndexOf("obj", m.index), dict = s.slice(dictStart < 0 ? 0 : dictStart, m.index); let body = buf.subarray(start, end);
    info.streams++;
    if (/\/FlateDecode/.test(dict)) { info.flate++; try { body = inflateSync(body); } catch (_) { try { body = inflateRawSync(body); } catch (_2) { info.unread++; continue; } } }
    else if (/\/(LZWDecode|ASCII85Decode|DCTDecode|JPXDecode|CCITTFaxDecode|JBIG2Decode)/.test(dict)) { if (!/DCTDecode|JPXDecode|CCITTFax|JBIG2/.test(dict)) info.unread++; continue; }
    const t = body.toString("latin1");
    if (!/\b(?:Tj|TJ)\b/.test(t)) { if (/\/Type\s*\/(?:ObjStm|XRef)/.test(dict) || /\/Subtype\s*\/Image/.test(dict)) continue; out.push(t.slice(0, 0)); continue; }
    for (const mm of t.matchAll(/\(((?:\\.|[^\\)])*)\)\s*Tj|\[((?:[^\\\]]|\\.)*)\]\s*TJ/g)) {
      const str = mm[1] !== undefined ? mm[1] : [...mm[2].matchAll(/\(((?:\\.|[^\\)])*)\)/g)].map((x) => x[1]).join("");
      out.push(str.replace(/\\([()\\])/g, "$1"));
    }
    info.hexOps += (t.match(/<[0-9A-Fa-f]{4,}>\s*(?:Tj|TJ)|\[(?:\s*<[0-9A-Fa-f]+>\s*-?\d*\.?\d*)+\s*\]\s*TJ/g) || []).length;
  }
  return { text: out.join("\n"), info };
}
function glbText(buf) {
  if (buf.length < 20 || buf.readUInt32LE(0) !== 0x46546C67) return { text: "", info: { bad: true } };
  const len = buf.readUInt32LE(12); return { text: buf.subarray(20, 20 + len).toString("utf8"), info: {} };
}
function zipEntries(buf) {
  const out = []; let e = buf.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06])); if (e < 0) return out;
  const n = buf.readUInt16LE(e + 10); let p = buf.readUInt32LE(e + 16);
  for (let i = 0; i < n && p + 46 <= buf.length && buf.readUInt32LE(p) === 0x02014b50; i++) {
    const method = buf.readUInt16LE(p + 10), csize = buf.readUInt32LE(p + 20), usize = buf.readUInt32LE(p + 24), nl = buf.readUInt16LE(p + 28), xl = buf.readUInt16LE(p + 30), cl = buf.readUInt16LE(p + 32), lh = buf.readUInt32LE(p + 42);
    out.push({ name: buf.subarray(p + 46, p + 46 + nl).toString("utf8"), method, csize, usize, lh }); p += 46 + nl + xl + cl;
  }
  return out;
}
function zipMember(buf, en) {
  const nl = buf.readUInt16LE(en.lh + 26), xl = buf.readUInt16LE(en.lh + 28), data = buf.subarray(en.lh + 30 + nl + xl, en.lh + 30 + nl + xl + en.csize);
  return en.method === 0 ? data : inflateRawSync(data);
}

// ---------------------------------------------------------------- the scan of one blob of text
function scanText(text, file, f, deny, hashed, label = "") {
  const lines = text.split(/\r?\n/), pageAt = label === "pdf pages" ? (() => { const starts = []; let n = 1; lines.forEach((l, i) => { if (l.includes("\f")) n += (l.match(/\f/g) || []).length; starts[i] = n; }); return (i) => starts[i - 1]; })() : null;
  const at = (rule, sev, why, line) => f.push({ rule, sev, file: pageAt ? `${file} (page ${pageAt(line || 1)})` : label ? `${file} (${label})` : file, line: pageAt ? null : (line || null), why });
  for (const R of RULES) {
    let hit = null;
    for (let i = 0; i < lines.length && !hit; i++) for (const re of R.re) { re.lastIndex = 0; if (re.test(lines[i])) { hit = i + 1; break; } }
    if (!hit) for (const re of R.re) { if (re.flags.includes("m") && re.test(text)) { hit = 1; break; } }
    if (hit) at(R.id, R.sev, R.why, hit);
  }
  if (deny.terms.length) for (let i = 0; i < lines.length; i++) { for (const re of deny.terms) if (re.test(lines[i])) { at("denylist.private", "error", "a term of the private deny-list (not shown)", i + 1); i = lines.length; break; } }
  if (hashed) {
    outer: for (let i = 0; i < lines.length; i++) {
      const toks = lines[i].toLowerCase().match(/[a-z0-9][a-z0-9._\/-]*/g) || [];
      for (let k = 0; k < toks.length; k++) for (let n = 1; n <= 3 && k + n <= toks.length; n++) if (hashed.set.has(hashTerm(hashed.salt, toks.slice(k, k + n).join(" ")))) { at("denylist.hashed", "error", "a hashed term from data-boundary/hashed-terms.json (not shown)", i + 1); break outer; }
    }
  }
}

function scanFile(file, buf, deny, hashed, f) {
  const e = ext(file), at = (rule, sev, why) => f.push({ rule, sev, file, line: null, why });
  if (e === ".pdf") {
    const { text, info } = pdfText(buf), pop = popplerText(buf);
    scanText(text, file, f, deny, hashed, "pdf text");                                                   // document info strings and plain text operators
    if (pop !== null) scanText(pop, file, f, deny, hashed, "pdf pages");                                // the text as a reader shows it, by page
    else if (info.hexOps > 0 || info.unread > 0) at("scan.unreadable", "warn", `${info.hexOps} text operators use fonts this check cannot decode and ${info.unread} streams could not be decoded (pdftotext is not installed): markings inside cannot be ruled out; review by a person`);
    if (info.images > 0) at("scan.bitmaps", "info", `${info.images} embedded image(s) not examined: a marking can be a bitmap`);
  } else if (e === ".glb") { const { text } = glbText(buf); scanText(text, file, f, deny, hashed, "glTF json"); }
  else if (e === ".gltf") scanText(buf.toString("utf8"), file, f, deny, hashed);
  else if ([".zip", ".docx", ".xlsx", ".pptx", ".vsdx"].includes(e)) {
    let members = []; try { members = zipEntries(buf); } catch (_) { at("scan.unreadable", "warn", "archive directory could not be read"); }
    for (const m of members) {
      scanText(m.name, file, f, deny, hashed, "member name");
      const me = ext(m.name); if (FORBIDDEN_EXT.has(me)) f.push({ rule: "file.forbidden-type", sev: ARCHIVE_REVIEWED.has(file) ? "info" : ARCHIVE_LISTED.has(file) ? "warn" : "error", file: `${file} (member ${path.basename(m.name)})`, line: null, why: `a ${FORBIDDEN_EXT.get(me)} file inside an archive${ARCHIVE_REVIEWED.has(file) ? " (the archive is reviewed in the manifest)" : ARCHIVE_LISTED.has(file) ? " (the archive is listed but not reviewed: an owner decision is needed)" : " that the manifest does not list"}` });
      if (m.usize > 8 * 1024 * 1024) continue;
      if (TEXT_EXT.has(me) || me === ".xml" || me === ".rels") { try { scanText(zipMember(buf, m).toString("utf8"), file, f, deny, hashed, `member ${path.basename(m.name)}`); } catch (_) { /* unreadable member */ } }
      else if (me === ".pdf") { try { const r = pdfText(zipMember(buf, m)); scanText(r.text, file, f, deny, hashed, `member ${path.basename(m.name)}`); if (r.info.hexOps) at("scan.unreadable", "warn", "a PDF inside the archive has fonts this check cannot decode"); } catch (_) { /* skip */ } }
    }
    if (members.length === 0 && e === ".zip") at("scan.unreadable", "warn", "archive has no readable members");
  } else if (TEXT_EXT.has(e) || e === "") { if (buf.length <= MAX_TEXT && !buf.subarray(0, 4096).includes(0)) scanText(buf.toString("utf8"), file, f, deny, hashed); }
}

// ---------------------------------------------------------------- manifest
const manifestPath = opt("--manifest") || "data-boundary/manifest.json";
function loadManifest() {
  if (!existsSync(manifestPath)) return { contract: CONTRACT, codeDirs: CODE_DIRS_DEFAULT, files: {} };
  const m = JSON.parse(readFileSync(manifestPath, "utf8")); m.files ||= {}; m.codeDirs ||= CODE_DIRS_DEFAULT; return m;
}
const saveManifest = (m) => { mkdirSync(path.dirname(manifestPath), { recursive: true }); const sorted = Object.fromEntries(Object.entries(m.files).sort(([a], [b]) => a.localeCompare(b))); writeFileSync(manifestPath, JSON.stringify({ ...m, files: sorted }, null, 1) + "\n"); };
const isCode = (m, f) => m.codeDirs.some((d) => f.startsWith(d));
const isDataLike = (m, f, size) => !isCode(m, f) && (DATA_EXT.has(ext(f)) || ((ext(f) === ".json" || ext(f) === ".svg" || ext(f) === ".xml" || ext(f) === ".txt") && size > 200 * 1024));

// ---------------------------------------------------------------- modes
function filesFor() {
  if (flag("--staged")) return split0(git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"));
  const r = opt("--range"); if (r) return split0(git("diff", "--name-only", "-z", "--diff-filter=ACMR", r));
  return split0(git("ls-files", "-z"));
}
const readWork = (f) => { try { return statSync(f).isFile() ? readFileSync(f) : null; } catch (_) { return null; } };

async function history(deny, hashed, revs = ["--all"]) {
  const f = [], where = (c, file) => `${file} @${c.slice(0, 8)}`;
  if (gitText("rev-parse", "--is-shallow-repository").trim() === "true") f.push({ rule: "history.shallow", sev: "error", file: "(repository)", line: null, why: "this clone is shallow: the history before the cut was not scanned (git fetch --unshallow, or check out with fetch-depth: 0 in CI)" });
  // data-like or forbidden files ever added, even if deleted since (they are still in the history)
  const names = gitText("log", ...revs, "--diff-filter=A", "--name-only", "--format=%x00%H").split("\0").slice(1);
  const seen = new Set();
  for (const chunk of names) { const lines = chunk.split("\n").filter(Boolean), c = lines.shift(); for (const file of lines) { const k = file; if (seen.has(k)) continue; seen.add(k); const e = ext(file);
    if (FORBIDDEN_EXT.has(e)) f.push({ rule: "history.forbidden-type", sev: "error", file: where(c, file), line: null, why: `a ${FORBIDDEN_EXT.get(e)} file was committed (it stays in history even when deleted)` }); } }
  const current = new Set(split0(git("ls-files", "-z")));
  const gone = revs[0] !== "--all" ? [] : [...seen].filter((p) => !current.has(p) && DATA_EXT.has(ext(p)) && !CODE_DIRS_DEFAULT.some((d) => p.startsWith(d)));
  for (const p of gone) f.push({ rule: "history.deleted-data", sev: "warn", file: p, line: null, why: "a data file that is no longer in the tree but is still in the history (older versions stay downloadable)" });
  // added text lines across all refs
  const p = spawn("git", ["log", ...revs, "--no-color", "-U0", "-p", "--format=%x01%H", "--diff-filter=AM", "--", ".", ":(exclude)vendor"], { maxBuffer: 1 << 30 });
  const rl = createInterface({ input: p.stdout, crlfDelay: Infinity }); let commit = "", file = "", n = 0;
  const seenHit = new Set();
  for await (const line of rl) {
    if (line.startsWith("\x01")) { commit = line.slice(1); continue; }
    if (line.startsWith("+++ ")) { file = line.slice(6); continue; }
    if (!line.startsWith("+") || line.length > 4000) continue;
    n++;
    const tmp = []; scanText(line.slice(1), file, tmp, deny, hashed);
    for (const x of tmp) { const k = `${x.rule}|${file}`; if (seenHit.has(k)) continue; seenHit.add(k); f.push({ ...x, rule: "history." + x.rule, file: where(commit, file), line: null }); }
  }
  return { findings: f, scannedLines: n };
}

// ---------------------------------------------------------------- main
async function main() {
  const deny = loadDenylist(), mf = loadManifest(), hashed = loadHashed(path.dirname(manifestPath)), findings = [];
  if (flag("--history")) {
    const r = await history(deny, hashed); report(r.findings, { mode: "history", scanned: `${r.scannedLines} added lines`, deny, hashed }); return;
  }
  for (const [n, v] of Object.entries(mf.external || {})) {
    const bad = !/^https:\/\/[^\s]+$/.test(v.url || "") ? "url (https required)" : !/^[0-9a-f]{64}$/.test(v.sha256 || "") ? "sha256" : !CLASSES.includes(v.class) ? "class" : !(v.evidence || "").trim() ? "evidence" : null;
    if (bad) findings.push({ rule: "external.malformed", sev: "error", file: n, line: null, why: `external asset record has a missing or invalid ${bad}` });
    else if (v.status !== "reviewed") findings.push({ rule: "external.unreviewed", sev: "warn", file: n, line: null, why: "an external asset not yet reviewed by a person" });
  }
  const files = filesFor(), tracked = new Set(split0(git("ls-files", "-z")));
  for (const [k, v] of Object.entries(mf.files)) { ARCHIVE_LISTED.add(k); if (v.status === "reviewed" || v.status === "generated") ARCHIVE_REVIEWED.add(k); }
  let scanned = 0;
  for (const file of files) {
    const buf = readWork(file); if (!buf) continue; scanned++;
    const e = ext(file), size = buf.length, rec = mf.files[file];
    if (FORBIDDEN_EXT.has(e) && !rec) findings.push({ rule: "file.forbidden-type", sev: "error", file, line: null, why: `a ${FORBIDDEN_EXT.get(e)} file that the manifest does not list` });
    if (isDataLike(mf, file, size)) {
      if (!rec) findings.push({ rule: "file.unlisted", sev: "error", file, line: null, why: "a data file with no provenance record in data-boundary/manifest.json (class, evidence, hash)" });
      else {
        if (rec.sha256 && rec.sha256 !== sha256(buf)) findings.push({ rule: "file.changed", sev: "error", file, line: null, why: `changed since it was recorded (${rec.status}); review the change, then re-record with --rehash or --accept` });
        if (rec.status === "generated" && !(rec.generator && tracked.has(rec.generator))) findings.push({ rule: "file.generator-missing", sev: "error", file, line: null, why: "recorded as generated, but the generator it names is not a tracked file" });
        else if (rec.status !== "reviewed" && rec.status !== "generated") findings.push({ rule: "file.unreviewed", sev: "warn", file, line: null, why: "recorded but not reviewed by a person (grandfathered): it needs an owner decision: --accept it, or remove it" });
      }
    }
    const t0 = Date.now(); scanFile(file, buf, deny, hashed, findings); if (process.env.BOUNDARY_TIMING && Date.now() - t0 > 1500) console.error(`slow: ${file} ${Date.now() - t0} ms`);
  }
  if (!flag("--staged") && !opt("--range")) for (const k of Object.keys(mf.files)) if (!tracked.has(k)) findings.push({ rule: "manifest.stale", sev: "warn", file: k, line: null, why: "listed in the manifest but not tracked" });
  if (flag("--init-manifest")) {
    let added = 0;
    for (const file of files) { const buf = readWork(file); if (!buf || mf.files[file] || !isDataLike(mf, file, buf.length)) continue; mf.files[file] = { class: "unclassified", status: "unreviewed", sha256: sha256(buf), size: buf.length, evidence: "" }; added++; }
    saveManifest({ contract: CONTRACT, ...mf }); console.log(`manifest: ${added} file(s) added as unreviewed -> ${manifestPath}`); return;
  }
  const acc = list("--accept");
  if (acc.length) {
    const cls = opt("--class"), ev = opt("--evidence"), gen = opt("--generated");
    if (!cls || !ev) { console.error("--accept needs --class and --evidence"); process.exit(2); }
    if (gen && !tracked.has(gen)) { console.error(`--generated names a script that is not tracked: ${gen}`); process.exit(2); }
    for (const file of acc) { const buf = readWork(file); if (!buf) { console.error(`no such file: ${file}`); process.exit(2); } mf.files[file] = { ...(mf.files[file] || {}), class: cls, status: gen ? "generated" : "reviewed", ...(gen ? { generator: gen } : {}), sha256: sha256(buf), size: buf.length, evidence: ev, [gen ? "recorded" : "reviewed"]: new Date().toISOString().slice(0, 10) }; }
    saveManifest({ contract: CONTRACT, ...mf }); console.log(`manifest: ${acc.length} file(s) marked ${gen ? "generated" : "reviewed"} (${cls})`); return;
  }
  const af = list("--accept-finding");
  if (af.length >= 2) {
    const ev = opt("--evidence"); if (!ev) { console.error("--accept-finding needs --evidence"); process.exit(2); }
    const f = path.join(path.dirname(manifestPath), "accepted-findings.json"), cur = existsSync(f) ? JSON.parse(readFileSync(f, "utf8")) : [];
    cur.push({ rule: af[0], file: af[1], ...(af[2] ? { commit: af[2] } : {}), evidence: ev, accepted: new Date().toISOString().slice(0, 10) });
    mkdirSync(path.dirname(f), { recursive: true }); writeFileSync(f, JSON.stringify(cur, null, 1) + "\n"); console.log(`accepted: ${af[0]} ${af[1]}${af[2] ? " @" + af[2] : ""}`); return;
  }
  if (flag("--list-unreviewed")) {
    const rows = Object.entries(mf.files).filter(([, v]) => v.status === "unreviewed").sort(([, a], [, b]) => (a.suggestedClass === "OWNER DECISION" ? 0 : 1) - (b.suggestedClass === "OWNER DECISION" ? 0 : 1) || b.size - a.size);
    console.log(`${rows.length} unreviewed file(s) in ${manifestPath}: each needs a person's decision (--accept ... --class ... --evidence ...) or removal\n`);
    for (const [f, v] of rows) console.log(`${String(v.suggestedClass || "?").padEnd(15)} ${(v.size / 1048576).toFixed(2).padStart(7)} MB  ${f}${v.note ? `\n${" ".repeat(24)}${v.note}` : ""}`);
    return;
  }
  if (flag("--rehash")) {
    let n = 0; for (const k of Object.keys(mf.files)) { const buf = readWork(k); if (buf && mf.files[k].sha256 !== sha256(buf)) { mf.files[k].sha256 = sha256(buf); mf.files[k].size = buf.length; n++; } }
    saveManifest({ contract: CONTRACT, ...mf }); console.log(`manifest: ${n} hash(es) re-recorded`); return;
  }
  let extra = "";
  if (opt("--range")) { const h = await history(deny, hashed, [opt("--range")]); findings.push(...h.findings.filter((x) => x.rule !== "history.shallow")); extra = `, ${h.scannedLines} added lines in the range's commits`; }
  report(findings, { mode: flag("--staged") ? "staged" : opt("--range") ? `range ${opt("--range")}` : "tracked", scanned: `${scanned} files${extra}`, deny, hashed });
}

function acceptedFilter(findings) {
  const f = path.join(path.dirname(manifestPath), "accepted-findings.json");
  if (!existsSync(f)) return findings;
  const acc = JSON.parse(readFileSync(f, "utf8"));
  const keep = findings.filter((x) => !acc.some((a) => {
    if (a.rule !== x.rule.replace(/^history\./, "") && a.rule !== x.rule) return false;
    const m = /^(.*?)(?: @([0-9a-f]+))?(?: \(.*\))?$/.exec(x.file), file = m ? m[1] : x.file, commit = m && m[2];
    return a.file === file && (!a.commit || (commit && (commit.startsWith(a.commit) || a.commit.startsWith(commit))));
  }));
  const n = findings.length - keep.length; if (n) console.error(`(${n} finding(s) accepted by data-boundary/accepted-findings.json)`);
  return keep;
}
function report(findings, ctx) {
  findings = acceptedFilter(findings);
  const strict = flag("--strict"), order = { error: 0, warn: 1, info: 2 };
  const uniq = [...new Map(findings.map((x) => [`${x.rule}|${x.file}|${x.line}`, x])).values()].sort((a, b) => order[a.sev] - order[b.sev] || a.file.localeCompare(b.file));
  const errors = uniq.filter((x) => x.sev === "error" || (strict && x.sev === "warn")), warns = uniq.filter((x) => x.sev === "warn" && !strict), infos = uniq.filter((x) => x.sev === "info");
  if (flag("--json")) { console.log(JSON.stringify({ contract: CONTRACT, ...ctx, deny: undefined, hashed: undefined, denylist: ctx.deny.source ? ctx.deny.terms.length : null, hashedTerms: ctx.hashed ? ctx.hashed.n : null, errors, warnings: warns, info: infos }, null, 1)); process.exit(errors.length ? 1 : 0); }
  console.log(`boundary check (${CONTRACT}), ${ctx.mode}: ${ctx.scanned}; private deny-list: ${ctx.deny.source ? `${ctx.deny.terms.length} terms` : "NOT SUPPLIED (only markings, file types and the manifest were checked)"}; hashed terms: ${ctx.hashed ? ctx.hashed.n : "none"}`);
  const show = (x) => `${x.sev.toUpperCase().padEnd(5)} ${x.rule}  ${x.file}${x.line ? `:${x.line}` : ""}  ${x.why}`;
  const group = (arr, cap) => { const by = new Map(); for (const x of arr) { if (!by.has(x.rule)) by.set(x.rule, []); by.get(x.rule).push(x); } for (const [r, xs] of by) { xs.slice(0, cap).forEach((x) => console.log("  " + show(x))); if (xs.length > cap) console.log(`  ... ${xs.length - cap} more ${r}`); } };
  group(errors, 40); group(warns, 6); group(infos, 3);
  console.log(`result: ${errors.length} error(s), ${warns.length} warning(s), ${infos.length} note(s)`);
  process.exit(errors.length ? 1 : 0);
}
if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("boundary-check.mjs")) main().catch((e) => { console.error("boundary check failed to run:", e.message); process.exit(2); });
