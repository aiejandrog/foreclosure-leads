"""One-shot: full verification of the redesigned tracker against the REAL production build
(docs/index.html — encrypted gate + real data + real Zillow/local photos). Gitignored _*.py.

Covers what design-preview.html cannot: actual image loading (Zillow CDN hotlinks + local img/
files + the Esri fallback chain), the gate, the digest rail, gallery, deal modal, compact mode,
mobile 390px. Run: python _redesign_verify.py
"""
import http.server, threading, os, functools, json
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright
import foreclosure_leads as F

HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, 'docs'); PORT = 8821
SHOTS = os.path.join(os.environ.get('TEMP', HERE), 'dealflow_shots'); os.makedirs(SHOTS, exist_ok=True)

# pick the first access entry; split code/phrase if the entry has both
_secret = F._load_codes()[0][1]
CODE, _, PHRASE = _secret.partition('\x1f')

srv = ThreadingHTTPServer(('127.0.0.1', PORT), functools.partial(SimpleHTTPRequestHandler, directory=DOCS))
threading.Thread(target=srv.serve_forever, daemon=True).start()

R = []
def rec(n, ok, d=''):
    R.append(ok)
    print((('  PASS ' if ok else '  FAIL ') + n + (' | ' + d if d else '')).encode('ascii', 'replace').decode('ascii'))

