#!/usr/bin/env python3
"""A part that exceeds --timeout must be retried (5x time, no part_failed first), and old jobs must be pruned. usage: python3 tests/test_retry.py"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); EX = os.path.join(ROOT, '.cache', 'solidworks_urdf_exporter', 'examples', '3_DOF_ARM')
s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close(); base = f'http://127.0.0.1:{port}'
tmp = tempfile.mkdtemp(); z = os.path.join(tmp, 'a.zip')
with zipfile.ZipFile(z, 'w') as zf:
    for f in os.listdir(EX):
        if f.lower().endswith(('.sldprt', '.sldasm')): zf.write(os.path.join(EX, f), f)
work = os.path.join(tmp, 'w'); env = dict(os.environ, SLD_TOL='1e-5')       # fine tolerance + a 1 s limit: the big part cannot finish in time on the first try
p = subprocess.Popen([sys.executable, os.path.join(ROOT, 'src', 'serve.py'), '--port', str(port), '--work', work, '--timeout', '1', '--jobs', '2'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
try:
    for _ in range(50):
        try: urllib.request.urlopen(base + '/health', timeout=1); break
        except Exception: time.sleep(0.2)
    def run():
        job = json.load(urllib.request.urlopen(urllib.request.Request(base + '/jobs', data=open(z, 'rb').read(), method='POST', headers={'X-Filename': 'a.zip'})))['job']; out = []; ev = None
        for raw in urllib.request.urlopen(f'{base}/jobs/{job}/events', timeout=300):
            l = raw.decode().rstrip()
            if l.startswith('event:'): ev = l[7:]
            elif l.startswith('data:'):
                out.append((ev, json.loads(l[6:])))
                if ev == 'job_done': break
        return job, out
    job, ev = run(); names = [e for e, _ in ev]
    assert 'part_retry' in names, names
    retried = {d['key'] for e, d in ev if e == 'part_retry'}; failed = {d['key'] for e, d in ev if e == 'part_failed'}; done = {d['key'] for e, d in ev if e == 'part_done'}
    assert retried <= done and not (retried & failed), (retried, failed, done)
    assert ev[-1][1]['parts_failed'] == 0, ev[-1]
    print('retried and completed:', sorted(retried))
    for _ in range(3): run()
    jobs = [d for d in os.listdir(work) if d not in ('cache', 'uploads')]; assert len(jobs) <= 3, jobs     # newest 2 kept + the one just finished
    assert not any(f.startswith('upload') for d in jobs for f in os.listdir(os.path.join(work, d))), 'upload copies must be removed'
    print('pruning ok:', len(jobs), 'job dirs'); print('OK')
finally: p.kill()
