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
DAILY_LIMIT = 8        # the worker's default batch: a few minutes a lead when the rows are read off the page

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
    c = {'pending': 0, 'reading': 0, 'token': 0, 'rows': 0, 'done': 0, 'failed': 0, 'miss': 0}
    for it in (q or {}).get('items', []):
        c[it.get('status', 'pending')] = c.get(it.get('status', 'pending'), 0) + 1
    c['total'] = len((q or {}).get('items', []))
    c['entity'] = sum(1 for it in (q or {}).get('items', []) if it.get('kind') == 'entity')
    c['pending'] -= sum(1 for it in (q or {}).get('items', [])
                        if it.get('kind') == 'entity' and it.get('status', 'pending') == 'pending')
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
                    fh.write('  owner is a bank/LLC/association, owner not resolved yet: %d\n' % c['entity'])
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
def add(case, owner_clean, last='', first='', folio='', county='MIAMI-DADE', reason='', kind='person'):
    """Called by records_liens.py for a lead neither free path could read. Never raises.

    kind is 'person' or 'entity'. An entity row is a lead whose owner field holds a bank, an LLC or
    an association (on a fresh filing that is usually the PLAINTIFF, not the homeowner). A name
    search on it cannot find the homeowner's mortgages, so the browser job never takes those:
    they are listed apart as "owner not resolved yet" for the owner resolver."""
    try:
        # 2099-... is this repo's invented-case convention: never queued outside a scratch DEALFLOW_DIR
        if case and not (str(case).startswith('2099-') and not os.environ.get('DEALFLOW_DIR')):
            _PENDING.append({'case': str(case), 'owner': str(owner_clean or ''), 'last': str(last or ''),
                             'first': str(first or ''), 'folio': str(folio or ''), 'county': county,
                             'reason': str(reason or '')[:160], 'kind': 'entity' if kind == 'entity' else 'person',
                             'status': 'pending'})
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
            for it in q['items'] if it.get('status') == 'pending' and it.get('kind') != 'entity'
            and _norm_folio(it.get('folio'))][:limit]
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


# ---- browser hand-off: Claude reads the search results off the page ------------------------------
# 2026-11-13 (Alejandro chose "Claude reads the results off the page"). Measured the same day:
#   * the browser tool will not return the county's search token (blocked), and returns only about
#     a thousand characters per call;
#   * the county page may not post to this computer (its Content-Security-Policy is default-src self);
#   * a scripted Chrome window gets no token at all.
# So the worker runs the search by hand in the real Chrome and runs the script `js` prints. That
# script reads the results the page already loaded and keeps the rows tied to this parcel: this
# folio; no folio but the parcel's subdivision; a satisfaction, assignment or release whose original
# book/page is one of those; and the owner's liens, judgments and tax liens. That is narrower than
# the nightly search (a name returns about a hundred rows and most carry no folio), so a chain
# read this way is saved as TRUNCATED: shown, never a clear and never a priced fact. It trims the
# rows to the twelve fields the parser reads and returns them a few at a time with a checksum. `rows` refuses a chunk whose
# checksum does not match, so a mis-copied digit cannot become a mortgage balance. `finish` then
# runs the scraper's own analyze() on the stored rows (records_liens.py --models-file).
FIELDS = ('doC_TYPE', 'foliO_NUMBER', 'seconD_PARTY', 'firsT_PARTY', 'reC_PAGE', 'reC_BOOK', 'reC_DATE',
          'cfN_MASTER_ID', 'oriG_REC_PAGE', 'oriG_REC_BOOK', 'subdiV_NAME', 'reC_BOOKPAGE')
CHUNK = 5              # rows per call: the browser tool cuts long answers
MAX_ROWS = 60          # more than this after scoping to the parcel is not a job for hand-copying
MOD = 1000000007

