#!/usr/bin/env python3
"""_suppressionfixtest — fixture coverage for the 2026-09-26 suppression decisions.

No network, no paid API, no real people. Synthetic cases 2099-..., 555-01 numbers, example.com.

  1. notes DNC is written through ledger_add; a non-DNC note is not
  2. inbound SMS STOP (EN and ES) is ledgered as #digits; "can you stop the sale" is not
  3. quoting our opt-out sentence is not a stop and is not ledgered; a real stop above the quote is
  4. "not a good time, try next month" is a permanent DO NOT CONTACT
  5. cadence's send-time re-check holds a stayed case and still sends a clean eligible lead
  6. a failed Quo inbound read holds POST /text and does not hold POST /send
  7. an unreadable ledger blocks /send; a fresh readable ledger lets the next eligible send through
"""
import datetime
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:300]) if d and not cond else ''))


def fresh_ledger(path):
    pathlib.Path(path).write_text(
        json.dumps({'_dealflow_notes': 1, 'notes': {}}), encoding='utf-8')


def notes_of(path):
    d = json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    return d.get('notes') or {}


# ---------------------------------------------------------------- 1-4. ledger + detector
def unit_ledger():
    print('-- ledger_from_notes, quotes, permanent no, inbound SMS')
    import optout_sync as O
    import quo_sync as Q
    import replies as R
    import outreach_copy as OC

    tmp = pathlib.Path(tempfile.mkdtemp(prefix='dfsup_'))
    oo, sup, status = tmp / 'optouts.json', tmp / 'bounced_emails.json', tmp / 'quo_inbound_status.json'
    old = (O.OPTOUTS, O.SUPPRESS, Q.INBOUND_STATUS, Q._key, Q._get)
    try:
        O.OPTOUTS, O.SUPPRESS, Q.INBOUND_STATUS = str(oo), str(sup), str(status)
        fresh_ledger(oo)

        added, _already = O.ledger_from_notes({'notes': {
            '2099-000501-CA-01': {'status': 'DO NOT CONTACT', 'optout': '2026-09-26',
                                  'dntph': ['5550100199']},
            '2099-000502-CA-01': {'status': 'Called', 'note': 'left a voicemail'},
        }})
        notes = notes_of(oo)
        rec('notes DNC case is ledgered DO NOT CONTACT',
            '2099-000501-CA-01' in notes and notes['2099-000501-CA-01'].get('status') == 'DO NOT CONTACT',
            sorted(notes))
        rec('notes DNC phone is ledgered as #digits', '#5550100199' in notes, sorted(notes))
        rec('a non-DNC note is not written', '2099-000502-CA-01' not in notes and '2099-000502-CA-01' not in added,
            added)
        rec('the writer is ledger_add (optlog names it)',
            any(x.get('src') == 'optout_sync.ledger_add' for x in (notes['2099-000501-CA-01'].get('optlog') or [])),
            notes['2099-000501-CA-01'].get('optlog'))

        # quoting our sentence back is not a stop; a real stop above the quote is, and only that is ledgered
        quote = ("On Mon, Sep 21, 2026 at 2:48 PM Test Sender <sender@example.com> wrote:\r\n"
                 + OC.OPTOUT_LINE_EN)
        quoted_only = "Thanks, please call me tomorrow.\r\n\r\n" + quote
        real_stop = "This is not a good time, try next month.\r\n\r\n" + quote
        rec('quoting the opt-out line is not a stop', R.is_stop_text(R.strip_quotes(quoted_only)) is False)
        rec('a real stop above the quote still is', R.is_stop_text(R.strip_quotes(real_stop)) is True)
        before = set(notes_of(oo))
        if R.is_stop_text(R.strip_quotes(quoted_only)):
            O.ledger_add(['2099-000401-CA-01'], 'quoted reply', 'fixture')
        rec('a quote-only reply is not ledgered', '2099-000401-CA-01' not in notes_of(oo), sorted(notes_of(oo)))
        raw = (b'From: Owner <owner@example.com>\r\nSubject: Re: a note\r\n'
               b'Content-Type: text/plain; charset=utf-8\r\n\r\n' + real_stop.encode('utf-8'))
        _subj, fresh = R.reply_text(raw)
        rec('reply_text keeps the fresh stop and drops the quote',
            R.is_stop_text(fresh) is True and OC.OPTOUT_LINE_EN not in fresh, fresh[:180])
        O.ledger_add(['2099-000402-CA-01'], 'owner said not a good time', 'fixture', excerpt=fresh[:80])
        entry = notes_of(oo).get('2099-000402-CA-01') or {}
        rec('"not a good time, try next month" is ledgered permanent DO NOT CONTACT',
            entry.get('status') == 'DO NOT CONTACT' and '2099-000402-CA-01' not in before, entry.get('status'))
        note_before = entry.get('note')
        O.ledger_add(['2099-000402-CA-01'], 'second pass must not rewrite', 'fixture')
        rec('a second ledger_add does not downgrade the permanent entry',
            (notes_of(oo).get('2099-000402-CA-01') or {}).get('note') == note_before)
        rec('"try next month" alone is a stop', R.is_stop_text('try next month') is True)
        rec('"the sale is next month" is not', R.is_stop_text('the sale is next month') is False)

        # inbound SMS. _get is local; no HTTP.
        bodies = {
            '5550100101': 'STOP',
            '5550100102': 'NO MAS',
            '5550100103': 'BASTA',
            '5550100104': 'can you stop the sale',
        }

        def _get(key, path, params=None):
            if path == '/phone-numbers':
                return {'data': [{'id': 'PN1'}]}
            if path == Q.MESSAGES_PATH:
                n10 = str((params or {}).get('participants') or '')[-10:]
                return {'data': [{'direction': 'incoming', 'content': bodies.get(n10, ''),
                                  'createdAt': '2026-09-26T12:00:00Z'}]}
            raise AssertionError(path)

        Q._key = lambda: 'fixture-key'
        Q._get = _get
        rc = Q.sync_messages(days=2, phones=['5550100' + s for s in ('101', '102', '103', '104')])
        notes = notes_of(oo)
        rec('inbound scan returns 0 on a clean read', rc == 0, rc)
        rec('EN STOP is ledgered as #digits',
            (notes.get('#5550100101') or {}).get('status') == 'DO NOT CONTACT', sorted(notes))
        rec('ES NO MAS is ledgered', '#5550100102' in notes)
        rec('ES BASTA is ledgered', '#5550100103' in notes)
        rec('"can you stop the sale" is not ledgered', '#5550100104' not in notes, sorted(notes))
        held, why = Q.text_hold()
        rec('a clean inbound scan does not hold texting', held is False, why)

        def _get_fail(key, path, params=None):
            if path == '/phone-numbers':
                return {'data': [{'id': 'PN1'}]}
            raise RuntimeError('fixture read failed')

        Q._get = _get_fail
        rc = Q.sync_messages(days=2, phones=['5550100105'])
        held, why = Q.text_hold()
        rec('a failed inbound read returns non-zero and holds texting', rc != 0 and held is True, (rc, why))
        rec('the text hold says email is not held', 'mail is not held' in why.lower(), why)
    finally:
        O.OPTOUTS, O.SUPPRESS, Q.INBOUND_STATUS, Q._key, Q._get = old
        shutil.rmtree(tmp, ignore_errors=True)


