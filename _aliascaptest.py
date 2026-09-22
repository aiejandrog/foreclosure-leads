"""One-shot: the per-address daily send cap is enforced on EVERY lane address, including the one
that is also the login. Gitignored _*.py. No network, no bridge, no credentials.

THE MORNING THIS EXISTS FOR (2026-09-18). The Morning Worker delivered 182 emails between 09:22
and 09:38 from alejandro@bsgflorida.com, a domain senders.json caps at 40 a day, and the bridge
refused none of them. Two correct-looking pieces composed into that:

  * ramp_start is 2026-09-21, so _ramp_cap() returns 0 for both warming aliases, and since
    2026-09-11 _lane_from() deliberately routes a zero-capped lane back to the main domain rather
    than mailing nobody. Correct, and its docstring promised "this can never push bsgflorida.com
    past 40".
  * /send only consulted the cap inside `if _cand != user`. The main-domain address IS the login,
    so every lane took the other branch, where nothing was metered but the global 300/day.

The routing kept its promise. The enforcement never read it. This suite asserts the arithmetic of
that promise and that the handler meters unconditionally.
"""
import io, os, re, sys, datetime as dt

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
import send_server as S

R = []
def rec(n, ok, d=''):
    R.append(bool(ok))
    print((('  PASS ' if ok else '  FAIL ') + n + (' | ' + str(d) if d else '')).encode('ascii', 'replace').decode())

CFG = S._load_senders()
USER = (CFG.get('lanes') or {}).get('default', '')
MAIN = str(CFG.get('main_domain') or 'bsgflorida.com').lower()
LANES = ['replied', 'urgent', 'active', 'early', 'balloon']

rec('senders.json loads with a lane map', bool(CFG.get('lanes')), ', '.join(sorted(CFG.get('lanes') or {})))
rec('the lane map is active for a main-domain login', S._senders_active(USER, CFG), USER)

# ---- the ramp is shut, which is the state that routes everything to the main domain ------------
today = dt.date.today()
start = dt.date.fromisoformat(str(CFG.get('ramp_start')))
shut = today < start
print('  ..    ramp_start %s, today %s -> aliases %s' % (start, today, 'SHUT' if shut else 'open'))

rec('the main domain gets the flat main_domain_cap, whatever the ramp says',
    S._ramp_cap(CFG, USER, today) == int(CFG.get('main_domain_cap') or 40),
    '%s -> %d' % (USER, S._ramp_cap(CFG, USER, today)))
rec('a warming alias is hard zero before ramp_start',
    S._ramp_cap(CFG, 'alejandro@bsgfl.com', start - dt.timedelta(days=1)) == 0)
rec('a warming alias opens at the first ramp step on ramp_start',
    S._ramp_cap(CFG, 'alejandro@bsgfl.com', start) == 5,
    S._ramp_cap(CFG, 'alejandro@bsgfl.com', start))

# ---- while the ramp is shut, every lane resolves to the main domain -----------------------------
routed = {L: S._lane_from(CFG, L, start - dt.timedelta(days=1)) for L in LANES}
rec('with the ramp shut, every lane routes to the main domain',
    all(a.endswith('@' + MAIN) for a in routed.values()), ', '.join(sorted(set(routed.values()))))
rec('and every one of those addresses carries a real cap, not zero',
    all(S._ramp_cap(CFG, a, start - dt.timedelta(days=1)) > 0 for a in routed.values()))

# ---- once the ramp opens, the lanes go back to their own aliases --------------------------------
open_day = start + dt.timedelta(days=1)
reopened = {L: S._lane_from(CFG, L, open_day) for L in LANES}
rec('the fallback self-reverts once the ramp opens',
    reopened['active'] == (CFG['lanes']['active']) and reopened['early'] == (CFG['lanes']['early']),
    'active=%s early=%s' % (reopened['active'], reopened['early']))

# ---- metering: the ledger is read by ADDRESS, and the login is not special ----------------------
_real_loader = S._load_ledger
def _fake_ledger(entries):
    S._load_ledger = lambda: entries

d = today.isoformat()
def sent(addr, n, **kw):
    e = {'d': d, 'from': addr, 'message_id': '<x>', 'ch': 'email'}
    e.update(kw)
    return [dict(e) for _ in range(n)]

try:
    _fake_ledger(sent(USER, 39))
    rec('39 sends from the login are counted against the login', S._alias_sent_today(USER) == 39,
        S._alias_sent_today(USER))
    cap = S._ramp_cap(CFG, USER, today)
    rec('at 39 of %d the next send is still affordable' % cap, S._alias_sent_today(USER) < cap)

    _fake_ledger(sent(USER, 40))
    rec('at %d of %d the next send is refused' % (cap, cap), S._alias_sent_today(USER) >= cap)

    # the shape of 2026-09-18: 182 real sends from the login, all on today's date
    _fake_ledger(sent(USER, 182))
    rec('the 09-18 run (182 from the login) is over the cap by the ledger the handler reads',
        S._alias_sent_today(USER) == 182 and 182 >= cap, '182 >= %d' % cap)

    # test sends and failures must not consume a homeowner's quota, and must not hide one either
    _fake_ledger(sent(USER, 5) + sent(USER, 3, test_mode=True) + sent(USER, 2, error='boom'))
    rec('test sends and failed sends are not metered', S._alias_sent_today(USER) == 5,
        S._alias_sent_today(USER))
