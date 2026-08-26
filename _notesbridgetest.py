#!/usr/bin/env python
"""_notesbridgetest.py -- proves the localStorage->disk notes bridge actually works.

WHY THIS EXISTS: until 2026-08-02 every call disposition, status and touch the operator ever
logged lived ONLY in one Chrome profile's localStorage. The bridge (send_server.py POST /notes
+ the tracker's notesBridgePush) is the fix. This test spawns the real server on a spare port,
POSTs a synthetic payload shaped exactly like the tracker's, and verifies the atomic write of
worker_notes.json + the daily snapshot. Synthetic artifacts are removed afterwards so a test
run can never masquerade as real call history.

Run:  python _notesbridgetest.py     (exit 0 = pass, 1 = fail)
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8829  # spare port so a live bridge on 8823 is untouched
NOTES = os.path.join(HERE, 'worker_notes.json')
SNAP = os.path.join(HERE, 'worker_notes_snapshots',
                    'worker_notes_%s.json' % dt.date.today().isoformat())
MARKER = '_notesbridgetest-synthetic'

PAYLOAD = {
    '_dealflow_notes': 1,
    'exported': dt.date.today().isoformat(),
    'device': MARKER,
    'notes': {
        'TEST-000-CA-01': {
            'status': 'Called - talked',
            'touches': [{'d': dt.date.today().isoformat(), 'ch': 'call', 'out': 'talked'}],
            'note': 'synthetic row written by _notesbridgetest.py',
        }
    },
    'workerLog': [{'t': 0, 'a': 'call', 'c': 'TEST-000-CA-01', 'ok': True}],
    'sentArchive': [],
}


def _post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode('utf-8'))


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode('utf-8'))


def main():
    fails = []
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, 'send_server.py'),
                            '--port', str(PORT)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HERE)
    try:
        # wait for the server to come up
        for _ in range(30):
            try:
                _get(f'http://127.0.0.1:{PORT}/health')
                break
            except Exception:
                time.sleep(0.3)
        else:
            print('FAIL: server never answered /health')
            return 1

        # preserve any REAL notes file so the test cannot destroy actual history
        real_backup = None
        if os.path.exists(NOTES):
            real_backup = open(NOTES, encoding='utf-8').read()

        # RICHEST-WINS GUARD (added to the server 2026-08-03, after this suite was written).
        # A bare synthetic payload is POORER than the real backup, so the server correctly REFUSES
        # it — this suite then failed on its own round-trip assertion for weeks while the product
        # was working exactly as designed. Push the real state PLUS the synthetic row: legitimately
        # richer, so it wins, and the round-trip assertion tests what it was written to test.
        # (The real file is restored in cleanup below, as it always was.)
        # Start from the WHOLE real payload, not hand-picked keys: _richness() also counts
        # sentArchive, and cherry-picking notes+workerLog produced a push with MORE notes but
        # LOWER richness (1229 notes / 5185 vs 5482) — refused, correctly. Copy everything, then
        # overlay the synthetic row and marker device.
        _pay = dict(PAYLOAD)
        if real_backup:
            try:
                _cur = json.loads(real_backup)
                _pay = dict(_cur)
                _merged = dict(_cur.get('notes') or {})
                _merged.update(PAYLOAD['notes'])
                _pay['notes'] = _merged
                _pay['_dealflow_notes'] = True
                _pay['device'] = MARKER
            except Exception:
                pass
        r = _post(f'http://127.0.0.1:{PORT}/notes', _pay)
        if r.get('saved') is False:
            fails.append(f'a legitimately richer push was refused: {r}')
        if not r.get('ok'):
            fails.append(f'/notes returned not-ok: {r}')
        if not os.path.exists(NOTES):
            fails.append('worker_notes.json was not written')
        else:
            back = json.load(open(NOTES, encoding='utf-8'))
            if back.get('device') != MARKER:
                fails.append('worker_notes.json content did not round-trip')
            if 'TEST-000-CA-01' not in (back.get('notes') or {}):
                fails.append('notes payload missing from worker_notes.json')
        if not os.path.exists(SNAP):
            fails.append('daily snapshot was not written')

        # A POORER push must be REFUSED — and must SAY SO. Until 2026-08-26 the server answered
        # 200/saved:true for these, so 17 real device pushes were silently dropped this month (one
        # holding 496 notes) while each device believed it had backed up. Refusing is right;
        # reporting it as a save is not.
        poor = _post(f'http://127.0.0.1:{PORT}/notes',
                     {'_dealflow_notes': True, 'device': MARKER + '-poor',
                      'notes': {'ONLY-1': {'status': 'x'}}, 'workerLog': []})
        if poor.get('saved') is not False:
            fails.append(f'a poorer push was not refused (clobber guard): {poor}')
        if not poor.get('refused'):
            fails.append(f'a refused push did not explain itself: {poor}')
        after_poor = json.load(open(NOTES, encoding='utf-8'))
        if 'ONLY-1' in (after_poor.get('notes') or {}):
            fails.append('the poorer push OVERWROTE the backup')

        # junk must be rejected
        try:
            r2 = _post(f'http://127.0.0.1:{PORT}/notes', {'hello': 'world'})
            if r2.get('ok'):
                fails.append('junk payload without _dealflow_notes flag was accepted')
        except urllib.error.HTTPError:
            pass  # 400 is the right answer

        # cleanup: remove synthetic artifacts / restore real file
        try:
            if real_backup is not None:
                with open(NOTES, 'w', encoding='utf-8') as f:
                    f.write(real_backup)
            elif os.path.exists(NOTES):
                cur = json.load(open(NOTES, encoding='utf-8'))
                if cur.get('device') == MARKER:
                    os.remove(NOTES)
            if os.path.exists(SNAP):
                snap = json.load(open(SNAP, encoding='utf-8'))
                if snap.get('device') == MARKER:
                    os.remove(SNAP)
        except Exception as e:
            fails.append(f'cleanup problem (check files by hand): {e}')
    finally:
        srv.terminate()

    if fails:
        print('FAIL (%d):' % len(fails))
        for f in fails:
            print('  -', f)
        return 1
    print('OK: POST /notes writes worker_notes.json + daily snapshot atomically; junk rejected.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
