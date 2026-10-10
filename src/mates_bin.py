"""Mate entity decoding for the *binary* MatesList layout (seen in swVersion 11000 files: 4_WHEELER, 3_DOF_ARM).

In this layout a mate record carries no component-path or id-rep CStrings. Instead (validated on 4_WHEELER):
  * each face entity holds one or more binary id-reps: a class tag (e.g. moEndFace3IntSurfIdRep_c, or a 2-byte
    back-reference to it), then u32 feature id F, u32 timestamp, i32 I [, i32 a, i32 b] -- the same numbers as the
    part's FACE_ID_2001 attribute [code, F, timestamp, 0, I, a, b]. The FIRST id-rep of an entity is the mated face;
    later ones appear to be neighbouring faces (context) and are ignored.
  * after an entity's id-reps the record stores that component's placement as doubles: 3x3 rotation then translation,
    equal to the instance's swTransform. That placement identifies the component instance.
Matching on (F, timestamp) is strict (timestamps are per-feature creation times), so false positives are unlikely.
"""
import struct
import numpy as np

def _faces_by_ft(faces):
    """faces: list of face dicts with 'id' = [code, F, ts, 0, I, ...] -> {(F, ts): [faces]}"""
    out = {}
    for g in faces:
        v = g['id']
        if len(v) >= 5:
            out.setdefault((v[1] & 0xffffffff, v[2] & 0xffffffff), []).append(g)
    return out

def _find_placements(seg, base, instances, tol=1e-9):
    """absolute offsets where an instance's placement is stored: [rotation 9 doubles, only when not identity]
    translation 3 doubles, then scale 1.0. Matched on translation + the trailing 1.0 (and the rotation when present)."""
    hits = []
    n = len(seg)
    for al in range(8):
        cnt = (n - al) // 8
        if cnt < 4: continue
        D = np.frombuffer(seg, dtype='<f8', count=cnt, offset=al)
        with np.errstate(invalid='ignore'):
            for inst in instances:
                M = inst['M']; t = M[3, :3]; R = M[:3, :3].ravel()
                m = (np.abs(D[:-3] - t[0]) < tol) & (np.abs(D[1:-2] - t[1]) < tol) & (np.abs(D[2:-1] - t[2]) < tol) & (np.abs(D[3:] - 1.0) < 1e-12)
                for k in np.nonzero(m)[0]:
                    rot_ok = k >= 9 and np.all(np.isfinite(D[k - 9:k])) and np.allclose(D[k - 9:k], R, atol=1e-9)
                    ident = np.allclose(R, np.eye(3).ravel(), atol=1e-12)
                    if rot_ok or ident:
                        hits.append((base + al + 8 * int(k), inst))
    hits.sort(key=lambda h: h[0])
    return hits

def decode_entities(d, start, end, instances, part_faces, prefer=None):
    """Return the entity list for the mate record d[start:end].
    instances: flattened part instances (dict path, part, M); part_faces(part) -> list of face dicts."""
    seg = d[start:end]
    keys = {}
    for inst in instances:
        if inst.get('part'):
            for k, gs in _faces_by_ft(part_faces(inst['part'])).items():
                keys.setdefault(k, set()).add(inst['part'])
    reps = []
    for (F, ts), parts in keys.items():
        pat = struct.pack('<II', F, ts)
        p = seg.find(pat)
        while p >= 0:
            if p + 20 <= len(seg):
                I, a, b = struct.unpack_from('<iii', seg, p + 8)
                reps.append((start + p, F, ts, I, a, b, parts))
            p = seg.find(pat, p + 1)
    reps.sort(key=lambda r: r[0])
    places = _find_placements(seg, start, [i for i in instances if i.get('part')])
    ents = []
    for off, inst in places:
        # id-reps between the previous placement and this one belong to this entity
        prev = ents[-1]['_off'] if ents else start
        mine = [r for r in reps if prev <= r[0] < off and inst['part'] in r[6]]
        if not mine: continue
        # The first id-rep is the referenced face for face entities; an edge entity (moCircleRef_w / moCompEdge_c)
        # lists its two adjacent faces. So walk the id-reps in order and take the first face whose surface kind
        # suits the mate (cylinder/cone for Concentric, plane for Coincident/Distance/...); else the first face.
        ff = _faces_by_ft(part_faces(inst['part']))
        allc = []
        for r in mine:
            cands = [g for g in ff.get((r[1], r[2]), []) if g['id'][4] == r[3]]
            if len(cands) > 1:
                c2 = [g for g in cands if len(g['id']) > 5 and g['id'][5] == r[4]]
                cands = c2 or cands
            allc += [(r, g) for g in cands]
        pick = next(((r, g) for r, g in allc if prefer and g['kind'] in prefer), allc[0] if allc else (mine[0], None))
        r, g = pick
        ents.append(dict(component=inst['path'], instance=inst['path'], idrep='bin:%d,%d,%d' % (r[1], r[2], r[3]),
                         idrep_rank=mine.index(r), _off=off, _inst=inst, _face=g, n_idreps=len(mine)))
    return ents

def mate_value(d, start, end, name='D1'):
    """value of a Distance/Angle parameter: the double right after the parameter's CString (layout as in mates.py)."""
    import re, math
    pat = b'\xff\xfe\xff' + bytes([len(name)]) + name.encode('utf-16le')
    p = d.find(pat, start, end)
    if p < 0: return None
    q = p + len(pat)
    for k in range(0, 40):
        v = struct.unpack_from('<d', d, q + k)[0]
        if math.isfinite(v) and abs(v - 2.0) < 1e-12:
            for j in range(k + 8, k + 48):
                w = struct.unpack_from('<d', d, q + j)[0]
                if math.isfinite(w) and 1e-6 < abs(w) < 100: return w
    return None
