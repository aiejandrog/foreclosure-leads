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
DAILY_LIMIT = 10       # the worker's default batch: several minutes a lead when the whole search is copied

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
    c = {'pending': 0, 'token': 0, 'rows': 0, 'done': 0, 'failed': 0, 'miss': 0}
    for it in (q or {}).get('items', []):
        c[it.get('status', 'pending')] = c.get(it.get('status', 'pending'), 0) + 1
    c['total'] = len((q or {}).get('items', []))
    c['entity'] = sum(1 for it in (q or {}).get('items', []) if it.get('kind') == 'entity')
    c['verified'] = sum(1 for it in (q or {}).get('items', []) if it.get('status') == 'done' and it.get('full'))
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
                    fh.write('  of those, owner field is a bank/LLC/association (searched by the people named in the case): %d\n' % c['entity'])
                    fh.write('  waiting for the browser job : %d\n' % c['pending'])
                    fh.write('  token captured, not read yet: %d\n' % c['token'])
                    fh.write('  read and saved              : %d  (whole search copied, counts as verified: %d)\n' % (c['done'], c['verified']))
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
def add(case, owner_clean, last='', first='', folio='', county='MIAMI-DADE', reason='', kind='person',
        names=None, address=''):
    """Called by records_liens.py for a lead it could not read. Never raises.

    names = every (LAST, FIRST) worth searching, in order: the owner when it is a person, then the
    people the case names as defendants. 2026-11-13 (Alejandro): "DON'T LET IT SKIP BANK OR
    ASSOCIATIONS". When the owner field holds a bank or an association (on a fresh filing that is
    the plaintiff), the homeowner is among the defendants, so those are the names to search. kind is
    kept as a label only; nothing is skipped on it."""
    try:
        # 2099-... is this repo's invented-case convention: never queued outside a scratch DEALFLOW_DIR
        if case and not (str(case).startswith('2099-') and not os.environ.get('DEALFLOW_DIR')):
            nm = []
            for x in (names or []):
                try:
                    pair = [str(x[0] or '').strip(), str(x[1] or '').strip()]
                except Exception:
                    continue
                if pair[0] and pair not in nm:
                    nm.append(pair)
            if not nm and (last or owner_clean):
                nm = [[str(last or owner_clean).strip(), str(first or '').strip()]]
            _PENDING.append({'case': str(case), 'owner': str(owner_clean or ''), 'last': str(last or ''),
                             'first': str(first or ''), 'folio': str(folio or ''), 'county': county,
                             'reason': str(reason or '')[:160], 'kind': 'entity' if kind == 'entity' else 'person',
                             'names': nm[:6], 'address': str(address or '')[:160], 'status': 'pending'})
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


def _names(it):
    return it.get('names') or [[it.get('last') or it.get('owner') or '', it.get('first') or '']]


def cmd_next(limit):
    """Every pending lead, nothing skipped. A lead with no folio carries its address so the worker
    can find the folio on the Property Appraiser site first."""
    q = _read(qpath(), {'items': []})
    rows = []
    for it in q['items']:
        if it.get('status') != 'pending':
            continue
        row = {'case': it['case'], 'names': _names(it), 'has_folio': bool(_norm_folio(it.get('folio')))}
        if not row['has_folio']:
            row['address'] = it.get('address', '')
        rows.append(row)
    print(json.dumps(rows[:limit], indent=1))


def cmd_folio(case, folio):
    f = _norm_folio(folio)
    if len(f) < 10:
        print('refused: a Miami-Dade folio has 13 digits')
        return 1
    if not _set(case, folio=f, folio_src='browser'):
        print('refused: unknown case for today')
        return 1
    print('ok: folio stored')
    return 0


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
# 2026-11-13. Measured that day: the browser tool will not return the county's search token and
# returns only about a thousand characters per call; the county page may not post to this computer
# (Content-Security-Policy default-src self); a scripted Chrome window gets no token at all.
#
# So the worker runs the name search by hand in the real Chrome and runs the script `js` prints.
# Version 1 copied only the rows tied to the parcel and saved the chain as TRUNCATED. Alejandro,
# the same evening: "I WANT VERIFIED EQUITY ... THE WHOLE SEARCH ... HIDDEN LIENS OR JUDGEMENT".
# Version 2 copies EVERY row the search returned, for EVERY name on the lead (the owner, and the
# people the case names as defendants), so the scraper's own analyze() sees exactly what the nightly
# search would have seen: mortgages, satisfactions, liens, judgments, tax liens, lis pendens. A read
# is saved as a full chain only when every row of every search arrived and no search hit the
# clerk's 500-row cap; anything less is still saved TRUNCATED, never as a clear.
#
# Rows are packed to fit the tool's answer size: the document type is an index into a list sent in
# the header, the constant time suffix on dates and the derivable book/page string are dropped and
# rebuilt here. Every chunk carries a checksum, so a mis-copied digit is refused, not stored.
FIELDS = ('doC_TYPE', 'foliO_NUMBER', 'seconD_PARTY', 'firsT_PARTY', 'reC_PAGE', 'reC_BOOK', 'reC_DATE',
          'cfN_MASTER_ID', 'oriG_REC_PAGE', 'oriG_REC_BOOK', 'subdiV_NAME', 'reC_BOOKPAGE')
