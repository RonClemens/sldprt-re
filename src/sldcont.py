import struct, zlib, re
M=bytes.fromhex('140006000800')
def rol(b,k): return bytes(((c<<k)|(c>>(8-k)))&0xff for c in b) if k else b
def read_streams(path):
    d=open(path,'rb').read()
    key=d[7]&7
    out={}
    for m in re.finditer(re.escape(M), d):
        mp=m.start()
        f1,csz,usz,nsz=struct.unpack_from('<IIII', d, mp+10)
        if nsz>300 or nsz==0: continue
        nm=rol(d[mp+26:mp+26+nsz],key)
        if any(c<0x20 or c>=0x80 for c in nm): continue
        name=nm.decode()
        pl=d[mp+26+nsz:mp+26+nsz+csz]
        try: data=zlib.decompress(pl,-15)
        except Exception: continue
        out.setdefault(name,data)
    return out
if __name__=='__main__':
    import sys,os
    s=read_streams(sys.argv[1])
    os.makedirs('streams',exist_ok=True)
    for k,v in s.items():
        print(len(v),k)
        open('streams/'+k.replace('/','__'),'wb').write(v)
