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
            s.pu,s.pv=bool(n['u_periodic']),bool(n['v_periodic'])    # periodic directions carry the full n+d+1 knot vector, like periodic curves (see below)
            s.urange=(s.ku[s.du],s.ku[-s.du-1]); s.vrange=(s.kv[s.dv],s.kv[-s.dv-1])
        else:
            s.n=n['n_vertices']; P=P.reshape(s.n,vd)
            s.k=s.expand(N[n['knots']]['knots'],N[n['knot_mult']]['mult'])
            s.d=n['degree']; s.periodic=n['periodic']
            # periodic curves carry the full n+d+1 knot vector (unclamped, uniform) and wrapped control points, so the usual evaluation on [k[d],k[n]] is the whole closed curve
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
        from scipy.spatial import cKDTree
        s.tree=cKDTree(s.gP)    # nearest grid point = Newton seed (replaces a brute-force distance matrix)
    def eval(s,u,v):
        u,v=np.broadcast_arrays(np.asarray(u,float),np.asarray(v,float)); sh=u.shape
        return s.nb.surface(u.ravel(),v.ravel()).reshape(sh+(3,))
    def inv(s,P,iters=12):
        P=np.atleast_2d(np.asarray(P,float))
        k=s.tree.query(P)[1]
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
def curve_fn(N,cid):
    """section / profile curve of a swept or spun surface -> (eval(u)->(m,3), (u0,u1), closed). Unsupported curve types raise NotImplementedError."""
    n=N[cid]; t=n['name']
    if t=='TRIMMED_CURVE': return curve_fn(N,n['basis_curve'])
    if t=='CIRCLE':
        c=np.array(n['centre']); nz=unit(n['normal']); x=unit(n['x_axis']); y=np.cross(nz,x); r=n['radius']
        return (lambda u: c+r*(np.cos(np.asarray(u,float))[...,None]*x+np.sin(np.asarray(u,float))[...,None]*y)),(0.0,TWO),True
    if t=='ELLIPSE':
        c=np.array(n['centre']); nz=unit(n['normal']); x=unit(n['x_axis']); y=np.cross(nz,x); A=n['major_radius']; B=n['minor_radius']
        return (lambda u: c+A*np.cos(np.asarray(u,float))[...,None]*x+B*np.sin(np.asarray(u,float))[...,None]*y),(0.0,TWO),True
    if t=='B_CURVE':
        nb=Nurbs(N,n['nurbs'],False); closed=bool(getattr(nb,'periodic',False)) or bool(np.allclose(nb.curve(nb.range[0]),nb.curve(nb.range[1]),atol=1e-9))
        return (lambda u: nb.curve(u)),(float(nb.range[0]),float(nb.range[1])),closed
    raise NotImplementedError('section curve '+t)
TWO=2*np.pi
class Swept(Surf):
    """extrusion of a section curve along a (unit) sweep direction: P(u,v)=C(u)+v*d"""
    def __init__(s,N,n):
        f0,(a0,a1),s.closed=curve_fn(N,n['section']); s.d=unit(n['sweep']); s.periodic_u=s.closed
        if s.closed:     # a closed section is re-parametrised to one full period (2*pi), so the periodic-surface machinery (unwrap, strips, refinement) applies unchanged
            s.f=lambda u,f0=f0,a0=a0,k=(a1-a0)/TWO: f0(a0+np.mod(np.asarray(u,float),TWO)*k); s.u0,s.u1=0.0,TWO
        else: s.f=f0; s.u0,s.u1=a0,a1
        m=2049 if s.closed else 1025; s.ug=np.linspace(s.u0,s.u1,m,endpoint=not s.closed); s.cp=s.f(s.ug); s.step=(s.u1-s.u0)/m
        from scipy.spatial import cKDTree
        s.tree=cKDTree(s.cp-(s.cp@s.d)[:,None]*s.d)    # the section seen along the sweep direction
        if s.closed: pc=np.vstack([s.cp,s.cp[:1]]); s.r=float(np.linalg.norm(np.diff(pc,axis=0),axis=1).sum()/TWO)    # arc length per radian (= the radius for a circle)
    def perp(s,u): C=s.f(u); return C-(C@s.d)[...,None]*s.d if np.ndim(C)==1 else C-(C@s.d)[:,None]*s.d
    def eval(s,u,v):
        u,v=np.broadcast_arrays(np.asarray(u,float),np.asarray(v,float)); return s.f(u)+v[...,None]*s.d
    def inv(s,P):
        P=np.atleast_2d(np.asarray(P,float)); Pp=P-(P@s.d)[:,None]*s.d
        u=s.ug[s.tree.query(Pp)[1]].copy(); h=max((s.u1-s.u0)*1e-6,1e-12)
        for _ in range(6):
            T=(s.perp(u+h)-s.perp(u-h))/(2*h); r=Pp-s.perp(u)
            u=u+np.clip((r*T).sum(1)/np.maximum((T*T).sum(1),1e-300),-2*s.step,2*s.step)
            u=(np.mod(u-s.u0,s.u1-s.u0)+s.u0) if s.closed else np.clip(u,s.u0,s.u1)
        return np.stack([u,(P-s.f(u))@s.d],-1)
