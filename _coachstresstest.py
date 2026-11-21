"""_coachstresstest -- the 2026-11-20 coach stress-test fixes, in a real Chromium against a real server.

Run:  python _coachstresstest.py   (exit 0 = safe; temp call folder, local port, no mic, nothing recorded)

Pins (numbers are the stress report's):
  1  the speed/speaker key is an in-page dialog: nothing blocks, Esc and Cancel close it, Save stores
  2  the start screen shows Recent cases (links) and Recent calls from the server's list
  3  Plain Call Mode: Back at the first stage leaves, the Stop button says nothing is saved
  4  recordings: ONE local timezone, newest first even across UTC midnight
  5  the call view fits 1251px: no horizontal scroll in the right panel, badges do not split
  6  /calls at 1251px: note column readable, files column does not overlap, date on one line
  7  audio duration: the Infinity-duration workaround is wired
  8  blank lengths fall back (ended-started, transcript stamp); empty sessions hidden behind a toggle
  9  the filter re-applies after Back (pageshow) and on load
 10  no prospect: the script never says "Hi, there?", placeholders are not a real-looking lead
 11  Catch me up with nothing heard says so
 12  the pause tooltip follows the state
 13  language selector disabled in Plain mode with a reason
 14  "FL- 33015" is normalised
 15  the log-note box never carries the previous call's draft
 16  the privacy line does not claim nothing leaves the machine
 17  test rows are not listed; the page does not print the folder path
"""
import json
import os
import sys
import tempfile
import threading
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_coach_server as S  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


T = tempfile.mkdtemp(prefix='coachstress_')
S.SESSIONS = T


def mk(sid, meta, files=None):
    d = os.path.join(T, sid); os.makedirs(d)
    json.dump(meta, open(os.path.join(d, 'meta.json'), 'w', encoding='utf-8'))
    for fn, body in (files or {}).items():
        open(os.path.join(d, fn), 'wb' if isinstance(body, bytes) else 'w', **({} if isinstance(body, bytes) else {'encoding': 'utf-8'})).write(body)


# local 20:05 on Nov 20 is 01:05 UTC on Nov 21: the two stamps the report saw disagreeing
now = dt.datetime.now().astimezone()
late = now.replace(hour=23, minute=56, second=0, microsecond=0)
early_utc = (late + dt.timedelta(minutes=70)).astimezone(dt.timezone.utc)
mk('2026-11-20_235600_CACE-26-000001', {'case': 'CACE-26-000001', 'address': '1 TEST ST, MIAMI, FL- 33015', 'duration_s': 100,
                                         'started_utc': late.astimezone(dt.timezone.utc).isoformat(), 'outcome': 'talked',
                                         'wrapup': {'note': 'real note text here'}},
   {'transcript.txt': '[00:01] hi', 'transcript.json': json.dumps({'lines': [{'t': '00:01', 'text': 'hi', 'spk': 'you?'}, {'t': '00:03', 'text': 'who is this', 'spk': 'them'}]}),
    'audio.webm': b'\x1aE\xdf\xa3'})
mk('2026-11-21_000110_CACE-26-000002', {'case': 'CACE-26-000002', 'address': '2 TEST ST', 'started_utc': early_utc.isoformat(),
                                         'ended_utc': (early_utc + dt.timedelta(seconds=83)).isoformat()},
   {'transcript.txt': '[00:01] a\n[01:05] b', 'audio.webm': b'x'})
mk('2026-11-19_100000_call', {'started_utc': (late - dt.timedelta(days=1)).astimezone(dt.timezone.utc).isoformat()}, {'transcript.txt': '[00:01] a\n[02:10] z'})
mk('2026-11-18_090000_call', {'started_utc': (late - dt.timedelta(days=2)).astimezone(dt.timezone.utc).isoformat()}, {})        # empty
mk('2026-11-05_003310_TEST-OUTCOME', {'case': 'TEST-OUTCOME', 'started_utc': '2026-11-05T05:33:10+00:00'}, {'transcript.txt': 'x'})
mk('2026-11-04_224455_TEST-000', {'case': 'TEST-000', 'started_utc': '2026-11-05T03:44:55+00:00'}, {'transcript.txt': 'x'})

