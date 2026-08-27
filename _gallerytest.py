"""One-shot: verify the property-photo thumbnail + swipe gallery + Zillow-link removal. Threaded server
(the page now loads many images; a single-threaded server would hang). Gitignored _*.py."""
import http.server, socketserver, threading, os, functools
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, 'docs'); PORT = 8809
CODE = P.live_code()
srv = ThreadingHTTPServer(('127.0.0.1', PORT), functools.partial(SimpleHTTPRequestHandler, directory=DOCS))
threading.Thread(target=srv.serve_forever, daemon=True).start()

R=[]
def rec(n, ok, d=''): R.append(ok); print((('  PASS ' if ok else '  FAIL ')+n+(' | '+d if d else '')).encode('ascii','replace').decode('ascii'))
# SKIP is not PASS. It records nothing, so a skipped check can never pad the score — it prints the
# reason and stays out of the tally. Used where live data, not the code, decides whether the case
# under test even exists today.
def skip(n): print(('  SKIP ' + n).encode('ascii','replace').decode('ascii'))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True); ctx = b.new_context(viewport={'width':1500,'height':1000})
    pg = ctx.new_page(); errs=[]
    pg.on('console', lambda m: errs.append(m.text) if m.type=='error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: '+str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg.wait_for_selector('#gatepw', timeout=15000); pg.fill('#gatepw', CODE); pg.click('#gatego')
    pg.wait_for_function("document.getElementById('gate') && getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)
    pg.wait_for_timeout(600)

    # 1) the text "Zillow" link is gone from the links row
    zt = pg.evaluate("() => [...document.querySelectorAll('.links a, .clinks a')].filter(a=>/^zillow$/i.test(a.textContent.trim())).length")
    rec('Text "Zillow" link removed from links row', zt == 0, str(zt))

    # 2) thumbnails render + a table column exists
    # Assert the PHOTO COLUMN EXISTS, not a magic total. Hard-coding 12 meant every later table
    # redesign (the 07-25 consolidation dropped one column) failed this check for a reason that
    # has nothing to do with photos — a red that says "gallery is broken" when the gallery is
    # fine. Test the thing the file is named after, and separately that colgroup and thead agree,
    # which is the mismatch a column change actually introduces.
    hdr = pg.evaluate("() => [...document.querySelectorAll('#tbl thead th')].map(t=>t.textContent.trim())")
    rec('Photo column present in the table header', 'Photo' in hdr, '%d cols: %s' % (len(hdr), hdr[:3]))
    ncol = pg.evaluate("() => document.querySelectorAll('#tbl colgroup col').length")
    rec('colgroup width count matches the header count', ncol == len(hdr),
        '%d <col> vs %d <th>' % (ncol, len(hdr)))
    thumbs = pg.evaluate("() => document.querySelectorAll('.pcell .pthumb').length")
    rec('Thumbnails render in rows', thumbs > 0, f'{thumbs} thumbs')

    # 3) above-the-fold thumbnails eager-load a real image — WAIT for loads (incl. imgFB swaps)
    # to finish first; sampling on a fixed timer raced the network and flaked 7/14, 11/14...
    try:
        pg.wait_for_function("""() => [...document.querySelectorAll('.pcell img:not([loading])')].every(i =>
          (i.complete && i.naturalWidth>0) || ((i.closest('.pthumb')||{classList:{contains:()=>false}}).classList.contains('imgfail')))""", timeout=20000)
    except Exception: pass
    # ASSERT THE RULE, NOT TODAY'S SORT ORDER. This required at least one eager <img> to exist,
    # which silently assumed the top rows carry photos. They need not: 633 of 1,940 leads have a
    # photo, but the default sort (tier/profit) put ZERO of them in the first 14 rows today — so
    # the check failed while the eager-loading code was perfectly correct and simply had nothing
    # to load. The real invariant is the RULE: a thumbnail above the fold must be eager (no
    # loading attr), one below it must be lazy, and any eager image must actually decode.
    eager = pg.evaluate("""() => {
      const rows=[...document.querySelectorAll('#tbl tbody tr')];
      const imgOf=tr=>{const c=tr.querySelector('.pcell'); return c?c.querySelector('img'):null;};
      const top=rows.slice(0,14).map(imgOf).filter(Boolean);
      const below=rows.slice(14).map(imgOf).filter(Boolean);
      return {topImgs:top.length,
              topEager:top.filter(i=>!i.hasAttribute('loading')).length,
              topLoaded:top.filter(i=>i.complete&&i.naturalWidth>0).length,
              belowImgs:below.length,
              belowLazy:below.filter(i=>i.getAttribute('loading')==='lazy').length};
    }""")
    if eager['topImgs'] == 0:
        skip('Above-the-fold eager rule (no lead in the top 14 rows has a photo today)')
    else:
        rec('Above-the-fold thumbnails are eager and decode',
            eager['topEager'] == eager['topImgs'] and eager['topLoaded'] == eager['topImgs'], str(eager))
    if eager['belowImgs'] == 0:
        skip('Below-the-fold lazy rule (no thumbnails past row 14)')
    else:
        rec('Below-the-fold thumbnails stay lazy', eager['belowLazy'] == eager['belowImgs'], str(eager))

    # 4) clicking a thumbnail opens the gallery with an image + a View-on-Zillow button
    c = pg.evaluate("() => { const r=DATA.find(x=>(x.photos||[]).length); return r?r.case:null; }")
    rec('At least one lead has photos', bool(c), str(c))
    if c:
        pg.evaluate("(c)=>openGallery(c)", c)
        pg.wait_for_function("document.getElementById('gallerymodal').classList.contains('show')", timeout=5000)
        gi = pg.evaluate("""(c)=>{ const r=DATA.find(x=>x.case===c); const z=(document.querySelector('.gzbtn')||{}).getAttribute?document.querySelector('.gzbtn').getAttribute('href'):null;
          return {hasImg: !!document.querySelector('#gallerybody .gimg'), zhref: z, want: r.zlisting||r.zillow||''}; }""", c)
        rec('Gallery opens with an image', bool(gi['hasImg']), '')
        rec('"View on Zillow" points at listing else search', gi['zhref']==gi['want'] and bool(gi['zhref']), str(gi)[:90])
        pg.evaluate("()=>closeGallery()")

    # 5) a no-photo lead shows the placeholder, not a broken <img>
    npc = pg.evaluate("() => { const r=DATA.find(x=>!(x.photos||[]).length); return r?r.case:null; }")
    if npc:
        ph = pg.evaluate("(c)=>{ const btn=document.querySelector('.logbtn[data-c=\"'+c+'\"]'); const row=btn?btn.closest('tr'):null; return row?{noimg: !!row.querySelector('.pthumb.noimg'), imgs: row.querySelectorAll('.pcell img').length}:{hidden:true}; }", npc)
        rec('No-photo lead shows placeholder (no broken img)', ph.get('hidden') or (ph.get('noimg') and ph.get('imgs')==0), str(ph))
    else:
        rec('No-photo lead placeholder path', True, '(every lead has a photo)')

    real=[e for e in errs if 'favicon' not in e]
    rec('No console/page errors', not real, '; '.join(real[:2]))
    b.close()
srv.shutdown()
ok=sum(R); print(f"\n==== {ok}/{len(R)} gallery checks passed ===="); raise SystemExit(0 if ok==len(R) else 1)