class Spun(Surf):
    """surface of revolution: the profile curve turned about the axis through `base` (angle u measured from the profile's own position)"""
    periodic_u=True
    def __init__(s,N,n):
        s.b=np.array(n['base']); s.a=unit(n['axis']); s.line=None
        x=np.array(n['x_axis']); x=x-(x@s.a)*s.a; s.x=unit(x); s.y=np.cross(s.a,s.x)
        c=N[n['profile']]
        while c['name']=='TRIMMED_CURVE': c=N[c['basis_curve']]
        if c['name']=='LINE' and s._init_line(c): return
        s.sphere=None
        if c['name']=='CIRCLE':     # a circle centred on the axis, in a plane holding the axis, is a sphere: its (profile, angle) inverse double-covers the surface and jumps between the two sheets
            rel=np.array(c['centre'])-s.b
            if np.linalg.norm(rel-(rel@s.a)*s.a)<1e-9 and abs(unit(c['normal'])@s.a)<1e-9:
                s.sphere=Sphere(dict(centre=np.array(c['centre']),radius=c['radius'],axis=s.a,x_axis=s.x))
        s.f,(s.v0,s.v1),s.pclosed=curve_fn(N,n['profile'])
        m=2049 if s.pclosed else 1025; s.vg=np.linspace(s.v0,s.v1,m,endpoint=not s.pclosed); s.step=(s.v1-s.v0)/m
        rel=s.f(s.vg)-s.b; s.rz=np.stack([np.hypot(rel@s.x,rel@s.y),rel@s.a],-1)
        from scipy.spatial import cKDTree
        s.tree=cKDTree(s.rz)
    def _init_line(s,c):
        """a straight profile (cone / cylinder / plane of revolution) has an open-ended parameter range, so it is evaluated and inverted analytically:
        v is the line parameter, the profile point sits at signed radius rho0 + v*dr along the fixed radial direction of the line"""
        rel=np.array(c['pvec'])-s.b; d=unit(c['direction']); z0=rel@s.a; rp=rel-z0*s.a; dz=d@s.a; dp=d-dz*s.a; rho0=np.linalg.norm(rp)
        if rho0>1e-12 and np.linalg.norm(np.cross(rp,dp))>1e-9*rho0*max(np.linalg.norm(dp),1e-30): return False    # skew to the axis: a hyperboloid
        rh=rp/rho0 if rho0>1e-12 else (dp/np.linalg.norm(dp) if np.linalg.norm(dp)>1e-12 else s.x)
        s.line=(z0,dz,rho0,d@rh,np.arctan2(rh@s.y,rh@s.x)); s.pclosed=False; s.v0,s.v1=0.0,0.0
        return True
    def _rzang(s,v):
        rel=s.f(v)-s.b; px=rel@s.x; py=rel@s.y; return np.hypot(px,py),rel@s.a,np.arctan2(py,px)
    def eval(s,u,v):
        u,v=np.broadcast_arrays(np.asarray(u,float),np.asarray(v,float))
        if s.line:
            z0,dz,rho0,dr,a0=s.line; r=rho0+v*dr; a=a0+u
            return s.b+(z0+v*dz)[...,None]*s.a+r[...,None]*(np.cos(a)[...,None]*s.x+np.sin(a)[...,None]*s.y)
        r,z,a0=s._rzang(v); a=a0+u
        return s.b+z[...,None]*s.a+r[...,None]*(np.cos(a)[...,None]*s.x+np.sin(a)[...,None]*s.y)
    def inv(s,P):
        P=np.atleast_2d(np.asarray(P,float)); rel=P-s.b; px=rel@s.x; py=rel@s.y
        if s.line:
            z0,dz,rho0,dr,a0=s.line; z=rel@s.a; rho=np.hypot(px,py)
            if abs(dz)>1e-9: v=(z-z0)/dz; neg=(rho0+v*dr)<0    # which nappe of the cone the point is on
            else: v=(rho-rho0)/dr; neg=np.zeros(len(P),bool)
            u=np.arctan2(py,px)-a0+np.where(neg,np.pi,0.0); return np.stack([(u+np.pi)%TWO-np.pi,v],-1)
        q=np.stack([np.hypot(px,py),rel@s.a],-1)
        v=s.vg[s.tree.query(q)[1]].copy(); h=max((s.v1-s.v0)*1e-6,1e-12)
        for _ in range(6):
            r0,z0,_=s._rzang(v); r1,z1,_=s._rzang(v+h); r2,z2,_=s._rzang(v-h); T=np.stack([r1-r2,z1-z2],-1)/(2*h)
            d=q-np.stack([r0,z0],-1); v=v+np.clip((d*T).sum(1)/np.maximum((T*T).sum(1),1e-300),-2*s.step,2*s.step)
            v=(np.mod(v-s.v0,s.v1-s.v0)+s.v0) if s.pclosed else np.clip(v,s.v0,s.v1)
        _,_,a0=s._rzang(v); u=np.arctan2(py,px)-a0; u=(u+np.pi)%TWO-np.pi
        return np.stack([u,v],-1)
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
    if t=='SWEPT_SURF': return Swept(N,n)
    if t=='SPUN_SURF': return Spun(N,n)
    if t=='BLENDED_EDGE': return NoEval(n)
    raise NotImplementedError(t)
