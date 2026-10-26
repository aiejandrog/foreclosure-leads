#!/usr/bin/env python3
"""lp_status.py — is this "fresh filing" still a live case?

THE PROBLEM
A lis pendens is recorded the day a foreclosure is FILED. We treat those as the front of the
funnel and nurture them for months. But a case can END long before any auction — the owner
reinstates, refinances, sells, or the bank withdraws — and the lis pendens stays recorded in the
Official Records forever. Nothing in the LP feed tells us the case died. So a dismissed case keeps
sitting in the EARLY lane looking like a fresh opportunity, and somebody eventually calls a
homeowner who already fixed their problem to ask about a foreclosure that no longer exists.

Found live: 2026-012840-CA-01 (RILES ADRIENNE M / JPMorgan Chase) — caseStatus CLOSED, dockets
VOLD + VOLDCV "Voluntary Dismissal ... WITHOUT PREJUDICE". Still on the board as a workable lead.

WHY caseStatus AND NOT DOCKET TEXT — this is the whole design decision
The obvious implementation is to grep the docket for "Voluntary Dismissal". That is WRONG and I
verified it against live data before writing this: case 2026-012644-CA-01 carries docket code NVDU,
"Notice Of Voluntary Dismissal Of Unknown Party" — dropping the unnamed defendants, a routine step
in a case that is very much alive. Its caseStatus is OPEN. A text match on "voluntary dismissal"
would have killed a live lead. So the STATUS field decides; docket codes only corroborate.

WHAT "CLOSED" MEANS, AND WHERE IT DOES NOT
Measured on this board: all 220 auction leads carrying a status have a FUTURE sale date, and 180
of them read CLOSED or RECLOSED. In Florida the clerk closes a foreclosure when FINAL JUDGMENT is
entered; the sale is post-judgment activity. So CLOSED on a lead WITH a scheduled sale is normal
and means nothing is wrong. It is only meaningful on a lead with NO sale scheduled — a lis pendens
— where it says the case reached a terminal state without ever getting to auction.

That is why this script only runs against LP leads, and why the flag it sets is scoped to them.

BROWARD (off by default). The Broward clerk's Commercial Data API returns a case summary with a
disposition status, ONE request per case. With DEALFLOW_LP_BROWARD=1 and BROWARD_CLERK_API_KEY set,
Broward LP rows are checked too. The vocabulary was NOT validated against live responses when this was
written (no key existed), so the mapping is deliberately strict: only a disposition that reads as a
dismissal of the whole action drops a row. Any other terminal-looking label is stored as `review` with
its text and drops nothing. A failed, denied, rate-limited or unparseable read stores nothing, so the
row stays "not checked" and the pass stops early rather than hammering the clerk.
Palm Beach has no adapter.

Run:  python lp_status.py                # check every LP case, cached
      python lp_status.py --refresh      # ignore the cache
      python lp_status.py --case <case>  # one case, verbose
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sibling_cases as S    # reuse the working, un-captcha'd OCS request chain

HERE = os.path.dirname(os.path.abspath(__file__))
IN = os.path.join(HERE, 'lis_pendens.json')
CACHE = os.path.join(HERE, '_lp_status_cache.json')
COVERAGE = os.path.join(HERE, '_lp_status_coverage.json')

# Counties this script can actually ask about. Broward and Palm Beach need their own clerk
# clients; until one exists, their rows must read as UNCHECKED rather than inherit the
# empty-status shape that means "verified and fine" everywhere else.
COUNTY_ADAPTERS = ('MIAMI-DADE',)

ENV_BROWARD = 'DEALFLOW_LP_BROWARD'
ENV_BROWARD_RUNTIME = 'LP_BROWARD_MAX_RUNTIME_S'
ENV_BROWARD_CASES = 'LP_BROWARD_MAX_CASES'
BROWARD_RUNTIME_DEFAULT = 600.0
BROWARD_CASES_DEFAULT = 400


def broward_enabled(env=None):
    """True only when the flag is on AND the clerk key is set. Either one missing = not checked."""
    env = os.environ if env is None else env
    if str(env.get(ENV_BROWARD) or '').strip() != '1':
        return False
    return bool(str(env.get('BROWARD_CLERK_API_KEY') or '').strip())


def adapters(env=None):
    """Counties checked this run. Broward joins only through broward_enabled()."""
    return COUNTY_ADAPTERS + (('BROWARD',) if broward_enabled(env) else ())


def _env_num(name, default, cast, env=None):
    env = os.environ if env is None else env
    try:
        v = cast(str(env.get(name) or '').strip() or default)
    except Exception:
        return default
    return v if v > 0 else default

# Terminal clerk statuses. On a lead with no sale scheduled these mean the case ended.
# How long a clerk status is trusted before it is fetched again. Two weeks is a compromise:
# short enough to catch a reinstatement while the lead is still being worked, long enough
# that the nightly re-checks ~1/14th of the Miami-Dade book per run instead of all of it.
STATUS_TTL_DAYS = 14

TERMINAL = {'CLOSED', 'RECLOSED', 'PJREPINACT', 'DISPOSED'}
# Docket codes that corroborate a true dismissal of the CASE. NVDU is deliberately excluded — it
# dismisses UNKNOWN PARTIES, not the action, and appears in healthy cases (see the docstring).
DISMISS_CODES = {'VOLD', 'VOLDCV', 'DISM', 'DISMCV', 'ORDISM'}


def _load(p, d):
    if not os.path.exists(p):
        return d
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


def check(case):
    """-> {status, dismissed, codes, checked} or {} when the clerk will not answer."""
    try:
        d = S._single(case)
    except Exception as e:
        print('  %s: lookup failed (%s)' % (case, str(e)[:70]))
        return {}
    status = str(d.get('caseStatus') or '').strip().upper()
    dk = d.get('dockets') or d.get('docketList') or []
    codes = sorted({str(x.get('docketCode') or x.get('code') or '').strip().upper()
                    for x in dk if (x.get('docketCode') or x.get('code'))})
    return {
        'status': status,
        # Terminal status is necessary. A dismissal code makes it definite; without one the case
        # may have closed by going to judgment, which is a different (still-dead-as-a-fresh-filing)
        # story — the board says which, rather than guessing.
        'dismissed': bool(status in TERMINAL and (DISMISS_CODES & set(codes))),
        'terminal': status in TERMINAL,
        'codes': [c for c in codes if c in DISMISS_CODES],
        'checked': time.strftime('%Y-%m-%d'),
    }


# ALLOWLIST. Only a label that is nothing but a whole-case dismissal drops a row. Every other label
# that mentions dismissal ("Dismissal Vacated", "Motion to Dismiss Denied", "Not Dismissed",
# "Reinstated After Dismissal", "Dismissed - Defendant Jones", "As To Unknown Parties") goes to review.
_DISMISS_WORD = re.compile(r'dismiss', re.I)
_WHOLE_DISMISSAL = re.compile(
    r'^(?:voluntar(?:y|ily)\s+|involuntar(?:y|ily)\s+)?dismiss(?:ed|al)(?:\s+(?:with|without)\s+prejudice)?$', re.I)
_TERMINAL_WORDS = re.compile(r'\b(closed|disposed|disposition|judgment|final|reclosed|satisfied)\b', re.I)


def broward_verdict(label):
    """label -> (dismissed, review). `dismissed` only for an unambiguous whole-case dismissal."""
    label = ' '.join(str(label or '').split())
    if not label:
        return False, False
    if _WHOLE_DISMISSAL.match(label):
        return True, False
    return False, bool(_DISMISS_WORD.search(label) or _TERMINAL_WORDS.search(label))


_KNOWN_PROBLEMS = {'denied', 'rate_limited', 'time_budget', 'captcha', 'truncated', 'not_json',
                   'http_404', 'http_500', 'http_502', 'http_503'}
_STOP_PROBLEMS = ('denied', 'rate_limited', 'time_budget', 'captcha', 'truncated')
_MAX_CONSECUTIVE_FAILS = 5


def check_broward(case, key, transport, clock, gap=None, deadline=None):
    """One Broward case -> (record_or_None, problem). A problem never produces a record."""
    import clerk_bk as CK
    api_case = CK.broward_api_case(case)
    if not api_case:
        return None, 'not_civil_number'
    gap = CK.DEFAULT_MIN_INTERVAL if gap is None else gap
    last = getattr(clock, 'last_http', None)
    if last is not None and gap > 0:
        wait = gap - (clock.time() - last)
        if wait > 0:
            if deadline is not None and clock.time() + wait > deadline:
                return None, 'time_budget'
            clock.sleep(wait)
    url = CK._api_url(api_case, 'case', key)
    if not CK.allowed_url(url, api_case):
        return None, 'refused_url'
    status, payload, problem = CK._get_json(url, key, transport, clock, CK.HTTP_TIMEOUT, deadline)
    clock.last_http = clock.time()
    if problem or payload is None:
        problem = problem if problem in _KNOWN_PROBLEMS else 'error'
        return None, problem
    label, err = CK.parse_case_payload(payload, api_case)
    if err:
        return None, 'unparseable'
    if not label:
        # a matching case number with no disposition field is a schema we do not know, not an open case
        return None, 'no_disposition'
    dismissed, review = broward_verdict(label)
    return {
        'status': label.upper(),
        'dismissed': dismissed,
        'terminal': dismissed,           # only a clear dismissal leaves the early lane
        'review': review and not dismissed,
        'codes': [],
        'src': 'broward-api',
        'checked': time.strftime('%Y-%m-%d'),
    }, ''


def run_broward(feed, cache, env=None, transport=None, clock=None):
    """Check Broward LP cases within the run budget. Returns a counts dict. Mutates cache."""
    import clerk_bk as CK
    env = os.environ if env is None else env
    key = CK.api_key(env)
    clock = clock or CK.Clock()
    transport = transport or CK.urllib_transport
    max_cases = _env_num(ENV_BROWARD_CASES, BROWARD_CASES_DEFAULT, int, env)
    budget = _env_num(ENV_BROWARD_RUNTIME, BROWARD_RUNTIME_DEFAULT, float, env)
    deadline = clock.time() + budget
    now = clock.time()
    rows = [r for r in feed if str(r.get('county') or '').upper() == 'BROWARD']
    # never-checked first, then the oldest check; newest filings first inside each group
    todo, fresh = [], 0
    for r in sorted(rows, key=lambda r: str(r.get('date') or ''), reverse=True):
        c = str(r.get('case') or '').strip()
        if not c or '-' not in c:
            continue
        rec = cache.get(c)
        if rec is None:
            todo.append((0, c))
        elif (now - float(rec.get('at') or 0)) > STATUS_TTL_DAYS * 86400:
            todo.append((1, c))
        else:
            fresh += 1
    todo = [c for _, c in sorted(todo, key=lambda t: t[0])][:max_cases]
    out = {'cases': len(rows), 'asked': 0, 'recorded': 0, 'fresh': fresh,
           'stopped': '', 'problems': {}, 'review': {}}
    fails = 0
    for c in todo:
        rec, problem = check_broward(c, key, transport, clock, deadline=deadline)
        out['asked'] += 1
        if rec:
            rec['at'] = clock.time()
            cache[c] = rec
            out['recorded'] += 1
            fails = 0
            if rec.get('review'):
                out['review'][rec['status']] = out['review'].get(rec['status'], 0) + 1
            continue
        out['problems'][problem] = out['problems'].get(problem, 0) + 1
        fails += 1
        # Denied, rate-limited or out of time: stop the pass. So does a run of failures in a row
        # (an outage or a changed schema), instead of working through every case until the deadline.
        if problem in _STOP_PROBLEMS:
            out['stopped'] = problem
            break
        if fails >= _MAX_CONSECUTIVE_FAILS:
            out['stopped'] = 'consecutive_failures'
            break
    return out


def main():
    args = sys.argv[1:]
    one = args[args.index('--case') + 1] if '--case' in args else None
    refresh = '--refresh' in args

    if one:
        print(json.dumps(check(one), indent=1))
        return

    feed = _load(IN, [])
    if not feed:
        print('no lis_pendens.json — nothing to check'); return
    cache = {} if refresh else _load(CACHE, {})

    # OCS is a Miami-Dade system (sibling_cases.py:14) — a BROWARD case number queried against
    # it returns junk, not a docket. Non-MD rows wait for their county's case-status client.
    #
    # THE EXCLUSION IS CORRECT; BEING SILENT ABOUT IT WAS NOT (audit 2026-09-21, defect 10).
    # A Broward LP row was simply dropped here, so lp_leads found no status for it, and a row
    # with no status renders exactly like a row verified OPEN: cstatus '', lpDismissed False,
    # lpClosed False. The lane that exists to stop us calling a homeowner whose case already
    # ended was therefore running on one county in three, and nothing on the board said so.
    # Coverage is now written out and stamped onto the row, so "verified live today" is
    # distinguishable from "never checkable here".
    by_county = {}
    for r in feed:
        by_county.setdefault(str(r.get('county') or 'MIAMI-DADE').upper(), 0)
        by_county[str(r.get('county') or 'MIAMI-DADE').upper()] += 1
    active = adapters()
    covered = sorted(c for c in by_county if c in active)
    uncovered = sorted(c for c in by_county if c not in active)
    try:
        json.dump({'ran': time.strftime('%Y-%m-%d %H:%M'),
                   'ttl_days': STATUS_TTL_DAYS,
                   'counties': by_county,
                   'checked': covered,
                   'no_adapter': uncovered},
                  open(COVERAGE, 'w', encoding='utf-8'), indent=1)
    except Exception as e:
        print('coverage not written: %s' % e)
    if uncovered:
        print('NO CASE-STATUS ADAPTER for %s — %d LP row(s) cannot be checked for dismissal here. '
              'They are stamped unchecked, not assumed live.'
              % (', '.join(uncovered), sum(by_county[c] for c in uncovered)))

    md_feed = [r for r in feed if str(r.get('county') or 'MIAMI-DADE').upper() in COUNTY_ADAPTERS]
    cases = [str(r.get('case') or '').strip() for r in md_feed]
    cases = [c for c in cases if c and '-' in c]          # skip synthesized LP-XXXX keys
    # STALE-CACHE RE-CHECK (2026-09-01). This was `c not in cache`, so a case checked ONCE as OPEN
    # was never looked at again -- and reinstatement is most common in the first weeks after filing,
    # exactly the window we then nurture for months. The board therefore kept showing owners who had
    # already fixed it, which is what reps hear on the phone: "I paid that off." A status is a
    # snapshot of a moving case, so it gets a shelf life.
    now = time.time()
    fresh = 0
    todo = []
    for c in cases:
        rec = cache.get(c)
        if rec is None:
            todo.append(c)
        elif (now - float(rec.get('at') or 0)) > STATUS_TTL_DAYS * 86400:
            todo.append(c)                                   # re-check, do not trust the old answer
        else:
            fresh += 1
    print('%d LP case(s) · %d to check (%d still fresh, TTL %dd)'
          % (len(cases), len(todo), fresh, STATUS_TTL_DAYS))

    for i, c in enumerate(todo, 1):
        rec = check(c)
        if rec:
            rec['at'] = time.time()      # what the TTL above reads. Without it every entry is
            cache[c] = rec               # permanently "fresh" and the re-check never fires.
        if i % 10 == 0 or i == len(todo):
            print('  %d/%d' % (i, len(todo)))
            json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=0)   # checkpoint
        time.sleep(0.6)

    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=0)

    if 'BROWARD' in active:
        bw = run_broward(feed, cache)
        json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=0)
        print('BROWARD: %d LP case(s) · %d asked · %d recorded · %d still fresh%s'
              % (bw['cases'], bw['asked'], bw['recorded'], bw['fresh'],
                 (' · STOPPED: ' + bw['stopped']) if bw['stopped'] else ''))
        if bw['problems']:
            print('  problems (counts only): ' + ', '.join('%s %d' % kv for kv in sorted(bw['problems'].items())))
        for lab, n in sorted(bw['review'].items()):
            print('  REVIEW (not dropped): %s x%d' % (lab, n))
    elif str(os.environ.get(ENV_BROWARD) or '').strip() == '1':
        print('BROWARD: flag is on but BROWARD_CLERK_API_KEY is not set, Broward rows stay unchecked.')

    dismissed = [c for c, v in cache.items() if v.get('dismissed')]
    terminal = [c for c, v in cache.items() if v.get('terminal') and not v.get('dismissed')]
    live = [c for c, v in cache.items() if not v.get('terminal')]
    print('\nlive %d · closed-without-a-dismissal-docket %d · DISMISSED %d'
          % (len(live), len(terminal), len(dismissed)))
    for c in dismissed:
        print('  DISMISSED  %s  %s  %s' % (c, cache[c].get('status'), ','.join(cache[c].get('codes') or [])))
    for c in terminal:
        print('  closed     %s  %s  (no dismissal docket — may have gone to judgment)' % (c, cache[c].get('status')))
    if dismissed or terminal:
        print('\nRe-run lp_leads.py then rebuild to drop these out of the EARLY lane.')


if __name__ == '__main__':
    main()
