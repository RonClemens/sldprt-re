#!/usr/bin/env python3
"""Masked survey of where a SolidWorks drawing (.SLDDRW) keeps its sheet size. For the CUI-side agent: run it on real drawings, paste the JSON into a masked Issue (docs/native-file-checks.md).

  python3 tools/survey_slddrw_sizes.py FILE_OR_DIR... [--out report.json]

Per file it reports ONLY: the first 8 hex of sha256 of the file name, the file's sha256, the stream names that are on a fixed structural allowlist (anything else is counted as "other"), per stream
the byte size and how many times each sheet-format style ("A3 - Landscape", "A (ANSI)", ...) appears as text (ASCII or UTF-16LE), how many times the word "zone" appears, and how many IEEE doubles
(little-endian, in metres) fall within 0.5 mm of a standard paper dimension (the candidate encoding for a sheet's width/height). No text, names or numbers from the drawing are printed.
"""
import hashlib, json, os, re, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from sldcont import read_streams_bytes
from slddrw import _SIZE

STRUCTURAL = {"Contents/Definition", "SheetPreviews/SheetNames", "Header2", "ModelStamps", "PreviewPNG", "Contents/Config-0", "Contents/Config-0-Partition", "Contents/DisplayLists", "Contents/DisplayLists__ZLB", "Contents/Sheet", "Contents/SheetFormat", "Contents/Header"}
# standard paper dimensions in mm (ISO A4..A0, ANSI A..E, landscape long side first), Y14.1 / ISO 5457
PAPER_MM = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594), "A0": (1189, 841), "A": (279.4, 215.9), "B": (431.8, 279.4), "C": (558.8, 431.8), "D": (863.6, 558.8), "E": (1117.6, 863.6)}
TOL_M = 0.0005

def _safe_name(n):
    if n in STRUCTURAL: return n
    if n.startswith("Images/Sheet_"): return "Images/Sheet_N"
    if n.startswith("VirtualComp/"): return "VirtualComp/*"
    return "other"

def _style_hits(b):
    hits = {}
    for blob in (b, b.decode("utf-16le", "ignore").encode("ascii", "ignore")):
        for m in _SIZE.finditer(blob.decode("latin-1")):
            style = (m.group(1) or m.group(4)).upper()
            hits[style] = hits.get(style, 0) + 1
    return hits

def _double_hits(b):
    targets = {}
    for s, (w, h) in PAPER_MM.items():
        for v in (w, h): targets.setdefault(round(v / 1000, 4), set()).add(s)
    out = {}
    for i in range(0, len(b) - 7):
        v = struct.unpack_from("<d", b, i)[0]
        if not (0.05 < v < 1.5): continue
        for t, styles in targets.items():
            if abs(v - t) <= TOL_M:
                for s in styles: out[s] = out.get(s, 0) + 1
    return out

def survey(path):
    data = open(path, "rb").read()
    streams = read_streams_bytes(data, lambda n: True)
    agg = {}
    for name, b in streams.items():
        k = _safe_name(name); e = agg.setdefault(k, dict(streams=0, bytes=0, zoneWord=0, styleText={}, paperDoubles={}))
        e["streams"] += 1; e["bytes"] += len(b)
        e["zoneWord"] += len(re.findall(rb"(?i)z\x00?o\x00?n\x00?e", b))
        for s, c in _style_hits(b).items(): e["styleText"][s] = e["styleText"].get(s, 0) + c
        if len(b) < 20_000_000:
            for s, c in _double_hits(b).items(): e["paperDoubles"][s] = e["paperDoubles"].get(s, 0) + c
    return dict(name=hashlib.sha256(os.path.basename(path).encode()).hexdigest()[:8], sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), streamsTotal=len(streams), byStream=agg)

def main(argv):
    out = None
    if "--out" in argv: i = argv.index("--out"); out = argv[i + 1]; del argv[i:i + 2]
    files = []
    for p in argv:
        if os.path.isdir(p): files += [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.lower().endswith(".slddrw")]
        elif os.path.exists(p): files.append(p)
        else: print("no such path", file=sys.stderr); return 2
    if not files: print("usage: survey_slddrw_sizes.py FILE_OR_DIR... [--out report.json]", file=sys.stderr); return 2
    rep = dict(schema="ipb-slddrw-size-survey/1", files=[])
    for f in files:
        try: rep["files"].append(survey(f))
        except Exception as e: rep["files"].append(dict(name=hashlib.sha256(os.path.basename(f).encode()).hexdigest()[:8], error=type(e).__name__))
    text = json.dumps(rep, indent=1) + "\n"
    if out: open(out, "w").write(text)
    else: sys.stdout.write(text)
    return 0

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
