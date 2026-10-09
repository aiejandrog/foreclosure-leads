#!/usr/bin/env python3
"""_texttripwiretest.py: /text leaves a durable record when a confirmed text is logged for a case or
number on the do-not-contact ledger, and still records the row (the 3-touch cap counts it).

Live bridge on a scratch port, fake 2099 cases, fake 555 numbers. No network, no sending.
Run: python _texttripwiretest.py
"""
import datetime as dt
import json
import os
import pathlib
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


def call(port, path, body=None):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or '{}')
    except Exception as e:
        return 0, {'err': str(e)}


sk = socket.socket(); sk.bind(('127.0.0.1', 0)); port = sk.getsockname()[1]; sk.close()
work = pathlib.Path(tempfile.mkdtemp(prefix='dftxt_'))
proc = None
try:
    for f in ('send_server.py', 'stay_gate.py', 'mail_guard.py', 'sync_gate.py', 'text_hold.py'):
        shutil.copy(HERE / f, work / f)
    (work / 'sync_status.json').write_text(json.dumps({
        'date': dt.date.today().isoformat(), 'state': 'finished', 'ok': True,
        'started_at': time.time() - 120, 'finished_at': time.time() - 60, 'steps': []}), encoding='utf-8')
    (work / 'optouts.json').write_text(json.dumps({'_dealflow_notes': True, 'notes': {
        '2099-000001-CA-01': {'optout': '2099-01-01'}, '#3055550199': {'optout': '2099-01-01'}}}), encoding='utf-8')
    (work / 'sale_history_cache.json').write_text(json.dumps({
        '2099-%06d-CA-01' % i: {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0} for i in range(1, 6)}),
        encoding='utf-8')
    for f in ('bounced_emails.json',):
        (work / f).write_text('{}', encoding='utf-8')
    (work / '_run.py').write_text('import sys\nsys.argv=["send_server.py","--port","%d","--limit","50"]\n'
                                  'exec(open("send_server.py", encoding="utf-8").read())\n' % port, encoding='utf-8')
    proc = subprocess.Popen([sys.executable, '_run.py'], cwd=str(work), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    up = any(call(port, '/health')[0] == 200 or time.sleep(0.25) for _ in range(60))
    rec('bridge starts', up)
    log = work / 'send_refusals.jsonl'
    rows = lambda: [json.loads(x) for x in log.read_text(encoding='utf-8').splitlines()] if log.exists() else []

    def text(case, to, confirmed=True):
        return call(port, '/text', {'case': case, 'to': to, 'confirmed': confirmed, 'owner': 'Jane'})

    st, j = text('2099-000002-CA-01', '3055550102')
    rec('control: a clean confirmed text is recorded and leaves no tripwire row', j.get('ok') is True and not rows(), (st, j, rows()))
    st, j = text('2099-000001-CA-01', '3055550103')
    rec('ledgered CASE: text row is still recorded', j.get('ok') is True, (st, j))
    rec('ledgered CASE: tripwire row written', any(r.get('code') == 'text_sent_to_optout' for r in rows()), rows())
    n0 = len(rows())
    st, j = text('2099-000003-CA-01', '(305) 555-0199')
    rec('ledgered NUMBER (other case): still recorded', j.get('ok') is True, (st, j))
    rec('ledgered NUMBER: tripwire row written', len(rows()) == n0 + 1, rows())
    n1 = len(rows())
    text('2099-000001-CA-01', '3055550104', confirmed=False)
    rec('an unconfirmed composer open is not contact and writes no tripwire row', len(rows()) == n1, rows())
    rec('no phone number is stored in the tripwire rows', all('555' not in json.dumps(r) for r in rows()), rows())
    sent = json.loads((work / 'text_sent.json').read_text(encoding='utf-8'))
    rec('all confirmed rows reached text_sent.json', len([x for x in sent if x['confirmed']]) == 3, sent)
finally:
    if proc:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
    shutil.rmtree(work, ignore_errors=True)
print('\n%d failed' % len(bad))
sys.exit(1 if bad else 0)
