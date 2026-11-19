"""_callfindleadtest -- Call Mode "Find a lead": search every lead on the phone by case, owner or street.

Run:  python _callfindleadtest.py   (exit 0 = safe; invented leads and 555-01xx numbers, no network)

WHY (2026-11-19, Alejandro): "what about all of the leads that aren't on that screen and I'm trying
to look up? Something like a search bar ... that works and looks up the cases with no issue at all."

Pins:
  - the search button is in the top bar on the dial card, the Find a number list and a side screen
  - a case number in any format finds it (CACE-26-..., 26-..., 2026-...-CA-01, digits only), exact
    case first, then case prefix, then name / street
  - an owner name in either order, or part of one; a partial street
  - a case in both ROWS and COV is one result (the dial row)
  - each status in plain words: dial queue, Find a number, other caller's list, teammate on it, held
    (hardSuppressed reason and the coverage row's own hold), not on a call list
  - a dial-queue result opens the card through _openCase
  - a held / other-seat / claimed lead opens a read-only detail with no dial, text or email control
  - a Find a number result lands on its card with the number/email box focused, and an email typed
    there + Call or email opens the prefilled composer (two taps from the search)
  - the last query is kept on this phone and comes back
  - under 50 ms per search on about 4,000 rows
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='findlead_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:500]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def d(n):
    return (dt.date.today() + dt.timedelta(days=n)).strftime('%m/%d/%Y')


def lead(case, owners, oname, addr, **kw):
    r = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': d(10), 'days': 10, 'tier': 'B',
         'score': 50, 'value': 400000, 'owners': owners, 'oname': oname, 'addr': addr, 'pkey': 'C' + case}
    r.update(kw)
    return r


def ph(n):
    return {'phones': ['30555501%02d' % n], 'phdnc': [False], 'phsrc': ['st'], 'phtype': ['mobile']}


# invented people, streets and case numbers
Q_DIAL = lead('2026-099902-CA-01', 'QUILL, MARTA', 'Marta', '8123 SW 157 AVE, MIAMI, FL 33193', **ph(11))
Q_SEAT = lead('2026-099903-CA-01', 'BRANDT, OSCAR', 'Oscar', '77 NE 9 ST, MIAMI, FL 33132', **ph(12))
Q_CLM = lead('2026-099904-CA-01', 'FENWICK, LIDIA', 'Lidia', '501 NW 30 CT, MIAMI, FL 33125', **ph(13))
Q_HELD = lead('2026-099905-CA-01', 'ORTEGA, PABLO', 'Pablo', '940 SW 8 ST, MIAMI, FL 33130', **ph(14))
B_LK = lead('CACE-26-099901', 'ZORILLA, INES', 'Ines', '1450 NW 44 AVE, LAUDERHILL, FL 33313', county='BROWARD')
B_LK2 = lead('2026-099907-CA-01', 'QUILLON, MARCO', 'Marco', '8130 SW 152 AVE, MIAMI, FL 33193')
B_OO = lead('2026-099906-CA-01', 'HALVERSON, GRETA', 'Greta', '33 SW 1 AVE, MIAMI, FL 33130')
SLIM = [Q_DIAL, Q_SEAT, Q_CLM, Q_HELD, B_LK, B_LK2, B_OO]
sink = []
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=sink)
dial_cases = [r['c'] for r in rows]
cov, _ = call_mode.coverage_rows(SLIM, dial_cases, optouts={B_OO['case']: 1})
call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})
rec('setup: four dial rows', sorted(dial_cases) == sorted([Q_DIAL['case'], Q_SEAT['case'], Q_CLM['case'], Q_HELD['case']]),
    dial_cases)
lkc = sorted(c['c'] for c in cov if c.get('lk'))
rec('setup: two leads on Find a number', lkc == sorted([B_LK['case'], B_LK2['case']]), lkc)
rec('setup: the opted-out lead is a coverage row held by the build', any(c['c'] == B_OO['case'] and c.get('oo') for c in cov))

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    sys.exit(1 if FAILS else 0)

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 7, "shipped": 7}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)

SETUP = """([rows, cov, C]) => {
  try { localStorage.clear(); } catch(e) {}
  try { localStorage.setItem('fcSeat', JSON.stringify({n: 2, i: 0, w: 'Ana'})); } catch(e) {}
  notes = {}; LKDONE = {};
  rows.forEach(r => { r.sb = (r.c === C.seat) ? 1 : 0; });
  cov.forEach(r => { if (r.lk) r.sb = 0; });
  /* a case in both arrays: the dial row must win */
  cov.push({c: C.dial, on: 'Shadow', o: 'SHADOW COPY', a: '1 NOWHERE ST', days: 10, ct: 'MI'});
  /* a coverage row with no number and not cleared for lookup */
  cov.push({c: '2025-012345-CA-01', on: 'Teo', o: 'VANCE, TEO', a: '12 NE 2 AVE, MIAMI, FL 33132', days: 200, ct: 'MI'});
  /* a client the build holds */
  cov.push({c: '2025-054321-CA-01', on: 'Rhea', o: 'MOSS, RHEA', a: '19 SW 3 ST, MIAMI, FL 33130', days: 30, ct: 'MI', client: 1});
  ROWS = rows; COV = cov;
  notes[C.held] = {status: 'DO NOT CONTACT'};
  notes[C.clm] = {clm: {d: 'other-device', t: Date.now(), w: 'Carlos'}};
  start();
  return true; }"""

C = {'dial': Q_DIAL['case'], 'seat': Q_SEAT['case'], 'clm': Q_CLM['case'], 'held': Q_HELD['case'],
     'lk': B_LK['case'], 'lk2': B_LK2['case'], 'oo': B_OO['case']}

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    pg = b.new_page(viewport={'width': 390, 'height': 844})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path)
    pg.wait_for_timeout(400)
    pg.evaluate(SETUP, [rows, cov, C])

    # 1. the button is in the top bar on several screens
    btn = pg.evaluate("""() => {
      const o = {};
      SCREEN = 'lead'; BLANE = null; render();
      o.card = !!document.querySelector('.top [data-find]');
      document.querySelector('.top [data-find]').click();
      o.openFromCard = SCREEN;
      flClose();
      BLANE = '__lk'; SCREEN = 'lead'; render();
      o.lk = !!document.querySelector('.top [data-find]');
      BLANE = null; SCREEN = 'lead'; render();
      screenCallStats();
      o.side = !!document.querySelector('[data-find]');
      document.querySelector('[data-find]').click();
      o.openFromSide = SCREEN;
      flClose();
      o.backFromSide = SCREEN;
      return o; }""")
    rec('search button on the dial card', btn['card'], btn)
    rec('...opens the search screen', btn['openFromCard'] == 'find', btn)
    rec('search button on the Find a number list', btn['lk'], btn)
    rec('search button on a side screen (call stats), and it opens there too',
        btn['side'] and btn['openFromSide'] == 'find', btn)
    rec('Back from the search returns to the queue', btn['backFromSide'] == 'lead', btn)

    def search(q):
        return pg.evaluate("(q) => flSearch(q).map(e => ({c: e.c, dial: e.dial, s: flStatus(e)}))", q)

    # 2. case number formats
    for q in ('CACE-26-099901', '26-099901', '2026-099901', 'cace26099901', '099901'):
        r = search(q)
        rec('case format %r finds the Broward lead first' % q, r and r[0]['c'] == C['lk'], r[:3])
    for q in ('2026-099902-CA-01', '26-099902', '099902', '2026099902'):
        r = search(q)
        rec('case format %r finds the Miami-Dade lead first' % q, r and r[0]['c'] == C['dial'], r[:3])
    r = search('2026-09990')
    rec('a case prefix lists every case that starts with it',
        set(x['c'] for x in r) >= {C['dial'], C['seat'], C['clm'], C['held'], C['lk'], C['lk2'], C['oo']}, r)
    r = search('26-099905')
    rec('exact case ranks above case prefixes', r and r[0]['c'] == C['held'], r[:3])

    # 3. names in both orders, partials; street fragment
    for q in ('Marta Quill', 'QUILL, MARTA', 'quill marta', 'mar qui'):
        r = search(q)
        rec('name %r finds the lead' % q, r and r[0]['c'] == C['dial'], r[:3])
    r = search('quill')
    rec('a whole-word name match ranks above a partial one (QUILL before QUILLON)',
        [x['c'] for x in r[:2]] == [C['dial'], C['lk2']], r)
    r = search('8123 SW 157')
    rec('partial street finds the lead', r and r[0]['c'] == C['dial'], r[:3])
    r = search('8123 sw 157th')
    rec('an ordinal typed or not ("157th" / "157") finds the same street', r and r[0]['c'] == C['dial'], r[:3])
    r = search('SW 15')
    rec('a street fragment matches both SW 15x streets', {x['c'] for x in r} >= {C['dial'], C['lk2']}, r)
    rec('nonsense finds nothing', search('zzqx') == [])

    # 4. dedupe
    r = search(C['dial'])
    rec('a case in both ROWS and COV is one result, the dial row',
        len([x for x in r if x['c'] == C['dial']]) == 1 and r[0]['dial'] is True, r)
    rec('...and the shadow coverage name is not searchable as a second lead', search('Shadow') == [], search('Shadow'))

    # 5. each status
    def st(c):
        r = search(c)
        return r[0]['s'] if r else None
    s = st(C['dial']); rec('status: In your dial queue', s and s['k'] == 'queue' and s['t'] == 'In your dial queue', s)
    s = st(C['lk']); rec('status: On Find a number', s and s['k'] == 'lk' and s['t'] == 'On Find a number', s)
    s = st(C['seat']); rec("status: On the other caller's list", s and s['k'] == 'seat' and s['t'] == "On the other caller's list", s)
    s = st(C['clm']); rec('status: A teammate is on it right now', s and s['k'] == 'claim' and s['w'] == 'Carlos', s)
    s = st(C['held']); rec('status: Held with the hardSuppressed reason', s and s['k'] == 'held' and s['t'] == 'Held: opted out', s)
    s = st(C['oo']); rec("status: Held with the coverage row's own hold", s and s['k'] == 'held' and 'do-not-contact' in s['t'], s)
    s = st('2025-054321'); rec('status: a client is held', s and s['k'] == 'held' and 'client' in s['t'], s)
    s = st('2025-012345'); rec('status: a lead on no list says so', s and s['k'] == 'off' and s['t'].startswith('Not on a call list'), s)

    # 6. the results screen shows owner, street, case, county, timing, status
    shown = pg.evaluate("""() => { screenFind(); const i = document.getElementById('flq');
      i.value = 'zorilla'; i.dispatchEvent(new Event('input'));
      return new Promise(res => setTimeout(() => { const el = document.querySelector('.flres');
        res(el ? el.innerText : ''); }, 250)); }""")
    for bit in ('ZORILLA, INES', '1450 NW 44', 'CACE-26-099901', 'Broward', 'Sale ', 'On Find a number'):
        rec('result card shows %r' % bit, bit in shown, shown)

    # 7. held / other seat / claim open read-only
    def tap(c):
        return pg.evaluate("""(c) => { screenFind(); const i = document.getElementById('flq'); i.value = c;
          i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}));
          const el = document.querySelector('.flres'); if (!el) return null; el.click();
          const app = document.getElementById('app');
          const det = document.querySelector('.fldet');
          return {screen: SCREEN, cur: cur && cur.c, blane: BLANE,
                  links: app.querySelectorAll('a[href^="tel:"], a[href^="sms:"], a[href^="mailto:"]').length,
                  btns: det ? Array.from(det.querySelectorAll('button')).map(b => b.id) : null,
                  text: det ? det.innerText : '',
                  active: document.activeElement ? (document.activeElement.className + '|' + (document.activeElement.getAttribute('data-c') || '')) : ''}; }""", c)
    for c, label in ((C['held'], 'held dial lead'), (C['oo'], 'held coverage lead'), (C['seat'], 'other-seat lead'),
                     (C['clm'], 'claimed lead'), ('2025-012345-CA-01', 'lead on no list')):
        t = tap(c)
        rec('%s opens a read-only detail with no dial, text or email action' % label,
            t and t['screen'] == 'finddetail' and t['links'] == 0 and t['btns'] == ['fldback'], t)
    t = tap(C['held'])
    rec('the held detail names the reason', t and 'Held: opted out' in t['text'], t)

    # 8. dial queue result opens the card
    t = tap(C['dial'])
    rec('a dial-queue result opens its card via _openCase', t and t['screen'] == 'lead' and t['cur'] == C['dial'], t)

    # 9. Find a number: two taps to the box, an email opens the prefilled composer
    pg.evaluate("() => { SCREEN = 'lead'; BLANE = null; render(); }")
    pg.click('.top [data-find]')                      # tap 1 opens the search
    pg.fill('#flq', '')
    pg.keyboard.type('Ines Zorilla')
    pg.wait_for_timeout(300)
    pg.click('.flres')                                # tap 2 lands on the lead's box
    land = pg.evaluate("""(c) => { const a = document.activeElement;
      const card = document.querySelector('.lkcard[data-lkc="' + c + '"]');
      const rc = card ? card.getBoundingClientRect() : null;
      return {screen: SCREEN, blane: BLANE, focus: LKFOCUS,
              active: a && a.classList.contains('lknum') ? a.getAttribute('data-c') : null,
              visible: !!rc && rc.bottom > 0 && rc.top < window.innerHeight}; }""", C['lk'])
    rec('a Find a number result lands on that list', land['screen'] == 'lead' and land['blane'] == '__lk', land)
    rec('...on its card, focused and in view', land['focus'] == C['lk'] and land['visible'], land)
    rec('...with its number/email box focused', land['active'] == C['lk'], land)
    pg.keyboard.type('ines.test@example.com')
    # the do-not-contact list hold is a gate: with it on, the composer refuses (nothing loosened)
    held = pg.evaluate("""(c) => { const was = [TEXTHOLDLIVE, TEXTHOLD]; TEXTHOLDLIVE = true; TEXTHOLD = {held: true, why: 'stale'};
      document.querySelector('button.lkadd[data-c="' + c + '"]').click(); const s = SCREEN;
      TEXTHOLDLIVE = was[0]; TEXTHOLD = was[1]; return s; }""", C['lk'])
    rec('with the do-not-contact list hold on, the email composer stays shut', held == 'lead', held)
    # a fresh list (as the bridge reports it): the composer opens
    pg.evaluate("() => { TEXTHOLDLIVE = true; TEXTHOLD = {held: false}; }")
    pg.click('button.lkadd[data-c="%s"]' % C['lk'])
    mail = pg.evaluate("""(c) => ({screen: SCREEN, to: (document.querySelector('.mailcard') || {}).innerText || '',
                                  body: ((document.querySelector('.mailbody') || {}).textContent || '').length})""", C['lk'])
    rec('pasting an email and tapping Call or email opens the prefilled template',
        mail['screen'] == 'mail' and 'ines.test@example.com' in mail['to'] and mail['body'] > 40, mail)

    # 10. last query remembered on this phone
    rem = pg.evaluate("""() => { const saved = localStorage.getItem('fcFindQ');
      _FLQ = ''; SCREEN = 'lead'; BLANE = null; screenFind();
      return {saved: saved, box: document.getElementById('flq').value}; }""")
    rec('the last query is saved on this phone', rem['saved'] == 'Ines Zorilla', rem)
    rec('...and is back in the box next time', rem['box'] == 'Ines Zorilla', rem)
    rem2 = pg.evaluate("""() => { const k = Storage.prototype.getItem; Storage.prototype.getItem = function(){ throw new Error('blocked'); };
      let ok = true; try { _FLQ = ''; screenFind(); } catch(e) { ok = false; } Storage.prototype.getItem = k; return ok; }""")
    rec('blocked storage does not break the search', rem2)

    # 11. a gate is never loosened: opted out after the list was drawn -> no card
    gate = pg.evaluate("""(c) => { notes[c] = {status: 'DO NOT CONTACT'}; SCREEN = 'lead'; render();
      const e = flSearch(c)[0]; flOpen(e); return {screen: SCREEN, cur: cur && cur.c}; }""", C['dial'])
    rec('a lead opted out after the search opens read-only, not the card', gate['screen'] == 'finddetail', gate)

    # 12. speed on about 4,000 rows
    perf = pg.evaluate("""() => {
      const F = ['ALVAREZ','BAKER','CRUZ','DIAZ','EVANS','FLORES','GARCIA','HILL','IBARRA','JONES','KIM','LOPEZ','MORA','NUNEZ','OWENS','PEREZ'];
      const G = ['ANA','BEN','CARLA','DAVID','ELENA','FRANK','GINA','HUGO','IRIS','JUAN'];
      for (let k = 0; k < 4000; k++) {
        COV.push({c: '2025-' + String(100000 + k) + '-CA-01', o: F[k % 16] + ', ' + G[k % 10] + ' ' + F[(k * 7) % 16],
                  on: G[k % 10], a: (100 + k) + ' SW ' + (k % 200) + ' AVE, MIAMI, FL 33' + String(100 + k % 99), days: k % 300, ct: 'MI'});
      }
      const qs = ['garcia', 'Juan Lopez', 'LOPEZ, JUAN', '2025-10', '25-103999', '1234 SW 34', 'sw 1', 'a', 'mor ana', 'CACE-26-099901'];
      const t0 = performance.now(); flSearch('garcia'); const first = performance.now() - t0;
      let worst = 0, n = 0, tot = 0;
      for (let rep = 0; rep < 5; rep++) qs.forEach(q => { const t = performance.now(); flSearch(q); const dt = performance.now() - t;
        worst = Math.max(worst, dt); tot += dt; n++; });
      const exact = flSearch('25-103999')[0];
      return {rows: _flIndex().length, first: first, worst: worst, avg: tot / n, exact: exact && exact.c}; }""")
    rec('index covers about 4,000 rows', perf['rows'] >= 4000, perf)
    rec('first search (index build included) under 50 ms', perf['first'] < 50, perf)
    rec('every search under 50 ms on about 4,000 rows', perf['worst'] < 50, perf)
    rec('exact case still first in the big book', perf['exact'] == '2025-103999-CA-01', perf)
    print('   timing: first %.1f ms, worst %.1f ms, avg %.2f ms over %d rows' % (perf['first'], perf['worst'], perf['avg'], perf['rows']))

    # typing into the box at speed: debounced, results land once
    typed = pg.evaluate("() => { SCREEN = 'lead'; screenFind(); return true; }")
    pg.fill('#flq', '')
    t0 = pg.evaluate('performance.now()')
    pg.keyboard.type('Juan Lopez', delay=15)
    pg.wait_for_timeout(300)
    n_res = pg.evaluate("document.querySelectorAll('.flres').length")
    rec('typing a name at speed shows the top 25', n_res == 25, n_res)
    b.close()

rec('no script error', not errs, errs)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
