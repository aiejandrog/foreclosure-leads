#!/usr/bin/env python3
"""_e2ewiringtest.py: one lead's data, board -> Call Mode -> send bridge -> disk -> board, end to end.

Hops driven here (docs/agent-team/WIRING-MAP.md has the table):
  1. call_mode.call_rows / build_html on invented leads (one GUESS equity card, two verified).
  2. The built page's own JS (logOutcome, ayApply, ayBookedUnverified, cmpLog, saveNotes,
     callNotesPush, callTextLedger, bridgeFetch + device key) runs under node and records every fetch it
     makes. Nothing is simulated by hand: the /notes and /text bodies below are what the page produced.
  3. Those recorded requests are replayed against a REAL send_server.py started on a FREE port, in a
     temp copy of the code with a temp DEALFLOW_DIR, a fresh temp optouts.json, smtplib replaced by a
     stub that raises, and every non-loopback urlopen logged and refused. Port 8823 is never touched.
  4. worker_notes.json is read back from the temp dir, then the board's _funnelStats and _mergeLead run
     under node on it, and outcome_learn counts the same notes.

A hop that is broken today is NOT hidden: its check reads "KNOWN GAP (<decision>)" and passes only while
the gap is exactly as documented. When someone fixes the hop, that check FAILS with "gap closed: update
the map and the assertion", which is the signal to flip it to a normal check.

No network, no paid call, no real DEALFLOW_DIR. Invented 2099 cases and 555 numbers.
Run: python _e2ewiringtest.py
"""
import datetime as dt
import io
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
NODE = os.environ.get('NODE_BIN', os.environ.get('NODE', 'node'))
WORK = pathlib.Path(tempfile.mkdtemp(prefix='e2ewire_'))
SRVDIR = WORK / 'bridge'
SRVDIR.mkdir()
# nothing below may read or write the real DEALFLOW_DIR
os.environ['DEALFLOW_DIR'] = str(WORK / 'df')
(WORK / 'df').mkdir()
os.environ['DEALFLOW_BK_CACHE'] = str(WORK / 'no_bk_cache.json')
os.environ['DEALFLOW_BRIDGE_KEYS'] = str(SRVDIR / 'bridge_keys.json')

import bridge_auth as BA  # noqa: E402
import call_mode  # noqa: E402

bad = []
gaps = []


