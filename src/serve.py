#!/usr/bin/env python3
"""Local helper for progressive SolidWorks import (see docs/viewer-integration.md).
usage: serve.py [--token T|auto] [--public-url https://tunnel] [--allow-host H] [--replay JOBDIR [--speed X]] [--port 8765] [--origin https://ronclemens.github.io] [--jobs N] [--timeout SEC] [--work DIR]
POST /jobs (raw .sldprt/.sldasm/.zip body, header X-Filename) -> {job}; GET /jobs/<id>/events (SSE); GET /jobs/<id>/parts/<key>.glb;
GET /jobs/<id>/assembly.glb; POST /jobs/<id>/cancel; GET /health. Binds to loopback only."""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
SCHEMA = os.path.join(HERE, 'sch_13006_s_t.txt'); VERSION = '0.1'
import numpy as np
from batch import safe_extract, walk
def _np(o):
    if hasattr(o, 'tolist'): return o.tolist()
    return str(o)
def part_key(fn): return re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.splitext(fn)[0])
class Job:
    def __init__(s, root, name):
        s.id = uuid.uuid4().hex[:12]; s.dir = os.path.join(root, s.id); os.makedirs(s.dir); s.name = name
        s.events = []; s.cv = threading.Condition(); s.cancel = False; s.procs = set(); s.done = False
        s.ready = {}      # key -> glb path
        s.t0 = time.time()
    def emit(s, ev, **data):
        with s.cv:
            s.events.append((ev, data)); s.cv.notify_all()
            try:    # recorded so a finished job can be replayed (--replay) while developing the viewer
                with open(os.path.join(s.dir, 'events.jsonl'), 'a') as f: f.write(json.dumps(dict(t=round(time.time() - s.t0, 3), ev=ev, data=data), default=_np) + '\n')
            except OSError: pass
