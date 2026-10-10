import numpy as np
from brep import Brep, TOL
from earclip import earclip, area2
TWO=2*np.pi
def unwrap1(x,P=TWO):
    x=np.array(x,float)
    for i in range(1,len(x)): x[i]-=P*np.round((x[i]-x[i-1])/P)
    d=x[0]-x[-1]; d-=P*np.round(d/P)
    return x,int(np.round((x[0]-x[-1]-d)/P))
def unwrap_loop(uv,P=TWO):
    u,w=unwrap1(uv[:,0],P); out=uv.copy(); out[:,0]=u; return out,w
def _cost(XYZ):
    def cost(a,b,c):
        A,B,C=XYZ[a],XYZ[b],XYZ[c]
        return max(np.linalg.norm(A-B),np.linalg.norm(B-C),np.linalg.norm(C-A))
    return cost
def _polygon_faces(loops2d,loops3,cost3=True):
    """ear-clip a face given parallel lists of 2D coords and 3D points per loop"""
    UV=[];XYZ=[];pools=[]
    for uv,L in zip(loops2d,loops3):
        base=len(UV); UV.extend(map(tuple,uv)); XYZ.extend(map(tuple,L)); pools.append(list(range(base,base+len(uv))))
    ar=[abs(area2(UV,l)) for l in pools]
    o=int(np.argmax(ar)); poly=pools[o]; holes=[l for k,l in enumerate(pools) if k!=o]
    UVa=np.array(UV); lo=UVa.min(0); span=np.maximum(UVa.max(0)-lo,1e-300); XYZa=np.array(XYZ)
    tris=np.array(earclip(((UVa-lo)/span).tolist(),poly,holes,_cost(XYZa)))
    return XYZa,tris
def _pip(Q,poly):
    """crossing-parity point-in-polygon, Q (m,2), poly (n,2)"""
    x,y=Q[:,0],Q[:,1]; inside=np.zeros(len(Q),bool)
    a=poly; b=np.roll(poly,-1,0)
    for (x1,y1),(x2,y2) in zip(a,b):
        if y1==y2: continue
        cond=((y1<=y)&(y<y2))|((y2<=y)&(y<y1))
        xi=x1+(y-y1)*(x2-x1)/(y2-y1)
        inside^=cond&(x<xi)
    return inside
def _dist_to_poly(Q,poly):
    a=poly; b=np.roll(poly,-1,0); d=np.full(len(Q),np.inf)
    for p,q in zip(a,b):
        pq=q-p; L=(pq*pq).sum(); t=np.clip(((Q-p)@pq)/max(L,1e-300),0,1)
        d=np.minimum(d,np.linalg.norm(Q-(p+t[:,None]*pq),axis=1))
    return d
def _strip(uvs,loops3,winds):
    """two loops each winding once around a periodic-u surface: cut at a seam and build one simple polygon"""
    ls=[]
    for uv,L,w in zip(uvs,loops3,winds):
        if w>0: uv=uv[::-1].copy(); L=L[::-1]   # unwrap1 winding is +1 for DEcreasing u
        ls.append((uv,L))
    ls.sort(key=lambda t:t[0][:,1].mean())
    u0=ls[0][0][0,0]; out=[]
    for uv,L in ls:
        um=np.mod(uv[:,0]-u0,TWO); k=int(np.argmin(um))
        uv=np.roll(uv,-k,0); L=np.roll(L,-k,0)
        u,_=unwrap1(uv[:,0]); u=u-u[0]+um[k]+u0
        uv=np.column_stack([u,uv[:,1]])
        uv=np.vstack([uv,[uv[0,0]+TWO,uv[0,1]]]); L=np.vstack([L,L[:1]])
        out.append((uv,L))
    (uA,LA),(uB,LB)=out
    return (uA,LA),(uB,LB)
