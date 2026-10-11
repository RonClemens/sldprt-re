// Progressive SolidWorks import for a Three.js viewer (contract: docs/viewer-integration.md section 5).
// THREE and GLTFLoader are injected so this file does not care how the host app resolves modules.
import { SldHelperClient } from './sld-helper-client.js';

/** hooks: { showGroup(group)  -> host replaces its current model with this group (and numbers meshes/fits the camera);
 *           onStatus(text, kind), onPanel(state) -> progress UI, refreshPart(mesh) -> called after a mesh's geometry/material was swapped }
 *  Returns {cancel(), keepPartial(), setShowUnresolved(bool), done: Promise} */
export function startProgressiveImport({ THREE, GLTFLoader, file, base, token, hooks, concurrency = 4 }) {
  const client = new SldHelperClient({ base, token }); const loader = new GLTFLoader();
  const st = { phase: 'upload', loaded: 0, total: file.size, partsDone: 0, partsTotal: 0, ready: 0, instTotal: 0, failed: [], elapsed: 0, stalled: 0, cancelled: false, finished: false };
  const panel = () => hooks.onPanel && hooks.onPanel({ ...st, failed: st.failed.slice() });
  const byPart = new Map();            // part key -> [mesh]
  const partState = new Map();         // part key -> 'pending' | 'done' | 'failed'
  const failedMeshes = new Set();      // meshes drawn as the red "unresolved" box
  let group = null, job = null, close = null, showUnresolved = true;
  const boxMat = () => new THREE.MeshBasicMaterial({ color: 0x5b8def, transparent: true, opacity: 0.18, depthWrite: false });
  const redMat = () => new THREE.MeshBasicMaterial({ color: 0xe5484d, transparent: true, opacity: 0.35, depthWrite: false });
  const queue = []; let active = 0;
  const pump = () => { while (active < concurrency && queue.length && !st.cancelled) { active++; queue.shift()().finally(() => { active--; pump(); }); } };

  function onPlan(p) {
    st.phase = 'convert'; st.instTotal = p.instances.length; st.partsTotal = 0;
    group = new THREE.Group(); group.name = p.name || file.name;
    const unit = new THREE.BoxGeometry(1, 1, 1); const sharedBox = boxMat();
    for (const inst of p.instances) {
      if (inst.suppressed) continue;                                  // suppressed instances are not drawn by SolidWorks either; ids stay stable because we skip, not renumber
      const m = new THREE.Mesh(unit, sharedBox);
      m.name = inst.path.split('/').pop(); m.userData.instanceId = inst.id; m.userData.partKey = inst.part; m.userData.pending = true;
      if (inst.bbox) {                                                // box = recorded world bbox, so the placeholder sits where the part will be
        const [lo, hi] = inst.bbox; m.matrixAutoUpdate = true;
        m.position.set((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2); m.scale.set(Math.max(hi[0] - lo[0], 1e-4), Math.max(hi[1] - lo[1], 1e-4), Math.max(hi[2] - lo[2], 1e-4));
        m.userData.boxPose = true;
      } else { m.matrixAutoUpdate = false; m.matrix.fromArray(inst.matrix); }
      m.userData.matrix = inst.matrix; m.userData.hiddenBySw = inst.hidden; m.visible = !inst.hidden;
      group.add(m); (byPart.get(inst.part) || byPart.set(inst.part, []).get(inst.part)).push(m);
    }
    for (const k of byPart.keys()) partState.set(k, 'pending');
    hooks.showGroup(group); panel();
  }
  function markFailed(key, reason) {
    partState.set(key, 'failed'); st.failed.push({ key, reason });
    for (const m of byPart.get(key) || []) { m.material = redMat(); m.userData.failed = reason; failedMeshes.add(m); m.visible = showUnresolved && !m.userData.hiddenBySw; }
    panel();
  }
  async function onPartDone(d) {
    queue.push(async () => {
      try {
        const buf = await client.fetchGlb(d.url); const gltf = await new Promise((ok, no) => loader.parse(buf, '', ok, no));
        let src = null; gltf.scene.traverse((o) => { if (o.isMesh && !src) src = o; });
        if (!src) throw new Error('empty part glb');
        const edges = new THREE.EdgesGeometry(src.geometry, 15);       // one shared edge set per part (not per instance)
        for (const m of byPart.get(d.key) || []) {
          m.geometry = src.geometry; m.material = src.material; m.userData.colorMaterial = src.material; m.userData.pending = false;
          m.matrixAutoUpdate = false; m.position.set(0, 0, 0); m.scale.set(1, 1, 1); m.matrix.fromArray(m.userData.matrix); m.matrixWorldNeedsUpdate = true;
          for (const c of m.children.slice()) if (c.isLineSegments) { m.remove(c); }
          const ls = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: 0x000000 })); ls.visible = false; m.add(ls);
          hooks.refreshPart && hooks.refreshPart(m);
        }
        partState.set(d.key, 'done');
      } catch (e) { markFailed(d.key, 'load_error:' + (e.message || e)); return; }
      panel();
    });
    pump();
  }
  const done = new Promise((resolve) => {
    (async () => {
      try {
        try { await client.health(); } catch (e) { throw Object.assign(new Error('helper_unreachable'), { code: 'helper_unreachable' }); }
        panel(); hooks.onStatus && hooks.onStatus(`Uploading ${file.name}…`, 'busy');
        ({ job } = await client.upload(file, { onProgress: (l, t) => { st.loaded = l; st.total = t; panel(); } }));
        close = client.events(job, {
          plan: onPlan, part_done: onPartDone, part_failed: (d) => markFailed(d.key, d.reason),
          progress: (d) => { st.elapsed = d.elapsed_s; st.partsDone = d.parts_done; st.partsTotal = d.parts_total; st.ready = d.instances_ready; st.stalled = 0; panel(); },
          stalled: (s) => { st.stalled = s; panel(); }, mates: (d) => hooks.onMates && hooks.onMates(d),
          job_done: (d) => { st.finished = true; st.phase = d.cancelled ? 'cancelled' : 'done'; st.elapsed = d.seconds; panel(); resolve({ job, ...d }); },
        });
      } catch (e) { st.phase = 'error'; st.error = e.code || e.message; panel(); resolve({ error: st.error }); }
    })();
  });
  return {
    done, client, get job() { return job; },
    cancel() { st.cancelled = true; queue.length = 0; if (job) client.cancel(job); if (close) close(); st.phase = 'cancelled'; panel(); },
    keepPartial() { if (close) close(); if (job) client.cancel(job); st.phase = 'partial'; st.cancelled = true; panel(); },
    setShowUnresolved(v) { showUnresolved = !!v; for (const m of failedMeshes) m.visible = showUnresolved && !m.userData.hiddenBySw; for (const [k, ms] of byPart) if (partState.get(k) === 'pending') for (const m of ms) m.visible = showUnresolved && !m.userData.hiddenBySw; },
  };
}
