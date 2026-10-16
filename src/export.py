import json, struct, numpy as np
from brep import TOL
def build_mesh(b, tess_face, log=print):
    N=b.N
    # body colour
    ids={n['id']:n['string'] for n in N.values() if n['name']=='ATT_DEF_ID'}
    defs={n['id']:ids.get(n['identifier'],'?') for n in N.values() if n['name']=='ATTRIB_DEF'}
    color=(0.75,0.75,0.78)
    for n in N.values():
        if n['name']=='ATTRIBUTE' and 'COLOUR' in defs.get(n['definition'],''):
            f=n['fields']; f=f[0] if isinstance(f,list) else f
            if f in N: color=tuple(N[f]['values'][:3])    # (the node can be missing when the body is cut off at 1 MiB)
    faces=[];fails=[]
    for fid,f in sorted(N.items()):
        if f['name']!='FACE': continue
        try:
            X,t,info=tess_face(b,fid); t=np.asarray(t,int).reshape(-1,3); X=np.asarray(X,float).reshape(-1,3)
            if len(t)==0:
                if len(X) and np.ptp(X,axis=0).max()<5*TOL: continue    # a sliver face thinner than the chord tolerance has nothing to draw
                raise ValueError('no triangles produced')
            bb=getattr(b,'bbox',None); outside=False
            if bb is not None:    # a face whose triangles leave the part's recorded bounding box was probably traced wrongly (long way round an arc, ...)
                lo,hi=np.array(bb[0]),np.array(bb[1]); mg=0.05*(hi-lo)+1e-3
                outside=bool((X.min(0)<lo-mg).any() or (X.max(0)>hi+mg).any())
        except Exception as e: fails.append((fid,repr(e))); continue
        faces.append(dict(fid=fid,X=X,t=np.array(t),sf=b.surf(f['surface']),f=f,mode=info['mode'],outside=outside))
        log('face',fid,N[f['surface']]['name'],info['mode'],'tris',len(t))
    nout=sum(1 for fc in faces if fc['outside'])
    if nout and nout<=0.5*len(faces):    # most faces agree with the recorded box: drop the few that leave it (stray geometry). If most do not, the box does not describe this part (other configuration, flexible part): keep everything
        fails.extend((fc['fid'],"ValueError('outside recorded bounding box')") for fc in faces if fc['outside']); faces=[fc for fc in faces if not fc['outside']]
    if not faces: raise ValueError('no face could be tessellated (%d failed; first: %s)'%(len(fails),fails[0][1] if fails else 'none'))
    # ---- consistent orientation: propagate across shared boundary edges (welded at 1 nm)
    key=lambda p:tuple(np.round(np.asarray(p)*1e9).astype(np.int64))
    for fc in faces:
        K=[key(p) for p in fc['X']]; de=set()
        for tri in fc['t']:
            for k in range(3): de.add((K[tri[k]],K[tri[(k+1)%3]]))
        fc['bnd']={(a,c) for (a,c) in de if (c,a) not in de}
    owner={}
    for i,fc in enumerate(faces):
        for (a,c) in fc['bnd']: owner.setdefault(frozenset((a,c)),[]).append((i,(a,c)))
    flip=[None]*len(faces)
    for s0 in range(len(faces)):
        if flip[s0] is not None: continue
        flip[s0]=False; stack=[s0]
        while stack:
            i=stack.pop()
            for (a,c) in faces[i]['bnd']:
                for j,(a2,c2) in owner[frozenset((a,c))]:
                    if j==i: continue
                    same=((a,c)==(a2,c2))
                    need=flip[i]^same            # neighbour must be flipped iff directions agree (after our own flip)
                    if flip[j] is None: flip[j]=need; stack.append(j)
                    elif flip[j]!=need: log('orientation conflict between faces',faces[i]['fid'],faces[j]['fid'])
    P=[];Nm=[];I=[];base=0
    for fc,fl in zip(faces,flip):
        t=fc['t'][:,[0,2,1]] if fl else fc['t']; fc['t']=t; fc['flip']=fl
    # global sign from signed volume
    Xall=np.vstack([fc['X'] for fc in faces]); off=0; Tall=[]
    for fc in faces: Tall.append(fc['t']+off); off+=len(fc['X'])
    Tall=np.vstack(Tall)
    vol=(Xall[Tall[:,0]]*np.cross(Xall[Tall[:,1]],Xall[Tall[:,2]])).sum()/6
    if vol<0:
        for fc in faces: fc['t']=fc['t'][:,[0,2,1]]
    for fc in faces:
        X,t,sf=fc['X'],fc['t'],fc['sf']
        tn=np.cross(X[t[:,1]]-X[t[:,0]],X[t[:,2]]-X[t[:,0]])
        if hasattr(sf,'inv'):
            uv=sf.inv(X); nrm=sf.normal_raw(uv[:,0],uv[:,1])
            if (np.einsum('ij,ij->i',nrm[t[:,0]],tn)).sum()<0: nrm=-nrm
        else:   # area-weighted vertex normals from the faces' own triangles
            nrm=np.zeros_like(X)
            for k in range(3): np.add.at(nrm,t[:,k],tn)
            nrm/=np.maximum(np.linalg.norm(nrm,axis=1,keepdims=True),1e-300)
        P.append(X); Nm.append(nrm); I.append(t+base); base+=len(X)
    P=np.vstack(P); Nm=np.vstack(Nm); I=np.vstack(I)
    return P,Nm,I,color,fails