def text_hold_unit():
    print('-- text_hold fail closed')
    import quo_sync as Q
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='dfqhold_'))
    try:
        missing = tmp / 'nope.json'
        held, why = Q.text_hold(path=str(missing))
        rec('missing status holds texting', held is True and 'mail is not held' in why.lower(), why)
        badf = tmp / 'bad.json'
        badf.write_text('{', encoding='utf-8')
        held, why = Q.text_hold(path=str(badf))
        rec('unreadable status holds texting', held is True, why)
        fail = tmp / 'fail.json'
        fail.write_text(json.dumps({'ok': False, 'ts': '2026-09-26T12:00:00+00:00', 'why': 'fixture'}),
                        encoding='utf-8')
        held, why = Q.text_hold(path=str(fail))
        rec('ok:false holds texting', held is True and 'failed' in why.lower(), why)
        stale = tmp / 'stale.json'
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=40)).isoformat()
        stale.write_text(json.dumps({'ok': True, 'ts': old}), encoding='utf-8')
        held, why = Q.text_hold(path=str(stale))
        rec('a scan older than 36h holds texting', held is True and 'old' in why.lower(), why)
        fresh = tmp / 'fresh.json'
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        fresh.write_text(json.dumps({'ok': True, 'ts': now, 'checked': 1, 'stops': 0}), encoding='utf-8')
        held, why = Q.text_hold(path=str(fresh))
        rec('a fresh ok scan does not hold texting', held is False and why == '', why)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 5. cadence send-time
