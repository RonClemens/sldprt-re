#!/usr/bin/env python3
"""Decode the mates of a SLDASM, check them geometrically against the placed components, derive revolute joints.
usage: sldasm_mates.py assembly.SLDASM --schema sch_13006.s_t [-o mates.json]"""
import argparse, json, os, sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from mates_resolve import resolve, check
from joints import derive_joints
def jsonable(o):
    if isinstance(o,np.ndarray): return o.tolist()
    if isinstance(o,(np.floating,)): return float(o)
    if isinstance(o,(np.integer,)): return int(o)
    if isinstance(o,dict): return {k:jsonable(v) for k,v in o.items() if not k.startswith('_')}
    if isinstance(o,(list,tuple)): return [jsonable(x) for x in o]
    return o
def build_report(sldasm,schema):
    folder=os.path.dirname(os.path.abspath(sldasm))
    mates,inst,models,root=resolve(sldasm,folder,schema)
    checks=dict(check(mates)); joints=derive_joints(mates)
    for m in mates: m['check']=checks[m['name']]
    return dict(assembly=os.path.basename(sldasm),mates=jsonable(mates),joints=jsonable(joints))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('sldasm'); ap.add_argument('--schema',required=True); ap.add_argument('-o','--out')
    a=ap.parse_args(); r=build_report(a.sldasm,a.schema)
    for m in r['mates']:
        extra=''
        if m.get('params') and m['params'].get('D1') is not None: extra+=f"  D1={m['params']['D1']:.6f} {'m' if m['type'] in ('distance','limitdistance') else 'rad'}"
        if m.get('limit_bounds_rad'): extra+=f"  bounds [{m['limit_bounds_rad'][1]:.4f}, {m['limit_bounds_rad'][0]:.4f}] rad"
        ins=' <-> '.join(f"{e.get('instance')}[{e.get('face',{}).get('kind','?')}]" for e in m['entities']) or '(no entities)'
        print(f"{m['name']:13s} {m['type']:11s} {ins}{extra}\n{'':13s} {m['check']}")
    print('\njoints:')
    for j in r['joints']:
        print(f"  {j['mate']}: {j['parent']} -> {j['child']} {j['type']}  axis dir {np.round(j['axis_dir'],4).tolist()} through {np.round(np.array(j['axis_point'])*1e3,3).tolist()} mm  limits {j['limit_lower']} .. {j['limit_upper']}")
    if a.out: json.dump(r,open(a.out,'w'),indent=1); print('wrote',a.out)
if __name__=='__main__': main()