def _strip_with_holes(sf,wuv,wL,wws,huv,hL):
    """cylinder face bounded by two loops winding once around the axis plus non-winding hole loops.
    The annulus is cut along a seam placed in the widest u-gap free of holes, giving one simple polygon in
    unwrapped (u,v); holes are shifted into the seam's 2pi window and the polygon is filled with an interior grid."""
    hu=[]
    for uv in huv:
        u,_=unwrap1(uv[:,0]); hu.append((np.mod(u.min(),TWO),u.max()-u.min()))
    # seam: middle of the largest circular gap between hole u-intervals
    iv=sorted(hu); best=(-1,0.0)
    for k,(a,w) in enumerate(iv):
        na=iv[(k+1)%len(iv)][0]+(TWO if k+1==len(iv) else 0.0); gap=na-(a+w)
        if gap>best[0]: best=(gap,a+w+gap/2)
    useam=np.mod(best[1],TWO)
    # rotate the loops so _strip's seam (first point of the lower loop) sits at useam
    lw=sorted(range(2),key=lambda k:wuv[k][:,1].mean()); A=lw[0]
    kA=int(np.argmin(np.abs(np.angle(np.exp(1j*(wuv[A][:,0]-useam))))))
    wuv=list(wuv); wL=list(wL); wuv[A]=np.roll(wuv[A],-kA,0); wL[A]=np.roll(wL[A],-kA,0)
    uu=[unwrap_loop(u)[0] for u in wuv]
    (uA,LA),(uB,LB)=_strip(uu,wL,wws)
    u0=uA[0,0]
    # polygon: lower loop ascending (u0 -> u0+2pi), upper loop descending, seams closing both ends
    poly=np.vstack([uA,uB[::-1]]); P3=np.vstack([LA,LB[::-1]])
    holes2=[];holes3=[]
    for uv,L in zip(huv,hL):
        u,_=unwrap1(uv[:,0]); u=u+TWO*np.floor((u0+TWO/2-u.mean())/TWO+0.5)
        holes2.append(np.column_stack([u,uv[:,1]])); holes3.append(L)
    h=2*np.arccos(max(0,1-TOL/sf.r))
    rr=refine_uv(sf,[poly]+holes2,[P3]+holes3,h,sf.r*h*4,np.array([sf.r,1.0]),aniso=True)
    if rr is None:
        S=np.array([sf.r,1.0]); rr=_polygon_faces([poly*S]+[x*S for x in holes2],[P3]+holes3)
    return refine_periodic_edges(sf,rr[0],rr[1],h)
def refine_periodic_edges(sf,X,T,hmax,max_iter=12):
    """Conforming red-green subdivision of interior edges whose span in the periodic u parameter exceeds hmax
    (chord error on a cylinder depends only on that span). Face-boundary edges (used by one triangle) are never
    split, so the face still shares its boundary polyline with its neighbours. Midpoints are evaluated on the surface."""
    X=[np.asarray(x,float) for x in X]; T=[tuple(t) for t in np.asarray(T)]
    uv=sf.inv(np.array(X)); U=list(uv[:,0]); V=list(uv[:,1])
    def span(a,b): return abs((U[b]-U[a]+np.pi)%TWO-np.pi)
    for _ in range(max_iter):
        cnt={}
        for t in T:
            for k in range(3):
                e=tuple(sorted((t[k],t[(k+1)%3]))); cnt[e]=cnt.get(e,0)+1
        split={}
        for e,c in cnt.items():
            if c==2 and span(*e)>hmax:
                a,b=e; um=U[a]+((U[b]-U[a]+np.pi)%TWO-np.pi)/2; vm=(V[a]+V[b])/2
                split[e]=len(X); X.append(np.asarray(sf.eval(np.array([um]),np.array([vm])),float).reshape(3)); U.append(um); V.append(vm)
        if not split: break
        NT=[]
        for t in T:
            m=[split.get(tuple(sorted((t[k],t[(k+1)%3])))) for k in range(3)]   # m[k] = midpoint of edge t[k]->t[k+1]
            n=sum(x is not None for x in m)
            if n==0: NT.append(t); continue
            if n==3:
                a,b,c=t; ab,bc,ca=m; NT+=[(a,ab,ca),(ab,b,bc),(ca,bc,c),(ab,bc,ca)]; continue
            # rotate so that edge 0 (t0->t1) is split
            r=[k for k in range(3) if m[k] is not None][0] if n==1 else [k for k in range(3) if m[k] is not None and m[(k+1)%3] is not None][0]
            a,b,c=t[r],t[(r+1)%3],t[(r+2)%3]; mab,mbc=m[r],m[(r+1)%3]
            if n==1: NT+=[(a,mab,c),(mab,b,c)]; continue
            NT.append((mab,b,mbc))   # edges a-b and b-c split; quad a,mab,mbc,c -> shorter diagonal
            if np.linalg.norm(X[a]-X[mbc])<=np.linalg.norm(X[mab]-X[c]): NT+=[(a,mab,mbc),(a,mbc,c)]
            else: NT+=[(a,mab,c),(mab,mbc,c)]
        T=NT
    return np.array(X),np.array(T)
