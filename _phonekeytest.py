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

# ---- door gate: a failed hash import still matches raw keys (it used to return False outright) ----
import builtins
_real_import = builtins.__import__
def _no_fl(name, *a, **k):
    if name == 'foreclosure_leads':
        raise ImportError('simulated')
    return _real_import(name, *a, **k)
builtins.__import__ = _no_fl
try:
    rec('door gate, no hash module: raw phone key still suppresses',
        CR._identity_opted({'emails': [], 'phones': ['3055550100']}, {'#(305) 555-0100': {'status': 'x'}}))
    rec('door gate, no hash module: raw email key still suppresses',
        CR._identity_opted({'emails': ['a@b.com'], 'phones': []}, {'@a@b.com': {'status': 'x'}}))
finally:
    builtins.__import__ = _real_import

# ---- backfill: an older punctuated key in the ledger gets its 10-digit twin; nothing is removed ----
json.dump({'_dealflow_notes': 1, 'notes': {
    '#(305) 555-0177': {'status': 'DO NOT CONTACT'}, '#13055550188': {'status': 'DO NOT CONTACT'},
    '#k3j9x2': {'status': 'DO NOT CONTACT'}, '#K4J9X2': {'status': 'DO NOT CONTACT'}, '#3055550199': {'status': 'DO NOT CONTACT'},
    '@x@y.com': {'status': 'DO NOT CONTACT'}, 'ZZ-CASE-2': {'status': 'DO NOT CONTACT'}}},
    open(OS.OPTOUTS, 'w', encoding='utf-8'))
_before = set(json.load(open(OS.OPTOUTS, encoding='utf-8'))['notes'])
_da, _ = OS.backfill_phone_keys(dry_run=True)
rec('backfill dry run writes nothing',
    set(json.load(open(OS.OPTOUTS, encoding='utf-8'))['notes']) == _before, str(_da))
_ba, _ = OS.backfill_phone_keys()
_after = set(json.load(open(OS.OPTOUTS, encoding='utf-8'))['notes'])
rec('backfill adds exactly the two 10-digit twins', sorted(_ba) == ['#3055550177', '#3055550188'], str(_ba))
rec('backfill keeps every old key', _before <= _after, str(sorted(_after - _before)))
rec('backfill leaves hashed keys alone, upper-case ones included',
    '#k3j9x2' in _after and '#k4j9x2' not in _after, str(sorted(_after)))
_ba2, _ = OS.backfill_phone_keys()
rec('backfill second run adds nothing', _ba2 == [], str(_ba2))

# ---- notes read: a punctuated do-not-text number comes back as '#' + 10 digits ----
_np = os.path.join(tmp, 'worker_notes.json')
json.dump({'notes': {'ZZ-CASE-3': {'status': 'DO NOT CONTACT', 'dntph': ['(305) 555-0166', '13055550155']}}},
          open(_np, 'w', encoding='utf-8'))
_nk = OS.notes_dnc_keys(_np)
rec('notes read: dntph keys are 10 digits', {'#3055550166', '#3055550155'} <= _nk, str(sorted(_nk)))

# ---- /text pre-check set: an older punctuated ledger key is found by the 10-digit lookup ----
try:
    import send_server as SS
    SS.OPTOUT_FILE = OS.OPTOUTS
    _tc, _te = SS._optout_set()
    rec('/text set: old punctuated key stands as 10 digits', '#3055550177' in _tc)
    rec('/text set: hashed key not turned into digits', '#k3j9x2' in _tc and '#' not in _tc)
    rec('/text set: emails unchanged', 'x@y.com' in _te)
except Exception as e:
    rec('/text set: send_server importable', False, repr(e))

print('\n==== %d/%d phone-key checks passed ====' % (sum(R), len(R)))
raise SystemExit(0 if all(R) else 1)
