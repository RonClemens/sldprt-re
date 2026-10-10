import numpy as np
def area2(P,poly):
    s=0.0
    for i in range(len(poly)):
        a=P[poly[i]]; b=P[poly[(i+1)%len(poly)]]; s+=a[0]*b[1]-b[0]*a[1]
    return s
def cross(o,a,b): return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
def pit(p,a,b,c,eps):
    # point in triangle (inclusive-ish) for CCW abc
    return cross(a,b,p)>=-eps and cross(b,c,p)>=-eps and cross(c,a,p)>=-eps
def bridge_holes(P,outer,holes):
    outer=list(outer)
    holes=sorted(holes,key=lambda h:-max(P[i][0] for i in h))
    for h in holes:
        mi=max(range(len(h)),key=lambda k:P[h[k]][0]); M=P[h[mi]]
        # ray to +x, find nearest edge crossing
        best=None; n=len(outer)
        for k in range(n):
            a=P[outer[k]]; b=P[outer[(k+1)%n]]
            if (a[1]<=M[1]<b[1]) or (b[1]<=M[1]<a[1]):
                t=(M[1]-a[1])/(b[1]-a[1]); x=a[0]+t*(b[0]-a[0])
                if x>=M[0]-1e-12 and (best is None or x<best[0]): best=(x,k)
        if best is None: raise ValueError('hole bridge: no visible edge')
        x,k=best; I=(x,M[1]); ia,ib=outer[k],outer[(k+1)%n]
        cand=ia if P[ia][0]>P[ib][0] else ib; cpos=outer.index(cand) if False else (k if cand==ia else (k+1)%n)
        # check reflex vertices inside triangle M,I,cand
        tri=(M,I,P[cand]); 
        if cross(*tri)<0: tri=(tri[0],tri[2],tri[1])
        inside=[]
        for q in range(n):
            if q==cpos: continue
            v=P[outer[q]]
            if pit(v,tri[0],tri[1],tri[2],0) :
                pv=P[outer[q-1]]; nv=P[outer[(q+1)%n]]
                if cross(pv,v,nv)<=0: inside.append(q)
        if inside:
            def ang(q):
                v=P[outer[q]]; dx=v[0]-M[0]; dy=v[1]-M[1]; return abs(dy)/max(np.hypot(dx,dy),1e-300)
            cpos=min(inside,key=ang)
        # splice: outer[:cpos+1] + hole starting at mi (full cycle) + hole[mi] + outer[cpos:]
        hh=h[mi:]+h[:mi]
        outer=outer[:cpos+1]+hh+[hh[0]]+outer[cpos:]
    return outer
def earclip(P,outer,holes=(),cost=None):
    P=[tuple(map(float,p)) for p in P]
    if area2(P,outer)<0: outer=outer[::-1]
    hs=[]
    for h in holes:
        h=list(h)
        if area2(P,h)>0: h=h[::-1]
        hs.append(h)
    ring=bridge_holes(P,outer,hs) if hs else list(outer)
    m=len(ring)
    xs=[p[0] for p in P]; ys=[p[1] for p in P]; sc=max(max(xs)-min(xs),max(ys)-min(ys),1e-300); eps=1e-12*sc*sc
    prv=[(i-1)%m for i in range(m)]; nxt=[(i+1)%m for i in range(m)]
    alive=[True]*m; count=m
    def pt(i): return P[ring[i]]
    def convex(i): return cross(pt(prv[i]),pt(i),pt(nxt[i]))>eps
    def valid(i):
        a,b,c=prv[i],i,nxt[i]
        pa,pb,pc=pt(a),pt(b),pt(c)
        if cross(pa,pb,pc)<=eps: return False
        j=nxt[c]
        while j!=a:
            pq=pt(j)
            if pq!=pa and pq!=pb and pq!=pc and pit(pq,pa,pb,pc,-eps): return False
            j=nxt[j]
        return True
    def score(i):
        return cost(ring[prv[i]],ring[i],ring[nxt[i]]) if cost else 0.0
    sco={}
    for i in range(m):
        if valid(i): sco[i]=score(i)
    tris=[]
    while count>3:
        if not sco:
            # rebuild (vertices may have been unblocked)
            for i in range(m):
                if alive[i] and valid(i): sco[i]=score(i)
            if not sco:
                # fallback: most convex alive vertex
                best=max((i for i in range(m) if alive[i]),key=lambda i:cross(pt(prv[i]),pt(i),pt(nxt[i])))
                sco[best]=0.0
        i=min(sco,key=sco.get); del sco[i]
        a,c=prv[i],nxt[i]
        tris.append((ring[a],ring[i],ring[c]))
        alive[i]=False; count-=1; nxt[a]=c; prv[c]=a
        for k in (a,c):
            sco.pop(k,None)
            if alive[k] and valid(k): sco[k]=score(k)
    rest=[i for i in range(m) if alive[i]]
    if len(rest)==3: tris.append(tuple(ring[i] for i in rest))
    return tris

if __name__=='__main__':
    P=[(0,0),(4,0),(4,4),(0,4),(1,1),(1,3),(3,3),(3,1)]
    t=earclip(P,[0,1,2,3],[[4,5,6,7]]); print(len(t)); 
    A=sum(abs(cross(P[a],P[b],P[c]))/2 for a,b,c in t); print(A)
