#!/usr/bin/env python
"""md_case_walk.py -- new Miami-Dade foreclosure filings by walking case numbers. $0, no captcha.

WHY (2026-11-16, Alex: "check the scraper ... is anything we are skipping that is bringing us down
with leads?"). Our Miami-Dade filing feed was the recorder's index, searched once per LISTED
plaintiff (59 lender names) with a paid captcha token. Two holes:
  * with the monthly paid-reads cap at $0 every sweep is refused, so the newest Miami-Dade filing
    on file was 2026-09-04, ten weeks stale, in Jesse's home county;
  * a plaintiff not on the list (a small lender, a private note holder, a credit union) was never
    swept at all, in any month.
Miami-Dade's court case lookup (docket.pull, the same call that reads every docket here) answers
for any case number with no login and no captcha, and the number space is sequential. Walking it
finds every foreclosure the court has, whoever the plaintiff is.

HOW
  forward   from the highest 2026 CA-01 number we hold, up to the frontier (a run of numbers the
            court does not know yet);
  backward  from the highest number we hold, toward 1, skipping what we hold, a bounded slice a
            night (newest gaps first). This fills the plaintiffs the old sweep never listed; once
            it reaches 1 it is done.
A case whose lookup failed is NOT skipped past: the cursor only moves over cases the court
answered for (found, or 'no such case'). An unreachable court stops the run.

WHAT A ROW IS
A mortgage foreclosure the court shows as open (case type RPMF Homestead / Non-Homestead, no
disposition): case, filing date, plaintiff, and the first named defendant as the owner. It has no
legal description, so fl_lp/palmbeach_resolve.py (county Miami-Dade) places it by owner name on the
state roll, with the same every-token rule as Broward and Palm Beach. When the recorder sweep later
returns the same case with its legal description, that row replaces this one (lis_pendens.merge).

    python md_case_walk.py                       forward 400 + backward 1000
    python md_case_walk.py --fwd 50 --back 0
    python md_case_walk.py --dry-run             counts only, writes nothing
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LIS = os.path.join(HERE, 'lis_pendens.json')
STATE = os.path.join(HERE, 'md_walk_state.json')       # gitignored: cursors only, no names
YEAR = 2026
SUFFIX = 'CA-01'
FRONTIER_RUN = 40          # this many consecutive unknown numbers = past the newest filing
STOP_ERRORS = 6            # consecutive failed lookups: the court is unreachable, stop
PAUSE_S = 0.45
FORECLOSURE_RE = re.compile(r'^\s*RPMF\b|FORECLOS', re.I)
UNKNOWN_RE = re.compile(r'\bUNKNOWN\b|\bTENANT\b|\bSPOUSE\b|\bPARTIES? IN POSSESSION\b|\bJOHN DOE\b|\bJANE DOE\b', re.I)


def case_no(year, seq):
    return '%d-%06d-%s' % (year, seq, SUFFIX)


def seq_of(case, year=YEAR):
    m = re.match(r'%d-(\d{6})-%s$' % (year, re.escape(SUFFIX)), str(case or '').strip().upper())
    return int(m.group(1)) if m else None


def held_sequences(rows, year=YEAR):
    """The sequence numbers of Miami-Dade CA-01 cases already in lis_pendens.json."""
    return {s for s in (seq_of(r.get('case'), year) for r in rows
                        if isinstance(r, dict) and (r.get('county') or 'MIAMI-DADE') == 'MIAMI-DADE') if s}


def is_foreclosure(j):
    if not isinstance(j, dict):
        return False
    return bool(FORECLOSURE_RE.search(str(j.get('caseType') or '')))


def is_open(j):
    return not str(j.get('dispositionCode') or '').strip() and not str(j.get('dispositionDate') or '').strip()


def _fdate(s):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
        try:
            d = datetime.datetime.strptime(str(s or '').strip()[:19 if 'T' in str(s) else 10], fmt).date()
            return '%d/%d/%d' % (d.month, d.day, d.year)
        except ValueError:
            continue
    return ''


def parties_of(j):
    """-> (plaintiff, owner). The owner is the first named Defendant who is a person or a business:
    'Unknown Tenant', 'Unknown Spouse' and the like are real defendants but not the owner."""
    plaintiff = owner = ''
    for p in j.get('parties') or []:
        t = str(p.get('partyTypeDesc') or '').strip().upper()
        n = ' '.join(str(p.get('partyName') or '').split())
        if not n:
            continue
        if t == 'PLAINTIFF' and not plaintiff:
            plaintiff = n.upper()
        elif t == 'DEFENDANT' and not owner and not UNKNOWN_RE.search(n):
            owner = n.upper()
    return plaintiff, owner


def to_row(j, kind_of=None):
    """One court case -> a lis_pendens.json row, or None when it is not a usable open foreclosure."""
    if not is_foreclosure(j) or not is_open(j):
        return None
    case = str(j.get('caseNumber') or '').strip().upper()
    if seq_of(case, int(case[:4]) if case[:4].isdigit() else YEAR) is None:
        return None
    plaintiff, owner = parties_of(j)
    date = _fdate(j.get('filingDate'))
    if not plaintiff or not owner or not date:
        return None
    parties = '%s; %s' % (plaintiff, owner)
    if kind_of is None:
        import lis_pendens as LP
        kind_of = LP._kind
    return {'date': date, 'case': case, 'docType': 'COURT FILING (case walk)', 'bookpage': '', 'legal': '',
            'parties': parties, 'plaintiff': plaintiff, 'owner': owner, 'kind': kind_of(parties),
            'county': 'MIAMI-DADE', 'instrument': '', 'parcel': '', 'src': 'court-walk',
            'courtType': str(j.get('caseType') or '').strip()[:30]}


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data, **kw):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, **kw)
    os.replace(tmp, path)


def lookup(case, pull):
    """-> ('found', json) | ('none', None) | ('error', None). 'none' = the court has no such case."""
    try:
        j = pull(case)
    except Exception as exc:                            # HTTP errors, timeouts
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        return ('none', None) if status in (400, 404) else ('error', None)
    if not isinstance(j, dict) or not (j.get('caseNumber') or j.get('caseStyle') or j.get('caseType')):
        return 'none', None
    return 'found', j


def walk(rows, state, fwd=400, back=600, year=YEAR, pull=None, pause=PAUSE_S, kind_of=None, say=print):
    """Walk the number space. -> (new_rows, state, report). Pure given `pull`; touches no file."""
    if pull is None:
        import docket
        pull = docket.pull
    held = held_sequences(rows, year)
    cur = state.setdefault(str(year), {})
    if 'fwd' not in cur:
        cur['fwd'] = (max(held) + 1) if held else 1
    if 'back' not in cur:
        # from the TOP of what we hold, down: the old sweep skipped every plaintiff off its list, so the
        # holes are everywhere below the newest case, not only under the oldest one. Newest first,
        # because a filing from last month matters more than one from January.
        cur['back'] = max(held) if held else 0
    out, rep = [], {'looked': 0, 'found': 0, 'foreclosures': 0, 'closed': 0, 'errors': 0,
                    'frontier': False, 'unreachable': False, 'skipped_held': 0}

    def one(seq):
        if seq in held:
            rep['skipped_held'] += 1
            return 'held'
        st, j = lookup(case_no(year, seq), pull)
        rep['looked'] += 1
        if pause:
            time.sleep(pause)
        if st == 'found':
            rep['found'] += 1
            if is_foreclosure(j):
                row = to_row(j, kind_of)
                if row:
                    rep['foreclosures'] += 1
                    out.append(row)
                else:
                    rep['closed'] += 1
        return st

    unknown_run = errs = 0
    n = 0
    while n < fwd:
        st = one(cur['fwd'])
        if st == 'error':
            errs += 1
            rep['errors'] += 1
            if errs >= STOP_ERRORS:
                rep['unreachable'] = True
                return out, state, rep
            continue                                    # same number again: never skip past a failure
        errs = 0
        cur['fwd'] += 1
        n += 1
        unknown_run = unknown_run + 1 if st == 'none' else 0
        if unknown_run >= FRONTIER_RUN:
            cur['fwd'] -= unknown_run                   # park the cursor at the first unknown number
            rep['frontier'] = True
            break
    n = errs = 0
    while n < back and cur['back'] >= 1:
        st = one(cur['back'])
        if st == 'error':
            errs += 1
            rep['errors'] += 1
            if errs >= STOP_ERRORS:
                rep['unreachable'] = True
                return out, state, rep
            continue
        errs = 0
        cur['back'] -= 1
        if st != 'held':
            n += 1                                      # a case we already hold costs nothing: not counted
    rep['back_done'] = cur['back'] < 1
    return out, state, rep


def merge(existing, new_rows):
    """-> (merged, added). Existing rows are never changed; a case already held is not added."""
    have = {(r.get('county') or 'MIAMI-DADE', str(r.get('case') or '').upper())
            for r in existing if isinstance(r, dict)}
    merged, added = list(existing), 0
    for r in new_rows:
        k = (r['county'], r['case'])
        if k in have:
            continue
        have.add(k)
        merged.append(r)
        added += 1
    return merged, added


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--fwd', type=int, default=400)
    ap.add_argument('--back', type=int, default=1000)
    ap.add_argument('--year', type=int, default=YEAR)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    rows = _load(LIS, None)
    if not isinstance(rows, list):
        print('md_case_walk: lis_pendens.json not readable; nothing done')
        return 1
    state = _load(STATE, {})
    new, state, rep = walk(rows, state, fwd=a.fwd, back=a.back, year=a.year)
    print('md_case_walk: looked at %d case number(s): %d exist, %d open foreclosure(s), %d closed, %d error(s)%s%s'
          % (rep['looked'], rep['found'], rep['foreclosures'], rep['closed'], rep['errors'],
             ', reached the newest filing' if rep['frontier'] else '',
             ', COURT UNREACHABLE (stopped)' if rep['unreachable'] else ''))
    if a.dry_run:
        return 0
    merged, added = merge(rows, new)
    if added:
        merged.sort(key=lambda x: (lambda d: d.toordinal() if d else 0)(
            _parse(x.get('date'))), reverse=True)
        _save(LIS, merged, indent=1)
    _save(STATE, state, indent=1)
    print('md_case_walk: %d new Miami-Dade filing(s) merged; lis_pendens.json holds %d row(s); cursors fwd %s back %s'
          % (added, len(merged), state[str(a.year)]['fwd'], state[str(a.year)]['back']))
    return 3 if rep['unreachable'] else 0


def _parse(s):
    try:
        return datetime.datetime.strptime(str(s or ''), '%m/%d/%Y').date()
    except ValueError:
        return None


if __name__ == '__main__':
    sys.exit(main())