def unlock(pg):
    pg.wait_for_selector('#gatepw', timeout=15000)
    pg.fill('#gatepw', CODE)
    if PHRASE: pg.fill('#gatephrase', PHRASE)
    pg.click('#gatego')
    pg.wait_for_function("document.getElementById('gate') && getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)
    pg.wait_for_timeout(700)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={'width': 1500, 'height': 1000})
    pg = ctx.new_page(); errs = []
    pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: ' + str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    unlock(pg)

    # 1) data + digest rail (DATA is a top-level let — not on window — so reference it bare)
    ndata = pg.evaluate("() => DATA.length")
    rec('Real data decrypts and loads', ndata > 400, f'{ndata} leads')
    dg = pg.evaluate("() => ({tiles: document.querySelectorAll('#digest .dg').length, visible: getComputedStyle(document.getElementById('digest')).display !== 'none'})")
    rec('Today digest rail renders', dg['tiles'] >= 3 and dg['visible'], str(dg))

    # 2) thumbnails + eager above-the-fold images: WAIT for them to finish (incl. any imgFB swap), then count
    th = pg.evaluate("() => { const t=[...document.querySelectorAll('.pcell .pthumb')]; return {thumbs:t.length, eager:document.querySelectorAll('.pcell img:not([loading])').length}; }")
    # STRUCTURAL, not a magic number. This asserted `> 40` rendered rows, which fails every time the
    # money model gets MORE honest and correctly demotes leads out of the default Tier A view — e.g.
    # the county-civil gate moved 13 fantasy-equity leads to Tier C and the count fell to 39. The
    # property worth testing is "every rendered row has a thumbnail", not how many rows survive
    # today's verdicts. A separate floor just proves the view is not empty.
    _rows = pg.evaluate("() => document.querySelectorAll('#tb tr[data-case]').length")
    rec('Thumbnails render in rows', _rows > 0 and th['thumbs'] >= _rows, f"{th} rows={_rows}")
    try:
        pg.wait_for_function("""() => [...document.querySelectorAll('.pcell img:not([loading])')].every(i =>
          (i.complete && i.naturalWidth>0) || ((i.closest('.pthumb')||{classList:{contains:()=>false}}).classList.contains('imgfail')))""", timeout=20000)
    except Exception: pass
    eg = pg.evaluate("() => { const e=[...document.querySelectorAll('.pcell img:not([loading])')]; return {n:e.length, loaded:e.filter(i=>i.complete&&i.naturalWidth>0).length, failed:e.filter(i=>(i.closest('.pthumb')||{classList:{contains:()=>false}}).classList.contains('imgfail')).length}; }")
    rec('Above-the-fold thumbnails loaded real pixels', eg['n'] > 0 and eg['loaded'] == eg['n'], str(eg))

    # 3) ZILLOW CDN hotlink loads in-page (the do-not-break check)
    z = pg.evaluate("""() => new Promise(res => {
      const r = (DATA||[]).find(x => x.photo_kind==='zillow' && (x.photos||[]).length);
      if(!r) return res({skip:'no zillow-photo lead'});
      const im = new Image();
      im.onload = () => res({ok:true, w:im.naturalWidth, h:im.naturalHeight, url:r.photos[0].slice(0,70), n:(r.photos||[]).length});
      im.onerror = () => res({ok:false, url:r.photos[0].slice(0,70)});
      im.src = r.photos[0]; setTimeout(() => res({ok:false, timeout:true}), 12000);
    })""")
    rec('Zillow CDN photo loads in-page', bool(z.get('ok')), json.dumps(z)[:100])

    # 4) local img/ photo loads in-page
    lc = pg.evaluate("""() => new Promise(res => {
      const r = (DATA||[]).find(x => (x.photos||[]).some(p => p.startsWith('img/')));
      if(!r) return res({skip:'no local-photo lead'});
      const u = r.photos.find(p => p.startsWith('img/'));
      const im = new Image();
      im.onload = () => res({ok:true, w:im.naturalWidth, url:u});
      im.onerror = () => res({ok:false, url:u});
      im.src = u; setTimeout(() => res({ok:false, timeout:true}), 8000);
    })""")
    rec('Local img/ photo loads in-page', bool(lc.get('ok')), json.dumps(lc)[:90])

    # 5) gallery on a multi-photo Zillow lead: image loads, nav works, Zillow button correct
    gc = pg.evaluate("() => { const r=DATA.find(x=>x.photo_kind==='zillow' && (x.photos||[]).length>1); return r?r.case:null; }")
    rec('Multi-photo Zillow lead exists', bool(gc), str(gc))
    if gc:
        pg.evaluate("(c)=>openGallery(c)", gc)
        pg.wait_for_function("document.getElementById('gallerymodal').classList.contains('show')", timeout=5000)
        pg.wait_for_timeout(1200)
        g = pg.evaluate("""(c)=>{ const r=DATA.find(x=>x.case===c); const img=document.querySelector('#gallerybody .gimg');
          return {loaded: !!(img && img.complete && img.naturalWidth>0), count:(document.querySelector('.gcount')||{}).textContent||'',
                  z:(document.querySelector('.gzbtn')||{getAttribute:()=>null}).getAttribute('href'), want:r.zlisting||r.zillow||''}; }""", gc)
        rec('Gallery image loads real pixels', g['loaded'], str(g)[:90])
        pg.evaluate("()=>galleryNav(1)"); pg.wait_for_timeout(300)
        g2 = pg.evaluate("() => (document.querySelector('.gcount')||{}).textContent||''")
        rec('Gallery nav advances', g2 != g['count'] and '2' in g2, f'{g["count"]} -> {g2}')
        rec('Gallery "View on Zillow" href correct', g['z'] == g['want'] and bool(g['z']), (g['z'] or '')[:60])
        pg.screenshot(path=os.path.join(SHOTS, 'prod_gallery.png'))
        pg.evaluate("()=>closeGallery()")

    # 6) deal math: opens via the real .dealtog click path. NOTE — the Deal Analyzer was consolidated
    # INTO the Call Sheet (one property hub, no second overlapping modal), so .dealtog now opens
    # #csmodal with the Math section expanded. This block used to wait on #dealmodal.show and timed
    # out, which ABORTED the whole suite here and silently skipped every check below it.
    dc = pg.evaluate("() => { const els=[...document.querySelectorAll('.dealtog')]; const e=els.find(e=>{ const r=DATA.find(x=>x.case===e.dataset.c); return r && (r.photos||[]).length; }); return e?e.dataset.c:null; }")
    if dc:
        pg.click(f'.dealtog[data-c="{dc}"]')
        pg.wait_for_function("(() => { const m=document.getElementById('csmodal'); return m && getComputedStyle(m).display!=='none'; })()", timeout=8000)
        pg.wait_for_timeout(600)
        dm = pg.evaluate("(c) => { const b=document.getElementById('csbody'); const r=DATA.find(x=>x.case===c); return {len: b? b.innerHTML.length:0, inputs: document.querySelectorAll('#csbody input').length, td: !!(r&&r.st==='TD')}; }", dc)
        rec('Deal math opens via .dealtog click (property hub)', dm['len'] > 2000, dc)
        # a TAX DEED sheet legitimately has fewer inputs: no senior/junior/HOA fields (no mortgage
        # survives a tax sale) and no negotiated-offer field (you buy at the public auction bid).
        _need = 2 if dm.get('td') else 3
        rec('Deal math renders inputs in the hub', dm['inputs'] >= _need, str(dm))
        pg.screenshot(path=os.path.join(SHOTS, 'prod_dealmodal.png'))
        pg.keyboard.press('Escape'); pg.wait_for_timeout(300)

    # 7) compact density toggle
    pg.click('#denstog'); pg.wait_for_timeout(250)
    cw = pg.evaluate("() => { const t=document.querySelector('.pcell .pthumb'); return {compact: document.body.classList.contains('compact'), w: t? Math.round(t.getBoundingClientRect().width):0}; }")
    rec('Compact toggle shrinks thumbnails', cw['compact'] and 0 < cw['w'] < 130, str(cw))
    pg.click('#denstog'); pg.wait_for_timeout(250)
    cw2 = pg.evaluate("() => { const t=document.querySelector('.pcell .pthumb'); return {compact: document.body.classList.contains('compact'), w: t? Math.round(t.getBoundingClientRect().width):0}; }")
    rec('Compact toggle off restores size', (not cw2['compact']) and cw2['w'] >= 140, str(cw2))

    # 8) search: spans the ENTIRE list ignoring the active tier filter (by design, see view()), so
    # assert semantics not direction: real term matches, nonsense term empties + offers county lookup,
    # clearing restores the default Tier-A view
    pg.evaluate("() => { const q=document.getElementById('q'); q.value=''; q.dispatchEvent(new Event('input')); }")
    rows0 = pg.evaluate("() => document.querySelectorAll('#tbl tbody tr').length")
    pg.fill('#q', 'MIAMI'); pg.wait_for_timeout(400)
    rows1 = pg.evaluate("() => document.querySelectorAll('#tbl tbody tr').length")
    rec('Search matches across the whole list', rows1 > 0 and rows1 != rows0, f'default {rows0} -> MIAMI {rows1}')
    pg.fill('#q', 'XYZZYNOMATCH99'); pg.wait_for_timeout(400)
    em = pg.evaluate("() => ({rows: document.querySelectorAll('#tbl tbody tr').length, empty: !!document.querySelector('.emptycell'), lookup: !!document.querySelector('.empty-lookup')})")
    rec('Nonsense search shows empty state + county lookup CTA', em['rows'] == 1 and em['empty'] and em['lookup'], str(em))
    pg.evaluate("() => { const q=document.getElementById('q'); q.value=''; q.dispatchEvent(new Event('input')); }")
    pg.wait_for_timeout(300)
    rows2 = pg.evaluate("() => document.querySelectorAll('#tbl tbody tr').length")
    rec('Clearing search restores default view', rows2 == rows0, f'{rows2} vs {rows0}')

    # 9) fire-lock: click non-control area -> pin; click another -> moves; click again -> releases;
    # controls never pin; persists across reload
    pg.evaluate("() => { const tr=document.querySelector('#tb tr[data-case]'); tr.querySelector('td.num').dispatchEvent(new MouseEvent('click',{bubbles:true})); }")
    pf1 = pg.evaluate("() => ({pinned: !!document.querySelector('tr.pinfire'), stored: localStorage.getItem('fcPinned')})")
    rec('Fire-lock: clicking a row locks it', pf1['pinned'] == True and bool(pf1['stored']), str(pf1))
    pg.evaluate("() => { const trs=document.querySelectorAll('#tb tr[data-case]'); trs[1].querySelector('td.num').dispatchEvent(new MouseEvent('click',{bubbles:true})); }")
    pf2 = pg.evaluate("""() => { const p=document.querySelector('tr.pinfire');
      return {moved: p && p.dataset.case !== (document.querySelector('#tb tr[data-case]')||{}).dataset?.case, stored: localStorage.getItem('fcPinned')}; }""")
    rec('Fire-lock: clicking another row MOVES the lock', pf2['moved'] == True, str(pf2))
    pg.evaluate("() => { document.querySelector('tr.pinfire td.num').dispatchEvent(new MouseEvent('click',{bubbles:true})); }")
    pf3 = pg.evaluate("() => ({pinned: !!document.querySelector('tr.pinfire'), stored: localStorage.getItem('fcPinned')})")
    rec('Fire-lock: clicking the locked row releases it', pf3['pinned'] == False and not pf3['stored'], str(pf3))
    pg.evaluate("() => { const b=document.querySelector('#tb .dealtog'); b.dispatchEvent(new MouseEvent('click',{bubbles:true})); }")
    pf4 = pg.evaluate("() => !!document.querySelector('tr.pinfire')")
    rec('Fire-lock: controls never pin (deal-analysis button)', pf4 == False, '')
    pg.evaluate("() => closeDealModal()"); pg.wait_for_timeout(200)
    # persistence across reload (device-remembered gate auto-unlocks)
    pg.evaluate("() => { const tr=document.querySelector('#tb tr[data-case]'); tr.querySelector('td.num').dispatchEvent(new MouseEvent('click',{bubbles:true})); }")
    pg.reload(wait_until='domcontentloaded'); pg.wait_for_timeout(1500)
    pf5 = pg.evaluate("() => ({gate: getComputedStyle(document.getElementById('gate')).display==='none', pinned: !!document.querySelector('tr.pinfire')})")
    rec('Fire-lock: pin survives reload', pf5['gate'] == True and pf5['pinned'] == True, str(pf5))
    pg.evaluate("() => { if(document.querySelector('tr.pinfire td.num')) document.querySelector('tr.pinfire td.num').dispatchEvent(new MouseEvent('click',{bubbles:true})); }")  # leave clean

    # 10) refresh button: setup view -> token save -> fresh-data notice -> cooldown -> fire (stubbed 204)
    pg.evaluate("() => { localStorage.removeItem('fcGhTok'); localStorage.removeItem('fcRefreshAt'); }")
    pg.click('#refreshbtn'); pg.wait_for_timeout(300)
    r1 = pg.evaluate("() => document.getElementById('refreshbody').innerHTML")
    rec('Refresh: token-setup view when no token', 'fine-grained' in r1 and 'Actions = Read and write' in r1, '')
    pg.fill('#ghtokin', 'github_pat_TESTTOKEN1234567890abcdefghij')
    pg.click('#gh-tok-save'); pg.wait_for_timeout(300)
    r2 = pg.evaluate("() => ({html: document.getElementById('refreshbody').innerHTML, tok: localStorage.getItem('fcGhTok')})")
    rec('Refresh: token saved, freshness view shows', bool(r2['tok']) and ('fresh' in r2['html'] or 'Fire' in r2['html']), '')
    pg.evaluate("() => localStorage.setItem('fcRefreshAt', String(Date.now()))")
    pg.evaluate("() => openRefresh()"); pg.wait_for_timeout(300)
    r3 = pg.evaluate("() => document.getElementById('refreshbody').innerHTML")
    rec('Refresh: cooldown blocks a second fire within the hour', 'Cooldown clears in' in r3 and 'pipeline is still building' in r3, '')
    pg.evaluate("() => localStorage.setItem('fcRefreshAt', '0')")
    pg.evaluate("""() => { window.fetch = (u,o) => String(u).includes('dispatches')
      ? Promise.resolve(new Response(null, {status:204})) : Promise.reject(new Error('off')); }""")
    pg.evaluate("() => openRefresh()"); pg.wait_for_timeout(300)
    pg.evaluate("() => { const b=document.getElementById('gh-fire'); if(b) b.click(); }")
    pg.wait_for_timeout(500)
    r4 = pg.evaluate("() => ({html: document.getElementById('refreshbody').innerHTML, at: localStorage.getItem('fcRefreshAt')})")
    rec('Refresh: dispatch fires + cooldown stamp set', 'Refresh queued' in r4['html'] and r4['at'] != '0', '')
    pg.keyboard.press('Escape'); pg.wait_for_timeout(200)
    r5 = pg.evaluate("() => document.getElementById('refreshmodal').classList.contains('show')")
    rec('Refresh: Escape closes the modal', r5 == False, '')
    # restore real fetch for the rest of the suite
    pg.evaluate("() => location.reload()"); pg.wait_for_timeout(1500)

    # 11) comps: ARV line + nearest-sales rows render in the property hub; dispo pack carries ARV.
    # (Post-consolidation the math lives in #csbody, not the retired #dmbody.)
    ac = pg.evaluate("""() => { const r=DATA.find(x=>x.arv && x.arvconf);
      if(!r) return {skip:'no comps yet'};
      openDealModal(r.case);
      const modal = (document.getElementById('csbody')||{innerHTML:''}).innerHTML;
      const rows = document.querySelectorAll('.compsline .comp').length;
      closeCallSheet();
      return {case: r.case, line: modal.includes('ARV ·'), rows, conf: r.arvconf}; }""")
    rec('Comps: ARV line + nearest-sales rows in property hub', bool(ac.get('skip')) or (ac['line'] == True and ac['rows'] >= 2), str(ac)[:80])
    dp = pg.evaluate("""() => { const r=DATA.find(x=>x.arv && (x.photos||[]).length && !x._nodata);
      if(!r) return {skip:'no comped lead with photos'};
      window.print = () => {};
      exportDispoPack(r.case);
      const t = document.getElementById('pvMathRows').innerHTML;
      return {case: r.case, hasArv: t.includes('ARV ·')}; }""")
    rec('Comps: dispo pack math includes the ARV row', bool(dp.get('skip')) or dp['hasArv'] == True, str(dp)[:70])

    # 12) cadence: enroll from a row link, panel shows it, cancel works, DNC never enrolls
    ce = pg.evaluate("""() => { localStorage.removeItem('fcCadence');
      const a=[...document.querySelectorAll('.cadencegen')][0];
      if(!a) return {skip:'no cadence link'};
      a.dispatchEvent(new MouseEvent('click',{bubbles:true}));
      const st = JSON.parse(localStorage.getItem('fcCadence')||'{}');
      const k = Object.keys(st)[0];
      return {enrolled: k, status: st[k]&&st[k].status, step: st[k]&&st[k].step,
              panel: document.getElementById('cadencemodal').classList.contains('show'),
              panelTxt: document.getElementById('cadencebody').innerText.includes('step 1/4')}; }""")
    rec('Cadence: row link enrolls + panel opens with step 1/4', bool(ce.get('skip')) or (ce['enrolled'] and ce['status']=='active' and ce['step']==0 and ce['panel'] and ce['panelTxt']), str(ce)[:90])
    ce2 = pg.evaluate("""() => { const x=document.querySelector('.cadcancel'); if(x) x.dispatchEvent(new MouseEvent('click',{bubbles:true}));
      const st = JSON.parse(localStorage.getItem('fcCadence')||'{}');
      return {status: st[Object.keys(st)[0]] && st[Object.keys(st)[0]].status}; }""")
    rec('Cadence: cancel from panel', ce2['status'] == 'cancelled', str(ce2))
    ce3 = pg.evaluate("""() => { const k = Object.keys(notes).find(c=>(notes[c]||{}).status==='DO NOT CONTACT');
      if(!k) return {skip:'no DNC lead'};
      enrollCadence(k);
      return {enrolled: !!JSON.parse(localStorage.getItem('fcCadence')||'{}')[k]}; }""")
    rec('Cadence: DO NOT CONTACT never enrolls', bool(ce3.get('skip')) or (ce3['enrolled'] == False), str(ce3))
    pg.evaluate("() => { closeCadence(); localStorage.removeItem('fcCadence'); }")

    pg.screenshot(path=os.path.join(SHOTS, 'prod_desktop_top.png'))
    pg.evaluate("() => window.scrollTo(0, 900)"); pg.wait_for_timeout(400)
    pg.screenshot(path=os.path.join(SHOTS, 'prod_desktop_rows.png'))

    real = [e for e in errs if 'favicon' not in e]
    rec('No console/page errors (desktop)', not real, '; '.join(real[:3])[:160])
    b.close()

    # ---- mobile 390px ----
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    pg = ctx.new_page(); errs = []
    pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: ' + str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    unlock(pg)
    m = pg.evaluate("() => ({cards: document.querySelectorAll('#cards > *').length, cardsVis: getComputedStyle(document.getElementById('cards')).display, overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth})")
    # same fix as the thumbnail check: assert card-per-row parity, not a drifting absolute count
    _mrows = pg.evaluate("() => document.querySelectorAll('#tb tr[data-case]').length")
    rec('Mobile cards render', _mrows > 0 and m['cards'] >= _mrows and m['cardsVis'] != 'none',
        f"{m} rows={_mrows}")
    rec('No horizontal overflow on mobile', m['overflow'] <= 1, f"overflow={m['overflow']}px")
    mc = pg.evaluate("""() => new Promise(res => {
      const im = document.querySelector('#cards .pthumb img');
      if(!im) return res({found:false});
      if(im.complete && im.naturalWidth>0) return res({loaded:true});
      im.onload = () => res({loaded:true}); im.onerror = () => res({loaded:false});
      setTimeout(() => res({loaded: im.complete && im.naturalWidth>0}), 8000);
    })""")
    rec('Mobile card photo loads', bool(mc.get('loaded')), json.dumps(mc)[:60])
    pg.screenshot(path=os.path.join(SHOTS, 'prod_mobile.png'))
    real = [e for e in errs if 'favicon' not in e]
    rec('No console/page errors (mobile)', not real, '; '.join(real[:3])[:160])
    b.close()

srv.shutdown()
ok = sum(R); print(f"\n==== {ok}/{len(R)} production checks passed ===="); print('shots:', SHOTS)
raise SystemExit(0 if ok == len(R) else 1)