def write_obj(path,P,Nm,I,scale=1.0,name='part'):
    with open(path,'w') as fh:
        fh.write(f'# SLDPRT -> OBJ (units: {"mm" if scale==1000 else "m"})\no {name}\n')
        for p in P*scale: fh.write('v %.9g %.9g %.9g\n'%tuple(p))
        for n in Nm: fh.write('vn %.6f %.6f %.6f\n'%tuple(n))
        for a,b,c in I+1: fh.write(f'f {a}//{a} {b}//{b} {c}//{c}\n')
def write_glb(path,P,Nm,I,color,name='part'):
    P=P.astype('<f4'); Nm=Nm.astype('<f4'); I=I.astype('<u4').ravel()
    pb,nb,ib=P.tobytes(),Nm.tobytes(),I.tobytes()
    blob=pb+nb+ib
    js={"asset":{"version":"2.0","generator":"sldprt-re (Parasolid XT -> glTF)"},
        "scene":0,"scenes":[{"nodes":[0]}],"nodes":[{"mesh":0,"name":name}],
        "meshes":[{"name":name,"primitives":[{"attributes":{"POSITION":0,"NORMAL":1},"indices":2,"material":0,"mode":4}]}],
        "materials":[{"name":"stainless","pbrMetallicRoughness":{"baseColorFactor":[*map(float,color),1.0],"metallicFactor":0.9,"roughnessFactor":0.35},"doubleSided":False}],
        "accessors":[{"bufferView":0,"componentType":5126,"count":len(P),"type":"VEC3","min":P.min(0).tolist(),"max":P.max(0).tolist()},
                     {"bufferView":1,"componentType":5126,"count":len(Nm),"type":"VEC3"},
                     {"bufferView":2,"componentType":5125,"count":len(I),"type":"SCALAR"}],
        "bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":len(pb),"target":34962},
                       {"buffer":0,"byteOffset":len(pb),"byteLength":len(nb),"target":34962},
                       {"buffer":0,"byteOffset":len(pb)+len(nb),"byteLength":len(ib),"target":34963}],
        "buffers":[{"byteLength":len(blob)}]}
    j=json.dumps(js,separators=(',',':')).encode(); j+=b' '*((4-len(j)%4)%4)
    blob+=b'\0'*((4-len(blob)%4)%4)
    with open(path,'wb') as fh:
        fh.write(struct.pack('<4sII',b'glTF',2,12+8+len(j)+8+len(blob)))
        fh.write(struct.pack('<I4s',len(j),b'JSON')+j); fh.write(struct.pack('<I4s',len(blob),b'BIN\0')+blob)

def write_glb_scene(path,parts,tree,name='assembly'):
    """parts: {key:(P,N,I,color)}; tree: {'name','children':[{'name','matrix'(4x4 row-vector, translation last row),'part':key|None,'children':[...]}]}
    One glTF mesh per unique part (instanced by nodes). The 4x4 row-major list of our row-vector matrix equals the
    column-major list glTF expects, so it is written as-is."""
    blobs=[];views=[];accs=[];meshes=[];mats=[];mesh_of={}
    def addbuf(b,target):
        off=sum(len(x) for x in blobs); pad=(4-len(b)%4)%4; blobs.append(b+b'\0'*pad)
        views.append({"buffer":0,"byteOffset":off,"byteLength":len(b),"target":target}); return len(views)-1
    for key,(P,Nm,I,color) in parts.items():
        P=P.astype('<f4'); Nm=Nm.astype('<f4'); I=I.astype('<u4').ravel()
        v0=addbuf(P.tobytes(),34962); v1=addbuf(Nm.tobytes(),34962); v2=addbuf(I.tobytes(),34963)
        a0=len(accs); accs.append({"bufferView":v0,"componentType":5126,"count":len(P),"type":"VEC3","min":P.min(0).tolist(),"max":P.max(0).tolist()})
        accs.append({"bufferView":v1,"componentType":5126,"count":len(Nm),"type":"VEC3"}); accs.append({"bufferView":v2,"componentType":5125,"count":len(I),"type":"SCALAR"})
        mats.append({"name":key,"pbrMetallicRoughness":{"baseColorFactor":[*map(float,color),1.0],"metallicFactor":0.6,"roughnessFactor":0.45}})
        meshes.append({"name":key,"primitives":[{"attributes":{"POSITION":a0,"NORMAL":a0+1},"indices":a0+2,"material":len(mats)-1,"mode":4}]}); mesh_of[key]=len(meshes)-1
    nodes=[]
    def add(n):
        node={"name":n['name']}
        if n.get('matrix') is not None: node["matrix"]=[float(x) for x in np.asarray(n['matrix']).reshape(-1)]
        if n.get('part') is not None: node["mesh"]=mesh_of[n['part']]
        if n.get('extras'): node["extras"]=n['extras']
        idx=len(nodes); nodes.append(node)
        ch=[add(c) for c in n.get('children',[])]
        if ch: nodes[idx]["children"]=ch
        return idx
    root=add(tree)
    blob=b''.join(blobs)
    js={"asset":{"version":"2.0","generator":"sldprt-re (SLDASM component tree + Parasolid XT -> glTF)"},"scene":0,"scenes":[{"nodes":[root]}],
        "nodes":nodes,"meshes":meshes,"materials":mats,"accessors":accs,"bufferViews":views,"buffers":[{"byteLength":len(blob)}]}
    j=json.dumps(js,separators=(',',':')).encode(); j+=b' '*((4-len(j)%4)%4)
    with open(path,'wb') as fh:
        fh.write(struct.pack('<4sII',b'glTF',2,12+8+len(j)+8+len(blob)))
        fh.write(struct.pack('<I4s',len(j),b'JSON')+j); fh.write(struct.pack('<I4s',len(blob),b'BIN\0')+blob)
