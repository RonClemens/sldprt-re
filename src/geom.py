import numpy as np
from scipy.interpolate import BSpline
def unit(v): v=np.asarray(v,float); return v/np.linalg.norm(v)
def sgn(s): return 1.0 if s=='+' else -1.0
class Nurbs:
    """tensor-product or curve NURBS evaluator (homogeneous coords)."""
    def __init__(s,N,nid,surf=False):
        n=N[nid]; s.surf=surf
        vd=n['vertex_dim']; s.rational=n['rational']
        vs=N[n['bspline_vertices']]['vertices']
        P=np.array(vs,float)
        if surf:
            s.nu,s.nv=n['n_u_vertices'],n['n_v_vertices']
            P=P.reshape(s.nu,s.nv,vd)
            s.ku=s.expand(N[n['u_knots']]['knots'],N[n['u_knot_mult']]['mult'])
            s.kv=s.expand(N[n['v_knots']]['knots'],N[n['v_knot_mult']]['mult'])
            s.du,s.dv=n['u_degree'],n['v_degree']
            assert not n['u_periodic'] and not n['v_periodic']
            s.urange=(s.ku[s.du],s.ku[-s.du-1]); s.vrange=(s.kv[s.dv],s.kv[-s.dv-1])
        else:
            s.n=n['n_vertices']; P=P.reshape(s.n,vd)
            s.k=s.expand(N[n['knots']]['knots'],N[n['knot_mult']]['mult'])
            s.d=n['degree']; s.periodic=n['periodic']
            assert not s.periodic, 'periodic nurbs curve'
            s.range=(s.k[s.d],s.k[-s.d-1])
        s.P=P; s.vd=vd
    @staticmethod
    def expand(k,m): return np.repeat(np.array(k,float),np.array(m,int))
    def _out(s,H):
        if s.rational: return H[...,:-1]/H[...,-1:]
        return H
    def curve(s,t):
        t=np.clip(np.asarray(t,float),*s.range)
        B=BSpline.design_matrix(t,s.k,s.d,extrapolate=False).toarray()
        return s._out(B@s.P)
    def surface(s,u,v):
        u=np.clip(np.asarray(u,float),*s.urange); v=np.clip(np.asarray(v,float),*s.vrange)
        Bu=BSpline.design_matrix(u,s.ku,s.du,extrapolate=False).toarray()
        Bv=BSpline.design_matrix(v,s.kv,s.dv,extrapolate=False).toarray()
        H=np.einsum('mi,mj,ijd->md',Bu,Bv,s.P)
        return s._out(H)
class Surf:
    periodic_u=False
    def normal_raw(s,u,v,h=1e-6):
        pu=(s.eval(u+h,v)-s.eval(u-h,v)); pv=(s.eval(u,v+h)-s.eval(u,v-h))
        n=np.cross(pu,pv); return n/np.maximum(np.linalg.norm(n,axis=-1,keepdims=True),1e-300)
class Plane(Surf):
    def __init__(s,n):
        s.p=np.array(n['pvec']); s.n=unit(n['normal']); s.x=unit(n['x_axis']); s.y=np.cross(s.n,s.x)
    def eval(s,u,v):
        u=np.asarray(u)[...,None]; v=np.asarray(v)[...,None]; return s.p+u*s.x+v*s.y
    def inv(s,P):
        d=np.asarray(P)-s.p; return np.stack([d@s.x,d@s.y],-1)
    def normal_raw(s,u,v,h=0): 
        shp=np.broadcast(np.asarray(u),np.asarray(v)).shape; return np.broadcast_to(s.n,shp+(3,))
class Cylinder(Surf):
    periodic_u=True
    def __init__(s,n):
        s.p=np.array(n['pvec']); s.a=unit(n['axis']); s.r=n['radius']; s.x=unit(n['x_axis']); s.y=np.cross(s.a,s.x)
    def eval(s,u,v):
        u=np.asarray(u)[...,None]; v=np.asarray(v)[...,None]
        return s.p+s.r*(np.cos(u)*s.x+np.sin(u)*s.y)+v*s.a
    def inv(s,P):
        d=np.asarray(P)-s.p; return np.stack([np.arctan2(d@s.y,d@s.x),d@s.a],-1)
