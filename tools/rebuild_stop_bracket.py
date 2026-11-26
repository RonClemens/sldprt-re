#!/usr/bin/env python3
"""Worked example: a drawing with no CAD part, rebuilt zone by zone. The STOP BRACKET (Hydro package, drawing 27244633, page 41 of the TDP): an L8x6x0.5 steel angle 3 1/2 in long with four slots in the base,
two holes in the upright and a gusset plate.

  python3 tools/rebuild_stop_bracket.py TDP.pdf OUT_DIR --fcpython /path/to/freecadcmd [--page 41]

What is automatic: the zones and views (sheet_zones), the dimensions (pdf_drawing), the circles and slots in the linework (drawing_part.circles / slots), the scale of each view from its overall outline against the printed
overall dimension, and the check of each hole and slot position against the printed dimensions (stated / unconfirmed). What is my reading of the drawing and is written in this script: which view shows what (V1 the upright seen from outside,
V6 the base from above, V2 the side with the gusset, V5 the gusset's own profile), how the dimension chains are referenced (from the right edge, from the top edge), and that the gusset stands against the upright's inner face.
Check those against the sheet before using the result for anything; the weight check at the end is the independent test: the solid's volume times the density of ASTM A36 steel against the weight printed on the sheet.
Not modelled: the rounded outer corners of the angle, the inner corner radius, weld fillets, the thread of the 3/8-16 UNC holes. The callout diameters (5/16 in holes, 17/32 in slots) are not in the text the reader recovers (stacked fractions after the
diameter symbol): they are taken from the measured circles and slots and agree with the callouts as seen in the zoomed crops.
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'src'))
import numpy as np
import drawing_part as D
import sheet_zones as Z
from pdf_drawing import read_pdf

PAGE = 41; RHO_LB_IN3 = 0.2836                       # ASTM A36 steel, lb per cubic inch


def inch(pt, s): return pt / s


def run(pdf, out_dir, fcpython, page=PAGE):
    os.makedirs(out_dir, exist_ok=True)
    z = Z.read_zones(pdf, page); sheet = read_pdf(pdf, pages=[page]).sheets[0]; S, geo = D.read_segments(pdf, page); C = D.circles(S); SL = D.slots(S)
    dims = {d.id: d for d in sheet.dimensions}; vals = lambda ids: [dims[i].value for i in ids if i in dims]
    V = {v['id']: v for v in z['views']}
    notes = []; prov = []                                                           # parameter table: name, value (in), status, evidence
    def need(cond, msg):
        if not cond: raise SystemExit('cannot rebuild: ' + msg)
    # --- scale of each view: outline extent in points over the printed overall dimension
    def scale_of(v, overall_in, axis):
        b = v['box']; ext = (b[3] - b[1]) if axis == 'y' else (b[2] - b[0]); return ext / overall_in
    need('V1' in V and 'V6' in V and 'V2' in V and 'V5' in V, 'the zones did not give the four views V1, V2, V5, V6 this script reads')
    s1 = scale_of(V['V1'], 8.0, 'y'); s6 = scale_of(V['V6'], 6.0, 'x')
    need(abs(V['V1']['box'][2] - V['V1']['box'][0]) / s1 - 3.5 < 0.02, 'the upright view is not 8 x 3 1/2 at one scale')
    prov.append(dict(name='scale V1 (pt/in)', value=round(s1, 3), status='inferred from the printed 8 in against the drawn outline', evidence=f'width then {round((V["V1"]["box"][2] - V["V1"]["box"][0]) / s1, 3)} in (printed 3 1/2)'))
    prov.append(dict(name='scale V6 (pt/in)', value=round(s6, 3), status='inferred from the printed 6 in against the drawn outline', evidence=f'height then {round((V["V6"]["box"][3] - V["V6"]["box"][1]) / s6, 3)} in (printed 3 1/2)'))
    def printed(v, view, tol=None):
        """the printed dimension of that view (the zones assign each dimension to its nearest view) nearest to v within the measuring resolution: a point of drawing is 1/54 in"""
        tol = tol or max(1 / 64, 1.2 / s1); c = [(abs(dims[i].value - v), i) for i in V[view]['dims'] if i in dims and abs(dims[i].value - v) <= tol]; return min(c)[1] if c else None
    # --- upright holes (V1): circles
    b1 = V['V1']['box']; holes = [c for c in C if b1[0] <= c['cx'] <= b1[2] and b1[1] <= c['cy'] <= b1[3]]; need(len(holes) == 2, f'expected 2 holes in the upright view, found {len(holes)}')
    upright = []
    for k, c in enumerate(sorted(holes, key=lambda c: -c['cy']), 1):
        x_right = inch(b1[2] - c['cx'], s1); y_up = inch(c['cy'] - b1[1], s1); dia = inch(2 * c['r'], s1)
        used = []
        for nm, val in ((f'upright hole {k}: from the right edge', x_right), (f'upright hole {k}: height above the base underside', y_up), (f'upright hole {k}: diameter', dia)):
            pid = printed(val, 'V1'); used.append(float(dims[pid].value) if pid else float(val)); prov.append(dict(name=nm, value=round(used[-1], 4), measured=round(float(val), 4), status='stated' if pid else 'unconfirmed (measured)', evidence=f"printed {dims[pid].text.strip()} ({pid})" if pid else 'no printed value; the callout reads 5/16 in THRU in the crop'))
        upright.append(tuple(used))
    # --- base slots (V6)
    b6 = V['V6']['box']; sl = [x for x in SL if b6[0] <= x['cx'] <= b6[2] and b6[1] <= x['cy'] <= b6[3]]; need(len(sl) == 4, f'expected 4 slots in the base view, found {len(sl)}')
    base_slots = []
    for k, x in enumerate(sorted(sl, key=lambda x: (-x['cy'], x['cx'])), 1):
        xc = inch(b6[2] - x['cx'], s6); yc = inch(b6[3] - x['cy'], s6); w = inch(x['width'], s6); L = inch(x['length_between_centres'], s6)
        ends = (xc - L / 2, xc + L / 2); pe = [printed(e, 'V6') for e in ends]; py = printed(yc, 'V6')
        ue = [float(dims[i].value) if i else float(e) for i, e in zip(pe, ends)]; uy = float(dims[py].value) if py else float(yc)
        prov.append(dict(name=f'slot {k}: end centres from the right edge', value=[round(e, 4) for e in ue], measured=[round(float(e), 4) for e in ends], status='stated' if all(pe) else 'unconfirmed (measured)', evidence=', '.join(f'{dims[i].text.strip()} ({i})' for i in pe if i)))
        prov.append(dict(name=f'slot {k}: centre line from the top edge', value=round(uy, 4), measured=round(float(yc), 4), status='stated' if py else 'unconfirmed (measured)', evidence=f'{dims[py].text.strip()} ({py})' if py else ''))
        prov.append(dict(name=f'slot {k}: width', value=round(float(w), 4), status='unconfirmed (measured)', evidence='callout reads 17/32 in SLOT THRU 4 PLCS in the crop'))
        base_slots.append(((ue[0] + ue[1]) / 2, uy, float(w), ue[1] - ue[0]))
    # --- sizes from the BOM and the views
    ang_len = 3.5; leg_up = 8.0; leg_base = 6.0; t = 0.5
    prov += [dict(name='angle member', value='L8x6x0.5 x 3 1/2', status='stated (BOM item 2.5.1)', evidence='8 in upright, 6 in base, 1/2 in thick'), dict(name='gusset thickness', value=0.25, status='stated', evidence='printed 1/4"'),
             dict(name='gusset profile', value='6 tall against the upright, 3 wide at the foot, 1/2 wide at the top, 1/2 x 45 chamfer at the corner', status='stated', evidence='V5 printed 6", 3", 1/2", 1/2" X 45')]
    g_pos = 2.375                                                                    # the gusset strip's centre from the right edge in V1: 3/4 + 1 5/8
    prov.append(dict(name='gusset position along the member', value=g_pos, status='stated (chain 3/4 + 1 5/8)', evidence='V1 chain; mirrored end is not determined by the sheet'))
    json.dump(dict(provenance=prov, scales=dict(V1=s1, V6=s6)), open(os.path.join(out_dir, 'bracket_provenance.json'), 'w'), indent=1, default=float)
    spec = dict(L=ang_len, up=leg_up, base=leg_base, t=t, upright_holes=upright, slots=base_slots, gusset=dict(pos=g_pos, t=0.25, height=6.0, foot=3.0, top=0.5, chamfer=0.5))
    fc = r'''
import json, FreeCAD, Part
from FreeCAD import Vector as V
sp = json.load(open(%r)); IN = 25.4
L, UP, BASE, T = sp['L'] * IN, sp['up'] * IN, sp['base'] * IN, sp['t'] * IN
# X along the base (0 = free end, BASE = outer face of the upright), Y up, Z along the member (0..L, measured from the 'right edge' of the views)
prof = Part.Face(Part.makePolygon([V(0, 0, 0), V(BASE, 0, 0), V(BASE, UP, 0), V(BASE - T, UP, 0), V(BASE - T, T, 0), V(0, T, 0), V(0, 0, 0)]))
solid = prof.extrude(V(0, 0, L))
for xc, yc, w, ln in sp['slots']:                               # slots through the base: centre x measured from the upright's outer face, z from the top edge of the plan (= z = L - yc)
    x = BASE - xc * IN; z = L - yc * IN; r = w * IN / 2; ln_ = ln * IN
    cyl1 = Part.makeCylinder(r, T + 2, V(x - ln_ / 2, -1, z), V(0, 1, 0)); cyl2 = Part.makeCylinder(r, T + 2, V(x + ln_ / 2, -1, z), V(0, 1, 0)); box = Part.makeBox(ln_, T + 2, 2 * r, V(x - ln_ / 2, -1, z - r))
    solid = solid.cut(cyl1.fuse(cyl2).fuse(box))
for xr, yu, d in sp['upright_holes']:                           # holes through the upright: from the right edge (z = xr), height yu above the underside
    solid = solid.cut(Part.makeCylinder(d * IN / 2, T + 2, V(BASE - T - 1, yu * IN, xr * IN), V(1, 0, 0)))
g = sp['gusset']; gt = g['t'] * IN; z0 = g['pos'] * IN - gt / 2; xin = BASE - T
pts = [V(xin - g['foot'] * IN, T, z0), V(xin - g['chamfer'] * IN, T, z0), V(xin, T + g['chamfer'] * IN, z0), V(xin, T + g['height'] * IN - 0 * IN, z0), V(xin - g['top'] * IN, T + g['height'] * IN, z0)]
pts = [V(xin - g['foot'] * IN, T, z0), V(xin - g['chamfer'] * IN, T, z0), V(xin, T + g['chamfer'] * IN, z0), V(xin, T + g['height'] * IN, z0), V(xin - g['top'] * IN, T + g['height'] * IN, z0), V(xin - g['foot'] * IN, T, z0)]
gus = Part.Face(Part.makePolygon(pts)).extrude(V(0, 0, gt))
solid = solid.fuse(gus).removeSplitter()
doc = FreeCAD.newDocument('b'); o = doc.addObject('Part::Feature', 'bracket'); o.Shape = solid; doc.recompute(); Part.export([o], %r)
print(json.dumps(dict(volume_in3=round(solid.Volume / IN ** 3, 3), solids=len(solid.Solids), faces=len(solid.Faces), bbox_in=[round(solid.BoundBox.XLength / IN, 3), round(solid.BoundBox.YLength / IN, 3), round(solid.BoundBox.ZLength / IN, 3)])))
''' % (os.path.join(out_dir, 'bracket_spec.json'), os.path.join(out_dir, 'stop_bracket_from_drawing.step'))
    json.dump(spec, open(os.path.join(out_dir, 'bracket_spec.json'), 'w'), default=float)
    open(os.path.join(out_dir, '_build_bracket.py'), 'w').write(fc)
    import subprocess
    r = subprocess.run([fcpython, '-c', f"exec(open({os.path.join(out_dir, '_build_bracket.py')!r}).read())"], capture_output=True, text=True, timeout=300)
    line = next((l for l in (r.stdout + r.stderr).splitlines() if l.startswith('{') and 'volume_in3' in l), None)
    need(line, 'the FreeCAD build failed: ' + (r.stderr or r.stdout)[-300:])
    res = json.loads(line); w_text = next((n.text for n in sheet.notes if 'WEIGHT' in n.text.upper() and 'LBS' in n.text.upper()), ''); import re
    m = re.search(r'(\d+(?:\.\d+)?)\s*LBS', w_text.upper()); stated = float(m.group(1)) if m else None
    res.update(weight_lb=round(res['volume_in3'] * RHO_LB_IN3, 3), stated_weight_lb=stated, weight_diff_percent=round(100 * (res['volume_in3'] * RHO_LB_IN3 - stated) / stated, 2) if stated else None, density_lb_in3=RHO_LB_IN3)
    json.dump(res, open(os.path.join(out_dir, 'bracket_result.json'), 'w'), indent=1); return res, prov


def main():
    a = sys.argv[1:]
    if len(a) < 2 or '--fcpython' not in a: raise SystemExit(__doc__)
    res, prov = run(a[0], a[1], a[a.index('--fcpython') + 1], int(a[a.index('--page') + 1]) if '--page' in a else PAGE)
    for p in prov: print(f"{p['name']:58s} {str(p['value']):28s} {p['status']}")
    print(json.dumps(res))


if __name__ == '__main__':
    main()
