#!/usr/bin/env python3
"""SLDPRT (SolidWorks 2015+) -> GLB/OBJ without SolidWorks.
container (chunked, raw-deflate) -> Parasolid XT transmit -> B-rep -> tessellation -> glTF/OBJ.
usage: sldprt2glb.py part.SLDPRT --schema sch_13006.s_t -o outdir"""
import argparse, os, struct, zlib, sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from sldcont import read_streams
from brep import Brep
import tess
from export import build_mesh, write_obj, write_glb
MAGIC=bytes.fromhex('231dd571da8148a2a85898b21b89ef99')
def geometry_transmits(streams):
    """yield (stream_name, section_idx, transmit bytes) for geometry streams"""
    for name,data in streams.items():
        if not (name.endswith('-Partition') or name.endswith('/LocalBodies')): continue
        pos=0;k=0
        while pos+4<=len(data):
            ln=struct.unpack_from('<I',data,pos)[0]; sec=data[pos+4:pos+4+ln]
            if sec[:16]!=MAGIC: break
            try: raw=zlib.decompress(sec[24:])
            except Exception: break
            yield name,k,raw; pos+=4+ln;k+=1
def load_part(path,schema,log=lambda *a:None,bbox=None):
    """SLDPRT -> (P,N,I,color) in part coordinates (meters)"""
    st=read_streams(path); best=None
    for name,k,raw in geometry_transmits(st):
        if b'TRANSMIT FILE (deltas)' in raw[:80]: continue
        try: br=Brep(raw,schema)
        except Exception as e: log('skip',name,k,repr(e)); continue
        nf=sum(1 for n in br.N.values() if n['name']=='FACE')
        if best is None or nf>best[0]: best=(nf,br)
    best[1].bbox=bbox
    return build_mesh(best[1],tess.tess_face,log=log)
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
