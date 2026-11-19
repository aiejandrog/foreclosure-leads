"""_callcopyaddrtest -- Call Mode "Copy address": the Whitepages search-bar spelling of a lead's address.

Run:  python _callcopyaddrtest.py   (exit 0 = safe; invented addresses, no network)

WHY (2026-11-18, Alejandro): agents copy the address off the card and paste it into the Whitepages
reverse-address search bar. Ours reads "5800 MARGATE BLVD APT 111, MARGATE, 33063"; Whitepages matches
"5800 Margate Blvd Apt 111, Margate, FL 33063": "Apt" for # / unit, ordinal numbered streets
("NW 123rd Ter"), title case, state before the ZIP.

Pins: wpSearchAddr formats each shape below; the Find a number card and the lead card paste box both
carry the button; tapping it writes that string to the clipboard; the Whitepages /address/ link is
unchanged by the refactor (wpStreet now shared as wpStreetFmt).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='copyaddr_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


CASES = [
    ('5800 MARGATE BLVD APT 111, MARGATE, 33063', '5800 Margate Blvd Apt 111, Margate, FL 33063'),
    ('2929 NW 123 TER #B105, MIAMI, FL 33167', '2929 NW 123rd Ter Apt B105, Miami, FL 33167'),
    ('200 NW 2 AVE, MIAMI, FL- 33101', '200 NW 2nd Ave, Miami, FL 33101'),
    ('100 NW 1 ST, MIAMI GARDENS, FL 33169', '100 NW 1st St, Miami Gardens, FL 33169'),
    ('1500 SW 13 CT UNIT 4, POMPANO BEACH, FL 33069-1234', '1500 SW 13th Ct Apt 4, Pompano Beach, FL 33069'),
    ('7700 SW 112 ST, MIAMI, 33156', '7700 SW 112th St, Miami, FL 33156'),
    ('100 SW 5 ST 9-14, MIAMI, FL 33130', '100 SW 5th St Apt 9-14, Miami, FL 33130'),
    ('300 NE 2 AVE 6-3, MIAMI, FL 33132', '300 NE 2nd Ave Apt 6-3, Miami, FL 33132'),
    ('400 SW 8 ST 12, MIAMI, FL 33130', '400 SW 8th St Apt 12, Miami, FL 33130'),
    ('500 US HIGHWAY 1, MIAMI, FL 33156', '500 US Highway 1, Miami, FL 33156'),
    ('50 E 1 AVE BLDG 3 APT 4, MIAMI, FL 33132', '50 E 1st Ave Bldg 3 Apt 4, Miami, FL 33132'),
    ('10 OCEAN DR PH 2, MIAMI BEACH, FL 33139', '10 Ocean Dr Ph 2, Miami Beach, FL 33139'),
    ('1000 W 2ND AVE LOT 12, HIALEAH, FL 33010', '1000 W 2nd Ave Lot 12, Hialeah, FL 33010'),
    ('123 NW 5TH ST 33125', '123 NW 5th St, FL 33125'),
    ('12 1/2 SW 3 ST, MIAMI, FL 33130', '12 1/2 SW 3rd St, Miami, FL 33130'),
    ('100 X ST, MIAMI, FL 331451234', '100 X St, Miami, FL 33145'),
    ('15 HWY A1A, FORT LAUDERDALE, FL 33316', '15 Hwy A1A, Fort Lauderdale, FL 33316'),
]

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    sys.exit(0)


def lead(case, addr, **kw):
    d = {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': '11/20/2026', 'days': 29, 'tier': 'B', 'score': 50,
         'value': 400000, 'owners': 'TEST OWNER ' + case, 'oname': 'Test Owner', 'addr': addr, 'pkey': 'C' + case}
    d.update(kw)
    return d


NOPH = lead('CACE26000901', '5800 MARGATE BLVD APT 111, MARGATE, FL 33063')
PH = lead('CACE26000902', '2929 NW 123 TER #B105, MIAMI, FL 33167', phones=['3055550101'], phdnc=[False],
          phsrc=['st'], phtype=['mobile'])
sink = []
rows, _ = call_mode.call_rows([NOPH, PH], None, None, lookup=sink)
cov, _ = call_mode.coverage_rows([NOPH, PH], [r['c'] for r in rows])
call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 2, "shipped": 2}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    pg = b.new_page(viewport={'width': 390, 'height': 900})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path)
    pg.wait_for_timeout(400)
    got = pg.evaluate("(cs) => cs.map(a => wpSearchAddr({a: a}))", [c[0] for c in CASES])
    for (src, want), g in zip(CASES, got):
        rec('formats %r' % src, g == want, g)
    rec('no address -> no button', pg.evaluate("wpCopyBtn({a: ''}) === ''"))
    out = pg.evaluate("""async ([rows, cov, cNo, cPh]) => {
      const o = {};
      try { localStorage.clear(); } catch(e) {}
      notes = {}; ROWS = rows; COV = cov; start();
      let copied = null;
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
        writeText: t => { copied = t; return Promise.resolve(); }, readText: () => Promise.resolve('') }});
      BLANE = '__lk'; SCREEN = 'lead'; render();
      const lb = document.querySelector('.lkcard[data-lkc="' + cNo + '"] button.wpcopy');
      o.listBtn = !!lb;
      if (lb) { lb.click(); await new Promise(r => setTimeout(r, 50)); }
      o.listCopied = copied;
      o.focusKept = LKFOCUS;
      copied = null;
      const wl = Array.from(document.querySelectorAll('.lkcard[data-lkc="' + cNo + '"] .flinks a')).find(x => /reverse-address#/.test(x.href));
      o.linkThere = !!wl;
      if (wl) { wl.addEventListener('click', e => e.preventDefault()); wl.click(); await new Promise(r => setTimeout(r, 50)); }
      o.linkCopied = copied;
      /* the copy must never stop the link from opening */
      let prevented = null;
      if (wl) { const h = e => { prevented = e.defaultPrevented; }; wl.parentNode.addEventListener('click', h, true); wl.click(); wl.parentNode.removeEventListener('click', h, true); }
      o.linkPrevented = prevented;
      /* an older a.wpcopy elsewhere on the page is not ours: no copy */
      copied = null;
      const old = document.createElement('a'); old.className = 'wpcopy'; old.href = '#x'; document.body.appendChild(old);
      old.addEventListener('click', e => e.preventDefault()); old.click(); await new Promise(r => setTimeout(r, 30));
      o.oldCopied = copied; old.remove();
      /* a quote or tag in county data stays inside the attribute */
      const box = document.createElement('div'); box.innerHTML = wpCopyBtn({a: '1 O"NEIL <b>X</b> ST, MIAMI, FL 33101'});
      o.quoteOk = box.querySelectorAll('button.wpcopy').length === 1 && !box.querySelector('b');
      copied = null;
      _openCase(cPh);
      const cb = document.querySelector('.pastecall button.wpcopy');
      o.cardBtn = !!cb;
      if (cb) { cb.click(); await new Promise(r => setTimeout(r, 50)); }
      o.cardCopied = copied;
      o.wpLink = (fileLinks({a: '2929 NW 123 TER #B105, MIAMI, FL 33167', o: 'X'}).find(x => x[0] === 'Whitepages addr') || [])[1];
      return o; }""", [rows, cov, NOPH['case'], PH['case']])
    b.close()

rec('Find a number card has Copy address', out['listBtn'], out)
rec('...tapping it copies the Whitepages spelling', out['listCopied'] == '5800 Margate Blvd Apt 111, Margate, FL 33063', out)
rec('Find a number card: the Whitepages addr link is the reverse-address search', out['linkThere'], out)
rec('...tapping the link copies the search-bar spelling too', out['linkCopied'] == '5800 Margate Blvd Apt 111, Margate, FL 33063', out)
rec('...and the link still opens (not prevented by the copy)', out['linkPrevented'] is False, out)
rec('an older a.wpcopy link is not hijacked by the Copy address listener', out['oldCopied'] is None, out)
rec('a quote or tag in the address cannot break out of the button', out['quoteOk'], out)
rec('lead card paste box has Copy address', out['cardBtn'], out)
rec('...tapping it copies the Whitepages spelling', out['cardCopied'] == '2929 NW 123rd Ter Apt B105, Miami, FL 33167', out)
rec('Whitepages addr link opens the reverse-address search carrying the copy string',
    out['wpLink'] == 'https://www.whitepages.com/reverse-address#' + '2929 NW 123rd Ter Apt B105, Miami, FL 33167'.replace(' ', '%20').replace(',', '%2C'), out)
rec('no script error', not errs, errs)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
