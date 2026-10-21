#!/usr/bin/env python3
"""Product structure of a SolidWorks package (a Pack-and-Go folder or zip of SLDASM / SLDPRT / SLDDRW), as one reconciled, indented BOM.
The CAD component tree is the skeleton; what is known about each node from other places is laid on it and every disagreement is reported:

  part number   what the viewer will call the part when it matches the BOM to the model: a part number the file's own properties set (props.py;
                the configuration's own first), else the file name. Other numbers (drawing, vendor) go in their own columns, never in this one.
  description   the Description property (a '-' placeholder counts as empty)  >  Name  >  Desc1 + Desc2 (McMaster style)  >  the drawing's title in a
                TDP index (tools/index-tdp.py in ipb_viz)  >  a name made from the file
  drawing       a .SLDDRW named like the model or like its part number, the 'SEE DWG n' comment property, a TDP drawing, then a TDP drawing number the
                file name starts with (marked inferred)
  Make / Buy    Buy when the file name starts with a vendor part number or a Vendor property is set, else Make

usage: structure.py PACKAGE_DIR_OR_ZIP -o out_prefix [--root FILE.SLDASM] [--tdp-index tdp-index.json] [--part-number-from properties|file-name]
writes out_prefix.csv (indented BOM the viewer opens as its 1D tree), out_prefix.json (tree, rows, discrepancies, summary) and prints a summary."""
import argparse, csv, io, json, os, re, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)

VENDOR_PN = re.compile(r'^(\d{4,5}[A-Z]\d{2,4})(?=[_\- .]|$)')
def norm(s):
    """drawing / file name comparison key: case, spaces and underscores folded, a trailing -ADS dropped"""
    return re.sub(r'[-_]ADS$', '', re.sub(r'[_\s]+', '-', os.path.splitext(os.path.basename(str(s)))[0].strip().upper()))
def title_of(stem):
    t = re.sub(r'[_\s]+', ' ', stem).strip()
    return t[:1].upper() + t[1:] if t else stem

def tree_instances(asm_path):
    """(root model, instances) of one assembly: instances = flatten_instances rows (path, part/assembly, file, cfg, hidden, suppressed)"""
    from sldcont import read_streams
    from sldasm2glb import parse_tree, flatten_instances
    st = read_streams(asm_path)
    if 'swXmlContents/COMPINSTANCETREE' not in st: raise ValueError('no component tree (older OLE2 file?)')
    _, models, _ = parse_tree(st['swXmlContents/COMPINSTANCETREE'])
    me = os.path.basename(asm_path).lower()
    root = [m for m in models.values() if m['file'] and m['file'].lower() == me and m['refs']]
    root = root[0] if root else max(models.values(), key=lambda m: len(m['refs']))
    return models, root, flatten_instances(models, root['id'])

def build_tree(root_model, instances, asm_name):
    """nested nodes from the flat instance list; identical siblings (same file and configuration) are merged into one node with a quantity"""
    top = dict(key=os.path.splitext(asm_name)[0], file=asm_name, assembly=True, qty=1, cfg=root_model.get('cfg'), children=[], bbox=root_model.get('bbox'))
    by_path = {'': top}
    for it in instances:
        parent = by_path.get('/'.join(it['path'].split('/')[:-1]))
        if parent is None: continue
        file = it['file'] if it.get('assembly') else it['part'] + '.SLDPRT'
        key = os.path.splitext(file)[0]
        twin = next((c for c in parent['children'] if c['file'].lower() == file.lower() and c.get('cfg') == it.get('cfg') and c['assembly'] == bool(it.get('assembly'))), None)
        if twin: twin['qty'] += 1; by_path[it['path']] = dict(children=[])        # a repeat: its own subtree is not expanded again (its instances land on a throwaway node)
        else:
            node = dict(key=key, file=file, assembly=bool(it.get('assembly')), qty=1, cfg=it.get('cfg'), children=[], hidden=bool(it['hidden']), suppressed=bool(it['suppressed']))
            parent['children'].append(node); by_path[it['path']] = node
    return top

