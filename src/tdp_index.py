#!/usr/bin/env python3
"""Index and classify every page of a technical data package (a PDF of vector drawings): what each page is, how readable it is for the drawing tools, and what it refers to.

  python3 src/tdp_index.py PACKAGE.pdf OUT.json [--pages 1-40] [--workers 4] [--md OUT.md]        index_page(pdf, page) -> dict

Per page: sheet size, drawing number / revision / title read from the title block, the counts the dimension reader finds (dimensions, views, datums, GD&T frames, notes), whether the text had to be decoded from glyph indices
(fonts that are not embedded, see pdf_drawing._decode_cid) or still has undecodable symbols, the SEE DWG references, a BOM-table flag, a class with the evidence it rests on, and `link_ready` (dimensions and views were read and the sheet
has or can infer a scale, so drawing_link can try it). Classes: assembly (a BOM table or a SEE DWG list), detail-part (dimensions, no BOM), layout-no-dims (views and text but no dimensions), schematic-or-wiring, piping, list-or-cover, unreadable (no vector text), other.

The classes are heuristics on counts and keywords, meant for sorting a package, not for deciding anything about a part: each page says what it was classified on. Reads the pages one by one (about a second each), in worker processes.
Output holds the drawings' text (numbers, titles, references): keep it where the drawings may be kept; the summary printed at the end is counts only.
"""
import json, os, re, sys
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)

SEE = re.compile(r'SEE\s+(?:DRAWING|DWG)\s*#?\s*([0-9][0-9A-Z\-]{4,})', re.I)
DWG_NO = re.compile(r'\b(\d{8})\b')
WIRING = re.compile(r'\b(WIRE|WIRING|HARNESS|CABLE|CONNECTOR|SCHEMATIC|TERMINAL|BULKHEAD|PINOUT|J\d{1,3}|P\d{1,3}|TB\d)\b', re.I)
PIPING = re.compile(r'\b(P&ID|PIPING|PIPE|VALVE|FLANGE|NPT|TUBE|FITTING|ISOMETRIC)\b', re.I)
LISTISH = re.compile(r'(DRAWING\s+LIST|TABLE\s+OF\s+CONTENTS|INDEX|LIST\s+OF\s+DRAWINGS)', re.I)


def index_page(pdf, page):
    from pdf_drawing import read_pdf
    out = dict(page=page)
    try: sh = read_pdf(pdf, pages=[page], keep_lines=False).sheets[0]
    except Exception as e: return dict(page=page, error=type(e).__name__, cls='unreadable', evidence=['the page could not be read'], link_ready=False)
    notes = [n.text for n in sh.notes]; text = ' '.join(notes); up = text.upper()
    dec = next((w for w in sh.warnings if w.startswith('text decoded')), None); bad = next((w for w in sh.warnings if 'could not be decoded' in w), None)
    nd, nv = len(sh.dimensions), len(sh.views)
    refs = sorted({m.group(1).upper() for m in SEE.finditer(up)})
    nrefs = len(SEE.findall(up))
    bom = bool(re.search(r'\bDESCRIPTION\b', up) and (re.search(r'\bQTY\b|\bITEM\b', up) or nrefs >= 2)) or nrefs >= 3
    W, H = sh.size_pt; corner = [n.text for n in sh.notes if n.pos and n.pos[0] > 0.55 * W and n.pos[1] < 0.3 * H]            # the title block: the bottom right of the sheet
    own = [t for t in corner if re.fullmatch(r'\s*\d{8}(?:-ADS)?\s*', t)]                # the drawing's own number is a note on its own; the master assembly's number sits in a 'MASTER ASSEMBLY DWG #' note
    nums = DWG_NO.findall(' '.join(own)) or DWG_NO.findall(' '.join(t for t in corner if 'MASTER' not in t.upper())) or DWG_NO.findall(text); dwg = max(set(nums), key=nums.count) if nums else None
    ev = []; cls = 'other'
    if not sh.notes and not sh.dimensions: cls, ev = 'unreadable', ['no text was read from the page (raster, or outlined text)']
    elif LISTISH.search(up) and nd <= 2: cls, ev = 'list-or-cover', ['list/index wording and no dimensions']
    elif nd >= 4 and not bom: cls, ev = 'detail-part', [f'{nd} dimensions, no BOM table']
    elif bom: cls, ev = 'assembly', [f'BOM table or SEE DWG list ({nrefs} references) and {nd} dimension{"s" if nd != 1 else ""}']
    elif nd >= 4: cls, ev = 'detail-part', [f'{nd} dimensions and a table that may be a hole/revision table']
    elif len(WIRING.findall(text)) >= 6 and nd < 4: cls, ev = 'schematic-or-wiring', [f'{len(WIRING.findall(text))} wiring words, {nd} dimensions']
    elif len(PIPING.findall(text)) >= 4 and nd < 4: cls, ev = 'piping', [f'{len(PIPING.findall(text))} piping words, {nd} dimensions']
    elif nd == 0 and nv >= 1 and len(sh.notes) >= 40: cls, ev = 'layout-no-dims', [f'{nv} views, {len(sh.notes)} text items, no dimensions (a layout, exploded view or panel drawing)']
    else: ev = [f'{nd} dimensions, {nv} views, no BOM table, no strong keywords']
    if cls == 'assembly' and len(WIRING.findall(text)) >= 12: cls, ev = 'schematic-or-wiring', ev + ['but many wiring words']
    out.update(size_pt=sh.size_pt, drawing=dwg, dimensions=nd, views=nv, datums=len(sh.datums), gdt=len(sh.gdt), notes=len(sh.notes), refs=refs, bom_table=bom, cls=cls, evidence=ev,
               decoded_from_glyphs=bool(dec), undecodable_symbols=bool(bad), scale=sh.scale, scale_text=sh.scale_text, warnings=[w for w in sh.warnings if not w.startswith('text decoded')],
               link_ready=bool(nd and nv and (sh.scale or any('scale inferred' in w for w in sh.warnings))))
    return out


