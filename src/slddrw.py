"""Reads what a viewer needs from a SolidWorks drawing (.SLDDRW): one preview image per sheet, the sheet
names, and the models the drawing references. Uses the same container reader as the part/assembly code
(sldcont) and inflates only the few streams involved, never the drawing's geometry.

  read_drawing(path) -> dict(sheets=[(name, png_bytes), ...], refs=[model file names, in order of appearance],
                             sizes=[native size dict | None, one per sheet])

A sheet preview is a 640x480 PNG ("Images/Sheet_<n>" in the container): enough to recognise a sheet and to
line its views up with the 3D model, not enough to read dimensions from. The drawing file records which
models it references in one place, before the first sheet (the document's reference list); it does not say
which model each sheet is about, so no per-sheet link is attempted here.

Sheet size (`sizes`) is a BEST-EFFORT, UNVERIFIED reading: no real drawing was available when it was written, so it looks only for the text SolidWorks uses for a sheet format or template
("A3 - Landscape", "A (ANSI) Landscape", "A0 (ISO)", a "... - landscape.slddrt" file name) in the drawing's definition stream, and reports nothing rather than guess when the text is absent
or ambiguous. Check it on a real file with `python3 src/slddrw.py FILE.SLDDRW` (it prints what it found and where). Each size is { system: "solidworks", style: "A3", standard: "ISO" | "ANSI",
orientation: "landscape" | "portrait" | None, via: "text-scan", confidence: "low", scope: "sheet" | "document" }.
"""
import os, re
from sldcont import read_streams_bytes

_REF = re.compile(r'[\w\-\.\(\)\+, &]{1,120}?\.(?:sldprt|sldasm)', re.I)
_SHEET_NAME = re.compile(r'Sheet[\w ()]*')
_SHEET_MARK = re.compile(r'Sheet(?:\d+|\s\d+)(?:\(\d+\))*')

def _wanted(name):
    return name in ('Contents/Definition', 'SheetPreviews/SheetNames') or name.startswith('Images/Sheet_')

def sheet_names(raw, count):
    """The sheet names from SheetPreviews/SheetNames (UTF-16 with length prefixes), or Sheet1..N if that stream is odd."""
    try:
        text = raw.decode('utf-16-le', 'ignore')
        names = []
        for m in _SHEET_NAME.finditer(text):
            n = m.group(0).strip()
            if n and n not in names: names.append(n)
        if len(names) == count: return names
    except Exception: pass
    return ['Sheet%d' % (i + 1) for i in range(count)]

# A sheet format as SolidWorks names it. The orientation word or an (ISO)/(ANSI) qualifier is required, so a part named "A3BRACKET" or "E-STOP" is not read as a size.
_SIZE = re.compile(r'(?<![A-Za-z0-9])(A[0-4]|[A-E])\s*(?:\((ISO|ANSI)\))?\s*[-\u2013_ ]\s*(Landscape|Portrait)(?![A-Za-z])|(?<![A-Za-z0-9])(A[0-4]|[A-E])\s*\((ISO|ANSI)\)', re.I)

def size_hits(definition):
    """Every sheet-format mention in a definition stream, in order: [{style, standard, orientation, offset}]."""
    hits = []
    for text in (definition.decode('utf-16-le', 'ignore'), definition.decode('latin-1', 'ignore')):
        for m in _SIZE.finditer(text):
            style = (m.group(1) or m.group(4)).upper()
            std = (m.group(2) or m.group(5) or ('ISO' if style.startswith('A') and style[1:].isdigit() else 'ANSI')).upper()
            hits.append(dict(style=style, standard=std, orientation=(m.group(3) or '').lower() or None, offset=m.start()))
        if hits: break              # the UTF-16 reading found them: do not count the same text again from the byte reading
    return hits

def sheet_sizes(definition, count):
    """One native size (or None) per sheet. One mention per sheet gives each its own; mentions that all agree give that size to every sheet (scope "document"); anything else is
    ambiguous and gives None rather than a guess."""
    def rec(h, scope): return dict(system='solidworks', style=h['style'], standard=h['standard'], orientation=h['orientation'], via='text-scan', confidence='low', scope=scope)
    hits = size_hits(definition or b'')
    if not hits or count < 1: return [None] * count
    if len(hits) == count: return [rec(h, 'sheet') for h in hits]
    if len({(h['style'], h['orientation']) for h in hits}) == 1: return [rec(hits[0], 'document') for _ in range(count)]
    return [None] * count

def references(definition):
    """Model file names the drawing references: those listed before its first sheet marker (the document's
    reference list); if there is no such list, every one found, in order of first appearance."""
    try: text = definition.decode('utf-16-le', 'ignore')
    except Exception: return []
    refs = [(m.start(), m.group(0).strip()) for m in _REF.finditer(text)]
    first = _SHEET_MARK.search(text)
    cut = first.start() if first else len(text)
    head = [r for o, r in refs if o < cut]
    chosen = head if head else [r for o, r in refs]
    out = []
    for r in chosen:
        r = re.sub(r'^[^\w]+', '', r)      # a stray length-prefix character in front of the name
        if r and r not in out: out.append(r)
    return out[:500]

def read_drawing(path):
    with open(path, 'rb') as f: data = f.read()
    streams = read_streams_bytes(data, _wanted)
    keys = sorted((k for k in streams if k.startswith('Images/Sheet_') and k.rsplit('_', 1)[-1].isdigit()), key=lambda k: int(k.rsplit('_', 1)[1]))
    names = sheet_names(streams.get('SheetPreviews/SheetNames', b''), len(keys))
    sheets = [(names[i], streams[k]) for i, k in enumerate(keys) if streams[k][:8] == b'\x89PNG\r\n\x1a\n']
    definition = streams.get('Contents/Definition', b'')
    return dict(sheets=sheets, refs=references(definition), sizes=sheet_sizes(definition, len(sheets)))

def png_size(png):
    import struct
    return struct.unpack('>II', png[16:24]) if len(png) >= 24 and png[:8] == b'\x89PNG\r\n\x1a\n' else (0, 0)


if __name__ == '__main__':
    import json, sys
    if len(sys.argv) != 2: sys.exit('usage: python3 src/slddrw.py FILE.SLDDRW   (prints the sheets found and every sheet-size mention, to check the reader on a real drawing)')
    with open(sys.argv[1], 'rb') as f: data = f.read()
    st = read_streams_bytes(data, _wanted)
    d = read_drawing(sys.argv[1])
    print(json.dumps(dict(sheets=[n for n, _ in d['sheets']], definition_bytes=len(st.get('Contents/Definition', b'')), size_mentions=size_hits(st.get('Contents/Definition', b'')), sizes=d['sizes']), indent=1))