class Server:
    def __init__(s, a):
        s.a = a; s.root = os.path.abspath(os.path.expanduser(a.work)) if a.work else os.path.join(os.path.expanduser('~'), '.sldprt-re', 'work'); os.makedirs(s.root, exist_ok=True)
        s.cache = os.path.join(s.root, 'cache'); s.jobs = {}; s.lock = threading.Lock()
        s.updir = os.path.join(s.root, 'uploads'); os.makedirs(s.updir, exist_ok=True); s.uploads = {}
    def start(s, filename, upload_path):
        with s.lock:
            for j in s.jobs.values():       # one job at a time: a new import cancels the previous one
                if not j.done: s.stop(j)
        s.prune(); j = Job(s.root, filename); shutil.move(upload_path, os.path.join(j.dir, 'upload' + os.path.splitext(filename)[1].lower()))
        with s.lock: s.jobs[j.id] = j
        threading.Thread(target=s.run, args=(j, filename), daemon=True).start(); return j
    # ---- resumable uploads (phone/tunnel path: a dropped connection resumes at the last good byte instead of restarting a 1.4 GB upload)
    def _up(s, uid):
        m = s.uploads.get(uid)
        if m is None:
            try: m = json.load(open(os.path.join(s.updir, uid + '.json'))); s.uploads[uid] = m      # survives a helper restart
            except (OSError, ValueError): return None
        return m
    def upload_new(s, filename, size):
        uid = uuid.uuid4().hex[:16]; m = dict(id=uid, name=filename, size=size); s.uploads[uid] = m
        json.dump(m, open(os.path.join(s.updir, uid + '.json'), 'w')); open(os.path.join(s.updir, uid + '.part'), 'wb').close(); return m
    def upload_offset(s, uid): return os.path.getsize(os.path.join(s.updir, uid + '.part'))
    def upload_done(s, uid):
        m = s._up(uid); part = os.path.join(s.updir, uid + '.part')
        if s.upload_offset(uid) != m['size']: raise ValueError('incomplete: have %d of %d bytes' % (s.upload_offset(uid), m['size']))
        j = s.start(m['name'], part); s.uploads.pop(uid, None)
        try: os.remove(os.path.join(s.updir, uid + '.json'))
        except OSError: pass
        return j
    # ---- import from a URL (e.g. a GitHub release asset): the helper downloads it, the phone never uploads 1.4 GB
    def url_ok(s, url):
        from urllib.parse import urlsplit
        import ipaddress, socket
        u = urlsplit(url)
        if u.scheme != 'https' or not u.hostname: return 'only https URLs are accepted'
        h = u.hostname.lower()
        if not any(h == a or h.endswith('.' + a) for a in s.a.allow_host): return 'host %s is not allowed (see --allow-host)' % h
        try:
            for fam, _, _, _, sa in socket.getaddrinfo(h, u.port or 443):
                ip = ipaddress.ip_address(sa[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved: return 'host resolves to a private address'
        except OSError as e: return 'cannot resolve host: %s' % e
        return None
    def start_url(s, url, filename):
        with s.lock:
            for j in s.jobs.values():
                if not j.done: s.stop(j)
        s.prune(); j = Job(s.root, filename); j.pending_url = True
        with s.lock: s.jobs[j.id] = j
        def go():
            import urllib.request, urllib.error
            dest = os.path.join(j.dir, 'download')
            try:
                j.emit('download', loaded=0, total=0, url=url)
                req = urllib.request.Request(url, headers={'User-Agent': 'sldprt-re-helper'})
                class Guard(urllib.request.HTTPRedirectHandler):      # a GitHub asset URL redirects to a CDN host: every hop must pass the same checks
                    def redirect_request(self, rq, fp, code, msg, hdrs, newurl):
                        bad = s.url_ok(newurl) if url.startswith('https') else None
                        if bad: raise urllib.error.URLError('redirect refused: ' + bad)
                        return super().redirect_request(rq, fp, code, msg, hdrs, newurl)
                with urllib.request.build_opener(Guard).open(req, timeout=60) as r, open(dest, 'wb') as f:
                    total = int(r.headers.get('Content-Length') or 0); got = 0; last = 0.0
                    while True:
                        if j.cancel: raise RuntimeError('cancelled')
                        b = r.read(1 << 20)
                        if not b: break
                        f.write(b); got += len(b)
                        if time.time() - last > 1.0: last = time.time(); j.emit('download', loaded=got, total=total, url=url)
                j.emit('download', loaded=got, total=total or got, url=url)
            except Exception as e:
                j.emit('job_done', ok=False, error='download_failed: ' + str(e)[:150], cancelled=j.cancel, seconds=round(time.time() - j.t0, 1)); j.done = True; return
            shutil.move(dest, os.path.join(j.dir, 'upload' + os.path.splitext(filename)[1].lower()))
            s.run(j, filename)
        threading.Thread(target=go, daemon=True).start(); return j
    def job_list(s):
        return [dict(id=j.id, name=j.name, done=j.done, events=len(j.events), started=round(j.t0), parts_ready=len(j.ready)) for j in sorted(s.jobs.values(), key=lambda j: j.t0, reverse=True)]
    def prune(s, keep=2):
        """The work dir is persistent now (cache survives restarts), so old jobs must not pile up: each holds the extracted ZIP."""
        done = sorted((j for j in s.jobs.values() if j.done), key=lambda j: j.t0, reverse=True)
        for j in done[keep:]:
            shutil.rmtree(j.dir, ignore_errors=True); s.jobs.pop(j.id, None)
    def start_replay(s):
        """A job whose events and part GLBs come from a recorded job directory (no conversion runs)."""
        with s.lock:
            for j in s.jobs.values():
                if not j.done: s.stop(j)
        j = Job(s.root, 'replay'); src = s.a.replay
        with s.lock: s.jobs[j.id] = j
        def go():
            try:
                rec = [json.loads(l) for l in open(os.path.join(src, 'events.jsonl'))]; last = 0.0
                for r in rec:
                    if j.cancel: break
                    time.sleep(min(max(0.0, r['t'] - last) / max(s.a.speed, 1e-6), 3.0)); last = r['t']
                    d = dict(r['data'])
                    if r['ev'] == 'part_done':
                        j.ready[d['key']] = os.path.join(src, 'parts', d['key'] + '.glb'); d['url'] = f'/jobs/{j.id}/parts/{d["key"]}.glb'
                    if r['ev'] == 'job_done' and j.cancel: d['cancelled'] = True
                    j.emit(r['ev'], **d)
            finally: j.done = True
        threading.Thread(target=go, daemon=True).start(); return j
    def stop(s, j):
        j.cancel = True
        for p in list(j.procs):
            try: p.kill()
            except Exception: pass
    # ---- plan (instances placed from the component tree, no geometry needed)
    def plan(s, j, files, asms):
        from sldcont import read_streams
        from sldasm2glb import parse_tree, flatten_instances
        from batch import top_level
        idx = {}
        for f in files: idx.setdefault(os.path.basename(f).lower(), f)
        inst = []; parts = {}; missing = set(); j.used_asms = []; j.skipped = []
        for asm in sorted(top_level(asms), key=os.path.getsize, reverse=True):
            st = read_streams(asm)
            if 'swXmlContents/COMPINSTANCETREE' not in st: j.skipped.append(dict(file=os.path.basename(asm), reason='ole2' if open(asm, 'rb').read(4) == b'\xd0\xcf\x11\xe0' else 'no_component_tree')); continue
            j.used_asms.append(asm)
            _, models, _ = parse_tree(st['swXmlContents/COMPINSTANCETREE'])
            me = os.path.basename(asm).lower()
            root = [m for m in models.values() if m['file'] and m['file'].lower() == me and m['refs']]
            root = root[0] if root else max(models.values(), key=lambda m: len(m['refs']))
            bbox = {m['file']: m['bbox'] for m in models.values() if m['file']}
            prefix = os.path.splitext(os.path.basename(asm))[0]
            for it in flatten_instances(models, root['id']):
                if it.get('assembly') or it['part'] is None: continue
                fn = it['part'] + '.SLDPRT'; key = part_key(fn)
                path = idx.get(fn.lower())
                bb_ = bbox.get(fn)
                if path is None: missing.add(fn); parts.setdefault(key, dict(key=key, file=fn, path=None, bytes=0)); 
                else: parts.setdefault(key, dict(key=key, file=fn, path=path, bytes=os.path.getsize(path), bbox=np.array(bb_).reshape(2, 3).tolist() if bb_ and len(bb_) == 6 else None))
                M = np.asarray(it['M']); bb = bbox.get(fn); world = None
                if bb and len(bb) == 6:
                    c = np.array([[x, y, z, 1.0] for x in (bb[0], bb[3]) for y in (bb[1], bb[4]) for z in (bb[2], bb[5])]) @ M
                    world = [c[:, :3].min(0).tolist(), c[:, :3].max(0).tolist()]
                inst.append(dict(id=len(inst), path=prefix + '/' + it['path'], part=key, matrix=M.reshape(-1).tolist(), bbox=world,
                                 hidden=bool(it['hidden']), suppressed=bool(it['suppressed'])))
        return inst, parts, sorted(missing)
    def run(s, j, filename):
        try:
            ext = os.path.splitext(filename)[1].lower(); up = os.path.join(j.dir, 'upload' + ext); src = os.path.join(j.dir, 'src'); os.makedirs(src)
            if ext == '.zip': n = safe_extract(up, src)
            else: shutil.copy(up, os.path.join(src, filename))
            os.remove(up)    # the extracted copy is all that is needed; a 1.4 GB upload would otherwise stay on disk
            asms = walk(src, '.sldasm'); vrep = dict(written=[], unmatched=[], ambiguous=[])
            if asms:
                from virtualcomp import materialize   # virtual components are embedded in their assembly: write them out as ordinary parts
                vrep = materialize(src, log=lambda *a: None)
            files = walk(src, '.sldprt') + asms
            if asms: inst, parts, missing = s.plan(j, files, asms)
            else:    # standalone parts: one identity instance each
                inst, parts, missing = [], {}, []
                for f in files:
                    k = part_key(os.path.basename(f)); parts[k] = dict(key=k, file=os.path.basename(f), path=f, bytes=os.path.getsize(f))
                    inst.append(dict(id=len(inst), path=os.path.splitext(os.path.basename(f))[0], part=k, matrix=np.eye(4).reshape(-1).tolist(), bbox=None, hidden=False, suppressed=False))
            j.parts = parts; need = {i['part'] for i in inst if not i['suppressed']}
            todo = sorted((parts[k] for k in need if parts[k]['path']), key=lambda p: p['bytes'])
            j.emit('plan', name=os.path.splitext(filename)[0], instances=inst, parts=[{k: v for k, v in p.items() if k not in ('path', 'bbox')} for p in parts.values()], missing=missing, skipped=getattr(j, 'skipped', []), virtual=dict(written=len(vrep['written']), unmatched=vrep['unmatched'], ambiguous=[a['name'] for a in vrep['ambiguous']]))
            for k in need:
                if not parts[k]['path']: j.emit('part_failed', key=k, reason='missing_file')
            counts = dict(ok=0, bad=len(need) - len(todo), total=len(need)); lk = threading.Lock()
            # one numpy thread per worker: N workers x N BLAS threads oversubscribes a laptop and turns slow parts into timeouts
            env = dict(os.environ, SLDPRT_CACHE=s.cache, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1'); retry = []
            os.makedirs(os.path.join(j.dir, 'parts'), exist_ok=True)
            def work(p, timeout=None, last=False):
                if j.cancel: return
                out = os.path.join(j.dir, 'parts', p['key'] + '.glb'); j.emit('part_started', key=p['key']); t1 = time.time()
                try:
                    pr = subprocess.Popen([sys.executable, '-I', os.path.join(HERE, 'warm.py'), p['path'], SCHEMA, out, json.dumps(p.get('bbox'))], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                    j.procs.add(pr)
                    try: so, se = pr.communicate(timeout=timeout or s.a.timeout)
                    except subprocess.TimeoutExpired: pr.kill(); pr.communicate(); res = dict(ok=False, error='timeout')
                    else: res = json.loads(so.strip().splitlines()[-1]) if so.strip() else dict(ok=False, error='crashed: ' + se[-120:])
                    j.procs.discard(pr)
                except Exception as e: res = dict(ok=False, error='error:' + repr(e)[:100])
                if j.cancel: return
                if res.get('error') == 'timeout' and not last:      # slow machine, not a broken part: retried below with more time and less contention
                    retry.append(p); j.emit('part_retry', key=p['key'], after_s=round(time.time() - t1)); return
                with lk:
                    if res.get('ok'): counts['ok'] += 1; j.ready[p['key']] = out
                    else: counts['bad'] += 1
                if res.get('ok'): j.emit('part_done', key=p['key'], url=f'/jobs/{j.id}/parts/{p["key"]}.glb', tris=res['tris'], failed_faces=res['failed_faces'], sec=res['sec'])
                else: j.emit('part_failed', key=p['key'], reason=res.get('error', 'error'))
                ready = [i for i in inst if i['part'] in j.ready]
                j.emit('progress', parts_done=counts['ok'] + counts['bad'], parts_total=counts['total'], instances_ready=len(ready), instances_total=len(inst), elapsed_s=round(time.time() - j.t0, 1))
            with ThreadPoolExecutor(max(1, s.a.jobs)) as ex: list(ex.map(work, todo))
            if retry and not j.cancel:
                with ThreadPoolExecutor(max(1, s.a.jobs // 2)) as ex: list(ex.map(lambda p: work(p, s.a.timeout * 5, True), sorted(retry, key=lambda p: p['bytes'])))
            j.inst = inst
            if getattr(j, 'used_asms', None) and not j.cancel:
                try:
                    from sldasm_mates import build_report
                    os.environ['SLD_SEARCH_ROOT'] = os.path.join(j.dir, 'src')    # mate resolution reads part faces: it must find parts and virtual components too
                    rep = build_report(max(j.used_asms, key=os.path.getsize), SCHEMA)
                    j.emit('mates', **json.loads(json.dumps(dict(joints=rep['joints'], mates=[{k: m[k] for k in ('name', 'type', 'entities', 'params', 'check') if k in m} for m in rep['mates']]), default=_np)))
                except Exception as e: j.emit('mates_unavailable', reason=repr(e)[:150])
            j.emit('job_done', ok=counts['bad'] == 0, parts_ok=counts['ok'], parts_failed=counts['bad'], cancelled=j.cancel, seconds=round(time.time() - j.t0, 1))
        except Exception as e: j.emit('job_done', ok=False, error=repr(e)[:200], cancelled=j.cancel, seconds=round(time.time() - j.t0, 1))
        finally: j.done = True
    def assembly_glb(s, j):
        from sldprt2glb import load_part
        from export import write_glb_scene
        os.environ['SLDPRT_CACHE'] = s.cache; parts = {}; kids = []
        for i in getattr(j, 'inst', []):
            if i['suppressed'] or i['part'] not in j.ready: continue
            if i['part'] not in parts:
                bb = j.parts[i['part']].get('bbox')    # same recorded-box hint (and so the same cache entry) as the conversion step
                P, N, I, c, _ = load_part(j.parts[i['part']]['path'], SCHEMA, bbox=tuple(map(tuple, bb)) if bb else None); parts[i['part']] = (P, N, I, c)
            kids.append(dict(name=i['path'].split('/')[-1], matrix=np.array(i['matrix']).reshape(4, 4), part=i['part']))
        out = os.path.join(j.dir, 'assembly.glb'); write_glb_scene(out, parts, dict(name=j.name, children=kids), os.path.splitext(j.name)[0]); return out
def make_handler(S):
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *a): pass
        def _cors(self):
            o = self.headers.get('Origin', '')
            if o in S.a.origins or re.match(r'^http://(localhost|127\.0\.0\.1)(:\d+)?$', o):
                self.send_header('Access-Control-Allow-Origin', o); self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Private-Network', 'true')
                self.send_header('Access-Control-Allow-Headers', 'X-Filename, X-Size, X-Offset, Content-Type, Last-Event-ID, Authorization'); self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS')
        def _json(self, code, obj):
            b = json.dumps(obj, default=_np).encode(); self.send_response(code); self._cors(); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_OPTIONS(self): self.send_response(204); self._cors(); self.send_header('Content-Length', '0'); self.end_headers()
        def _auth(self):
            if not S.a.token: return True
            from urllib.parse import urlsplit, parse_qs
            q = parse_qs(urlsplit(self.path).query).get('token', [''])[0]
            ok = self.headers.get('Authorization', '') == 'Bearer ' + S.a.token or q == S.a.token
            if not ok: self._json(401, dict(error='bad or missing token'))
            return ok
        def do_POST(self):
            if not self._auth(): return
            self.path = self.path.split('?')[0]
            if self.path == '/jobs' and S.a.replay:
                n = int(self.headers.get('Content-Length', 0))
                while n > 0:    # the upload body is ignored in replay mode
                    b = self.rfile.read(min(n, 1 << 20)); n -= len(b)
                    if not b: break
                return self._json(200, dict(job=S.start_replay().id))
            if self.path == '/uploads':
                fn = os.path.basename(self.headers.get('X-Filename', 'upload.zip')); ext = os.path.splitext(fn)[1].lower()
                if ext not in ('.zip', '.sldprt', '.sldasm'): return self._json(400, dict(error='unsupported file type ' + ext))
                try: size = int(self.headers.get('X-Size', ''))
                except ValueError: return self._json(400, dict(error='X-Size required'))
                m = S.upload_new(fn, size); return self._json(200, dict(upload=m['id'], offset=0, chunk=32 << 20))
            m = re.match(r'^/uploads/(\w+)/complete$', self.path)
            if m:
                if not S._up(m.group(1)): return self._json(404, dict(error='unknown upload'))
                try: return self._json(200, dict(job=S.upload_done(m.group(1)).id))
                except ValueError as e: return self._json(409, dict(error=str(e), offset=S.upload_offset(m.group(1))))
            if self.path == '/jobs' and 'json' in self.headers.get('Content-Type', ''):
                n = int(self.headers.get('Content-Length', 0))
                try: body = json.loads(self.rfile.read(n) or b'{}')
                except ValueError: return self._json(400, dict(error='bad json'))
                url = body.get('url', '')
                if not S.a.token: return self._json(403, dict(error='import from URL needs --token (the helper must not fetch URLs for unauthenticated callers)'))
                bad = S.url_ok(url)
                if bad: return self._json(400, dict(error=bad))
                fn = os.path.basename(url.split('?')[0]) or 'download.zip'; ext = os.path.splitext(fn)[1].lower()
                if ext not in ('.zip', '.sldprt', '.sldasm'): return self._json(400, dict(error='unsupported file type ' + ext))
                return self._json(200, dict(job=S.start_url(url, fn).id))
            if self.path == '/jobs':
                fn = os.path.basename(self.headers.get('X-Filename', 'upload.zip')); ext = os.path.splitext(fn)[1].lower()
                if ext not in ('.zip', '.sldprt', '.sldasm'): return self._json(400, dict(error='unsupported file type ' + ext))
                n = int(self.headers.get('Content-Length', 0)); tmp = os.path.join(S.root, 'up_' + uuid.uuid4().hex)
                with open(tmp, 'wb') as f:
                    while n > 0:
                        b = self.rfile.read(min(n, 1 << 20))
                        if not b: break
                        f.write(b); n -= len(b)
                if n > 0: os.remove(tmp); return self._json(400, dict(error='upload incomplete'))
                return self._json(200, dict(job=S.start(fn, tmp).id))
            m = re.match(r'^/jobs/(\w+)/cancel$', self.path)
            if m and m.group(1) in S.jobs: S.stop(S.jobs[m.group(1)]); return self._json(200, dict(ok=True))
            self._json(404, dict(error='not found'))
        def do_PUT(self):
            if not self._auth(): return
            m = re.match(r'^/uploads/(\w+)$', self.path.split('?')[0]); meta = S._up(m.group(1)) if m else None
            if not meta: return self._json(404, dict(error='unknown upload'))
            have = S.upload_offset(meta['id']); off = int(self.headers.get('X-Offset', -1)); n = int(self.headers.get('Content-Length', 0))
            if off != have or have + n > meta['size']:    # the client resumes from the offset we report
                while n > 0:
                    b = self.rfile.read(min(n, 1 << 20)); n -= len(b)
                    if not b: break
                return self._json(409, dict(error='offset mismatch', offset=have))
            with open(os.path.join(S.updir, meta['id'] + '.part'), 'ab') as f:
                while n > 0:
                    b = self.rfile.read(min(n, 1 << 20))
                    if not b: break
                    f.write(b); n -= len(b)
            self._json(200, dict(offset=S.upload_offset(meta['id'])))
        def do_GET(self):
            if self.path.split('?')[0] == '/health': return self._json(200, dict(ok=True, version=VERSION, auth=bool(S.a.token), replay=bool(S.a.replay)))
            if not self._auth(): return
            self.path = self.path.split('?')[0]
            if self.path == '/jobs': return self._json(200, dict(jobs=S.job_list()))
            m = re.match(r'^/uploads/(\w+)$', self.path)
            if m:
                u = S._up(m.group(1))
                return self._json(200, dict(offset=S.upload_offset(u['id']), size=u['size'])) if u else self._json(404, dict(error='unknown upload'))
            m = re.match(r'^/jobs/(\w+)/(events|assembly\.glb|parts/([\w.-]+)\.glb)$', self.path)
            j = S.jobs.get(m.group(1)) if m else None
            if not j: return self._json(404, dict(error='not found'))
            if m.group(2) == 'events':
                self.send_response(200); self._cors(); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Cache-Control', 'no-cache'); self.send_header('Connection', 'close'); self.end_headers()
                i = int(self.headers.get('Last-Event-ID', -1)) + 1
                try:
                    while True:
                        with j.cv:
                            if i >= len(j.events) and not j.done: j.cv.wait(10)
                            batch = j.events[i:]; stop = j.done and i + len(batch) >= len(j.events)
                        if not batch: self.wfile.write(b': keep-alive\n\n'); self.wfile.flush()
                        for k, (ev, data) in enumerate(batch):
                            self.wfile.write(f'id: {i + k}\nevent: {ev}\ndata: {json.dumps(data, default=_np)}\n\n'.encode())
                        self.wfile.flush(); i += len(batch)
                        if stop: break
                except (BrokenPipeError, ConnectionResetError): pass
                self.close_connection = True; return
            path = S.assembly_glb(j) if m.group(2) == 'assembly.glb' else j.ready.get(m.group(3))
            if not path or not os.path.exists(path): return self._json(404, dict(error='not ready'))
            sz = os.path.getsize(path); self.send_response(200); self._cors(); self.send_header('Content-Type', 'model/gltf-binary'); self.send_header('Content-Length', str(sz)); self.end_headers()
            with open(path, 'rb') as f: shutil.copyfileobj(f, self.wfile)
    return H
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--port', type=int, default=8765); ap.add_argument('--origin', action='append', default=['https://ronclemens.github.io'])
    ap.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 2) - 1)); ap.add_argument('--timeout', type=int, default=120, help='per-part seconds; a part that times out is retried once with 5x this and half the parallelism'); ap.add_argument('--work', help='work dir (cache, jobs). Default: ~/.sldprt-re/work, so the conversion cache survives restarts')
    ap.add_argument('--token', default=os.environ.get('SLD_TOKEN'), help='require this bearer token (header, or ?token= for EventSource)')
    ap.add_argument('--replay', metavar='JOBDIR', help='serve a recorded job dir (events.jsonl + parts/) instead of converting'); ap.add_argument('--speed', type=float, default=1.0)
    ap.add_argument('--allow-host', action='append', default=['github.com', 'githubusercontent.com'], help='hosts the helper may download from (import by URL); subdomains included')
    ap.add_argument('--public-url', help='the https URL of the tunnel to this helper; prints a ready-to-open ipb_viz link for the phone')
    ap.add_argument('--viewer', default='https://ronclemens.github.io/ipb_viz/')
    a = ap.parse_args(); a.origins = set(a.origin)
    if a.token == 'auto': import secrets; a.token = secrets.token_urlsafe(18)
    S = Server(a)
    srv = ThreadingHTTPServer(('127.0.0.1', a.port), make_handler(S)); srv.daemon_threads = True
    print(f'sldprt-re helper {VERSION} on http://127.0.0.1:{a.port}  (work dir {S.root}, allowed origins {sorted(a.origins)})', flush=True)
    if a.public_url:
        if not a.token: print('warning: --public-url without --token: anyone with the URL can use this helper. Use --token auto.', flush=True)
        from urllib.parse import quote
        print('open on the phone: %s?sldhelper=%s%s' % (a.viewer, quote(a.public_url.rstrip('/'), safe=''), '&sldtoken=' + a.token if a.token else ''), flush=True)
    elif a.token: print('token: ' + a.token, flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
if __name__ == '__main__': main()