def _job(a): return index_page(*a)


def page_count(pdf):
    from pdfminer.pdfpage import PDFPage
    with open(pdf, 'rb') as f: return sum(1 for _ in PDFPage.get_pages(f))


def summary(rows):
    cnt = {}
    for r in rows: cnt[r['cls']] = cnt.get(r['cls'], 0) + 1
    return dict(pages=len(rows), classes=cnt, decoded_from_glyphs=sum(1 for r in rows if r.get('decoded_from_glyphs')), undecodable_symbols=sum(1 for r in rows if r.get('undecodable_symbols')),
                link_ready=sum(1 for r in rows if r.get('link_ready')), dimensions=sum(r.get('dimensions', 0) for r in rows), errors=sum(1 for r in rows if r.get('error')))


def to_markdown(rows, source=''):
    s = summary(rows); L = [f'# TDP index ({source})', '', f"{s['pages']} pages: " + ', '.join(f'{n} {c}' for c, n in sorted(s['classes'].items(), key=lambda t: -t[1])) + f". {s['dimensions']} dimensions read in all; {s['link_ready']} pages are ready for linking; "
         f"{s['decoded_from_glyphs']} needed their text decoded from glyph indices; {s['undecodable_symbols']} still have undecodable symbols.", '', '| page | drawing | class | dims | views | datums | GD&T | refs | link-ready | notes |', '|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        if r.get('error'): L.append(f"| {r['page']} | | unreadable | | | | | | no | {r['error']} |"); continue
        L.append(f"| {r['page']} | {r.get('drawing') or ''} | {r['cls']} | {r['dimensions']} | {r['views']} | {r['datums']} | {r['gdt']} | {', '.join(r['refs'][:4])}{'…' if len(r['refs']) > 4 else ''} | {'yes' if r['link_ready'] else 'no'} | "
                 + ('glyph-decoded; ' if r['decoded_from_glyphs'] else '') + ('symbols not decoded; ' if r['undecodable_symbols'] else '') + ' |')
    return '\n'.join(L) + '\n'


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0].startswith('-'): raise SystemExit(__doc__)
    opt = lambda n, d=None: a[a.index(n) + 1] if n in a else d
    n = page_count(a[0]); pages = list(range(1, n + 1))
    if opt('--pages'):
        lo, _, hi = opt('--pages').partition('-'); pages = list(range(int(lo), int(hi or lo) + 1))
    w = int(opt('--workers', 4))
    with Pool(w) as p: rows = p.map(_job, [(a[0], pg) for pg in pages], chunksize=1)
    json.dump(dict(source=os.path.basename(a[0]), pages=rows, summary=summary(rows)), open(a[1], 'w'), indent=1, default=float)
    if opt('--md'): open(opt('--md'), 'w').write(to_markdown(rows, os.path.basename(a[0])))
    print(json.dumps(summary(rows)))


if __name__ == '__main__':
    main()