JS = r"""var out; { const FOLIO = %(folio)s, K = %(chunk)d, F = %(fields)s, N = %(size)d, MAXR = %(maxr)d, MOD = %(mod)d;
const q = new URLSearchParams(location.search).get('qs');
if(!q){ out = JSON.stringify({err: 'no search results on this page: run the name search first'}); }
else {
  if(!window.__eqRows){
    const j = await fetch('/officialrecords/api/SearchResults/getStandardRecords?qs=' + encodeURIComponent(q)).then(r => r.json());
    const nf = v => String(v == null ? '' : v).replace(/\D/g, '').replace(/^0+/, '');
    const all = j.recordingModels || [];
    window.__eqAll = all.length;
    window.__eqOnParcel = all.filter(r => nf(r.foliO_NUMBER) === FOLIO).length;
    const sub = r => String(r.subdiV_NAME || '').trim(), bp = (b, p) => (Number(b) || 0) + '/' + (Number(p) || 0);
    const on = all.filter(r => nf(r.foliO_NUMBER) === FOLIO), subs = {}; on.forEach(r => { if(sub(r)) subs[sub(r)] = 1; });
    const blank = all.filter(r => !nf(r.foliO_NUMBER));
    const keep = new Set(on); blank.forEach(r => { if(sub(r) && subs[sub(r)]) keep.add(r); });
    for(let pass = 0; pass < 2; pass++){ const have = {}; keep.forEach(r => { have[bp(r.reC_BOOK, r.reC_PAGE)] = 1; });
      blank.forEach(r => { if(Number(r.oriG_REC_BOOK) && have[bp(r.oriG_REC_BOOK, r.oriG_REC_PAGE)]) keep.add(r); }); }
    blank.forEach(r => { if(!sub(r) && /LIEN|JUDG|LIS PENDENS|TAX/i.test(String(r.doC_TYPE || ''))) keep.add(r); });
    window.__eqRows = all.filter(r => keep.has(r))
      .map(r => F.map(k => { const v = r[k]; return (typeof v === 'string') ? v.slice(0, 60) : (v == null ? null : v); }));
  }
  const rows = window.__eqRows, of = Math.ceil(rows.length / N);
  if(rows.length > MAXR){ out = JSON.stringify({err: 'too many rows', scoped: rows.length, all: window.__eqAll}); }
  else {
    const part = rows.slice(K * N, K * N + N);
    let s = 0; part.forEach(r => { const c = r.map(v => v == null ? '' : String(v)).join('|'); for(let i = 0; i < c.length; i++) s = (s * 31 + c.charCodeAt(i)) %% MOD; });
    out = JSON.stringify({k: K, of: of, all: window.__eqAll, onParcel: window.__eqOnParcel, scoped: rows.length, sum: s, rows: part});
  }
} }
out"""


def _norm_folio(s):
    return ''.join(ch for ch in str(s or '') if ch.isdigit()).lstrip('0')


def rows_path(case):
    d = os.path.join(qdir(), 'rows')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, ''.join(ch for ch in case if ch.isalnum() or ch == '-') + '.json')


def checksum(rows):
    s = 0
    for r in rows:
        c = '|'.join('' if v is None else (str(int(v)) if isinstance(v, float) and v == int(v) else str(v)) for v in r)
        for ch in c:
            s = (s * 31 + ord(ch)) % MOD
    return s


def js_for(case, chunk=0):
    q = _read(qpath(), {'items': []})
    it = next((x for x in q['items'] if x['case'] == case), None)
    if not it:
        return None
    return JS % {'folio': json.dumps(_norm_folio(it.get('folio'))), 'chunk': int(chunk),
                 'fields': json.dumps(list(FIELDS)), 'size': CHUNK, 'maxr': MAX_ROWS, 'mod': MOD}