class Cone(Surf):
    periodic_u=True
    def __init__(s,n):
        s.p=np.array(n['pvec']); s.a=unit(n['axis']); s.r=n['radius']; s.x=unit(n['x_axis']); s.y=np.cross(s.a,s.x)
        s.t=n['sin_half_angle']/n['cos_half_angle']
    def eval(s,u,v):
        u=np.asarray(u)[...,None]; v=np.asarray(v)[...,None]
        return s.p+(s.r+v*s.t)*(np.cos(u)*s.x+np.sin(u)*s.y)+v*s.a
    def inv(s,P):
        d=np.asarray(P)-s.p; return np.stack([np.arctan2(d@s.y,d@s.x),d@s.a],-1)
class BSurf(Surf):
    def __init__(s,N,n):
        s.nb=Nurbs(N,n['nurbs'],True)
        # grid for seeding inversion
        gu=np.linspace(*s.nb.urange,41); gv=np.linspace(*s.nb.vrange,max(41,s.nb.nv*3))
        U,V=np.meshgrid(gu,gv,indexing='ij'); s.gU=U.ravel(); s.gV=V.ravel()
        s.gP=s.nb.surface(s.gU,s.gV)
    def eval(s,u,v):
        u,v=np.broadcast_arrays(np.asarray(u,float),np.asarray(v,float)); sh=u.shape
        return s.nb.surface(u.ravel(),v.ravel()).reshape(sh+(3,))
    def inv(s,P,iters=12):
        P=np.atleast_2d(np.asarray(P,float))
        k=np.empty(len(P),int)
        for i0 in range(0,len(P),64):
            d=((P[i0:i0+64,None,:]-s.gP[None,:,:])**2).sum(-1); k[i0:i0+64]=d.argmin(1)
        u=s.gU[k].copy(); v=s.gV[k].copy(); h=1e-6
        (u0,u1),(v0,v1)=s.nb.urange,s.nb.vrange
        for _ in range(iters):
            S=s.eval(u,v); Su=(s.eval(u+h,v)-s.eval(u-h,v))/(2*h); Sv=(s.eval(u,v+h)-s.eval(u,v-h))/(2*h)
            r=P-S
            a=(Su*Su).sum(1); b=(Su*Sv).sum(1); c=(Sv*Sv).sum(1); e=(Su*r).sum(1); f=(Sv*r).sum(1)
            det=a*c-b*b; det=np.where(abs(det)<1e-30,1e-30,det)
            du=(c*e-b*f)/det; dv=(a*f-b*e)/det
            u=np.clip(u+du,u0,u1); v=np.clip(v+dv,v0,v1)
        return np.stack([u,v],-1)
class Torus(Surf):
    periodic_u=True; periodic_v=True
    def __init__(s,n):
        s.c=np.array(n['centre']); s.a=unit(n['axis']); s.x=unit(n['x_axis']); s.y=np.cross(s.a,s.x)
        s.R=n['major_radius']; s.r=n['minor_radius']
    def eval(s,u,v):
        u=np.asarray(u)[...,None]; v=np.asarray(v)[...,None]
        rad=(s.R+s.r*np.cos(v))
        return s.c+rad*(np.cos(u)*s.x+np.sin(u)*s.y)+s.r*np.sin(v)*s.a
    def inv(s,P):
        d=np.asarray(P)-s.c; px=d@s.x; py=d@s.y
        return np.stack([np.arctan2(py,px),np.arctan2(d@s.a,np.hypot(px,py)-s.R)],-1)
class Sphere(Surf):
    periodic_u=True
    def __init__(s,n):
        s.c=np.array(n['centre']); s.r=n['radius']; s.a=unit(n['axis']); s.x=unit(n['x_axis']); s.y=np.cross(s.a,s.x)
    def eval(s,u,v):
        u=np.asarray(u)[...,None]; v=np.asarray(v)[...,None]
        return s.c+s.r*(np.cos(v)*(np.cos(u)*s.x+np.sin(u)*s.y)+np.sin(v)*s.a)
    def inv(s,P):
        d=np.asarray(P)-s.c; px=d@s.x; py=d@s.y
        return np.stack([np.arctan2(py,px),np.arctan2(d@s.a,np.hypot(px,py))],-1)
class NoEval(Surf):
    """surface type we cannot evaluate (e.g. rolling-ball blend): faces are meshed from their boundary only"""
    def __init__(s,n): s.kind=n['name']
def make_surface(N,nid):
    n=N[nid]; t=n['name']
    if t=='PLANE': return Plane(n)
    if t=='CYLINDER': return Cylinder(n)
    if t=='CONE': return Cone(n)
    if t=='B_SURFACE': return BSurf(N,n)
    if t=='TORUS': return Torus(n)
    if t=='SPHERE': return Sphere(n)
    if t=='BLENDED_EDGE': return NoEval(n)
    raise NotImplementedError(t)
