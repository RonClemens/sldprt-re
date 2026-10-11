"""Resolve decoded mates to world-space geometry and check them against the placed assembly."""
import os, re, sys, math
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from sldcont import read_streams
from sldprt2glb import geometry_transmits
from brep import Brep
from mates import parse_mates, parse_idrep, comp_path
from mates_bin import decode_entities, mate_value
from sldasm2glb import parse_tree, flatten_instances, find_root
def face_index(part_path,schema):
    """FACE_ID_2001 -> list of face geometry dicts (part coordinates)"""
    st=read_streams(part_path); raw=[r for n,k,r in geometry_transmits(st) if b'deltas' not in r[:80]][0]
    b=Brep(raw,schema); N=b.N
    ids={n['id']:n['string'] for n in N.values() if n['name']=='ATT_DEF_ID'}
    defs={n['id']:ids.get(n['identifier'],'?') for n in N.values() if n['name']=='ATTRIB_DEF'}
    idx={}
    for n in N.values():
        if n['name']=='ATTRIBUTE' and defs.get(n['definition'])=='FACE_ID_2001' and N[n['owner']]['name']=='FACE':
            vals=N[[p for p in n['fields'] if p!=1][0]]['values']
            f=N[n['owner']]; s=N[f['surface']]; g=dict(face=f['id'],kind=s['name'],id=vals)
            sg=(1 if f['sense']=='+' else -1)*(1 if s['sense']=='+' else -1)
            if s['name']=='CYLINDER': g.update(point=np.array(s['pvec']),dir=np.array(s['axis']),radius=s['radius'])
            elif s['name']=='PLANE': g.update(point=np.array(s['pvec']),dir=np.array(s['normal'])*sg)
            elif s['name']=='CONE': g.update(point=np.array(s['pvec']),dir=np.array(s['axis']),radius=s['radius'])
            idx.setdefault((vals[1],vals[4] if len(vals)>4 else None),[]).append(g)
    return idx
def comp_path_raw(c):
    """first path element of a component string without its '@assembly' part ('Front Plane', 'Pipe-1')"""
    return c.split('/')[0].split('@')[0]
def to_world(g,M):
    R=M[:3,:3]
    if 'point' not in g or 'dir' not in g: return dict(g)    # non-analytic faces (B-spline, ...) carry no axis/plane to place
    return dict(g,point=g['point']@R+M[3,:3],dir=g['dir']@R)
def line_dist(p1,d1,p2,d2):
    d1=d1/np.linalg.norm(d1); d2=d2/np.linalg.norm(d2); c=np.cross(d1,d2)
    if np.linalg.norm(c)<1e-9: return np.linalg.norm(np.cross(p2-p1,d1)),0.0
    return abs((p2-p1)@c)/np.linalg.norm(c), math.degrees(math.asin(min(1,np.linalg.norm(c))))
def _disambiguate(m):
    """An id-rep (feature id, index) can match several faces of a part; the first was always taken. When any entity has more than one
    candidate, try the combinations and keep the one that satisfies the mate -- only if exactly one does (otherwise keep the first and
    flag m['ambiguous_faces']). m['disambiguated'] marks mates whose faces were chosen by their own constraint (not an independent check)."""
    import itertools
    from mates_check import judge
    ents=[e for e in m['entities'] if e.get('_cands')]
    if len(m['entities'])!=2 or len(ents)!=2 or all(len(e['_cands'])==1 for e in ents): return
    if judge(m)[0]=='pass': return                      # the first candidates already hold: nothing to decide
    ok=[]
    for combo in itertools.product(*[range(len(e['_cands'])) for e in ents]):
        trial=dict(m,entities=[dict(e,face=dict(to_world(e['_cands'][i],e['_inst']['M']),part=e['_inst']['part'])) for e,i in zip(ents,combo)])
        if judge(trial)[0]=='pass': ok.append(combo)
    if len(ok)==1:
        for e,i in zip(ents,ok[0]): e['face']=dict(to_world(e['_cands'][i],e['_inst']['M']),part=e['_inst']['part'])
        m['disambiguated']=True
    else: m['ambiguous_faces']=len(ok)
