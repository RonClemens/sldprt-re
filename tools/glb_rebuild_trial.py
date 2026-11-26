#!/usr/bin/env python3
"""Try the drawing-to-solid pipeline on every part of a GLB: draw each part (make_drawing_from_glb), rebuild it from its drawing (rebuild_from_blocks), compare the rebuilt solid with the part's mesh.

  python3 tools/glb_rebuild_trial.py MODEL.glb OUT_DIR --fcpython /path/to/freecadcmd [--workers 3] [--parts BK-1501,BK-1402] [--md OUT.md]

Per part: size, the drawing's scale, the roles the zone reader found, whether the views agreed, holes cut, the solid's volume, and the volume overlap (intersection over union) with the mesh. The drawings are made by a script from the mesh, so this tests the
reader and the rebuild on other geometry, not on drawings made by people.
"""
import json, os, sys, time
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'src')); sys.path.insert(0, HERE)


def one(a):
    glb, node, out, fcp = a; r = dict(node=node)
    try:
        import make_drawing_from_glb as M, rebuild_from_blocks as R, numpy as np
        d = os.path.join(out, node.replace('<', '_').replace('>', '')); os.makedirs(d, exist_ok=True); pdf = os.path.join(d, 'part.pdf'); info = M.write_part_drawing(glb, node, pdf); r.update(size_mm=info['size_mm'], scale_n=info['scale_n'], triangles=info['triangles'])
        spec = R.build_spec(pdf, 1)
        if 'error' in spec: return dict(r, status='not rebuilt', why=spec['error'])
        r.update(views={k: v['id'] for k, v in spec['views'].items()}, warnings=[n for n in spec['not_shown'] if 'disagree' in n], holes=len(spec['holes']), slots=len(spec['slots']), rib=bool(spec['rib']), unit_guess=(spec.get('unit_guess') or {}).get('chosen'),
                 rebuilt_extents_mm=[round(spec['X'] * 25.4, 1), round(spec['Y'] * 25.4, 1), round(spec['D'] * 25.4, 1)])
        sp = os.path.join(d, 'spec.json'); json.dump(spec, open(sp, 'w'), default=float); step = os.path.join(d, 'part.step'); b = R.build_freecad(sp, step, fcp)
        if 'error' in b: return dict(r, status='FreeCAD failed', why=b['error'][:150])
        r['solid_in3'] = b['volume_in3']
        if b['volume_in3'] < 1e-6: return dict(r, status='empty solid')
        V, F = M.load_node(glb, node); c = R.compare_mesh(spec, step, V, F, d, fcp); r.update(extents_in=c['extents'], iou=c['volume']['iou'], only_in_rebuild=c['volume']['only_in_rebuild'], only_in_mesh=c['volume']['only_in_cad'])
        r['status'] = 'compared'
    except Exception as e: r.update(status='crashed', why=f'{type(e).__name__}: {str(e)[:150]}')
    return r


def main():
    a = sys.argv[1:]
    if len(a) < 2 or '--fcpython' not in a: raise SystemExit(__doc__)
    opt = lambda n, d=None: a[a.index(n) + 1] if n in a else d
    import json as J, struct
    d = open(a[0], 'rb').read(); jl = struct.unpack_from('<I', d, 12)[0]; js = J.loads(d[20:20 + jl]); names = [n['name'] for n in js['nodes'] if 'mesh' in n]
    seen = set(); parts = []
    for n in names:
        base = n.split('<')[0]
        if base in seen: continue
        seen.add(base); parts.append(n)
    if opt('--parts'): parts = [p for p in parts if p.split('<')[0] in opt('--parts').split(',')]
    os.makedirs(a[1], exist_ok=True)
    with Pool(int(opt('--workers', 3))) as pool: res = pool.map(one, [(a[0], n, a[1], opt('--fcpython')) for n in parts], chunksize=1)
    json.dump(res, open(os.path.join(a[1], 'trial.json'), 'w'), indent=1, default=float)
    L = ['# Drawing-to-solid trial on the parts of ' + os.path.basename(a[0]), '', '| part | size (mm) | roles found | status | IoU | holes | warnings |', '|---|---|---|---|---|---|---|']
    for r in res: L.append(f"| {r['node']} | {r.get('size_mm', '')} | {', '.join(sorted(r.get('views', {})))} | {r['status']}{(': ' + r['why']) if r.get('why') else ''} | {r.get('iou', '')} | {r.get('holes', '')} | {'; '.join(r.get('warnings', []))[:120]} |")
    open(opt('--md', os.path.join(a[1], 'trial.md')), 'w').write('\n'.join(L) + '\n')
    comp = [r for r in res if r['status'] == 'compared']; print(json.dumps(dict(parts=len(res), compared=len(comp), mean_iou=round(sum(r['iou'] for r in comp) / len(comp), 4) if comp else None, status={s: sum(1 for r in res if r['status'] == s) for s in {r['status'] for r in res}})))


if __name__ == '__main__':
    main()
