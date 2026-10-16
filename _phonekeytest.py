"""Phone opt-out keys: written as 10 digits, and read the same way whatever shape an older key has.

Real ledger 2026-10-16: 2 of 10 phone keys were saved with punctuation ('#(305) 555-0100') and the
door planner (_carlos_route._identity_opted) compared strings, so it could never match them.
No browser, no live data: runs on any checkout.
"""
import os, sys, json, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
R = []
def rec(n, ok, d=''):
    R.append(bool(ok)); print(('  PASS ' if ok else '  FAIL ') + n + ((' | ' + d) if d else ''))

import optout_sync as OS
import _carlos_route as CR

# ---- writer: every shape lands as '#' + 10 digits ----
for raw, want in [('#(305) 555-0100', '#3055550100'), ('#+1 305 555 0100', '#3055550100'),
                  ('#13055550100', '#3055550100'), ('#305-555-0100', '#3055550100'),
                  ('#3055550100', '#3055550100'), ('@someone@example.com', '@someone@example.com'),
                  ('ZZ-CASE-1', 'ZZ-CASE-1'), ('#abcdef0123456789abcdef', '#abcdef0123456789abcdef'),
                  ('#', '#'), ('#k3j9x2a', '#k3j9x2a'), ('#3055550100 x12', '#3055550100 x12')]:
    got = OS._norm_phone_key(raw)
    rec('writer: %r -> %r' % (raw, want), got == want, got)

tmp = tempfile.mkdtemp()
OS.OPTOUTS = os.path.join(tmp, 'optouts.json')
OS.SUPPRESS = os.path.join(tmp, 'bounced_emails.json')
added, already = OS.ledger_add(['#(305) 555-0100', '#3055550100'], 'test', 'phonekeytest')
led = json.load(open(OS.OPTOUTS, encoding='utf-8'))['notes']
rec('ledger_add stores one digits-only key', list(led) == ['#3055550100'], str(list(led)))
rec('a second shape of the same number is "already"', already == ['#3055550100'] or len(added) == 1, '%s %s' % (added, already))

# ---- reader: an OLD punctuated / 11-digit key still suppresses the lead's 10-digit number ----
for key in ('#(305) 555-0100', '#305-555-0100', '#13055550100', '#3055550100'):
    ledger = {key: {'status': 'DO NOT CONTACT'}}
    rec('door gate: old key %r suppresses the 10-digit number' % key,
        CR._identity_opted({'emails': [], 'phones': ['3055550100']}, ledger))
    rec('door gate: old key %r suppresses a punctuated lead number' % key,
        CR._identity_opted({'emails': [], 'phones': [{'number': '(305) 555-0100'}]}, ledger))
rec('door gate: a different number is NOT suppressed',
    not CR._identity_opted({'emails': [], 'phones': ['3055550101']}, {'#(305) 555-0100': {'status': 'x'}}))
rec('door gate: email keys unchanged',
    CR._identity_opted({'emails': ['a@b.com'], 'phones': []}, {'@a@b.com': {'status': 'x'}}))
rec('door gate: no phone, no match',
    not CR._identity_opted({'emails': [], 'phones': []}, {'#3055550100': {'status': 'x'}}))

print('\n==== %d/%d phone-key checks passed ====' % (sum(R), len(R)))
raise SystemExit(0 if all(R) else 1)
