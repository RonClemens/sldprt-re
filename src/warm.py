#!/usr/bin/env python3
"""convert one part into the SLDPRT_CACHE and print a one-line JSON result (run as a subprocess so a hang can be killed)"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
def main():
    path, schema = sys.argv[1], sys.argv[2]; t0 = time.time()
    from sldprt2glb import load_part
    try:
        P, N, I, c, fails = load_part(path, schema)
        r = dict(ok=True, tris=len(I), failed_faces=len(fails))
    except Exception as e: r = dict(ok=False, error=repr(e)[:200])
    r['sec'] = round(time.time() - t0, 1); print(json.dumps(r))
if __name__ == '__main__': main()
