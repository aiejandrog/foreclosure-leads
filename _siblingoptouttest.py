"""_siblingoptouttest -- a stop on one of an owner's cases refuses a send for their other case.

Run: python _siblingoptouttest.py   (exit 0 = safe; no network, SMTP stubbed, synthetic data only)

WHY (2026-10-08). The ledger is keyed by case. send_server /send refused on the recipient address
or the meta.c case only, so a stop ledgered on case A after the board was built did not stop an
email for the same owner's case B sent to a different address. The board now hands over the owner's
other cases (meta.pcs) and the portfolio cases in the message (meta.portfolio); a stop on any of
them refuses the send. It can only add a refusal. Also pins the morning planner's sibling rule
(a shared email or number puts the other case under the same opt-out) and its institution guard.
"""
import json, os, pathlib, shutil, socket, subprocess, sys, tempfile, time, urllib.request, urllib.error
import datetime as dt

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:200]) if d and not cond else ''))


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def call(port, path, payload=None, timeout=8):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data,
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


def planner():
    print('-- morning planner')
    sys.argv = [sys.argv[0]]
    import morning_planner as MP
    def L(case, emails=(), phones=()):
        return {'case': case, 'emails': list(emails), 'phones': list(phones)}
    leads = [L('FAKE-1', ['a@x.invalid'], ['3055550101']),
             L('FAKE-2', ['a@x.invalid']),                         # same email, other case
             L('FAKE-3', [], [{'number': '(305) 555-0101', 'score': 90}]),   # same number, dict-shaped
             L('FAKE-4', ['b@x.invalid'], ['3055550104']),         # unrelated
             L('FAKE-5', ['lawyer@x.invalid']), L('FAKE-6', ['lawyer@x.invalid']),
             L('FAKE-7', ['lawyer@x.invalid'])]
    led = {'notes': {'FAKE-1': {'status': 'DO NOT CONTACT'}}}
    MP._SIB = {'em': set(), 'ph': set()}
    before = [MP._opted_out(r, led) for r in leads]
    rec('before siblings: only the ledgered case is out', before == [True, False, False, False, False, False, False], before)
    MP._set_siblings(leads, led)
    after = [MP._opted_out(r, led) for r in leads]
    rec('same email and same number close the other cases', after[:3] == [True, True, True], after)
    rec('an unrelated owner stays', after[3] is False, after)
    led2 = {'notes': {'FAKE-5': {'status': 'DO NOT CONTACT'}}}
    MP._set_siblings(leads * 3, led2, max_shared=8)               # shared by 9 leads: an institution
    rec('an email on more than 8 leads is an institution, not a person',
        not MP._opted_out(L('FAKE-9', ['lawyer@x.invalid']), {}) , MP._SIB)
    MP._set_siblings(leads * 3, led2, max_shared=99)             # same data, no institution guard
    rec('...and the guard is what kept it out', MP._opted_out(L('FAKE-9', ['lawyer@x.invalid']), {}))
    MP._SIB = {'em': set(), 'ph': set()}


def server():
    print('-- live bridge')
    port = free_port()
    work = pathlib.Path(tempfile.mkdtemp(prefix='dfsib_'))
    proc = None
    try:
        for f in ('send_server.py', 'stay_gate.py', 'mail_guard.py', 'sync_gate.py'):
            shutil.copy(HERE / f, work / f)
        (work / 'sync_status.json').write_text(json.dumps({
            'date': dt.date.today().isoformat(), 'state': 'finished', 'ok': True,
            'started_at': time.time() - 120, 'finished_at': time.time() - 60, 'steps': []}), encoding='utf-8')
        (work / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
        (work / 'sender.json').write_text(json.dumps({'name': 'Test Sender'}), encoding='utf-8')
        (work / 'senders.json').write_text(json.dumps({
            'main_domain': 'example.com', 'main_domain_cap': 40,
            'ramp_start': '2020-01-01', 'ramp': [{'through_day': 9999, 'per_day': 100}],
            'lanes': {'default': 'tester@example.com', 'active': 'tester@example.com'},
            'first_touch': {'from': ['warm@wu.example'], 'per_day': 100}}), encoding='utf-8')
        (work / 'bounced_emails.json').write_text('{}', encoding='utf-8')
        (work / 'optouts.json').write_text(json.dumps({'_dealflow_notes': True, 'notes': {
            '2099-000001-CA-01': {'status': 'DO NOT CONTACT', 'optout': '2099-01-01'}}}), encoding='utf-8')
        # stay data: every case below is clear, so only the opt-out check can refuse
        (work / 'sale_history_cache.json').write_text(json.dumps({
            '2099-%06d-CA-01' % i: {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0}
            for i in range(1, 12)}), encoding='utf-8')
        (work / 'verified_emails.json').write_text(json.dumps(
            {'owner%d@example.com' % i: {'v': 'ok', 'why': 'zerobounce:valid', 'd': '2026-09-26'}
             for i in range(1, 60)}), encoding='utf-8')
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
            if call(port, '/health')[0] == 200:
                up = True; break
            time.sleep(0.25)
        rec('bridge starts', up)
        if not up:
            return
        n = [0]

        def smtp_calls():
            p = work / 'smtp_calls.txt'
            return p.read_text(encoding='utf-8').split() if p.exists() else []

        def send(case, **extra):
            n[0] += 1
            meta = {'c': case, 'owner': 'Jane', 'addr': '1 Main St', 'wl': 'active'}
            meta.update(extra)
            return call(port, '/send', {'to': 'owner%d@example.com' % n[0], 'subj': 'About 1 Main St',
                                        'body': 'Hello Jane, a short note.', 'meta': meta})

        st, j = send('2099-000001-CA-01')
        rec('control: the ledgered case itself is refused', j.get('blocked') == 'optout', (st, j))
        st, j = send('2099-000002-CA-01')
        rec('control: an unrelated case with no sibling list sends', j.get('ok') is True, (st, j))
        before = len(smtp_calls())
        st, j = send('2099-000003-CA-01', pcs=['2099-000001-CA-01'])
        rec('other case of an opted-out owner (meta.pcs) is refused', j.get('blocked') == 'optout'
            and 'same owner' in str(j.get('err')), (st, j))
        st, j = send('2099-000004-CA-01', portfolio=['2099-000001-ca-01'])
        rec('portfolio message that includes an opted-out case is refused (any case)', j.get('blocked') == 'optout', (st, j))
        rec('refused sends reached no SMTP', len(smtp_calls()) == before, smtp_calls())
        st, j = send('2099-000005-CA-01', pcs=['2099-000009-CA-01', 7, None, {'x': 1}])
        rec('a sibling list with no opted-out case, or junk entries, changes nothing', j.get('ok') is True, (st, j))
        st, j = send('2099-000006-CA-01', pcs='2099-000001-CA-01')
        rec('a non-list pcs is ignored, not an error', j.get('ok') is True, (st, j))
        st, j = send('2099-000007-CA-01', test=True, pcs=['2099-000001-CA-01'])
        rec('operator test sends (meta.test) skip the sibling check like the case check', j.get('blocked') != 'optout', (st, j))
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(5)
            except Exception:
                proc.kill()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    planner()
    server()
    print(f'\n==== {len(ok)}/{len(ok) + len(bad)} sibling opt-out checks passed ====')
    if bad:
        print('FAILED:', bad)
    raise SystemExit(0 if not bad else 1)
