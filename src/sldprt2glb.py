#!/usr/bin/env python3
"""SLDPRT (SolidWorks 2015+) -> GLB/OBJ without SolidWorks.
container (chunked, raw-deflate) -> Parasolid XT transmit -> B-rep -> tessellation -> glTF/OBJ.
usage: sldprt2glb.py part.SLDPRT --schema sch_13006.s_t -o outdir"""
import argparse, os, re, struct, zlib, sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from sldcont import read_streams
from brep import Brep
import tess
from export import build_mesh, write_obj, write_glb
MAGIC=bytes.fromhex('231dd571da8148a2a85898b21b89ef99')
def geometry_transmits(streams):
    """yield (stream_name, section_idx, transmit bytes) for geometry streams.
    *-Partition: [u32 len][section]... back to back. */LocalBodies (imported / multi-body parts): MFC-framed, so the same
    sections are found by scanning for the section magic instead."""
    for name,data in streams.items():
        if name.endswith('/LocalBodies'):
            for k,m in enumerate(re.finditer(re.escape(MAGIC),data)):
                p=m.start()
                if p+24>len(data): continue
                cs=struct.unpack_from('<I',data,p+20)[0]
                try: yield name,k,zlib.decompress(data[p+24:p+24+cs])
                except Exception: continue
            continue
        if not name.endswith('-Partition'): continue
        pos=0;k=0
        while pos+4<=len(data):
            ln=struct.unpack_from('<I',data,pos)[0]; sec=data[pos+4:pos+4+ln]
            if sec[:16]!=MAGIC: break
            try: raw=zlib.decompress(sec[24:])
            except Exception: break
            yield name,k,raw; pos+=4+ln;k+=1
def fallback_transmits(streams):
    """last resort for parts whose Partition/LocalBodies hold no body (e.g. components imported from STEP): the feature history
    in *-ResolvedFeatures stores complete body snapshots. Only used when nothing else yields a body with faces."""
    for name,data in streams.items():
        if 'ResolvedFeatures' not in name: continue
        for k,m in enumerate(re.finditer(re.escape(MAGIC),data)):
            p=m.start()
            if p+24>len(data): continue
            cs=struct.unpack_from('<I',data,p+20)[0]
            try: raw=zlib.decompress(data[p+24:p+24+cs])
            except Exception: continue
            if raw[:2]==b'PS': yield name,k,raw
def is_ole2(path):
    with open(path,'rb') as f: return f.read(4)==b'\xd0\xcf\x11\xe0'
CACHE_VERSION='3'    # bump when tessellation output changes (new surface types, boundary fixes) so stale cached meshes are not reused
def _cache_file(path,bbox):
    d=os.environ.get('SLDPRT_CACHE')
    if not d: return None
    import hashlib
    from brep import TOL
    h=hashlib.sha1(open(path,'rb').read()+repr(bbox).encode()+repr(TOL).encode()+CACHE_VERSION.encode()).hexdigest()    # tolerance is part of the key: a coarse and a fine mesh of one part must not collide
    os.makedirs(d,exist_ok=True); return os.path.join(d,h+'.npz')
def load_part(path,schema,log=lambda *a:None,bbox=None):
    """SLDPRT -> (P,N,I,color,fails) in part coordinates (meters). Set env SLDPRT_CACHE=dir to cache results on disk."""
    cf=_cache_file(path,bbox)
    if cf and os.path.exists(cf):
        z=np.load(cf,allow_pickle=False)
        return z['P'],z['N'],z['I'],tuple(z['color']),[tuple(x) for x in z['fails'].tolist()]
    r=_load_part(path,schema,log,bbox)
    if cf:
        P,N,I,color,fails=r
        tmp=cf+'.%d.tmp.npz'%os.getpid()
        np.savez(tmp,P=P,N=N,I=I,color=np.array(color),fails=np.array([[str(a) for a in f] for f in fails],dtype=str).reshape(-1,2))
        os.replace(tmp,cf)
    return r
def _load_part(path,schema,log,bbox):
    if is_ole2(path): raise ValueError('pre-2015 OLE2 SolidWorks file (unsupported)')
    st=read_streams(path); best=None
    for name,k,raw in geometry_transmits(st):
        if b'TRANSMIT FILE (deltas)' in raw[:80]: continue
        try: br=Brep(raw,schema)
        except Exception as e: log('skip',name,k,repr(e)); continue
        nf=sum(1 for n in br.N.values() if n['name']=='FACE')
        if nf and (best is None or nf>best[0]): best=(nf,br)
    if best is None:
        for name,k,raw in fallback_transmits(st):
            if b'TRANSMIT FILE (deltas)' in raw[:80]: continue
            try: br=Brep(raw,schema)
            except Exception as e: log('skip',name,k,repr(e)); continue
            nf=sum(1 for n in br.N.values() if n['name']=='FACE')
            if nf and (best is None or nf>best[0]): best=(nf,br)
    if best is None: raise ValueError('no decodable Parasolid body with faces found')
    best[1].bbox=bbox
    r=build_mesh(best[1],tess.tess_face,log=log)
    if getattr(best[1],'truncated',False): r[4].append(('truncated','body cut off at 1 MiB in the file; geometry incomplete'))    # surfaced through the failed-face count
    return r
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('sldprt'); ap.add_argument('--schema',required=True); ap.add_argument('-o','--out',default='out')
    a=ap.parse_args(); os.makedirs(a.out,exist_ok=True)
    st=read_streams(a.sldprt); print(len(st),'streams')
    best=None
    for name,k,raw in geometry_transmits(st):
        if b'TRANSMIT FILE (deltas)' in raw[:80]: continue
        try: br=Brep(raw,a.schema)
        except Exception as e: print('skip',name,k,repr(e)); continue
        nf=sum(1 for n in br.N.values() if n['name']=='FACE'); print(name,'section',k,'nodes',len(br.N),'faces',nf)
        if best is None or nf>best[0]: best=(nf,br,name)
    nf,br,name=best; print('using',name,'with',nf,'faces')
    P,Nm,I,color,fails=build_mesh(br,tess.tess_face,log=lambda *a:print(*a,flush=True))
    base=os.path.splitext(os.path.basename(a.sldprt))[0]
    write_glb(os.path.join(a.out,base+'.glb'),P,Nm,I,color,base); write_obj(os.path.join(a.out,base+'.obj'),P,Nm,I,1000.0,base)
    vol=(P[I[:,0]]*np.cross(P[I[:,1]],P[I[:,2]])).sum()/6
    print('verts',len(P),'tris',len(I),'volume mm3 %.3f'%(vol*1e9),'failed faces',fails)
if __name__=='__main__': main()
