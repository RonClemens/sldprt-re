import numpy as np
from xt import parse
from schema import load_schema
from geom import *
import os
TOL=float(os.environ.get('SLD_TOL','1e-5'))    # chord tolerance in metres (default 10 um); coarser = far fewer triangles, e.g. SLD_TOL=2e-4 for viewing
# Empirical rules (validated on a 2-56 hex nut: watertight, 0 orientation conflicts):
#  * edge start/end taken from the halfedge with sense '+' (SWAP=False)
#  * periodic curves (circles): arc runs CLOCKWISE (about the curve's own normal) from start to end when
#    the curve sense is '+', counter-clockwise when '-'   (FLIPMODE=2)
SWAP=False
FLIPMODE=2
def simplify(P,tol):
    """Ramer-Douglas-Peucker: drop polyline points that deviate less than `tol` from the chord (end points kept). Used only for coarse viewing meshes."""
    n=len(P)
    if n<=2: return P
    keep=np.zeros(n,bool); keep[0]=keep[-1]=True; stack=[(0,n-1)]
    while stack:
        a,b=stack.pop()
        if b<=a+1: continue
        ab=P[b]-P[a]; L2=float(ab@ab); seg=P[a+1:b]-P[a]
        if L2<1e-30: d=np.linalg.norm(seg,axis=1)
        else:
            t=np.clip((seg@ab)/L2,0,1); d=np.linalg.norm(seg-t[:,None]*ab,axis=1)
        i=int(d.argmax())
        if d[i]>tol: k=a+1+i; keep[k]=True; stack+=[(a,k),(k,b)]
    return P[keep]