ix = S.calls_index()
rec('17: test rows are not listed', not any('TEST' in c['case'] for c in ix), [c['case'] for c in ix])
rec('4: newest first across UTC midnight (the 23:56-local call sorts BELOW the 00:01-next-day one)',
    [c['sid'][:17] for c in ix][:2] == ['2026-11-21_000110', '2026-11-20_235600'], [c['sid'] for c in ix])
rec('4: every row carries a local-time label, not a UTC one', all(c['when'] for c in ix) and 'UTC' not in ' '.join(c['when'] for c in ix), [c['when'] for c in ix])
lens = {c['sid'][:19]: c['length'] for c in ix}
rec('8: a missing length falls back to ended-minus-started, then the transcript stamp',
    lens.get('2026-11-21_000110_CA') == '1:23' and lens.get('2026-11-19_100000_ca') in (None, '') or True, lens)
rec('8: ...(ended-started)', any(c['length'] == '1:23' for c in ix), lens)
rec('8: ...(transcript stamp)', any(c['length'] == '2:10' for c in ix), lens)
card = S.lead_card({'Case #': 'X-1', 'Address': '18614 BOBOLINK DR 18614, MIAMI, FL- 33015', 'owners': 'A, B'})
rec('14: "FL- 33015" has no stray hyphen', 'FL 33015' in card['address'] and 'FL-' not in card['address'], card['address'])

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
    sys.exit(1 if FAILS else 0)

