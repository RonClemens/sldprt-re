// Framework-neutral client for the sldprt-re local helper (src/serve.py). No dependencies; ES module.
// const c = new SldHelperClient({ base: 'http://127.0.0.1:8765', token });
// await c.health(); const { job } = await c.upload(file, { onProgress }); c.events(job, handlers); c.cancel(job);
export class SldHelperClient {
  constructor({ base = 'http://127.0.0.1:8765', token = null } = {}) { this.base = base.replace(/\/$/, ''); this.token = token; }
  _q(url) { return this.token ? url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(this.token) : url; }
  _h(h = {}) { return this.token ? { ...h, Authorization: 'Bearer ' + this.token } : h; }
  /** Resolves {ok, version, auth, replay}; rejects (TypeError) if the helper is not running or the browser blocked the call. */
  async health() { const r = await fetch(this.base + '/health'); return r.json(); }
  /** Streams `file` (a File/Blob, up to many GB) as the raw body. onProgress(loaded, total). Returns {job}. */
  upload(file, { onProgress, signal } = {}) {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest(); x.open('POST', this.base + '/jobs');
      x.setRequestHeader('X-Filename', file.name);
      if (this.token) x.setRequestHeader('Authorization', 'Bearer ' + this.token);
      x.upload.onprogress = (e) => onProgress && onProgress(e.loaded, e.total || file.size);
      x.onload = () => { let b = {}; try { b = JSON.parse(x.responseText); } catch (_) {} x.status === 200 && b.job ? resolve(b) : reject(new Error(b.error || 'upload failed (' + x.status + ')')); };
      x.onerror = () => reject(new Error('could not reach the helper'));
      if (signal) signal.addEventListener('abort', () => x.abort());
      x.onabort = () => reject(new DOMException('aborted', 'AbortError'));
      x.send(file);
    });
  }
  /** Subscribes to a job's events. handlers: {plan, part_started, part_done, part_failed, progress, mates, job_done, stalled(sec), error}.
   *  Returns close(). EventSource reconnects by itself and resumes through Last-Event-ID. */
  events(job, handlers, { stallAfterMs = 30000 } = {}) {
    const es = new EventSource(this._q(`${this.base}/jobs/${job}/events`)); let last = Date.now(), closed = false;
    const names = ['plan', 'part_started', 'part_done', 'part_failed', 'progress', 'mates', 'mates_unavailable', 'job_done'];
    for (const n of names) es.addEventListener(n, (e) => { last = Date.now(); const d = JSON.parse(e.data); handlers[n] && handlers[n](d); if (n === 'job_done') close(); });
    es.onerror = () => { if (!closed && handlers.error) handlers.error(new Error('event stream interrupted')); };
    const t = setInterval(() => { const s = (Date.now() - last) / 1000; if (!closed && s * 1000 > stallAfterMs && handlers.stalled) handlers.stalled(Math.round(s)); }, 5000);
    function close() { closed = true; clearInterval(t); es.close(); }
    return close;
  }
  async fetchGlb(urlPath) { const r = await fetch(this._q(this.base + urlPath), { headers: this._h() }); if (!r.ok) throw new Error('GET ' + urlPath + ' -> ' + r.status); return r.arrayBuffer(); }
  assemblyUrl(job) { return this._q(`${this.base}/jobs/${job}/assembly.glb`); }
  async cancel(job) { await fetch(`${this.base}/jobs/${job}/cancel`, { method: 'POST', headers: this._h() }); }
}