def accept_chunk(case, payload):
    """Store one chunk the page returned. -> (ok, message). Chunks arrive in order, 0 first."""
    q = _read(qpath(), {'items': []})
    it = next((x for x in q['items'] if x['case'] == case), None)
    if not it or it.get('kind') == 'entity' or it.get('status') not in ('pending', 'reading'):
        return False, 'case is not waiting on today\'s list'
    if not _norm_folio(it.get('folio')):
        return False, 'this lead has no folio, so its rows cannot be tied to the parcel'
    try:
        k, of, rows = int(payload['k']), int(payload['of']), payload['rows']
        want = int(payload['sum'])
    except Exception:
        return False, 'chunk is missing k / of / sum / rows'
    if not isinstance(rows, list) or len(rows) > CHUNK or any(
            not isinstance(r, list) or len(r) != len(FIELDS) for r in rows):
        return False, 'rows are not %d-field lists of at most %d' % (len(FIELDS), CHUNK)
    if checksum(rows) != want:
        return False, 'checksum mismatch: the rows were not copied exactly. Run the script for chunk %d again.' % k
    path = rows_path(case)
    cur = _read(path, None) if k else None
    have = (cur or {}).get('chunks', 0)
    if k != have:
        return False, 'expected chunk %d next, got %d' % (have, k)
    models = (cur or {}).get('recordingModels', []) + [dict(zip(FIELDS, r)) for r in rows]
    if len(models) > MAX_ROWS:
        return False, 'more than %d rows' % MAX_ROWS
    done = (k + 1) >= max(1, of)
    _write(path, {'case': case, 'chunks': k + 1, 'of': of, 'complete': done,
                  'on_parcel': int(payload.get('onParcel') or 0), 'recordingModels': models})
    _set(case, status='rows' if done else 'reading', rows=len(models))
    return True, ('all %d row(s) stored' % len(models)) if done else ('chunk %d of %d stored; run chunk %d next' % (k + 1, of, k + 1))


def cmd_finish():
    q = _read(qpath(), None)
    if not q:
        print('nothing queued today')
        clean()
        return 0
    for it in q['items']:
        if it.get('status') == 'reading':            # a half-copied lead is not read: it goes back to waiting
            it['status'] = 'pending'
    _write(qpath(), q)
    todo = [it for it in q['items'] if it.get('status') in ('token', 'rows')]
    for it in todo:
        argv = [sys.executable, '-u', 'records_liens.py', '--case', it['case'], '--cached-only']
        if it.get('status') == 'rows':
            argv += ['--models-file', rows_path(it['case'])]
        p = subprocess.run(argv,
                           cwd=HERE, capture_output=True, text=True, timeout=600,
                           encoding='utf-8', errors='replace')
        # the scraper prints "  ok  <case>" only after it wrote the chain to records_liens.json
        ok = p.returncode == 0 and ('  ok  ' + it['case']) in (p.stdout or '')
        _set(it['case'], status='done' if ok else 'failed',
             finished_at=dt.datetime.now().isoformat(timespec='seconds'))
        time.sleep(0.5)
    q = _read(qpath(), q)
    import shutil
    shutil.rmtree(os.path.join(qdir(), 'rows'), ignore_errors=True)      # raw rows are not kept once read
    removed = clean()
    c = _counts(q)
    _log('worker_finish', old_files_removed=removed, **c)
    _desktop_note(q)
    print('finish: %(done)d read, %(failed)d token did not read, %(miss)d browser miss, %(pending)d still waiting' % c)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['status', 'next', 'js', 'rows', 'token', 'miss', 'finish', 'clean'])
    ap.add_argument('--chunk', type=int, default=0)
    ap.add_argument('--file', default='')
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
    if a.cmd == 'js':
        s = js_for(a.case, a.chunk)
        print(s if s else 'refused: unknown case for today')
        return 0 if s else 1
    if a.cmd == 'rows':
        try:
            payload = json.load(open(a.file, encoding='utf-8'))
        except Exception as e:
            print('refused: could not read --file as JSON (%s)' % str(e)[:60])
            return 1
        okk, msg = True, 'no chunks in the file'
        for ch in (payload if isinstance(payload, list) else [payload]):     # one chunk, or the lead's chunks in order
            okk, msg = accept_chunk(a.case, ch)
            if not okk:
                break
        try:
            os.remove(a.file)                                              # the hand-off file is not kept
        except OSError:
            pass
        print(('ok: ' if okk else 'refused: ') + msg)
        return 0 if okk else 1
    print('removed %d old file(s)' % clean())
    return 0


if __name__ == '__main__':
    sys.exit(main())
