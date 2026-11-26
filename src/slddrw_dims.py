"""The dimensions stored in a SolidWorks drawing (.SLDDRW): SolidWorks' own measured value, the dimension text, and the components it spans.

  read_dimensions(path) -> [ {name, value_m, text, printed, unit, reference, label, components, offset}, ... ]      python3 src/slddrw_dims.py FILE.SLDDRW [--json]

Where it comes from (verified on one real drawing, 27244607, against its PDF export; see docs/drawing-compare.md): `Contents/Definition` is an MFC archive. A dimension between two model entities is a
`moRefDim2E_c` object whose name is an MFC string `RD<n>`, followed by the double 2.0 and the measured value in metres (what SolidWorks itself measured on the model, not the rounded text). The dimension's text
is a markup string a little further on, e.g. ` 4<STACK size=1 divhratio=0.1 divwgap=1.0>21<OVER>32</STACK>" \\r\\nTOTAL HEIGHT` (a stacked fraction, an inch mark, a label after the line break); a reference
dimension's text is wrapped in `<Border type=14> ... </Border>` (the parentheses). The components the dimension spans appear as MFC strings of the form `<part or assembly>-<instance>`.

Not decoded: which face or edge of each component the dimension touches (the id-rep objects, as for mates), the tolerance object (`moDimTolerance_c`), the view each dimension belongs to (dimensions come out in
file order, grouped by view), and every dimension class other than `RD<n>` reference dimensions (angular, ordinate, sketch dimensions are skipped). Unverified on any other file.
"""
import os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_STR = re.compile(rb'\xff\xfe\xff(.)', re.S)
M_PER_IN = 0.0254


def mfc_strings(d):
    """[(offset, text)] for every MFC unicode string (FF FE FF <len> UTF-16) in the stream"""
    out = []
    for m in _STR.finditer(d):
        n = m.group(1)[0]; raw = d[m.end():m.end() + 2 * n]
        if len(raw) < 2 * n: continue
        try: out.append((m.start(), raw.decode('utf-16-le'), m.end() + 2 * n))
        except UnicodeDecodeError: continue
    return out


def parse_markup(s):
    """dimension text markup -> dict(printed, unit, reference, label, plain). `printed` is the value the text shows (a stacked fraction is added to its whole number)."""
    reference = '<Border type=14>' in s
    label = None
    body = s
    if '\r\n' in s: body, label = s.split('\r\n', 1); label = re.sub(r'<[^>]*>', '', label).strip() or None
    body = re.sub(r'<STACK[^>]*>\s*(\d+)\s*<OVER>\s*(\d+)\s*</STACK>', r' \1/\2', body)
    plain = re.sub(r'<[^>]*>', '', body).strip()
    unit = 'inch' if '"' in plain else None
    m = re.match(r'^(?:(\d+(?:\.\d+)?)\s*)?(?:(\d+)\s*/\s*(\d+))?\s*"?\s*$', plain) or re.match(r'^(\d+(?:\.\d+)?)', plain)
    printed = None
    if m:
        whole = m.group(1); printed = float(whole) if whole else 0.0
        if m.lastindex and m.lastindex >= 3 and m.group(2) and m.group(3): printed += int(m.group(2)) / int(m.group(3))
    return dict(printed=printed, unit=unit, reference=reference, label=label, plain=plain)


_CLS = re.compile(rb'\xff\xff(..)([\x03-\x40])\x00([A-Za-z_][A-Za-z0-9_]*)')


def id_triples(d, a, b):
    """face references in d[a:b]: records of three little-endian int32 (feature id F, a per-part constant, face index I). Only the dominant constant counts (other bit patterns that look like a
    triple, e.g. inside a double, are noise). -> [(offset, F, I)]"""
    from collections import Counter
    cand = [(p, struct.unpack_from('<III', d, p)) for p in range(a, b - 11)]
    cand = [(p, t) for p, t in cand if 0 < t[0] < 5000 and t[1] >= 0x10000000 and 0 <= t[2] < 100000]
    cnt = Counter(t[1] for _, t in cand)
    if not cnt or cnt.most_common(1)[0][1] < 2: return []
    top = cnt.most_common(1)[0][0]
    return [(p, t[0], t[2]) for p, t in cand if t[1] == top]