finally:
    S._load_ledger = _real_loader

# ---- the handler must not condition the cap on "is this address the login?" ---------------------
src = io.open(os.path.join(HERE, 'send_server.py'), encoding='utf-8').read()
blk = re.search(r'_cand = _lane_from\(_cfg, _wl\).*?# ---- send ----', src, re.S)
rec('the lane/cap block is still where it was', bool(blk))
if blk:
    b = blk.group(0)
    rec('the cap is read for every resolved lane address',
        re.search(r'\n\s+if _cand:\s*\n', b) is not None and '_acap = _ramp_cap(' in b)
    rec('the cap check is NOT nested under "different from the login"',
        re.search(r'if _cand and _cand != user:', b) is None)
    rec('From: is still only rewritten when the lane address differs from the login',
        '_cand if _cand != user else None' in b)
    rec('going over the cap still answers 409 skip, not a hard stop',
        "'skip': True" in b and "'alias_cap': True" in b and 'self._json(409' in b)

# ---- THE CLI COUSIN MUST METER THE SAME WAY (2026-09-22) ---------------------------------------
# outreach_email.py is the second SMTP sender in the project and it kept the pre-#23 shape for four
# more days: its cap check sat inside `if _cand and _cand != user`, so the main domain -- the only
# address that is both a lane target and the login -- was metered by nothing but DAILY_MAX = 50,
# which is itself ABOVE the 40 senders.json allows it. Unlike the bridge's version this would NOT
# have healed when the ramp opened: `replied` and `urgent` point at the login by design.
osrc = io.open(os.path.join(HERE, 'outreach_email.py'), encoding='utf-8').read()
oblk = re.search(r'_cand = _SS\._lane_from\(_cfg, lane\).*?\n\s+try:\n', osrc, re.S)
rec('outreach_email still has the lane/cap block', bool(oblk))
if oblk:
    ob = oblk.group(0)
    rec('outreach_email reads the cap for every resolved lane address',
        re.search(r'\n\s+if _cand:\s*\n', ob) is not None and '_cap = _SS._ramp_cap(' in ob)
    rec('outreach_email does NOT nest the cap under "different from the login"',
        re.search(r'if _cand and _cand != user:', ob) is None)
    rec('outreach_email still only rewrites From: when the address differs from the login',
        '_cand if _cand != user else None' in ob)
    rec('outreach_email still skips the lead rather than stopping the batch',
        'continue' in ob and 'at its warm-up cap for today' in ob)

# DAILY_MAX is not a substitute for the per-address cap, and this is why: it is higher than the
# ceiling the main domain is supposed to have, so a batch that respects only DAILY_MAX overshoots.
_dm = re.search(r'^DAILY_MAX\s*=\s*(\d+)', osrc, re.M)
rec('outreach_email DAILY_MAX is above main_domain_cap, so it cannot stand in for it',
    bool(_dm) and int(_dm.group(1)) > int(CFG.get('main_domain_cap') or 40),
    'DAILY_MAX=%s vs main_domain_cap=%s' % (_dm.group(1) if _dm else '?', CFG.get('main_domain_cap')))

# ---- cadence.py already meters unconditionally; assert it stays that way ------------------------
# cadence is the only UNATTENDED sender, so a regression here mails homeowners with nobody watching.
csrc = io.open(os.path.join(HERE, 'cadence.py'), encoding='utf-8').read()
rec('cadence meters on the resolved alias, not on "is it the login"',
    re.search(r'\n\s+if alias:\s*\n\s+used, cap = _ss\._alias_sent_today\(alias\), _ss\._ramp_cap\(', csrc)
    is not None and re.search(r'if alias and alias != ', csrc) is None)

# ---- after the ramp opens, replied/urgent stay on the login -- so the login is never exempt -----
# This is the half of the bug that would have outlived 2026-09-21 in the CLI sender.
rec('replied and urgent still resolve to the login once the ramp is open',
    S._lane_from(CFG, 'replied', open_day) == USER and S._lane_from(CFG, 'urgent', open_day) == USER,
    'replied=%s urgent=%s' % (S._lane_from(CFG, 'replied', open_day), S._lane_from(CFG, 'urgent', open_day)))
rec('and that address still carries the flat main_domain_cap on that day',
    S._ramp_cap(CFG, USER, open_day) == int(CFG.get('main_domain_cap') or 40),
    S._ramp_cap(CFG, USER, open_day))

print('\n%d/%d passed' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