COARSE=TOL>1.5e-5    # coarse viewing mode: boundary curves are simplified to the tolerance as well
class Brep:
    def __init__(s,buf,schema_path='/mnt/user-data/uploads/sch_13006_s_t.txt'):
        _,base=load_schema(schema_path)
        try: g=parse(buf,base)
        except Exception:    # bodies over 1 MiB are cut off inside the container: keep every node that did decode and mesh what is complete
            g=parse(buf,base,partial=True)
            if not g.get('truncated') or len(g['nodes'])<10: raise
        s.truncated=bool(g.get('truncated')); s.g=g; s.N=g['nodes']; N=s.N
        s.surfs={}
        s.edge_cache={}
    def raw_surf(s,fid):
        if not hasattr(s,'_raw'): s._raw={}
        if fid not in s._raw: s._raw[fid]=make_surface(s.N,fid)
        return s._raw[fid]
    def surf(s,fid):
        if fid not in s.surfs:
            sf=s.raw_surf(fid); s.surfs[fid]=getattr(sf,'sphere',None) or sf    # a spun circle about its own diameter is meshed as the sphere it is
        return s.surfs[fid]
    # ---- dense polylines of curves
    def curve_poly(s,cid,depth=0):
        N=s.N; n=N[cid]; t=n['name']
        if t=='CIRCLE':
            c=np.array(n['centre']); nz=unit(n['normal']); x=unit(n['x_axis']); y=np.cross(nz,x); r=n['radius']
            m=max(16,int(np.ceil(2*np.pi/(2*np.arccos(max(0,1-TOL/r))))))
            th=np.linspace(0,2*np.pi,m+1)[:-1]
            return c+r*(np.cos(th)[:,None]*x+np.sin(th)[:,None]*y), True
        if t=='ELLIPSE':
            c=np.array(n['centre']); nz=unit(n['normal']); x=unit(n['x_axis']); y=np.cross(nz,x); A=n['major_radius']; B=n['minor_radius']
            m=max(24,int(np.ceil(2*np.pi/(2*np.arccos(max(0,1-TOL/max(A,B)))))))
            th=np.linspace(0,2*np.pi,m+1)[:-1]
            return c+A*np.cos(th)[:,None]*x+B*np.sin(th)[:,None]*y, True
        if t=='B_CURVE':
            nb=Nurbs(N,n['nurbs'],False)
            kn=np.unique(nb.k); ts=[]
            for a,b in zip(kn[:-1],kn[1:]):
                ts.append(np.linspace(a,b,max(2,6*nb.d)+1)[:-1])
            ts.append([kn[-1]]); ts=np.concatenate(ts); ts=ts[(ts>=nb.range[0]-1e-14)&(ts<=nb.range[1]+1e-14)]
            P=nb.curve(ts)
            if nb.vd in(2,3) and P.shape[1]==2: return P,False  # uv curve
            return (simplify(P,TOL) if COARSE else P),bool(getattr(nb,'periodic',False))     # a periodic B-curve is a closed loop, like a circle
        if t=='INTERSECTION':
            P=np.array(N[n['chart']]['hvec'],float)
            P=s._refine_chart(P,n)
            return (simplify(P,TOL) if COARSE else P),False
        if t=='TRIMMED_CURVE':
            return s.curve_poly(n['basis_curve'],depth+1)
        if t=='SP_CURVE':
            uv,_=s.curve_poly(n['b_curve'],depth+1)
            sf=s.raw_surf(n['surface'])     # pcurve parameters belong to the stored (spun) form
            if sf.__class__.__name__=='Spun': uv=uv[:,::-1]     # on a spun surface the pcurve is stored as (profile parameter, angle)
            if sf.__class__.__name__!='Plane' and len(uv)>1:     # a linear pcurve (2 control points) is an arc on a revolved surface: sample it finely enough to follow the curvature
                k=np.maximum(1,np.ceil(np.abs(np.diff(uv,axis=0)).max(1)/0.05)).astype(int)
                uv=np.vstack([uv[i]+(uv[i+1]-uv[i])*(np.arange(k[i])/k[i])[:,None] for i in range(len(k))]+[uv[-1:]])
            return sf.eval(uv[:,0],uv[:,1]),False
        raise NotImplementedError(t)
    def _trimmed_sp_poly(s,tc):
        """the arc of a periodic surface pcurve that its TRIMMED_CURVE selects: the trim parameters may run past the end of the period, which only the parameters (not the end points) can tell from the complementary arc"""
        n=s.N[tc]; sp=s.N[n['basis_curve']]
        if sp['name']!='SP_CURVE' or s.N[sp['b_curve']]['name']!='B_CURVE': return None
        nb=Nurbs(s.N,s.N[sp['b_curve']]['nurbs'],False)
        if not getattr(nb,'periodic',False) or nb.vd not in (2,3): return None
        p1,p2=sorted((n['parm_1'],n['parm_2'])); per=nb.range[1]-nb.range[0]; p2=min(p2,p1+per)
        m=max(16,int((p2-p1)/per*max(200,12*len(np.unique(nb.k)))))
        uv=nb.curve(nb.range[0]+(np.linspace(p1,p2,m+1)-nb.range[0])%per)
        sf=s.raw_surf(sp['surface'])
        if sf.__class__.__name__=='Spun': uv=uv[:,::-1]
        if sf.__class__.__name__!='Plane':
            k=np.maximum(1,np.ceil(np.abs(np.diff(uv,axis=0)).max(1)/0.05)).astype(int)
            uv=np.vstack([uv[i]+(uv[i+1]-uv[i])*(np.arange(k[i])/k[i])[:,None] for i in range(len(k))]+[uv[-1:]])
        return sf.eval(uv[:,0],uv[:,1])
    def _refine_chart(s,P,n):
        """Parasolid stores an intersection curve as a coarse chart (chordal error often millimetres): insert midpoints and pull them onto both surfaces until the polyline follows the true curve"""
        ch=s.N[n['chart']]; ce=ch.get('chordal_error',0) or 0
        if ce<=TOL or len(P)<2: return P
        try:
            S=[s.raw_surf(x) for x in n['surface'] if x!=1]
            if len(S)!=2 or not all(hasattr(x,'inv') for x in S): return P
            lv=int(min(6,np.ceil(np.log(ce/TOL)/np.log(4))))
            for _ in range(lv):
                M=(P[:-1]+P[1:])/2
                for _ in range(8):     # Newton step onto both surfaces at once (alternating projections crawl when they meet at a shallow angle)
                    nn=[];bb=[]
                    for x in S:
                        uv=x.inv(M); v=x.eval(uv[:,0],uv[:,1])-M; nr=x.normal_raw(uv[:,0],uv[:,1]); nn.append(nr); bb.append((v*nr).sum(1))
                    n1,n2=nn; c=(n1*n2).sum(1); det=np.maximum(1-c*c,1e-12)
                    a=(bb[0]-c*bb[1])/det; b2=(bb[1]-c*bb[0])/det     # minimal-norm delta = a*n1+b2*n2 with n1.delta=bb0, n2.delta=bb1
                    M=M+a[:,None]*n1+b2[:,None]*n2
                Q=np.empty((2*len(P)-1,3)); Q[0::2]=P; Q[1::2]=M; P=Q
            return P
        except (AttributeError,NotImplementedError,ValueError): return P
    # ---- edges
    def edge_vertices(s,eid):
        N=s.N; e=N[eid]; h=N[e['halfedge']]
        if h['sense']!='+': h=N[h['other']]
        st=h['vertex']; en=N[h['other']]['vertex']
        if SWAP: st,en=en,st
        pos=lambda v: None if v==1 else np.array(N[N[v]['point']]['pvec'])
        return pos(st),pos(en),h['id']
    def _halfedge_curves(s):
        if not hasattr(s,'_hec'):
            s._hec={x['owner']:i for i,x in s.N.items() if x['name']=='TRIMMED_CURVE' and x.get('owner') in s.N and s.N[x['owner']]['name']=='HALFEDGE'}
        return s._hec
    def edge_poly(s,eid):
        """3D polyline from start vertex to end vertex of the edge (edge direction)."""
        if eid in s.edge_cache: return s.edge_cache[eid]
        N=s.N; e=N[eid]; A,B,_=s.edge_vertices(eid)
        cid=e['curve']; base=cid
        if cid==1 and A is None and B is None:
            # a ring edge with no 3D curve and no vertices (periodic faces, e.g. pipe elbows): its geometry is stored per half-edge as a
            # trimmed surface curve (SP_CURVE). Evaluate that on its surface; the full pcurve is the closed ring.
            hc=s._halfedge_curves().get(e['halfedge']) or s._halfedge_curves().get(N[e['halfedge']]['other'])
            if hc is not None:
                D,_=s.curve_poly(hc)
                if np.linalg.norm(D[0]-D[-1])>1e-9: D=np.vstack([D,D[:1]])
                s.edge_cache[eid]=D; return D
        if cid==1 and A is not None and B is not None:
            # a curve-less edge with vertices (a circle split in two on a revolved face): the half-edge's surface curve carries the geometry; keep the part between the vertices
            hc=s._halfedge_curves().get(e['halfedge']) or s._halfedge_curves().get(N[e['halfedge']]['other'])
            if hc is not None:
                try:
                    T=s._trimmed_sp_poly(hc) if np.linalg.norm(A-B)>1e-9 else None
                    if T is not None: s.edge_cache[eid]=clip(T,A,B,False,False); return s.edge_cache[eid]
                    D,_=s.curve_poly(hc)
                except (AttributeError,NotImplementedError): D=None    # the pcurve lies on a surface we cannot evaluate (blend): fall back to the straight chord below
                if D is not None:
                    per=bool(np.linalg.norm(D[0]-D[-1])<1e-9)
                    if per: D=D[:-1]
                    s.edge_cache[eid]=clip(D,A,B,per,False); return s.edge_cache[eid]
        while base!=1 and N[base]['name']=='TRIMMED_CURVE': base=N[base]['basis_curve']    # a trimmed straight line is still just its two end points
        if cid==1 or N[base]['name']=='LINE':
            pts=np.array([A,B]) if A is not None and B is not None else None
            if pts is None: raise ValueError('line edge without vertices')
        else:
            D,per=s.curve_poly(cid)
            cn=N[cid]
            if cn['name']=='INTERSECTION' and A is not None and B is not None and not per and len(D)>3 and np.linalg.norm(D[0]-D[-1])<max(1e-9,2e-3*np.linalg.norm(np.diff(D,axis=0),axis=1).sum()):    # (a stored chart closes to within its own sampling slack)
                # a closed chart: its two end vertices can lie on either side of the seam, so open clipping would take the arc through the seam wrongly or reuse the other edge's stretch; go against the chart order
                s.edge_cache[eid]=clip(D[:-1],B,A,True,False)[::-1]; return s.edge_cache[eid]    # the edge runs against the chart order (verified: edges sharing a chart then cover complementary stretches)
            if cn['name'] in ('CIRCLE','ELLIPSE'): per=True
            if cn['name']=='INTERSECTION' and A is None and B is None: per=False
            flip=(FLIPMODE==1 and N[cid]['sense']=='-') or (FLIPMODE==2 and N[cid]['sense']=='+')
            if A is None and B is None: flip=(cn['sense']=='-')
            pts=clip(D,A,B,per,flip)
            if per and A is not None and B is not None:
                if cn['name']=='TRIMMED_CURVE' and N[base]['name'] in ('CIRCLE','ELLIPSE'):
                    # the trim parameters (radians on the circle) give the arc's true extent: when the rule picked the complementary arc, take the other one
                    span=abs(cn['parm_2']-cn['parm_1']); alt=clip(D,A,B,per,not flip)
                    if abs(_arc_extent(alt,N[base])-span)<abs(_arc_extent(pts,N[base])-span)-1e-3: pts=alt
                pts=pts[::-1]   # arcs: directed B->A (empirical, see notes)
        s.edge_cache[eid]=pts; return pts
    def face_loops_directed(s,fid):
        """loops as directed polylines, face on the LEFT when seen from the outward normal (verified on planes).
        halfedges are walked via 'forward'; each edge polyline runs A->B, reversed when the halfedge sense is '-'."""
        N=s.N; f=N[fid]; loops=[]; l=f['loop']
        while l!=1:
            L=N[l]
            if N[L['halfedge']]['edge']==1: l=L['next']; continue    # vertex loop (cone apex, sphere pole): a point, not a boundary
            h=h0=L['halfedge']; segs=[]
            while True:
                H=N[h]; p=s.edge_poly(H['edge'])
                segs.append(p[::-1] if H['sense']=='-' else p); h=H['forward']
                if h==h0: break
            P=np.vstack([segs[0]]+[q[1:] for q in segs[1:]])
            if np.linalg.norm(P[0]-P[-1])<1e-9: P=P[:-1]
            loops.append(P); l=L['next']
        return loops
    # ---- face loops (list of closed 3D polylines, each point list w/o repeated end)
    def face_loops(s,fid):
        N=s.N; f=N[fid]; loops=[]; l=f['loop']
        while l!=1:
            L=N[l]
            if N[L['halfedge']]['edge']==1: l=L['next']; continue    # vertex loop (cone apex, sphere pole): a point, not a boundary
            h=h0=L['halfedge']; segs=[]
            while True:
                H=N[h]; segs.append(s.edge_poly(H['edge'])); h=H['backward']
                if h==h0: break
            loops.append(chain_undirected(segs)); l=L['next']
        return loops
