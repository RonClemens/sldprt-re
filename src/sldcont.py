import struct, zlib, re
M=bytes.fromhex('140006000800')
def rol(b,k): return bytes(((c<<k)|(c>>(8-k)))&0xff for c in b) if k else b
def read_streams(path,want=None):
    return read_streams_bytes(open(path,'rb').read(),want)
def read_streams_bytes(d,want=None):
    """streams of a SolidWorks container held in memory (also used for the files embedded as VirtualComp/* streams)"""
    key=d[7]&7
    out={}
    for m in re.finditer(re.escape(M), d):
        mp=m.start()
        f1,csz,usz,nsz=struct.unpack_from('<IIII', d, mp+10)
        if nsz>300 or nsz==0: continue
        nm=rol(d[mp+26:mp+26+nsz],key)
        if any(c<0x20 or c>=0x80 for c in nm): continue
        name=nm.decode()
        if want and not want(name): continue    # skip inflating streams the caller does not need
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
