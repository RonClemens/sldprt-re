#!/usr/bin/env python3
"""Report-only scan of a ZIP/folder of SolidWorks files: what is in it and what this pipeline can decode. No geometry is written.
usage: survey.py Hydro_CAD.zip [-o survey.json] [--tess] [--jobs N] [--timeout SEC]
  default : container type, size, Parasolid schema banner, face/surface/curve statistics, assembly reference counts
  --tess  : also tessellate every part (slow) and list failing faces / timings"""
import argparse, collections, json, os, re, signal, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); SRC = os.path.join(ROOT, 'src'); sys.path.insert(0, SRC)
from batch import safe_extract, walk
SCHEMA = os.path.join(SRC, 'sch_13006_s_t.txt')
class Timeout(Exception): pass
def _alarm(*_): raise Timeout()
def survey_one(args):
    path, tess, timeout = args
    out = dict(file=os.path.basename(path), size=os.path.getsize(path)); t0 = time.time()
    signal.signal(signal.SIGALRM, _alarm); signal.alarm(timeout)
    try:
        head = open(path, 'rb').read(8)
        if head[:4] == b'\xd0\xcf\x11\xe0': out['container'] = 'OLE2 (pre-2015, unsupported)'; return out
        from sldcont import read_streams
        st = read_streams(path); out['container'] = 'chunked' if st else 'unknown'; out['streams'] = len(st)
        if path.lower().endswith('.sldasm'):
            from sldasm2glb import parse_tree
            files, models, _ = parse_tree(st['swXmlContents/COMPINSTANCETREE'])
            out['instances'] = sum(len(m['refs']) for m in models.values()); out['models'] = len(models)
            out['mates_stream'] = any('MatesList' in k for k in st)
        else:
            from sldprt2glb import geometry_transmits
            from brep import Brep
            best = None
            for name, k, raw in geometry_transmits(st):
                if b'TRANSMIT FILE (deltas)' in raw[:80]: continue
                m = re.search(rb'SCH_\d+_\d+_\d+', raw[:400]); 
                try: br = Brep(raw, SCHEMA)
                except Exception as e: out.setdefault('decode_errors', []).append(repr(e)[:150]); continue
                nf = sum(1 for n in br.N.values() if n['name'] == 'FACE')
                if nf and (best is None or nf > best[0]): best = (nf, br, m.group().decode() if m else None)
            if best is None:    # same last resort as the converter: bodies kept in the feature history
                from sldprt2glb import fallback_transmits
                for name, k, raw in fallback_transmits(st):
                    try: br = Brep(raw, SCHEMA)
                    except Exception: continue
                    nf = sum(1 for n in br.N.values() if n['name'] == 'FACE')
                    if nf and (best is None or nf > best[0]): best = (nf, br, None)
            if best is None: out['error'] = 'no decodable Parasolid body'; return out
            nf, br, banner = best; c = collections.Counter(n['name'] for n in br.N.values())
            out.update(schema=banner, faces=nf, surfaces={k: c[k] for k in ('PLANE', 'CYLINDER', 'CONE', 'SPHERE', 'TORUS', 'B_SURFACE', 'BLENDED_EDGE', 'OFFSET_SURF', 'SWEPT_SURF', 'SPUN_SURF') if c[k]},
                       curves={k: c[k] for k in ('LINE', 'CIRCLE', 'ELLIPSE', 'B_CURVE', 'INTERSECTION', 'SP_CURVE', 'TRIMMED_CURVE') if c[k]})
            if tess:
                import tess as T
                from export import build_mesh
                P, N, I, col, fails = build_mesh(br, T.tess_face, log=lambda *a: None)
                out.update(tris=len(I), failed_faces=len(fails), fail_reasons=sorted({f[1][:80] for f in fails}))
    except Timeout: out['error'] = f'timeout {timeout}s'
    except Exception as e: out['error'] = repr(e)[:200]
    finally: signal.alarm(0); out['sec'] = round(time.time() - t0, 1)
    return out
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('src'); ap.add_argument('-o', '--out', default='survey.json')
    ap.add_argument('--tess', action='store_true'); ap.add_argument('--jobs', type=int, default=os.cpu_count()); ap.add_argument('--timeout', type=int, default=900)
    a = ap.parse_args(); root = a.src
    if os.path.isfile(a.src):
        root = tempfile.mkdtemp(prefix='sldsurvey_'); print('extracted', safe_extract(a.src, root), 'CAD files')
    files = walk(root, '.sldprt') + walk(root, '.sldasm')
    import multiprocessing as mp
    with mp.Pool(max(1, a.jobs), maxtasksperchild=10) as pool: res = pool.map(survey_one, [(f, a.tess, a.timeout) for f in files], chunksize=1)
    json.dump(res, open(a.out, 'w'), indent=1)
    C = collections.Counter
    prt = [r for r in res if r['file'].lower().endswith('.sldprt')]; asm = [r for r in res if r['file'].lower().endswith('.sldasm')]
    print(f'\n{len(prt)} parts, {len(asm)} assemblies, {sum(r["size"] for r in res) / 1e6:.1f} MB')
    print('containers:', dict(C(r.get('container', '?') for r in res)))
    print('schemas   :', dict(C(r.get('schema') for r in prt if r.get('schema'))))
    for key in ('surfaces', 'curves'):
        t = C()
        for r in prt: t.update(r.get(key, {}))
        print(f'{key:10}:', dict(t))
    print('faces total:', sum(r.get('faces', 0) for r in prt), '  max faces in a part:', max([r.get('faces', 0) for r in prt] or [0]))
    print('assembly instances total:', sum(r.get('instances', 0) for r in asm))
    errs = C(r['error'].split('(')[0][:60] for r in res if 'error' in r); print('errors    :', dict(errs))
    if a.tess:
        print('failed faces:', sum(r.get('failed_faces', 0) for r in prt), ' reasons:', dict(C(x for r in prt for x in r.get('fail_reasons', []))))
    print('details ->', a.out)
if __name__ == '__main__': main()
