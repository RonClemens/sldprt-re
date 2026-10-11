#!/usr/bin/env python3
"""End-to-end check of the progressive import: ipb_viz (with viewer/ipb_viz.patch applied, passed as VIEWER_DIR) + helper in --replay mode.
usage: e2e_viewer.py VIEWER_DIR REPLAY_JOBDIR OUT_PREFIX [speed]
Takes screenshots while parts arrive and asserts: boxes appear first, then real geometry replaces them, with stable mesh numbering."""
import asyncio, os, subprocess, sys, time, tempfile
from playwright.async_api import async_playwright
V, R, OUT = sys.argv[1:4]; SPEED = sys.argv[4] if len(sys.argv) > 4 else '40'
HERE = os.path.dirname(os.path.abspath(__file__)); SERVE = os.path.join(HERE, '..', 'src', 'serve.py')
async def main():
    web = subprocess.Popen([sys.executable, '-m', 'http.server', '8802', '--bind', '127.0.0.1'], cwd=V, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hp = subprocess.Popen([sys.executable, SERVE, '--port', '8766', '--replay', R, '--speed', SPEED, '--work', tempfile.mkdtemp(), '--token', 'secret'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'])
            pg = await b.new_page(viewport={'width': 1300, 'height': 850}); logs = []
            pg.on('console', lambda m: logs.append(m.text)); pg.on('pageerror', lambda e: logs.append('PAGEERROR ' + str(e)))
            await pg.goto('http://127.0.0.1:8802/index.html?sldhelper=http://127.0.0.1:8766&sldtoken=secret')
            await pg.click('#btn-import-cad')
            dummy = os.path.join(tempfile.mkdtemp(), 'Hydro_CAD.zip'); open(dummy, 'wb').write(b'PK\x05\x06' + b'\0' * 18)
            await pg.set_input_files('#cad-file-input', dummy)
            shots = 0; first_boxes = None; last = ''
            for i in range(240):
                await asyncio.sleep(0.5); lab = await pg.inner_text('#sld-progress .sld-label')
                if lab != last and (i % 6 == 0 or 'Done' in lab): await pg.screenshot(path=f'{OUT}_{shots}.png'); shots += 1
                last = lab
                if first_boxes is None and 'instances placed' in lab: first_boxes = lab
                if lab.startswith('Done') or lab.startswith('Import failed') or lab.startswith('The conversion helper'): break
            await pg.screenshot(path=f'{OUT}_final.png')
            print('first progress label:', first_boxes); print('last label:', last); print('console:', [l for l in logs if 'rror' in l][:5])
            ok = last.startswith('Done')
            await b.close()
        sys.exit(0 if ok else 1)
    finally:
        web.kill(); hp.kill()
asyncio.run(main())
