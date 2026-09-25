"""The auction archive may fill a hole, and may never overwrite a number it already holds.

Gitignored `_*.py` with a `!` negation in .gitignore. No network, no browser: this drives
auction_archive.snapshot() against a temp archive and asserts the merge contract.

THE GAP THIS EXISTS FOR (found 2026-09-22, sizing the caveman-week equity screen)

snapshot() carried the comment "keep the richest address/value we have ever seen" over code that
backfilled the ADDRESS and nothing else. value, judg and county were written once, on first
sighting, and never touched again.

First sighting is the night the case lands on the auction calendar. The property-appraiser
enrichment has not run against it yet, so `value` is 0 that night. It stayed 0 for the life of the
row no matter what the board learned the next morning. Measured on the live archive the day this
was found: of 294 upcoming Miami-Dade sales, 97% carried a judgment and 24% carried a judgment AND
a value. The 24% is this bug. Judgment-versus-value is the only screen the caveman week runs, so a
column that silently stops filling is the whole exercise reading as "no equity anywhere".

The fix is NOT "last write wins". The archive's stated law is that a scrape glitch must never
erase a date we already recorded, and the same reasoning covers the money: a 0 arriving tonight is
the absence of a reading, not a reading of zero. So the contract is one-directional -- a real
number may land in a hole, and nothing may displace a real number.
"""
import io, json, os, shutil, sys, tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

R = []
def rec(n, ok, d=''):
    R.append(bool(ok))
    print((('  PASS ' if ok else '  FAIL ') + n + (' | ' + str(d) if d else '')).encode('ascii', 'replace').decode())

import auction_archive as A   # noqa: E402

CASE = '2026-000001-CA-01'

def run(rows, seed=None):
    """snapshot() against a throwaway archive seeded with `seed`, returning the merged row."""
    tmp = tempfile.mkdtemp(prefix='archfill')
    try:
        arc_path = os.path.join(tmp, 'auction_archive.json')
        real_arc, real_rows = A.ARCHIVE, A._rows_from_board
        A.ARCHIVE = arc_path
        A._rows_from_board = lambda: (rows, 'fixture')
        try:
            if seed is not None:
                json.dump({CASE: seed}, io.open(arc_path, 'w', encoding='utf-8'))
            out = A.snapshot()
        finally:
            A.ARCHIVE, A._rows_from_board = real_arc, real_rows
        return out.get(CASE, {})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def row(**kw):
    base = {'case': CASE, 'auction': '2026-10-15', 'addr': '', 'county': '', 'value': 0, 'judg': 0}
    base.update(kw)
    return base

# ── 1. the bug itself: calendar night, then enrichment ───────────────────────────────────────────
# Night one the case is on the calendar with nothing but a date. Night two the appraiser value and
# the final judgment have landed. Before the fix the row stayed 0/0 forever.
seen_first = {'auction': '2026-10-15', 'addr': '', 'county': 'MIAMI-DADE', 'value': 0, 'judg': 0,
              'first_seen': '2026-09-01', 'last_seen': '2026-09-01'}
r = run([row(addr='1 MAIN ST, MIAMI, 33130', county='MIAMI-DADE', value=410000, judg=228000)],
        seed=seen_first)
rec('a value learned after calendar night reaches the archive', r.get('value') == 410000, r.get('value'))
rec('a judgment learned after calendar night reaches the archive', r.get('judg') == 228000, r.get('judg'))
rec('an address learned later still backfills (unchanged behaviour)',
    r.get('addr') == '1 MAIN ST, MIAMI, 33130', r.get('addr'))

# ── 2. the half of the contract that is NOT last-write-wins ──────────────────────────────────────
# A scrape that comes back empty is a failed reading, not a reading of zero. It must not blank a
# number the archive already holds -- same reasoning as the sale-date law snapshot() opens with.
held = {'auction': '2026-10-15', 'addr': '9 OLD RD, MIAMI, 33130', 'county': 'MIAMI-DADE',
        'value': 500000, 'judg': 300000, 'first_seen': '2026-09-01', 'last_seen': '2026-09-01'}
r = run([row(addr='', county='', value=0, judg=0)], seed=held)
rec('an empty scrape cannot blank a value the archive holds', r.get('value') == 500000, r.get('value'))
rec('an empty scrape cannot blank a judgment the archive holds', r.get('judg') == 300000, r.get('judg'))
rec('an empty scrape cannot blank an address the archive holds',
    r.get('addr') == '9 OLD RD, MIAMI, 33130', r.get('addr'))

# A DIFFERENT non-zero number is still not an overwrite. The archive is append-only; a re-read that
# disagrees is a fact for the live board to settle, not for this file to silently adopt.
r = run([row(value=1, judg=1, addr='CHANGED', county='BROWARD')], seed=held)
rec('a disagreeing value does not overwrite the held one', r.get('value') == 500000, r.get('value'))
rec('a disagreeing judgment does not overwrite the held one', r.get('judg') == 300000, r.get('judg'))
rec('a disagreeing county does not overwrite the held one', r.get('county') == 'MIAMI-DADE', r.get('county'))

# ── 3. county fills the same way (it was frozen on first sight too) ──────────────────────────────
r = run([row(county='BROWARD', value=100, judg=50)],
        seed={'auction': '2026-10-15', 'addr': '', 'county': '', 'value': 0, 'judg': 0,
              'first_seen': '2026-09-01', 'last_seen': '2026-09-01'})
rec('a missing county backfills', r.get('county') == 'BROWARD', r.get('county'))

# ── 4. nothing above disturbed the sale-date law snapshot() is built on ──────────────────────────
r = run([row(auction='2026-09-01', value=7)],
        seed={'auction': '2026-10-15', 'addr': '', 'county': '', 'value': 0, 'judg': 0,
              'first_seen': '2026-09-01', 'last_seen': '2026-09-01'})
rec('an EARLIER sale date still cannot move a recorded one', r.get('auction') == '2026-10-15', r.get('auction'))
rec('...while the value on that same row still backfills', r.get('value') == 7, r.get('value'))

r = run([row(auction='2026-11-20')],
        seed={'auction': '2026-10-15', 'addr': '', 'county': '', 'value': 0, 'judg': 0,
              'first_seen': '2026-09-01', 'last_seen': '2026-09-01'})
rec('a LATER sale date still moves the row and records reset_from',
    r.get('auction') == '2026-11-20' and r.get('reset_from') == '2026-10-15', r)

# ── 5. a brand-new case is unaffected ────────────────────────────────────────────────────────────
r = run([row(addr='2 NEW ST', county='PALM BEACH', value=222, judg=111)])
rec('a first sighting still records everything it was handed',
    (r.get('value'), r.get('judg'), r.get('county')) == (222, 111, 'PALM BEACH'), r)

print('\n%d/%d pass' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
