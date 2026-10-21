"""File properties of a SolidWorks part or assembly (SolidWorks 2015+ containers), read from the docProps streams:
  docProps/custom.xml                 the file's custom properties (UserDefinedProperties)
  docProps/Config-N-Properties.xml    the custom properties of configuration N (the usual home of a part number in a PDM setup)
  docProps/ISolidWorksInformation.xml SolidWorks' own computed properties, among them 'SW-BOM Part Number' (what the BOM shows: the
                                      document name unless the "BOM part number" option says otherwise), 'SW-Configuration Name',
                                      'SW-Title', 'SW-Subject', 'SW-Comments', 'SW-Author'
  swXmlContents/KeyWords              the configuration list: N -> name
Only reads; deciding what a part number means for a model is the viewer's job. Older OLE2 files carry none of these streams.
usage: python3 src/props.py FILE.SLDPRT [FILE ...]   (prints the identity record of each as JSON)"""
import re, json, html, sys
import xml.etree.ElementTree as ET
from sldcont import read_streams

# Property names (compared with everything but letters and digits removed, lower-case), strongest first.
PN_NAMES = ['partnumber', 'partno', 'partnum', 'pn', 'partid', 'documentnumber', 'docnumber', 'docno', 'number']
DESC_NAMES = ['description', 'desc', 'partdescription', 'title']
REV_NAMES = ['revision', 'rev', 'revlevel', 'revisionlevel']
MAX_PROPS, MAX_VALUE = 60, 240

def _key(name): return re.sub(r'[^a-z0-9]', '', name.lower())

def parse_props_xml(data):
    """{property name: value text} of one docProps XML stream: every <property name="..."> with a value child (vt:lpstr, vt:lpwstr, vt:i4 ...).
    The codepage entry (empty name) and empty values are left out."""
    if not data: return {}
    try: root = ET.fromstring(data.lstrip(b'\xef\xbb\xbf').strip(b'\x00'))
    except ET.ParseError:
        i = data.find(b'<?xml')                                  # a stream may start with stray bytes
        if i < 0: return {}
        try: root = ET.fromstring(data[i:])
        except ET.ParseError: return {}
    out = {}
    for el in root.iter():
        if el.tag.split('}')[-1] != 'property': continue
        name = (el.get('name') or '').strip()
        if not name: continue
        val = ''.join((c.text or '') for c in el if isinstance(c.tag, str)).strip()
        if val and name not in out: out[name] = val[:MAX_VALUE]
    return out

def config_names(keywords):
    """{N: configuration name} from swXmlContents/KeyWords."""
    if not keywords: return {}
    t = keywords.decode('utf-8', 'replace')
    return {int(i): html.unescape(n) for i, n in re.findall(r'<Configuration id="(\d+)" Name="([^"]*)"', t)}

def _first(props, names):
    """(value, original name) of the first property whose normalised name is in `names` (earlier names win)."""
    by = {}
    for n, v in props.items(): by.setdefault(_key(n), (v, n))
    for k in names:
        if k in by: return by[k]
    return ('', '')

def _identity(custom, sw):
    pn, nm = _first(custom, PN_NAMES); src = 'custom:' + nm if pn else ''
    if not pn and sw.get('SW-BOM Part Number'): pn, src = sw['SW-BOM Part Number'], 'bom'
    desc, dn = _first(custom, DESC_NAMES)
    if not desc and sw.get('SW-Title'): desc = sw['SW-Title']
    rev, _ = _first(custom, REV_NAMES)
    rec = {}
    if pn: rec['partNumber'], rec['partNumberSource'] = pn, src
    if desc: rec['description'] = desc
    if rev: rec['revision'] = rev
    return rec

def read_properties(path_or_streams):
    """The properties record of one file:
    {partNumber?, partNumberSource?, description?, revision?, custom:{...}, sw:{configuration, bomPartNumber, title, subject, comments, author},
     configs:{configuration name: {partNumber?, ..., custom:{...}}}}
    partNumberSource is 'custom:<property name>' when a property you set says it, 'bom' when it is SolidWorks' computed BOM part number
    (which equals the file name unless set otherwise). Returns {} when the file has no docProps streams."""
    st = path_or_streams if isinstance(path_or_streams, dict) else read_streams(path_or_streams, want=lambda n: n.startswith('docProps/') or n == 'swXmlContents/KeyWords')
    if not any(k.startswith('docProps/') for k in st): return {}
    custom = parse_props_xml(st.get('docProps/custom.xml'))
    info = parse_props_xml(st.get('docProps/ISolidWorksInformation.xml'))
    sw = {k: info[s] for k, s in (('configuration', 'SW-Configuration Name'), ('bomPartNumber', 'SW-BOM Part Number'), ('title', 'SW-Title'),
                                  ('subject', 'SW-Subject'), ('comments', 'SW-Comments'), ('author', 'SW-Author')) if info.get(s)}
    rec = _identity(custom, info)
    rec['custom'] = dict(list(custom.items())[:MAX_PROPS])
    if sw: rec['sw'] = sw
    names = config_names(st.get('swXmlContents/KeyWords'))
    cfgs = {}
    for k in st:
        m = re.fullmatch(r'docProps/Config-(\d+)-Properties\.xml', k)
        if not m: continue
        cp = parse_props_xml(st[k])
        if not cp: continue
        c = _identity(cp, {})
        c['custom'] = dict(list(cp.items())[:MAX_PROPS])
        cfgs[names.get(int(m.group(1)), 'Config-%s' % m.group(1))] = c
    if cfgs: rec['configs'] = cfgs
    return rec

if __name__ == '__main__':
    for f in sys.argv[1:]: print(f, json.dumps(read_properties(f), ensure_ascii=False))
