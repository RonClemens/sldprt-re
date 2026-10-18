"""Reads what a viewer needs from a SolidWorks drawing (.SLDDRW): one preview image per sheet, the sheet
names, and the models the drawing references. Uses the same container reader as the part/assembly code
(sldcont) and inflates only the few streams involved, never the drawing's geometry.

  read_drawing(path) -> dict(sheets=[(name, png_bytes), ...], refs=[model file names, in order of appearance])

A sheet preview is a 640x480 PNG ("Images/Sheet_<n>" in the container): enough to recognise a sheet and to
line its views up with the 3D model, not enough to read dimensions from. The drawing file records which
models it references in one place, before the first sheet (the document's reference list); it does not say
which model each sheet is about, so no per-sheet link is attempted here.
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
    return dict(sheets=sheets, refs=references(streams.get('Contents/Definition', b'')))

def png_size(png):
    import struct
    return struct.unpack('>II', png[16:24]) if len(png) >= 24 and png[:8] == b'\x89PNG\r\n\x1a\n' else (0, 0)
