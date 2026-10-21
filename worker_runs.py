"""Print the Morning Worker flight recorder (worker_runs.jsonl, written by send_server /runlog).

    python worker_runs.py            today's events
    python worker_runs.py 2026-10-21 one day
    python worker_runs.py all        every day, one summary line each

Counts and codes only, by design: no case, name or address is ever recorded. Read-only."""
import collections
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path=None):
    path = path or os.path.join(HERE, 'worker_runs.jsonl')
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding='utf-8', errors='replace'):
        try:
            rows.append(json.loads(line))
        except Exception:
            pass                       # a torn line is noise
    return rows


def day_of(r):
    return str(r.get('ts_local') or '')[:10]


def main(argv):
    rows = load()
    want = argv[1] if len(argv) > 1 else dt.date.today().isoformat()
    if want == 'all':
        by = collections.defaultdict(collections.Counter)
        for r in rows:
            by[day_of(r)][r.get('ev')] += 1
        for d in sorted(by):
            print(d, dict(by[d]))
        return 0
    sel = [r for r in rows if day_of(r) == want]
    if not sel:
        print('no worker events on %s (%d total rows). The recorder only exists on a bridge running '
              'this code, and the board must be rebuilt and loaded for the board-side events.' % (want, len(rows)))
        return 0
    for r in sel:
        print(str(r.get('ts_local'))[11:19], r.get('ev'), json.dumps(r.get('x') or {}, ensure_ascii=False))
    ans = collections.Counter((r['x'].get('status'), r['x'].get('blocked') or ('ok' if r['x'].get('ok') else '')) for r in sel
                              if r.get('ev') == 'send_answer')
    if ans:
        print('send answers (status, blocked-or-ok):', dict(ans))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