def collect(node, out=None):
    out = [] if out is None else out
    out.append(node)
    for c in node['children']: collect(c, out)
    return out

PLACEHOLDER = {'', '-', '--', '---', 'n/a', 'na', 'none', '- none -', 'tbd', 'tbs'}
def real(v): return '' if str(v or '').strip().lower() in PLACEHOLDER else str(v).strip()
def loose(s): return re.sub(r'[^a-z0-9]', '', str(s or '').lower())
def prop(custom, *names):
    """first real value among the custom properties with one of these names (letters and digits compared, case folded)"""
    by = {}
    for k, v in custom.items(): by.setdefault(loose(k), v)
    for n in names:
        if real(by.get(loose(n))): return real(by[loose(n)])
    return ''
SEE = re.compile(r'SEE\s+(?:DRAWING|DWG)\s+([\w-]+)', re.I)

def explicit_pn(rec, key):
    """the part number a file's own properties set: the same rule the viewer applies (a custom property you set, or SolidWorks' computed BOM
    part number only when it differs from the file name), so a BOM row written with it matches the CAD part"""
    if not rec or not rec.get('partNumber'): return ''
    return rec['partNumber'] if str(rec.get('partNumberSource', '')).startswith('custom:') or loose(rec['partNumber']) != loose(key) else ''

