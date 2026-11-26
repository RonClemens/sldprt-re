#!/usr/bin/env python3
"""The helper's drawing-check endpoint end to end on synthetic data: a replay job holding one box part (100 x 60 x 10 mm) and the synthetic drawing PDF (tools/make_test_drawing.py), posted as a raw PDF.
Needs pdfminer.six, opencv-python-headless, scipy. usage: python3 tests/test_serve_check.py"""
import json, os, socket, struct, subprocess, sys, tempfile, time, urllib.error, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
try:
    import numpy as np, cv2, scipy, pdfminer  # noqa
except ImportError as e:
    print('SKIP (missing', e.name + ')'); sys.exit(0)
import make_test_drawing as G


def box_glb(path, size=(0.1, 0.06, 0.01)):
    x, y, z = size; V = np.array([[a * x, b * y, c * z] for a in (0, 1) for b in (0, 1) for c in (0, 1)], np.float32); I = []
    for a, b, c, d in [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]: I += [a, b, c, a, c, d]
    I = np.array(I, np.uint16); vb = V.tobytes(); ib = I.tobytes(); ib += b'\0' * (-len(ib) % 4)
    js = dict(asset=dict(version='2.0'), scene=0, scenes=[dict(nodes=[0])], nodes=[dict(name='Box<1>', mesh=0)], meshes=[dict(primitives=[dict(attributes=dict(POSITION=0), indices=1)])],
              accessors=[dict(bufferView=0, componentType=5126, count=8, type='VEC3', min=V.min(0).tolist(), max=V.max(0).tolist()), dict(bufferView=1, componentType=5123, count=len(I), type='SCALAR')],
              bufferViews=[dict(buffer=0, byteOffset=0, byteLength=len(vb)), dict(buffer=0, byteOffset=len(vb), byteLength=len(I) * 2)], buffers=[dict(byteLength=len(vb) + len(ib))])
    j = json.dumps(js).encode(); j += b' ' * (-len(j) % 4); b = vb + ib
    open(path, 'wb').write(b'glTF' + struct.pack('<II', 2, 12 + 8 + len(j) + 8 + len(b)) + struct.pack('<I', len(j)) + b'JSON' + j + struct.pack('<I', len(b)) + b'BIN\0' + b)


tmp = tempfile.mkdtemp(); rep = os.path.join(tmp, 'rep'); os.makedirs(os.path.join(rep, 'parts')); box_glb(os.path.join(rep, 'parts', 'Box.glb'))
with open(os.path.join(rep, 'events.jsonl'), 'w') as f:
    f.write(json.dumps(dict(t=0, ev='part_done', data=dict(key='Box'))) + '\n'); f.write(json.dumps(dict(t=0, ev='job_done', data=dict(parts_failed=0))) + '\n')
pdf = os.path.join(tmp, 'd.pdf'); G.write_pdf(G.build(), pdf); pdf_bytes = open(pdf, 'rb').read()
sk = socket.socket(); sk.bind(('127.0.0.1', 0)); port = sk.getsockname()[1]; sk.close(); base = f'http://127.0.0.1:{port}'
p = subprocess.Popen([sys.executable, os.path.join(ROOT, 'src', 'serve.py'), '--port', str(port), '--work', os.path.join(tmp, 'w'), '--replay', rep, '--speed', '1000', '--token', 'tok'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
H = {'Authorization': 'Bearer tok'}


def call(path, data=None, method=None, headers=None):
    req = urllib.request.Request(base + path, data=data, method=method or ('POST' if data is not None else 'GET'), headers={**H, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()


def wait_check(job, cid):
    for _ in range(300):
        st = json.loads(call(f'/jobs/{job}/checks/{cid}')[1])
        if st['state'] != 'running': return st
        time.sleep(0.5)
    raise AssertionError('check did not finish')


try:
    for _ in range(50):
        try: urllib.request.urlopen(base + '/health', timeout=1); break
        except Exception: time.sleep(0.2)
    assert call('/jobs/x/checks', b'{}', headers={'Content-Type': 'application/json'})[0] == 404, 'unknown job'
    job = json.loads(call('/jobs', b'', headers={'X-Filename': 'a.zip'})[1])['job']; time.sleep(1.5)
    assert call(f'/jobs/{job}/checks', b'{}', headers={'Content-Type': 'application/json'})[0] == 400, 'JSON without doc'
    assert call(f'/jobs/{job}/checks', b'x', headers={'Content-Type': 'text/plain'})[0] == 415
    assert call(f'/jobs/{job}/checks?page=0', pdf_bytes, headers={'Content-Type': 'application/pdf'})[0] == 400, 'page 0'
    code, body = call(f'/jobs/{job}/checks?page=1&part=Box', pdf_bytes, headers={'Content-Type': 'application/pdf'}); assert code == 202, (code, body); cid = json.loads(body)['check']
    st = wait_check(job, cid); assert st['state'] == 'done' and st['error'] is None, st
    s = st['summary']; assert s['dimensions'] >= 6 and s['status'].get('pass', 0) >= 3 and not s['status'].get('fail'), s
    assert 'text' not in json.dumps(s) and set(st['files']) == {'annotated.glb', 'determinacy.md', 'determinacy.json', 'results.json'}, st
    assert s['determinacy']['undefined'] >= 0 and 'bom_issues' in s
    code, glb = call(f'/jobs/{job}/checks/{cid}/annotated.glb'); assert code == 200 and glb[:4] == b'glTF' and b'DIM ' in glb
    code, md = call(f'/jobs/{job}/checks/{cid}/determinacy.md'); assert code == 200 and b'Determinacy report' in md
    assert call(f'/jobs/{job}/checks/{cid}/bom.csv')[0] == 404 and call(f'/jobs/{job}/checks/{cid}/..%2f..%2fx')[0] == 404
    assert len(json.loads(call(f'/jobs/{job}/checks')[1])['checks']) == 1
    # failures come back as a state, with a code a host can show
    code, body = call(f'/jobs/{job}/checks?page=1&part=Nope', pdf_bytes, headers={'Content-Type': 'application/pdf'}); st = wait_check(job, json.loads(body)['check']); assert st['state'] == 'error' and st['error']['code'] == 'bad-request', st
    code, body = call(f'/jobs/{job}/checks?page=7&part=Box', pdf_bytes, headers={'Content-Type': 'application/pdf'}); st = wait_check(job, json.loads(body)['check']); assert st['state'] == 'error' and st['error']['code'] == 'bad-page', st
    code, body = call(f'/jobs/{job}/checks?page=1&part=Box', b'not a pdf', headers={'Content-Type': 'application/pdf'}); st = wait_check(job, json.loads(body)['check']); assert st['state'] == 'error' and st['error']['code'] in ('unreadable-pdf', 'no-dimensions'), st
    assert call(f'/jobs/{job}/checks?page=1&part=Box', pdf_bytes, headers={'Content-Type': 'application/pdf', 'Authorization': 'Bearer wrong'})[0] == 401
    print('OK: raw-PDF drawing check -> %d dimensions, status %s, determinacy %s; error states bad-request / bad-page / unreadable; auth enforced' % (s['dimensions'], s['status'], s['determinacy']))
finally:
    p.kill(); p.wait()
