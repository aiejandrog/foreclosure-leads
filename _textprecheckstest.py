#!/usr/bin/env python3
"""_textprecheckstest.py: every Text button asks the bridge BEFORE it opens an sms: composer, and
opens nothing unless the bridge says ok:true (2026-10-09, Alex chose "Check first").

  1. POST /text/check on a live bridge: clean case ok; ledgered case, the owner's other case (pcs),
     a ledgered number (raw, 11-digit) refused; stale ledger (text hold) refused; active §362 stay
     refused; junk bodies refused; it records nothing (no text_sent.json row).
  2. The three client helpers (board textPreflight, Call Mode textPreflight, worker textGate) run in
     node with a fake fetch: only an explicit ok:true goes ahead; a refusal, a non-JSON answer, a
     rejected fetch, ok:"yes" and a bridge that never answers (timeout) all open nothing.
  3. Every place that opens an sms: composer sits behind the helper.

Fake 2099 cases, fake 555 numbers, scratch port, no network, no sending.   python _textprecheckstest.py
"""
import datetime as dt
import json
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
bad = []


def rec(n, c, d=''):
    print(('  PASS ' if c else '  FAIL ') + n + ((' | ' + str(d)[:300]) if d and not c else ''))
    if not c:
        bad.append(n)


def call(port, path, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or '{}')
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {'err': str(e)}