def reconcile(top, files, drawings, tdp, pn_from='properties'):
    """lays part number / description / drawing / Make-Buy on every node and returns the discrepancy list"""
    from props import read_properties
    from serve import part_key
    disc = []
    dwg_by = {}
    for d in drawings: dwg_by.setdefault(norm(d), d)
    tdp_by = {}
    for d in (tdp or {}).get('drawings', []):
        k = norm(d['drawing'])
        if k not in tdp_by or len(d.get('bom') or []) > len(tdp_by[k].get('bom') or []): tdp_by[k] = d
    tdp_keys = sorted((k for k in tdp_by if len(k) >= 6), key=len, reverse=True)
    known = set(dwg_by) | set(tdp_by)
    prop_cache = {}
    def props_of(node):
        k = node['file'].lower()
        if k not in prop_cache:
            p = files.get(k)
            try: prop_cache[k] = read_properties(p) if p else {}
            except Exception: prop_cache[k] = {}
        return prop_cache[k]
    for n in collect(top):
        stem = n['key']; pr = props_of(n); cfg = n.get('cfg') or ''
        cfgrec = (pr.get('configs') or {}).get(cfg, {}) if pr else {}
        custom = {**(pr.get('custom') or {}), **(cfgrec.get('custom') or {})} if pr else {}     # the configuration's own properties win over the file's
        pn = explicit_pn(cfgrec, stem) or explicit_pn(pr, stem)
        n['propertyPn'] = pn
        n['partNumber'], n['pnSource'] = (pn, 'file property') if pn and pn_from == 'properties' else (part_key(n['file']), 'file name')
        # drawing: same-named .SLDDRW, one named by the part number, the 'SEE DWG n' comment, a TDP drawing, then a TDP number the name starts with
        dwg, dsrc, tdp_hit = '', '', None
        for cand, how in ((stem, 'file'), (pn, 'file (part number)')):
            if cand and norm(cand) in dwg_by: dwg, dsrc = dwg_by[norm(cand)], how; break
        if not dwg:
            m = SEE.search(prop(custom, 'Comments'))
            if m and norm(m.group(1)) in known: dwg, dsrc = dwg_by.get(norm(m.group(1))) or tdp_by[norm(m.group(1))]['drawing'], 'file comment'
        if not dwg and norm(stem) in tdp_by: dwg, dsrc = tdp_by[norm(stem)]['drawing'], 'tdp'
        if not dwg:
            k = norm(stem); pre = next((t for t in tdp_keys if k.startswith(t + '-')), None)
            if pre: dwg, dsrc = tdp_by[pre]['drawing'], 'tdp prefix (inferred)'
        if dwg: tdp_hit = tdp_by.get(norm(dwg))
        n['drawing'], n['drawingSource'] = (os.path.splitext(dwg)[0] if dwg else ''), dsrc
        m = VENDOR_PN.match(stem)
        n['vendorPn'] = prop(custom, 'PartNo', 'Part No') if m is None and False else (m.group(1) if m else '')
        n['manufacturer'] = prop(custom, 'Vendor', 'Manufacturer')
        n['material'] = prop(custom, 'Material')
        title = (tdp_hit or {}).get('title') or '' if dsrc in ('file', 'file (part number)', 'file comment', 'tdp') else ''
        vend = ' '.join(x for x in (prop(custom, 'Desc1'), prop(custom, 'Desc2')) if x)
        generic = ('Assembly ' if n['assembly'] else 'Part ') + stem if re.fullmatch(r'[\d\-_ .]*\d{5,}[\w\-.]*', stem) and not re.search(r'[A-Za-z]{4,}', stem) else title_of(stem)
        n['description'] = prop(custom, 'Description') or prop(custom, 'Name') or vend or (title.title() if title else '') or generic
        n['makeBuy'] = 'Buy' if (m or n['manufacturer']) else 'Make'
        n['tdp'] = tdp_hit
    # disagreements ---------------------------------------------------------------------------------------------------------------------
    nodes = collect(top)
    used_dwg = {norm(n['drawing']) for n in nodes if n['drawing'] and not n['drawingSource'].endswith('(inferred)')}
    for n in nodes:
        if not n['assembly']: continue
        if not n['drawing']: disc.append(dict(kind='assembly_without_drawing', part=n['partNumber'], file=n['file'])); continue
        t = n.get('tdp')
        if t and not n['drawingSource'].endswith('(inferred)'):
            printed = {norm(m.group(1)) for b in (t.get('bom') or []) for m in [SEE.search(b.get('text') or '')] if m}
            cad = {norm(c['drawing']) for c in n['children'] if c['assembly'] and c['drawing']}
            only_dwg, only_cad = sorted(printed - cad), sorted(cad - printed)
            if only_dwg or only_cad: disc.append(dict(kind='sub_assemblies_differ', part=n['partNumber'], drawing=n['drawing'], only_on_drawing=only_dwg, only_in_cad=only_cad))
    files_norm = {norm(n['file']) for n in nodes}
    for d in drawings:
        if norm(d) not in used_dwg and norm(d) not in files_norm: disc.append(dict(kind='drawing_without_model', drawing=os.path.splitext(d)[0]))
    for k, d in tdp_by.items():
        if k not in used_dwg and k not in files_norm and (d.get('bom') or []): disc.append(dict(kind='tdp_drawing_not_matched', drawing=d['drawing']))
    return disc

def rows_of(top):
    rows = []
    def visit(n, level, outline):
        bb = n.get('bbox')
        env = ' x '.join(str(round(abs(bb[i + 3] - bb[i]) * 1000)) for i in range(3)) if bb and len(bb) == 6 else ''
        rows.append(dict(level=level, partNumber=n['partNumber'], item=outline, description=n['description'], qty=n['qty'], makeBuy=n['makeBuy'], envelope=env,
                         drawing=n['drawing'], drawingSource=n['drawingSource'], vendorPn=n['vendorPn'], propertyPn=n['propertyPn'], manufacturer=n['manufacturer'], material=n['material'],
                         pnSource=n['pnSource'], configuration=n.get('cfg') or '', hidden=bool(n.get('hidden')), suppressed=bool(n.get('suppressed')), file=n['file']))
        for i, c in enumerate(n['children'], 1): visit(c, level + 1, f'{outline}.{i}' if outline else str(i))
    visit(top, 0, '')
    return rows

