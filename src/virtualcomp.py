"""Embedded components. (1) Virtual components: parts created inside an assembly have no .SLDPRT file; each is stored as a complete embedded part
container in a 'VirtualComp/<name>' stream of the assembly that owns it (the text after '^' in the component name).
Stream name = component name before '^', cut to ~25 characters plus a numeric id when long. `materialize` writes every
virtual component out as a normal .SLDPRT so the rest of the pipeline needs no special case.
(2) Imported components (STEP files imported into SolidWorks, 'IC~~' paths such as '2584.000_01.stp.SLDPRT'): stored in
'ImportedComp/<exact file name>' streams, also complete part containers; written out under their own file name."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sldcont import read_streams, read_streams_bytes
NS_VIRTUAL = re.compile(r'<swReference [^>]*swName="([^"]*)"[^>]*swIsVirtualComponent="YES"')
def _score(stream, base):
    """score if `stream` is the stream of component `base`: the full name, or -- for names over 25 characters -- the first 25 characters
    plus a numeric id (observed: 'Copy of 4464K354_TYPE 304' + '53'). Anything looser also matched different components whose names
    merely ended in a digit ('Copy of Copy of 27244647-C2' is not a truncated '...-C4 MESH')."""
    if stream == base: return len(base) + 1
    if len(base) > 25 and stream.startswith(base[:25]) and len(stream) > 25 and stream[25:].isdigit(): return 25
    return 0
def _size_matches(data, bb, tol=0.03):
    """does the embedded body's extent match the assembly's recorded bounding box (metres)? Uses the B-rep's vertex points only
    (no tessellation, so it is fast): compares the longest side, which is set by vertices for tubes, plates and blocks."""
    if not bb or len(bb) != 6: return False
    import numpy as np
    from sldcont import read_streams_bytes
    from sldprt2glb import geometry_transmits
    from brep import Brep
    try:
        best = None
        for _, _, raw in geometry_transmits(read_streams_bytes(data)):
            if b'(deltas)' in raw[:80]: continue
            br = Brep(raw, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sch_13006_s_t.txt'))
            pts = np.array([n['pvec'] for n in br.N.values() if n['name'] == 'POINT'])
            if len(pts) and (best is None or len(pts) > len(best)): best = pts
    except Exception: return False
    if best is None: return False
    a = np.sort(best.max(0) - best.min(0)); b = np.sort(np.array(bb[3:]) - np.array(bb[:3]))
    return abs(a[-1] - b[-1]) <= tol * max(b[-1], 1e-3)
def materialize(root, out=None, log=print):
    """write virtual components found under `root` to <root>/__virtual__/<name>.SLDPRT; returns a report dict"""
    out = out or os.path.join(root, '__virtual__'); os.makedirs(out, exist_ok=True)
    asms = sorted(os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.lower().endswith('.sldasm') and '__virtual__' not in d)
    bbox = {}; streams = []; imported = {}; need = {}                       # streams: (asm stem, stream name, bytes); need: component name -> owning asm files
    for a in asms:
        try: st = read_streams(a, want=lambda n: n.startswith(('VirtualComp/', 'ImportedComp/')) or n == 'swXmlContents/COMPINSTANCETREE')
        except Exception as e: log('virtualcomp: cannot read', a, repr(e)[:80]); continue
        stem = os.path.splitext(os.path.basename(a))[0].lower()
        def collect(items, owner_stem, depth=0):
            for k, v in items.items():
                if not k.startswith('VirtualComp/'): continue
                name = k[len('VirtualComp/'):]; streams.append((owner_stem, name, v))
                if depth < 4:     # a virtual *assembly* embeds its own VirtualComp/* streams
                    try: inner = read_streams_bytes(v, want=lambda n: n.startswith('VirtualComp/'))
                    except Exception: continue
                    collect(inner, name.lower(), depth + 1)
        collect(st, stem)
        for k, v in st.items():       # imported (STEP) components keep their file name as the stream name
            if k.startswith('ImportedComp/') and k.upper().endswith('.SLDPRT'): imported.setdefault(k[len('ImportedComp/'):], v)
        for m in re.finditer(r'<swModel [^>]*swName="([^"]*)"[^>]*?swBoundingBox="([^"]*)"', st.get('swXmlContents/COMPINSTANCETREE', b'').decode('utf8', 'replace')):
            bbox[m.group(1)] = [float(x) for x in m.group(2).split()]
        for n in set(NS_VIRTUAL.findall(st.get('swXmlContents/COMPINSTANCETREE', b'').decode('utf8', 'replace'))): need.setdefault(n, set()).add(stem)
    rep = dict(written=[], unmatched=[], ambiguous=[], imported=[])
    for fn, data in sorted(imported.items()):
        dst = os.path.join(out, fn)
        if os.path.exists(dst): continue
        with open(dst, 'wb') as f: f.write(data)
        rep['imported'].append(fn)
    for name in sorted(need):
        base, owner = (name.split('^', 1) + [''])[:2] if '^' in name else (name, '')
        cand = [(sc, st_, sn, v) for st_, sn, v in streams if (sc := _score(sn, base))]
        if owner: own = [c for c in cand if c[1] == owner.lower()]; cand = own or cand
        if not cand: rep['unmatched'].append(name); continue
        best = max(c[0] for c in cand); top = [c for c in cand if c[0] == best]
        if len(top) > 1:     # same truncated name: only accept a candidate whose body size matches the recorded bounding box
            top = [c for c in top if _size_matches(c[3], bbox.get(name))]
            if len(top) != 1: rep['ambiguous'].append(dict(name=name, streams=[c[2] for c in cand if c[0] == best])); continue   # never guess between two bodies
        dst = os.path.join(out, name + '.SLDPRT')
        with open(dst, 'wb') as f: f.write(top[0][3])
        rep['written'].append(name)
    json.dump(rep, open(os.path.join(out, 'index.json'), 'w'), indent=1)
    log(f"virtual components: {len(rep['written'])} written, {len(rep['unmatched'])} unmatched, {len(rep['ambiguous'])} ambiguous (of {len(need)}); imported (STEP) components: {len(rep['imported'])}")
    return rep
if __name__ == '__main__':
    r = materialize(sys.argv[1]); [print(' unmatched:', n) for n in r['unmatched'][:10]]; [print(' ambiguous:', a['name'], a['streams']) for a in r['ambiguous'][:10]]
