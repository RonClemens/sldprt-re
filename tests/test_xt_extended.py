#!/usr/bin/env python3
"""Synthetic checks (no proprietary files) for bodies with more than 32767 Parasolid nodes and for >1 MiB sections.
usage: python3 -I tests/test_xt_extended.py"""
import os, struct, sys, zlib
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'src'))
from schema import load_schema
import xt
from sldprt2glb import inflate_section, MAGIC
_, base = load_schema(os.path.join(ROOT, 'src', 'sch_13006_s_t.txt'))
def ptr(i):    # writer side of the encoding documented in NOTES.md
    return struct.pack('>h', i) if i < 32768 else struct.pack('>hH', -(i - 32767), 1)
def int_values(i, vals): return struct.pack('>h', 82) + b'\xff' + struct.pack('>i', len(vals)) + ptr(i) + b''.join(struct.pack('>i', v) for v in vals)
def attribute(i, owner, fields):
    return (struct.pack('>h', 81) + b'\xff' + struct.pack('>i', len(fields)) + ptr(i) + struct.pack('>i', 7) + ptr(1) + ptr(owner) + ptr(1) * 4
            + b''.join(ptr(f) for f in fields))
# first use of a type: layout byte 255 = "base layout", so the stream has no schema-delta bytes
buf = b'PS' + struct.pack('>i', 5) + b'HELLO' + struct.pack('>i', 0) + struct.pack('>hhH', 30, 0, 0)
buf += int_values(32770, [1, 2, 3])
buf += attribute(40000, 32770, [32770, 1, 65535]) + struct.pack('>hh', 1, 0)
g = xt.parse(buf, base)
assert set(g['nodes']) == {32770, 40000}, sorted(g['nodes'])
a = g['nodes'][40000]; assert a['owner'] == 32770 and a['fields'] == [32770, 1, 65535], a
assert g['nodes'][32770]['values'] == [1, 2, 3]
assert g['end'] == len(buf)
# plain ids below 32768 are unchanged
assert xt.R(struct.pack('>h', 32767)).ptr() == 32767 and xt.R(struct.pack('>h', 12)).ptr() == 12
# continuation records after a full 1 MiB deflate block
body = os.urandom(1 << 20) + os.urandom(5000) * 3 + b'tail'
first = zlib.compress(body[:1 << 20]); rest = body[1 << 20:]
sec = MAGIC + struct.pack('<II', len(body[:1 << 20]), len(first)) + first
for k in range(0, len(rest), 4096):
    c = zlib.compress(rest[k:k + 4096]); sec += struct.pack('<II', len(rest[k:k + 4096]), len(c)) + c
sec += struct.pack('<II', 0, 0) + b'\x00\xff garbage'
assert inflate_section(sec) == body
small = MAGIC + struct.pack('<II', 10, 0) + zlib.compress(b'0123456789') + struct.pack('<II', 4, 99) + b'junk'
assert inflate_section(small) == b'0123456789'     # no continuation unless the first block is exactly 1 MiB
print('ok')
