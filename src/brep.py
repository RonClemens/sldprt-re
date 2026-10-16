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
    def surf(s,fid):
        if fid not in s.surfs: s.surfs[fid]=make_surface(s.N,fid)
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
            return (simplify(P,TOL) if COARSE else P),False
        if t=='TRIMMED_CURVE':
            return s.curve_poly(n['basis_curve'],depth+1)
        if t=='SP_CURVE':
            uv,_=s.curve_poly(n['b_curve'],depth+1)
            sf=s.surf(n['surface']); return sf.eval(uv[:,0],uv[:,1]),False
        raise NotImplementedError(t)
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
        while base!=1 and N[base]['name']=='TRIMMED_CURVE': base=N[base]['basis_curve']    # a trimmed straight line is still just its two end points
        if cid==1 or N[base]['name']=='LINE':
            pts=np.array([A,B]) if A is not None and B is not None else None
            if pts is None: raise ValueError('line edge without vertices')
        else:
            D,per=s.curve_poly(cid)
            cn=N[cid]
            if cn['name'] in ('CIRCLE','ELLIPSE'): per=True
            if cn['name']=='INTERSECTION' and A is None and B is None: per=False
            flip=(FLIPMODE==1 and N[cid]['sense']=='-') or (FLIPMODE==2 and N[cid]['sense']=='+')
            if A is None and B is None: flip=(cn['sense']=='-')
            pts=clip(D,A,B,per,flip)
            if per and A is not None and B is not None: pts=pts[::-1]   # arcs: directed B->A (empirical, see notes)
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
        if (iA,tA)<=(iB,tB): mid=D[iA+1:iB+1]
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
