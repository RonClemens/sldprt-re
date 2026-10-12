#!/usr/bin/env python3
"""Phone-path helper tests: token auth, resumable upload (offset resume + JS client with an injected network failure), URL-import guards,
job listing. Needs the cached example assemblies (python3 tests/regress.py clones them). usage: python3 tests/test_phone.py"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); EX = os.path.join(ROOT, '.cache', 'solidworks_urdf_exporter', 'examples', '4_WHEELER')
def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p
def call(base, path, method='GET', data=None, headers=None, token='tok'):
    h = dict(headers or {}); 
    if token: h['Authorization'] = 'Bearer ' + token
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=h), timeout=30) as r: return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b'{}')
def events(base, job, token='tok'):
    req = urllib.request.Request(f'{base}/jobs/{job}/events', headers={'Authorization': 'Bearer ' + token}); out = []; ev = None
    for raw in urllib.request.urlopen(req, timeout=120):
        l = raw.decode().rstrip()
        if l.startswith('event:'): ev = l[7:]
        elif l.startswith('data:'):
            out.append((ev, json.loads(l[6:])))
            if ev == 'job_done': break
    return out
def main():
    tmp = tempfile.mkdtemp(); z = os.path.join(tmp, 'wheeler.zip')
    with zipfile.ZipFile(z, 'w') as zf:
        for f in os.listdir(EX):
            if f.lower().endswith(('.sldprt', '.sldasm')): zf.write(os.path.join(EX, f), f)
    data = open(z, 'rb').read(); port = free_port(); base = f'http://127.0.0.1:{port}'
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, 'src', 'serve.py'), '--port', str(port), '--work', os.path.join(tmp, 'w'), '--token', 'tok', '--select-timeout', '0'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: urllib.request.urlopen(base + '/health', timeout=1); break
            except Exception: time.sleep(0.2)
        # auth
        assert call(base, '/jobs', token=None)[0] == 401, 'unauthenticated list must be refused'
        assert call(base, '/health', token=None)[0] == 200
        # resumable upload by hand: wrong offset is refused with the true offset, then resumes
        st, m = call(base, '/uploads', 'POST', headers={'X-Filename': 'wheeler.zip', 'X-Size': str(len(data))}); assert st == 200, m
        uid = m['upload']; half = len(data) // 2
        assert call(base, f'/uploads/{uid}', 'PUT', data[:half], {'X-Offset': '0'})[1]['offset'] == half
        st, r = call(base, f'/uploads/{uid}', 'PUT', data[:100], {'X-Offset': '5'}); assert st == 409 and r['offset'] == half, (st, r)
        st, r = call(base, f'/uploads/{uid}/complete', 'POST'); assert st == 409 and r['offset'] == half, 'incomplete upload must not start a job'
        assert call(base, f'/uploads/{uid}')[1]['offset'] == half
        assert call(base, f'/uploads/{uid}', 'PUT', data[half:], {'X-Offset': str(half)})[1]['offset'] == len(data)
        st, r = call(base, f'/uploads/{uid}/complete', 'POST'); assert st == 200, r
        ev = events(base, r['job']); names = [e for e, _ in ev]
        assert names[0] == 'plan' and names[-1] == 'job_done' and 'part_done' in names, names[:5]
        assert ev[-1][1]['parts_failed'] == 0, ev[-1]
        jobs = call(base, '/jobs')[1]['jobs']; assert jobs and jobs[0]['id'] == r['job'] and jobs[0]['done'], jobs
        print('hand-driven resumable upload + job:', len([e for e in names if e == 'part_done']), 'parts')
        # the JS client, with one injected network failure
        out = subprocess.run(['node', os.path.join(ROOT, 'tests', 'phone_client.mjs'), base, 'tok', z], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr[-600:]
        res = json.loads(out.stdout.strip().splitlines()[-1]); assert res['injected'] == 1 and res['sent'] == res['size'], res
        ev = events(base, res['job']); assert ev[-1][0] == 'job_done' and ev[-1][1]['parts_failed'] == 0, ev[-1]
        print('JS client resumed after an injected failure:', res)
        # URL import guards
        for url, want in (('http://github.com/x/y.zip', 400), ('https://example.com/y.zip', 400), ('https://github.com/x/y.txt', 400)):
            st, r = call(base, '/jobs', 'POST', json.dumps(dict(url=url)).encode(), {'Content-Type': 'application/json'}); assert st == want, (url, st, r)
        print('URL import guards ok'); 
    finally: p.kill()
    # no token: URL import refused outright
    port = free_port(); base = f'http://127.0.0.1:{port}'
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, 'src', 'serve.py'), '--port', str(port), '--work', os.path.join(tmp, 'w2')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: urllib.request.urlopen(base + '/health', timeout=1); break
            except Exception: time.sleep(0.2)
        st, r = call(base, '/jobs', 'POST', json.dumps(dict(url='https://github.com/x/y.zip')).encode(), {'Content-Type': 'application/json'}, token=None); assert st == 403, (st, r)
        print('URL import without token refused')
    finally: p.kill()
    print('OK')
main()
