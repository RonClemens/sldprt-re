"""GLB inspector: per-mesh tris, bbox (mm), volume (mm3), open/non-manifold edges; compares BIN chunks."""
import sys, json, struct, numpy as np
def load(p):
    d=open(p,'rb').read(); off=12; ch={}
    while off<len(d):
        L,t=struct.unpack_from('<II',d,off); ch[t]=d[off+8:off+8+L]; off+=8+L
    return json.loads(ch[0x4E4F534A]), ch.get(0x004E4942,b'')
CT={5126:('f',4),5125:('I',4),5123:('H',2)}; NC={'SCALAR':1,'VEC3':3,'VEC2':2,'VEC4':4}
def acc(g,b,i):
    a=g['accessors'][i]; bv=g['bufferViews'][a['bufferView']]; f,s=CT[a['componentType']]; n=NC[a['type']]
    o=bv.get('byteOffset',0)+a.get('byteOffset',0)
    return np.frombuffer(b,dtype='<'+f+str(s) if False else np.dtype('<'+{'f':'f4','I':'u4','H':'u2'}[f]),count=a['count']*n,offset=o).reshape(-1,n) if n>1 else np.frombuffer(b,dtype=np.dtype('<'+{'f':'f4','I':'u4','H':'u2'}[f]),count=a['count'],offset=o)
def mstats(g,b,mi):
    out=[]
    for pr in g['meshes'][mi]['primitives']:
        P=acc(g,b,pr['attributes']['POSITION']).astype(float)*1000; I=acc(g,b,pr['indices']).reshape(-1,3)
        # weld by position for topology
        q=np.round(P,4); _,inv=np.unique(q,axis=0,return_inverse=True); T=inv.ravel()[I]
        deg=int(((T[:,0]==T[:,1])|(T[:,1]==T[:,2])|(T[:,0]==T[:,2])).sum())
        e=np.sort(np.concatenate([T[:,[0,1]],T[:,[1,2]],T[:,[2,0]]]),axis=1); _,c=np.unique(e,axis=0,return_counts=True)
        v=P[I]; vol=np.einsum('ij,ij->i',v[:,0],np.cross(v[:,1],v[:,2])).sum()/6
        out.append(dict(tris=len(I),open=int((c==1).sum()),nonmanifold=int((c>2).sum()),degenerate=deg,vol=round(vol,3),bbox=np.round(P.max(0)-P.min(0),4).tolist()))
    return out
if __name__=='__main__':
    g,b=load(sys.argv[1])
    for i,m in enumerate(g['meshes']): print(m.get('name',i), mstats(g,b,i))
    print('nodes',len(g.get('nodes',[])),'meshes',len(g['meshes']),'extensions',g.get('extensionsUsed'))
    if len(sys.argv)>2:
        g2,b2=load(sys.argv[2]); print('BIN identical:',b==b2)
