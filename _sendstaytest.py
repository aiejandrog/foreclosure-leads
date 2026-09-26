"""_sendstaytest -- the send bridge refuses a case under a live §362 stay, and fails CLOSED.

Run:  python _sendstaytest.py     (exit 0 = safe; no network, no SMTP, synthetic data only)

WHY (2026-09-26). send_server.py POST /send checked opt-outs, caps, the bounce breaker and
mail_guard, and never asked whether the case was under an automatic bankruptcy stay. It trusted the
board page, and three Miami cases were emailed under a live stay the week of 2026-09-21 because a
board is a snapshot. The bridge now decides stay status itself from sale_history_cache.json
(stay_gate.py). This file pins:

  1. stay_gate.check() on its own: stayed blocked, lifted allowed, non-stayed allowed, missing /
     unresolvable / never-read case blocked, missing / corrupt / empty cache blocked, stem matching
     across the -CA-01 suffix, a sibling suffix's active stay blocks, a cache rewrite is seen on
     the next call, labelled numbers ("CASE NO 2025-...", "Case No.", "CASE #") resolve to the
     cache key while unreadable ones are still refused.
  2. PARITY with outreach_email: on the same cache, the rows outreach_email's own merge +
     _eligible() refuse for 'active bankruptcy stay' are exactly the cases stay_gate calls
     stay_active. The bridge reuses that rule; it does not invent a second one.
  3. The REAL server on a scratch port with SMTP stubbed: a refused case sends nothing, writes no
     mail_sent.json row, answers 451/503 with a machine-readable `blocked` code, and leaves a line
     in send_refusals.jsonl.
"""
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

import stay_gate as SG  # noqa: E402

ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:200]) if d and not cond else ''))


# Synthetic cache in sale_history.py's shape (v5 entries carry a/bd/sl).
CACHE = {
    '2099-000001-CA-01': {'a': True, 'bd': '2026-09-10', 'sl': '', 'b': 1, 'v': 5, 't': 0},   # stayed
    '2099-000002-CA-01': {'a': False, 'bd': '2026-03-01', 'sl': '2026-06-17', 'b': 1, 'v': 5, 't': 0},  # lifted
    '2099-000003-CA-01': {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0},             # never stayed
    '2099-000004-CA-01': {'s': 2, 'n': 3, 'v': 2, 't': 0},                                    # pre-v4: no stay fields
    '2099-000005':       {'a': True, 'bd': '2026-09-01', 'sl': '', 'b': 1, 'v': 5, 't': 0},   # stored WITHOUT suffix
    '2099-000006-CA-01': {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0},             # clean sibling ...
    '2099-000006-CC-01': {'a': True, 'bd': '2026-08-20', 'sl': '', 'b': 1, 'v': 5, 't': 0},   # ... active sibling
    '2099-000007-CA-01': {'a': True, 'bd': '2026-01-05', 'sl': '2026-02-01', 'v': 5, 't': 0},  # a + sl: lift wins
}


