// tools/boundary-check.mjs (docs/data-boundary.md): throwaway git repositories with synthetic content. Marking strings are assembled here at run time so this file itself scans clean.
// node tests/boundary-check.test.mjs
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { createHash } from "node:crypto";
import { deflateSync } from "node:zlib";
let failed = 0;
const t = (ok, name) => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}`); if (!ok) failed++; };
const TOOL = join(import.meta.dirname, "../tools/boundary-check.mjs");
const j = (...p) => p.join("");
const CUI_BANNER = j("C", "UI//", "SP-XYZ"), DIST_B = j("DISTRIBUTION STATEMENT ", "B: dist", "ribution auth", "orized to US Gov", "ernment agencies"), DIST_A = j("DISTRIBUTION STATEMENT ", "A: approved for public release"), ITAR_T = j("subject to ", "IT", "AR export controls");
const NOFORN_T = j("NO", "FORN");

function repo() {
  const d = mkdtempSync(join(tmpdir(), "bnd-test-"));
  const sh = (...a) => execFileSync("git", a, { cwd: d, stdio: "pipe" });
  sh("init", "-q"); sh("config", "user.email", "t@example.invalid"); sh("config", "user.name", "t"); sh("checkout", "-q", "-b", "main");
  const put = (f, c) => { mkdirSync(dirname(join(d, f)), { recursive: true }); writeFileSync(join(d, f), c); };
  const commit = (m) => { sh("add", "-A"); sh("commit", "-q", "-m", m, "--allow-empty"); };
  const run = (args = [], env = {}) => { const r = spawnSync("node", [TOOL, ...args], { cwd: d, encoding: "utf8", env: { ...process.env, HOME: d, BOUNDARY_DENYLIST: "", ...env } }); return { code: r.status, out: r.stdout + r.stderr }; };
  return { d, sh, put, commit, run, done: () => rmSync(d, { recursive: true, force: true }) };
}
const sha = (b) => createHash("sha256").update(b).digest("hex");
const stored = (entries) => {            // a zip with stored members
  const parts = [], cd = []; let off = 0;
  for (const [name, data] of entries) {
    const nb = Buffer.from(name), lh = Buffer.alloc(30); lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt32LE(data.length, 18); lh.writeUInt32LE(data.length, 22); lh.writeUInt16LE(nb.length, 26);
    parts.push(lh, nb, data);
    const ce = Buffer.alloc(46); ce.writeUInt32LE(0x02014b50, 0); ce.writeUInt16LE(20, 4); ce.writeUInt16LE(20, 6); ce.writeUInt32LE(data.length, 20); ce.writeUInt32LE(data.length, 24); ce.writeUInt16LE(nb.length, 28); ce.writeUInt32LE(off, 42);
    cd.push(ce, nb); off += 30 + nb.length + data.length;
  }
  const cdBuf = Buffer.concat(cd), end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10); end.writeUInt32LE(cdBuf.length, 12); end.writeUInt32LE(off, 16);
  return Buffer.concat([...parts, cdBuf, end]);
};
const pdfWith = (text, flate) => { const c = Buffer.from(`BT /F1 12 Tf 72 700 Td (${text}) Tj ET`), body = flate ? deflateSync(c) : c; return Buffer.concat([Buffer.from(`%PDF-1.4\n1 0 obj\n<< /Length ${body.length}${flate ? " /Filter /FlateDecode" : ""} >>\nstream\n`), body, Buffer.from("\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")]); };
const glbWith = (extras) => { const js = Buffer.from(JSON.stringify({ asset: { version: "2.0" }, extras })); const pad = Buffer.concat([js, Buffer.alloc((4 - js.length % 4) % 4, 0x20)]); const h = Buffer.alloc(20); h.writeUInt32LE(0x46546C67, 0); h.writeUInt32LE(2, 4); h.writeUInt32LE(20 + pad.length, 8); h.writeUInt32LE(pad.length, 12); h.writeUInt32LE(0x4E4F534A, 16); return Buffer.concat([h, pad]); };

// ---- a clean repository passes
let R = repo();
R.put("README.md", "# nothing here\nplain text\n"); R.put("src/a.js", "export const a = 1;\n"); R.commit("clean");
let r = R.run();
t(r.code === 0 && /0 error/.test(r.out) && /NOT SUPPLIED/.test(r.out), "a clean repository passes, and says the private deny-list was not supplied");

// ---- markings: found, located, and never echoed
R.put("docs/note.md", `line one\n${CUI_BANNER} at the top\n`); R.commit("marking");
r = R.run();
t(r.code === 1 && /marking\.cui\s+docs\/note\.md:2/.test(r.out), "a controlled-information banner in text is an error, with file and line");
t(!r.out.includes(CUI_BANNER) && !r.out.includes("SP-XYZ"), "and the output never contains the matched text");
r = R.run(["--json"]);
t(JSON.parse(r.out).errors.some((e) => e.rule === "marking.cui") && !r.out.includes("SP-XYZ"), "--json is valid and also redacted");
R.put("docs/note.md", "fine again\n"); R.put("docs/d.txt", `${DIST_B}\n${DIST_A}\n`); R.commit("dist");
r = R.run();
t(/marking\.distribution\s+docs\/d\.txt:1/.test(r.out) && !/docs\/d\.txt:2/.test(r.out), "a restricted distribution statement (class B) is an error; the public-release one (class A) is not");
R.put("docs/d.txt", `see ${ITAR_T}\n`); R.commit("export");
t(/marking\.export\s+docs\/d\.txt:1/.test(R.run().out), "an export-control caveat is an error");
R.put("docs/d.txt", `handle as ${NOFORN_T}\n`); R.commit("noforn");
t(/marking\.export/.test(R.run().out), "so is a handling caveat");
R.put("docs/d.txt", "The word controlled and the word CUI alone are fine; so is a part number SB-2102.\n"); R.commit("benign");
t(R.run().code === 0, "ordinary words (including the bare acronym) are not flagged: only marking forms are");

R.put("tools/s.js", `const isBanner = (l) => l.startsWith("${j("C", "UI//")}");\n`); R.put("docs/prose.md", `the TEMPLATES folder is gitignored (${j("C", "UI")}) and never published\n`); R.commit("code and prose");
t(R.run().code === 0, "code that tests for a banner prefix (a bare quoted prefix) and prose that mentions the acronym in parentheses are not markings");
R.put("docs/pm.md", `(${j("C", "UI")}) This paragraph starts with a portion mark.\n`); R.commit("portion mark");
t(/marking\.cui\s+docs\/pm\.md:1/.test(R.run().out), "a portion mark at the start of a line is");
R.sh("rm", "-q", "docs/pm.md"); R.commit("rm pm");

// ---- data files need a provenance record
R.put("data/x.csv", "a,b\n1,2\n"); R.commit("data");
r = R.run();
t(r.code === 1 && /file\.unlisted\s+data\/x\.csv/.test(r.out), "a data file with no record in the manifest is an error");
t(R.run(["--init-manifest"]).code === 0 && R.run().code === 0, "--init-manifest grandfathers it as unreviewed: the check passes with a warning");
r = R.run(); t(/file\.unreviewed\s+data\/x\.csv/.test(r.out) && /1 warning/.test(r.out), "and the warning names it");
t(R.run(["--strict"]).code === 1, "--strict turns the warning into a failure");
t(R.run(["--accept", "data/x.csv", "--class", "synthetic"]).code === 2, "--accept without evidence is refused");
t(R.run(["--accept", "data/x.csv", "--class", "synthetic", "--evidence", "made by hand for tests"]).code === 0 && R.run(["--strict"]).code === 0, "--accept with a class and evidence marks it reviewed: strict passes");
R.put("data/x.csv", "a,b\n1,3\n"); R.commit("changed");
r = R.run(); t(r.code === 1 && /file\.changed\s+data\/x\.csv/.test(r.out), "a recorded file that changed is an error until it is re-recorded");
t(R.run(["--rehash"]).code === 0 && R.run().code === 0, "--rehash re-records it");
t(R.run(["--accept", "data/x.csv", "--class", "synthetic", "--evidence", "e", "--generated", "tools/none.py"]).code === 2, "--generated must name a tracked script");
R.put("tools/gen.py", "print(1)\n"); R.commit("gen");
t(R.run(["--accept", "data/x.csv", "--class", "synthetic", "--evidence", "e", "--generated", "tools/gen.py"]).code === 0 && R.run(["--strict"]).code === 0, "a generated file with a tracked generator passes strict");
R.sh("rm", "-q", "tools/gen.py"); R.commit("no generator");
t(/file\.generator-missing/.test(R.run().out), "if the generator disappears the record no longer holds");
R.put("tools/gen.py", "print(1)\n"); R.commit("gen again");

// ---- file types
R.put("cad/part.SLDPRT", "x"); R.commit("native");
r = R.run(); t(r.code === 1 && /file\.forbidden-type\s+cad\/part\.SLDPRT/.test(r.out), "a native CAD file is an error");
R.sh("rm", "-q", "cad/part.SLDPRT"); R.put("keys/a.pem", "x"); R.commit("key"); r = R.run();
t(/file\.forbidden-type\s+keys\/a\.pem/.test(r.out), "key material is an error");
R.sh("rm", "-q", "keys/a.pem"); R.commit("rm");

// ---- readers: PDF (plain and compressed), glTF, archives
for (const [flate, label] of [[false, "plain"], [true, "compressed"]]) {
  R.put("docs/p.pdf", pdfWith(DIST_B.replace(/[()]/g, ""), flate)); R.commit("pdf");
  R.run(["--init-manifest"]); r = R.run();
  t(/marking\.distribution\s+docs\/p\.pdf/.test(r.out) && !r.out.includes("authorized"), `a distribution statement inside a ${label} PDF text stream is found, not echoed`);
  R.sh("rm", "-q", "docs/p.pdf"); R.run(["--rehash"]); R.commit("rm pdf");
}
R.put("m/x.glb", glbWith({ note: CUI_BANNER })); R.commit("glb"); R.run(["--init-manifest"]);
t(/marking\.cui\s+m\/x\.glb/.test(R.run().out), "a marking in glTF extras is found");
R.sh("rm", "-q", "m/x.glb"); R.commit("rm glb");
R.put("z/a.zip", stored([["inside/readme.txt", Buffer.from(`${DIST_B}\n`)], ["inside/Part.SLDPRT", Buffer.from("x")]])); R.commit("zip");
r = R.run(); t(/file\.unlisted\s+z\/a\.zip/.test(r.out) && /member Part\.SLDPRT/.test(r.out) && /marking\.distribution\s+z\/a\.zip \(member readme\.txt\)/.test(r.out), "an unlisted archive is an error, its native CAD member is named, and a marking inside a member is found");
R.run(["--init-manifest"]); r = R.run(); t(/forbidden-type[^\n]*archive is listed but not reviewed/.test(r.out) && !/ERROR .*forbidden-type/.test(r.out), "once listed (unreviewed) the native member is a warning: an owner decision is needed");
R.sh("rm", "-q", "z/a.zip"); R.run(["--rehash"]); R.commit("rm zip");
{ const sr = R.run(); t(sr.code === 0 && /manifest\.stale\s+z\/a\.zip/.test(sr.out), "a manifest entry whose file is gone is reported as stale, and only as a warning"); }

// ---- private deny-list and hashed terms
const dl = join(R.d, "deny.txt"); writeFileSync(dl, "# private\nzebra project\nre:\\bQX-\\d{4}\\b\n");
R.put("docs/q.md", "meeting about the Zebra Project schedule\nand a part QX-1234\n"); R.commit("terms");
r = R.run(["--denylist", dl]);
t(r.code === 1 && /denylist\.private\s+docs\/q\.md:1/.test(r.out) && !/zebra/i.test(r.out.replace(/docs\/q\.md/g, "")) && /2 terms/.test(r.out), "a term of the private deny-list is an error; the term is not echoed");
t(R.run(["--denylist", dl, "--json"]).out.includes("QX-1234") === false, "nor in the JSON");
r = R.run([], { BOUNDARY_DENYLIST: dl }); t(r.code === 1 && /denylist\.private/.test(r.out), "$BOUNDARY_DENYLIST works the same");
r = R.run([]); t(r.code === 0 && /NOT SUPPLIED/.test(r.out), "without a deny-list the same file passes, and the run says the deny-list was not applied");
mkdirSync(join(R.d, "data-boundary"), { recursive: true });
writeFileSync(join(R.d, "data-boundary/hashed-terms.json"), JSON.stringify({ salt: "s1", hashes: [createHash("sha256").update("s1" + "kestrel valve").digest("hex")] })); R.put("docs/q.md", "ordered the Kestrel Valve today\n"); R.commit("hashed");
r = R.run(); t(r.code === 1 && /denylist\.hashed\s+docs\/q\.md:1/.test(r.out) && !/kestrel/i.test(r.out.replace(/docs\/q\.md/g, "")), "a salted hash of a term, committed, catches the term in public CI without revealing it");
R.put("docs/q.md", "nothing\n"); R.sh("rm", "-q", "-f", "data-boundary/hashed-terms.json"); R.commit("clean terms");

// ---- modes: staged, range, history
R.put("docs/s.md", `${CUI_BANNER}\n`); R.sh("add", "docs/s.md");
t(R.run(["--staged"]).code === 1, "--staged checks what is about to be committed");
R.sh("reset", "-q", "docs/s.md"); rmSync(join(R.d, "docs/s.md"));
const base = R.sh("rev-parse", "HEAD").toString().trim();
R.put("docs/r.md", `${CUI_BANNER}\n`); R.commit("bad"); R.put("docs/r.md", "ok\n"); R.commit("fixed");
r = R.run(["--range", `${base}..HEAD`]);
t(r.code === 1 && /history\.marking\.cui\s+docs\/r\.md @/.test(r.out) && /0 error/.test(R.run(["--range", "HEAD~1..HEAD"]).out), "--range checks the end state and every line the range's commits added: a marking added and fixed within one push is still pushed, so it fails; the last commit alone passes");
t(R.run(["--history"]).code === 1 && /history\.marking\.cui\s+docs\/r\.md @/.test(R.run(["--history"]).out), "--history finds a marking that was committed and later removed: it is still in the history");
R.put("big/deleted.pdf", pdfWith("hello", false)); R.commit("add pdf"); R.sh("rm", "-q", "big/deleted.pdf"); R.commit("rm pdf");
t(/history\.deleted-data\s+big\/deleted\.pdf/.test(R.run(["--history"]).out), "--history lists data files that were deleted but are still downloadable");
R.put("old/file.SLDASM", "x"); R.commit("native"); R.sh("rm", "-q", "old/file.SLDASM"); R.commit("rm native");
t(/history\.forbidden-type\s+old\/file\.SLDASM @/.test(R.run(["--history"]).out), "--history flags a native CAD file that was ever committed");
R.done();

// ---- accepted findings: a person's decision about one specific finding, recorded in a reviewable file
R = repo(); R.put("a.md", "x\n"); R.commit("c"); R.put("notes/n.txt", `${CUI_BANNER}\n`); R.commit("bad");
t(R.run().code === 1, "(setup) a marking is an error");
t(R.run(["--accept-finding", "marking.cui", "notes/n.txt"]).code === 2, "--accept-finding without evidence is refused");
t(R.run(["--accept-finding", "marking.cui", "notes/n.txt", "--evidence", "a synthetic example in a test of the checker"]).code === 0, "--accept-finding records it");
r = R.run(); t(r.code === 0 && /accepted by data-boundary\/accepted-findings\.json/.test(r.out), "the accepted finding no longer fails, and the run says one was accepted");
R.put("notes/other.txt", `${CUI_BANNER}\n`); R.commit("another");
t(R.run().code === 1 && /other\.txt/.test(R.run().out), "the acceptance covers that file and rule only: the same marking elsewhere still fails");
R.done();

// ---- a mirror of a public repository, verified without git
R = repo(); R.put("data/a.csv", "a,b\n1,2\n"); R.put("data/b.csv", "c,d\n3,4\n"); R.commit("data");
R.run(["--init-manifest"]); R.run(["--accept", "data/a.csv", "data/b.csv", "--class", "synthetic", "--evidence", "made for tests"]); R.commit("manifest");
const mirror = mkdtempSync(join(tmpdir(), "bnd-mirror-")), cp = (f) => { mkdirSync(dirname(join(mirror, f)), { recursive: true }); writeFileSync(join(mirror, f), readFileSync(join(R.d, f))); };
for (const f of ["data/a.csv", "data/b.csv", "data-boundary/manifest.json"]) cp(f);
const vs = (extra = {}) => { const r2 = spawnSync("node", [TOOL, "--verify-samples", mirror], { cwd: tmpdir(), encoding: "utf8" }); return { code: r2.status, out: r2.stdout + r2.stderr }; };
r = vs(); t(r.code === 0 && /2\/2 files match/.test(r.out), "a faithful mirror verifies, run from a directory that is not a git repository");
writeFileSync(join(mirror, "data/b.csv"), "c,d\n3,5\n"); r = vs();
t(r.code === 1 && /mirror\.changed\s+data\/b\.csv/.test(r.out), "a file that differs from the recorded hash is an error: not the public copy");
rmSync(join(mirror, "data/b.csv")); r = vs(); t(r.code === 1 && /mirror\.missing\s+data\/b\.csv/.test(r.out), "a missing file is an error");
cp("data/b.csv"); writeFileSync(join(mirror, "private.csv"), "x\n"); r = vs();
t(r.code === 0 && /mirror\.extra\s+private\.csv/.test(r.out), "a data file of the mirror's own that the manifest does not list is only a note: it is yours, not the public set");
t(spawnSync("node", [TOOL, "--verify-samples", tmpdir()], { encoding: "utf8" }).status === 2, "a directory without a manifest is refused");
rmSync(mirror, { recursive: true, force: true }); R.done();
// external (release-asset) records: well-formed, absent is a note, present is hashed
{
  const X = repo(); X.put("data/a.csv", "a,b\n1,2\n"); X.commit("data"); X.run(["--init-manifest"]);
  X.run(["--accept", "data/a.csv", "--class", "synthetic", "--evidence", "made for tests"]);
  const mp = join(X.d, "data-boundary/manifest.json"), m = JSON.parse(readFileSync(mp, "utf8")), body = Buffer.from("big archive"), h = createHash("sha256").update(body).digest("hex");
  m.external = { "big.zip": { url: "https://example.org/big.zip", sha256: h, size: body.length, class: "owner-attested-public", status: "reviewed", evidence: "test", reviewed: "2026-01-01" } };
  writeFileSync(mp, JSON.stringify(m)); X.commit("manifest");
  t(X.run([]).code === 0, "a well-formed external record passes the run");
  const mir = mkdtempSync(join(tmpdir(), "bnd-ext-")); mkdirSync(join(mir, "data-boundary")); mkdirSync(join(mir, "data"));
  writeFileSync(join(mir, "data-boundary/manifest.json"), JSON.stringify(m)); writeFileSync(join(mir, "data/a.csv"), "a,b\n1,2\n");
  const ve = () => { const q = spawnSync("node", [TOOL, "--verify-samples", mir], { cwd: tmpdir(), encoding: "utf8" }); return { code: q.status, out: q.stdout + q.stderr }; };
  r = ve(); t(r.code === 0 && /external\.absent\s+big\.zip/.test(r.out) && /example\.org\/big\.zip/.test(r.out), "an absent external asset is a note with its URL and hash");
  writeFileSync(join(mir, "big.zip"), body); r = ve(); t(r.code === 0 && /external\.match\s+big\.zip/.test(r.out), "a present external asset with the recorded hash matches");
  writeFileSync(join(mir, "big.zip"), "tampered"); r = ve(); t(r.code === 1 && /external\.changed\s+big\.zip/.test(r.out), "an external asset with another hash is an error");
  m.external["big.zip"].url = "http://example.org/big.zip"; writeFileSync(mp, JSON.stringify(m)); X.commit("bad");
  r = X.run([]); t(r.code === 1 && /external\.malformed/.test(r.out + (r.err || "")), "an external record without https is malformed");
  rmSync(mir, { recursive: true, force: true }); X.done();
}


// ---- this repository itself
const self = spawnSync("node", [TOOL], { cwd: join(import.meta.dirname, ".."), encoding: "utf8" });
t(self.status === 0 && /0 error/.test(self.stdout), `this repository passes its own check (${(self.stdout.match(/result: (.*)/) || [])[1]})`);
console.log(failed ? `\n${failed} FAILED` : "\nall passed");
process.exit(failed ? 1 : 0);
