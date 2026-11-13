#!/usr/bin/env python3
"""equity_queue.py - the leads the nightly lien scraper could not read for free, one file a day.

WHY (2026-11-13, Alejandro). records_liens.py reads recorded mortgages through a real browser
(Camoufox) for free. When that misses, the paid captcha fallback is refused by the $0 rule and the
lead used to scroll past in leads-run.log as "not pulled: spend cap" and nothing else. His words:
"you need to log it, separate it and keep everything organized ... everything has to be logged".

WHAT THIS IS. A small queue with one owner, the scraper, and one worker, a scheduled Claude session
that drives the real Chrome:

    ~/DEALFLOW/equity-queue/
        queue-YYYY-MM-DD.json   today's misses: case, owner name parts, folio, status per lead
        tried.json              case -> last browser attempt and outcome (stops daily re-queues)
        log.jsonl               one counts-only line per scraper flush and per worker finish
    Desktop/DealFlow-equity-queue.txt   counts only, no names: what is waiting, what got done

The worker does not read mortgages itself. It runs the county's own name search in Chrome, copies
the search token (`qs`) the county issues, and hands it back with `token`. `finish` then runs
`records_liens.py --cached-only`, the free plain-HTTP path, so every existing parser, the parcel
check and the equity math are the ones the nightly run uses. Nothing here spends money.

SELF-CLEANING. Queue files older than KEEP_DAYS are deleted on every flush and every finish,
log.jsonl is trimmed to its last LOG_LINES lines, tried.json drops entries older than RETRY_DAYS.

HOMEOWNER DATA stays under DEALFLOW_DIR. The Desktop file and log.jsonl are counts only.

    python equity_queue.py status            counts for today
    python equity_queue.py next [--limit N]  JSON list of pending leads for the worker
    python equity_queue.py token --case C --qs "<qs>"
    python equity_queue.py miss  --case C --why "no results for that name"
    python equity_queue.py finish            read the tokens in, mark done/failed, clean up
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time

import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_DAYS = 7          # queue files
RETRY_DAYS = 14        # a lead the browser also missed is not queued again for this long
LOG_LINES = 400
DAILY_LIMIT = 25       # the worker's default batch: about a minute a lead in a real browser

_PENDING = []          # filled by add() during one records_liens run, written by flush()


def qdir():
    d = os.path.join(P.DEALFLOW_DIR, 'equity-queue')
    os.makedirs(d, exist_ok=True)
    return d


def _today():
    return dt.date.today().isoformat()


def qpath(day=None):
    return os.path.join(qdir(), 'queue-%s.json' % (day or _today()))


def _read(path, default):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return default


def _write(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, path)


def _log(event, **counts):
    path = os.path.join(qdir(), 'log.jsonl')
    row = dict(ts=dt.datetime.now().isoformat(timespec='seconds'), event=event, **counts)
    lines = []
    try:
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
    except OSError:
        pass
    lines.append(json.dumps(row))
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines[-LOG_LINES:]) + '\n')


def _counts(q):
    c = {'pending': 0, 'token': 0, 'done': 0, 'failed': 0, 'miss': 0}
    for it in (q or {}).get('items', []):
        c[it.get('status', 'pending')] = c.get(it.get('status', 'pending'), 0) + 1
    c['total'] = len((q or {}).get('items', []))
    return c


def _desktop_note(q):
    """Counts only. Never a name, a case number or an address: the Desktop syncs to OneDrive."""
    for d in (os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop'),
              os.path.join(os.path.expanduser('~'), 'Desktop')):
        if os.path.isdir(d):
            c = _counts(q)
            try:
                with open(os.path.join(d, 'DealFlow-equity-queue.txt'), 'w', encoding='utf-8') as fh:
                    fh.write('DealFlow equity queue - %s\n' % (q or {}).get('date', _today()))
                    fh.write('updated %s\n\n' % dt.datetime.now().strftime('%Y-%m-%d %H:%M'))
                    fh.write('Leads the free scraper could not read: %d\n' % c['total'])
                    fh.write('  waiting for the browser job : %d\n' % c['pending'])
                    fh.write('  token captured, not read yet: %d\n' % c['token'])
                    fh.write('  equity read and saved       : %d\n' % c['done'])
                    fh.write('  browser found nothing       : %d\n' % c['miss'])
                    fh.write('  token did not read          : %d\n' % c['failed'])
                    fh.write('\nDetails (names, cases): %s\n' % qpath((q or {}).get('date')))
            except OSError:
                pass
            return


def clean():
    """Delete old queue files, trim tried.json. Safe to call any time."""
    cutoff = dt.date.today() - dt.timedelta(days=KEEP_DAYS)
    removed = 0
    for fn in os.listdir(qdir()):
        if fn.startswith('queue-') and fn.endswith('.json'):
            try:
                if dt.date.fromisoformat(fn[6:16]) < cutoff:
                    os.remove(os.path.join(qdir(), fn))
                    removed += 1
            except (ValueError, OSError):
                pass
        elif fn.endswith('.tmp'):
            try:
                os.remove(os.path.join(qdir(), fn))
            except OSError:
                pass
    tp = os.path.join(qdir(), 'tried.json')
    tried = _read(tp, {})
    keep_from = (dt.date.today() - dt.timedelta(days=RETRY_DAYS)).isoformat()
    kept = {k: v for k, v in tried.items() if str((v or {}).get('d', '')) >= keep_from}
    if len(kept) != len(tried):
        _write(tp, kept)
    return removed


# ---- scraper side -----------------------------------------------------------------------------
def add(case, owner_clean, last='', first='', folio='', county='MIAMI-DADE', reason=''):
    """Called by records_liens.py for a lead neither free path could read. Never raises."""
    try:
        if case:
            _PENDING.append({'case': str(case), 'owner': str(owner_clean or ''), 'last': str(last or ''),
                             'first': str(first or ''), 'folio': str(folio or ''), 'county': county,
                             'reason': str(reason or '')[:160], 'status': 'pending'})
    except Exception:
        pass


def flush():
    """Write today's file from what add() collected. Merges with a file already there (a second
    run the same day), keeps finished statuses, skips leads the browser missed recently."""
    try:
        if not _PENDING:
            clean()
            return 0
        tried = _read(os.path.join(qdir(), 'tried.json'), {})
        q = _read(qpath(), None) or {'date': _today(), 'source': 'records_liens.py', 'items': []}
        have = {it['case']: it for it in q['items']}
        added = skipped = 0
        for it in _PENDING:
            if it['case'] in have:
                continue
            if (tried.get(it['case']) or {}).get('out') == 'miss':
                skipped += 1
                continue
            q['items'].append(it)
            have[it['case']] = it
            added += 1
        q['built_at'] = dt.datetime.now().isoformat(timespec='seconds')
        _write(qpath(), q)
        del _PENDING[:]
        clean()
        _log('scraper_flush', added=added, skipped_recent_miss=skipped, **_counts(q))
        _desktop_note(q)
        return added
    except Exception as e:                      # the queue must never fail the lien run
        print('  equity queue: could not write (%s)' % str(e)[:100])
        return 0


# ---- worker side ------------------------------------------------------------------------------
def _set(case, **kw):
    q = _read(qpath(), None)
    if not q:
        return None
    for it in q['items']:
        if it['case'] == case:
            it.update(kw)
            _write(qpath(), q)
            return it
    return None


def cmd_next(limit):
    q = _read(qpath(), {'items': []})
    rows = [{'case': it['case'], 'last': it['last'], 'first': it['first'], 'owner': it['owner']}
            for it in q['items'] if it.get('status') == 'pending'][:limit]
    print(json.dumps(rows, indent=1))


def cmd_token(case, qs):
    qs = str(qs or '').strip()
    it = _read(qpath(), {'items': []})
    it = next((x for x in it['items'] if x['case'] == case), None)
    if not it or len(qs) < 20:
        print('refused: unknown case for today or an empty token')
        return 1
    cache = _read(P.records_qs(), {})
    cache[it['owner']] = qs
    _write(P.records_qs(), cache)
    _set(case, status='token', token_at=dt.datetime.now().isoformat(timespec='seconds'))
    print('ok: token stored')
    return 0


def cmd_miss(case, why):
    if not _set(case, status='miss', why=str(why or '')[:160]):
        print('refused: unknown case for today')
        return 1
    tp = os.path.join(qdir(), 'tried.json')
    tried = _read(tp, {})
    tried[case] = {'d': _today(), 'out': 'miss'}
    _write(tp, tried)
    print('ok: marked miss')
    return 0


def cmd_finish():
    q = _read(qpath(), None)
    if not q:
        print('nothing queued today')
        clean()
        return 0
    todo = [it for it in q['items'] if it.get('status') == 'token']
    for it in todo:
        p = subprocess.run([sys.executable, '-u', 'records_liens.py', '--case', it['case'], '--cached-only'],
                           cwd=HERE, capture_output=True, text=True, timeout=600,
                           encoding='utf-8', errors='replace')
        # the scraper prints "  ok  <case>" only after it wrote the chain to records_liens.json
        ok = p.returncode == 0 and ('  ok  ' + it['case']) in (p.stdout or '')
        _set(it['case'], status='done' if ok else 'failed',
             finished_at=dt.datetime.now().isoformat(timespec='seconds'))
        time.sleep(0.5)
    q = _read(qpath(), q)
    removed = clean()
    c = _counts(q)
    _log('worker_finish', old_files_removed=removed, **c)
    _desktop_note(q)
    print('finish: %(done)d read, %(failed)d token did not read, %(miss)d browser miss, %(pending)d still waiting' % c)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['status', 'next', 'token', 'miss', 'finish', 'clean'])
    ap.add_argument('--case', default='')
    ap.add_argument('--qs', default='')
    ap.add_argument('--why', default='')
    ap.add_argument('--limit', type=int, default=DAILY_LIMIT)
    a = ap.parse_args()
    if a.cmd == 'status':
        q = _read(qpath(), {'date': _today(), 'items': []})
        print(json.dumps(dict(date=q.get('date'), **_counts(q))))
        return 0
    if a.cmd == 'next':
        return cmd_next(a.limit) or 0
    if a.cmd == 'token':
        return cmd_token(a.case, a.qs)
    if a.cmd == 'miss':
        return cmd_miss(a.case, a.why)
    if a.cmd == 'finish':
        return cmd_finish()
    print('removed %d old file(s)' % clean())
    return 0


if __name__ == '__main__':
    sys.exit(main())