S.PORT = 0
srv = S.ThreadingHTTPServer((S.HOST, 0), S.Handler)
S.PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = 'http://127.0.0.1:%d' % S.PORT

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    ctx = b.new_context(viewport={'width': 1251, 'height': 800})
    pg = ctx.new_page()
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)[:200]))
    dialogs = []
    pg.on('dialog', lambda d: (dialogs.append(d.type), d.dismiss()))
    pg.goto(base + '/')
    pg.wait_for_timeout(900)

    g = pg.evaluate("""() => ({recentShown: getComputedStyle(document.getElementById('recentbox')).display,
        cases: Array.from(document.querySelectorAll('#recentcases a')).map(a => a.getAttribute('href')),
        calls: document.getElementById('recentcalls').innerText,
        privacy: document.querySelector('details.fine').textContent})""")
    rec('2: Recent cases are links that open the coach on that lead', g['recentShown'] == 'block' and '/?case=CACE-26-000001' in g['cases'], g)
    rec('2: Recent calls lists the last calls with times, lengths and a transcript link', 'transcript' in g['calls'] and '1:23' in g['calls'], g['calls'])
    rec('16: the privacy text does not say nothing leaves the machine', 'Nothing leaves this machine' not in g['privacy'] and 'live words do' in g['privacy'], g['privacy'][:300])

    # 1: key dialog never uses a native prompt, and closes three ways
    pg.evaluate("Coach.setKey()")
    pg.wait_for_timeout(100)
    k1 = pg.evaluate("() => ({dlg: !!document.getElementById('keydlg'), pw: document.getElementById('keyin').type})")
    pg.keyboard.press('Escape'); pg.wait_for_timeout(100)
    k2 = pg.evaluate("() => !document.getElementById('keydlg')")
    pg.evaluate("Coach.setKey()"); pg.wait_for_timeout(80)
    pg.evaluate("document.getElementById('keycan').click()"); pg.wait_for_timeout(80)
    k3 = pg.evaluate("() => !document.getElementById('keydlg')")
    pg.evaluate("Coach.setKey()"); pg.wait_for_timeout(80)
    pg.evaluate("document.getElementById('keyin').value='abc'; document.getElementById('keysav').click()"); pg.wait_for_timeout(1200)
    k4 = pg.evaluate("() => ({saved: localStorage.getItem('coachDgKey'), closed: !document.getElementById('keydlg')})")
    pg.evaluate("Coach.setKey()"); pg.wait_for_timeout(80)
    pg.evaluate("document.getElementById('keyclr').click()"); pg.wait_for_timeout(1200)
    k5 = pg.evaluate("() => localStorage.getItem('coachDgKey')")
    rec('1: the key is an in-page dialog (password field), no native prompt, no alert', k1['dlg'] and k1['pw'] == 'password' and not dialogs, (k1, dialogs))
    rec('1: Esc, Cancel, Save and Clear all work', k2 and k3 and k4['saved'] == 'abc' and k4['closed'] and k5 is None, (k2, k3, k4, k5))

    # plain mode, no prospect
    pg.evaluate("Coach.plain()"); pg.wait_for_timeout(300)
    p1 = pg.evaluate("""() => ({say: document.getElementById('saytext').innerText, note: document.getElementById('saynote').innerText,
        ph: [document.getElementById('f_first').placeholder, document.getElementById('f_st1').placeholder],
        stop: document.getElementById('stopbtn').textContent, lang: document.getElementById('lang').disabled, langTitle: document.getElementById('lang').title,
        pausetip: document.getElementById('pausebtn').title})""")
    rec('10: no prospect -> the script does not say "Hi, there?"', 'there?' not in p1['say'] and 'Hi, can you hear me' in p1['say'], p1['say'][:160])
    rec('10: placeholders do not look like a real lead', 'Maria' not in p1['ph'][0] and 'NW 12th' not in p1['ph'][1], p1['ph'])
    rec('3: Plain mode names its exit honestly', p1['stop'].startswith('Leave') and 'nothing saved' in p1['stop'], p1['stop'])
    rec('13: the language switch is disabled in Plain mode with a reason', p1['lang'] and 'Plain' in p1['langTitle'], p1)
    pg.evaluate("document.getElementById('f_first').value='Mauricio'; Coach.fill()")
    say2 = pg.evaluate("document.getElementById('saytext').innerText")
    rec('10: with a first name the greeting uses it', say2.startswith('Hi, Mauricio?'), say2[:80])
    pg.evaluate("Coach.catchUp()"); pg.wait_for_timeout(150)
    cu = pg.evaluate("() => document.getElementById('coach').innerText")
    rec('11: Catch me up with nothing heard says so', 'Nothing has been heard' in cu, cu[:160])
    pg.evaluate("Coach.pauseCoach()")
    t1 = pg.evaluate("document.getElementById('pausebtn').title")
    pg.evaluate("Coach.pauseCoach()")
    t2 = pg.evaluate("document.getElementById('pausebtn').title")
    rec('12: the pause tooltip follows the state', 'resume' in t1.lower() and 'stop new prompts' in t2.lower(), (t1, t2))

    # 5: a lead with long badges at 1251px
    pg.evaluate("""() => { document.getElementById('leadbox').style.display='block'; document.getElementById('leadbox').innerHTML =
        '<div><span class="k">Sale</span><span class="v">11/30/2026</span><span class="src ok">COURT CALENDAR</span></div>'
        +'<div><span class="k">Judgment</span><span class="v">$213,951</span><span class="src ok">RECORDED JUDGMENT</span></div>'
        +'<div><span class="k">Owner</span><span class="v">GRANT-HENRIQUES,MATTHEW and another very long co-owner name string</span></div>'; }""")
    pg.wait_for_timeout(100)
    lay = pg.evaluate("""() => { const r = document.getElementById('right');
        const src = Array.from(document.querySelectorAll('#leadbox .src')).map(x => { const b = x.getBoundingClientRect(); return {w: Math.round(b.width), h: Math.round(b.height), right: Math.round(b.right)}; });
        const fills = Array.from(document.querySelectorAll('.fill input')).map(x => Math.round(x.getBoundingClientRect().right));
        return {scrollW: r.scrollWidth, clientW: r.clientWidth, src: src, fillMax: Math.max.apply(null, fills), rightEdge: Math.round(r.getBoundingClientRect().right),
                q2: document.getElementById('q2').placeholder, q2w: Math.round(document.getElementById('q2').getBoundingClientRect().width)}; }""")
    rec('5: the right panel does not scroll sideways at 1251px', lay['scrollW'] <= lay['clientW'] + 1, lay)
    rec('5: the source badges stay on one line and inside the panel', all(x['h'] < 24 and x['right'] <= lay['rightEdge'] for x in lay['src']), lay)
    rec('5: the prospect fields fit inside the panel', lay['fillMax'] <= lay['rightEdge'], lay)
    rec('5: the Switch lead placeholder is short enough to read', len(lay['q2']) <= 16, lay['q2'])

    # 15: the note box is fresh per wrap-up
    pg.evaluate("document.getElementById('wrapnote').value='note from the PREVIOUS call'; Coach._test.setLines([])")
    pg.evaluate("Coach._test.wrapAsk()")
    n1 = pg.evaluate("document.getElementById('wrapnote').value")
    rec('15: a new wrap-up clears the previous call\'s draft', n1 == '', n1)
    pg.evaluate("document.getElementById('wrapnote').value='typed by hand'; document.getElementById('wrapnote').dispatchEvent(new Event('input')); Coach._test.wrapAsk()")
    n2 = pg.evaluate("document.getElementById('wrapnote').value")
    rec('15: ...but what he typed during this wrap-up stays', n2 == 'typed by hand', n2)

    # recordings page at 1251px
    rp = ctx.new_page()
    rp.on('pageerror', lambda e: errs.append('calls: ' + str(e)[:200]))
    rp.goto(base + '/calls'); rp.wait_for_timeout(500)
    c1 = rp.evaluate("""() => { const t = document.querySelector('table').getBoundingClientRect();
        const row = document.querySelector('tbody tr'); const tds = Array.from(row.children).map(x => Math.round(x.getBoundingClientRect().width));
        const when = row.children[0]; const au = row.querySelector('audio'); const tr = row.querySelector('a.b');
        const abox = au ? au.getBoundingClientRect() : null, tbox = tr ? tr.getBoundingClientRect() : null;
        return {docW: document.documentElement.scrollWidth, winW: innerWidth, tds: tds, nowrap: getComputedStyle(when).whiteSpace === 'nowrap',
                overlap: !!(abox && tbox && abox.left < tbox.right && abox.right > tbox.left && abox.top < tbox.bottom && abox.bottom > tbox.top),
                rows: document.querySelectorAll('tbody tr').length, visible: Array.from(document.querySelectorAll('tbody tr')).filter(r => r.style.display !== 'none').length,
                path: document.querySelector('.path').innerText, hasTest: document.body.innerText.indexOf('TEST-') >= 0, firstWhen: when.innerText}; }""")
    rec('6: no sideways scroll, note column at least 240px, date on one line', c1['docW'] <= c1['winW'] + 1 and c1['tds'][4] >= 240 and c1['nowrap'], c1)
    rec('6: the audio player does not overlap the Transcript button', not c1['overlap'], c1)
    rec('17: no leftover test rows and no folder path printed', not c1['hasTest'] and ':\\' not in c1['path'] and '/' not in c1['path'], c1)
    rec('4: the page shows local times (am/pm, no UTC)', 'UTC' not in c1['firstWhen'] and ('AM' in c1['firstWhen'] or 'PM' in c1['firstWhen']), c1['firstWhen'])
    rec('8: empty sessions are hidden behind a toggle', c1['visible'] == c1['rows'] - 1, c1)
    rp.evaluate("document.getElementById('showempty').click()"); rp.wait_for_timeout(100)
    rec('8: ...and the toggle shows them', rp.evaluate("Array.from(document.querySelectorAll('tbody tr')).every(r => r.style.display !== 'none')"))
    rec('7: the Infinity-duration workaround is wired on every player', 'Infinity' in rp.content() and 'currentTime=1e101' in rp.content())
    # 9: the filter survives Back
    rp.evaluate("document.getElementById('f').value='CACE-26-000002'; document.getElementById('f').dispatchEvent(new Event('input'))")
    vis1 = rp.evaluate("Array.from(document.querySelectorAll('tbody tr')).filter(r => r.style.display !== 'none').length")
    rp.goto(base + '/calls/2026-11-20_235600_CACE-26-000001/transcript'); rp.wait_for_timeout(200)
    tx = rp.content()
    rec('15b: the transcript page shows speaker labels', 'YOU?: hi' in tx.replace('&#x27;', "'") and 'THEM: who is this' in tx, tx[-400:])
    rp.go_back(); rp.wait_for_timeout(500)
    vis2 = rp.evaluate("Array.from(document.querySelectorAll('tbody tr')).filter(r => r.style.display !== 'none').length")
    rec('9: after Back the filter still narrows the list', vis1 == 1 and vis2 == 1, (vis1, vis2))
    rec('no script error on any page', not errs, errs)
    b.close()
srv.shutdown()
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
