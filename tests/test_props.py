"""usage: python3 tests/test_props.py   -- builds SolidWorks-style containers with docProps streams and reads the properties back with src/props.py"""
import os, struct, sys, zlib, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import props
M = bytes.fromhex('140006000800')
def chunk(name, data):
    nm = name.encode(); co = zlib.compressobj(wbits=-15); comp = co.compress(data) + co.flush()
    return M + b'\x00' * 4 + struct.pack('<IIII', 0, len(comp), len(data), len(nm)) + nm + comp
def container(streams):
    return b'\x00' * 7 + b'\x00' + b'\x00' * 8 + b''.join(chunk(k, v) for k, v in streams.items())
def pxml(items, root='Properties'):
    body = ''.join('<property name="%s" pid="%d"><vt:%s>%s</vt:%s></property>' % (n, i + 2, t, v, t) for i, (n, t, v) in enumerate(items))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><%s xmlns="urn:x" xmlns:vt="urn:vt"><propertySection xmlns="" name="UserDefinedProperties" fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}">'
            '<property name="" pid="1"><vt:i2>65001</vt:i2></property>%s<propertyNameDictionaryElement name="" pid="0"></propertyNameDictionaryElement></propertySection></%s>' % (root, body, root)).encode()
keywords = '﻿<Keywords id="1" Name="P"><Configuration id="0" Name="Default" Type="ConfigurationManager"/><Configuration id="1" Name="Long &amp; Short" Type="ConfigurationManager"/></Keywords>'.encode()
info = lambda pn: pxml([('SW-File Name', 'lpstr', 'Bracket'), ('SW-Configuration Name', 'lpstr', 'Default'), ('SW-Title', 'lpstr', 'Mounting bracket'), ('SW-BOM Part Number', 'lpstr', pn)])

def read(streams):
    with tempfile.NamedTemporaryFile(suffix='.SLDPRT', delete=False) as f: f.write(container(streams)); p = f.name
    try: return props.read_properties(p)
    finally: os.remove(p)

# 1. nothing set: SolidWorks' computed BOM part number (the file name) is the identity
r = read({'docProps/custom.xml': pxml([]), 'docProps/ISolidWorksInformation.xml': info('Bracket'), 'swXmlContents/KeyWords': keywords})
assert r['partNumber'] == 'Bracket' and r['partNumberSource'] == 'bom' and r['custom'] == {}, r
assert r['sw']['configuration'] == 'Default' and r['description'] == 'Mounting bracket', r
# 2. a custom property wins over the computed one, whatever its spelling; description and revision are found the same way
r = read({'docProps/custom.xml': pxml([('Part Number', 'lpwstr', '12-3456-A'), ('Description', 'lpwstr', 'BRACKET, MOUNTING'), ('Revision', 'lpwstr', 'C'), ('Weight', 'r8', '1.5')]),
          'docProps/ISolidWorksInformation.xml': info('Bracket'), 'swXmlContents/KeyWords': keywords})
assert r['partNumber'] == '12-3456-A' and r['partNumberSource'] == 'custom:Part Number', r
assert r['description'] == 'BRACKET, MOUNTING' and r['revision'] == 'C' and r['custom']['Weight'] == '1.5', r
r = read({'docProps/custom.xml': pxml([('PartNo', 'lpstr', 'X-9')]), 'docProps/ISolidWorksInformation.xml': info('Bracket')})
assert r['partNumber'] == 'X-9' and r['partNumberSource'] == 'custom:PartNo', r
# 3. per-configuration properties are keyed by the configuration's name (from KeyWords)
r = read({'docProps/custom.xml': pxml([]), 'docProps/Config-0-Properties.xml': pxml([('Part Number', 'lpstr', 'B-100')], 'ConfigProperties'),
          'docProps/Config-1-Properties.xml': pxml([('Part Number', 'lpstr', 'B-101'), ('Description', 'lpstr', 'long')], 'ConfigProperties'),
          'docProps/ISolidWorksInformation.xml': info('Bracket'), 'swXmlContents/KeyWords': keywords})
assert r['configs']['Default']['partNumber'] == 'B-100' and r['configs']['Long & Short']['partNumber'] == 'B-101', r['configs']
assert r['configs']['Long & Short']['description'] == 'long' and r['partNumber'] == 'Bracket', r
# 4. an empty or missing stream, a bad XML, a file with no docProps at all
assert props.parse_props_xml(b'') == {} and props.parse_props_xml(b'not xml') == {} and props.parse_props_xml(b'junk\x00<?xml version="1.0"?><P><property name="a" pid="2"><vt:lpstr xmlns:vt="u">v</vt:lpstr></property></P>') == {'a': 'v'}
assert read({'Contents/Config-0': b'x'}) == {}
# 5. names are compared loosely and long values are cut
assert props._first({'part_number': '1'}, props.PN_NAMES)[0] == '1' and props._first({'Part #': '2'}, props.PN_NAMES)[0] == '' and props._first({'PART NUMBER': '3', 'number': '4'}, props.PN_NAMES)[0] == '3'
assert len(props.parse_props_xml(pxml([('Long', 'lpstr', 'x' * 1000)]))['Long']) == props.MAX_VALUE
print('ok')