def resolve(sldasm,folder,schema):
    st=read_streams(sldasm)
    files,models,_=parse_tree(st['swXmlContents/COMPINSTANCETREE']); root=find_root(models,os.path.basename(sldasm))
    inst=flatten_instances(models,root['id']); byname={i['path']:i for i in inst}
    mkey=[k for k in st if k.endswith('-MatesList')][0]
    mates=parse_mates(st[mkey]); cache={}
    def part_faces(part):
        if part not in cache:
            want=part.lower()+'.sldprt'; p=next((os.path.join(folder,f) for f in os.listdir(folder) if f.lower()==want),None)
            if p is None:    # not beside the assembly: search the extracted tree(s) (ZIP sub-folders, virtual components)
                for r_ in [r for r in os.environ.get('SLD_SEARCH_ROOT','').split(os.pathsep) if r]:
                    for d_,_,fs_ in os.walk(r_):
                        hit=next((f for f in fs_ if f.lower()==want),None)
                        if hit: p=os.path.join(d_,hit); break
                    if p: break
            try: cache[part]=face_index(p,schema) if p else {}    # part file unavailable: its mates stay unresolved instead of aborting the assembly
            except Exception: cache[part]={}
        return cache[part]
    offs=[m['offset'] for m in mates]+[len(st[mkey])]
    def flat_faces(part): return [g for gs in part_faces(part).values() for g in gs]
    for k,m in enumerate(mates):
        if m['entities'] or m['idreps']: continue
        # binary layout (no component-path / id-rep strings): see mates_bin.py
        try: ents=decode_entities(st[mkey],offs[k],offs[k+1],inst,flat_faces,prefer=('CYLINDER','CONE') if m['type']=='concentric' else ('PLANE',))
        except Exception as e: m['entities']=[]; m['layout']='binary'; m['error']=repr(e)[:100]; continue
        for e in ents:
            e['top']=e['instance'].split('/')[0]; e['is_assembly_level']=False
            if e['_face'] is not None: e['face']=to_world(e['_face'],e['_inst']['M']); e['face']['part']=e['_inst']['part']
            del e['_face']
        m['entities']=ents; m['layout']='binary'
        if m['type'] in ('distance','angle') : m.setdefault('params',{})['D1']=mate_value(st[mkey],offs[k],offs[k+1])
    for m in mates:
        if m.get('layout')=='binary': continue
        try:
            ents=m['entities']; reps=list(m['idreps'])
            for e in ents:
                path='/'.join(comp_path(e['component'])); e['instance']=path; e['top']=comp_path(e['component'])[0]
                inst_=byname.get(path)
                e['is_assembly_level']= inst_ is None   # a sub-assembly (its origin planes etc.)
                if inst_ is None and re.fullmatch(r'.+-\d+',comp_path_raw(e['component'])): e['dangling']=True; m['dangling']=True    # names a component that is not in the assembly's tree (leftover/replaced): cannot be resolved
                e['_inst']=inst_
            # assign id-reps to entities: first entity (in order) whose part owns a face with those ids
            used=set()
            for r in reps:
                try: cls,vals=parse_idrep(r); key=(vals[0],vals[1])
                except (ValueError,IndexError): m.setdefault('unparsed_idreps',[]).append(r[:60]); continue    # id-rep classes not decoded yet (e.g. moDerivedSurfIdRep_c)
                for k,e in enumerate(ents):
                    if k in used or e['_inst'] is None: continue
                    g=part_faces(e['_inst']['part']).get(key)
                    if g: e['idrep']=r; used.add(k); e['_cands']=g; e['face']=to_world(g[0],e['_inst']['M']); e['face']['part']=e['_inst']['part']; break
            _disambiguate(m)
        except Exception as e: m['entities']=[]; m['error']=repr(e)[:100]    # one odd mate must not abort the whole assembly
    return mates,inst,models,root
def check(mates):
    rows=[]
    for m in mates:
        f=[e.get('face') for e in m['entities']]
        if len(f)!=2: rows.append((m['name'],'entities not decoded (%d found)'%len(f))); continue
        if m['type']=='concentric' and all(f) and all('radius' in x for x in f):
            dist,ang=line_dist(f[0]['point'],f[0]['dir'],f[1]['point'],f[1]['dir'])
            rows.append((m['name'],'axes offset %.4f mm, misalignment %.4f deg, radii %.2f/%.2f mm'%(dist*1e3,ang,f[0]['radius']*1e3,f[1]['radius']*1e3)))
        elif m['type']=='coincident' and all(f) and f[0]['kind']==f[1]['kind']=='PLANE':
            n1=f[0]['dir']/np.linalg.norm(f[0]['dir']); n2=f[1]['dir']/np.linalg.norm(f[1]['dir'])
            dd=(f[1]['point']-f[0]['point'])@n1
            rows.append((m['name'],'planes %s (normals dot %+.4f), distance %.5f mm'%('aligned/parallel' if n1@n2>0 else 'opposed/anti-parallel',n1@n2,dd*1e3)))
        elif m['type']=='distance' and all(f) and f[0]['kind']==f[1]['kind']=='PLANE':
            n1=f[0]['dir']/np.linalg.norm(f[0]['dir']); n2=f[1]['dir']/np.linalg.norm(f[1]['dir'])
            dd=abs((f[1]['point']-f[0]['point'])@n1); D=(m.get('params') or {}).get('D1')
            rows.append((m['name'],'planes normals dot %+.4f, gap %.5f mm, stored D1 %s mm'%(n1@n2,dd*1e3,'%.5f'%(D*1e3) if D else '?')))
        else: rows.append((m['name'],'not geometrically checked (%s)'%('reference-plane entity' if m['type'] in('coincident','angle','limitangle') else m['type'])))
    return rows
if __name__=='__main__':
    ap=sys.argv; mates,inst,models,root=resolve(ap[1],os.path.dirname(os.path.abspath(ap[1])),ap[2])
    for n,r in check(mates): print(f'{n:13s} {r}')