def _zip(A,B):
    """zipper triangulation between two ascending-u loops (A below B), both closed with a duplicated end point"""
    (uA,LA),(uB,LB)=A,B; nA,nB=len(uA),len(uB)
    X=np.vstack([LA,LB]); i=j=0; tris=[]
    while i<nA-1 or j<nB-1:
        adv_a = i<nA-1 and (j==nB-1 or uA[i+1,0]<=uB[j+1,0])
        if adv_a: tris.append((i,i+1,nA+j)); i+=1
        else: tris.append((i,nA+j+1,nA+j)); j+=1
    return X,np.array(tris)
def tess_torus_region(b,f,sf,L,inside):
    """torus face bounded by ONE closed loop that does not wind. The face is either the disc inside the loop
    (inside=True) or the rest of the torus (inside=False). A periodic grid supplies interior vertices; the ragged
    gap between grid and the exact loop polyline is re-triangulated so the loop stays shared with its neighbour."""
    uv=sf.inv(L); u,wu=unwrap1(uv[:,0]); v,wv=unwrap1(uv[:,1])
    assert wu==0 and wv==0,'loop winds on torus'
    loop=np.column_stack([u,v]); cen=loop.mean(0)
    rmax=sf.R+sf.r; rmin=max(min(sf.r,abs(sf.R)),1e-4)
    nu=max(24,int(np.ceil(TWO/(2*np.arccos(max(0,1-TOL/rmax))))))
    nv=max(24,int(np.ceil(TWO/(2*np.arccos(max(0,1-TOL/sf.r))))))
    us=cen[0]+(np.arange(nu)+0.5)*TWO/nu-np.pi; vs=cen[1]+(np.arange(nv)+0.5)*TWO/nv-np.pi
    UU,VV=np.meshgrid(us,vs,indexing='ij'); Q=np.column_stack([UU.ravel(),VV.ravel()])
    rho=max(abs(sf.R+sf.r*np.cos(cen[1])),1e-4); S=np.array([rho,sf.r])
    ins=_pip(Q,loop); d=_dist_to_poly(Q*S,loop*S)
    step=max(TWO*rho/nu,TWO*sf.r/nv)
    keep=(ins&(d>1.6*step)) if inside else ((~ins)&(d>1.6*step))
    idx=lambda i,j:(i%nu)*nv+(j%nv)
    tris=[]
    for i in range(nu):
        for j in range(nv):
            a,bq,c,dd=idx(i,j),idx(i+1,j),idx(i+1,j+1),idx(i,j+1)
            for tr in ((a,bq,c),(a,c,dd)):
                if all(keep[k] for k in tr): tris.append(tr)
    if not tris:   # patch smaller than the grid step: plain polygon
        X,tt=_polygon_faces([loop],[L]); return X,tt
    de={}
    for tr in tris:
        for k in range(3): de[(tr[k],tr[(k+1)%3])]=1
    bnd={(p,q) for (p,q) in de if (q,p) not in de}
    nxt={}
    for p,q in bnd: nxt.setdefault(p,[]).append(q)
    assert all(len(x)==1 for x in nxt.values()),'ragged boundary not a simple cycle'
    start=next(iter(nxt)); cyc=[start]
    while True:
        n=nxt[cyc[-1]][0]
        if n==start: break
        cyc.append(n)
    assert len(cyc)==len(nxt),'multiple ragged cycles'
    cq=Q[cyc].copy(); cq=cq-TWO*np.round((cq-cen)/TWO)
    used=sorted({k for tr in tris for k in tr}); gidx={k:n for n,k in enumerate(used)}
    GX=sf.eval(Q[used,0],Q[used,1]); cx=GX[[gidx[k] for k in cyc]]
    XYZ_f,tf=_polygon_faces([cq,loop],[cx,L])
    ncyc=len(cyc); X=list(GX); remap={i:gidx[cyc[i]] for i in range(ncyc)}
    # _polygon_faces numbers loops in the given order: cycle first, then loop
    for i in range(ncyc,len(XYZ_f)): remap[i]=len(X); X.append(XYZ_f[i])
    T=[(gidx[p],gidx[q],gidx[r]) for p,q,r in tris]+[tuple(remap[i] for i in tr) for tr in tf]
    return np.array(X),np.array(T)
