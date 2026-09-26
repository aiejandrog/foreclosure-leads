"""sync_gate.py - HOLD every send until TODAY's 07:15 opt-out sync has finished successfully.

THE DECISION (Alejandro, 2026-09-26). The suppression list (optouts.json) has exactly one writer:
Foreclosure Bot's opt-out sync pipeline - the inbox scan (replies.py) feeding optout_sync.py, whose
`ledger_add` is the single write path (#70), then ledger_sync.py's add-only union with the other
machine. Nothing else writes it; _ledgerownertest.py enforces that in code. The sync runs at 07:15
(morning_sync.py via run-optout-sync.bat), before the 08:00 Morning Worker.

THE RULE THIS FILE ENFORCES. If that sync has not completed successfully TODAY by the time anything
wants to send, EVERY send is held - fail closed, logged, and visible in the bridge's /health - rather
than mailing against yesterday's list. A STOP that arrived overnight is only in the ledger once this
morning's sync has run; before that, a send can reach someone who already said stop.

Held when, and the reason says which:
  * there is no sync_status.json at all (the sync has never run on this machine)
  * it cannot be read or parsed
  * the last run was not today (local date on this machine)
  * today's run started and has not finished (still running, or killed mid-way)
  * today's run finished with a failed step (the reason names the step and its exit code)
  * the recorded finish time is in the future (a clock problem; nothing about it can be trusted)

What it does NOT do: decide who is opted out. It never reads or writes optouts.json. It only answers
"is the list known to be current this morning". The per-recipient opt-out match, the 2-day ledger
staleness guard and the bankruptcy-stay gate in send_server.py all still run after it.

    python sync_gate.py          # prints the verdict; exit 0 = sends may proceed, 3 = HOLD
    python sync_gate.py --json
"""
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS = os.path.join(HERE, 'sync_status.json')
HOLD_RC = 3
REMEDY = 'run run-optout-sync.bat (or: python morning_sync.py) on this machine, then resume sends'

_last_logged = {'reason': None}


def _hhmm(ts):
    try:
        return time.strftime('%H:%M', time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return '?'


def verdict(now=None, path=None):
    """{'ok': bool, 'reason': str, 'status': dict|None}. Never raises: any doubt is a HOLD."""
    now = time.time() if now is None else float(now)
    path = path or STATUS
    today = dt.datetime.fromtimestamp(now).date().isoformat()
    try:
        with open(path, encoding='utf-8') as f:
            st = json.load(f)
        if not isinstance(st, dict):
            raise ValueError('not a JSON object')
    except FileNotFoundError:
        return {'ok': False, 'status': None,
                'reason': "HOLD - no opt-out sync has ever been recorded on this machine (no sync_status.json), "
                          "so the do-not-contact list cannot be shown to be current. " + REMEDY}
    except Exception as e:
        return {'ok': False, 'status': None,
                'reason': 'HOLD - sync_status.json cannot be read (%s). %s' % (str(e)[:80], REMEDY)}
    day = str(st.get('date') or '')
    if day != today:
        return {'ok': False, 'status': st,
                'reason': "HOLD - today's 07:15 opt-out sync has not run (last run: %s). Sending against "
                          "yesterday's list could reach someone who said stop overnight. %s" % (day or 'unknown', REMEDY)}
    fin = st.get('finished_at')
    if st.get('state') != 'finished' or fin is None:
        return {'ok': False, 'status': st,
                'reason': "HOLD - today's opt-out sync started at %s and has not finished (still running, or it "
                          "was killed). %s" % (_hhmm(st.get('started_at')), REMEDY)}
    try:
        fin = float(fin)
    except (TypeError, ValueError):
        return {'ok': False, 'status': st, 'reason': 'HOLD - sync_status.json has an unreadable finish time. ' + REMEDY}
    if fin > now + 300:
        return {'ok': False, 'status': st,
                'reason': 'HOLD - the recorded sync finish time is in the future (clock problem). ' + REMEDY}
    if st.get('ok') is not True:
        bad = [s for s in (st.get('steps') or []) if isinstance(s, dict) and not s.get('ok')]
        what = '; '.join('%s rc=%s%s' % (s.get('name', '?'), s.get('rc', '?'),
                                         (' (' + str(s.get('why'))[:100] + ')') if s.get('why') else '')
                         for s in bad) or 'no step detail'
        return {'ok': False, 'status': st,
                'reason': "HOLD - today's opt-out sync FAILED at %s: %s. %s" % (_hhmm(fin), what, REMEDY)}
    return {'ok': True, 'status': st,
            'reason': "today's opt-out sync finished OK at %s - sends may proceed" % _hhmm(fin)}


def health(now=None, path=None):
    """The compact block send_server's /health publishes."""
    v = verdict(now, path)
    st = v.get('status') or {}
    return {'sync_ok': v['ok'], 'sync_hold': None if v['ok'] else v['reason'],
            'sync_date': st.get('date'), 'sync_finished_at': st.get('finished_at'), 'sync_reason': v['reason']}


def log_hold(v, log=print):
    """One log line per distinct hold reason per process, so a paused worker does not flood the log."""
    if v['ok']:
        _last_logged['reason'] = None
        return False
    if _last_logged['reason'] == v['reason']:
        return False
    _last_logged['reason'] = v['reason']
    log('SENDS HELD - ' + v['reason'])
    return True


def main(argv):
    v = verdict()
    if '--json' in argv:
        print(json.dumps(v, indent=1, default=str))
    else:
        print(('[sync-gate] OK - ' if v['ok'] else '[sync-gate] ') + v['reason'])
    return 0 if v['ok'] else HOLD_RC


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
