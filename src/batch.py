#!/usr/bin/env python3
"""Batch converter: ZIP (or folder) of SLDPRT/SLDASM -> one GLB per top-level assembly + conversion report.
Parts are converted once, in parallel, into an on-disk cache; assemblies then instance them. A failing part or
assembly never stops the run; everything is listed in report.json.
usage: batch.py Hydro_CAD.zip -o out [--jobs N] [--timeout SEC] [--work DIR]"""
import argparse, json, os, signal, subprocess, sys, tempfile, time, zipfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
SCHEMA = os.path.join(HERE, 'sch_13006_s_t.txt')
def safe_extract(zpath, dest):
    """extract only .sldprt/.sldasm members, refusing paths that escape dest"""
    n = 0; dest = os.path.realpath(dest)
    with zipfile.ZipFile(zpath) as z:
        for m in z.infolist():
            if m.is_dir() or not m.filename.lower().endswith(('.sldprt', '.sldasm')): continue
            t = os.path.realpath(os.path.join(dest, m.filename))
            if os.path.commonpath([dest, t]) != dest: continue
            os.makedirs(os.path.dirname(t), exist_ok=True)
            with z.open(m) as src, open(t, 'wb') as out:
                while (b := src.read(1 << 20)): out.write(b)
            n += 1
    return n
def walk(root, ext):
    return sorted(os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.lower().endswith(ext))
class Timeout(Exception): pass
def _alarm(*_): raise Timeout()
def warm(args):
    path, timeout = args
    from sldprt2glb import load_part
    t0 = time.time(); signal.signal(signal.SIGALRM, _alarm); signal.alarm(timeout)
    try:
        P, N, I, c, fails = load_part(path, SCHEMA)
        return dict(file=os.path.basename(path), ok=True, tris=len(I), failed_faces=len(fails), sec=round(time.time() - t0, 1))
    except Timeout: return dict(file=os.path.basename(path), ok=False, error=f'timeout after {timeout}s')
    except Exception as e: return dict(file=os.path.basename(path), ok=False, error=repr(e)[:200])
    finally: signal.alarm(0)
def top_level(asms):
    """assemblies not referenced as a child of another assembly"""
    from sldcont import read_streams
    from sldasm2glb import parse_tree
    used = set(); ok = []   # (stray assemblies that fail to parse are kept as top-level so the failure is reported)
    for a in asms:
        try:
            st = read_streams(a)
            files, models, _ = parse_tree(st['swXmlContents/COMPINSTANCETREE'])
            me = os.path.basename(a).lower(); ok.append(a)
            for f in files.values():
                if f.upper().endswith('.SLDASM') and f.lower() != me: used.add(f.lower())
        except Exception: ok.append(a)
    return [a for a in ok if os.path.basename(a).lower() not in used] or ok
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('src'); ap.add_argument('-o', '--out', default='out')
    ap.add_argument('--jobs', type=int, default=os.cpu_count()); ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--work', help='extraction + cache dir (default: temp)'); a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    work = a.work or tempfile.mkdtemp(prefix='sldbatch_'); os.makedirs(work, exist_ok=True)
    root = a.src
    if os.path.isfile(a.src):
        root = os.path.join(work, 'src'); print('extracted', safe_extract(a.src, root), 'CAD files')
    os.environ['SLDPRT_CACHE'] = os.path.join(work, 'cache'); os.environ['SLD_SEARCH_ROOT'] = root
    parts = walk(root, '.sldprt'); asms = walk(root, '.sldasm'); print(len(parts), 'parts,', len(asms), 'assemblies')
    import multiprocessing as mp
    rep = dict(parts=[], assemblies=[]); t0 = time.time()
    with mp.Pool(max(1, a.jobs), maxtasksperchild=10) as pool:
        for i, r in enumerate(pool.imap_unordered(warm, [(p, a.timeout) for p in parts]), 1):
            rep['parts'].append(r); print(f'[{i}/{len(parts)}]', r['file'], 'ok' if r['ok'] else 'FAILED ' + r['error'], flush=True)
    for asm in top_level(asms):
        base = os.path.splitext(os.path.basename(asm))[0]
        r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'sldasm2glb.py'), asm, '--schema', SCHEMA, '-o', a.out],
                           capture_output=True, text=True, cwd=HERE, env=os.environ)
        missing = [l.split()[-1] for l in r.stdout.splitlines() if l.startswith(('MISSING', 'FAILED part'))]
        rep['assemblies'].append(dict(file=os.path.basename(asm), ok=r.returncode == 0, glb=base + '.glb' if r.returncode == 0 else None,
                                      problems=[l for l in r.stdout.splitlines() if l.startswith(('MISSING', 'FAILED part'))], error=r.stderr[-300:] if r.returncode else None))
        print('assembly', base, 'ok' if r.returncode == 0 else 'FAILED', flush=True)
    rep['seconds'] = round(time.time() - t0, 1)
    json.dump(rep, open(os.path.join(a.out, 'report.json'), 'w'), indent=1)
    bad = [p for p in rep['parts'] if not p['ok']]
    print(f"done: {len(parts) - len(bad)}/{len(parts)} parts, {sum(x['ok'] for x in rep['assemblies'])}/{len(rep['assemblies'])} assemblies, report.json written")
if __name__ == '__main__': main()