DATE_SFX = ' 12:00:00 AM'
BUDGET = 820           # characters of row data per call: the browser tool cuts long answers
MAX_ROWS = 520         # the clerk returns at most 500 rows for a name
CLERK_CAP = 500
MOD = 1000000007

JS = r"""var out; { const K = %(chunk)d, BUD = %(budget)d, MOD = %(mod)d, SFX = %(sfx)s;
const q = new URLSearchParams(location.search).get('qs');
if(!q){ out = JSON.stringify({err: 'no search results on this page: run the name search first'}); }
else {
  if(window.__eqQ !== q){
    const j = await fetch('/officialrecords/api/SearchResults/getStandardRecords?qs=' + encodeURIComponent(q)).then(r => r.json());
    const all = j.recordingModels || [], types = [];
    const ti = v => { const s = (v == null) ? null : String(v); let i = types.indexOf(s); if(i < 0){ types.push(s); i = types.length - 1; } return i; };
    const dsfx = all.every(r => r.reC_DATE == null || String(r.reC_DATE).endsWith(SFX));
    const bpd = all.every(r => String(r.reC_BOOKPAGE == null ? '' : r.reC_BOOKPAGE) === (r.reC_BOOK + '/' + r.reC_PAGE));
    const rows = all.map(r => { const a = [ti(r.doC_TYPE), r.foliO_NUMBER, r.seconD_PARTY, r.firsT_PARTY, r.reC_PAGE, r.reC_BOOK,
      (dsfx && r.reC_DATE != null) ? String(r.reC_DATE).slice(0, -SFX.length) : r.reC_DATE, r.cfN_MASTER_ID, r.oriG_REC_PAGE, r.oriG_REC_BOOK, r.subdiV_NAME];
      if(!bpd) a.push(r.reC_BOOKPAGE); return a.map(v => v === undefined ? null : v); });
    const cuts = []; let cur = [], len = 0;
    rows.forEach(r => { const L = JSON.stringify(r).length + 1; if(cur.length && len + L > BUD){ cuts.push(cur); cur = []; len = 0; } cur.push(r); len += L; });
    if(cur.length) cuts.push(cur);
    window.__eqQ = q; window.__eq = {n: rows.length, types: types, dsfx: dsfx, bpd: bpd, cuts: cuts};
  }
  const E = window.__eq;
  if(K < 0){ out = JSON.stringify({hdr: 1, n: E.n, of: E.cuts.length, dsfx: E.dsfx, bpd: E.bpd, types: E.types}); }
  else {
    const part = E.cuts[K] || [];
    let s = 0; part.forEach(r => { const c = r.map(v => v == null ? '' : String(v)).join('|'); for(let i = 0; i < c.length; i++) s = (s * 31 + c.charCodeAt(i)) %% MOD; });
    out = JSON.stringify({k: K, of: E.cuts.length, sum: s, rows: part});
  }
} }
out"""


def _norm_folio(s):
    return ''.join(ch for ch in str(s or '') if ch.isdigit()).lstrip('0')


def rows_path(case):
    d = os.path.join(qdir(), 'rows')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, ''.join(ch for ch in case if ch.isalnum() or ch == '-') + '.json')


