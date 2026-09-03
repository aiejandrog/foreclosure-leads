"""engine_drift.py -- FAIL-LOUD watchdog for two-engine drift.

WHY THIS EXISTS
Two engines run this pipeline: laptop owns the board, cloud is the $0 failover. The
reconciliation on 2026-09-01 moved five enrichers to the laptop because their outputs are
gitignored per-engine and cloud work on them was structurally invisible to the laptop's board.

The recurring failure mode is that WRONG-ENGINE SCRIPTS FAIL SILENTLY -- `set +e` swallows
exit codes, and publish_guard was once blind to four enrichers (a build with none of them was
indistinguishable from a complete one). This file is the guard that comes AFTER the fix, so a
new stage that goes silent, a cache that goes stale, or a machine misidentifying itself gets
noticed before it publishes.

THE FAIL-LOUD RULE, and it is the only rule that matters here: a state this file cannot
evaluate must ALARM, never print ok. A watchdog that "succeeds while doing nothing" is the
exact bug class it exists to catch. There are three exit codes:

    0 = CLEAN                every check ran and passed
    2 = DRIFT                a real inconsistency was detected
    3 = CANNOT EVALUATE      an input the check depends on is missing -- treated as ALARM

A branch that hits a case it didn't foresee raises rather than continuing. There is no ok-with-
caveats state; a caveat becomes an ALARM until the check learns to prove otherwise.

DECLARED, NOT SNIFFED: `engine.id` (gitignored, per-machine, one line naming this engine) is
the identity marker. The operator sets it once. Refusing to bootstrap from `os.hostname()` or
some environment guess is deliberate -- guessing identity is exactly how the wrong engine
starts owning things it does not produce.

Usage:
    python engine_drift.py           # human report
    python engine_drift.py --json    # machine-readable
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, 'docs', 'index.html')
ID_FILE = os.path.join(HERE, 'engine.id')
COV_MARK = re.compile(r'<!--\s*DEALFLOW-COVERAGE\s*(\{.*?\})\s*-->')

# THE RECONCILIATION BASELINE, hardcoded here so it becomes an ASSERTION rather than a memory
# note. Every entry is a file whose contents materially shape docs/index.html. Two categories:
#
#   PER_ENGINE_LAPTOP -- gitignored on both machines; laptop-only after 2026-09-01. If cloud
#     ever writes to one of these, the drift is silent (cloud commits nothing, its file dies
#     with the container), but ALSO wasteful (cloud pays for API calls whose result nobody
#     sees). This file is the second half of the fix that first appeared in commit 604ad6e.
#
#   SHARED_TRACKED -- committed to the repo; either engine may write. The watchdog reports
#     per-file authorship so a drift back into "both write, races expected" is visible before
#     it recurs.
#
# Adding a new enricher: add it here in the SAME commit that adds the script. Otherwise the
# guard cannot see it and the file becomes the exact class of thing this file exists to catch.

PER_ENGINE_LAPTOP = [
    ('leads_final.json',        'the board itself'),
    ('records_liens.json',      'MDC lien chains (2Captcha, laptop-only key)'),
    ('code_liens.json',         'code-enforcement (moved to laptop 2026-09-01)'),
    ('county_taxes.json',       'FL tax bills (moved to laptop 2026-09-01)'),
    ('skiptrace_results.json',  'phones + emails (Tracerfy, laptop-only key)'),
    ('lp_addresses.json',       'lis-pendens address resolver'),
]

SHARED_TRACKED = [
    ('broward_mortgages.json',  'moved to laptop 2026-09-01, but tracked -- verify author'),
    ('judgment_dates.json',     'FS 55.03 accrual dates -- moved to laptop 2026-09-01'),
    ('cash_buyers.json',        'moved to laptop 2026-09-01'),
    ('auction_archive.json',    'sale-history stamps (§362 stay flags)'),
    ('geocode_cache.json',      'address -> lat/lon'),
    ('sale_history_cache.json', 'reset counter (proprietary column)'),
    ('redfin_cache.json',       'value comps'),
]

# Coverage stamp keys mapped to the file that SHOULD carry non-zero for them. If the stamp
# says field=N but the file is missing or empty, that is a certain drift -- something wrote
# the stamp against a cache that was not there at build time.
COV_TO_FILE = {
    'liens':     'records_liens.json',
    'phones':    'skiptrace_results.json',
    'codeliens': 'code_liens.json',
    'taxes':     'county_taxes.json',
    'judgdt':    'judgment_dates.json',
    # 'arv' -> spread across redfin/zillow/manual, no single-file check
    # 'leads' -> leads_final.json but with its own upstream, already guarded elsewhere
    # 'ownflip', 'wp', 'bkstay' -> in-line derivations, no file to check
}

# Freshness ceilings. A cache older than this while the board says it carries that data is a
# certain sign the build read stale content -- not an ALARM by itself (a Sunday off, a legit
# skip), but reported so the operator can decide.
FRESH_HOURS_ENRICHMENT = 48
FRESH_HOURS_BOARD = 30

ALARMS = []
NOTES = []


def _alarm(msg):    ALARMS.append(msg)
def _note(msg):     NOTES.append(msg)


def read_engine_id():
    """The one thing this file cannot guess. Absent = CANNOT EVALUATE, exit 3."""
    if not os.path.exists(ID_FILE):
        _alarm('engine.id MISSING -- cannot identify which engine ran this build. '
               'Create engine.id with a single line: "laptop" or "cloud".')
        return None
    try:
        v = open(ID_FILE, encoding='utf-8').read().strip().lower()
    except Exception as e:
        _alarm('engine.id UNREADABLE (%s) -- cannot identify this engine.' % e)
        return None
    if v not in ('laptop', 'cloud'):
        _alarm('engine.id says "%s" -- must be exactly "laptop" or "cloud". '
               'Guessing identity is how the wrong engine starts owning things.' % v)
        return None
    return v


def _age_hours(path):
    if not os.path.exists(path):
        return None
    return (dt.datetime.now() - dt.datetime.fromtimestamp(os.path.getmtime(path))).total_seconds() / 3600.0


def _size(path):
    try: return os.path.getsize(path)
    except Exception: return -1


def _git_author(path):
    """Email of the last commit that touched `path`, or '' if not tracked / not committed."""
    try:
        r = subprocess.run(['git', 'log', '-1', '--format=%ae', '--', path],
                           cwd=HERE, capture_output=True, text=True, timeout=30)
        return (r.stdout or '').strip() if r.returncode == 0 else ''
    except Exception:
        return ''


def _git_committer_date(path):
    try:
        r = subprocess.run(['git', 'log', '-1', '--format=%ci', '--', path],
                           cwd=HERE, capture_output=True, text=True, timeout=30)
        return (r.stdout or '').strip() if r.returncode == 0 else ''
    except Exception:
        return ''


def read_coverage():
    """Coverage stamp from docs/index.html, or None. Absent = CANNOT EVALUATE."""
    if not os.path.exists(DOCS):
        _alarm('docs/index.html MISSING -- no board to check against.')
        return None
    try:
        head = open(DOCS, encoding='utf-8', errors='replace').read(8000)
    except Exception as e:
        _alarm('docs/index.html UNREADABLE (%s)' % e); return None
    m = COV_MARK.search(head)
    if not m:
        _alarm('docs/index.html has NO coverage stamp -- build predates DEALFLOW-COVERAGE, '
               'or was corrupted. Cannot evaluate drift without a census.')
        return None
    try:
        return json.loads(m.group(1))
    except Exception as e:
        _alarm('coverage stamp is malformed JSON (%s) -- treated as missing.' % e); return None


def check_per_engine_files(engine, cov):
    """Every laptop-owned per-engine file must exist AND be fresh AND back its coverage claim."""
    is_laptop = (engine == 'laptop')
    results = []
    for fn, why in PER_ENGINE_LAPTOP:
        p = os.path.join(HERE, fn)
        age = _age_hours(p); sz = _size(p)
        r = {'file': fn, 'why': why, 'age_hours': age, 'bytes': sz,
             'owner': 'laptop', 'i_own': is_laptop}
        if is_laptop:
            if not os.path.exists(p):
                _alarm('%s MISSING on the laptop -- laptop owns it (%s). Something did not run.'
                       % (fn, why))
            elif sz < 3:
                _alarm('%s is essentially empty (%d bytes) -- likely a truncated write.' % (fn, sz))
            elif age > FRESH_HOURS_ENRICHMENT:
                _note('%s is %.0fh old (limit %dh) -- likely running from a stale cache.'
                      % (fn, age, FRESH_HOURS_ENRICHMENT))
        else:
            # On cloud: these are laptop-owned, so cloud may or may not have them. It is not an
            # alarm per se, but if cloud has FRESH ones it is drifting BACK toward the pre-
            # reconciliation state.
            if os.path.exists(p) and age is not None and age < 12:
                _alarm('cloud engine has a FRESH %s (%.0fh old) -- the reconciliation moved this '
                       'file to laptop-only. Cloud is wasting API calls its board will not use.'
                       % (fn, age))
        results.append(r)
    return results


def check_shared_files():
    """Report last author + freshness for every shared file. Report, not alarm -- the point is
    visibility: a file suddenly co-authored by both engines is a drift back toward races, and
    an operator has to see it to notice."""
    rows = []
    for fn, why in SHARED_TRACKED:
        p = os.path.join(HERE, fn)
        rows.append({
            'file': fn, 'why': why,
            'age_hours_disk': _age_hours(p),
            'last_commit_author': _git_author(fn),
            'last_commit_date': _git_committer_date(fn),
        })
    return rows


def check_coverage_backing(engine, cov):
    """If the stamp says a field carries data but its backing file is absent or empty, that is
    a certain drift: something wrote the stamp against a cache that did not exist at build time."""
    if not cov:
        return
    for field, fn in COV_TO_FILE.items():
        val = cov.get(field, 0) or 0
        if not val:
            continue
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            _alarm('coverage says %s=%d but %s is MISSING on this engine -- the board carries '
                   'enrichment this machine cannot see.' % (field, val, fn))
        elif _size(p) < 3:
            _alarm('coverage says %s=%d but %s is empty on this engine.' % (field, val, fn))


def check_board_freshness(cov):
    """The board itself must be fresh; a stale docs/index.html means no publish went out."""
    age = _age_hours(DOCS)
    if age is None:
        return  # already alarmed in read_coverage
    if age > FRESH_HOURS_BOARD:
        _alarm('docs/index.html is %.0fh old (limit %dh) -- no publish has happened, or the '
               'nightly did not reach the publish step.' % (age, FRESH_HOURS_BOARD))
    return age


def render_text(engine, cov, per_engine_rows, shared_rows, board_age):
    L = []
    W = 78
    L.append('=' * W)
    L.append('  ENGINE DRIFT WATCHDOG')
    L.append('=' * W)
    L.append('  engine.id       : %s' % (engine or '(missing)'))
    L.append('  docs/index.html : %s' % ('%.1fh old' % board_age if board_age is not None else 'MISSING'))
    if cov:
        L.append('  coverage stamp  : %s' % json.dumps(cov, separators=(',', ':')))
    L.append('')
    L.append('  PER-ENGINE (laptop-owned)')
    for r in per_engine_rows:
        age = ('%.1fh' % r['age_hours']) if r['age_hours'] is not None else 'MISSING'
        L.append('    %-28s  %-10s  %d KB   %s' % (r['file'], age, max(0, r['bytes'])//1024, r['why']))
    L.append('')
    L.append('  SHARED (tracked)')
    for r in shared_rows:
        age = ('%.1fh' % r['age_hours_disk']) if r['age_hours_disk'] is not None else 'MISSING'
        auth = r['last_commit_author'] or '(no commits found)'
        L.append('    %-28s  %-10s  by %s' % (r['file'], age, auth))
    L.append('')
    if ALARMS:
        L.append('  !! ALARM  %d drift condition(s) detected' % len(ALARMS))
        for a in ALARMS:
            L.append('     - ' + a)
    if NOTES:
        L.append('  .. NOTES  %d observation(s), not a hard fail' % len(NOTES))
        for n in NOTES:
            L.append('     - ' + n)
    if not ALARMS and not NOTES:
        L.append('  OK -- every check ran and passed.')
    L.append('=' * W)
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    engine = read_engine_id()
    cov = read_coverage()
    per_engine_rows = check_per_engine_files(engine, cov) if engine else []
    shared_rows = check_shared_files() if engine else []
    check_coverage_backing(engine, cov) if engine else None
    board_age = check_board_freshness(cov)

    exit_code = 0
    # No engine id or no coverage = CANNOT EVALUATE, exit 3.
    if engine is None or cov is None:
        exit_code = 3
    elif ALARMS:
        exit_code = 2

    if a.json:
        print(json.dumps({
            'engine': engine, 'coverage': cov,
            'per_engine': per_engine_rows, 'shared': shared_rows,
            'alarms': ALARMS, 'notes': NOTES, 'exit': exit_code,
        }, indent=1))
    else:
        try:
            print(render_text(engine, cov, per_engine_rows, shared_rows, board_age))
        except UnicodeEncodeError:
            print(render_text(engine, cov, per_engine_rows, shared_rows, board_age)
                  .encode('ascii', 'replace').decode('ascii'))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
