#!/usr/bin/env python3
"""Regression test on the public ROS solidworks_urdf_exporter example assemblies.
Clones the examples (once) into .cache/, converts each assembly, and checks per-part mesh metrics and mate checks
against tests/expected.json. Usage: python3 tests/regress.py [--update]"""
import json, os, re, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src'); sys.path.insert(0, os.path.join(ROOT, 'tools'))
from glbstat import load, mstats
CACHE = os.path.join(ROOT, '.cache', 'solidworks_urdf_exporter')
ASSEMBLIES = ['ORIGINAL_3_DOF_ARM/ORIGINAL_3_DOF_ARM', '4_WHEELER/4_WHEELER', '3_DOF_ARM/3_DOF_ARM']
SCHEMA = os.path.join(SRC, 'sch_13006_s_t.txt')
def run(*a): return subprocess.run([sys.executable, '-I', *a], capture_output=True, text=True, cwd=SRC)
def main():
    if not os.path.isdir(CACHE):
        subprocess.check_call(['git', 'clone', '-q', '--depth', '1', 'https://github.com/ros/solidworks_urdf_exporter.git', CACHE])
    got = {}
    with tempfile.TemporaryDirectory() as out:
        for a in ASSEMBLIES:
            asm = os.path.join(CACHE, 'examples', a + '.SLDASM'); name = os.path.basename(a)
            r = run('sldasm2glb.py', asm, '--schema', SCHEMA, '-o', out)
            if r.returncode: print(r.stdout, r.stderr); raise SystemExit(f'{name}: conversion failed')
            g, b = load(os.path.join(out, name + '.glb'))
            parts = {m['name']: mstats(g, b, i)[0] for i, m in enumerate(g['meshes'])}
            r = run('sldasm_mates.py', asm, '--schema', SCHEMA)
            ok = len(re.findall(r'offset 0\.0000 mm, misalignment 0\.0000 deg|distance -?0\.00000 mm', r.stdout)) + \
                 sum(1 for a_, b_ in re.findall(r'gap (\S+) mm, stored D1 (\S+) mm', r.stdout) if a_ == b_)
            got[name] = dict(parts={k: dict(tris=v['tris'], open=v['open'], vol=round(float(v['vol']), 1)) for k, v in parts.items()},
                             mates_validated=ok, failed_faces='failed faces [(' in run('sldasm2glb.py', asm, '--schema', SCHEMA, '-o', out).stdout)
    exp_path = os.path.join(ROOT, 'tests', 'expected.json')
    if '--update' in sys.argv or not os.path.exists(exp_path):
        json.dump(got, open(exp_path, 'w'), indent=1); print('wrote', exp_path); return
    exp = json.load(open(exp_path)); bad = 0
    for name, e in exp.items():
        g_ = got.get(name, {})
        for p, m in e['parts'].items():
            q = g_.get('parts', {}).get(p)
            if q is None or q['open'] != m['open'] or abs(q['vol'] - m['vol']) > 1e-3 * abs(m['vol']) or q['tris'] != m['tris']:
                print(f'CHANGED {name}/{p}: expected {m} got {q}'); bad += 1
        if g_.get('mates_validated', 0) < e['mates_validated']:
            print(f'REGRESSED {name}: mates validated {g_.get("mates_validated")} < {e["mates_validated"]}'); bad += 1
        if g_.get('failed_faces') and not e.get('failed_faces'):
            print(f'REGRESSED {name}: failed faces'); bad += 1
    for name, g_ in got.items():
        print(f'{name}: ' + ', '.join(f"{p} {v['tris']} tris, {v['open']} open, {v['vol']} mm3" for p, v in g_['parts'].items()) + f"; mates validated {g_['mates_validated']}")
    print('OK' if not bad else f'{bad} difference(s)'); sys.exit(1 if bad else 0)
if __name__ == '__main__': main()