def refine_uv(sf,uvloops,loops3,du,dv,S,aniso=False):
    """interior grid for a curved analytic face in (unwrapped) UV. S = metric scale (arc length per radian/unit) for u,v.
    Returns X,T or None when the face is too small for interior points."""
    areas=[abs(area2([tuple(p) for p in uv],list(range(len(uv))))) for uv in uvloops]
    o=int(np.argmax(areas)); outer=uvloops[o]; holes=[uv for k,uv in enumerate(uvloops) if k!=o]
    allp=np.vstack(uvloops); lo=allp.min(0); hi=allp.max(0)
    nu=int(np.floor((hi[0]-lo[0])/du)); nv=int(np.floor((hi[1]-lo[1])/dv))
    if nu<3 or nv<3: return None
    us=lo[0]+(np.arange(nu)+0.5)*(hi[0]-lo[0])/nu; vs=lo[1]+(np.arange(nv)+0.5)*(hi[1]-lo[1])/nv
    UU,VV=np.meshgrid(us,vs,indexing='ij'); Q=np.column_stack([UU.ravel(),VV.ravel()])
    keep=_pip(Q,outer)
    for h in holes: keep&=~_pip(Q,h)
    step=max((hi[0]-lo[0])/nu*S[0],(hi[1]-lo[1])/nv*S[1])
    dmin=np.full(len(Q),np.inf)
    if aniso:   # band = 1.3 grid cells in each direction (keeps triangles narrow in the curved direction)
        cell=np.array([(hi[0]-lo[0])/nu*S[0],(hi[1]-lo[1])/nv*S[1]]); Sc=S/cell
        for uv in uvloops: dmin=np.minimum(dmin,_dist_to_poly(Q*Sc,uv*Sc))
        keep&=dmin>1.3
    else:
        for uv in uvloops: dmin=np.minimum(dmin,_dist_to_poly(Q*S,uv*S))
        keep&=dmin>1.3*step
    tris=[]
    for i in range(nu-1):
        for j in range(nv-1):
            a,b_,c,d=i*nv+j,(i+1)*nv+j,(i+1)*nv+j+1,i*nv+j+1
            if keep[a] and keep[b_] and keep[c] and keep[d]: tris+= [(a,b_,c),(a,c,d)]
    if not tris: return None
    de={}
    for tr in tris:
        for k in range(3): de[(tr[k],tr[(k+1)%3])]=1
    bnd={(p,q) for (p,q) in de if (q,p) not in de}
    nxt={}
    for p,q in bnd: nxt.setdefault(p,[]).append(q)
    if any(len(v)!=1 for v in nxt.values()): return None
    cycles=[]; seen=set()
    for s0 in list(nxt):
        if s0 in seen: continue
        cyc=[s0]; seen.add(s0)
        while True:
            n=nxt[cyc[-1]][0]
            if n==s0: break
            cyc.append(n); seen.add(n)
        cycles.append(cyc)
    used=sorted({k for tr in tris for k in tr}); gidx={k:n for n,k in enumerate(used)}
    GX=sf.eval(Q[used,0],Q[used,1])
    # ring fill: polygon = original loops + ragged cycles (as extra holes)
    l2=list(uvloops); l3=list(loops3); offs=[0]
    for uv in uvloops: offs.append(offs[-1]+len(uv))
    cyc_off=[]
    for cyc in cycles:
        cyc_off.append(offs[-1]); l2.append(Q[cyc]); l3.append(GX[[gidx[k] for k in cyc]]); offs.append(offs[-1]+len(cyc))
    XYZ_f,tf=_polygon_faces(l2,l3)
    X=list(GX); remap={}
    for ci,cyc in enumerate(cycles):
        for n,k in enumerate(cyc): remap[cyc_off[ci]+n]=gidx[k]
    for i in range(len(XYZ_f)):
        if i not in remap: remap[i]=len(X); X.append(XYZ_f[i])
    T=[(gidx[p],gidx[q],gidx[r]) for p,q,r in tris]+[tuple(remap[i] for i in tr) for tr in tf]
    return np.array(X),np.array(T)
def _inside_bbox(X,bbox,tol=2e-4):
    if bbox is None: return True
    lo,hi=np.array(bbox[0]),np.array(bbox[1]); span=np.maximum(hi-lo,1e-9)
    return bool(((X>=lo-tol*span-1e-6)&(X<=hi+tol*span+1e-6)).all())

