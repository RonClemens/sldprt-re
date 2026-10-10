#!/usr/bin/env python3
"""SLDASM (SolidWorks 2015+ container) -> GLB with the real assembly hierarchy.
The component tree (files, configurations, suppressed/hidden flags, 4x4 placements) is stored as XML in the
'swXmlContents/COMPINSTANCETREE' stream. Parts are converted with the SLDPRT pipeline.
usage: sldasm2glb.py assembly.SLDASM --schema sch_13006.s_t -o outdir [--include-hidden]"""
import argparse, os, sys, json
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import xml.etree.ElementTree as ET
from sldcont import read_streams
from sldprt2glb import load_part
from export import write_glb_scene
NS='{http://www.solidworks.com/sw2003/schema}'
def parse_tree(xml_bytes):
    root=ET.fromstring(xml_bytes)
    files={f.get('id'):f.get('swPath').replace('\\','/').split('/')[-1] for f in root.iter(NS+'swFile')}
    models={}
    for m in root.iter(NS+'swModel'):
        refs=[]
        for r in m.findall(NS+'swReference'):
            refs.append(dict(name=r.get('swName'),num=int(r.get('swReferenceNumber')),cfg=r.get('swConfigurationName'),
                suppressed=r.get('swSuppressed')=='YES',hidden=r.get('swHidden')=='YES',model=r.get('swModelRef'),
                T=np.array([float(x) for x in r.get('swTransform').split()]).reshape(4,4)))
        bb=m.get('swBoundingBox')
        models[m.get('id')]=dict(id=m.get('id'),name=m.get('swName'),cfg=m.get('swConfigurationName'),file=files.get(m.get('swFileRef')),
                                 refs=refs,bbox=[float(x) for x in bb.split()] if bb else None)
    # the document itself = the swFile with swDocType ASSEMBLY that has a path ending like our input; pick model whose file is that assembly
    return files,models,root
def flatten_instances(models,root_id):
    """all part instances (including suppressed / hidden, flagged) with world 4x4 (row-vector convention)"""
    out=[]
    def rec(mid,W,path,flags):
        for r in models[mid]['refs']:
            ch=models[r['model']]; M=r['T']@W; nm=f"{r['name']}<{r['num']}>"
            fl=dict(suppressed=flags['suppressed'] or r['suppressed'],hidden=flags['hidden'] or r['hidden'])
            p=path+[nm]
            if ch['file'].upper().endswith('.SLDPRT'): out.append(dict(path='/'.join(p),part=ch['file'].rsplit('.',1)[0],M=M,cfg=r['cfg'],**fl))
            else:
                out.append(dict(path='/'.join(p),part=None,assembly=True,M=M,cfg=r['cfg'],**fl)); rec(r['model'],M,p,fl)
    rec(root_id,np.eye(4),[],dict(suppressed=False,hidden=False))
    return out
def find_root(models,sldasm_name):
    me=sldasm_name.lower()
    c=[m for m in models.values() if m['file'] and m['file'].lower()==me and m['refs']]
    return c[0] if c else max(models.values(),key=lambda m:len(m['refs']))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('sldasm'); ap.add_argument('--schema',required=True); ap.add_argument('-o','--out',default='out'); ap.add_argument('--include-hidden',action='store_true')
    a=ap.parse_args(); os.makedirs(a.out,exist_ok=True); folder=os.path.dirname(os.path.abspath(a.sldasm))
    st=read_streams(a.sldasm)
    if 'swXmlContents/COMPINSTANCETREE' not in st: sys.exit('no COMPINSTANCETREE stream (older OLE2 SolidWorks file?)')
    files,models,_=parse_tree(st['swXmlContents/COMPINSTANCETREE'])
    me=os.path.basename(a.sldasm).lower()
    root=[m for m in models.values() if m['file'] and m['file'].lower()==me and m['cfg']==models.get(m['id'])['cfg'] and m['refs']]
    root=root[0] if root else max(models.values(),key=lambda m:len(m['refs']))
    print('root model',root['name'],'cfg',root['cfg'],'refs',len(root['refs']))
    def find(fn):
        for f in os.listdir(folder):
            if f.lower()==fn.lower(): return os.path.join(folder,f)
    parts={}; report=[]
    def load(file,bbox):
        key=file.rsplit('.',1)[0]
        if key not in parts:
            path=find(file)
            if path is None: print('MISSING part file',file); parts[key]=None; return key
            P,N,I,c,fails=load_part(path,a.schema,bbox=tuple(map(tuple,np.array(bbox).reshape(2,3))) if bbox else None)
            vol=(P[I[:,0]]*np.cross(P[I[:,1]],P[I[:,2]])).sum()/6
            print(f'  part {key}: {len(P)} verts {len(I)} tris, volume {vol*1e9:.1f} mm3, failed faces {fails}')
            parts[key]=(P,N,I,c)
        return key
    def build(mid,depth=0):
        kids=[]
        for r in models[mid]['refs']:
            if r['suppressed']: print('  skip suppressed',r['name'],r['num']); continue
            if r['hidden'] and not a.include_hidden: print('  skip hidden',r['name'],r['num']); continue
            ch=models[r['model']]; nm=f"{r['name']}<{r['num']}>"
            node=dict(name=nm,matrix=r['T'],extras={'config':r['cfg'],'file':ch['file']})
            if ch['file'].upper().endswith('.SLDPRT'):
                key=load(ch['file'],ch['bbox'])
                if parts[key] is None: continue
                node['part']=key
            else: node['children']=build(r['model'],depth+1)
            kids.append(node)
        return kids
    tree=dict(name=root['name'],children=build(root['id']))
    try:   # optional: embed decoded joints (needs the MatesList stream)
        from sldasm_mates import build_report
        rep=build_report(a.sldasm,a.schema); tree['extras']={'joints':rep['joints'],'mates':[{k:m[k] for k in ('name','type','entities','params','limit_bounds_rad','check') if k in m} for m in rep['mates']]}
        print('embedded',len(rep['joints']),'joints and',len(rep['mates']),'mates in the GLB root node extras')
    except Exception as e: print('mates not embedded:',repr(e)[:120])
    parts={k:v for k,v in parts.items() if v is not None}
    base=os.path.splitext(os.path.basename(a.sldasm))[0]
    write_glb_scene(os.path.join(a.out,base+'.glb'),parts,tree,base)
    json.dump({'root':root['name'],'tree':tree},open(os.path.join(a.out,base+'_tree.json'),'w'),default=lambda o:o.tolist() if hasattr(o,'tolist') else str(o),indent=1)
    print('wrote',os.path.join(a.out,base+'.glb'))
if __name__=='__main__': main()