def cadence_recheck():
    print('-- cadence send-time re-check, and a clean lead still sends')
    import cadence as C
    import send_server as S
    import diligence_gate as DG

    ctmp = pathlib.Path(tempfile.mkdtemp(prefix='dfcadfix_'))
    saved = {
        'HERE': C.HERE, 'QUEUE': C.QUEUE, 'STATE': C.STATE, 'OPTOUTS': C.OPTOUTS,
        'load_optouts': C._oe._load_optouts, 'load_leads': C._oe._load_leads,
        'gate': DG.gate, 'load_key': C.load_key, 'imap': C.imap_replies, 'steps': C.steps,
        'ev': S._deliverability_evidence, 'senders': S._load_senders, 'smtp': S._smtp_send,
        'active': S._senders_active, 'ledger': S.SENT_LEDGER,
    }
    sent = []
    try:
        C.HERE = str(ctmp)
        C.QUEUE = str(ctmp / 'cadence_queue.json')
        C.STATE = str(ctmp / 'cadence_state.json')
        C.OPTOUTS = str(ctmp / 'optouts.json')
        S.SENT_LEDGER = str(ctmp / 'mail_sent.json')
        json.dump([], open(S.SENT_LEDGER, 'w'))
        fresh_ledger(C.OPTOUTS)
        today = datetime.date.today().isoformat()

        def sync_ok(directory):
            json.dump({
                'date': today, 'state': 'finished', 'ok': True,
                'started_at': time.time() - 120, 'finished_at': time.time() - 60, 'steps': [],
            }, open(os.path.join(directory, 'sync_status.json'), 'w'))

        def run_cadence():
            argv, stdout = sys.argv, sys.stdout
            sys.argv = ['cadence.py']
            buf = io.StringIO()
            sys.stdout = buf
            try:
                rc = C.main()
            finally:
                sys.argv, sys.stdout = argv, stdout
            return rc, buf.getvalue()

        # No sync_status.json: running cadence.py directly is held, same as the .bat.
        rows_probe = [{'case': '2099-000304-CA-01', 'owner': 'Clean', 'email': 'clean@example.com', 'step': 0}]
        json.dump({'sender': {'name': 'Test Sender'}, 'queue': rows_probe}, open(C.QUEUE, 'w'))
        C._oe._load_optouts = lambda: set()
        C._oe._load_leads = lambda: [{'case': '2099-000304-CA-01', 'days': 40}]
        import diligence_gate as DG0
        DG0.gate = lambda row: {'hold': False, 'code': '', 'why': ''}
        C.load_key = lambda: ('sender@example.com', 'app-password')
        C.imap_replies = lambda *a, **k: {}
        C.steps = lambda s, sender: [('subj %d' % i, 'body %d about 1 Example St.' % i) for i in range(4)]
        zb0 = {'v': 'ok', 'why': 'zerobounce:valid', 'd': today}
        S._deliverability_evidence = lambda: {
            'bounced': set(), 'replied': set(), 'proven': set(),
            'ver': {'clean@example.com': zb0}, 'last_mailed': {}}
        S._load_senders = lambda: {
            'main_domain': 'example.com', 'main_domain_cap': 40,
            'lanes': {'active': 'sender@example.com', 'default': 'sender@example.com', 'early': 'sender@example.com'},
            'first_touch': {'from': [], 'per_day': 0}}
        S._senders_active = lambda user, cfg: False
        S._smtp_send = lambda *a, **k: sent.append(a[3] if len(a) > 3 else k.get('to_addr')) or '<mid@test>'
        rc, out = run_cadence()
        st = json.load(open(C.STATE))
        rec('cadence.py with no 07:15 sync holds and does not mail',
            not sent and st['2099-000304-CA-01']['step'] == 0 and 'HOLD' in out, out[-400:])
        sent.clear()
        os.remove(C.STATE)
        rows = [
            {'case': '2099-000301-CA-01', 'owner': 'Stay', 'email': 'stayed@example.com', 'step': 0},
            {'case': '2099-000302-CA-01', 'owner': 'Pass', 'email': 'passed@example.com', 'step': 0},
            {'case': '2099-000303-CA-01', 'owner': 'Opt', 'email': 'opted@example.com', 'step': 0},
            {'case': '2099-000304-CA-01', 'owner': 'Clean', 'email': 'clean@example.com', 'step': 0},
        ]
        json.dump({'sender': {'name': 'Test Sender'}, 'queue': rows}, open(C.QUEUE, 'w'))
        leads = {
            '2099-000301-CA-01': {'case': '2099-000301-CA-01', 'days': 40, 'saleBkAct': True},
            '2099-000302-CA-01': {'case': '2099-000302-CA-01', 'days': -3},
            '2099-000303-CA-01': {'case': '2099-000303-CA-01', 'days': 40},
            '2099-000304-CA-01': {'case': '2099-000304-CA-01', 'days': 40},
        }
        C._oe._load_optouts = lambda: {'opted@example.com'}
        C._oe._load_leads = lambda: list(leads.values())
        DG.gate = lambda row: {'hold': False, 'code': '', 'why': ''}
        C.load_key = lambda: ('sender@example.com', 'app-password')
        C.imap_replies = lambda *a, **k: {}
        C.steps = lambda s, sender: [('subj %d' % i, 'body %d about 1 Example St.' % i) for i in range(4)]
        zb = {'v': 'ok', 'why': 'zerobounce:valid', 'd': today}
        S._deliverability_evidence = lambda: {
            'bounced': set(), 'replied': set(), 'proven': set(),
            'ver': {'clean@example.com': zb, 'stayed@example.com': zb,
                    'passed@example.com': zb, 'opted@example.com': zb},
            'last_mailed': {}}
        S._load_senders = lambda: {
            'main_domain': 'example.com', 'main_domain_cap': 40,
            'lanes': {'active': 'sender@example.com', 'default': 'sender@example.com', 'early': 'sender@example.com'},
            'first_touch': {'from': [], 'per_day': 0}}
        S._senders_active = lambda user, cfg: False
        S._smtp_send = lambda *a, **k: sent.append(a[3] if len(a) > 3 else k.get('to_addr')) or '<mid@test>'
        sync_ok(str(ctmp))

        argv, stdout = sys.argv, sys.stdout
        sys.argv = ['cadence.py']
        buf = io.StringIO()
        sys.stdout = buf
        try:
            rc = C.main()
        finally:
            sys.argv, sys.stdout = argv, stdout
        out = buf.getvalue()
        st = json.load(open(C.STATE))
        rec('cadence exits 0', rc == 0, rc)
        rec('stayed case is held and not mailed',
            st['2099-000301-CA-01']['status'] == 'held' and 'stayed@example.com' not in sent, (st['2099-000301-CA-01']['status'], sent))
        rec('auction-passed case is cancelled and not mailed',
            st['2099-000302-CA-01']['status'] == 'cancelled' and 'passed@example.com' not in sent,
            st['2099-000302-CA-01']['status'])
        rec('an address already on the opt-out ledger is suppressed and not mailed',
            st['2099-000303-CA-01']['status'] == 'suppressed' and 'opted@example.com' not in sent,
            st['2099-000303-CA-01']['status'])
        rec('a clean eligible lead is still sent', 'clean@example.com' in sent, sent)
        rec('the clean lead advances one step', st['2099-000304-CA-01']['step'] == 1, st['2099-000304-CA-01'])

        # unreadable ledger: the same clean lead, a new queue, nothing mailed
        sent.clear()
        c2 = ctmp / 'bad'
        c2.mkdir()
        C.QUEUE = str(c2 / 'q.json')
        C.STATE = str(c2 / 's.json')
        C.OPTOUTS = str(c2 / 'optouts.json')
        (c2 / 'optouts.json').write_text('{', encoding='utf-8')
        json.dump({'sender': {'name': 'Test Sender'}, 'queue': [
            {'case': '2099-000304-CA-01', 'owner': 'Clean', 'email': 'clean@example.com', 'step': 0}]},
                  open(C.QUEUE, 'w'))
        buf = io.StringIO()
        sys.stdout = buf
        sys.argv = ['cadence.py']
        try:
            C.main()
        finally:
            sys.argv, sys.stdout = argv, stdout
        out = buf.getvalue()
        st = json.load(open(C.STATE))
        rec('an unreadable ledger holds cadence and does not mail the clean lead',
            not sent and st['2099-000304-CA-01']['step'] == 0 and 'UNREADABLE' in out, out[-400:])
    finally:
        C.HERE, C.QUEUE, C.STATE, C.OPTOUTS = saved['HERE'], saved['QUEUE'], saved['STATE'], saved['OPTOUTS']
        C._oe._load_optouts, C._oe._load_leads = saved['load_optouts'], saved['load_leads']
        DG.gate, C.load_key, C.imap_replies, C.steps = saved['gate'], saved['load_key'], saved['imap'], saved['steps']
        S._deliverability_evidence, S._load_senders = saved['ev'], saved['senders']
        S._smtp_send, S._senders_active, S.SENT_LEDGER = saved['smtp'], saved['active'], saved['ledger']
        shutil.rmtree(ctmp, ignore_errors=True)