def _arc_extent(P,c):
    """angle swept by the polyline P about the circle node c"""
    nz=unit(c['normal']); x=unit(c['x_axis']); y=np.cross(nz,x); d=np.asarray(P)-np.array(c['centre'])
    a=np.arctan2(d@y/c.get('minor_radius',1.0),d@x/c.get('major_radius',1.0)); return float(np.abs(np.diff(np.unwrap(a))).sum())    # (an ellipse is parametrised by its eccentric angle)
def clip(D,A,B,per,flip):
    n=len(D)
    def proj(P):
        a=D[:-1] if not per else D; b=D[1:] if not per else np.roll(D,-1,0)
        ab=b-a; t=((P-a)*ab).sum(1)/np.maximum((ab*ab).sum(1),1e-300); t=np.clip(t,0,1)
        q=a+t[:,None]*ab; d=((q-P)**2).sum(1); i=d.argmin(); return i,t[i],q[i],d[i]
    if A is None and B is None:
        out=D.copy() if not per else np.vstack([D,D[:1]])
        return out[::-1] if flip else out
    if flip: A,B=B,A
    iA,tA,qA,_=proj(A); iB,tB,qB,_=proj(B)
    if not per:
        if (iA,tA)<=(iB,tB): mid=D[iA+1:iB+1]; out=np.vstack([[A],mid,[B]])
        else: mid=D[iB+1:iA+1][::-1]; out=np.vstack([[A],mid,[B]])
    else:
        if np.linalg.norm(np.asarray(A)-np.asarray(B))<1e-9: mid=np.vstack([D[iA+1:],D[:iA+1]])    # one vertex closes the whole ring
        elif (iA,tA)<=(iB,tB): mid=D[iA+1:iB+1]
        else: mid=np.vstack([D[iA+1:],D[:iB+1]])
        out=np.vstack([[A],mid,[B]])
    if flip: out=out[::-1]
    return out
