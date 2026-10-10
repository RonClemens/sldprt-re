"""Load each model file into sandbox.babylonjs.com via its file input; iso camera; screenshot; report scene stats + console errors."""
import sys, asyncio, json, os
from playwright.async_api import async_playwright
STATS = """() => { const s = BABYLON.EngineStore.LastCreatedScene; if(!s) return null;
 const ms = s.meshes.filter(m => m.getTotalVertices() > 0 && !/sky|ground|BackgroundHelper/i.test(m.name));
 let mn=[1e9,1e9,1e9], mx=[-1e9,-1e9,-1e9];
 for (const m of ms){ m.computeWorldMatrix(true); const b=m.getBoundingInfo().boundingBox;
   for(let i=0;i<3;i++){ mn[i]=Math.min(mn[i],b.minimumWorld.asArray()[i]); mx[i]=Math.max(mx[i],b.maximumWorld.asArray()[i]); } }
 const c = s.activeCamera; if (c && c.alpha!==undefined){ c.alpha = Math.PI/4; c.beta = Math.PI/3; }
 return {meshes: ms.length, tris: ms.reduce((a,m)=>a+m.getTotalIndices()/3,0), min: mn, max: mx,
         names: s.meshes.filter(m=>/sky|ground|Background/i.test(m.name)).map(m=>m.name), handedness: s.useRightHandedSystem}; }"""
async def main(files, outdir):
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"])
        res = {}
        for f in files:
            pg = await b.new_page(viewport={'width':1000,'height':750}); log=[]
            pg.on('console', lambda m, log=log: log.append(f'{m.type}: {m.text[:300]}'))
            pg.on('pageerror', lambda e, log=log: log.append(f'pageerror: {e}'))
            await pg.goto('https://sandbox.babylonjs.com/', wait_until='networkidle', timeout=90000)
            await pg.set_input_files('#files', f)
            await pg.wait_for_timeout(8000)
            st = await pg.evaluate(STATS)
            await pg.wait_for_timeout(1500)
            shot = os.path.join(outdir, os.path.splitext(os.path.basename(f))[0] + ('_obj' if f.endswith('.obj') else '') + '.png')
            await pg.screenshot(path=shot)
            res[os.path.basename(f)] = dict(stats=st, console=[l for l in log if 'v9' not in l], screenshot=shot)
            await pg.close()
        await b.close()
        print(json.dumps(res, indent=1))
asyncio.run(main(sys.argv[2:], sys.argv[1]))