# ---------------------------------------------------------------- 6-7. live bridge
def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, path, payload=None, timeout=8):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data,
                                 headers={'Content-Type': 'application/json'} if data else {},
                                 method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {'err': str(e)}


def bridge():
    print('-- Quo read failure holds texting, not email; unreadable ledger blocks sends')
    port = free_port()
    work = pathlib.Path(tempfile.mkdtemp(prefix='dfsupsrv_'))
    proc = None
    try:
        for f in ('send_server.py', 'stay_gate.py', 'mail_guard.py', 'sync_gate.py', 'quo_sync.py'):
            shutil.copy(HERE / f, work / f)
        today = datetime.date.today().isoformat()
        (work / 'sync_status.json').write_text(json.dumps({
            'date': today, 'state': 'finished', 'ok': True,
            'started_at': time.time() - 120, 'finished_at': time.time() - 60, 'steps': []}), encoding='utf-8')
        (work / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
        (work / 'sender.json').write_text(json.dumps({'name': 'Test Sender'}), encoding='utf-8')
        oo = work / 'optouts.json'
        fresh_ledger(oo)
        (work / 'sale_history_cache.json').write_text(json.dumps({
            '2099-000310-CA-01': {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0}}), encoding='utf-8')
        (work / 'verified_emails.json').write_text(json.dumps({
            'owner%d@example.com' % i: {'v': 'ok', 'why': 'zerobounce:valid', 'd': today}
            for i in range(1, 8)}), encoding='utf-8')
        (work / 'quo_inbound_status.json').write_text(json.dumps({
            'ok': False, 'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'why': 'fixture read failed', 'checked': 0, 'stops': 0, 'errors': 1}), encoding='utf-8')
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
        err = open(work / 'bridge.err', 'w', encoding='utf-8')
        proc = subprocess.Popen([sys.executable, str(shim)], cwd=str(work),
                                stdout=subprocess.DEVNULL, stderr=err)
        up = False
        for _ in range(60):
            if call(port, '/health')[0] == 200:
                up = True
                break
            time.sleep(0.25)
        rec('bridge starts', up, (work / 'bridge.err').read_text(encoding='utf-8')[-400:] if not up else '')
        if not up:
            return

        def smtp_n():
            p = work / 'smtp_calls.txt'
            return p.read_text(encoding='utf-8').split() if p.exists() else []

        st, h = call(port, '/health')
        rec('/health reports the text hold and a fresh opt-out sync',
            h.get('text_hold') is True and h.get('sync_ok') is True and h.get('optout_stale') is False,
            {k: h.get(k) for k in ('text_hold', 'sync_ok', 'optout_stale', 'text_hold_why')})
        rec('the text-hold reason says email is not held',
            'mail is not held' in str(h.get('text_hold_why') or '').lower(), h.get('text_hold_why'))

        st, j = call(port, '/text', {'case': '2099-000310-CA-01', 'to': '5550100101', 'confirmed': True})
        rec('POST /text is refused while the inbound scan failed',
            st == 200 and j.get('ok') is False and j.get('blocked') == 'quo_inbound', {'st': st, 'j': j})
        rec('a refused text writes no text_sent.json row', not (work / 'text_sent.json').exists())

        st, j = call(port, '/send', {
            'to': 'owner1@example.com', 'subj': 'About 1 Main St',
            'body': 'Hello Jane, a short note about 1 Main St.',
            'meta': {'owner': 'Test Owner', 'addr': '1 Main St', 'wl': 'active', 'c': '2099-000310-CA-01'}})
        rec('a clean eligible lead still sends while texting is held',
            st == 200 and j.get('ok') is True, {'st': st, 'j': j})
        rec('that send reached SMTP once', smtp_n() == ['owner1@example.com'], smtp_n())

        oo.write_text('{', encoding='utf-8')
        raw = oo.read_bytes()
        import optout_sync as O
        old_oo, old_sup = O.OPTOUTS, O.SUPPRESS
        O.OPTOUTS, O.SUPPRESS = str(oo), str(work / 'bounced_emails.json')
        try:
            try:
                O.ledger_add(['2099-000310-CA-01'], 'must not replace a torn ledger', 'fixture')
                refused = False
            except O.LedgerUnreadable:
                refused = True
            backs = list(work.glob('optouts.json.corrupt-*'))
            rec('ledger_add refuses a corrupt ledger', refused)
            rec('the corrupt ledger bytes are unchanged', oo.read_bytes() == raw, oo.read_bytes()[:40])
            rec('the bad bytes were copied to a timestamped backup',
                len(backs) == 1 and backs[0].read_bytes() == raw, [p.name for p in backs])
        finally:
            O.OPTOUTS, O.SUPPRESS = old_oo, old_sup
        st, j = call(port, '/send', {
            'to': 'owner2@example.com', 'subj': 'About 1 Main St',
            'body': 'Hello Jane, a short note about 1 Main St.',
            'meta': {'owner': 'Test Owner', 'addr': '1 Main St', 'wl': 'active', 'c': '2099-000310-CA-01'}})
        rec('an unreadable ledger blocks /send',
            st == 200 and j.get('ok') is False and j.get('blocked') == 'optout_stale'
            and 'UNREADABLE' in str(j.get('err') or ''), {'st': st, 'j': j})
        rec('the blocked send did not reach SMTP', smtp_n() == ['owner1@example.com'], smtp_n())

        fresh_ledger(oo)
        st, j = call(port, '/send', {
            'to': 'owner3@example.com', 'subj': 'About 1 Main St',
            'body': 'Hello Jane, a short note about 1 Main St.',
            'meta': {'owner': 'Test Owner', 'addr': '1 Main St', 'wl': 'active', 'c': '2099-000310-CA-01'}})
        rec('a readable ledger lets the next eligible lead send',
            st == 200 and j.get('ok') is True and smtp_n() == ['owner1@example.com', 'owner3@example.com'],
            {'st': st, 'j': j, 'smtp': smtp_n()})
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    unit_ledger()
    text_hold_unit()
    cadence_recheck()
    bridge()
    print('\n%d passed, %d failed' % (len(ok), len(bad)))
    sys.exit(1 if bad else 0)