def chain_undirected(segs):
    segs=[np.asarray(x) for x in segs]
    cur=segs[0]; used=[0]; out=[cur]
    while len(used)<len(segs):
        end=out[-1][-1]; best=None
        for k,sg in enumerate(segs):
            if k in used: continue
            for rev in (False,True):
                a=sg[-1] if rev else sg[0]; d=np.linalg.norm(a-end)
                if best is None or d<best[0]: best=(d,k,rev)
        d,k,rev=best; used.append(k); out.append(segs[k][::-1] if rev else segs[k])
    P=np.vstack([out[0]]+[p[1:] for p in out[1:]])
    if np.linalg.norm(P[0]-P[-1])<1e-9: P=P[:-1]
    return P
def chain(segs):
    pts=[segs[0]]; 
    for sg in segs[1:]:
        last=pts[-1][-1]
        if np.linalg.norm(sg[0]-last)>np.linalg.norm(sg[-1]-last): sg=sg[::-1]
        pts.append(sg)
    out=[pts[0]]+[p[1:] for p in pts[1:]]
    P=np.vstack(out)
    if np.linalg.norm(P[0]-P[-1])<1e-9: P=P[:-1]
    return P
if __name__=='__main__':
    b=Brep(open('part_sec0.bin','rb').read())
    N=b.N
    for f in N.values():
        if f['name']!='FACE': continue
        try:
            L=b.face_loops(f['id'])
        except Exception as ex:
            print(f['id'],'ERR',repr(ex)); continue
        gaps=[]
        print(f['id'],N[f['surface']]['name'],[len(l) for l in L])