def csv_text(rows):
    out = io.StringIO(); w = csv.writer(out, lineterminator='\n')
    w.writerow(['Level', 'Part Number', 'Item No.', 'Description', 'Qty', 'Make/Buy', 'Envelope W x H x D (mm)', 'Manufacturer', 'Material', 'Vendor P/N', 'Property P/N', 'Drawing', 'Drawing source', 'Part number source', 'Configuration', 'File'])
    for r in rows: w.writerow([r['level'], r['partNumber'], r['item'], r['description'], r['qty'], r['makeBuy'], r['envelope'], r['manufacturer'], r['material'], r['vendorPn'], r['propertyPn'], r['drawing'], r['drawingSource'], r['pnSource'], r['configuration'], r['file']])
    return out.getvalue()

def prepare(src, work=None):
    """a folder holding the package: the folder itself, or a zip extracted (with its virtual components written out as parts)"""
    if os.path.isdir(src): return src
    from batch import safe_extract
    from virtualcomp import materialize
    work = work or tempfile.mkdtemp(prefix='structure-')
    safe_extract(src, work)
    try: materialize(work, log=lambda *a: None)
    except Exception: pass
    return work

def build(src, root=None, tdp_index=None, work=None, pn_from='properties'):
    from batch import walk, top_level
    d = prepare(src, work)
    files = {os.path.basename(p).lower(): p for ext in ('.sldprt', '.sldasm') for p in walk(d, ext)}
    drawings = [os.path.basename(p) for p in walk(d, '.slddrw')]
    asms = walk(d, '.sldasm')
    tops = top_level(asms)
    if root: rp = next((a for a in asms if os.path.basename(a).lower() == root.lower()), None)
    else: rp = None
    sized = []
    if not rp:
        for a in tops:
            try: sized.append((len(tree_instances(a)[2]), a))
            except Exception: pass
        rp = max(sized)[1] if sized else None
    if not rp: raise SystemExit('no readable top-level assembly')
    models, rootm, inst = tree_instances(rp)
    top = build_tree(rootm, inst, os.path.basename(rp))
    tdp = json.load(open(tdp_index)) if tdp_index else None
    disc = reconcile(top, files, drawings, tdp, pn_from)
    rows = rows_of(top)
    cnt = lambda k: sum(1 for x in disc if x['kind'] == k)
    summary = dict(root=os.path.basename(rp), top_level_assemblies=[os.path.basename(a) for a in tops], rows=len(rows), assemblies=sum(1 for n in collect(top) if n['assembly']),
                   with_drawing=sum(1 for r in rows if r['drawing']), drawings_in_package=len(drawings), discrepancies={k: cnt(k) for k in sorted({x['kind'] for x in disc})},
                   part_number_sources={s: sum(1 for r in rows if r['pnSource'] == s) for s in sorted({r['pnSource'] for r in rows})})
    return dict(summary=summary, rows=rows, discrepancies=disc), rows

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0]); ap.add_argument('package'); ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--root'); ap.add_argument('--tdp-index'); ap.add_argument('--work')
    ap.add_argument('--part-number-from', choices=['properties', 'file-name'], default='properties', help="'properties': a part number set in a file's own properties is the part number (the viewer matches parts by it when the helper sends the properties); 'file-name': always the file name, for a static converted model that carries no properties (the property number still gets its own column)")
    a = ap.parse_args()
    res, rows = build(a.package, a.root, a.tdp_index, a.work, a.part_number_from.replace('-name', '-name') if a.part_number_from == 'properties' else 'file-name')
    open(a.out + '.csv', 'w', newline='', encoding='utf-8').write(csv_text(rows))
    json.dump(res, open(a.out + '.json', 'w'), indent=1, ensure_ascii=False)
    print(json.dumps(res['summary'], indent=1))
if __name__ == '__main__': main()
