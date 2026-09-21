"""A seat page must carry the WHOLE book: its own half dialable, everything else as coverage.

WHY THIS EXISTS (2026-09-21). Call Mode's coverage rows are the rest of the book — one slim,
un-dialable row per lead the dial queue does not carry — and they exist so the nine board lanes
read the same totals on a handset as they do on the board. They were cut against the CREW-WIDE
dial list, one line above the seat split, on the stated argument that halving the lane counts per
phone would make two callers read two different books.

The argument is right and the code did the opposite of it. A seat page shipped its own half of the
dial queue plus coverage for everything outside the CREW list, so the OTHER seat's dial rows were
in neither list and were absent from the page altogether. Every lane on Alejandro's handset read
short by the size of Carlos's queue, and the leads that went missing were the most callable in the
book: they had passed every gate and made the cap, which is exactly why coverage skipped them.

make_callmode's own "every lead, or say which ones are missing" guard did not catch it, because it
checked the CREW union (_dial_all | _cov) rather than what the page ships (this seat's rows |
_cov). That is the trap worth remembering: a guard that asserts over a superset of what you ship
proves nothing about what you ship.

1863e22 scaled the cap with the crew (400 -> 800 crew-wide) to give each phone a full list. That
was the right fix for a different defect and it DOUBLED this one: ~400 leads now fall off each page
instead of ~200. The two interact, which is why this is pinned separately from _queuechurntest.

NOTHING HERE TOUCHES THE RESERVED SUPPRESSION SURFACE (CLAUDE.md) — opt-outs, STOP detection and
the send-time gates are passed through, never changed.

Run: python _seatcoveragetest.py
"""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_mode                                                    # noqa: E402

CHECKS, FAILS = [], []


def rec(name, ok, detail=''):
    CHECKS.append(name)
    if not ok:
        FAILS.append(name)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(detail)) if detail else ''))


def book(n_leads=2394, n_qualified=790):
    """The live 2026-09-21 shape: a book of which only some leads have a traced number, so the
    rest land in coverage the way they do on a real build."""
    out = []
    for i in range(n_leads):
        d = {'case': 'C%05d' % i, 'owners': 'OWNER %d' % i, 'oname': 'Owner %d' % i,
             'addr': '%d NW 109 AVE, MIAMI FL 33172' % i, 'days': 30, 'auction': '10/20/2026',
             'value': 300000, 'eq': 90 - (i * 0.03)}
        if i < n_qualified:
            d['phones'] = ['305555%04d' % (i % 10000)]
            d['phdnc'] = [False]
        out.append(d)
    return out


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


SEATS = [s for s in call_mode.CALL_SEATS if s]
CREW_CAP = 400 * (len(SEATS) or 1)          # what make_tracker passes since 1863e22

print('=== call mode: the whole book on every seat page ===\n')

LEADS = book()
allc = {d['case'] for d in LEADS}
crew, total = quiet(call_mode.call_rows, LEADS, cap=CREW_CAP)
print('-- fixture: %d leads, %d qualified, crew window %d (cap %d x %d seat(s)) --\n'
      % (len(LEADS), total, len(crew), 400, len(SEATS) or 1))

# ---- 1. what each page ships ----------------------------------------------------------------
print('-- 1. dial + coverage = every lead, on both pages --')
pages = {}
for s in SEATS:
    mine = call_mode.seat_rows(crew, s[0], s[1])
    cov, _ = quiet(call_mode.coverage_rows, LEADS, [r['c'] for r in mine])
    pages[s[2]] = (mine, cov)
    shipped = {r['c'] for r in mine} | {r['c'] for r in cov}
    rec('%s: dial + coverage = every lead' % s[2], shipped == allc,
        '%d of %d on the page' % (len(shipped), len(allc)))
    rec('%s: no lead is on the page twice' % s[2],
        not ({r['c'] for r in mine} & {r['c'] for r in cov}))

rec('both pages total the same book — two callers read one book',
    len({len({r['c'] for r in d} | {r['c'] for r in c}) for d, c in pages.values()}) == 1,
    ' / '.join('%s %d' % (w, len({r['c'] for r in d} | {r['c'] for r in c}))
               for w, (d, c) in pages.items()))

for s in SEATS:
    other = [o for o in SEATS if o is not s]
    mine, cov = pages[s[2]]
    othercases = set()
    for o in other:
        othercases |= {r['c'] for r in call_mode.seat_rows(crew, o[0], o[1])}
    rec('%s: the other phone\'s queue ships as COVERAGE, never as a hole' % s[2],
        othercases <= {r['c'] for r in cov}, '%d rows' % len(othercases))

# ---- 2. the defect itself, pinned so a revert fails loudly ------------------------------------
print('\n-- 2. the old crew-wide cut, pinned --')
old_cov, _ = quiet(call_mode.coverage_rows, LEADS, [r['c'] for r in crew])
old_shipped = {r['c'] for r in call_mode.seat_rows(crew, *SEATS[0][:2])} | {r['c'] for r in old_cov}
lost = len(allc) - len(old_shipped)
rec('cutting coverage against the CREW list really did lose leads off the page', lost > 0,
    '%d leads were on neither list' % lost)
rec('and scaling the cap with the crew made that hole BIGGER, not smaller',
    lost > (len(crew) // 2) * 0.8,
    '%d lost at crew window %d' % (lost, len(crew)))

# ---- 3. the guard that missed it --------------------------------------------------------------
print('\n-- 3. the guard has to assert over what the PAGE ships --')
src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
i_split = src.find('rows = seat_rows(rows, _sn, _si)')
i_dial = src.find('_dial_all = list(rows)')
rec('_dial_all is taken AFTER the seat split, not before',
    i_split > 0 and i_dial > i_split, 'split@%d dial_all@%d' % (i_split, i_dial))
rec('coverage_rows is still fed from _dial_all (one definition, not two)',
    "coverage_rows(slim, [r.get('c') for r in _dial_all]" in src)

# ---- 4. the rows that grew are still not dialable ---------------------------------------------
print('\n-- 4. a bigger coverage set is still a countable one --')
cov0 = pages[SEATS[0][2]][1]
rec('no phone numbers in any coverage row', not any('p' in r for r in cov0))
rec('phone COUNTS only (np), never the numbers',
    all(isinstance(r.get('np', 0), int) for r in cov0))
rec('no lead gains a dialable number by moving into coverage',
    not any(r.get('p') or r.get('r') for r in cov0))

print('\n%d checks, %d failed' % (len(CHECKS), len(FAILS)))
if FAILS:
    print('FAILED:')
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