def unit():
    print('-- stay_gate.check()')
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='dfstay_'))
    try:
        cp = str(tmp / 'sale_history_cache.json')
        pathlib.Path(cp).write_text(json.dumps(CACHE), encoding='utf-8')
        c = lambda case: SG.check(case, cp)

        r = c('2099-000001-CA-01')
        rec('stayed case is refused (stay_active)', not r['ok'] and r['code'] == SG.STAY_ACTIVE, r)
        rec('the refusal names the filing date', '2026-09-10' in r['why'], r['why'])
        r = c('2099-000002-CA-01')
        rec('lifted stay is allowed', r['ok'] and r['code'] == SG.CLEAR and r['sl'] == '2026-06-17', r)
        r = c('2099-000003-CA-01')
        rec('never-stayed case is allowed', r['ok'] and r['code'] == SG.CLEAR, r)
        r = c('2099-000007-CA-01')
        rec('a=True with a lift date reads as lifted (same as outreach_email: saleLift clears)',
            r['ok'], r)
        for missing in ('', None, '   '):
            r = c(missing)
            rec('missing case %r is refused (stay_no_case)' % (missing,),
                not r['ok'] and r['code'] == SG.NO_CASE, r)
        for weird in ('CACE-24-001234', '50-2024-CA-001234-XXXX-MB', 'CASE-1', '2099-00001-CA-01'):
            r = c(weird)
            rec('unresolvable case %r is refused (stay_case_unresolvable)' % weird,
                not r['ok'] and r['code'] == SG.UNRESOLVABLE, r)
        r = c('2099-000999-CA-01')
        rec('a valid stem the cache never read is refused (stay_unverified)',
            not r['ok'] and r['code'] == SG.UNVERIFIED, r)
        r = c('2099-000004-CA-01')
        rec('a pre-v4 entry with no stay fields is refused, not read as clear',
            not r['ok'] and r['code'] == SG.UNVERIFIED, r)

        # ---- stem matching (first 11 chars) ----
        r = c('2099-000001')
        rec('stem: bare 2099-000001 finds the -CA-01 entry and is refused',
            r['code'] == SG.STAY_ACTIVE and r['matched'] == ['2099-000001-CA-01'], r)
        r = c('2099-000001-CA-02')
        rec('stem: a different suffix on the same stem is refused', r['code'] == SG.STAY_ACTIVE, r)
        r = c('2099-000005-CA-01')
        rec('stem: board number with -CA-01 matches a cache key stored without the suffix',
            r['code'] == SG.STAY_ACTIVE and r['matched'] == ['2099-000005'], r)
        r = c(' 2099-000001-ca-01 ')
        rec('stem: case/whitespace-insensitive', r['code'] == SG.STAY_ACTIVE, r)
        r = c('2099-000006-CA-01')
        rec('stem: one ACTIVE entry among siblings blocks even when the exact key is clean',
            r['code'] == SG.STAY_ACTIVE and len(r['matched']) == 2, r)
        rec('case_stem() is the first 11 characters', SG.case_stem('2025-007384-CA-01') == '2025-007384'
            and SG.case_stem('CACE-24-1') == '')

        # ---- labelled numbers ("CASE NO 2025-...") resolve to the cache key; junk still refuses ----
        # One Miami-Dade lis pendens row carries its number as "CASE NO 2025-...". Before, that raw
        # string had no stem and the lead was refused as unresolvable forever, even once
        # sale_history.py (#73) had read its docket under the clean number.
        for lab in ('CASE NO 2099-000001-CA-01', 'Case No. 2099-000001-CA-01', 'Case No.: 2099-000001',
                    'CASE # 2099-000001-CA-01', 'CASE#2099-000001-CA-01', 'Case Number: 2099-000001-CA-01',
                    'NO. 2099-000001-CA-01', '  case  no   2099 - 000001 - CA - 01 '):
            r = c(lab)
            rec('label: %r resolves to the stayed case and is refused (stay_active)' % lab,
                r['code'] == SG.STAY_ACTIVE and r['matched'] == ['2099-000001-CA-01'], r)
        r = c('CASE NO 2099-000003-CA-01')
        rec('label: "CASE NO" on a read, clear case clears (the lead is no longer wrongly blocked)',
            r['ok'] and r['code'] == SG.CLEAR, r)
        r = c('CASE NO 2099-000999-CA-01')
        rec('label: "CASE NO" on a case never read is still refused (stay_unverified)',
            not r['ok'] and r['code'] == SG.UNVERIFIED, r)
        r = c('CASE NO 2099-000004-CA-01')
        rec('label: "CASE NO" on a pre-v4 entry is still refused (stay_unverified)',
            not r['ok'] and r['code'] == SG.UNVERIFIED, r)
        for junk in ('CASE', 'CASE NO', 'Case No.:', 'CASE NO CACE-24-001234', 'CASE # 50-2024-CA-001234-XXXX-MB',
                     'BW 2099-000003-CA-01', 'LP-ZZOWNERNAME', 'CASE NO. 2099000003CA01', 'NO2099-000003-CA-01',
                     'CASE NO CASE NO 2099-000003-CA-01', 'CASE-2099-000003-CA-01', '2099-000003x', 'x2099-000003'):
            r = c(junk)
            rec('label: genuinely unreadable %r is still refused (stay_case_unresolvable)' % junk,
                not r['ok'] and r['code'] == SG.UNRESOLVABLE, r)
        # the sequence must END after six digits. Before, the stem was simply the first 11 characters,
        # so a mistyped 2099-0000031 read as 2099-000003 -- a CLEAR case -- and was allowed.
        r = c('2099-0000031-CA-01')
        rec('a seven-digit sequence is not truncated onto a clear case (refused, not cleared)',
            not r['ok'] and r['code'] == SG.UNRESOLVABLE, r)
        r = c('2099-000003-CA-01')
        rec('... while the real six-digit number still clears', r['ok'], r)
        rec('case_stem(): labels and hyphen spacing normalise to the cache key',
            SG.case_stem('CASE NO 2025-007384-CA-01') == SG.case_stem('2025 - 007384') == '2025-007384'
            and SG.case_stem('2025-007384CA01') == '2025-007384')
        # a cache written with a labelled key is indexed on the same stem (never silently dropped)
        cpl = str(tmp / 'labelled_cache.json')
        pathlib.Path(cpl).write_text(json.dumps({
            'CASE NO 2099-000008-CA-01': {'a': True, 'bd': '2026-09-02', 'sl': '', 'b': 1, 'v': 5, 't': 0},
            '2099-000009-CA-01': {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0}}), encoding='utf-8')
        r = SG.check('2099-000008-CA-01', cpl)
        rec('a labelled cache key still blocks its case', r['code'] == SG.STAY_ACTIVE, r)

        # ---- fresh reads ----
        c('2099-000003-CA-01')                                   # warm the memo
        cache2 = dict(CACHE)
        cache2['2099-000003-CA-01'] = {'a': True, 'bd': '2026-09-25', 'sl': '', 'b': 1, 'v': 5, 't': 1}
        time.sleep(0.02)
        pathlib.Path(cp).write_text(json.dumps(cache2, indent=1), encoding='utf-8')
        os.utime(cp, None)
        r = c('2099-000003-CA-01')
        rec('a stay written to the cache after startup is seen on the next check (mtime reload)',
            r['code'] == SG.STAY_ACTIVE, r)

        # ---- fail closed on the data itself ----
        pathlib.Path(cp).write_text('{"2099-000003-CA-01": {"a": fal', encoding='utf-8')
        r = c('2099-000003-CA-01')
        rec('corrupt / half-written cache is refused (stay_data_unavailable)',
            not r['ok'] and r['code'] == SG.UNAVAILABLE, r)
        r = c('2099-000002-CA-01')
        rec('... and a previously-parsed clear verdict is NOT served from memory',
            not r['ok'] and r['code'] == SG.UNAVAILABLE, r)
        pathlib.Path(cp).write_text('{}', encoding='utf-8')
        r = c('2099-000003-CA-01')
        rec('empty cache is refused, not read as "no stays"', r['code'] == SG.UNAVAILABLE, r)
        pathlib.Path(cp).write_text('[1, 2]', encoding='utf-8')
        r = c('2099-000003-CA-01')
        rec('wrong-shape cache (list) is refused', r['code'] == SG.UNAVAILABLE, r)
        os.remove(cp)
        r = c('2099-000003-CA-01')
        rec('missing cache file is refused', not r['ok'] and r['code'] == SG.UNAVAILABLE, r)
        h = SG.health(cp)
        rec('health() reports the missing cache', h['ok'] is False and h['err'], h)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parity():
    """stay_gate's verdict == outreach_email's own merge + _eligible, on the same cache."""
    print('-- parity with outreach_email')
    import outreach_email as OE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='dfstaypar_'))
    here0 = OE.HERE
    try:
        (tmp / 'sale_history_cache.json').write_text(json.dumps(CACHE), encoding='utf-8')
        cases = [k for k in CACHE if len(k) == 17]              # the exact-key rows OE can merge
        (tmp / 'leads_final.json').write_text(json.dumps(
            [{'Case #': k, 'owners': 'JANE Q HOMEOWNER', 'emails': ['x%d@example.com' % i]}
             for i, k in enumerate(cases)]), encoding='utf-8')
        OE.HERE = str(tmp)
        rows = OE._load_leads()
        oe_stayed = set()
        for r in rows:
            okk, why = OE._eligible(r, [], set(), 0)
            if why == 'active bankruptcy stay':
                oe_stayed.add(OE._case(r))
        # Same PREDICATE, entry for entry ...
        pred = {k for k in cases if SG.entry_stay_active(CACHE[k])}
        rec('same predicate: outreach_email refuses exactly the entries entry_stay_active() flags',
            oe_stayed == pred, {'outreach_email': sorted(oe_stayed), 'stay_gate': sorted(pred)})
        # ... and the bridge is only ever STRICTER: stem matching adds sibling-suffix blocks
        # (2099-000006-CA-01 is clean on its own key and blocked by its active -CC-01 sibling).
        sg_stayed = {k for k in cases if SG.check(k, str(tmp / 'sale_history_cache.json'))['code']
                     == SG.STAY_ACTIVE}
        rec('the bridge never clears a case outreach_email would refuse (superset)',
            oe_stayed <= sg_stayed, {'outreach_email': sorted(oe_stayed), 'bridge': sorted(sg_stayed)})
        rec('the only extra bridge refusal here is the stem sibling',
            sg_stayed - oe_stayed == {'2099-000006-CA-01'}, sorted(sg_stayed - oe_stayed))
        rec('the parity check is not vacuous (both see the stayed rows)',
            {'2099-000001-CA-01', '2099-000006-CC-01'} <= oe_stayed, sorted(oe_stayed))
    finally:
        OE.HERE = here0
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------ live server
def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


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


