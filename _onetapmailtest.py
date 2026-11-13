"""One-tap "Send email" in Call Mode: the address on file, the Morning Worker's template, no compose.

2026-11-13 (Alejandro). He works in Call Mode and wants to email a lead the way the Morning Worker
would. The phone page carries only a COUNT of a lead's addresses, so the page names the case
(meta.onfile, empty `to`) and the bridge resolves the addresses from skiptrace_results.json. This
file pins:

  1. _onfile_emails(): case-keyed, well-formed only, de-duplicated, an address mailed before first.
  2. The REAL bridge on a scratch port with SMTP stubbed: an on-file send reaches SMTP at the address
     on file and writes a ledger row; a case with no address answers no_onfile_email and sends
     nothing; an opted-out address and a stayed case are still refused on this path; a test send
     never takes it.
  3. The page: the button, its holds, the payload (no address leaves the page), the masked toast,
     the Coach link on the queue cards, and the built page still parses.

No network, no real SMTP, synthetic data only (example.com).
"""
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import _sendstaytest as H  # noqa: E402  (harness only: free_port, call, write_sync_ok; guarded main)
import send_server as S  # noqa: E402
import call_mode  # noqa: E402

ok, bad = [], []
NODE = os.environ.get('NODE_BIN', 'node')


def rec(name, cond, detail=''):
    (ok if cond else bad).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)[:400]) if detail and not cond else ''))


ST = {
    '2099-000002-CA-01': {'emails': ['First@Example.com', 'second@example.com', 'first@example.com', 'not-an-address']},
    '2099-000003-CA-01': {'emails': ['gone@example.com']},
    '2099-000001-CA-01': {'emails': ['stayed@example.com']},
    '2099-000007-CA-01': {'emails': []},
}

# ---- 1. the resolver -----------------------------------------------------------------------------
print('-- _onfile_emails()')
tmp = pathlib.Path(tempfile.mkdtemp(prefix='onetap_'))
_here, _led, _wd = S.HERE, S._load_ledger, S._worker_mailable_domains
try:
    S.HERE = str(tmp)
    S._worker_mailable_domains = lambda: None
    S._load_ledger = lambda: []
    (tmp / 'skiptrace_results.json').write_text(json.dumps(ST), encoding='utf-8')
    rec('addresses come back lowercased, de-duplicated, malformed ones dropped',
        S._onfile_emails('2099-000002-CA-01') == ['first@example.com', 'second@example.com'],
        S._onfile_emails('2099-000002-CA-01'))
    rec('a case with no addresses, an unknown case and an empty case all give nothing',
        S._onfile_emails('2099-000007-CA-01') == [] and S._onfile_emails('2099-999999-CA-01') == []
        and S._onfile_emails('') == [] and S._onfile_emails(None) == [])
    S._load_ledger = lambda: [{'ch': 'email', 'message_id': '<1>', 'to': 'second@example.com'}]
    rec('an address this system already mailed comes first, so a follow-up stays on it',
        S._onfile_emails('2099-000002-CA-01') == ['second@example.com', 'first@example.com'])
    S._worker_mailable_domains = lambda: frozenset(['gmail.com'])
    rec('the Morning Worker domain list filters the pool when it can be read',
        S._onfile_emails('2099-000002-CA-01') == [])
    (tmp / 'skiptrace_results.json').write_text('{ not json', encoding='utf-8')
    rec('an unreadable skip trace gives nothing (the send is then refused, not guessed)',
        S._onfile_emails('2099-000002-CA-01') == [])
finally:
    S.HERE, S._load_ledger, S._worker_mailable_domains = _here, _led, _wd
    shutil.rmtree(tmp, ignore_errors=True)

