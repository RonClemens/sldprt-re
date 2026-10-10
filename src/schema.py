def load_schema(path):
    L=open(path,encoding='latin1').read().split('\n')
    i=[k for k,l in enumerate(L) if l.startswith(': SCHEMA FILE')][0]
    counts=L[i+1]; i+=2
    types={}
    while i<len(L) and L[i].strip() and not L[i].startswith("**"):
        head=L[i]; i+=1
        a,rest=head.split(' ',1)
        parts=rest.split(';')
        name=parts[0].strip(); desc=parts[1].strip()
        tr,nf,var=[int(x) for x in parts[2].split()]
        fields=[]
        for _ in range(nf):
            fl=L[i]; i+=1
            p=fl.split(';')
            ftr,ncls,nel=[int(x) for x in p[2].split()]
            fields.append(dict(name=p[0].strip(),code=p[1].strip(),transmitted=ftr,nodeclass=ncls,n=nel))
        types[int(a)]=dict(id=int(a),name=name,desc=desc,transmitted=tr,variable=var,fields=fields)
    return counts,types
if __name__=='__main__':
    c,t=load_schema('/mnt/user-data/uploads/sch_13006_s_t.txt')
    print(c,len(t),max(t))
    for k in (101,13,14,15,16,17,29,30,31,32,50,51,53,60,62,67,81,82):
        if k in t: print(k,t[k]['name'],t[k]['transmitted'],t[k]['variable'],[(f['name'],f['code'],f['transmitted'],f['n']) for f in t[k]['fields']])
