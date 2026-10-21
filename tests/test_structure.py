"""usage: python3 tests/test_structure.py  -- builds a tiny SolidWorks-style package (assemblies with a component tree, parts with properties,
drawing files) and checks the reconciled structure that src/structure.py makes of it"""
import os, struct, sys, tempfile, zlib, json, csv, io
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import structure
M = bytes.fromhex('140006000800')
def chunk(name, data):
    nm = name.encode(); co = zlib.compressobj(wbits=-15); comp = co.compress(data) + co.flush()
    return M + b'\x00' * 4 + struct.pack('<IIII', 0, len(comp), len(data), len(nm)) + nm + comp
def container(streams): return b'\x00' * 7 + b'\x00' + b'\x00' * 8 + b''.join(chunk(k, v) for k, v in streams.items())
NS = 'http://www.solidworks.com/sw2003/schema'
ID = ' '.join('1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1'.split())
def tree(files, models):
    fx = ''.join('<swFile id="%s" swPath="C:\\x\\%s" swDocType="%s"/>' % (i, p, 'ASSEMBLY' if p.upper().endswith('SLDASM') else 'PART') for i, p in files.items())
    mx = ''
    for mid, (fref, cfg, refs) in models.items():
        rx = ''.join('<swReference swName="%s" swReferenceNumber="%d" swConfigurationName="%s" swSuppressed="NO" swHidden="NO" swModelRef="%s" swTransform="%s"/>' % (n, k, c, mr, ID) for k, (n, c, mr) in enumerate(refs, 1))
        mx += '<swModel id="%s" swName="%s" swFileRef="%s" swConfigurationName="%s" swBoundingBox="0 0 0 0.1 0.2 0.3">%s</swModel>' % (mid, files[fref], fref, cfg, rx)
    return ('<?xml version="1.0"?><swDoc xmlns="%s">%s%s</swDoc>' % (NS, fx, mx)).encode()
def prop_xml(items):
    body = ''.join('<property name="%s" pid="%d"><vt:lpstr>%s</vt:lpstr></property>' % (n, i + 2, v) for i, (n, v) in enumerate(items))
    return ('<?xml version="1.0"?><Properties xmlns="urn:x" xmlns:vt="urn:vt"><propertySection xmlns="" name="UserDefinedProperties"><property name="" pid="1"><vt:i2>65001</vt:i2></property>%s</propertySection></Properties>' % body).encode()
def info(name): return prop_xml([('SW-File Name', name), ('SW-BOM Part Number', name)])
def part(name, custom=(), cfgs=None):
    st = {'docProps/custom.xml': prop_xml(custom), 'docProps/ISolidWorksInformation.xml': info(name),
          'swXmlContents/KeyWords': b'<Keywords><Configuration id="0" Name="Default"/><Configuration id="1" Name="Alt"/></Keywords>'}
    for i, c in (cfgs or {}).items(): st['docProps/Config-%d-Properties.xml' % i] = prop_xml(c)
    return container(st)
def asm(name, files, models): return container({'swXmlContents/COMPINSTANCETREE': tree(files, models), 'docProps/ISolidWorksInformation.xml': info(name)})

d = tempfile.mkdtemp()
def w(n, b): open(os.path.join(d, n), 'wb').write(b)
# TOP = SUB x2 + P1 + P2 (alt config) + a purchased screw; SUB = P3
files = {'f1': 'TOP.SLDASM', 'f2': 'SUB-100.SLDASM', 'f3': 'P1.SLDPRT', 'f4': 'P2.SLDPRT', 'f5': 'P3.SLDPRT', 'f6': '91251A621_SOCKET_HEAD_CAP_SCREW.SLDPRT'}
models = {'m1': ('f1', 'Default', [('SUB-100', 'Default', 'm2'), ('SUB-100', 'Default', 'm2'), ('P1', 'Default', 'm3'), ('P2', 'Alt', 'm4'), ('SCREW', 'Default', 'm6')]),
          'm2': ('f2', 'Default', [('P3', 'Default', 'm5')]),
          'm3': ('f3', 'Default', []), 'm4': ('f4', 'Alt', []), 'm5': ('f5', 'Default', []), 'm6': ('f6', 'Default', [])}