def server():
    print('-- live bridge')
    port = free_port()
    work = pathlib.Path(tempfile.mkdtemp(prefix='dfstaysrv_'))
    proc = None
    try:
        # The bridge imports stay_gate and mail_guard from its own folder, exactly as in the repo.
        for f in ('send_server.py', 'stay_gate.py', 'mail_guard.py'):
            shutil.copy(HERE / f, work / f)
        (work / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
        (work / 'sender.json').write_text(json.dumps({'name': 'Test Sender'}), encoding='utf-8')
        # A FRESH, empty opt-out ledger so the 2-day staleness gate does not refuse first.
        (work / 'optouts.json').write_text(json.dumps({'_dealflow_notes': True, 'notes': {}}),
                                           encoding='utf-8')
        cp = work / 'sale_history_cache.json'
        cp.write_text(json.dumps(CACHE), encoding='utf-8')
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
                up = True
                break
            time.sleep(0.25)
        rec('bridge starts', up)
        if not up:
            return

        def smtp_calls():
            p = work / 'smtp_calls.txt'
            return p.read_text(encoding='utf-8').split() if p.exists() else []

        def ledger():
            p = work / 'mail_sent.json'
            return json.loads(p.read_text(encoding='utf-8')) if p.exists() else []

        def refusals():
            p = work / 'send_refusals.jsonl'
            return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []

        n = [0]

        def send(case, test=False, drop_c=False):
            n[0] += 1
            meta = {'owner': 'Jane', 'addr': '1 Main St', 'wl': 'active'}
            if not drop_c:
                meta['c'] = case
            if test:
                meta['test'] = True
            return call(port, '/send', {'to': 'owner%d@example.com' % n[0], 'subj': 'About 1 Main St',
                                        'body': 'Hello Jane, a short note about 1 Main St.', 'meta': meta})

        st, h = call(port, '/health')
        sd = h.get('stay_data') or {}
        rec('/health exposes stay_data (readable, counts the active stays)',
            sd.get('ok') is True and sd.get('cases') == 8 and sd.get('active') == 3, sd)

        st, j = send('2099-000001-CA-01')
        rec('stayed case: 451 blocked=stay_active', st == 451 and j.get('blocked') == 'stay_active'
            and j.get('ok') is False, {'st': st, 'j': j})
        rec('stayed case: skip=true so the worker advances instead of halting', j.get('skip') is True, j)
        rec('stayed case: nothing reached SMTP and no ledger row', not smtp_calls() and not ledger(),
            {'smtp': smtp_calls(), 'ledger': ledger()})
        rf = refusals()
        rec('stayed case: refusal logged to send_refusals.jsonl with case + reason, no address',
            len(rf) == 1 and rf[0].get('case') == '2099-000001-CA-01' and rf[0].get('code') == 'stay_active'
            and 'example.com' not in json.dumps(rf), rf)

        st, j = send('2099-000002-CA-01')
        rec('lifted stay: sends (200)', st == 200 and j.get('ok') is True, {'st': st, 'j': j})
        st, j = send('2099-000003-CA-01')
        rec('never-stayed case: sends (200)', st == 200 and j.get('ok') is True, {'st': st, 'j': j})
        rec('the two allowed sends reached SMTP and the ledger', len(smtp_calls()) == 2 and len(ledger()) == 2,
            {'smtp': smtp_calls(), 'ledger': len(ledger())})

        st, j = send('', drop_c=True)
        rec('no meta.c: 451 blocked=stay_no_case', st == 451 and j.get('blocked') == 'stay_no_case', {'st': st, 'j': j})
        st, j = send('   ')
        rec('blank meta.c: 451 blocked=stay_no_case', st == 451 and j.get('blocked') == 'stay_no_case', {'st': st, 'j': j})
        st, j = send('CACE-24-001234')
        rec('non-Miami-Dade number: 451 blocked=stay_case_unresolvable',
            st == 451 and j.get('blocked') == 'stay_case_unresolvable', {'st': st, 'j': j})
        st, j = send('2099-000999-CA-01')
        rec('never-read case: 451 blocked=stay_unverified', st == 451 and j.get('blocked') == 'stay_unverified',
            {'st': st, 'j': j})

        st, j = send('2099-000005-CA-01')
        rec('stem: -CA-01 board number vs suffix-less cache key -> refused',
            st == 451 and j.get('blocked') == 'stay_active' and j.get('matched') == ['2099-000005'], {'st': st, 'j': j})
        st, j = send('2099-000001')
        rec('stem: bare stem vs -CA-01 cache key -> refused', st == 451 and j.get('blocked') == 'stay_active',
            {'st': st, 'j': j})

        st, j = send('2099-000001-CA-01', test=True)
        rec('meta.test (advisor/operator 1:1, never an owner) is not stay-gated', st == 200, {'st': st, 'j': j})

        # stale board, fresh cache: the case turns stayed AFTER the bridge started
        c2 = dict(CACHE)
        c2['2099-000003-CA-01'] = {'a': True, 'bd': '2026-09-25', 'sl': '', 'b': 1, 'v': 5, 't': 1}
        time.sleep(0.02)
        cp.write_text(json.dumps(c2, indent=1), encoding='utf-8')
        st, j = send('2099-000003-CA-01')
        rec('a stay written after startup is enforced on the next send (no restart needed)',
            st == 451 and j.get('blocked') == 'stay_active', {'st': st, 'j': j})

        before = len(smtp_calls())
        cp.write_text('{"2099-000002-CA-01": ', encoding='utf-8')
        st, j = send('2099-000002-CA-01')
        rec('corrupt cache: 503 blocked=stay_data_unavailable', st == 503
            and j.get('blocked') == 'stay_data_unavailable', {'st': st, 'j': j})
        cp.unlink()
        st, j = send('2099-000002-CA-01')
        rec('missing cache: 503 blocked=stay_data_unavailable', st == 503
            and j.get('blocked') == 'stay_data_unavailable', {'st': st, 'j': j})
        st, h = call(port, '/health')
        rec('/health says stay_data.ok=false while the cache is missing',
            (h.get('stay_data') or {}).get('ok') is False, h.get('stay_data'))
        rec('no refused request reached SMTP', len(smtp_calls()) == before, smtp_calls())
        rec('every refusal is in send_refusals.jsonl', len(refusals()) == 10, len(refusals()))

        # stay_gate.py absent next to the bridge: refuse, never send ungated
        proc.terminate(); proc.wait(timeout=10); proc = None
        cp.write_text(json.dumps(CACHE), encoding='utf-8')
        (work / 'stay_gate.py').unlink()
        port2 = free_port()
        shim.write_text(shim.read_text(encoding='utf-8').replace('"%d"' % port, '"%d"' % port2), encoding='utf-8')
        proc = subprocess.Popen([sys.executable, str(shim)], cwd=str(work),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if call(port2, '/health')[0] == 200:
                break
            time.sleep(0.25)
        port = port2
        st, j = send('2099-000003-CA-01')
        rec('stay_gate.py missing: 503 refused, not an ungated send', st == 503
            and j.get('blocked') == 'stay_data_unavailable', {'st': st, 'j': j})
    finally:
        if proc is not None:
            try:
                proc.terminate(); proc.wait(timeout=10)
            except Exception:
                pass
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    unit()
    parity()
    server()
    total = len(ok) + len(bad)
    print(f'\n==== {len(ok)}/{total} send-bridge stay checks passed ====')
    if bad:
        print('FAILED:', bad)
    raise SystemExit(0 if not bad else 1)
