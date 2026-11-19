"""_coachhandofftest -- one log per call: the docked coach and Call Mode speak one outcome vocabulary.

Run:  python _coachhandofftest.py   (exit 0 = pass; invented case and 555-01xx number, no network,
      the real coach server is never started: 127.0.0.1:8777 is served by Playwright routes)

WHY (2026-11-18): the coach's outcome buttons used their own names (appointment, not_interested,
bad_number, "voicemail / no answer") while Call Mode logs CALL_OUTCOMES codes. The caller logged a
call twice, or the coach's record and Call Mode's disagreed.

Pins:
  - the coach's outcome buttons ARE Call Mode's codes (call_mode.CALL_OUTCOMES), and so is the
    server's CALL_MODE_CODES; the server maps the old names and drops anything else
  - the coach page reads an old-name wrap-up and hands on {code, outcome(old name where one existed)}
  - Call Mode's coachDonePick takes the new code, still takes an old coach's names, refuses
    prototype keys, other origins and unknown codes
  - END TO END (Call Mode page with the real coach page docked in its iframe): picking Appointment in
    the coach lights Appointment on Call Mode's outcome screen and logs NOTHING; one tap on it logs
    exactly one dial, oc 'appt', with the coach's note
  - a coach "Hard no" only lights the button: Call Mode's own confirm still stands between it and
    the permanent do-not-call (dismissed -> nothing written)
  - a message from any other origin is ignored
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='coachhandoff_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402
import call_coach_server as srv  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


CODES = [o[0] for o in call_mode.CALL_OUTCOMES]
coach = open(os.path.join(HERE, 'call_coach_live.html'), encoding='utf-8').read()
grid = coach[coach.index('<div class="grid">'):]
grid = grid[:grid.index('</div>')]
btn = re.findall(r'<button data-oc="([a-z_]+)" onclick="Coach\.finish\(\'([a-z_]+)\'\)">', grid)
rec('every coach outcome button is a Call Mode code, in Call Mode order-free parity',
    sorted(b[0] for b in btn) == sorted(CODES) and all(a == b for a, b in btn), btn)
rec('the coach page code list equals CALL_OUTCOMES',
    sorted(json.loads(re.search(r'var OC_CODES=(\[[^\]]*\])', coach).group(1).replace("'", '"'))) == sorted(CODES))
rec('the server code list equals CALL_OUTCOMES', sorted(srv.CALL_MODE_CODES) == sorted(CODES), srv.CALL_MODE_CODES)
rec('server: old names map to codes, codes pass, auto_silence kept, anything else dropped',
    [srv.outcome_code(x) for x in ('appointment', 'not_interested', 'bad_number', 'appt', 'DNC', 'auto_silence',
                                   'sold_it', '', None, '__proto__')]
    == ['appt', 'notint', 'badnum', 'appt', 'dnc', 'auto_silence', None, None, None, None])
rec('server: every wrap-up word the model may say becomes a Call Mode code',
    all(srv.outcome_code(w) in CODES for w in srv.WRAP_OUTCOMES))

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    sys.exit(1 if FAILS else 0)

CASE = '2026-000777-CA-01'
LEAD = {'case': CASE, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': '12/20/2026', 'days': 32,
        'tier': 'B', 'score': 50, 'value': 400000, 'owners': 'TEST, OWNER', 'oname': 'Owner Test',
        'addr': '100 NW 1 ST, MIAMI, FL 33101', 'pkey': 'C' + CASE,
        'phones': ['3055550177'], 'phdnc': [False], 'phsrc': ['st'], 'phtype': ['mobile']}
rows, _ = call_mode.call_rows([LEAD], None, None)
rec('setup: one callable invented lead', len(rows) == 1 and rows[0]['c'] == CASE, rows[:1])

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 1, "shipped": 1}')

COACH_HIT = {'case': CASE, 'address': '100 NW 1 ST', 'owners': 'TEST, OWNER', 'auction': '12/20/2026', 'days': 32}


def coach_route(route):
    u = route.request.url
    if '/api/lead' in u:
        route.fulfill(status=200, content_type='application/json', body=json.dumps({'matches': [COACH_HIT]}))
    elif '/api/' in u:
        route.fulfill(status=200, content_type='application/json', body='{}')
    else:
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body=coach)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    ctx = b.new_context(viewport={'width': 1400, 'height': 900})
    ctx.route('http://127.0.0.1:8777/**', coach_route)
    # Call Mode ships on https, and the coach posts to its frame's ancestor origin: from a file://
    # parent that origin is opaque and Chrome drops the message, so the built page is served from
    # memory at an invented https origin. Nothing leaves the machine.
    ctx.route('https://callmode.test/**', lambda r: r.fulfill(status=200, content_type='text/html; charset=utf-8', body=html))
    pg = ctx.new_page()
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    dialogs = []

    def on_dialog(d):
        dialogs.append(d.message[:80])
        d.dismiss()
    pg.on('dialog', on_dialog)
    pg.goto('https://callmode.test/call/')
    pg.wait_for_timeout(400)

    # --- Call Mode's trust boundary, in the page ---------------------------------------------
    pk = pg.evaluate("""(c) => {
      const O = 'http://127.0.0.1:8777', base = {src:'bsg-coach', 'case':c, note:'n'};
      const k = d => { const r = coachDonePick(Object.assign({}, base, d), O, 1); return r ? r.k : null; };
      return {code: k({code:'appt', outcome:'appointment'}), noans: k({code:'noanswer', outcome:'noanswer'}),
              old: k({outcome:'not_interested'}), oldvm: k({outcome:'voicemail'}),
              proto: k({outcome:'toString'}), proto2: k({code:'constructor'}), junk: k({code:'sold', outcome:'sold'}),
              origin: coachDonePick(Object.assign({}, base, {code:'appt'}), 'https://evil.example', 1)};
    }""", CASE)
    rec('Call Mode takes the coach\'s Call Mode code', pk['code'] == 'appt' and pk['noans'] == 'noanswer', pk)
    rec('...and still takes an old coach\'s names', pk['old'] == 'notint' and pk['oldvm'] == 'voicemail', pk)
    rec('...and refuses prototype keys, unknown codes and other origins',
        pk['proto'] is None and pk['proto2'] is None and pk['junk'] is None and pk['origin'] is None, pk)

    # --- dial the lead, dock the coach --------------------------------------------------------
    st = pg.evaluate("""(rows) => {
      try { localStorage.clear(); } catch(e) {}
      notes = {}; ROWS = rows; start();
      cur = ROWS[0]; phIdx = 0; screenOutcome();
      coachDock('http://127.0.0.1:8777/?case=' + encodeURIComponent(cur.c));
      return {screen: SCREEN, btns: document.querySelectorAll('.oc button').length,
              dials: ((notes[cur.c] || {}).dials || []).length};
    }""", rows)
    rec('setup: Call Mode outcome screen up, coach docked', st['screen'] == 'outcome' and st['btns'] == len(CODES), st)
    pg.wait_for_timeout(600)
    fr = None
    for f in pg.frames:
        if f.url.startswith('http://127.0.0.1:8777/'):
            fr = f
    rec('the coach page loaded in the dock (routed, no real server)', fr is not None, [f.url for f in pg.frames])
    if fr is None:
        b.close()
        sys.exit(1)
    fr.evaluate("() => Coach.plain()")
    fr.wait_for_timeout(1200)
    cl = fr.evaluate("""() => ({lead: (Coach._test.handoffMsg('appt') || {})['case'],
      a: Coach._test.handoffMsg('appt'), n: Coach._test.handoffMsg('noanswer'), bad: Coach._test.handoffMsg('sold'),
      old: [Coach._test.ocCode('appointment'), Coach._test.ocCode('bad_number'), Coach._test.ocCode('voicemail'), Coach._test.ocCode('x')]})""")
    rec('coach: the lead from Call Mode is attached', cl['lead'] == CASE, cl)
    rec('coach: hands on the Call Mode code, with the old name for an older Call Mode',
        cl['a']['code'] == 'appt' and cl['a']['outcome'] == 'appointment' and cl['n']['code'] == 'noanswer'
        and cl['n']['outcome'] == 'noanswer' and cl['bad'] is None, cl)
    rec('coach: an old-name wrap-up reads as the Call Mode code', cl['old'] == ['appt', 'badnum', 'voicemail', ''], cl)

    # --- the coach picks Appointment -> Call Mode lights it, logs nothing ---------------------
    fr.evaluate("() => { document.getElementById('wrapnote').value = 'Wants to keep it. Advisor call set.'; Coach.finish('appt'); }")
    pg.wait_for_timeout(500)
    lit = pg.evaluate("""() => {
      const lit = Array.from(document.querySelectorAll('.oc button')).filter(b => /outline/.test(b.getAttribute('style') || '')).map(b => b.dataset.oc);
      const n = notes[cur.c] || {};
      return {lit: lit, banner: (document.getElementById('coachdone') || {}).textContent || '',
              k: _coachDone && _coachDone.k, dials: (n.dials || []).length, touches: (n.touches || []).length, status: n.status || ''};
    }""")
    rec('coach Appointment pre-selects Appointment on Call Mode\'s outcome screen', lit['lit'] == ['appt'] and lit['k'] == 'appt', lit)
    rec('...with the confirm banner and the note that will be saved',
        'Tap the lit outcome to log it' in lit['banner'] and 'Wants to keep it' in lit['banner'], lit)
    rec('...and NOTHING is logged by the coach (no dial, no touch, no status)',
        lit['dials'] == 0 and lit['touches'] == 0 and lit['status'] == '', lit)

    # --- one tap on the lit outcome logs it once ---------------------------------------------
    one = pg.evaluate("""() => {
      const c = cur.c;
      document.querySelector('.oc button[data-oc="appt"]').click();
      const n = notes[c] || {};
      return {dials: (n.dials || []).map(d => d.oc), note: n.note || ''};
    }""")
    rec('one tap logs exactly one dial, oc appt', one['dials'] == ['appt'], one)
    rec('...and the coach\'s note rides that one log', 'coach: Wants to keep it. Advisor call set.' in one['note'], one)

    # --- a coach Hard no only lights it; Call Mode's own confirm still guards it --------------
    dn = pg.evaluate("""(rows) => {
      notes = {}; ROWS = rows; cur = ROWS[0]; phIdx = 0; _coachDone = null; screenOutcome();
      return true; }""", rows)
    fr.evaluate("() => { Coach.plain(); }")
    fr.wait_for_timeout(1200)
    fr.evaluate("() => Coach.finish('dnc')")
    pg.wait_for_timeout(500)
    d1 = pg.evaluate("""() => { const n = notes[cur.c] || {};
      const lit = Array.from(document.querySelectorAll('.oc button')).filter(b => /outline/.test(b.getAttribute('style') || '')).map(b => b.dataset.oc);
      return {lit: lit, dials: (n.dials || []).length, status: n.status || '', optout: n.optout || ''}; }""")
    rec('coach Hard no lights Hard no and writes nothing', d1['lit'] == ['dnc'] and d1['dials'] == 0 and d1['status'] == '' and not d1['optout'], d1)
    nd = len(dialogs)
    d2 = pg.evaluate("""() => { document.querySelector('.oc button[data-oc="dnc"]').click(); const n = notes[cur.c] || {};
      return {dials: (n.dials || []).length, status: n.status || '', optout: n.optout || ''}; }""")
    rec('tapping it still asks Call Mode\'s permanent-stop confirm; dismissed -> nothing written',
        len(dialogs) > nd and 'permanently' in dialogs[-1] and d2['dials'] == 0 and d2['status'] == '' and not d2['optout'], [dialogs, d2])

    # --- another origin cannot light anything ------------------------------------------------
    ev = pg.evaluate("""async (c) => { _coachDone = null;
      window.postMessage({src:'bsg-coach', v:2, 'case':c, code:'appt', outcome:'appointment'}, '*');
      await new Promise(r => setTimeout(r, 150)); return _coachDone; }""", CASE)
    rec('a handoff from any other origin is ignored', ev is None, ev)
    rec('no page errors', not [e for e in errs], errs[:3])
    b.close()

print('\n%d failure(s)' % len(FAILS) if FAILS else '\nALL PASS')
sys.exit(1 if FAILS else 0)