w('TOP.SLDASM', asm('TOP', files, models))
w('SUB-100.SLDASM', asm('SUB-100', {'f2': 'SUB-100.SLDASM', 'f5': 'P3.SLDPRT'}, {'m2': ('f2', 'Default', [('P3', 'Default', 'm5')]), 'm5': ('f5', 'Default', [])}))
w('P1.SLDPRT', part('P1', [('PartNo', '12-0001'), ('Description', '-'), ('Name', 'BRACKET'), ('Comments', 'SEE DWG 12-0001'), ('Material', '6061-T6')]))
w('P2.SLDPRT', part('P2', [('PartNo', 'P2-DEFAULT')], {1: [('PartNo', 'P2-ALT'), ('Description', 'SPACER, ALT')]}))
w('P3.SLDPRT', part('P3'))
w('91251A621_SOCKET_HEAD_CAP_SCREW.SLDPRT', part('91251A621_SOCKET_HEAD_CAP_SCREW', [('Vendor', 'McMaster-Carr'), ('Desc1', 'Black-Oxide'), ('Desc2', 'Socket Head Cap Screw')]))
w('SUB-100.SLDDRW', b'x'); w('12-0001.SLDDRW', b'x')           # only the names matter here
tdp = os.path.join(d, 'tdp.json')
json.dump({'drawings': [{'drawing': 'SUB-100', 'title': 'SUB ASSEMBLY', 'bom': [{'item': '1', 'qty': '1', 'text': 'THE P3 SEE DWG P3-DRAW'}]},
                        {'drawing': '12-0001', 'title': 'BRACKET DWG', 'bom': []}, {'drawing': 'ORPHAN-77', 'title': 'NOT IN CAD', 'bom': [{'item': '1', 'qty': '1', 'text': 'X'}]}]}, open(tdp, 'w'))

res, rows = structure.build(d, tdp_index=tdp)
by = {(r['level'], r['file']): r for r in rows}
top = rows[0]
assert top['file'] == 'TOP.SLDASM' and top['level'] == 0 and res['summary']['root'] == 'TOP.SLDASM', top
sub = by[(1, 'SUB-100.SLDASM')]
assert sub['qty'] == 2 and sub['drawing'] == 'SUB-100' and sub['drawingSource'] == 'file', sub           # two identical instances are one row of quantity 2, drawing by name
assert sum(1 for r in rows if r['file'] == 'P3.SLDPRT') == 1, [r['file'] for r in rows]                  # its subtree is listed once
p1 = by[(1, 'P1.SLDPRT')]
assert p1['partNumber'] == '12-0001' and p1['pnSource'] == 'file property' and p1['material'] == '6061-T6', p1
assert p1['description'] == 'BRACKET' and p1['drawing'] == '12-0001', p1                                   # '-' is no description; Name is used; the drawing named like the part number
p2 = by[(1, 'P2.SLDPRT')]
assert p2['partNumber'] == 'P2-ALT' and p2['description'] == 'SPACER, ALT' and p2['configuration'] == 'Alt', p2   # the configuration's own properties win
scr = by[(1, '91251A621_SOCKET_HEAD_CAP_SCREW.SLDPRT')]
assert scr['makeBuy'] == 'Buy' and scr['vendorPn'] == '91251A621' and scr['manufacturer'] == 'McMaster-Carr' and scr['description'] == 'Black-Oxide Socket Head Cap Screw', scr
assert scr['partNumber'] == '91251A621_SOCKET_HEAD_CAP_SCREW', scr                                       # the file name stays the part number: that is what the viewer matches
p3 = by[(2, 'P3.SLDPRT')]
assert p3['partNumber'] == 'P3' and p3['pnSource'] == 'file name' and p3['makeBuy'] == 'Make', p3
kinds = {x['kind'] for x in res['discrepancies']}
assert {'assembly_without_drawing', 'sub_assemblies_differ', 'tdp_drawing_not_matched'} <= kinds, res['discrepancies']
assert any(x['kind'] == 'tdp_drawing_not_matched' and x['drawing'] == 'ORPHAN-77' for x in res['discrepancies'])
sd = next(x for x in res['discrepancies'] if x['kind'] == 'sub_assemblies_differ'); assert sd['only_on_drawing'] == ['P3-DRAW'], sd
# file-name mode keeps the file name even where a property sets a number, and still reports the property's
res2, rows2 = structure.build(d, tdp_index=tdp, pn_from='file-name')
q = next(r for r in rows2 if r['file'] == 'P1.SLDPRT'); assert q['partNumber'] == 'P1' and q['propertyPn'] == '12-0001', q
# the CSV reads back with the viewer's columns first
text = structure.csv_text(rows); r0 = next(csv.DictReader(io.StringIO(text)))
assert list(r0)[:7] == ['Level', 'Part Number', 'Item No.', 'Description', 'Qty', 'Make/Buy', 'Envelope W x H x D (mm)'], list(r0)
assert top['envelope'] == '100 x 200 x 300', top['envelope']
print('ok')