def rec(name, ok, extra=''):
    print(('  PASS ' if ok else '  FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        bad.append(name)


def gap(name, decision, still_broken, extra=''):
    """Pass only while the gap is exactly as documented; fail loudly when it closes."""
    gaps.append((name, decision))
    if still_broken:
        print('  PASS KNOWN GAP (%s) %s' % (decision, name))
    else:
        print('  FAIL gap closed, update the map and flip this to a normal check: %s (%s) | %s' % (name, decision, extra))
        bad.append('gap closed: ' + name)


T = '2026-10-31'
G, V, M = '2099-000101-CA-01', '2099-000102-CA-01', '2099-000103-CA-01'
GUESS = {'state': 'none', 'label': 'GUESS - never searched', 'fact': False, 'judgment': None,
         'judgment_source': 'not posted', 'liens': [], 'basis': 400000.0, 'basis_source': 'county',
         'net': None, 'may_quote': False, 'why': 'x'}
FACT = {'state': 'clear', 'label': 'VERIFIED CLEAR - chain traced', 'fact': True, 'judgment': 100000.0,
        'judgment_source': 'docket', 'liens': [], 'basis': 400000.0, 'basis_source': 'county',
        'net': 300000.0, 'may_quote': True, 'why': 'x'}


def lead(case, phone, eqc):
    return {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': '11/20/2026', 'days': 20, 'tier': 'B',
            'score': 50, 'value': 400000, 'phones': [phone], 'phtype': ['mobile'], 'phsrc': ['st'],
            'phdnc': [False], 'phrank': ['ok'], 'phbest': 0, 'eqc': eqc}


# ------------------------------------------------------------------ 1. hop: call_rows / build_html
print('-- 1. call_rows -> build_html')
rows, _tot = call_mode.call_rows([lead(G, '3055550101', GUESS), lead(V, '3055550102', FACT),
                                  lead(M, '3055550103', FACT)], None, None)
by = {r['c']: r for r in rows}
rec('all three invented leads ship as call rows', set(by) == {G, V, M}, sorted(by))
rec('GUESS lead ships eqc.f falsy, verified lead ships eqc.f truthy',
    not (by[G].get('eqc') or {}).get('f') and (by[V].get('eqc') or {}).get('f') == 1, [by[k].get('eqc') for k in (G, V)])
tpl = io.open(HERE / 'tracker_template.html', encoding='utf-8').read()
html = call_mode.build_html(rows, len(rows), {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 0, "shipped": 0}')
rec('build_html builds a page', len(html) > 50000)


def fn(name, end='\n}\n'):
    a = html.find('function %s(' % name)
    if a < 0:
        raise AssertionError('function %s not in the built page' % name)
    return html[a:html.index(end, a) + len(end)]


def one(name):
    a = html.index('function %s(' % name)
    return html[a:html.index('\n', a)]


def bridge_block():
    s, e = 'BRIDGE-KEY-START', 'BRIDGE-KEY-END'
    a = html.index(s)
    return html[html.rindex('/*', 0, a):html.index(e) + len(e)] + ' */'


# ------------------------------------------------------------------ 2. hop: the page's own JS
print('-- 2. the page JS under node (bridgeFetch + device key)')
BRIDGE_KEY = BA.add('e2e-phone')
(WORK / 'rows.json').write_text(json.dumps(rows), encoding='utf-8')
JS = r"""
var fs = require('fs');
var ROWS = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')), T = '%(T)s', KEY = '%(KEY)s';
var NOW = new Date(2026, 9, 31, 12).getTime();
Date.now = function(){ return NOW; };
var __store = {}, __calls = [], __toasts = [], __timers = [];
var localStorage = {getItem:function(k){ return Object.prototype.hasOwnProperty.call(__store,k)?__store[k]:null; },
                    setItem:function(k,v){ __store[k]=String(v); }, removeItem:function(k){ delete __store[k]; }};
var window = globalThis;
function toast(t){ __toasts.push(t); }
globalThis.fetch = function(url, opts){ __calls.push({url:url, opts:opts||{}});
  return Promise.resolve(new Response('{"ok":true}', {status:200, headers:{'Content-Type':'application/json'}})); };
var realST = setTimeout; setTimeout = function(f, ms){ __timers.push([f, ms]); return __timers.length; };
function clearTimeout(){}
""" % {'T': T, 'KEY': BRIDGE_KEY}
JS += bridge_block() + r"""
var notes = {}, LS = 'fcLeadNotes', _LSLAST = null, _SAVEFAIL = 0, _FCGEN = 0, _cnT = null, _cnBusy = false;
var _WORKED = [], touched = false;
function today(){ return T; } function nowTS(){ return T + ' 12:00:00'; } function caller(){ return 'carlos'; }
function noState(r){ return {hard:false, soft:false, open:false, why:''}; }
function stopEverywhere(){} function retireFromWorkerQ(){} function _ftsaCapToast(){ return false; }
function _wasLp(){ return false; } function queueSync(){} function logErr(){}
function _absorbStored(){ return 0; }
function lang(){ return 'en'; } function firstName(r){ return 'Ana'; }
""" + html[html.index('var AY_STAGES'):html.index('\n', html.index('var AY_STAGES'))] + '\n' \
    + fn('esc') + one('eqFact') + '\n' + one('_inbound') + '\n' \
    + html[html.index('var CMP_CHIPS'):html.index('];', html.index('var CMP_CHIPS')) + 2] + '\n' \
    + ''.join(fn(n) for n in ('ayIsStage', 'ayDateOk', 'ayTomorrow', 'ayApply', 'ayBookedUnverified', 'cmpDone',
                              'cmpLog', 'logOutcome', 'callTextLedger')) \
    + fn('saveNotes', '\n}}\n') + fn('callNotesPushSoon') + r"""
var out = {};
function row(c){ return ROWS.filter(function(x){ return x.c === c; })[0]; }
var rg = row('%(G)s'), rv = row('%(V)s'), rm = row('%(M)s');
__store.dfBridgeKey = KEY; __store.fcDevice = 'e2e-phone';
out.eqFact = {g: eqFact(rg), v: eqFact(rv)};
// Appointment set on the call screen (logOutcome 'appt'); the GUESS lead is booked on unverified equity
logOutcome(rg, {k:'appt', t:'APPOINTMENT SET', h:72}, '3055550101');
logOutcome(rv, {k:'appt', t:'APPOINTMENT SET', h:72}, '3055550102');
// After-the-yes panel
out.gOfferErr = ayApply(notes['%(G)s'], {stage:'Offer made', date:'2026-11-04', what:'send terms', eqOk:true, eqSource:'my gut', fact:eqFact(rg)}, T, NOW);
out.vApptErr = ayApply(notes['%(V)s'], {stage:'Appointment', date:'2026-11-04', what:'walk the house', eqOk:true, eqSource:'title search', keepOrSell:'sell', fact:eqFact(rv)}, T, NOW);
var probe = JSON.parse(JSON.stringify(notes['%(V)s']));
out.vOfferErrOnClone = ayApply(probe, {stage:'Offer made', date:'2026-11-06', what:'send terms', eqOk:true, eqSource:'title search', fact:eqFact(rv)}, T, NOW);
// Complete sheet: chips write plain manual touches
out.cmpV = cmpLog(rv, {text:1}, '', '');
out.cmpM = cmpLog(rm, {call:1, email:1, other:1}, '', 'left a note at the door');
out.cmpAgain = cmpLog(rm, {call:1}, '', '');          // already logged today: locked, written once
saveNotes();
out.scheduled = __timers.length ? __timers[__timers.length-1][1] : null;
callTextLedger(rv, '3055550102', false, 'ladder', 'cold', 'hola');
callTextLedger(rv, '3055550102', true, 'ladder', 'cold', 'hola');
(async function(){
  __timers[__timers.length-1][0]();
  await new Promise(function(r){ realST(r, 30); });
  out.calls = __calls.map(function(c){ return {url:c.url, method:(c.opts||{}).method, headers:(c.opts||{}).headers||{}, body:(c.opts||{}).body}; });
  out.notes = notes; out.toasts = __toasts;
  console.log(JSON.stringify(out)); process.exit(0);
})();
""" % {'G': G, 'V': V, 'M': M}
jsfile = WORK / 'page.js'
jsfile.write_text(JS, encoding='utf-8')
p = subprocess.run([NODE, str(jsfile), str(WORK / 'rows.json')], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
if p.returncode != 0:
    print('FAIL node page run:', p.stderr[-800:])
    sys.exit(1)
page = json.loads(p.stdout.strip().splitlines()[-1])
calls = page['calls']
rec('eqFact: GUESS false, verified true', page['eqFact'] == {'g': False, 'v': True}, page['eqFact'])
rec('GATE: Offer made refused on the GUESS lead (even with the equity box ticked)',
    'not verified' in page['gOfferErr'] and page['notes'][G]['status'] == 'Appointment', page['gOfferErr'])
rec('GATE: the same Offer made is accepted on the verified lead (control)', page['vOfferErrOnClone'] == '', page['vOfferErrOnClone'])
rec('Appointment save on the verified lead accepted', page['vApptErr'] == '', page['vApptErr'])
rec('GUESS booking is flagged unverified with next step "Verify equity"',
    page['notes'][G].get('apptUnverified') == T and page['notes'][G]['nextStep']['what'] == 'Verify equity'
    and page['notes'][G]['next'] == '2026-11-01', page['notes'][G])
rec('Complete sheet wrote 1 + 3 touches, and a second tap on a locked chip wrote none',
    page['cmpV'] == 1 and page['cmpM'] == 3 and page['cmpAgain'] == 0, (page['cmpV'], page['cmpM'], page['cmpAgain']))
rec('saveNotes schedules the debounced /notes push (4 s)', page['scheduled'] == 4000, page['scheduled'])
paths = [c['url'].replace('http://127.0.0.1:8823', '') for c in calls]
rec('page made exactly: /text (unconfirmed), /text (confirmed), /notes, each to 127.0.0.1:8823',
    paths == ['/text', '/text', '/notes'] and all(c['url'].startswith('http://127.0.0.1:8823/') for c in calls), paths)
rec('every call carries the device key from localStorage',
    all(c['headers'].get('X-Dealflow-Key') == BRIDGE_KEY for c in calls))
nb = json.loads(calls[2]['body'])
rec('/notes body: flag, device, notes only (no workerLog / sentArchive = no deletion signal)',
    nb.get('_dealflow_notes') == 1 and nb.get('device') == 'e2e-phone' and 'workerLog' not in nb
    and 'sentArchive' not in nb and set(nb['notes']) == {G, V, M}, sorted(nb))
tb = [json.loads(c['body']) for c in calls[:2]]
rec('/text bodies: case, pkey, to, confirmed false then true, device',
    tb[0]['case'] == V and tb[0]['to'] == '3055550102' and tb[0]['confirmed'] is False and tb[1]['confirmed'] is True
    and tb[1]['device'] == 'e2e-phone', tb)

# ------------------------------------------------------------------ 3. hop: the real send bridge
print('-- 3. send_server.py on a free port, temp dir, stub SMTP')
for f in ('send_server.py', 'bridge_auth.py', 'bounce_scan.py', 'stay_gate.py', 'mail_guard.py', 'deliverability.py',
          'sync_gate.py', 'text_hold.py', 'optout_sync.py', 'paths.py', 'footprint.py'):
    if (HERE / f).exists():
        shutil.copy(HERE / f, SRVDIR / f)
(SRVDIR / 'sync_status.json').write_text(json.dumps({
    'date': dt.date.today().isoformat(), 'state': 'finished', 'ok': True,
    'started_at': time.time() - 120, 'finished_at': time.time() - 60, 'steps': []}), encoding='utf-8')
(SRVDIR / 'optouts.json').write_text(json.dumps({'_dealflow_notes': True, 'notes': {
    '2099-000999-CA-01': {'optout': '2099-01-01'}, '@gone@invented.test': {'optout': '2099-01-01'}}}), encoding='utf-8')
(SRVDIR / 'bounced_emails.json').write_text('{}', encoding='utf-8')
(SRVDIR / 'sale_history_cache.json').write_text(json.dumps(
    {c: {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0} for c in (G, V, M)}), encoding='utf-8')
sk = socket.socket(); sk.bind(('127.0.0.1', 0)); PORT = sk.getsockname()[1]; sk.close()
rec('test port is not the real bridge port', PORT != 8823, PORT)
RUNNER = r'''
import sys, io, smtplib, urllib.request, urllib.error, urllib.parse
sys.argv = ["send_server.py", "--port", "%d", "--limit", "50"]
def _log(s):
    with open("egress.log", "a", encoding="utf-8") as f: f.write(s + "\n")
class _NoSMTP:
    def __init__(self, *a, **k): _log("SMTP " + repr(a)[:80]); raise OSError("smtp blocked by the test")
smtplib.SMTP = _NoSMTP; smtplib.SMTP_SSL = _NoSMTP
_orig = urllib.request.urlopen
def _fake(req, *a, **k):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if urllib.parse.urlparse(url).hostname in ("127.0.0.1", "localhost"): return _orig(req, *a, **k)
    _log("EGRESS " + url); raise urllib.error.URLError("blocked by the test")
urllib.request.urlopen = _fake
exec(open("send_server.py", encoding="utf-8").read())
''' % PORT
(SRVDIR / '_run.py').write_text(RUNNER, encoding='utf-8')
env = dict(os.environ, DEALFLOW_DIR=str(WORK / 'df'), DEALFLOW_BRIDGE_KEYS=str(SRVDIR / 'bridge_keys.json'),
           DEALFLOW_BRIDGE_RELOAD_POLL_S='0', DEALFLOW_BRIDGE_REQUIRE_KEY='')
proc = subprocess.Popen([sys.executable, '_run.py'], cwd=str(SRVDIR), env=env, stdout=subprocess.DEVNULL,
                        stderr=open(WORK / 'srv.err', 'w'))


def call(path, body=None, headers=None, method=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {'Content-Type': 'application/json'}
    h.update(headers or {})
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (PORT, path), data=data, headers=h,
                                 method=method or ('POST' if data is not None else 'GET'))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or '{}')
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {'err': str(e)}


def replay(c, extra=None, drop_key=False):
    """Send a request the page recorded, to the test port, with the headers the page set."""
    h = dict(c['headers'])
    if drop_key:
        h.pop('X-Dealflow-Key', None)
    h.update(extra or {})
    path = c['url'].replace('http://127.0.0.1:8823', '')
    return call(path, raw=c['body'].encode('utf-8'), headers=h)


try:
    up = False
    for _ in range(80):
        if call('/pid')[0] == 200:
            up = True
            break
        time.sleep(0.25)
    rec('bridge starts', up, (WORK / 'srv.err').read_text(errors='replace')[-400:] if not up else '')
    if not up:
        raise SystemExit(1)
    proxied = {'X-Forwarded-For': '100.64.1.2'}
    # --- device key
    st, j = replay(calls[2], extra=proxied, drop_key=True)
    rec('DEVICE KEY: a non-loopback (proxied) /notes with no key is 401 device_key',
        st == 401 and j.get('blocked') == 'device_key', (st, j))
    st, j = replay(calls[2], extra={'X-Dealflow-Key': 'not-the-key', **proxied}, drop_key=True)
    rec('DEVICE KEY: a wrong key is 401', st == 401, (st, j))
    rec('DEVICE KEY: the refused pushes wrote nothing', not (SRVDIR / 'worker_notes.json').exists())
    st, j = call('/wp/lookup', {'case': V}, headers=proxied)
    rec('DEVICE KEY: /wp/lookup with no key is 401 before any lookup (no paid call)', st == 401, (st, j))
    st, j = call('/health', headers=proxied)
    rec('DEVICE KEY: keyless remote /health is liveness only, texting held',
        st == 200 and j.get('ok') is True and 'sent_today' not in j and j.get('text_hold') is True, (st, j))
    # --- /notes from the page
    st, j = replay(calls[2])
    rec('/notes (page body, key, loopback) -> 200 saved', st == 200 and j.get('saved') is True and j.get('notes_count') == 3, (st, j))
    st, j = replay(calls[2], extra=proxied)
    rec('/notes with the key through a proxy header -> 200 (idempotent)', st == 200 and j.get('saved') is True, (st, j))
    wn = json.loads((SRVDIR / 'worker_notes.json').read_text(encoding='utf-8'))
    dn = wn['notes']
    rec('worker_notes.json holds all three leads', set(dn) == {G, V, M}, sorted(dn))
    rec('disk G: Appointment, apptUnverified, next step "Verify equity" tomorrow, no Offer made',
        dn[G]['status'] == 'Appointment' and dn[G].get('apptUnverified') == T
        and dn[G]['nextStep'] == {'date': '2026-11-01', 'what': 'Verify equity'} and 'Offer made' not in (dn[G].get('stageAt') or {}),
        dn[G])
    rec('disk V: Appointment, next-step date + text, equity verified with source, keepOrSell, stageAt',
        dn[V]['status'] == 'Appointment' and dn[V]['nextStep'] == {'date': '2026-11-04', 'what': 'walk the house'}
        and dn[V]['next'] == '2026-11-04' and dn[V]['equityVerified']['ok'] is True
        and dn[V]['equityVerified']['source'] == 'title search' and dn[V]['keepOrSell'] == 'sell'
        and dn[V]['stageAt'] == {'Appointment': T}, dn[V])
    mk = lambda c: sorted(t.get('mk') for t in dn[c]['touches'] if t.get('src') == 'manual')
    rec('disk V: the manual Complete touch (text) arrived next to the call touch',
        mk(V) == ['text'] and any(t.get('ch') == 'call' for t in dn[V]['touches']), dn[V]['touches'])
    rec('disk M: the three manual touches arrived (call, email, other), tagged src manual',
        mk(M) == ['call', 'email', 'other'] and all(t['src'] == 'manual' and t['by'] == 'carlos' for t in dn[M]['touches']),
        dn[M]['touches'])
    # --- later push: newer next step wins, an older push never walks it back
    later = json.loads(json.dumps(nb))
    later['notes'][V]['nextStep'] = {'date': '2026-11-09', 'what': 'meet at the house'}
    later['notes'][V]['next'] = '2026-11-09'
    later['notes'][V]['afterYesAt'] = later['notes'][V]['afterYesAt'] + 5000
    st, j = call('/notes', later, headers={'X-Dealflow-Key': BRIDGE_KEY})
    wn2 = json.loads((SRVDIR / 'worker_notes.json').read_text(encoding='utf-8'))['notes']
    rec('a newer after-the-yes save replaces the next step on disk (D1 merge)',
        st == 200 and wn2[V]['nextStep']['date'] == '2026-11-09' and wn2[V]['next'] == '2026-11-09', wn2[V].get('nextStep'))
    st, j = replay(calls[2])      # the original, now OLDER, body again
    wn3 = json.loads((SRVDIR / 'worker_notes.json').read_text(encoding='utf-8'))['notes']
    rec('an older push does not walk the next step back', wn3[V]['nextStep']['date'] == '2026-11-09', wn3[V].get('nextStep'))
    rec('the manual touches were not duplicated by the repeat pushes', mk(V) == ['text'] and mk(M) == ['call', 'email', 'other'],
        (mk(V), mk(M)))
    # --- /text/check then /text ledger
    st, j = call('/text/check', {'case': V, 'to': '3055550102', 'pcs': []}, headers={'X-Dealflow-Key': BRIDGE_KEY})
    rec('/text/check: clean case + clean number -> ok:true', j.get('ok') is True, (st, j))
    st, j = call('/text/check', {'case': '2099-000999-CA-01', 'to': '3055550104', 'pcs': []})
    rec('/text/check: a case on the fresh temp ledger is refused (optout)', j.get('ok') is False and j.get('blocked') == 'optout', j)
    st0, j0 = replay(calls[0])
    st1, j1 = replay(calls[1])
    rec('/text from the page (unconfirmed, then confirmed) -> ok', st0 == 200 and st1 == 200 and j0.get('ok') and j1.get('ok'), (j0, j1))
    ts = json.loads((SRVDIR / 'text_sent.json').read_text(encoding='utf-8')) if (SRVDIR / 'text_sent.json').exists() else []
    rows_ = ts if isinstance(ts, list) else ts.get('rows') or ts.get('sent') or list(ts.values())
    rec('text_sent.json has the Call Mode text, confirmed once', len([r for r in rows_ if isinstance(r, dict) and r.get('confirmed')]) == 1
        and any(isinstance(r, dict) and r.get('case') == V for r in rows_), rows_)
    # --- /mark, /marks, /marks/consumed (board worker's black box)
    st, j = call('/mark', {'c': G, 'k': 'called', 'extra': {'n': 1}}, headers={'X-Dealflow-Key': BRIDGE_KEY})
    st2, mj = call('/marks')
    rec('/mark appends; GET /marks returns it with an offset', st == 200 and any(m.get('c') == G for m in mj.get('marks', [])) and mj.get('offset', 0) > 0, mj)
    st3, cj = call('/marks/consumed', {'offset': mj.get('offset', 0)})
    st4, mj2 = call('/marks')
    rec('/marks/consumed archives the replayed prefix', cj.get('consumed') == mj.get('offset') and mj2.get('marks') == []
        and (SRVDIR / 'worker_marks.applied.jsonl').exists(), (cj, mj2))
    # --- /runlog: counts and codes only
    st, j = call('/runlog', {'ev': 'e2e_wiring', 'x': {'queue': 3, 'case': 'Z' * 200, 'nested': {'a': 1}}})
    wr = [json.loads(x) for x in (SRVDIR / 'worker_runs.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    rec('/runlog writes one line; strings cut to 80, nested values dropped',
        st == 200 and wr and wr[-1]['ev'] == 'e2e_wiring' and wr[-1]['x'].get('queue') == 3
        and len(wr[-1]['x'].get('case', '')) == 80 and 'nested' not in wr[-1]['x'], wr[-1:])
    import worker_runs as WRN
    rec('worker_runs.load reads the bridge file', any(r.get('ev') == 'e2e_wiring' for r in WRN.load(str(SRVDIR / 'worker_runs.jsonl'))))
    # --- fill-only fields on the disk merge (_merge_note): probe what a SECOND push can change
    P2 = '2099-000104-CA-01'
    hdr = {'X-Dealflow-Key': BRIDGE_KEY}
    first = {'_dealflow_notes': 1, 'device': 'e2e-phone', 'notes': {P2: {
        'status': 'Contacted', 'wpm': ['3055550201'], 'wpe': ['a@invented.test'], 'phone': '3055550201',
        'no': 'soft', 'noAt': 1, 'replied': T + ' 10:00:00', 'wq': '2026-10-30',
        'touches': [{'d': T, 'ts': T + ' 10:00:00', 'ch': 'call', 'out': 'Called - talked'}]}}}
    second = {'_dealflow_notes': 1, 'device': 'e2e-phone2', 'notes': {P2: {
        'status': 'Contacted', 'wpm': ['3055550201', '3055550202'], 'wpe': ['a@invented.test', 'b@invented.test'],
        'phone': '3055550202', 'no': 'hard', 'noAt': 2, 'replied': T + ' 15:00:00', 'wq': '2026-10-31',
        'touches': [{'d': T, 'ts': T + ' 10:00:00', 'ch': 'call', 'out': 'Called - talked'}]}}}
    call('/notes', first, headers=hdr)
    call('/notes', second, headers=hdr)
    p2 = json.loads((SRVDIR / 'worker_notes.json').read_text(encoding='utf-8'))['notes'][P2]
    gap('a second pasted number (wpm) on a lead already on disk does not reach the disk copy',
        'D17 (related note: wpm is not in _merge_note)', p2.get('wpm') == ['3055550201'], p2.get('wpm'))
    gap('a second pasted email (wpe) on a lead already on disk does not reach the disk copy',
        'D15 (callfix finding)', p2.get('wpe') == ['a@invented.test'], p2.get('wpe'))
    gap('a newer replied stamp does not replace the stored one on disk',
        'D15 (callfix finding)', p2.get('replied') == T + ' 10:00:00', p2.get('replied'))
    gap('a newer hard no does not replace a stored soft no on disk (status DNC still ledgers; the recorded reason is wrong)',
        'D15 (RULED 2026-10-30, fixer item queued)', p2.get('no') == 'soft', p2.get('no'))
    # --- D18: a hand-logged email/text (Complete sheet) is not in the send ledgers
    ml0 = SRVDIR / 'mail_sent.json'
    gap('Complete-sheet "Emailed" is a touch only: no mail_sent.json row, so cadence cannot see it',
        'D18', (not ml0.exists()) or M not in ml0.read_text(encoding='utf-8'), 'mail_sent has the row')
    # --- /health, /send
    st, j = call('/health', headers={'X-Dealflow-Key': BRIDGE_KEY})
    rec('/health with the key: the full answer', st == 200 and 'sent_today' in j, (st, j))
    st, j = call('/send', {'to': 'gone@invented.test', 'subj': 'x', 'body': 'x', 'meta': {'c': V}}, headers={'X-Dealflow-Key': BRIDGE_KEY})
    rec('/send to a ledgered address is refused and nothing is mailed', j.get('ok') is False, (st, j))
    ml = SRVDIR / 'mail_sent.json'
    rec('no mail_sent.json row was written by the test', (not ml.exists()) or not json.loads(ml.read_text(encoding='utf-8') or '[]'))
finally:
    proc.terminate()
    try:
        proc.wait(8)
    except Exception:
        proc.kill()
eg = SRVDIR / 'egress.log'
rec('nothing left the machine: no SMTP attempt, no non-loopback request',
    not eg.exists() or not eg.read_text(encoding='utf-8').strip(), eg.read_text(encoding='utf-8')[:300] if eg.exists() else '')

# ------------------------------------------------------------------ 4. hop: board + outcome_learn on the disk notes
print('-- 4. board _funnelStats / _mergeLead and outcome_learn on worker_notes.json')
disk = {k: v for k, v in json.loads((SRVDIR / 'worker_notes.json').read_text(encoding='utf-8'))['notes'].items()
        if k in (G, V, M)}      # P2 is the fill-only probe above, not one of the three funnel leads


def fnsrc(name):
    i = tpl.index('function %s(' % name)
    j = tpl.index('{', i)
    d = 0
    for k in range(j, len(tpl)):
        if tpl[k] == '{':
            d += 1
        elif tpl[k] == '}':
            d -= 1
            if d == 0:
                return tpl[i:k + 1]
    raise AssertionError(name)


def line(prefix):
    i = tpl.index(prefix)
    return tpl[i:tpl.index('\n', i)]


def region(a, b):
    i = tpl.index(a)
    return tpl[i:tpl.index(b, i)]


STAGE_BLOCK = '\n'.join([line("const esc = s =>"), fnsrc('_today'), fnsrc('_effOptout'), fnsrc('_isWrongOwner'),
                         region("const _AY_OPEN", "function _nowTS()")])
MERGE_BLOCK = '\n'.join([fnsrc('_today'), fnsrc('_nowTS'), fnsrc('_devSrc'),
                         region("const _DNC = s =>", "function mergeNotes(")])
(WORK / 'stage_block.js').write_text(STAGE_BLOCK, encoding='utf-8')
(WORK / 'merge_block.js').write_text(MERGE_BLOCK, encoding='utf-8')
(WORK / 'disk.json').write_text(json.dumps(disk), encoding='utf-8')
(WORK / 'stage_h.js').write_text(r'''
var fs = require('fs'); var notes = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
eval(fs.readFileSync(process.argv[3], 'utf8'));
console.log(JSON.stringify({stats: _funnelStats('%s'), overdue: _overdueFollowups(notes, '%s')}));
''' % (T, T), encoding='utf-8')
r = subprocess.run([NODE, str(WORK / 'stage_h.js'), str(WORK / 'disk.json'), str(WORK / 'stage_block.js')],
                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
rec('board _funnelStats runs on the disk notes', r.returncode == 0, r.stderr[-300:])
fs_ = json.loads(r.stdout)
s = fs_['stats']
rec('board: 2 appointments, 0 offers (the gate held), 2 open deals, 1 unverified booking, nothing overdue',
    (s['appointments'], s['offers'], s['openDeals'], s['apptUnverified'], s['overdue'], s['noNext']) == (2, 0, 2, 1, 0, 0), s)
rec('board: the next-step date on disk is the one the Overdue list would use (V 2026-11-09 is not overdue)',
    fs_['overdue'] == [], fs_['overdue'])
gap('Complete-sheet-only lead (manual touches, no status) is not an attempt on the board funnel',
    'by design (Complete comment); no D-number yet, reported to Lead',
    s['attempts'] == 2 and s['contacts'] == 2, s)
import outcome_learn as OL  # noqa: E402
ay = OL.after_yes_counts(disk, dt.date(2026, 10, 31))
rec('outcome_learn.after_yes_counts agrees with the board funnel',
    (ay.get('reached Appointment', 0), ay.get('reached Offer made', 0), ay.get('open deals', 0),
     ay.get('appointments booked on unverified equity', 0), ay.get('open deals with equity verified', 0))
    == (s['appointments'], s['offers'], s['openDeals'], s['apptUnverified'], 1), ay)
OL.HERE = str(SRVDIR)
OL.load_raw_leads = lambda: []
OL.load_board_rows = lambda: []
oc = OL.compute(today=dt.date(2026, 10, 31))
rec('outcome_learn.compute reads the bridge file: 2 dials, 2 leads dialed, 2 appointments',
    (oc['totals'].get('dials'), oc['totals'].get('leads dialed'), oc['after_yes'].get('reached Appointment')) == (2, 2, 2), oc['totals'])
gap('manual Complete touches never reach outcome_learn (dials only; M has 0 dials, 3 touches)',
    'by design (Complete comment); no D-number yet, reported to Lead',
    oc['totals'].get('leads dialed') == 2 and sum(1 for t in disk[M]['touches'] if t.get('src') == 'manual') == 3, oc['totals'])

# board _mergeLead: a board that already holds the lead receives the bridge's note
(WORK / 'merge_cases.json').write_text(json.dumps([
    [{'status': 'Called - talked', 'next': '2026-10-20', 'touches': [{'d': T, 'ts': T + ' 09:00:00', 'ch': 'call', 'out': 'board call'}]}, disk[V]],
    [{'status': 'Contacted', 'touches': [{'d': T, 'ts': T + ' 09:00:00', 'ch': 'call', 'out': 'board call'}]}, disk[G]],
    [{'status': '', 'note': ''}, disk[M]],
]), encoding='utf-8')
(WORK / 'merge_h.js').write_text(r'''
var fs = require('fs'); var cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
eval(fs.readFileSync(process.argv[3], 'utf8'));
console.log(JSON.stringify(cases.map(function(c){ return _mergeLead(c[0], c[1])[0]; })));
''', encoding='utf-8')
r = subprocess.run([NODE, str(WORK / 'merge_h.js'), str(WORK / 'merge_cases.json'), str(WORK / 'merge_block.js')],
                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
rec('board _mergeLead runs on the disk notes', r.returncode == 0, r.stderr[-300:])
mg = json.loads(r.stdout)
rec('board merge V: stage, next-step date/text, equity, keepOrSell all arrive',
    mg[0]['status'] == 'Appointment' and mg[0]['nextStep'] == disk[V]['nextStep'] and mg[0]['next'] == disk[V]['next']
    and mg[0]['equityVerified']['ok'] is True and mg[0]['keepOrSell'] == 'sell', mg[0])
rec('board merge V: the manual text touch arrives and the board\'s own call touch is kept',
    any(t.get('mk') == 'text' for t in mg[0]['touches']) and any(t.get('out') == 'board call' for t in mg[0]['touches']),
    mg[0].get('touches'))
rec('board merge G: stage Appointment and the "Verify equity" next step arrive',
    mg[1]['status'] == 'Appointment' and mg[1]['nextStep']['what'] == 'Verify equity', mg[1])
rec('board merge G: the unverified-booking flag arrives (D14 fix)', mg[1].get('apptUnverified') == T, mg[1])
rec('board merge M: all three manual touches arrive', sorted(t.get('mk') for t in mg[2].get('touches', [])) == ['call', 'email', 'other'], mg[2])

# ------------------------------------------------------------------ result
print()
print('KNOWN GAPS asserted as expected: %d' % len(gaps))
for g_, d_ in gaps:
    print('  - %s [%s]' % (g_, d_))
shutil.rmtree(WORK, ignore_errors=True)
print('ALL PASS' if not bad else 'FAILED: %d -> %s' % (len(bad), bad))
sys.exit(1 if bad else 0)