def tess_face(b,fid):
    N=b.N; f=N[fid]; sf=b.surf(f['surface'])
    loops3=b.face_loops(fid); kind=sf.__class__.__name__
    info=dict(mode='uv')
    if kind=='Cone':
        info['mode']='axis'; l2=[np.stack([(L-sf.p)@sf.x,(L-sf.p)@sf.y],-1) for L in loops3]
        X,t=_polygon_faces(l2,loops3)
    elif kind=='NoEval':
        info['mode']='pca'; allp=np.vstack(loops3); m=allp.mean(0)
        _,_,vt=np.linalg.svd(allp-m,full_matrices=False); e1,e2=vt[0],vt[1]
        l2=[np.stack([(L-m)@e1,(L-m)@e2],-1) for L in loops3]
        X,t=_polygon_faces(l2,loops3)
    elif kind=='Torus':
        uvs=[sf.inv(L) for L in loops3]
        winds=[(unwrap1(u[:,0])[1],unwrap1(u[:,1])[1]) for u in uvs]
        if any(w!=(0,0) for w in winds):
            info['mode']='axis'; l2=[np.stack([(L-sf.c)@sf.x,(L-sf.c)@sf.y],-1) for L in loops3]
            X,t=_polygon_faces(l2,loops3)
        else:
            assert len(loops3)==1,'torus face with several non-winding loops'
            Ld=b.face_loops_directed(fid)[0]; ud=sf.inv(Ld); uu,_=unwrap1(ud[:,0]); vv,_=unwrap1(ud[:,1])
            area=np.sum(uu*np.roll(vv,-1)-np.roll(uu,-1)*vv)/2
            sg=(1 if f['sense']=='+' else -1)*(1 if N[f['surface']]['sense']=='+' else -1)
            inside=((area>0)==(sg>0)); info['mode']='torus-inside' if inside else 'torus-complement'
            inside=getattr(b,'force_side',{}).get(fid,inside)
            X,t=tess_torus_region(b,f,sf,loops3[0],inside)
            bbox=getattr(b,'bbox',None)
            if not _inside_bbox(X,bbox):          # stored part bounding box vetoes this side
                X2,t2=tess_torus_region(b,f,sf,loops3[0],not inside)
                if _inside_bbox(X2,bbox): X,t=X2,t2; info['mode']+='(bbox-flipped)'
    elif kind=='Sphere':
        uvs=[sf.inv(L) for L in loops3]; winds=[unwrap1(u[:,0])[1] for u in uvs]
        if any(winds):
            info['mode']='axis'; l2=[np.stack([(L-sf.c)@sf.x,(L-sf.c)@sf.y],-1) for L in loops3]
            X,t=_polygon_faces(l2,loops3)
        else:
            l2=[]
            for uv in uvs: uu,_=unwrap_loop(uv); l2.append(uu)
            ref=l2[0][:,0].mean(); l2=[np.column_stack([uv[:,0]+TWO*np.round((ref-uv[:,0].mean())/TWO),uv[:,1]]) for uv in l2]
            h=2*np.arccos(max(0,1-TOL/sf.r)); rr=refine_uv(sf,l2,loops3,h,h,np.array([sf.r*max(np.cos(np.mean([p[:,1].mean() for p in l2])),0.2),sf.r]))
            if rr is not None: info['mode']='sphere-refined'; X,t=rr
            else: X,t=_polygon_faces(l2,loops3)
    else:
        per=getattr(sf,'periodic_u',False)
        if kind=='Cylinder':
            uvs=[sf.inv(L) for L in loops3]; ws=[unwrap1(u[:,0])[1] for u in uvs]
            if len(loops3)==2 and all(abs(w)==1 for w in ws):
                info['mode']='strip'; uu=[unwrap_loop(u)[0] for u in uvs]
                X,t=_zip(*_strip(uu,loops3,ws))
                info['n']=len(t); return X,t,info
            wi=[k for k,w in enumerate(ws) if abs(w)==1]
            if len(wi)==2 and len(loops3)>2 and all(ws[k]==0 for k in range(len(ws)) if k not in wi):
                # tube wall with cross holes: cut the strip at a seam clear of the holes, then ear-clip with holes
                X,t=_strip_with_holes(sf,[uvs[k] for k in wi],[loops3[k] for k in wi],[ws[k] for k in wi],
                                      [uvs[k] for k in range(len(uvs)) if k not in wi],[loops3[k] for k in range(len(uvs)) if k not in wi])
                info['mode']='strip-holes'; info['n']=len(t); return X,t,info
        l2=[]
        for L in loops3:
            uv=sf.inv(L)
            if per: uv,_=unwrap_loop(uv)
            l2.append(uv)
        if per:
            ref=l2[0][:,0].mean(); l2=[np.column_stack([uv[:,0]+TWO*np.round((ref-uv[:,0].mean())/TWO),uv[:,1]]) for uv in l2]
        rr=None
        if kind=='Cylinder':
            h=2*np.arccos(max(0,1-TOL/sf.r)); rr=refine_uv(sf,l2,loops3,h,sf.r*h*4,np.array([sf.r,1.0]))
        if rr is not None: info['mode']='cyl-refined'; X,t=rr
        else: X,t=_polygon_faces(l2,loops3)
    info['n']=len(t)
    return X,t,info
