"""_textreplytest -- the notice text's C / R replies route somewhere: C books it, R flags the reschedule.

Run:  python _textreplytest.py   (exit 0 = safe; invented 555-01xx numbers, no network)

WHY (2026-11-20): the shipped first text says "Text C to confirm or R to reschedule" and nothing
listened. Replies land on his phone, so the Who-texted-me screen is the router.

Pins:
  - the lookup card for a replying number shows "Texted C" and "Texted R" buttons
  - C: inbound text touch, replied flag, status Appointment (never walking back Under contract),
    stageAt stamped, nextStep = next business day 9:00 am, n.next mirrored, both buttons disable
  - R: inbound text touch, replied flag, status left alone when already set (else Contacted),
    nextStep = call to pick the new time
  - a DO NOT CONTACT lead shows neither button (the generic replied flag stays)
  - the default date in the text body itself is the same next business day, spelled out
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='textreply_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


_nb = dt.date.today() + dt.timedelta(days=1)
while _nb.weekday() >= 5:
    _nb += dt.timedelta(days=1)
NB_ISO = _nb.isoformat()
NB_LONG = '%s, %s %d' % (_nb.strftime('%A'), _nb.strftime('%B'), _nb.day)

AUC = (dt.date.today() + dt.timedelta(days=29)).strftime('%m/%d/%Y')


def lead(case, i):
    return {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': AUC, 'days': 29, 'tier': 'B', 'score': 50,
            'value': 400000, 'owners': 'TESTER, DANA %02d' % i, 'oname': 'Dana Tester',
            'addr': '%d00 NW 1 ST, MIAMI, FL 33101' % (i + 1), 'pkey': 'C' + case,
            'phones': ['30555503%02d' % i], 'phdnc': [False], 'phsrc': ['st'], 'phtype': ['mobile']}


SLIM = [lead('CACE2600500%d' % i, i) for i in range(3)]
sink = []
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=sink)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})
C0, C1, C2 = [x['case'] for x in SLIM]
N0, N1, N2 = [x['phones'][0] for x in SLIM]

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    sys.exit(0)

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 6, "shipped": 6}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    ctx = b.new_context(viewport={'width': 390, 'height': 844})
    pg = ctx.new_page()
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path)
    pg.wait_for_timeout(400)
    o = pg.evaluate("""([rows, cov, C0, C1, C2, N0, N1, N2]) => {
      const o = {};
      try { localStorage.clear(); } catch(e) {} notes = {};
      ROWS = JSON.parse(JSON.stringify(rows)); COV = JSON.parse(JSON.stringify(cov));
      if (typeof PHIDX !== 'undefined') PHIDX = {d: {}, t: [], h: {}};   // baked index empty: these are SAVED numbers
      notes[C0] = {status:'', note:'', phone: N0};
      notes[C1] = {status:'', note:'', phone: N1};
      notes[C2] = {status:'', note:'', phone: N2};
      start();
      const look = (num) => { screenLookup(); const q = document.getElementById('lkq');
        q.value = num; q.dispatchEvent(new Event('input')); return document.getElementById('lkout'); };
      /* C on a fresh lead */
      let box = look(N0);
      o.btns = {c: !!box.querySelector('.lkrc'), r: !!box.querySelector('.lkrr'), rep: !!box.querySelector('.lkrep')};
      box.querySelector('.lkrc[data-c="' + C0 + '"]').click();
      const n0 = notes[C0];
      o.afterC = {status: n0.status, stageAt: (n0.stageAt || {})['Appointment'], next: n0.next,
                  step: n0.nextStep, replied: n0.replied,
                  touch: (n0.touches || []).slice(-1)[0],
                  cDis: box.querySelector('.lkrc').disabled, rDis: box.querySelector('.lkrr').disabled};
      /* C never walks back a later stage */
      notes[C1].status = 'Under contract';
      box = look(N1);
      box.querySelector('.lkrc[data-c="' + C1 + '"]').click();
      o.noWalkBack = notes[C1].status;
      /* R on a lead with no status */
      box = look(N2);
      box.querySelector('.lkrr[data-c="' + C2 + '"]').click();
      const n2 = notes[C2];
      o.afterR = {status: n2.status, step: n2.nextStep, next: n2.next, replied: n2.replied,
                  touch: (n2.touches || []).slice(-1)[0]};
      /* a held lead shows neither router button */
      notes[C0].status = 'DO NOT CONTACT';
      box = look(N0);
      o.dnc = {c: !!box.querySelector('.lkrc'), r: !!box.querySelector('.lkrr'),
               bar: box.textContent.indexOf('DO NOT CONTACT') >= 0, rep: !!box.querySelector('.lkrep')};
      /* the text body names the same next business day */
      o.body = boardTextBody(ROWS[2]);
      return o; }""", [rows, cov, C0, C1, C2, N0, N1, N2])
    b.close()

rec('the lookup card shows Texted C, Texted R and the generic replied flag',
    o['btns'] == {'c': True, 'r': True, 'rep': True}, o['btns'])
rec('C books it: status Appointment, stage stamped today', o['afterC']['status'] == 'Appointment'
    and o['afterC']['stageAt'] == dt.date.today().isoformat(), o['afterC'])
rec('...next step is the next business day, 9:00 am, mirrored to n.next',
    o['afterC']['next'] == NB_ISO and o['afterC']['step'] == {'date': NB_ISO, 'what': 'Consultation 9:00 am - they texted C'}, o['afterC'])
rec('...logged as an INBOUND text touch, replied flag set',
    o['afterC']['touch']['ch'] == 'text' and 'TEXTED C' in o['afterC']['touch']['out']
    and o['afterC']['replied'] == dt.date.today().isoformat(), o['afterC'])
rec('...both router buttons disable after the tap', o['afterC']['cDis'] and o['afterC']['rDis'], o['afterC'])
rec('C never walks back a later stage', o['noWalkBack'] == 'Under contract', o['noWalkBack'])
rec('R flags the reschedule: touch, replied, Contacted, next step says call to pick the time',
    'TEXTED R' in o['afterR']['touch']['out'] and o['afterR']['status'] == 'Contacted'
    and o['afterR']['next'] == NB_ISO and 'new time' in o['afterR']['step']['what'], o['afterR'])
rec('a DO NOT CONTACT lead shows neither router button (hold bar shown, generic flag stays)',
    o['dnc'] == {'c': False, 'r': False, 'bar': True, 'rep': True}, o['dnc'])
rec('the text body names the same day: "on %s at 9:00 am"' % NB_LONG,
    ('on %s at 9:00 am' % NB_LONG) in o['body'], o['body'][:220])
rec('no script error', not errs, errs)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
