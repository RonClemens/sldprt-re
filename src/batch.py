#!/usr/bin/env python3
"""Batch converter: ZIP (or folder) of SLDPRT/SLDASM -> one GLB per top-level assembly + conversion report.
Parts are converted once, in parallel, into an on-disk cache; assemblies then instance them. A failing part or
assembly never stops the run; everything is listed in report.json.
usage: batch.py Hydro_CAD.zip -o out [--jobs N] [--timeout SEC] [--work DIR]"""
import argparse, json, os, subprocess, sys, tempfile, time, zipfile
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
def warm(args):
    path, timeout = args; name = os.path.basename(path)
    try:    # own process per part: subprocess timeouts kill hangs inside native numpy code, which an in-process alarm cannot
        r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'warm.py'), path, SCHEMA], capture_output=True, text=True, timeout=timeout, env=os.environ)
        out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else dict(ok=False, error=('crashed: ' + r.stderr[-150:]))
    except subprocess.TimeoutExpired: out = dict(ok=False, error=f'timeout after {timeout}s')
    except Exception as e: out = dict(ok=False, error=repr(e)[:200])
    return dict(file=name, **out)
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
    ap.add_argument('--jobs', type=int, default=os.cpu_count()); ap.add_argument('--timeout', type=int, default=900, help='per part, seconds'); ap.add_argument('--asm-timeout', type=int, default=1800, help='per assembly, seconds')
    ap.add_argument('--work', help='extraction + cache dir (default: temp)'); a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    work = a.work or tempfile.mkdtemp(prefix='sldbatch_'); os.makedirs(work, exist_ok=True)
    root = a.src
    if os.path.isfile(a.src):
        root = os.path.join(work, 'src'); print('extracted', safe_extract(a.src, root), 'CAD files')
    os.environ['SLDPRT_CACHE'] = os.path.join(work, 'cache'); os.environ['SLD_SEARCH_ROOT'] = root
    parts = walk(root, '.sldprt'); asms = walk(root, '.sldasm'); print(len(parts), 'parts,', len(asms), 'assemblies')
    from concurrent.futures import ThreadPoolExecutor, as_completed
    rep = dict(parts=[], assemblies=[]); t0 = time.time()
    with ThreadPoolExecutor(max(1, a.jobs)) as ex:
        futs = [ex.submit(warm, (p, a.timeout)) for p in sorted(parts, key=os.path.getsize)]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result(); rep['parts'].append(r); print(f'[{i}/{len(parts)}]', r['file'], 'ok' if r['ok'] else 'FAILED ' + r['error'], flush=True)
    def save(): json.dump(rep, open(os.path.join(a.out, 'report.json'), 'w'), indent=1)
    todo = sorted(top_level(asms), key=os.path.getsize)    # smallest first: quick results early, giants last
    def run_asm(asm):
        base = os.path.splitext(os.path.basename(asm))[0]; t1 = time.time()
        try:
            r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'sldasm2glb.py'), asm, '--schema', SCHEMA, '-o', a.out],
                               capture_output=True, text=True, cwd=HERE, env=os.environ, timeout=a.asm_timeout)
        except subprocess.TimeoutExpired:
            return dict(file=os.path.basename(asm), ok=False, error=f'timeout after {a.asm_timeout}s', sec=round(time.time() - t1, 1))
        return dict(file=os.path.basename(asm), ok=r.returncode == 0, glb=base + '.glb' if r.returncode == 0 else None, sec=round(time.time() - t1, 1),
                    problems=[l for l in r.stdout.splitlines() if l.startswith(('MISSING', 'FAILED part'))], error=r.stderr[-300:] if r.returncode else None)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max(1, min(a.jobs, 3))) as ex:     # assemblies hold all parts in memory: keep concurrency modest
        for f in as_completed([ex.submit(run_asm, x) for x in todo]):
            r = f.result(); rep['assemblies'].append(r); save()
            print('assembly', r['file'], 'ok' if r['ok'] else 'FAILED ' + str(r.get('error'))[:80], f"{r['sec']}s", flush=True)
    rep['seconds'] = round(time.time() - t0, 1)
    save()
    bad = [p for p in rep['parts'] if not p['ok']]
    print(f"done: {len(parts) - len(bad)}/{len(parts)} parts, {sum(x['ok'] for x in rep['assemblies'])}/{len(rep['assemblies'])} assemblies, report.json written")
if __name__ == '__main__': main()