# ---- 2. the real bridge --------------------------------------------------------------------------
print('-- live bridge')
port = H.free_port()
work = pathlib.Path(tempfile.mkdtemp(prefix='onetapsrv_'))
proc = None
try:
    for f in ('send_server.py', 'bridge_auth.py', 'stay_gate.py', 'mail_guard.py', 'deliverability.py',
              'sync_gate.py', 'footprint.py'):
        shutil.copy(HERE / f, work / f)
    H.write_sync_ok(work)
    (work / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
    (work / 'sender.json').write_text(json.dumps({'name': 'Test Sender'}), encoding='utf-8')
    (work / 'senders.json').write_text(json.dumps({
        'main_domain': 'example.com', 'main_domain_cap': 40,
        'ramp_start': '2020-01-01', 'ramp': [{'through_day': 9999, 'per_day': 100}],
        'lanes': {'default': 'tester@example.com', 'active': 'tester@example.com'},
        'first_touch': {'from': ['warm@wu.example'], 'per_day': 100},
    }), encoding='utf-8')
    (work / 'bounced_emails.json').write_text('{}', encoding='utf-8')
    (work / 'optouts.json').write_text(json.dumps(
        {'_dealflow_notes': True, 'notes': {'@gone@example.com': {'status': 'stop'}}}), encoding='utf-8')
    (work / 'sale_history_cache.json').write_text(json.dumps(H.CACHE), encoding='utf-8')
    (work / 'skiptrace_results.json').write_text(json.dumps(ST), encoding='utf-8')
    shim = work / '_run_bridge.py'
    shim.write_text(
        'import sys, smtplib\n'
        'class _FakeSMTP:\n'
        '    def __init__(self, *a, **k): pass\n'
        '    def __enter__(self): return self\n'
        '    def __exit__(self, *a): return False\n'
        '    def login(self, u, p): pass\n'
        '    def send_message(self, m, **k):\n'
        '        open("smtp_calls.txt", "a", encoding="utf-8").write(str(m["To"]) + "\\n")\n'
        '        return {}\n'
        'smtplib.SMTP_SSL = _FakeSMTP\n'
        'sys.argv = ["send_server.py", "--port", "%d", "--limit", "50"]\n'
        'exec(open("send_server.py", encoding="utf-8").read())\n' % port, encoding='utf-8')
    proc = subprocess.Popen([sys.executable, str(shim)], cwd=str(work),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    up = False
    for _ in range(60):
        if H.call(port, '/health')[0] == 200:
            up = True
            break
        time.sleep(0.25)
    rec('bridge starts', up)

    def smtp_calls():
        p = work / 'smtp_calls.txt'
        return p.read_text(encoding='utf-8').split() if p.exists() else []

    def ledger():
        p = work / 'mail_sent.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else []

    def onfile(case, **extra):
        meta = {'c': case, 'owner': 'Jane', 'addr': '1 Main St', 'wl': 'active', 'onfile': 1}
        meta.update(extra)
        return H.call(port, '/send', {'to': '', 'subj': 'About 1 Main St',
                                      'body': 'Hello Jane, a short note about 1 Main St.', 'meta': meta})

    if up:
        st, j = onfile('2099-000007-CA-01')
        rec('no address on file: refused as no_onfile_email, skip, nothing sent',
            j.get('ok') is False and j.get('blocked') == 'no_onfile_email' and j.get('skip') is True
            and not smtp_calls() and not ledger(), {'st': st, 'j': j})

        st, j = onfile('2099-000002-CA-01')
        rec('address on file: the send goes out and answers with the address used',
            st == 200 and j.get('ok') is True and j.get('to') == 'first@example.com', {'st': st, 'j': j})
        rec('...it reached SMTP at that address and wrote one ledger row for the case',
            smtp_calls() == ['first@example.com'] and len(ledger()) == 1
            and ledger()[0].get('case') == '2099-000002-CA-01' and ledger()[0].get('to') == 'first@example.com',
            {'smtp': smtp_calls(), 'ledger': ledger()})

        before = len(smtp_calls())
        st, j = onfile('2099-000003-CA-01')
        rec('an opted-out address on file is still refused on this path',
            j.get('ok') is not True and len(smtp_calls()) == before, {'st': st, 'j': j})
        st, j = onfile('2099-000001-CA-01')
        rec('a case under a bankruptcy stay is still refused on this path',
            st == 451 and j.get('blocked') == 'stay_active' and len(smtp_calls()) == before, {'st': st, 'j': j})
        st, j = onfile('2099-000002-CA-01', test=True)
        rec('a test send never resolves an on-file address (empty `to` stays invalid)',
            st == 400 and len(smtp_calls()) == before, {'st': st, 'j': j})
        st, j = H.call(port, '/send', {'to': '', 'subj': 's', 'body': 'b',
                                       'meta': {'c': '2099-000002-CA-01', 'owner': 'Jane', 'addr': '1 Main St'}})
        rec('an empty `to` without meta.onfile is still a 400', st == 400 and len(smtp_calls()) == before, {'st': st, 'j': j})
finally:
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    shutil.rmtree(work, ignore_errors=True)

# ---- 3. the page ---------------------------------------------------------------------------------
print('-- call mode page')
tpl = io.open(HERE / 'tracker_template.html', encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 0, "shipped": 0}')
T = tempfile.mkdtemp(prefix='onetapjs_')
big = max(re.findall(r'<script[^>]*>(.*?)</script>', html, re.S), key=len)
f = os.path.join(T, 'page.js')
io.open(f, 'w', encoding='utf-8').write(big)
p = subprocess.run([NODE, '--check', f], capture_output=True, text=True, timeout=60)
rec('the built page passes node --check', p.returncode == 0, p.stderr[:400])

a = big.index('function mailOnFileWhy(r){')
b = big.index('function screenMail(r, raw, back){')
block = big[a:b]
block = block[:block.rindex('/*')]
rec('the on-file payload carries no address: empty `to`, meta.onfile, the worker lane',
    "if(M.onfile){ payload.to = ''; payload.meta.onfile = 1; payload.meta.wl = M.early ? 'early' : 'active'; }" in big)
rec('the one-tap send goes through the bridge and never opens a mail client',
    'mailViaBridge(r, M)' in block and 'mailto:' not in block and 'window.open' not in block)
rec('the button is on the dial card and on the Find-a-number card',
    '+ mailOnFileBtn(r)\n    + _wpeList(r)' in big and "+ mailOnFileBtn(lkAsDial(r, ''))" in big
    and ".pastecall .mlone" in big and ".lkcard button.mlone" in big)
rec('a pasted Whitepages address still has its own path', 'function screenMail(' in big and 'class="pcem"' in big)
rec('the queue cards keep the Coach link', big.count('/Whitepages|People search|Coach/') >= 1,
    big.count('/Whitepages|People search|Coach/'))

runner = r'''
var MAIL_OK = true, _held = false, _stage = 'cold', _hard = '';
function hardSuppressed(r){ return _hard; }
function textingHeld(){ return _held; }
function textHoldWhy(){ return 'list is stale'; }
function mailStageCM(r){ return _stage; }
function _stageWord(s){ return s; }
function esc(s){ return String(s); }
''' + block + r'''
var out = {};
var r = {c:'2099-000002-CA-01', ne:2, st:''};
out.btn = mailOnFileBtn(r);
out.none = mailOnFileBtn({c:'x', ne:0, st:''});
out.bal = mailOnFileBtn({c:'x', ne:1, st:'BAL'});
_hard = 'on the do-not-contact list'; out.hard = mailOnFileBtn(r); _hard = '';
_held = true; out.held = mailOnFileBtn(r); _held = false;
_stage = 'done'; out.done = mailOnFileBtn(r);
_stage = 'replied'; out.replied = mailOnFileBtn(r); _stage = 'cold';
MAIL_OK = false; out.nomail = mailOnFileBtn(r); MAIL_OK = true;
out.mask = _maskAddr('first@example.com'); out.maskEmpty = _maskAddr('');
console.log(JSON.stringify(out));
'''
rf = os.path.join(T, 'run.js')
io.open(rf, 'w', encoding='utf-8').write(runner)
q = subprocess.run([NODE, rf], capture_output=True, text=True, timeout=60, encoding='utf-8')
rec('node ran the button logic', q.returncode == 0, q.stderr[-400:])
if q.returncode == 0:
    o = json.loads(q.stdout)
    rec('a lead with an email on file gets a Send email button naming the stage',
        'class="mlone"' in o['btn'] and 'Send email' in o['btn'] and 'cold' in o['btn'], o['btn'])
    rec('no email on file, the investor lane, or no templates: no button at all',
        o['none'] == '' and o['bal'] == '' and o['nomail'] == '')
    rec('a do-not-contact lead shows the reason instead of a button',
        'mlone' not in o['hard'] and 'do-not-contact' in o['hard'], o['hard'])
    rec('a stale do-not-contact list holds the button', 'mlone' not in o['held'] and 'not confirmed fresh' in o['held'], o['held'])
    rec('three emails sent or a reply already in: no fourth template',
        'mlone' not in o['done'] and 'mlone' not in o['replied'], [o['done'], o['replied']])
    rec('the toast masks the address', o['mask'].startswith('f') and o['mask'].endswith('@example.com')
        and 'first' not in o['mask'] and o['maskEmpty'] == 'the address on file', o['mask'])
shutil.rmtree(T, ignore_errors=True)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