def read_dimensions(path_or_bytes):
    from sldcont import read_streams, read_streams_bytes
    d = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else read_streams(path_or_bytes, want=lambda n: n == 'Contents/Definition').get('Contents/Definition')
    if d is None: return []
    strs = mfc_strings(d); names = [(o, s, e) for o, s, e in strs if re.fullmatch(r'RD\d+', s)]
    out = []
    for i, (o, name, e) in enumerate(names):
        end = names[i + 1][0] if i + 1 < len(names) else e + 20000
        value = None
        for p in range(e, min(e + 80, len(d) - 16)):                   # the double 2.0, then the measured value
            if d[p:p + 8] == struct.pack('<d', 2.0):
                for q in range(p + 8, min(p + 60, len(d) - 8)):
                    v = struct.unpack('<d', d[q:q + 8])[0]
                    if v == v and 1e-5 < abs(v) < 10 and v not in (1.0, 2.0) and d[q + 7] in (0x3f, 0x3e, 0x3d, 0x40): value = v; break
                break
        cand = next(((so, s) for so, s, se in strs if e < so < end and 'objID' not in s and ('<STACK' in s or '\"' in s or re.fullmatch(r'\s*(<Border[^>]*>)?\s*\d+(\.\d+)?\s*(</Border>)?\s*(\r\n.*)?', s, re.S))), None)
        text = cand[1] if cand else None
        stop = min(end, cand[0] if cand else e + 4000)                  # the components named before the text belong to this dimension; later strings belong to other objects
        comps = [s for so, s, se in strs if e < so < stop and re.fullmatch(r'.*[A-Za-z].*-\d+', s) and '<' not in s and '\\' not in s and not s.startswith('Display State')]
        # the entities it spans: each id-rep (named the first time its class occurs) lists a primary face (feature, index) and then its neighbouring faces; the component is the one named most recently before it
        ents = []; classes = [(m.start(), m.group(3)[:m.group(2)[0]].decode()) for m in _CLS.finditer(d, e, stop) if len(m.group(3)) >= m.group(2)[0]]
        classes = [(co, c) for co, c in classes if c.endswith('IdRep_c')]
        cstr = [(so, s_) for so, s_, se in strs if e < so < stop and re.fullmatch(r'.*[A-Za-z].*-\d+', s_) and '<' not in s_ and '\\' not in s_ and not s_.startswith('Display State')]
        tri = id_triples(d, e, stop)
        for k, (co, cls) in enumerate(classes):
            nxt = classes[k + 1][0] if k + 1 < len(classes) else stop
            mine = [t for t in tri if co < t[0] < nxt]
            if not mine: continue
            comp = next((c for so_, c in reversed(cstr) if so_ < co), None)
            ents.append(dict(rep=cls, component=comp, feature=mine[0][1], face=mine[0][2], neighbours=[(F, I) for _, F, I in mine[1:]]))
        rec = dict(name=name, value_m=value, text=text, components=comps, entities=ents, offset=o)
        if text: rec.update({k: v for k, v in parse_markup(text).items() if k != 'plain'})
        else: rec.update(printed=None, unit=None, reference=False, label=None)
        out.append(rec)
    return out


def main():
    import json
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args: raise SystemExit(__doc__)
    dims = read_dimensions(args[0])
    if '--json' in sys.argv: print(json.dumps(dims, indent=1)); return
    print(f'{len(dims)} reference dimensions')
    for r in dims:
        v = r['value_m']; inch = f'{v / M_PER_IN:.4f} in' if v else '-'
        for x in r['entities']: print(f"        face ({x['feature']}, {x['face']}) of {x['component']}  [{x['rep']}]  + {len(x['neighbours'])} neighbouring faces")
        print(f"  {r['name']:4s} SolidWorks measured {('%.6f m' % v) if v else '-':>12} = {inch:>10} | printed {r['printed']!s:<8} {r['unit'] or '':5s} {'(reference)' if r['reference'] else '':11s} {r['label'] or '':13s} | {', '.join(r['components'])}")


if __name__ == '__main__':
    main()
