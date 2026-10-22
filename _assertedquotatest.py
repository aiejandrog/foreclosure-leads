"""A first touch riding only a paid 'valid' is paced at PROBE_DAILY_CAP, whatever the breaker says.

WHY THIS EXISTS
`_recipient_verdict` admits a first touch on one of three codes. Two are OBSERVED — 'delivered'
(we mailed it and it never bounced) and 'replied' (the owner wrote to us). The third,
'zerobounce_valid', is a paid API assertion and nothing more; this file measures that class at a
56% false-valid rate on this list (63 of 113 hard-bounced, 2026-08-26) and `_cls()` calls it
"PROBE-eligible, not proven". PROBE_DAILY_CAP exists to pace exactly it.

THE BUG. The only code that consulted the quota sat inside

    if _hb['blocked'] and not _ft_exempt:

so the trickle was read only while the bounce breaker was engaged, and skipped whenever the
trailing rate looked clean or a warm-up sender made the first touch exempt — i.e. it was absent in
exactly the state that precedes a bad day. Measured on 2026-10-21: 43 sends, 42 of them to
addresses this system had never mailed, 0 booked to the probe lane, 5 hard bounces.

FAILS ON MAIN: 'an asserted-evidence first touch is metered outside the breaker branch' and the
three checks under it — on main there is no such branch at all, so `_asserted_branch()` returns
None and they fail. The quota-counting and verdict checks pass on main too; they are here to prove
the thing being metered is the right class and that the counter it reuses actually counts.

No server, no socket, no SMTP, no real ledger read or written, nothing sent. Invented
example.invalid addresses and a fabricated in-memory ledger only.
"""
import ast
import datetime as dt
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import send_server as S   # noqa: E402

ok, bad = [], []


def rec(name, cond, detail=''):
    (ok if cond else bad).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)) if detail else ''))


SRC = io.open(os.path.join(HERE, 'send_server.py'), encoding='utf-8').read()
TREE = ast.parse(SRC)


def _breaker_ifs():
    """Every `if` whose test mentions the breaker verdict (_hb['blocked'] / _bh['blocked'])."""
    out = []
    for node in ast.walk(TREE):
        if isinstance(node, ast.If) and 'blocked' in ast.dump(node.test):
            out.append(node)
    return out


def _asserted_branch():
    """The always-on asserted-evidence branch: tests _first_touch AND the zerobounce_valid code."""
    for node in ast.walk(TREE):
        if not isinstance(node, ast.If):
            continue
        t = ast.dump(node.test)
        if '_first_touch' in t and 'zerobounce_valid' in t:
            return node
    return None


# ---- 1. the metering exists and is NOT nested under the breaker ----------------------------
branch = _asserted_branch()
rec('an asserted-evidence first touch is metered outside the breaker branch', branch is not None)

            # No `if branch is not None` guard: on main the branch is absent and these must FAIL,
            # not quietly skip. A regression gate that reports "11 passed" on the broken tree is
            # the same empty-looks-healthy failure this suite is about.
body = ast.dump(branch) if branch is not None else ''
rec('that branch consults the probe quota', '_probe_quota_left' in body)
rec('it holds with 451 + skip (advance the lead, do not pause the run)',
    "'skip'" in body and '451' in body)
rec('it books the send to the probe lane so the quota counts it',
    "'probe'" in body and 'lane' in body)

# the decisive structural claim: this branch is not inside any breaker-conditional `if`
inside_breaker = (branch is not None
                  and any(branch in list(ast.walk(b)) for b in _breaker_ifs()))
rec('the metering is reached even when the breaker is CLEAR',
    branch is not None and not inside_breaker)

# the pre-existing breaker-only probe call must still be there — this PR adds pacing, it does not
# remove the proven-lane behaviour that only applies while blocked
rec('the breaker-engaged proven/probe lane is left in place',
    SRC.count('_probe_quota_left()') >= 3, SRC.count('_probe_quota_left()'))

# ---- 2. the class being metered is the asserted one ----------------------------------------
def ev(bounced=(), ver=None, replied=(), proven=(), last_mailed=None):
    return {'bounced': set(bounced), 'ver': dict(ver or {}), 'replied': set(replied),
            'proven': set(proven), 'last_mailed': dict(last_mailed or {})}


A = 'nobody.invented@example.invalid'

code, _ = S._recipient_verdict(A, ev(ver={A: {'v': 'ok', 'why': 'zerobounce:valid'}}))
rec("a paid valid is the metered class ('zerobounce_valid')", code == 'zerobounce_valid', code)

code, _ = S._recipient_verdict(A, ev(proven=(A,)))
rec("observed delivery is NOT metered ('delivered')", code == 'delivered', code)

code, _ = S._recipient_verdict(A, ev(replied=(A,)))
rec("a reply is NOT metered ('replied')", code == 'replied', code)

# ---- 3. the counter it reuses actually counts ----------------------------------------------
TODAY = dt.date.today().isoformat()
YEST = (dt.date.today() - dt.timedelta(days=1)).isoformat()
_real_loader = S._load_ledger
try:
    rec('the cap is 10 a day', S.PROBE_DAILY_CAP == 10, S.PROBE_DAILY_CAP)

    S._load_ledger = lambda: []
    rec('an empty day leaves the whole quota', S._probe_quota_left() == 10, S._probe_quota_left())

    S._load_ledger = lambda: [{'ch': 'email', 'd': TODAY, 'lane': 'probe'} for _ in range(4)]
    rec("four probe sends today leave six", S._probe_quota_left() == 6, S._probe_quota_left())

    S._load_ledger = lambda: [{'ch': 'email', 'd': TODAY, 'lane': 'probe'} for _ in range(10)]
    rec('a spent quota leaves zero, which is what holds the send',
        S._probe_quota_left() == 0, S._probe_quota_left())

    S._load_ledger = lambda: [{'ch': 'email', 'd': TODAY, 'lane': 'probe'} for _ in range(42)]
    rec('the 2026-10-21 volume (42) cannot come back — quota floors at zero',
        S._probe_quota_left() == 0, S._probe_quota_left())

    S._load_ledger = lambda: [{'ch': 'email', 'd': YEST, 'lane': 'probe'} for _ in range(10)]
    rec("yesterday's probes do not spend today's quota", S._probe_quota_left() == 10,
        S._probe_quota_left())

    S._load_ledger = lambda: [{'ch': 'email', 'd': TODAY, 'lane': ''} for _ in range(10)]
    rec('an observed-evidence send does not spend the probe quota',
        S._probe_quota_left() == 10, S._probe_quota_left())
finally:
    S._load_ledger = _real_loader

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