# ---------------------------------------------------------------- 1. the bridge route
print('-- bridge /text/check')
sk = socket.socket(); sk.bind(('127.0.0.1', 0)); port = sk.getsockname()[1]; sk.close()
work = pathlib.Path(tempfile.mkdtemp(prefix='dfchk_'))
proc = None
try:
    for f in ('send_server.py', 'stay_gate.py', 'mail_guard.py', 'sync_gate.py', 'text_hold.py', 'optout_sync.py'):
        shutil.copy(HERE / f, work / f)
    (work / 'sync_status.json').write_text(json.dumps({
        'date': dt.date.today().isoformat(), 'state': 'finished', 'ok': True,
        'started_at': time.time() - 120, 'finished_at': time.time() - 60, 'steps': []}), encoding='utf-8')
    (work / 'optouts.json').write_text(json.dumps({'_dealflow_notes': True, 'notes': {
        '2099-000001-CA-01': {'optout': '2099-01-01'}, '#3055550199': {'optout': '2099-01-01'}}}), encoding='utf-8')
    cache = {'2099-%06d-CA-01' % i: {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0} for i in range(1, 8)}
    cache['2099-000007-CA-01'] = {'a': True, 'bd': '2099-01-01', 'sl': '', 'b': 1, 'v': 5, 't': 0}
    (work / 'sale_history_cache.json').write_text(json.dumps(cache), encoding='utf-8')
    (work / 'bounced_emails.json').write_text('{}', encoding='utf-8')
    (work / '_run.py').write_text('import sys\nsys.argv=["send_server.py","--port","%d","--limit","50"]\n'
                                  'exec(open("send_server.py", encoding="utf-8").read())\n' % port, encoding='utf-8')
    proc = subprocess.Popen([sys.executable, '_run.py'], cwd=str(work), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    up = any(call(port, '/health')[0] == 200 or time.sleep(0.25) for _ in range(60))
    rec('bridge starts', up)
    chk = lambda **b: call(port, '/text/check', b)[1]
    j = chk(case='2099-000002-CA-01', to='3055550102')
    rec('clean case + clean number: ok', j.get('ok') is True, j)
    j = chk(case='2099-000001-CA-01', to='3055550102')
    rec('ledgered case refused', j.get('ok') is False and j.get('blocked') == 'optout', j)
    j = chk(case='2099-000003-CA-01', to='3055550103', pcs=['2099-000001-ca-01'])
    rec("owner's other case on the ledger (pcs) refused", j.get('ok') is False and j.get('blocked') == 'optout', j)
    j = chk(case='2099-000004-CA-01', to='(305) 555-0199')
    rec('ledgered number refused (formatted)', j.get('ok') is False and j.get('blocked') == 'optout', j)
    j = chk(case='2099-000004-CA-01', to='+13055550199')
    rec('ledgered number refused (11-digit)', j.get('ok') is False and j.get('blocked') == 'optout', j)
    j = chk(case='2099-000005-CA-01', to='3055550105', pcs=['2099-000009-CA-01', 7, None], portfolio='x')
    rec('junk sibling entries change nothing', j.get('ok') is True, j)
    j = chk(case='2099-000007-CA-01', to='3055550107')
    rec('active 362 stay refused', j.get('ok') is False and j.get('blocked') not in (None, 'optout'), j)
    j = chk(case='', to='3055550108')
    rec('no case refused', j.get('ok') is False, j)
    rec('non-JSON body refused', call(port, '/text/check', raw=b'not json')[1].get('ok') is False)
    rec('JSON list body refused', call(port, '/text/check', raw=b'[1,2]')[1].get('ok') is False)
    rec('it records nothing (no text_sent.json)', not (work / 'text_sent.json').exists())
    rec('/text still works and is not shadowed by the new route',
        call(port, '/text', {'case': '2099-000002-CA-01', 'to': '3055550102', 'confirmed': False})[1].get('ok') is True)
    j = chk(case='2099-000002-CA-01', to='')
    rec('no number: refused', j.get('ok') is False, j)
    j = chk(case='2099-000002-CA-01', to='555')
    rec('garbled short number: refused', j.get('ok') is False, j)
    (work / 'worker_notes.json').write_text(json.dumps({'notes': {
        '2099-000006-CA-01': {'status': 'Do Not Contact', 'dntph': ['3055550177']}}}), encoding='utf-8')
    j = chk(case='2099-000002-CA-01', to='3055550177')
    rec('rep-logged DNC number (not yet ledgered) refused', j.get('ok') is False and j.get('blocked') == 'optout', j)
    j = chk(case='2099-000006-CA-01', to='3055550106')
    rec('rep-logged DNC case (not yet ledgered) refused', j.get('ok') is False and j.get('blocked') == 'optout', j)
    # stale ledger -> text hold -> refused
    old = time.time() - 40 * 86400
    import os
    os.utime(work / 'optouts.json', (old, old))
    j = chk(case='2099-000002-CA-01', to='3055550102')
    rec('stale do-not-contact list (text hold) refused', j.get('ok') is False and j.get('blocked') == 'text_hold', j)
finally:
    if proc:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
    shutil.rmtree(work, ignore_errors=True)

# ---------------------------------------------------------------- 2. the client helpers
print('-- client helpers')
BOARD = (HERE / 'tracker_template.html').read_text(encoding='utf-8')
CM = (HERE / 'call_mode.py').read_text(encoding='utf-8')


def jsfn(src, name):
    m = re.search(r'\nfunction ' + name + r'\(.*?\n}\n', src, re.S)
    assert m, name
    return m.group(0)


def worker_gate():
    i = BOARD.index("    +   'function textGate(")
    j = BOARD.index("    +   'function openHere(")
    out = []
    for ln in BOARD[i:j].splitlines():
        ln = ln.strip()
        if not ln.startswith('+'):
            continue
        m = re.match(r"\+\s*'(.*)'\s*$", ln)
        if m:
            out.append(m.group(1).replace('\\\\', '\\'))
    return '\n'.join(out)


SCEN = {
    'ok': 'fetch=function(){return Promise.resolve({json:function(){return Promise.resolve({ok:true});}});};',
    'refused': 'fetch=function(){return Promise.resolve({json:function(){return Promise.resolve({ok:false,err:"on the do-not-contact ledger"});}});};',
    'okstring': 'fetch=function(){return Promise.resolve({json:function(){return Promise.resolve({ok:"yes"});}});};',
    'nojson': 'fetch=function(){return Promise.resolve({json:function(){return Promise.reject(new Error("x"));}});};',
    'reject': 'fetch=function(){return Promise.reject(new Error("down"));};',
    'throws': 'fetch=function(){throw new Error("boom");};',
    'hang': 'fetch=function(){return new Promise(function(){});};',
}
EXPECT = {'ok': True}


def run_node(js):
    r = subprocess.run(['node', '-e', js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


for label, fn, call_expr, kind in (
        ('board', jsfn(BOARD, 'textPreflight'), 'textPreflight({case:"2099-000002-CA-01"}, "3055550102")', 'promise'),
        ('Call Mode', jsfn(CM, 'textPreflight'), 'textPreflight("2099-000002-CA-01", "3055550102", [])', 'promise')):
    for name, scen in SCEN.items():
        js = (scen + fn + 'var t0=Date.now(); ' + call_expr + '.then(function(v){console.log(JSON.stringify({ok:v.ok, why:v.why, ms:Date.now()-t0}));});')
        v = run_node(js)
        want = EXPECT.get(name, False)
        rec('%s helper, %s -> %s' % (label, name, 'go' if want else 'opens nothing'), v['ok'] is want, v)
        if name == 'hang':
            rec('%s helper gives up on a silent bridge within ~4 s' % label, 3000 <= v['ms'] <= 5500, v)

g = worker_gate()
rec('worker textGate extracted', 'function textGate' in g and 'text/check' in g, g[:200])
for name, scen in SCEN.items():
    js = ('var TGBUSY=false; var logs=[]; function addLog(a,b,c,d){logs.push(c);} ' + scen + g
          + 'var went=0; var t0=Date.now(); textGate("2099-000002-CA-01","sms:+13055550102?body=hi",[],"Jane",function(){went++;});'
            'setTimeout(function(){console.log(JSON.stringify({went:went, logs:logs, ms:Date.now()-t0}));}, 4200);')
    v = run_node(js)
    want = 1 if name == 'ok' else 0
    rec('worker textGate, %s -> %s' % (name, 'go' if want else 'opens nothing'), v['went'] == want, v)
    if want == 0:
        rec('worker textGate, %s -> says why in the log' % name, any('Not texted' in x for x in v['logs']), v)

# ---------------------------------------------------------------- 3. every composer sits behind it
print('-- call sites')
# board: the modal link
m = re.search(r"const sd = e\.target\.closest && \(e\.target\.closest\('\.txsend'\) \|\| e\.target\.closest\('\.txwa'\)\);(.*?)const mk = ", BOARD, re.S)
blk = m.group(1) if m else ''
rec('board Text link: preventDefault, then textPreflight, then navigate',
    'e.preventDefault()' in blk and blk.index('textPreflight(') < blk.index('location.href = href'), blk[:200])
rec('board Text link: preventDefault runs BEFORE any early return', blk.index('e.preventDefault()') < blk.index('if(!r) return;'))
rec('board WhatsApp link goes through the same pre-flight', "class=\"txwa\" href=\"#\"" in BOARD and "isWa" in blk and "window.open(href" in blk)
rec('board: no composer href left on a navigable anchor (sms: / wa.me are in data-href)',
    'class="txsend" href="#" data-href=' in BOARD and not re.search(r'class="txwa" href="\'\+esc', BOARD))
rec('board: the generic stamp skips the WhatsApp link too', "classList.contains('txwa')" in BOARD)
rec('board: a stale callback (modal closed / other lead) opens nothing', "caseAtTap" in blk and "classList.contains('show')" in blk)
WG = BOARD[BOARD.index('function textGate'):BOARD.index('function openHere')]
rec('worker: only one pre-flight in flight; moving to another lead drops the stale one',
    'if(TGBUSY) return;' in WG and 'g0!==(typeof i' in WG)
rec('board Text link: touch + ledger post only after the check', blk.index('textPreflight(') < blk.index('autoLog(') < blk.index('_textLedgerPost('))
rec('board: the generic sms: click stamp skips the modal link', "classList.contains('txsend') || a.classList.contains('txwa')) return;" in BOARD)
# worker: both openHere(sms) uses are inside textGate callbacks
sites = [m.start() for m in re.finditer(r'openHere\((?:r\.smsHref|x\.sms)\)', BOARD)]
rec('worker: three places open an sms: href (row, batch, queue tap)', len(sites) == 3, len(sites))
for n, i in enumerate(sites):
    pre = BOARD[max(0, i - 400):i]
    rec('worker sms: open #%d sits directly inside a textGate callback' % (n + 1),
        pre.rfind('textGate(') > pre.rfind('});') and 'function(){' in pre[pre.rfind('textGate('):], pre[-200:])
# call mode
j = CM.index("if($('tx')) $('tx').onclick")
rec('Call Mode composer: preflight before anything is logged or opened',
    CM.index('textPreflight(', j) < CM.index('n.textopen = today()', j) < CM.index('openComposer();', j))
k = CM.index("$('txr').onclick")
rec('Call Mode re-open: preflight first', CM.index('textPreflight(', k) < CM.index('openComposer();', k))
rec('Call Mode lookup Text link is gated', 'class="lktx"' in CM and "querySelectorAll('.lktx')" in CM
    and CM.index('textPreflight(a.dataset.c', CM.index("querySelectorAll('.lktx')")) < CM.index('location.href = href', CM.index("querySelectorAll('.lktx')")))
rec('Call Mode: no bare sms: anchor left', len(re.findall(r'<a href="sms:', CM)) == 1)  # the gated one
rec('Call Mode: the only location.href to sms: is inside openComposer / the gated link',
    len(re.findall(r"location\.href = 'sms:'", CM)) == 1)

print('\n%d failed' % len(bad))
sys.exit(1 if bad else 0)
