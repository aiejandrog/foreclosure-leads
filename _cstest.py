"""Call-sheet audit: open the sheet on REAL leads and assert every session feature is actually present —
PLAY banner, map scout links, RealForeclose verify, county-correct records link, debt/chain, People/CyberBG
find-a-number, geocode distance, LP framing. Gitignored _*.py. Run: python _cstest.py
"""
import http.server, threading, os, functools
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright
import foreclosure_leads as F

HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, 'docs'); PORT = 8831
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
    pg.wait_for_timeout(500)

def sheet(pg, case):
    return pg.evaluate("(c)=>{ const r=DATA.find(x=>x.case===c); return r?_callSheet(r):null; }", case)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_context(viewport={'width': 1400, 'height': 1000}).new_page(); errs = []
    pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: ' + str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    unlock(pg)

    # pick representative leads by type
    picks = pg.evaluate("""() => {
      const md = DATA.filter(r=>(r.county||'MIAMI-DADE')==='MIAMI-DADE' && r.st==='FC');
      const withPhone = md.find(r=>_cleanPhones(r).length>0);
      // ...and not contact-BLOCKED: the first no-phone pick landed on a §362-stayed lead
      // (2025-002125-CA-01) whose sheet CORRECTLY suppresses find-a-number along with every other
      // channel — hunting numbers for a protected debtor is the violation. The check's promise is
      // about plain no-phone leads, so sample one whose contact gate is open. (2026-08-26)
      const noPhone = md.find(r=>_cleanPhones(r).length===0 && r.st!=='LP' && !_textContactBlocked(r));
      const withAuc = md.find(r=>r.auc);
      const withRecqs = md.find(r=>r.recqs);
      const lp = DATA.find(r=>r.st==='LP' && !r.co && r.people);   // human-owned LP: People-search IS the play
      const lpco = DATA.find(r=>r.st==='LP' && r.co);              // company/HOA LP: People-search correctly absent
      const bro = DATA.find(r=>(r.county)==='BROWARD' && r.st!=='LP');
      const pb = DATA.find(r=>(r.county)==='PALM BEACH' && r.st!=='LP');
      const g = (r)=>r?{case:r.case, play:(r._play||{}).t, county:r.county||'MIAMI-DADE'}:null;
      return {withPhone:g(withPhone), noPhone:g(noPhone), withAuc:g(withAuc), withRecqs:g(withRecqs), lp:g(lp), bro:g(bro), pb:g(pb)};
    }""")

    wp = picks['withPhone']
    if wp:
        h = sheet(pg, wp['case'])
        rec('FC sheet: PLAY banner present', 'class="csplay"' in h and 'PLAY:' in h, f"{wp['case']} play={wp['play']}")
        rec('FC sheet: map scout (Aerial/Street View/Directions)', 'Aerial' in h and 'Street View' in h and 'Directions' in h, '')
        rec('FC sheet: debt/chain (WHAT SURVIVES) present', 'WHAT SURVIVES THE SALE' in h, '')
        rec('FC sheet: distance/geocode row present', 'from ' in h and (' mi ' in h or 'mi from' in h or 'unknown' in h), '')
        rec('FC sheet: People/CyberBG find-a-number present', 'cspeople' in h and ('People' in h), '')

    wa = picks['withAuc']
    if wa:
        h = sheet(pg, wa['case'])
        rec('FC sheet w/auc: RealForeclose verify link present', 'verify on RealForeclose' in h and 'class="csverify"' in h, wa['case'])

    wr = picks['withRecqs']
    if wr:
        h = sheet(pg, wr['case'])
        rec('MD sheet: records deep-link (SearchResults?qs) present', 'SearchResults?qs=' in h, wr['case'])

    npk = picks['noPhone']
    if npk:
        h = sheet(pg, npk['case'])
        rec('No-phone sheet: shows find-a-number links (not dead-end)', 'cspeople' in h, npk['case'])

    lp = picks['lp']
    if lp:
        h = sheet(pg, lp['case'])
        rec('LP sheet: PLAY = LP-EARLY banner', 'LP-EARLY' in h and 'csplay' in h, f"{lp['case']} play={lp['play']}")
        rec('LP sheet: FRESH LIS PENDENS framing (no $0 debt wall)', 'FRESH LIS PENDENS' in h and 'WHAT SURVIVES THE SALE' not in h, '')
        rec('LP sheet: People find-a-number present (the play)', 'cspeople' in h, '')
        rec('LP sheet: no 9999-day clock', '9999' not in h, '')

    bro = picks['bro']
    if bro:
        h = sheet(pg, bro['case'])
        # the "Open the records" link (survBox) must NOT be a Miami-Dade clerk URL for a Broward lead
        md_leak = 'onlineservices.miamidadeclerk.gov' in h and 'SearchResults?qs=' not in h
        rec('Broward sheet: records link is NOT the Miami-Dade clerk', not md_leak, bro['case'])

    pb = picks['pb']
    if pb:
        h = sheet(pg, pb['case'])
        md_leak = 'onlineservices.miamidadeclerk.gov' in h and 'SearchResults?qs=' not in h
        rec('Palm Beach sheet: records link is NOT the Miami-Dade clerk', not md_leak, pb['case'])

    # Whitepages integration: on leads WITHOUT the paid WP Pro API cache, the sheet still shows the
    # free-consumer-page fallback links (reverse-address + name search). On leads WITH the API cache,
    # they're hidden in favor of the "🔒 WP Pro verified" badge (see Velima assertion below).
    # ...and CONTACTABLE: a §362-suppressed lead's sheet carries no contact links at all, so the
    # Whitepages assertions below failed about a lead the board is deliberately refusing to work.
    uncov = pg.evaluate("""() => {
      const ok = (typeof _textContactBlocked==='function') ? (r=>!_textContactBlocked(r)) : (()=>true);
      const r = DATA.find(x => x.st==='FC' && !x.wpKey && !x.co && x.owners && ok(x));
      return r ? {case: r.case, cs: _callSheet(r)} : null;
    }""")
    if uncov:
        rec('Sheet (uncovered lead): Whitepages reverse-address link present', 'whitepages.com/address/' in uncov['cs'], uncov['case'])
        rec('Sheet (uncovered lead): Whitepages name link present', 'whitepages.com/name/' in uncov['cs'], '')
    else:
        print('  SKIP (all leads are WP-covered — no uncovered lead to test the fallback link on)')
    velima = pg.evaluate("() => { const r=DATA.find(x=>x.case==='2025-016135-CA-01'); return r ? {cs:_callSheet(r), wa:_wpAddrUrl(r), wn:_wpNameUrl(r)} : null; }")
    if velima:
        rec('Velima sheet: WP reverse-address deep-link built', '/address/11410-NE-13-AVE/MIAMI-FL' in velima['wa'], velima['wa'])
        # On WP-covered leads the free-consumer-page links are intentionally hidden and replaced with
        # the "🔒 WP Pro verified above" badge (avoids the misleading upsell nag).
        rec('Velima sheet: WP Pro verified badge (not free-page link)', 'wpverified' in velima['cs'] and 'whitepages.com/address/' not in velima['cs'], '')
    rowwp = pg.evaluate("() => document.querySelectorAll('#tb a[href*=\"whitepages.com\"]').length")
    rec('Rows: Whitepages links in the dig cluster', rowwp > 0, f'{rowwp} links')

    # Chrome-parity P0 fixes verification on Velima and a BatchData lead
    velcs = pg.evaluate("() => { const r=DATA.find(x=>x.case==='2025-016135-CA-01'); return r ? {cs:_callSheet(r), defs:r.defs, orconf:r.orconf} : null; }")
    if velcs:
        rec('Velima: gov/code/tax defendant warning renders', 'CODE-LIEN SURVIVAL RISK' in velcs['cs'] and 'Miami-Dade County' in velcs['cs'], '')
        rec('Velima: THE RULE explainer renders', 'THE RULE' in velcs['cs'] and 'Wiped:' in velcs['cs'] and 'Survives:' in velcs['cs'], '')
        rec('Velima: NEXT STEP explainer renders', 'NEXT STEP' in velcs['cs'] and 'Final Judgment' in velcs['cs'], '')
    # NOTE: the Deal Analyzer was consolidated INTO the Call Sheet (Cursor commit e448de8 — one property
    # hub, no second overlapping modal). openDealModal() now redirects to openCallSheet(id,{math:true})
    # and #dmbody is empty, so this assertion reads the call-sheet HTML where the banner actually lives.
    # The BD honest-banner test only applies when a lead's chain came from BatchData and NOT from
    # Official Records — i.e. the records path never traced it. After the unattended MD Turnstile
    # unlock, most MD leads now carry a real records `conf` ('ok'/'low'/'none'), so a pure orconf=='bd'
    # lead may not exist on the board. Skip gracefully rather than fail on the improved coverage.
    bdlead = pg.evaluate("() => { const r=DATA.find(x=>x.orconf==='bd'); return r ? {case:r.case, cs:_callSheet(r)} : null; }")
    if bdlead:
        # A BD-sourced lead may (a) have zero auto-fills → falls into the scoped "Mortgage chain
        # checked (BatchData estimate)" fallback, OR (b) HAS auto-fills → the higher-tier
        # "Auto-pulled from county records" banner takes precedence. Either is honest; both are
        # WRONG only if the sheet shows the blanket "no surviving mortgage" claim. Assert the
        # negative rather than one specific phrase — the concept, not the wording.
        cs = bdlead['cs']
        honest = ('Mortgage chain checked' in cs) or ('Auto-pulled from county records' in cs)
        blanket_lie = ('no surviving mortgage or lien found' in cs)
        rec('BatchData lead: shows an honest banner (never the blanket clean-lien claim)',
            honest and not blanket_lie, bdlead['case'])
        pg.evaluate("() => closeDealModal && closeDealModal()")
    else:
        print('  SKIP BatchData honest banner test — no pure-BD leads on this board (MD Turnstile unlock covered them)')
    satlead = pg.evaluate("() => { const r=DATA.find(x=>(x.orliens||[]).some(l=>String(l.st).toUpperCase()!=='OPEN' && l.d)); return r ? {case:r.case, cs:_callSheet(r)} : null; }")
    if satlead:
        rec('Satisfied mortgages show dates (chain vintage on the sheet)', 'dsclosed-d' in satlead['cs'], satlead['case'])
    lpwp = picks['lp'] and pg.evaluate("(c)=>{ const r=DATA.find(x=>x.case===c); return _callSheet(r).includes('whitepages.com/name/'); }", picks['lp']['case'])
    rec('LP sheet: Whitepages name link (no addr yet, name-only)', bool(lpwp), '')

    # actually open one via the UI path (not just string-eval) to catch render/JS errors
    if wp:
        pg.evaluate("(c)=>openCallSheet(c)", wp['case']); pg.wait_for_timeout(400)
        shown = pg.evaluate("() => { const m=document.getElementById('csmodal'); return m && getComputedStyle(m).display!=='none' && document.getElementById('csbody').innerHTML.length>1000; }")
        rec('Call sheet opens via UI with content', bool(shown), '')
        pg.screenshot(path=os.path.join(os.environ.get('TEMP', HERE), 'cs_sheet.png'))

    # ============================================================================================
    # CONTRADICTION HUNT REGRESSION SUITE (workflow wi3g8qwgs, 2026-07-24)
    # These MUST pass forever — if they don't, some new surface is bypassing _basisOf / _netEqOf.
    # ============================================================================================
    unify = pg.evaluate("""() => {
      // Find or fabricate a testable lead: needs r.value, r._slien, r._jlien, r._btax positive,
      // and an assessable divergence between raw arv||val and gated _basisOf. Use the first FC MD
      // lead with a non-zero orjuniors or orsurv on the current board.
      const r = DATA.find(x => x.st === 'FC' && (x._chainSurv > 0 || x._jlien > 0));
      if(!r) return {skip: 'no chain-populated FC lead in this build'};
      // stash + inject typed override to prove surv derivation respects it
      const orig = JSON.parse(JSON.stringify(notes[r.case] || {}));
      notes[r.case] = notes[r.case] || {};
      notes[r.case].slien = 12345;
      notes[r.case].jlien = 6789;
      notes[r.case].btax = 4321;
      recompute();
      const rr = DATA.find(x => x.case === r.case);
      // now snapshot the four surfaces
      const csEq = (function(){
        const html = _callSheet(rr);
        // grab the NET EQUITY value from the four-boxes region
        const m = html.match(/NET EQUITY[\\s\\S]{0,400}?\\$([0-9,]+)/);
        return m ? parseInt(m[1].replace(/,/g,''),10) : null;
      })();
      // copy CS output
      let copyEq = null;
      const origCopyShare = window._copyShare;
      window._copyShare = (txt) => { const m = txt.match(/NET EQUITY:\\s+\\$([0-9,]+)/); if(m) copyEq = parseInt(m[1].replace(/,/g,''),10); };
      _copyCallSheet(rr.case);
      window._copyShare = origCopyShare;
      // Jose SMS
      const netEqOf = _netEqOf(rr);
      // DA _equity is baked at recompute, exposed via rr._equity if available; otherwise recompute path
      const daEq = (rr._equity != null) ? Math.max(0, rr._equity - (rr._btax||0) - (rr._mlien||0)) : null;
      // restore
      notes[r.case] = orig;
      recompute();
      return { case: rr.case, csEq, copyEq, netEqOf, daEq };
    }""")
    if unify.get('skip'):
        print('  SKIP unification tests:', unify['skip'])
    else:
        # rendered surfaces always Math.round the number; _netEqOf returns raw float. Compare on rounded.
        neqRound = round(unify['netEqOf'])
        rec('Unification: on-screen CS NET EQUITY matches _netEqOf (rounded)', unify['csEq'] == neqRound, f"cs={unify['csEq']} vs netEqOf={neqRound} on {unify['case']}")
        rec('Unification: Copy CS NET EQUITY matches _netEqOf (rounded)', unify['copyEq'] == neqRound, f"copy={unify['copyEq']} vs netEqOf={neqRound}")
        rec('Unification: Copy CS matches on-screen CS (no drift)', unify['csEq'] == unify['copyEq'], f"cs={unify['csEq']} vs copy={unify['copyEq']}")

    # ARV-gate: a lead where arv is >2.5x county value (Hialeah phantom-ARV) must render county in CS
    arvgate = pg.evaluate("""() => {
      const r = DATA.find(x => x.value>0 && x.arv > 2.5*x.value);
      if(!r) return {skip: 'no ARV-out-of-range lead'};
      const html = _callSheet(r);
      const valUsed = _basisOf(r);
      return {case: r.case, valsrc: r._valsrc, valUsed, arv: r.arv, val: r.value, htmlHasCountyOnly: valUsed === r.value};
    }""")
    if arvgate.get('skip'):
        print('  SKIP ARV-gate test:', arvgate['skip'])
    else:
        rec('ARV gate: 2.5x-over-county lead uses county value in _basisOf', arvgate['htmlHasCountyOnly'], f"{arvgate['case']} val={arvgate['val']} arv={arvgate['arv']} _valsrc={arvgate['valsrc']}")

    # Owner name normalization: the property hub header must go through _ownerName, never raw ALL CAPS.
    # (Post-consolidation the hub IS the call sheet — dealModalBody() now returns only the math inner,
    # so assert against _callSheet where the owner header actually renders.)
    daowner = pg.evaluate("""() => {
      const r = DATA.find(x => /[A-Z]{4,}/.test(x.owners||'') && !(x.co));
      if(!r) return {skip:'no all-caps owner'};
      const body = _callSheet(r);
      const raw  = (r.owners||'').split(';')[0].trim();
      const norm = _ownerName(raw, r);
      return {case: r.case, raw, norm,
              headerHasNorm: body.includes(norm),
              headerHasRawCaps: norm !== raw && body.includes('>'+raw+'<')};
    }""")
    if daowner.get('skip'):
        print('  SKIP owner-name test:', daowner['skip'])
    else:
        rec('Property hub header uses _ownerName (not raw ALL CAPS)',
            daowner['headerHasNorm'] and not daowner['headerHasRawCaps'],
            f"{daowner['case']}: '{daowner['norm']}'")

    # TEXT-CTA HONESTY: a lead whose every number is DNC / Do-Not-Text must NOT render the primary
    # navy Text button (texting those is prohibited — FL FTSA / TCPA). It must render the muted
    # .cstextoff "no textable number" state instead. Leads WITH a textable number keep the CTA.
    textgate = pg.evaluate("""() => {
      const hasT = DATA.find(x => textablePhones(x).length > 0 && !_textContactBlocked(x));
      const dncOnly = DATA.find(x => (x.phones||[]).length > 0 && textablePhones(x).length === 0 && !_textContactBlocked(x));
      const noNum  = DATA.find(x => (x.phones||[]).length === 0 && !_textContactBlocked(x));
      const g = r => r ? {case:r.case, bar:_messagesBarHtml(r)} : null;
      return {hasT:g(hasT), dncOnly:g(dncOnly), noNum:g(noNum)};
    }""")
    if textgate.get('hasT'):
        rec('Text CTA: textable lead gets the primary button',
            'cstextbtn' in textgate['hasT']['bar'] and 'openTextSingle' in textgate['hasT']['bar'],
            textgate['hasT']['case'])
    if textgate.get('dncOnly'):
        _bar = textgate['dncOnly']['bar']
        rec('Text CTA: DNC-only lead gets NO text button (compliance)',
            'cstextoff' in _bar and 'cstextbtn' not in _bar and 'openTextSingle' not in _bar,
            textgate['dncOnly']['case'])
    else:
        print('  SKIP DNC-only text-gate test: no DNC-only lead on this board')
    if textgate.get('noNum'):
        _bar = textgate['noNum']['bar']
        rec('Text CTA: no-number lead points at Find a number (no dead composer)',
            'cstextoff' in _bar and 'cstextbtn' not in _bar, textgate['noNum']['case'])

    # LINE-TYPE GATE: no number Whitepages typed as a landline may be offered as textable. An SMS to
    # a landline is delivered nowhere, so the operator believes he reached an owner he never did.
    # (Board-wide this was 119 numbers before the gate landed.)
    land = pg.evaluate("""() => {
      let bad = 0, sample = null;
      DATA.forEach(r => { const tp = textablePhones(r), t = r.phtype||[];
        (r.phones||[]).forEach((ph,i) => { const d = String(ph).replace(/\\D/g,'');
          if(tp.indexOf(d) > -1 && String(t[i]||'').toLowerCase().indexOf('land') > -1){ bad++; sample = sample || (r.case+' '+d); } }); });
      return {bad, sample};
    }""")
    rec('Line-type gate: zero landlines offered as textable', land['bad'] == 0, f"{land['bad']} leaked" + (f" e.g. {land['sample']}" if land['sample'] else ''))

    # WP PHONE SHAPE: r.phones must be bare digit STRINGS (skiptrace shape) with metadata in the
    # parallel phtype/phdnc arrays. Dicts stringify to "[object Object]" -> 0 digits -> the contact
    # line silently renders EMPTY, dropping paid-for numbers from Call/Text/WA/CSV (was 116 numbers).
    shape = pg.evaluate("""() => {
      let dict = 0, dead = 0, misaligned = 0;
      DATA.forEach(r => {
        (r.phones||[]).forEach(ph => { if(ph && typeof ph === 'object'){ dict++;
          if(String(ph).replace(/\\D/g,'').length === 0) dead++; } });
        if((r.phones||[]).length && (r.phtype||[]).length && r.phtype.length < r.phones.length) misaligned++;
      });
      return {dict, dead, misaligned};
    }""")
    rec('WP phones bake as strings (no [object Object] dead lines)', shape['dict'] == 0 and shape['dead'] == 0, str(shape))

    # SUPPRESSION GATE: opted-out / wrong-person / active-BK-stay leads must never render a tappable
    # number anywhere — the contact block already blocks, but the WP household section did not (9 leads).
    supp = pg.evaluate("""() => {
      const s = DATA.filter(r => _textContactBlocked(r));
      let leaks = 0, sample = null;
      s.forEach(r => { if((r.wpOwners||[]).length && _wpHousehold(r).indexOf('tel:') > -1){ leaks++; sample = sample || r.case; } });
      return {suppressed: s.length, leaks, sample};
    }""")
    rec('Suppressed leads leak no tel: links in WP household', supp['leaks'] == 0,
        f"{supp['leaks']} of {supp['suppressed']} suppressed" + (f" e.g. {supp['sample']}" if supp['sample'] else ''))

    # RESIDENTS: the paid-for household array must actually reach the sheet, labeled as NOT the owner.
    resid = pg.evaluate("""() => {
      const w = DATA.filter(r => (r.wpResidents||[]).length && !_textContactBlocked(r));
      if(!w.length) return {skip:'no resident data baked'};
      const r = w[0], h = _wpHousehold(r);
      let ph = 0; w.forEach(x => (x.wpResidents||[]).forEach(o => ph += (o.phones||[]).length));
      return {leads: w.length, phones: ph, case: r.case,
              rendered: h.indexOf('ALSO AT THIS ADDRESS') > -1,
              labeled: h.indexOf('not the deed owner') > -1};
    }""")
    if resid.get('skip'):
        print('  SKIP residents test:', resid['skip'])
    else:
        rec('WP residents bake + render as non-owner household',
            resid['rendered'] and resid['labeled'],
            f"{resid['leads']} leads, {resid['phones']} resident phones")

    # OUTGOING MESSAGES must quote the SAME value the deal math used. _joseMsg/_liveMsg used to read
    # raw r.arv, so a lead whose ARV the analyzer REJECTED (low conf, or outside the 0.7x-2.5x county
    # band) still went to Jose as "(comp ARV)" — a phantom number in a message sent to a partner.
    arvmsg = pg.evaluate("""() => {
      const r = DATA.find(x => x.value > 0 && x.arv > 2.5 * x.value);
      if(!r) return {skip:'no out-of-band ARV lead'};
      const jm = _joseMsg(r), lm = _liveMsg(r);
      const phantom = Math.round(r.arv).toLocaleString(), county = Math.round(r.value).toLocaleString();
      return {case:r.case, joseOk: jm.indexOf(phantom) === -1 && jm.indexOf(county) > -1,
                           liveOk: lm.indexOf(phantom) === -1 && lm.indexOf(county) > -1};
    }""")
    if arvmsg.get('skip'):
        print('  SKIP outgoing-ARV test:', arvmsg['skip'])
    else:
        rec('Jose brief quotes the gated basis, not a rejected ARV', arvmsg['joseOk'], arvmsg['case'])
        rec('LIVE alert quotes the gated basis, not a rejected ARV', arvmsg['liveOk'], arvmsg['case'])

    # SEEN vs FILED — and WORKED vs GLANCED (operator's own workflow, 2026-07-27):
    #   viewed = "I WORKED it" — stamped ONLY by a contact-intent click (tel:/sms:, a people-search
    #            link, the Text CTA). Opening the hub / map / photos stamps NOTHING. His words: "I'm
    #            just looking for the number. I'm pressing the white page link... to get a human on
    #            the phone" — and he mostly never opens the hub at all, so hub-open-as-seen marked
    #            leads he never worked while leaving no trace on the ones he actually did.
    #   filed  = "I'm done with it" (explicit Seen tap) -> what Hide viewed actually removes
    seen = pg.evaluate("""() => new Promise(res => {
      const row = document.querySelector('#tb tr[data-case]');
      const c = row.dataset.case;
      viewed.delete(c); filed.delete(c);
      const rowsBefore = document.querySelectorAll('#tb tr[data-case]').length;
      openCallSheet(c); closeCallSheet();                       // LOOKING: must stamp nothing now
      const out = {hubStampsNothing: !viewed.has(c), notFiled: !filed.has(c)};
      // WORKING: a people-search click must stamp the lead. Where those links LIVE moved twice
      // (2026-08-26 audit): the row's links sub-row lazy-renders AND renders EMPTY of anchors on
      // the current build — the find-a-number chips now live on the CALL SHEET (_csPeople), and
      // the delegated stamper resolves the open sheet via callSheetCase. Test the path the
      // operator actually uses: open a sheet that carries people links, click one there.
      let c2 = null, link = null, map = null;
      const cand = DATA.find(x => x.people && !_textContactBlocked(x));
      if(cand){
        c2 = cand.case;
        openCallSheet(c2);
        link = [...document.querySelectorAll('.cspeople a[href]')].find(a =>
          /truepeoplesearch\\.com|whitepages\\.com|cyberbackgroundchecks\\.com/i.test(a.href));
        map = [...document.querySelectorAll('a[href]')].find(a => /google\\.com\\/maps/i.test(a.href));
        if(link){
          viewed.delete(c2);
          link.addEventListener('click', ev => ev.preventDefault(), {once:true});   // don't leave the page
          link.click();
        }
      }
      out.linkFound = !!link;
      out.contactStamps = !!c2 && viewed.has(c2);
      // a map click (looking, not working) must NOT stamp
      if(c2) viewed.delete(c2);
      if(map){ map.addEventListener('click', ev => ev.preventDefault(), {once:true}); map.click(); }
      out.mapFound = !!map;
      out.mapStampsNothing = !c2 || !viewed.has(c2);
      if(c2) closeCallSheet();
      // give the listener's delayed render() a beat, then check nothing vanished + the split holds
      setTimeout(() => {
        _markViewed(c); render();                                // simulate a worked lead
        const stillListed = !!document.querySelector('#tb tr[data-case="'+CSS.escape(c)+'"]');
        out.survivesHideViewed = stillListed &&
          document.querySelectorAll('#tb tr[data-case]').length === rowsBefore;
        out.hasChip = _seenChip(DATA.find(x=>x.case===c)).indexOf('seen') > -1;
        _toggleViewed(c);                                        // explicit tap -> now it files
        out.tapFiles = filed.has(c);
        out.hiddenAfterTap = !document.querySelector('#tb tr[data-case="'+CSS.escape(c)+'"]');
        _toggleViewed(c); render();                              // restore
        res(out);
      }, 600);
    })""")
    rec('Seen: opening the hub stamps NOTHING (looking is not working)',
        seen['hubStampsNothing'] and seen['notFiled'], '')
    rec('Seen: a people-search click stamps the lead as worked',
        seen['linkFound'] and seen['contactStamps'], '' if seen['linkFound'] else 'no people link found on first row')
    rec('Seen: a map click stamps nothing', (not seen['mapFound']) or seen['mapStampsNothing'], '')
    rec('Seen: a worked deal does NOT hide', seen['survivesHideViewed'], 'row must survive Hide viewed')
    rec('Seen: the row shows a "seen" chip', seen['hasChip'], '')
    rec('Seen: tapping Seen files it and hides it', seen['tapFiles'] and seen['hiddenAfterTap'], '')

    # STALE-VIEW RE-SURFACE: a filed lead whose auction is now inside 10 days must come back.
    resurf = pg.evaluate("""() => {
      const r = DATA.find(x => typeof x.days==='number' && x.days>=0 && x.days<=10 && x.st!=='LP');
      if(!r) return {skip:'no near-auction lead'};
      filed.add(r.case); viewed.set(r.case, Date.now());          // filed just now
      const freshFiled = _viewedResurfaced(r);                    // must stay filed
      viewed.set(r.case, Date.now() - 9*864e5);                   // filed, last look 9 days ago
      const back = _viewedResurfaced(r);
      const chip = _seenChip(r);
      filed.delete(r.case); viewed.delete(r.case); render();
      return {case:r.case, days:r.days, staysWhenFresh: !freshFiled, comesBackWhenStale: back,
              chipSaysBack: chip.indexOf('BACK') > -1};
    }""")
    if resurf.get('skip'):
        print('  SKIP re-surface test:', resurf['skip'])
    else:
        rec('Seen: recently-filed lead stays filed', resurf['staysWhenFresh'], resurf['case'])
        rec('Seen: stale-filed lead with a near auction comes BACK', resurf['comesBackWhenStale'],
            f"{resurf['case']} auction in {resurf['days']}d")
        rec('Seen: the returning lead is badged BACK', resurf['chipSaysBack'], '')

    # CHANGE-DELTA: "seen 9d ago" says a deal is stale; it does not say whether re-opening is worth
    # the time. A tiny fingerprint (days-to-auction, phone count, judgment, pipeline status) is
    # snapshotted at view time and diffed on the way back, so the row can say WHY it changed.
    delta = pg.evaluate("""() => {
      const r = DATA.find(x => typeof x.days==='number' && x.days>=0 && x.days<=30 && x.st!=='LP');
      if(!r) return {skip:'no dated lead'};
      const c = r.case;
      _markViewed(c);                     // baseline: the stamp a contact-intent click now fires
      const quiet = _seenDelta(r).length === 0;                 // nothing changed since 1ms ago
      // rewind the baseline: auction was 14d further out, 2 fewer phones, judgment unposted
      seenFP.set(c, {d:(r.days||0)+14, p:Math.max(0,(r.phones||[]).length-2), j:0, s:''});
      const kinds = _seenDelta(r).map(x=>x.k);
      const chip = _seenChip(r);
      viewed.delete(c); filed.delete(c); seenFP.delete(c); render();
      return {quietRightAfterLooking: quiet, kinds,
              rendersChip: chip.indexOf('deltachip') > -1};
    }""")
    if delta.get('skip'):
        print('  SKIP change-delta test:', delta['skip'])
    else:
        rec('Delta: no change reported immediately after looking', delta['quietRightAfterLooking'], '')
        rec('Delta: detects clock + phones + judgment movement',
            all(k in delta['kinds'] for k in ('clock','phone','judg')), str(delta['kinds']))
        rec('Delta: renders on the row', delta['rendersChip'], '')

    # REACHABILITY TRIAGE: every lead must resolve to exactly ONE contact state, so a call session or
    # door route can be triaged from the board instead of by opening call sheets one at a time. The
    # states must also be honest — a suppressed lead can never read as reachable.
    reach = pg.evaluate("""() => {
      const m = {ok:0, mid:0, no:0, none:0, multi:0, hh:0};
      let suppressedShownReachable = 0, landlineShownTextable = 0;
      DATA.forEach(r => {
        const h = _reachChip(r);
        const hits = ['rch rch-ok','rch rch-mid','rch rch-no"','rch rch-none'].filter(c => h.indexOf(c) > -1).length;
        if(hits !== 1) m.multi++;
        if(h.indexOf('rch rch-ok')   > -1) m.ok++;
        else if(h.indexOf('rch rch-mid')  > -1) m.mid++;
        else if(h.indexOf('rch rch-no"')  > -1) m.no++;
        else if(h.indexOf('rch rch-none') > -1) m.none++;
        if(h.indexOf('rch-hh') > -1) m.hh++;
        // a lead under opt-out / wrong-person / BK stay must NEVER render as reachable
        if(_textContactBlocked(r) && h.indexOf('rch rch-ok') > -1) suppressedShownReachable++;
        // a lead whose only numbers are landlines must not claim textability
        const t = r.phtype||[], d = r.phdnc||[];
        const anyTextable = (r.phones||[]).some((p,i) => !d[i] && String(t[i]||'').toLowerCase().indexOf('land') === -1);
        if(!anyTextable && h.indexOf('rch rch-ok') > -1) landlineShownTextable++;
      });
      m.total = DATA.length; m.sum = m.ok + m.mid + m.no + m.none;
      m.suppressedShownReachable = suppressedShownReachable;
      m.landlineShownTextable = landlineShownTextable;
      return m;
    }""")
    rec('Reach: every lead gets exactly one contact state',
        reach['sum'] == reach['total'] and reach['multi'] == 0,
        f"{reach['sum']}/{reach['total']} partitioned, {reach['multi']} ambiguous")
    rec('Reach: suppressed leads never render as reachable',
        reach['suppressedShownReachable'] == 0, f"{reach['no']} suppressed")
    rec('Reach: landline/DNC-only never claims textable',
        reach['landlineShownTextable'] == 0, f"{reach['mid']} dial-only")

    # DEBT-STACK RECONCILIATION. The stack derives lien positions from its own guess (face amount
    # closest to the judgment) while _chainSurv comes from the parser aggregate — and _chainSurv is
    # what drives net equity, profit and the verdict. When the two disagree the sheet used to
    # contradict ITSELF on one screen: "WHAT SURVIVES: $0 · RECORDS-VERIFIED" in the box, and
    # "SENIOR — SURVIVES $435,000 — this eats the equity" in a row below. 8 live leads did this.
    # The stack may now disagree, but it must SAY SO — never assert against the box in silence.
    recon = pg.evaluate("""() => {
      let silent = 0, flagged = 0, agree = 0; const ex = [];
      DATA.forEach(r => {
        if(!(r.orliens||[]).length) return;
        // TAX DEEDS HAVE NO SURVIVING CHAIN. FS 197.552 extinguishes every mortgage and private
        // lien, and _debtStack correctly renders the tax-deed variant ("wiped at the tax-deed
        // sale") with no senior row and no reconciliation banner — while _chainSurv still carries
        // a foreclosure-lens figure. Scoring that as a silent contradiction flagged case 53894 on
        // a panel behaving exactly as designed. (Second time tonight TDs in a denominator caused a
        // false alarm — see the plaintiff check in _diligencetest.)
        if(r.st === 'TD') return;
        const h = _debtStack(r), box = +r._chainSurv || 0;
        const asserts = h.indexOf('SENIOR — SURVIVES') > -1;
        const banner  = h.indexOf('CHAIN NOT RECONCILED') > -1;
        if(banner) flagged++; else agree++;
        if(asserts && box === 0 && !banner){ silent++; ex.push(r.case+' (row asserts senior, box $0)'); }
        if(!asserts && box > 0 && !banner){ silent++; ex.push(r.case+' (box $'+Math.round(box)+', no senior row)'); }
      });
      return {silent, flagged, agree, ex: ex.slice(0,3)};
    }""")
    rec('Debt stack never contradicts the WHAT-SURVIVES box in silence',
        recon['silent'] == 0, f"{recon['silent']} silent; {recon['flagged']} flagged; {recon['agree']} agree" +
        (' | ' + '; '.join(recon['ex']) if recon['ex'] else ''))
    rec('Debt stack flags the real unreconciled leads', recon['flagged'] > 0, f"{recon['flagged']} banners")

    # LOOKUP PANEL = SAME CHAIN AS THE MATH. The Lookup report used to read the chain a THIRD way
    # (raw orsurvfirst/orjunior), consulting neither _chainSurv nor the operator's researched _slien.
    # On the live board that disagreed on 45 leads totalling $10.8M, worst single case $1,308,288 —
    # and always in the same direction: a phantom survivor, subtracted from the seller-net figure
    # quoted to a HOMEOWNER. That is a lowball walk-away number on a deal we could have closed.
    lk = pg.evaluate("""() => {
      const src = String(lkReport);
      // property ACCESS only — the bare words also appear in the comment explaining why they left
      const raw = /\.\s*(orsurvfirst|orjunior)\b|\[\s*['"](orsurvfirst|orjunior)['"]\s*\]/.test(src);
      let diverge = 0, dollars = 0;
      DATA.forEach(r => {
        if(!(r.orliens||[]).length) return;
        const shown = r._slien != null ? +r._slien : (+r._chainSurv || 0);
        const truth = +r._slien || 0;
        const d = Math.abs(shown - truth); if(d > 1000){ diverge++; dollars += d; }
      });
      // a researched $0 must SURVIVE the round trip — the `||` fallback used to resurrect the chain
      const t = DATA.find(x => (+x._chainSurv||0) > 1000);
      let zeroHolds = true, typedFlows = true;
      if(t){
        notes[t.case] = notes[t.case] || {};
        notes[t.case].slien = 0; recompute();
        zeroHolds = (+t._slien === 0) && t._slienSet === true;
        notes[t.case].slien = 250000; recompute();
        typedFlows = (+t._slien === 250000) && t._slienSet === true;
        delete notes[t.case].slien; recompute();
      }
      return {raw, diverge, dollars, zeroHolds, typedFlows, probed: !!t};
    }""")
    rec('Lookup panel reads no third chain source', not lk['raw'], 'orsurvfirst/orjunior gone from lkReport')
    rec('Lookup surviving-lien agrees with the deal math', lk['diverge'] == 0,
        f"{lk['diverge']} divergent, ${lk['dollars']:,.0f} misstated")
    rec('Researched $0 survivor is not overwritten by the chain', lk['zeroHolds'] and lk['probed'], '')
    rec('Typed survivor flows through to Lookup', lk['typedFlows'] and lk['probed'], '')

    # TAX DEED IS NOT A FORECLOSURE. The row headline used to model "you acquire at the OPENING bid" —
    # true at a foreclosure sale, a fantasy at a tax deed where an equity parcel gets bid up toward
    # market. On the live board that overstated the deal analyzer's own model by up to $2,023,300 on
    # one lead (2026-2799TD: row $2,548,223 vs modeled $524,923). Walking into an auction believing
    # there is $2.5M on the table is how you overbid.
    td = pg.evaluate("""() => {
      const tds = DATA.filter(r => r.st === 'TD' && !r._nodata);
      let over = 0, worst = 0, labelled = 0, ownerSplit = 0;
      tds.forEach(r => {
        const ne = _netEqOf(r) || 0, pf = (r._profit == null ? 0 : Math.max(0, +r._profit));
        const d = ne - pf; if (d > 1000) { over++; if (d > worst) worst = d; }
        if (_netEqLabel(r,1) === 'MODELED PROFIT') labelled++;
        // owner-side equity is a DIFFERENT number and must not have collapsed into ours
        if ((_ownerEqOf(r)||0) > ne + 1000) ownerSplit++;
      });
      const fc = DATA.filter(r => r.st !== 'TD' && !r._nodata);
      // the foreclosure path must be untouched by all of this
      const fcSame = fc.every(r => Math.abs((_netEqOf(r)||0) - (_ownerEqOf(r)||0)) < 1);
      const fcLabel = fc.length ? _netEqLabel(fc[0],1) === 'NET EQUITY' : false;
      const msg = tds.length ? _joseMsg(tds[0]) : '';
      return {n: tds.length, over, worst: Math.round(worst), labelled, ownerSplit,
              fcSame, fcLabel,
              msgHonest: !/Foreclosing judgment|Surviving senior mortgage/.test(msg),
              msgBid: /Est\\. winning bid/.test(msg)};
    }""")
    rec('Tax-deed rows never headline the opening-bid fantasy', td['over'] == 0,
        f"{td['n']} TD leads, {td['over']} overstating, worst ${td['worst']:,}")
    rec('Tax-deed money box is labelled MODELED PROFIT', td['labelled'] == td['n'],
        f"{td['labelled']}/{td['n']}")
    rec('Foreclosure net-equity math is unchanged', td['fcSame'] and td['fcLabel'], '')
    rec('Owner equity stayed a separate number on TDs', td['ownerSplit'] > 0,
        f"{td['ownerSplit']} TDs where owner equity > our profit")
    rec('Jose message quotes auction economics on a TD, not a phantom judgment',
        td['msgHonest'] and td['msgBid'], '')

    # TD LENS. Money was only half of it — the sheet still SPOKE foreclosure on a tax deed: a "FINAL
    # JUDGMENT · lender on the case" that does not exist, and a survivor box ordering the operator to
    # go pull a senior 2nd that FS 197.552 extinguishes at the sale. That sends them researching the
    # wrong lien and implies the equity is at risk from a mortgage that will be wiped.
    lens = pg.evaluate("""() => {
      // not LP: a fresh lis pendens deliberately has no FINAL JUDGMENT box (the LP-framing check
      // above REQUIRES that), so sampling one here misread the LP lens as a broken FC lens.
      const t = DATA.find(r => r.st === 'TD' && !r._nodata);
      const f = DATA.find(r => r.st !== 'TD' && r.st !== 'LP' && !r._nodata);
      if(!t || !f) return {ok:false};
      const boxOf = () => [...document.querySelectorAll('.csbox')]
        .map(b => (((b.querySelector('.csl')||{}).textContent||'')+' | '+((b.querySelector('.csv')||{}).textContent||'')+' | '+((b.querySelector('.css')||{}).textContent||''))).join('\\u2028');
      openCallSheet(t.case); const tdBox = boxOf();
      closeCallSheet(); openCallSheet(f.case); const fcBox = boxOf();
      closeCallSheet();
      return {ok:true,
        tdNoJudgment: !/FINAL JUDGMENT/.test(tdBox),
        tdOpening:    /OPENING BID/.test(tdBox),
        tdWiped:      /MORTGAGES WIPED/.test(tdBox) && /197\\.552/.test(tdBox),
        tdNoHunt:     !/senior 2nd\\/HELOC/.test(tdBox),
        // PAYOFF is the accrued-interest variant of the same foreclosure lens (FS 55.03 accrual,
        // _isAccrued swaps the label) — both count as intact; only the TD lens leaking would not.
        fcIntact:     /(FINAL JUDGMENT|PAYOFF)/.test(fcBox) && !/MORTGAGES WIPED/.test(fcBox)};
    }""")
    rec('TD sheet drops the phantom final judgment for the opening bid',
        lens['ok'] and lens['tdNoJudgment'] and lens['tdOpening'], '')
    rec('TD sheet states mortgages are wiped (FS 197.552), not "go pull the senior"',
        lens['ok'] and lens['tdWiped'] and lens['tdNoHunt'], '')
    rec('Foreclosure sheet keeps the foreclosure lens', lens['ok'] and lens['fcIntact'], '')

    # SOURCE-OF-TRUTH SWEEP (6-lens audit, 2026-07-27). Fifteen surfaces were still computing money
    # from their own private reading of the board — raw chain fields, raw county value, the baked
    # gross r.eq — instead of the resolvers the math uses. The worst were the two SMS surfaces
    # (senior from _chainSurv beside a net from _slien: one message, two readings of one lien) and
    # the Deal desk homeowner payoff (judgment + senior only, dropping juniors/HOA/code/taxes the
    # tracker itself had resolved). This block pins the whole batch.
    sw = pg.evaluate("""() => {
      const out = {};
      const t = DATA.find(x => (+x._chainSurv||0) > 10000 && x.st !== 'TD');
      notes[t.case] = notes[t.case] || {};
      notes[t.case].slien = 0; recompute();
      out.mrZero   = mrText(t) === '';                          // researched 0 kills the row badge
      out.deadZero = !isFlaggedDead(t) || !!(t.ordeeded && t.ordeedconf === 'ok');
      out.dsZero   = _debtStack(t).indexOf('CHAIN NOT RECONCILED') > -1
                     || _debtStack(t).indexOf('SENIOR — SURVIVES') === -1;
      notes[t.case].slien = 174000; recompute();
      out.mrTyped   = mrText(t).indexOf('174k') > -1;           // typed value reaches the badge
      out.joseTyped = _joseMsg(t).indexOf('174,000') > -1 && _joseMsg(t).indexOf('you verified') > -1;
      out.liveTyped = _liveMsg(t).indexOf('174,000') > -1;
      delete notes[t.case].slien; recompute();
      const h = DATA.find(x => (+x.orhoa||0) > 1000);
      if(h){ notes[h.case] = notes[h.case] || {}; notes[h.case].assess = 0; recompute();
        out.hoaZero = _debtSheetBody(h).indexOf('HOA / association') === -1;
        delete notes[h.case].assess; recompute(); } else out.hoaZero = true;
      out.onePct = DATA.filter(x => !x._nodata).every(x => _eqPct(x) === netEqPct(x));
      const td = DATA.find(x => x.st === 'TD' && x.hs && +x.value > 0 && +x.obid > +x.value * 0.4);
      out.hsOwed = td ? (_ownerOwedOf(td) <= (+td.obid) - (+td.value)/2 + 1) : true;
      const m = DATA.find(x => x.folio && (x.orliens||[]).length && x.st !== 'TD');
      if(m){ const want = Math.round(_ownerOwedOf(m) + _ownerStackOf(m));
        lkResults = {}; const fk = String(m.folio).replace(/\\D/g,'');
        lkResults[fk] = {FOLIO: m.folio, TRUE_OWNER1: 'X', TOTAL_VAL_CUR: 400000};
        const html = lkReport(lkResults[fk]);
        out.payoffFull = html.indexOf('id="lkpo" type="number" value="' + want + '"') > -1;
      } else out.payoffFull = true;
      return out;
    }""")
    rec('Researched $0 survivor silences row badge, dead-flag and debt stack',
        sw['mrZero'] and sw['deadZero'] and sw['dsZero'], '')
    rec('Typed survivor reaches badge, Jose SMS and LIVE alert',
        sw['mrTyped'] and sw['joseTyped'] and sw['liveTyped'], '')
    rec('Typed $0 assessment cannot resurrect the raw HOA figure', sw['hoaZero'], '')
    rec('One equity percentage everywhere (_eqPct === netEqPct)', sw['onePct'], '')
    rec('Homestead tax-deed half-assessment is not owner debt', sw['hsOwed'], '')
    rec('Deal desk homeowner payoff carries the FULL resolved stack', sw['payoffFull'], '')

    # AUDIT RESIDUALS (closed after the 6-lens run's verify pass):
    #  - _debtStack() spoke pure foreclosure on tax deeds: a "being foreclosed" guess against a tax
    #    collector, senior/junior positioning, a nobody-is-"named in the suit" wipe analysis, and an
    #    assignment warning that fired on every TD because the plaintiff never matches a lender.
    #  - the row equity chip was gated on raw baked r.eq (a lead with a basis but eq=0 showed no chip
    #    at all), and TD rows said "% spread" over a number that is a modeled profit margin.
    resid = pg.evaluate("""() => {
      const out = {};
      const tds = DATA.filter(x => x.st==='TD' && (x.orliens||[]).length);
      out.tdStackClean = tds.length === 0 || tds.every(x => { const h = _debtStack(x);
        return h.indexOf('wiped at the tax-deed sale') > -1 && h.indexOf('BEING FORECLOSED') === -1
            && h.indexOf('SENIOR') === -1 && h.indexOf('CHAIN NOT RECONCILED') === -1; });
      const fc = DATA.find(x => x.st!=='TD' && (x.orliens||[]).length && (+x.judg||0) > 0);
      out.fcStackIntact = fc ? _debtStack(fc).indexOf('who holds what') > -1 : true;
      // every rendered row with a basis carries an equity/margin chip in SOME submeta cell
      let withBasis = 0, missing = 0;
      [...document.querySelectorAll('#tb tr[data-case]')].forEach(tr => {
        const r = DATA.find(x => x.case === tr.getAttribute('data-case'));
        if(!r || r._nodata || !_basisOf(r)) return;
        withBasis++;
        const has = [...tr.querySelectorAll('.submeta')].some(s => /%\\s*(eq|margin)/.test(s.textContent));
        if(!has) missing++;
      });
      out.withBasis = withBasis; out.chipMissing = missing;
      out.noSpread = document.body.innerHTML.indexOf('% spread') === -1;
      return out;
    }""")
    rec('Tax-deed debt stack speaks tax-deed, not foreclosure', resid['tdStackClean'], '')
    rec('Foreclosure debt stack unchanged', resid['fcStackIntact'], '')
    rec('Every basis-bearing row carries an equity/margin chip',
        resid['chipMissing'] == 0 and resid['withBasis'] > 0,
        f"{resid['chipMissing']} missing of {resid['withBasis']}")
    rec('TD rows say margin, never spread', resid['noSpread'], '')

    # SENTINELS AND FAKE ZEROS MUST NEVER REACH THE SCREEN. A lis pendens arrives with days=9999
    # ("no sale scheduled"), no folio and no address — all 125 of them. Every clock site tested
    # `days >= 0`, which 9999 satisfies, so the board printed "in 9999d" as if the auction were 27
    # years out; and the row rendered "$0 val · $0 owed", which reads as a worthless property when
    # the truth is we never looked it up. Both are lies a closer could act on.
    lpv = pg.evaluate("""() => {
      const lp = document.getElementById('freshlp'); if(lp) lp.click();
      const vis = document.getElementById('tb').textContent + ' ' + document.getElementById('cards').textContent;
      const rows = document.querySelectorAll('#tb tr[data-case]').length;
      const out = {
        rows: rows,
        leak9999   : (vis.match(/9999/g) || []).length,
        fakeZeroVal: (vis.match(/\\$0 val/g) || []).length,
        fakeZeroOwe: (vis.match(/\\$0 owed/g) || []).length,
        honestVal  : (vis.match(/parcel not resolved/g) || []).length,
        honestClock: (vis.match(/no sale date yet/g) || []).length,
        resolveBtns: document.querySelectorAll('a.resolveparcel').length,
        legalShown : document.querySelectorAll('.legalline').length,
        unresolved : DATA.filter(x => _unresolved(x)).length,
        legalBaked : DATA.filter(x => String(x.legal||'').trim()).length,
      };
      // the helper itself must classify the sentinel correctly
      out.helperOK = !_hasClock({days: 9999}) && _hasClock({days: 12}) && !_hasClock({days: -1})
                     && _clockTxt({days: 9999}) === 'no sale date yet';
      // an unresolved lead must never enter the work queue — handing him a lead with no address
      // and no number is handing him nothing
      out.notInQueue = _workQueue().every(x => !_unresolved(x.r));
      if(lp) lp.click();
      return out;
    }""")
    rec('LP lane: the 9999 sentinel never renders', lpv['leak9999'] == 0,
        f"{lpv['rows']} LP rows shown")
    rec('LP lane: no fake "$0 val / $0 owed" on an unlooked-up parcel',
        lpv['fakeZeroVal'] == 0 and lpv['fakeZeroOwe'] == 0, '')
    rec('LP lane: says "parcel not resolved" + "no sale date yet" instead',
        lpv['honestVal'] > 0 and lpv['honestClock'] > 0,
        f"{lpv['honestVal']} val / {lpv['honestClock']} clock")
    # Measures UNRESOLVED rows, not rows-shown. Those were the same number until lp_resolve.py
    # started filling addr/folio on lis pendens leads (2026-07-30); now 85 of 125 LP rows ARE
    # resolved and correctly carry no Resolve-parcel action, so comparing against rows-shown
    # failed on a board that had just gotten better. Assert the thing the name promises.
    #
    # THIRD REVISION (2026-08-26). Row links now render LAZILY on row-expand (collapsed rows carry
    # no dig cluster at all — measured: 5.2KB of row HTML, zero links; expanding renders the
    # Resolve action fine). Counting buttons in the collapsed DOM read 0/29 on a lane that is
    # fully covered one click deep. Assert what the OPERATOR experiences: expand a sample of
    # unresolved rows and require the action to appear in each.
    expchk = pg.evaluate("""() => {
      // the lpv block above toggled the Fresh-filings lane back OFF on its way out — re-enter it
      const lp = document.getElementById('freshlp'); if(lp) lp.click();
      const trs = [...document.querySelectorAll('#tb tr[data-case]')];
      const un = trs.filter(tr => { const r = DATA.find(x => x.case === tr.getAttribute('data-case'));
                                    return r && _unresolved(r); });
      let opened = 0, withBtn = 0;
      for(const tr of un.slice(0, 3)){
        tr.click(); opened++;
        if(document.body.innerHTML.indexOf('resolveparcel') > -1) withBtn++;
        tr.click();                      // collapse again so the next sample is clean
      }
      if(lp) lp.click();                 // leave the lane as we found it for the checks below
      return {opened, withBtn};
    }""")
    rec('LP lane: an unresolved row offers Resolve-parcel on expand',
        expchk['opened'] > 0 and expchk['withBtn'] == expchk['opened'],
        f"{expchk['withBtn']}/{expchk['opened']} expanded rows show the action "
        f"({lpv['honestVal']} unresolved in-lane)")
    # The board-wide number is the pipeline gap, not a UI gap: unresolved leads outside the LP
    # lane have no Resolve action anywhere. Tracked as the LP-resolver backlog, asserted here only
    # so a silent jump in that count is visible. Bound re-grounded 2026-08-26: the 200 was set when
    # LP was Dade-only; the sweep now runs 3 counties (1,189 filings, 497 high-confidence, and
    # only HIGH auto-flows by design) so ~700 unresolved IS the current pipeline state, not a
    # regression. Next silent jump should still trip this.
    rec('board-wide unresolved count stays within the known LP backlog',
        lpv['unresolved'] <= 800, f"{lpv['unresolved']} unresolved board-wide")
    rec('LP lane: the legal description is baked and shown as the parcel identity',
        lpv['legalBaked'] > 0 and lpv['legalShown'] > 0,
        f"{lpv['legalBaked']} baked / {lpv['legalShown']} rendered")
    rec('Clock helper classifies sentinel, real and passed correctly', lpv['helperOK'], '')
    rec('Unresolved leads never enter the work queue', lpv['notInQueue'],
        f"{lpv['unresolved']} unresolved on the board")

    # REFRESH ON DEMAND. Two different actions were hiding behind one button: "give me the newest
    # board" (instant, no setup) and "re-run the full cloud scrape" (40-70 min, needs a hand-made
    # GitHub token). On a device with no token the button did nothing but show a token form — so the
    # everyday need, pulling the current build onto a phone that has been cached for days, was
    # unreachable. Also: the cooldown was 60*3600000 = 60 HOURS while the UI printed the remainder as
    # minutes and promised "one fire per hour", silently locking the button for two and a half days.
    rf = pg.evaluate("""() => {
      const out = {};
      const prev = localStorage.getItem('fcRefreshAt');
      localStorage.setItem('fcRefreshAt', String(Date.now() - 30*60000));
      out.at30 = _refreshCooldownLeft();          // ~30 minutes left, NOT ~3570
      localStorage.setItem('fcRefreshAt', String(Date.now() - 61*60000));
      out.at61 = _refreshCooldownLeft();          // expired
      if(prev === null) localStorage.removeItem('fcRefreshAt'); else localStorage.setItem('fcRefreshAt', prev);
      openRefresh();
      out.opens     = !!document.getElementById('refreshmodal').classList.contains('show');
      out.pullBtn   = !!document.getElementById('gh-pull');
      out.statusSlot= !!document.getElementById('pullstat');
      out.tokenPathKept = !!document.getElementById('ghtokin') || !!document.getElementById('gh-fire');
      out.pullFn    = typeof _pullLatest === 'function';
      const md=document.getElementById('refreshmodal');
      md.classList.remove('show'); document.body.classList.remove('modal-open');
      return out;
    }""")
    rec('Refresh cooldown is 60 MINUTES, not 60 hours',
        29 <= rf['at30'] <= 31 and rf['at61'] == 0, f"30min→{rf['at30']} left, 61min→{rf['at61']}")
    rec('Refresh modal offers an instant no-token "get latest board"',
        rf['opens'] and rf['pullBtn'] and rf['statusSlot'] and rf['pullFn'], '')
    rec('Refresh modal still keeps the full cloud re-scrape path', rf['tokenPathKept'], '')

    # ASSOCIATION FORECLOSURES MUST NOT READ AS FANTASY EQUITY. Florida's Uniform Case Number is
    # {county:2}{year:4}{court:2}{seq}. Palm Beach files that way ("502025CC016197XXXAMB") and matched
    # NONE of the case-number patterns, which only covered Broward's CACE/COCE prefixes and
    # Miami-Dade's -CA-/-CC- infixes — so 182/182 PB leads classified as '' and defaulted to
    # Bank/Mortgage. CC is County Civil, whose ~$50k jurisdictional cap means it essentially cannot
    # hear a residential mortgage foreclosure — those are association cases, and under FS 718.116 the
    # association lien is SUBORDINATE, so the first mortgage SURVIVES the sale. Modeled as a mortgage
    # foreclosure instead, a $14,249 judgment on a $658,467 Boynton house headlined ~98% equity.
    hoa = pg.evaluate("""() => {
      const pb  = DATA.filter(x => x.county === 'PALM BEACH');
      const ccs = pb.filter(x => /^\\d{6}CC\\d/.test(String(x.case||'')));
      // Assert the SAFETY PROPERTY, not a label. A county-civil case with a bank plaintiff is
      // usually a junior/HELOC action rather than an association one, so forcing ctype='HOA' would
      // swap one false label for another. What must hold either way: the first mortgage survives,
      // so the equity is not real and the lead must not present as clean senior equity.
      const bad = ccs.filter(x => !x.mr || !x.eqfake);
      return {
        pb: pb.length, cc: ccs.length, misclassified: bad.length,
        allVerify : ccs.every(x => x._verdict === 'VERIFY' || x._verdict === 'PASS'),
        noneTierA : ccs.every(x => x.tier !== 'A'),
        allWarn   : ccs.every(x => mrText(x) !== ''),
        noneQueued: _workQueue().every(q => !(q.r.county === 'PALM BEACH' && /^\\d{6}CC\\d/.test(String(q.r.case||'')))),
        worst: ccs.sort((a,b)=>(+b.value||0)-(+a.value||0)).slice(0,1)
                  .map(x => x.case+' judg $'+Math.round(+x.judg||0).toLocaleString()+' val $'+Math.round(+x.value||0).toLocaleString())[0] || '',
      };
    }""")
    if hoa['cc'] == 0:
        print('  SKIP association-classification test: no Palm Beach County Civil cases on this board')
    else:
        rec('County-civil cases are flagged mortgage-survives + fantasy-equity',
            hoa['misclassified'] == 0, f"{hoa['cc']} CC of {hoa['pb']} PB · worst: {hoa['worst']}")
        rec('County-civil cases never headline as Tier A equity', hoa['noneTierA'], '')
        rec('County-civil rows warn that the first mortgage survives', hoa['allWarn'], '')
        rec('County-civil cases never enter the work queue', hoa['noneQueued'], '')

    # SURVIVING SENIOR HAS ONE DEFINITION. The three chain engines disagreed about what `surv` holds:
    # records_liens.py:305 and broward_liens.py:325 sum every open lien EXCEPT the foreclosing one
    # (seniors + juniors), while batchdata_liens.py:113 sums the seniors ONLY. The board applied the
    # records-style `surv - juniors_post` subtraction to both, so on BatchData leads the junior
    # balance came out of the senior figure twice — and Math.max(0,...) swallowed the remainder,
    # erasing an entire $811,577 first mortgage to $0 on 502024CA012300XXXAMB. The pipeline now emits
    # one normalized seniors-only field and the browser does no arithmetic on it at all.
    sen = pg.evaluate("""() => {
      const ch = DATA.filter(x => x.orsurvsen != null);
      if(!ch.length) return {skip: true};
      const bd = ch.filter(x => x.orsrc === 'batchdata');
      return {
        chained: ch.length, bd: bd.length,
        // the surviving SENIOR can never exceed the total surviving stack
        invariant: ch.every(x => (+x.orsurvsen||0) <= (+x.orsurv||0) + 1),
        // BatchData's surv is already seniors-only, so normalizing must be a no-op for it
        bdIdentity: bd.every(x => Math.abs((+x.orsurvsen||0) - (+x.orsurv||0)) <= 1),
        // provenance is emitted separately from confidence
        srcSplit: ch.every(x => x.orsrc === 'batchdata' || x.orsrc === 'records'),
        // a chain proving nothing survives must emit a real 0, never fall back to reading the junior
        noJuniorAsSenior: ch.every(x => !((+x.orsurvsen||0) === 0 && (+x._chainSurv||0) > 0)),
        // _chainSurv must equal the normalized field on non-HOA leads — no browser-side subtraction
        noBrowserMath: ch.filter(x => x.orftype !== 'HOA')
                         .every(x => Math.abs((+x._chainSurv||0) - (+x.orsurvsen||0)) <= 1),
      };
    }""")
    if sen.get('skip'):
        print('  SKIP surviving-senior test: no chain-fed leads on this board')
    else:
        rec('Surviving senior never exceeds the total surviving stack', sen['invariant'],
            f"{sen['chained']} chain-fed ({sen['bd']} BatchData)")
        rec('BatchData seniors-only figure is normalized without double-subtracting', sen['bdIdentity'], '')
        rec('Chain provenance is tracked separately from confidence', sen['srcSplit'], '')
        rec('A junior balance is never read as a surviving senior', sen['noJuniorAsSenior'], '')
        rec('The browser does no arithmetic on the surviving senior', sen['noBrowserMath'], '')

    # ASSOCIATION SALE = THE WHOLE OPEN STACK SURVIVES (FS 718.116). The HOA branch preferred
    # `orsurvfirst` — max(o.amt), ONE mortgage — over the summed stack, and since both tracers leave
    # juniors_post at 0 on an HOA case there was no second bucket to catch the remainder. Every extra
    # open mortgage vanished from _slien, _netEqOf, netEqPct, _cash and _profit at once, and the call
    # sheet printed the understated figure tagged RECORDS-VERIFIED.
    # And ONE THRESHOLD CANNOT SERVE TWO QUANTITIES: netEqPct is an equity ratio on a foreclosure but
    # a profit MARGIN on a tax deed, capped at ~20% by the TD model's own bid floor and cost load —
    # so "30%+ equity" was arithmetically unsatisfiable for every tax deed ever scraped.
    st = pg.evaluate("""() => {
      const hoa = DATA.filter(x => x.orftype === 'HOA' && (+x.orsurv||0) > 0);
      const td  = DATA.filter(x => x.st === 'TD' && !x._nodata && _basisOf(x));
      const ownerPct = r => Math.round((_ownerEqOf(r) / _basisOf(r)) * 100);
      return {
        hoaN: hoa.length,
        // the surviving figure must be the summed stack, never just the biggest mortgage
        usesFullStack: hoa.every(x => Math.abs((+x._chainSurv||0) - (+x.orsurvsen||0)) <= 1),
        multi: hoa.filter(x => (+x.orsurv||0) > (+x.orsurvfirst||0) + 1).length,
        tdN: td.length,
        // the profit-margin ceiling that made the old filter impossible
        tdMaxNetEqPct: td.length ? Math.max(...td.map(x => netEqPct(x))) : 0,
        tdPassOwner: td.filter(x => ownerPct(x) >= 30).length,
      };
    }""")
    if st['hoaN']:
        rec('Association sales count the FULL surviving stack, not the largest mortgage',
            st['usesFullStack'], f"{st['hoaN']} HOA chains, {st['multi']} with >1 open mortgage")
    if st['tdN']:
        # documents the defect: if this ever reaches 30 the old filter would have been satisfiable
        rec('Tax-deed profit margin is structurally below the old 30% equity threshold',
            st['tdMaxNetEqPct'] < 30, f"max netEqPct across {st['tdN']} TDs = {st['tdMaxNetEqPct']}%")
        rec('The 30%+ equity filter can now surface tax deeds', st['tdPassOwner'] > 0,
            f"{st['tdPassOwner']}/{st['tdN']} pass on owner equity")

    # THE CALL SCRIPT MUST QUOTE WHAT THE BOARD ALREADY KNOWS. The script asked the OWNER for the
    # mortgage balance while the resolved chain sat two clicks away — so the operator would ask a
    # question the tracker could answer for him in real time. The panel now embeds worth, judgment,
    # surviving senior, junior/HOA/tax, verdict and play at the top of the script, each row tagged
    # with provenance.
    sc = pg.evaluate("""() => {
      const src = String(genScript);
      return {
        panel: /_knowsEN/.test(src) && /scriptknow/.test(src),
        inBoth: (src.match(/_knowsEN/g)||[]).length + (src.match(/_knowsES/g)||[]).length >= 2,
      };
    }""")
    rec('Call script embeds the resolved money model at the top', sc['panel'], '')
    rec('Both single-language and EN/ES views carry the panel', sc['inBoth'], '')

    # HOMESTEAD TAX-DEED FLOOR MUST USE ASSESSED, NOT MARKET. FS 197.502(6)(c) adds half the ASSESSED
    # value to the opening bid. My earlier fix wrote half of `r.value` (JV/market); on a Save Our
    # Homes-differentiated homestead assessed << market, so half-market over-subtracts and the
    # Math.max(0,…) clamped an impossible negative to zero — the deal desk quoted a $0 payoff on a
    # homestead facing a real tax-deed sale.
    hs = pg.evaluate("""() => {
      const t = DATA.find(x => x.st === 'TD' && x.hs);
      if(!t) return {skip: true};
      // simulate an assessed value that is 60% of market (typical SOH cap)
      const orig = t.assessed_value; t.assessed_value = Math.round((+t.value||0) * 0.6);
      const owedAV = _ownerOwedOf(t);
      t.assessed_value = 0; const owedFallback = _ownerOwedOf(t);
      t.assessed_value = orig;
      // and: no false-negative-clamped zero when the pre-subtract debt is positive
      const clampsToZero = owedAV === 0 && (+t.obid||+t.judg||0) > 0 && (+t.value||0) > 0;
      return {av: owedAV, fallback: owedFallback, hasClampBug: clampsToZero};
    }""")
    if hs.get('skip'):
        print('  SKIP homestead-TD test: no homestead tax deed on this board')
    else:
        rec('Homestead TD half-assessed floor never clamps a real debt to $0',
            not hs['hasClampBug'], '')

    # THE INLINE CALL SCRIPT — the actual "sync" between the script and the deal analyzer. The old
    # Call script button opened a Blob URL in a new window with no live reference back to the row,
    # so an owner's answer to "what's the mortgage balance?" had to be transcribed by hand into a
    # separate Math panel. The script now ALSO renders as a collapsible section on the call sheet
    # itself, above Math, using the same resolved data — one screen, one source of truth.
    ic = pg.evaluate("""() => {
      const c = DATA.find(x => !x._nodata && (+x.judg||0) > 0);
      if(!c) return {skip: true};
      openCallSheet(c.case);
      const cs = document.getElementById('cscript');
      const rows = cs ? cs.querySelectorAll('.scriptknow-t tr').length : 0;
      const actions = document.querySelectorAll('.cscript-actions a').length;
      closeCallSheet();
      openCallSheet(c.case, {script: true});
      const cs2 = document.getElementById('cscript');
      const opened = cs2 && cs2.hasAttribute('open');
      closeCallSheet();
      return {shows: !!cs, rows, actions, openWithFlag: opened,
              stateVar: typeof callSheetScriptOpen !== 'undefined'};
    }""")
    if ic.get('skip'):
        print('  SKIP inline call-script test: no priced lead on this board')
    else:
        rec('Call script also renders inline on the call sheet', ic['shows'], '')
        rec('Inline script carries the What-the-tracker-knows table', ic['rows'] >= 4,
            f"{ic['rows']} rows visible")
        rec('Inline script has the print-popup and jump-to-Math shortcuts', ic['actions'] >= 2, '')
        rec('openCallSheet({script:true}) opens the section directly',
            ic['openWithFlag'] and ic['stateVar'], '')

    # ONE MORE: date-string comparison in records_liens.py returned the wrong sort. `M/D/YYYY` was
    # compared as text, so '1/10/2006' >= '10/31/2006' is True — a January loan silently classified
    # as newer than an October loan, swapping seniors and juniors in juniors_post. Now parses through
    # datetime.strptime, and the demo below proves the two paths disagree.
    import subprocess, sys
    # Char-wise: '1/10/2005' vs '1/1/2006' — at position 3 '0' (0x30) < '/' (0x2F) is False, so
    # the string compare says '1/10/2005' > '1/1/2006' — reading a January-2005 loan as NEWER than
    # a January-2006 loan. That inversion silently flips seniors and juniors in juniors_post.
    # Measured across a synthetic pool of 192 realistic M/D/YYYY dates: 8,304 of 18,336 ordered
    # pairs (45%) disagree between string sort and real date sort.
    demo = subprocess.run([sys.executable, '-c',
        "import records_liens as R; a=R._parse_recd('1/10/2005'); b=R._parse_recd('1/1/2006');"
        " print('STR<', '1/10/2005' < '1/1/2006', 'DATE<', a<b)"],
        capture_output=True, text=True, cwd=HERE)
    ok = 'STR< False DATE< True' in demo.stdout
    rec('records_liens date parser corrects the M/D/YYYY string-sort bug', ok, demo.stdout.strip()[:80])

    # AFTER-CALL BAR. The Log modal captures outcomes but costs a modal — which is why it goes
    # unused mid-dial-session, leaving "✓ seen 3d ago" as the only memory of what happened. A tel:/
    # text tap arms a one-tap outcome bar (same QUICKLOG specs, same logTouch writer, same guards);
    # research clicks (people-search) must never arm it — they'd pollute the funnel the cadence
    # engine schedules from.
    ac = pg.evaluate("""() => {
      const out = {};
      const tr = [...document.querySelectorAll('#tb tr[data-case]')].find(tr =>
        tr.nextElementSibling && tr.nextElementSibling.querySelector('a[href^="tel:"]'));
      if(!tr) return {skip: true};
      const c = tr.dataset.case;
      const tel = tr.nextElementSibling.querySelector('a[href^="tel:"]');
      tel.addEventListener('click', e => e.preventDefault(), {once: true});
      const before = ((notes[c]||{}).touches||[]).length;
      tel.click();
      out.arms = !!(_afterCall && _afterCall.c === c && _afterCall.ch === 'call');
      _showAfterCall();
      out.bounceGuard = !document.getElementById('aftercall');       // <3s = dialer focus-bounce
      _afterCall.t = Date.now() - 5000;
      _showAfterCall();
      const bar = document.getElementById('aftercall');
      out.shows = !!bar;
      if(bar) bar.querySelector('button[data-ql="0"]').click();      // ☎ No answer
      const ts = ((notes[c]||{}).touches||[]); const last = ts[ts.length-1]||{};
      out.logs = ts.length === before + 1 && last.ch === 'call' && last.out === 'no answer'
                 && (notes[c]||{}).status === 'Called - no answer' && !!(notes[c]||{}).next;
      out.cleans = !document.getElementById('aftercall') && _afterCall === null;
      const wp = [...document.querySelectorAll('#tb tr.lrow a[href]')].find(a => /truepeoplesearch|whitepages/i.test(a.href));
      if(wp){ wp.addEventListener('click', e => e.preventDefault(), {once: true}); wp.click(); }
      out.researchSilent = _afterCall === null;
      ts.pop(); if(!ts.length) delete (notes[c]||{}).touches;
      (notes[c]||{}).status=''; (notes[c]||{}).next=''; save(); render();
      return out;
    }""")
    if ac.get('skip'):
        print('  SKIP after-call test: no tel: link on the board')
    else:
        rec('After-call: a tel: tap arms the bar, dialer bounce guarded',
            ac['arms'] and ac['bounceGuard'], '')
        rec('After-call: one tap logs through logTouch with the QUICKLOG spec',
            ac['shows'] and ac['logs'], '')
        rec('After-call: bar cleans up after itself', ac['cleans'], '')
        rec('After-call: research clicks never enter the touch funnel', ac['researchSilent'], '')

    # WORK QUEUE. _closeScore ranks how good a DEAL is; it is blind to what he has already done, so a
    # lead called yesterday ranked identically to one nobody had ever dialed and a follow-up set for
    # today did not rise at all. _queueState layers work-state on top, and the bar puts the NUMBER in
    # front of him rather than navigating anywhere (he does not open the hub). The call button is a
    # real tel: anchor so it flows through the contact-intent listener: call -> Seen -> after-call
    # bar -> log -> next lead, with no decision in between.
    wq = pg.evaluate("""() => {
      const out = {};
      const q0 = _workQueue();
      if(q0.length < 6) return {skip: 'queue too small'};
      out.reachableOnly = q0.every(x => _bestPhones(x.r).length > 0 || (x.r.addr && x.r.st !== 'LP'));
      out.excludesFiled = q0.every(x => !filed.has(x.r.case) || _viewedResurfaced(x.r));
      out.ranked = q0.every((x, i) => i === 0 || q0[i-1].s >= x.s);
      const c = q0[5].r.case; const n = notes[c] = notes[c] || {};
      n.touches = [{d: _today(), ch: 'call', out: 'no answer'}];
      out.touchedTodayDrops = !_workQueue().some(x => x.r.case === c);
      n.next = '2099-01-01';
      out.futureDateDrops = !_workQueue().some(x => x.r.case === c);
      n.next = '2020-01-01';
      const pos = _workQueue().findIndex(x => x.r.case === c);
      out.overdueRises = pos > -1 && pos < 3;
      delete n.touches; delete n.next; save();
      // the full loop, on a lead that actually has a number
      const q = _workQueue().filter(x => _bestPhones(x.r).length > 0);
      if(!q.length) return Object.assign(out, {skipLoop: true});
      _wq = q; _wqi = 0; _wqShow();
      const bar = document.getElementById('workbar'); const target = q[0].r.case;
      out.opens = !!bar && pinnedCase === target;
      const call = bar.querySelector('.wbcall');
      out.callIsTel = (call.getAttribute('href')||'').indexOf('tel:') === 0;
      call.addEventListener('click', e => e.preventDefault(), {once: true});
      const before = ((notes[target]||{}).touches||[]).length;
      call.click();
      out.armsFromBar = !!(_afterCall && _afterCall.c === target) && viewed.has(target);
      if(_afterCall){
        _afterCall.t = Date.now() - 5000; _showAfterCall();
        const acb = document.getElementById('aftercall');
        out.stacked = acb && acb.classList.contains('hasqueue');
        acb.querySelector('button[data-ql="0"]').click();
        out.logged = ((notes[target]||{}).touches||[]).length === before + 1;
        out.autoAdvanced = document.getElementById('workbar') &&
          /^2\\//.test(document.getElementById('workbar').querySelector('.wbn').textContent);
      }
      const ts = ((notes[target]||{}).touches||[]); ts.pop();
      if(!ts.length) delete (notes[target]||{}).touches;
      (notes[target]||{}).status = ''; (notes[target]||{}).next = ''; save();
      _wqClose(); render();
      return out;
    }""")
    if wq.get('skip'):
        print('  SKIP work-queue test:', wq['skip'])
    else:
        rec('Queue: reachable + unfiled only, ranked by score',
            wq['reachableOnly'] and wq['excludesFiled'] and wq['ranked'], '')
        rec('Queue: a lead touched today drops out until tomorrow', wq['touchedTodayDrops'], '')
        rec('Queue: a follow-up set for the future is not offered yet', wq['futureDateDrops'], '')
        rec('Queue: an OVERDUE follow-up rises to the front', wq['overdueRises'], '')
        rec('Queue: opens pinned on the top lead with a real tel: action',
            wq.get('opens') and wq.get('callIsTel'), '')
        rec('Queue: the bar\'s own call stamps Seen and arms the after-call bar',
            wq.get('armsFromBar'), '')
        rec('Queue: logging the outcome auto-advances to the next lead',
            wq.get('logged') and wq.get('autoAdvanced') and wq.get('stacked'), '')

    # close any leftover modals from earlier checks before hitting the Playbook button
    pg.evaluate("() => { if(typeof closeCallSheet==='function') closeCallSheet(); if(typeof closeDealModal==='function') closeDealModal(); }")
    pg.wait_for_timeout(200)
    # Playbook panel — the field manual on the site
    pgpb = pg.evaluate("() => { return {btn: !!document.getElementById('playbookbtn'), modal: !!document.getElementById('playbookmodal')}; }")
    rec('Playbook: header button + modal exist', pgpb['btn'] and pgpb['modal'], '')
    pg.click('#playbookbtn'); pg.wait_for_timeout(300)
    pb = pg.evaluate("""() => {
      const m = document.getElementById('playbookmodal');
      const vis = m && getComputedStyle(m).display !== 'none';
      const sections = ['pb-rule','pb-exits','pb-script','pb-objections','pb-law','pb-math','pb-dd','pb-felony']
        .map(id => !!document.getElementById(id));
      const tabs = document.querySelectorAll('.pbtab').length;
      const hasRule = document.body.innerText.includes('The Rule');
      const hasFL501 = document.body.innerText.includes('FL 501.1377');
      const has5exits = document.body.innerText.includes('5 Exits');
      return {vis, sections, tabs, hasRule, hasFL501, has5exits};
    }""")
    rec('Playbook: modal opens on button click', bool(pb['vis']), '')
    rec('Playbook: all 8 sections rendered', all(pb['sections']), str(pb['sections']))
    rec('Playbook: 8 tab buttons render', pb['tabs'] == 8, f"{pb['tabs']} tabs")
    rec('Playbook: RULE / 5 EXITS / FL 501.1377 content present', pb['hasRule'] and pb['has5exits'] and pb['hasFL501'], '')
    # tab click jumps to section
    pg.evaluate("() => document.querySelector('.pbtab[data-pb=\"felony\"]').click()")
    pg.wait_for_timeout(300)
    tabActive = pg.evaluate("() => (document.querySelector('.pbtab.active')||{}).dataset?.pb")
    rec('Playbook: tab click switches active state', tabActive == 'felony', str(tabActive))
    pg.keyboard.press('Escape'); pg.wait_for_timeout(200)
    closed = pg.evaluate("() => getComputedStyle(document.getElementById('playbookmodal')).display === 'none'")
    rec('Playbook: Escape closes it', closed, '')
    # keyboard shortcut 'P' opens it (works cross-platform; the ? variant is also wired)
    pg.evaluate("() => document.activeElement && document.activeElement.blur()")   # ensure focus on body
    pg.wait_for_timeout(100)
    pg.keyboard.press('KeyP'); pg.wait_for_timeout(250)
    reopened = pg.evaluate("() => getComputedStyle(document.getElementById('playbookmodal')).display === 'block'")
    rec('Playbook: keyboard shortcut P opens it', reopened, '')
    pg.keyboard.press('Escape'); pg.wait_for_timeout(200)

    # ERR_CONNECTION_REFUSED is the board probing the localhost send bridge (send_server.py) while
    # it is offline — a designed, supported state ("Logged to this phone only" degradation), and
    # the normal one on a test runner. Filtering it keeps the check able to catch REAL errors
    # instead of always drowning in bridge noise. (2026-08-26)
    # [notes-bridge] lines are an INTENTIONAL diagnostic, not a page error: a fresh test tab holds
    # one note, the bridge's richest-wins guard rightly refuses its push, and the board now says so
    # out loud (added 2026-08-26 — 17 real refusals had been silent). Firing here would mean the
    # warning works. Page errors and every other console error still fail this check.
    real = [e for e in errs if 'favicon' not in e and 'ERR_CONNECTION_REFUSED' not in e
            and '[notes-bridge]' not in e]
    rec('No console/page errors', not real, '; '.join(real[:3])[:200])
    b.close()

srv.shutdown()
ok = sum(R); print(f"\n==== {ok}/{len(R)} call-sheet audit checks passed ====")
raise SystemExit(0 if ok == len(R) else 1)
