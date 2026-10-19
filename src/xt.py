import struct
from schema import load_schema
NULLF=bytes.fromhex('c2bc928f996e0000')
class R:
    def __init__(s,b,p=0): s.b=b; s.p=p
    def u8(s): v=s.b[s.p]; s.p+=1; return v
    def i16(s): v=struct.unpack_from('>h',s.b,s.p)[0]; s.p+=2; return v
    def ptr(s):
        """node index. 0..32767 = plain i16. A body with more than 32767 nodes writes index 32767+k (k>=1) as the negative
        i16 -k followed by a u16 'block' (1 = 32768..65535; further blocks assumed to continue: +32768 each)."""
        v=s.i16()
        if v<0:
            blk=struct.unpack_from('>H',s.b,s.p)[0]; s.p+=2
            return 32767-v+32768*(blk-1)
        return v
    def i32(s): v=struct.unpack_from('>i',s.b,s.p)[0]; s.p+=4; return v
    def f64(s): v=struct.unpack_from('>d',s.b,s.p)[0]; s.p+=8; return v
    def s8(s): n=s.u8(); v=s.b[s.p:s.p+n].decode('latin1'); s.p+=n; return v
    def s32(s): n=s.i32(); v=s.b[s.p:s.p+n].decode('latin1'); s.p+=n; return v
def read_val(r,code):
    if code=='u': return r.u8()
    if code=='c': return chr(r.u8())
    if code=='l': return bool(r.u8())
    if code=='n': return r.i16()
    if code=='w': return r.i16()  # utf16 unit
    if code=='d': return r.i32()
    if code=='p': return r.ptr()
    if code=='f': return r.f64()
    if code=='i': return (r.f64(),r.f64())
    if code in 'vh': return (r.f64(),r.f64(),r.f64())
    if code=='b': return tuple(r.f64() for _ in range(6))
    raise ValueError('code '+code)
def base_layout(t):
    return [dict(name=f['name'],code=f['code'],n=f['n']) for f in t['fields'] if f['transmitted']]
def parse(buf, base, partial=False):
    """partial=True: a truncated stream returns the nodes read so far (flagged 'truncated') instead of raising"""
    r=R(buf)
    assert buf[:2]==b'PS'; r.p=2
    banner=r.s32(); schema=r.s32()
    mx=r.i16(); mn=r.i16(); r.p+=2
    layouts={}   # type -> (name, variable, fields)
    nodes={}
    order=[]
    while True:
        start=r.p
        try: nt=r.i16()
        except Exception:
            if partial: return dict(banner=banner,schema=schema,nodes=nodes,order=order,truncated=True,end=start,layouts=layouts)
            raise
        if nt==1:
            return dict(banner=banner,schema=schema,nodes=nodes,order=order,tail=r.i16(),end=r.p,layouts=layouts)
        try:
            if nt not in layouts:
                fc=r.u8()
                if fc==255 and nt in base:
                    bt=base[nt]; layouts[nt]=(bt['name'],bool(bt['variable']),base_layout(bt))
                elif nt in base:
                    bt=base[nt]; bl=base_layout(bt); bi=0; fl=[]; var=bool(bt['variable'])
                    while True:
                        c=chr(r.u8())
                        if c=='Z': break
                        if c=='C': fl.append(bl[bi]); bi+=1
                        elif c=='D': bi+=1
                        elif c in 'IA':
                            fl.append(read_emb(r))
                        else: raise ValueError(f'bad delta {c!r} at {r.p-1} type {nt}')
                    layouts[nt]=(bt['name'],var,fl)
                else:
                    name=r.s8(); desc=r.s8()
                    fl=[read_emb(r) for _ in range(fc)]
                    var=any(f.get('var') for f in fl)
                    layouts[nt]=(name,var,fl)
            name,var,fl=layouts[nt]
            count=r.i32() if var else None
            nid=r.ptr()
            node=dict(type=nt,name=name,id=nid)
            for k,f in enumerate(fl):
                n=f['n']
                last=(k==len(fl)-1)
                if var and last:
                    if f['code']=='c': v=''.join(read_val(r,'c') for _ in range(count))
                    else: v=[read_val(r,f['code']) for _ in range(count)]
                elif n>1:
                    v=[read_val(r,f['code']) for _ in range(n)]
                else: v=read_val(r,f['code'])
                node[f['name']]=v
        except Exception:
            if partial: return dict(banner=banner,schema=schema,nodes=nodes,order=order,truncated=True,end=start,layouts=layouts)
            raise
        nodes[nid]=node; order.append(nid)
def read_emb(r):
    name=r.s8(); pc=r.i16(); n=r.i16()
    code='p' if pc!=0 else r.s8()
    var=False
    if n==2:
        var=bool(r.u8())
    return dict(name=name,code=code,n=(n-1 if n>2 else n),var=var,ptrclass=pc)
if __name__=='__main__':
    c,base=load_schema('/mnt/user-data/uploads/sch_13006_s_t.txt')
    buf=open('part_sec0.bin','rb').read()
    g=parse(buf,base)
    print(len(g['nodes']),g['end'],len(buf),g['tail'])
