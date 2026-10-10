"""Decoder for SolidWorks assembly mates ('Contents/Config-N-MatesList', an MFC-style serialized archive).
Recovered so far (validated on ORIGINAL_3_DOF_ARM, see NOTES.md):
  header: u32, u16 mate-count; each mate = class tag / name CString / entity references / parameters.
  strings: UTF-16 CString  = FF FE FF <len u8> <len*2 bytes>
  entity: <id-rep string>,<component path string>  (id-rep absent for reference-plane entities)
  id-rep strings 'moFromSktEnt3IntSurfIdRep_c,F,I,-1,0' / 'moFromSktEntSurfIdRep_c,F,I' / 'moEndFaceSurfIdRep_c,F,I,0'
     match the part's FACE_ID_2001 face attribute  [code, F, timestamp, 0, I, ...]
  Angle / LimitAngle: parameter 'D1' CString followed by the angle (radians); limit mates also hold a moDimTolerance_c
     with two doubles (upper, lower bound).
"""
import re, struct, math
import numpy as np
MATE_NAME=re.compile(r'^(Concentric|Coincident|Angle|LimitAngle|Parallel|Perpendicular|Distance|LimitDistance|Tangent|Lock|Width|Symmetric|Gear|Hinge|Slot|Path|Screw|Cam|Universal|Linear|Rack)\d+$')
def _cstrings(d):
    out=[]
    for m in re.finditer(rb'\xff\xfe\xff(.)',d,re.S):
        n=m.group(1)[0]; s=m.end(); e=s+2*n
        if n==0 or e>len(d): continue
        raw=d[s:e]
        if all(raw[i+1]==0 and (32<=raw[i]<127 or raw[i] in (0,10,13)) for i in range(0,len(raw),2)) and raw[:2]!=b'\0\0':
            out.append((m.start(),raw.decode('utf-16le').rstrip('\x00\n\r ')))
    return out
def _kind(name):
    for k in ('LimitAngle','LimitDistance','Concentric','Coincident','Angle','Parallel','Perpendicular','Distance','Tangent','Lock'):
        if name.startswith(k): return k.lower()
    return 'other'
def parse_mates(d):
    count=struct.unpack_from('<H',d,4)[0]
    cs=_cstrings(d)
    heads=[(o,s) for o,s in cs if MATE_NAME.match(s)]
    mates=[]
    for i,(o,name) in enumerate(heads):
        end=heads[i+1][0] if i+1<len(heads) else len(d)
        seg=[(so,s) for so,s in cs if o<so<end]
        idreps=[(so,s) for so,s in seg if s.startswith('mo') and ',' in s]
        comps=[(so,s) for so,s in seg if '@' in s and ':' not in s and ('-' in s)]
        ents=[dict(component=c,idrep=None,_off=so) for so,c in comps[:2]]
        mate=dict(name=name,type=_kind(name),offset=o,idreps=[r for _,r in idreps],entities=[{k:v for k,v in e.items() if k!='_off'} for e in ents])
        # parameters: 'D1' followed by [2.0, ..., value]
        for so,s in seg:
            if re.fullmatch(r'D\d+',s):
                p=so+4+2*len(s)
                ds=[(k,struct.unpack_from('<d',d,p+k)[0]) for k in range(0,40)]
                vals=[v for k,v in ds if math.isfinite(v) and 1e-3<abs(v)<100]
                mate.setdefault('params',{})[s]=vals[1] if len(vals)>1 and abs(vals[0]-2.0)<1e-9 else None
        if mate['type'] in ('limitangle','limitdistance') and mate.get('params'):
            # (upper, lower) doubles follow the D1 value; first consecutive pair with upper>lower, both within +-7
            so=[so for so,s_ in seg if s_=='D1'][0]; p0=so+4+2*2
            val_off=None
            for k in range(0,40):
                v=struct.unpack_from('<d',d,p0+k)[0]
                if math.isfinite(v) and abs(v-mate['params']['D1'])<1e-12: val_off=p0+k; break
            if val_off:
                for k in range(8,100):
                    a=struct.unpack_from('<d',d,val_off+k)[0]; b=struct.unpack_from('<d',d,val_off+k+8)[0]
                    if all(math.isfinite(x) and abs(x)<7 and abs(x)>1e-3 for x in (a,b)) and a-b>0.1 and abs(a-1)>1e-9 and abs(b-1)>1e-9:
                        mate['limit_bounds_rad']=[a,b]; break
        mates.append(mate)
    assert len(mates)==count,(len(mates),count)
    return mates
def parse_idrep(s):
    """'moFromSktEnt3IntSurfIdRep_c,58,5,-1,0, ' -> (class,[ints])"""
    p=[x.strip() for x in s.split(',') if x.strip()!='']
    return p[0],[int(x) for x in p[1:]]
def comp_path(c):
    """'Arm_link-1@ORIGINAL/Arm_brace-1@Arm_link' -> ['Arm_link<1>','Arm_brace<1>'] (instance names as used by flatten)"""
    out=[]
    for part in c.split('/'):
        nm=part.split('@')[0]; base,_,num=nm.rpartition('-'); out.append(f'{base}<{num}>')
    return out
