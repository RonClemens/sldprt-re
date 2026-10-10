"""Derive kinematic joints from decoded mates.
A revolute joint = a Concentric mate between two components (axis = the cylinder axis, in world coordinates);
limits/angles come from LimitAngle / Angle mates between the same pair of top-level components."""
import numpy as np, math
from mates import comp_path
def derive_joints(mates):
    joints=[]
    for m in mates:
        if m['type']!='concentric': continue
        f=[e.get('face') for e in m['entities']]
        if len(f)!=2 or not all(f) or not all(x['kind'] in ('CYLINDER','CONE') for x in f): continue
        a,b=(e.get('top') or comp_path(e['component'])[0] for e in m['entities'])
        d=f[0]['dir']/np.linalg.norm(f[0]['dir'])
        joints.append(dict(mate=m['name'],parent=a,child=b,axis_point=f[0]['point'].tolist(),axis_dir=d.tolist(),type='revolute',
                           angle_value=None,limit_upper=None,limit_lower=None,limit_mate=None))
    for j in joints:
        for m in mates:
            if m['type']=='limitangle' and {e.get('top') or comp_path(e['component'])[0] for e in m['entities']}=={j['parent'],j['child']}:
                j['limit_mate']=m['name']; j['angle_value']=m['params']['D1']
                if 'limit_bounds_rad' in m: j['limit_upper'],j['limit_lower']=m['limit_bounds_rad']
    return joints