def _canon(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def checksum(rows):
    s = 0
    for r in rows:
        for ch in '|'.join(_canon(v) for v in r):
            s = (s * 31 + ord(ch)) % MOD
    return s


def js_for(case, chunk=-1):
    q = _read(qpath(), {'items': []})
    if not any(x['case'] == case for x in q['items']):
        return None
    return JS % {'chunk': int(chunk), 'budget': BUDGET, 'mod': MOD, 'sfx': json.dumps(DATE_SFX)}


def _unpack(row, hdr):
    """One packed row -> the dict the parser reads. Raises on a malformed row."""
    width = 11 if hdr['bpd'] else 12
    if not isinstance(row, list) or len(row) != width:
        raise ValueError('a row has %s fields, expected %d' % (len(row) if isinstance(row, list) else '?', width))
    ti = row[0]
    if not isinstance(ti, int) or not 0 <= ti < len(hdr['types']):
        raise ValueError('a row names a document type the header does not list')
    d = row[6]
    if hdr['dsfx'] and d is not None:
        d = str(d) + DATE_SFX
    bp = ('%s/%s' % (_canon(row[5]), _canon(row[4]))) if hdr['bpd'] else row[11]
    vals = [hdr['types'][ti], row[1], row[2], row[3], row[4], row[5], d, row[7], row[8], row[9], row[10], bp]
    return dict(zip(FIELDS, vals))


def accept_search(case, payload, name_index=0):
    """Store ONE name search: [header, chunk 0, chunk 1, ...] exactly as the page returned them.
    -> (ok, message). All or nothing: a search with a bad chunk stores no rows."""
    q = _read(qpath(), {'items': []})
    it = next((x for x in q['items'] if x['case'] == case), None)
    if not it or it.get('status') not in ('pending', 'rows'):
        return False, 'case is not waiting on today\'s list'
    names = _names(it)
    if not 0 <= int(name_index) < len(names):
        return False, 'this lead has %d name(s); --name-index must be 0..%d' % (len(names), len(names) - 1)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict) or not payload[0].get('hdr'):
        return False, 'the file must be a list that starts with the header object (the --chunk -1 answer)'
    hdr = payload[0]
    try:
        n, of = int(hdr['n']), int(hdr['of'])
        hdr = {'n': n, 'of': of, 'dsfx': bool(hdr['dsfx']), 'bpd': bool(hdr['bpd']), 'types': list(hdr['types'])}
    except Exception:
        return False, 'the header is missing n / of / dsfx / bpd / types'
    if n > MAX_ROWS:
        return False, 'more than %d rows' % MAX_ROWS
    chunks = payload[1:]
    if len(chunks) != of:
        return False, 'the header says %d chunk(s), the file has %d' % (of, len(chunks))
    models = []
    for i, ch in enumerate(chunks):
        try:
            k, rows, want = int(ch['k']), ch['rows'], int(ch['sum'])
        except Exception:
            return False, 'chunk %d is missing k / sum / rows' % i
        if k != i:
            return False, 'chunks out of order: position %d holds chunk %d' % (i, k)
        if not isinstance(rows, list) or checksum(rows) != want:
            return False, 'checksum mismatch in chunk %d: the rows were not copied exactly. Run the script for chunk %d again.' % (k, k)
        try:
            models += [_unpack(r, hdr) for r in rows]
        except ValueError as e:
            return False, 'chunk %d: %s' % (k, e)
    if len(models) != n:
        return False, 'the header says %d row(s), the chunks hold %d' % (n, len(models))
    path = rows_path(case)
    cur = _read(path, None) or {'case': case, 'parts': []}
    cur['parts'] = [p for p in cur['parts'] if p.get('i') != int(name_index)]
    cur['parts'].append({'i': int(name_index), 'name': ' '.join(x for x in names[int(name_index)] if x),
                         'n': n, 'capped': n >= CLERK_CAP, 'models': models})
    _write(path, cur)
    _set(case, status='rows', rows=sum(p['n'] for p in cur['parts']), searches=len(cur['parts']))
    left = [i for i in range(len(names)) if i not in {p['i'] for p in cur['parts']}]
    return True, ('%d row(s) stored for name %d. ' % (n, int(name_index))) + (
        'Names still to search: %s' % left if left else 'Every name on this lead is stored.')


def _models_file(it):
    """Merge a lead's stored searches into the file records_liens.py reads. -> (path, full)."""
    path = rows_path(it['case'])
    cur = _read(path, None) or {'parts': []}
    seen, models = set(), []
    for p in sorted(cur['parts'], key=lambda p: p['i']):
        for m in p['models']:
            key = (m.get('cfN_MASTER_ID'), m.get('firsT_PARTY'), m.get('seconD_PARTY'), m.get('doC_TYPE'))
            if key in seen:
                continue
            seen.add(key)
            models.append(m)
    names = _names(it)
    full = bool(cur['parts']) and len(cur['parts']) == len(names) and not any(p.get('capped') for p in cur['parts'])
    out = path[:-5] + '.models.json'
    _write(out, {'case': it['case'], 'full': full, 'searched': '; '.join(p['name'] for p in cur['parts']),
                 'searches': len(cur['parts']), 'names_on_lead': len(names), 'recordingModels': models})
    return out, full


def cmd_finish():
    q = _read(qpath(), None)
    if not q:
        print('nothing queued today')
        clean()
        return 0
    todo = [it for it in q['items'] if it.get('status') in ('token', 'rows')]
    for it in todo:
        argv = [sys.executable, '-u', 'records_liens.py', '--case', it['case'], '--cached-only']
        full = None
        if it.get('status') == 'rows':
            mf, full = _models_file(it)
            argv += ['--models-file', mf]
            if it.get('folio_src') == 'browser':
                argv += ['--folio', it['folio']]
        p = subprocess.run(argv,
                           cwd=HERE, capture_output=True, text=True, timeout=600,
                           encoding='utf-8', errors='replace')
        # the scraper prints "  ok  <case>" only after it wrote the chain to records_liens.json
        ok = p.returncode == 0 and ('  ok  ' + it['case']) in (p.stdout or '')
        _set(it['case'], status='done' if ok else 'failed', full=full,
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
    ap.add_argument('cmd', choices=['status', 'next', 'js', 'rows', 'folio', 'token', 'miss', 'finish', 'clean'])
    ap.add_argument('--chunk', type=int, default=-1, help='-1 = the header, 0.. = a data chunk')
    ap.add_argument('--name-index', type=int, default=0)
    ap.add_argument('--folio', default='')
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
    if a.cmd == 'folio':
        return cmd_folio(a.case, a.folio)
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
        okk, msg = accept_search(a.case, payload, a.name_index)
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
